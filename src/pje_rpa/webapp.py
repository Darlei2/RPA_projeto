"""Pagina local onde o operador solta o .pfx + senha e o cadastro roda sozinho.

Escuta SOMENTE em 127.0.0.1. O .pfx e a senha nunca sao gravados: ficam em memoria
durante a execucao e sao descartados ao final. O certificado e instalado
temporariamente no repositorio do Windows para o PJeOffice PRO oficial assinar,
e removido (com a chave privada) ao terminar.
"""
from __future__ import annotations

from urllib.parse import urlparse

import json
import queue
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .audit import AuditLog
from .browser import PlaywrightBrowser
from .certificate import CertificateError, WindowsCertificateStore, read_pfx
from .config import (
    ConfigurationError,
    assert_live_ready,
    assert_rehearsal_ready,
    lawyer_from_dict,
    load_config,
    validate_lawyer,
)
from .flow import PJeRegistrationFlow
from .models import LawyerRecord, digits
from .signer import PJeOfficeSigner
from .totp import gerar_totp, normalizar_chave


INSPECTION_TIMEOUT_S = 30 * 60


class _Session:
    """Guarda o estado de uma execucao e a fila de eventos para a pagina (SSE)."""

    def __init__(self) -> None:
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.code_box: "queue.Queue[str | None]" = queue.Queue(maxsize=1)
        self.running = False
        self.inspection_active = False
        self.inspection_close = threading.Event()

    def emit(self, kind: str, **data) -> None:
        self.events.put({"kind": kind, **data})


class RegistrationServer:
    def __init__(
        self,
        config_path: Path,
        audit_root: Path,
        host: str = "127.0.0.1",
        port: int = 8723,
        rehearsal: bool = False,
    ) -> None:
        self.config = load_config(config_path)
        self.audit_root = audit_root
        self.host = host
        self.port = port
        self.rehearsal = rehearsal
        self.session = _Session()
        # Hosts/origens aceitos: bloqueia sites abertos no mesmo navegador e DNS rebinding.
        self._allowed_hosts = {f"{name}:{port}" for name in ("127.0.0.1", "localhost", "[::1]")}
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # silencia o log padrao
                pass

            def do_GET(self):
                if not self._trusted(require_origin=False):
                    return
                if self.path == "/":
                    page = _PAGE.replace("__MODE__", "ensaio" if server.rehearsal else "real")
                    self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
                elif self.path == "/events":
                    self._stream()
                else:
                    self._send(404, "text/plain", b"nao encontrado")

            def do_POST(self):
                if not self._trusted(require_origin=True):
                    return
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                if self.path == "/start":
                    self._start(body)
                elif self.path == "/close-inspection":
                    if not server.rehearsal or not server.session.inspection_active:
                        self._send(
                            409,
                            "application/json",
                            b'{"error":"inspecao indisponivel"}',
                        )
                        return

                    server.session.inspection_close.set()
                    self._send(
                        200,
                        "application/json",
                        b'{"ok":true}',
                    )

                elif self.path == "/code":
                    try:
                        server.session.code_box.put_nowait((json.loads(body).get("code") or "").strip() or None)
                    except queue.Full:
                        self._send(409, "application/json", b'{"error":"codigo ja enviado"}')
                        return
                    self._send(200, "application/json", b'{"ok":true}')
                else:
                    self._send(404, "text/plain", b"nao encontrado")

            # ---- helpers
            def _trusted(self, require_origin: bool) -> bool:
                host_ok = self.headers.get("Host", "") in server._allowed_hosts
                origin = self.headers.get("Origin")
                origin_ok = origin in {f"http://{h}" for h in server._allowed_hosts} if origin else not require_origin
                if host_ok and origin_ok:
                    return True
                self._send(403, "text/plain", b"origem nao permitida")
                return False

            def _send(self, status, ctype, payload):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _stream(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                while True:
                    event = server.session.events.get()
                    self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    if event["kind"] == "inspection_closed" or (
                        not server.rehearsal
                        and event["kind"] in {"done", "error"}
                    ):
                        break

            def _start(self, body):
                if server.session.running:
                    self._send(409, "application/json", b'{"error":"ja em execucao"}')
                    return
                try:
                    payload = json.loads(body)
                    pfx = _decode_pfx(payload["pfx_base64"])
                    password = payload["password"]
                    record = _record_from_payload(payload["cadastro"])
                    totp_secret = normalizar_chave(payload.get("totp_secret", ""))
                except (KeyError, ValueError) as exc:
                    self._send(400, "application/json",
                               json.dumps({"error": f"entrada invalida: {exc}"}).encode("utf-8"))
                    return
                server.session = _Session()
                server.session.running = True
                threading.Thread(target=server._execute, args=(pfx, password, record, totp_secret), daemon=True).start()
                self._send(200, "application/json", b'{"ok":true}')

        self._httpd = ThreadingHTTPServer((host, port), Handler)

    def serve_forever(self) -> None:
        self._cleanup_pending("execucao anterior interrompida")
        mode = "ENSAIO (para antes de enviar)" if self.rehearsal else "REAL"
        print(f"Pagina local de cadastro em http://{self.host}:{self.port}/  modo {mode}  (Ctrl+C encerra)")
        try:
            self._httpd.serve_forever()
        except KeyboardInterrupt:
            self._httpd.server_close()
            self._cleanup_pending("encerramento com Ctrl+C")

    # ------------------------------------------------------- certificados pendentes
    # O thumbprint (publico, sem segredo) e anotado ANTES da instalacao e apagado depois da
    # remocao. Se o processo morrer no meio (Ctrl+C, janela fechada, queda), a proxima
    # abertura da pagina remove o que ficou no repositorio do Windows.
    @property
    def _pending_path(self) -> Path:
        return self.audit_root.parent / "certificados-pendentes.txt"

    def _pending(self) -> list[str]:
        try:
            return [t for t in self._pending_path.read_text(encoding="ascii").split() if len(t) == 40]
        except FileNotFoundError:
            return []

    def _set_pending(self, thumbprints: list[str]) -> None:
        if thumbprints:
            self._pending_path.parent.mkdir(parents=True, exist_ok=True)
            self._pending_path.write_text("\n".join(sorted(set(thumbprints))) + "\n", encoding="ascii")
        else:
            self._pending_path.unlink(missing_ok=True)

    def _cleanup_pending(self, why: str) -> None:
        remaining = []
        for thumbprint in self._pending():
            try:
                WindowsCertificateStore().remove(thumbprint)
                print(f"Certificado {thumbprint[:8]}... removido do repositorio do Windows ({why})")
            except Exception:  # noqa: BLE001
                remaining.append(thumbprint)
                print(f"ATENCAO: nao foi possivel remover o certificado {thumbprint[:8]}...; remova pelo certmgr.msc")
        self._set_pending(remaining)

    # ------------------------------------------------------------------ execucao
    def _execute(self, pfx: bytes, password: str, record: LawyerRecord, totp_secret: str = "") -> None:
        session = self.session
        store = WindowsCertificateStore()
        thumbprint = None
        browser = None
        try:
            session.emit("step", text="Lendo o certificado A1")
            info = read_pfx(pfx, password)
            errors = validate_lawyer(record)
            if errors:
                raise ConfigurationError("; ".join(errors))
            if self.rehearsal:
                assert_rehearsal_ready(self.config, record)
            else:
                assert_live_ready(self.config, record)

            if store.contains(info.thumbprint_sha1):
                # Ja estava instalado antes do robo: usa e NAO remove ao final (nao e nosso).
                session.emit("step", text="Certificado ja estava no repositorio do Windows; sera mantido")
            else:
                session.emit("step", text=f"Instalando o certificado de {info.safe_summary()['titular']} no repositorio")
                self._set_pending(self._pending() + [info.thumbprint_sha1])
                thumbprint = info.thumbprint_sha1
                installed = store.install(pfx, password)
                if installed != thumbprint:
                    self._set_pending(self._pending() + [installed])
                    thumbprint = installed

            audit = AuditLog(self.audit_root)
            def _broadcast(event, state):
                session.emit("log", event=event, state=state)

                if event in ("advance_button_located", "advance_button_clicked"):
                    try:
                        import json as _json

                        last = audit.path.read_text(encoding="utf-8").splitlines()[-1]
                        text = _json.loads(last).get("details", {}).get("text", "")
                        verb = "BOTAO PARA AVANCAR" if event.endswith("located") else "CLICADO"
                        session.emit("step", text=f"{verb}: {text}")
                    except Exception:
                        pass
                    return

                if event != "adaptive_form_evaluated":
                    return

                try:
                    import json as _json

                    lines = audit.path.read_text(
                        encoding="utf-8"
                    ).splitlines()

                    details = _json.loads(
                        lines[-1]
                    ).get("details", {})

                    sections = {
                        "verified": "IDENTIDADE VALIDADA",
                        "filled": "PREENCHIDOS",
                        "preserved": "PRESERVADOS",
                        "absent": "AUSENTES",
                        "pending": "REVISAO NECESSARIA",
                        "blocked": "DIVERGENCIAS",
                    }

                    for key, label in sections.items():
                        items = details.get(key, [])

                        if not isinstance(items, list) or not items:
                            continue

                        names = [
                            str(item)
                            for item in items
                            if isinstance(item, str)
                        ]

                        session.emit(
                            "step",
                            text=label + ": " + ", ".join(names[:30]),
                        )

                    count = details.get("unknown_optional", 0)

                    if isinstance(count, int) and count:
                        session.emit(
                            "step",
                            text=f"CONTROLES OPCIONAIS DESCONHECIDOS: {count}",
                        )

                except Exception:
                    session.emit(
                        "step",
                        text="ATENCAO: relatorio detalhado indisponivel",
                    )

            audit.on_record = _broadcast
            session.emit("run", run_id=audit.run_id)

            signer = PJeOfficeSigner(
                self.config.signer,
                site_hosts=(urlparse(self.config.base_url).hostname or "",),
            )
            browser = PlaywrightBrowser(self.config)
            flow = PJeRegistrationFlow(
                self.config, record, info, password, browser, signer, audit,
                code_provider=lambda msg: self._provide_code(session, msg, totp_secret),
                rehearsal=self.rehearsal,
            )
            result = flow.run()
            serialized = asdict(result)
            serialized["status"] = result.status.value
            serialized["state"] = result.state.value
            audit.write_result(serialized)
            session.emit("done", status=result.status.value, reason=result.reason, run_id=audit.run_id)
        except (CertificateError, ConfigurationError) as exc:
            session.emit("error", reason=str(exc))
        except Exception as exc:  # noqa: BLE001 - a pagina mostra o tipo, o log tem o resto
            import traceback
            traceback.print_exc()
            session.emit("error", reason=f"Falha inesperada: {type(exc).__name__}")
        finally:
            password = ""
            totp_secret = ""  # melhor esforco: strings Python nao garantem limpeza da RAM
            if thumbprint:
                try:
                    store.remove(thumbprint)
                    self._set_pending([t for t in self._pending() if t != thumbprint])
                    session.emit("step", text="Certificado removido do repositorio")
                except Exception:
                    session.emit("step", text="ATENCAO: remova o certificado manualmente do repositorio do Windows")
            close_error = None
            if browser is not None:
                try:
                    if self.rehearsal:
                        session.inspection_active = True
                        session.emit(
                            "inspection_ready",
                            text="Certificado temporario tratado. "
                                 "Edge aberto para inspecao.",
                        )
                        # Sem prazo, fechar a aba prenderia o servidor (nova execucao recusada).
                        if not session.inspection_close.wait(INSPECTION_TIMEOUT_S):
                            session.emit("step", text="Tempo de inspecao esgotado; fechando o Edge")

                    browser.close()
                except Exception as exc:
                    close_error = type(exc).__name__
                    session.emit(
                        "step",
                        text="ERRO AO FECHAR EDGE: " + close_error,
                    )
                finally:
                    session.inspection_active = False

            session.running = False

            if self.rehearsal:
                session.emit(
                    "inspection_closed",
                    text=(
                        "Inspecao encerrada; fechamento solicitado."
                        if close_error is None
                        else "Falha no fechamento: " + close_error
                    ),
                    ok=close_error is None,
                )

    def _provide_code(self, session: _Session, message: str, totp_secret: str) -> "str | None":
        if not totp_secret:
            return self._ask_code(session, message)
        # Evita gerar codigo prestes a expirar. O horario do Windows deve estar sincronizado.
        import time
        segundos_restantes = 30 - (time.time() % 30)
        if segundos_restantes < 8:
            time.sleep(segundos_restantes + 0.3)
        session.emit("step", text="Codigo de autenticacao TOTP gerado localmente")
        return gerar_totp(totp_secret)

    def _ask_code(self, session: _Session, message: str) -> "str | None":
        session.emit("code_request", text=message)
        try:
            return session.code_box.get(timeout=self.config.signer.appear_timeout_s * 4 if self.config.signer else 180)
        except queue.Empty:
            return None


def _decode_pfx(value: str) -> bytes:
    import base64

    return base64.b64decode(value)


def _record_from_payload(raw: dict) -> LawyerRecord:
    return lawyer_from_dict(raw)


_PAGE = """<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cadastro de advogado no PJe</title>
<style>
  :root{color-scheme:light dark;--bg:#0f1115;--panel:#171a21;--ink:#e7e9ee;--muted:#9aa3b2;
        --line:#272b35;--accent:#5b8def;--ok:#3ecf8e;--warn:#e0a33e;--err:#e5484d;}
  *{box-sizing:border-box}
  body{margin:0;font:15px/1.5 "Inter",system-ui,sans-serif;background:var(--bg);color:var(--ink);
       display:grid;place-items:start center;padding:32px 16px}
  main{width:100%;max-width:560px}
  h1{font-size:1.35rem;font-weight:650;margin:0 0 4px}
  p.sub{color:var(--muted);margin:0 0 24px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:16px}
  label{display:block;font-weight:550;margin:12px 0 4px}
  input{width:100%;padding:10px 12px;background:#0d0f14;border:1px solid var(--line);
        border-radius:8px;color:var(--ink);font:inherit}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .drop{border:1.5px dashed var(--line);border-radius:10px;padding:22px;text-align:center;color:var(--muted);cursor:pointer}
  .drop.has{border-color:var(--accent);color:var(--ink)}
  button{margin-top:20px;width:100%;padding:12px;border:0;border-radius:9px;background:var(--accent);
         color:#fff;font:inherit;font-weight:600;cursor:pointer}
  button:disabled{opacity:.5;cursor:not-allowed}
  #steps{list-style:none;padding:0;margin:0}
  #steps li{padding:8px 0;border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:baseline}
  #steps li:last-child{border:0}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--muted);flex:none;translate:0 4px}
  .dot.ok{background:var(--ok)} .dot.err{background:var(--err)} .dot.warn{background:var(--warn)}
  .hidden{display:none}
  .note{font-size:.82rem;color:var(--muted);margin-top:8px}
  .verdict{font-weight:650;padding:12px;border-radius:9px;margin-top:4px}
  .verdict.ok{background:rgba(62,207,142,.12);color:var(--ok)}
  .verdict.err{background:rgba(229,72,77,.12);color:var(--err)}
  .mode{font-weight:600;padding:10px 12px;border-radius:9px;margin:0 0 16px}
  .mode.ensaio{background:rgba(224,163,62,.14);color:var(--warn)}
  .mode.real{background:rgba(229,72,77,.14);color:var(--err)}
</style></head><body><main>
  <h1>Cadastro de advogado no PJe</h1>
  <p class="sub">O certificado e a senha ficam neste computador e sao apagados ao final.</p>
  <p class="mode" id="mode"></p>

  <div class="card" id="form">
    <div class="drop" id="drop">Arraste o arquivo .pfx / .p12 aqui ou clique para escolher
      <input type="file" id="pfx" accept=".pfx,.p12" class="hidden"></div>
    <label>Senha do certificado</label>
    <input type="password" id="password" autocomplete="off">
    <label>Chave secreta 2FA / TOTP (opcional)</label>
    <input type="password" id="totp_secret" autocomplete="off" spellcheck="false"
      placeholder="Base32: normalmente 32 caracteres (A-Z e 2-7)">
    <p class="note">Usada apenas nesta execucao para gerar o codigo quando o PJe solicitar. Deixe vazio para informar o 2FA manualmente. Nao cole QR Code ou dados aqui no chat.</p>
    <div class="row">
      <div><label>Nome do titular</label><input id="nome"></div>
      <div><label>CPF</label><input id="cpf" inputmode="numeric"></div>
    </div>
    <div class="row">
      <div><label>OAB numero</label><input id="oab_numero" inputmode="numeric"></div>
      <div><label>OAB UF</label><input id="oab_uf" maxlength="2"></div>
    </div>
    <div class="row">
      <div><label>E-mail</label><input id="email"></div>
      <div><label>Telefone</label><input id="telefone" inputmode="numeric"></div>
    </div>
    <div class="row">
      <div><label>CEP</label><input id="cep" inputmode="numeric"></div>
      <div><label>Numero</label><input id="numero"></div>
    </div>
    <label>Logradouro</label><input id="logradouro">
    <div class="row">
      <div><label>Bairro</label><input id="bairro"></div>
      <div><label>Complemento</label><input id="complemento"></div>
    </div>
    <div class="row">
      <div><label>Cidade</label><input id="cidade"></div>
      <div><label>UF</label><input id="uf" maxlength="2"></div>
    </div>
    <label>Referencia da autorizacao do advogado</label><input id="autorizacao_ref"
      placeholder="contrato/termo que autoriza o uso do A1 (minimo 3 caracteres)">
    <p class="note">O cadastro so segue se nome e CPF do certificado baterem com estes dados.</p>
    <button id="go" disabled>Iniciar cadastro</button>
  </div>

  <div class="card hidden" id="progress">
    <ul id="steps"></ul>
    <div id="codebox" class="hidden">
      <label>Codigo de segundo fator</label>
      <input id="code" inputmode="numeric" autocomplete="off">
      <button id="sendcode">Enviar codigo</button>
    </div>
    <div id="verdict" class="hidden"></div>
    <button id="close_inspection" class="hidden" type="button">Encerrar inspecao e fechar Edge</button>
    <button id="retry" class="hidden" type="button">Corrigir dados e tentar novamente</button>
  </div>
<script>
const $=id=>document.getElementById(id);
const MODE="__MODE__";
$("mode").className="mode "+MODE;
$("mode").textContent=MODE==="ensaio"
  ?"Modo ENSAIO: entra no PJe e confere os dados, mas para antes de enviar. Nada e assinado."
  :"Modo REAL: o cadastro sera enviado e o termo de compromisso assinado com este certificado.";
$("go").textContent=MODE==="ensaio"?"Iniciar ensaio":"Iniciar cadastro";
let pfxB64=null;
const drop=$("drop"),file=$("pfx");
drop.onclick=()=>file.click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add("has")};
drop.ondragleave=()=>drop.classList.remove("has");
drop.ondrop=e=>{e.preventDefault();file.files=e.dataTransfer.files;load()};
file.onchange=load;
function load(){const f=file.files[0];if(!f)return;const r=new FileReader();
  r.onload=()=>{pfxB64=r.result.split(",")[1];drop.textContent=f.name;drop.classList.add("has");check()};
  r.readAsDataURL(f);}
const req=["password","nome","cpf","oab_numero","oab_uf","email","telefone","cep","numero","logradouro","bairro","cidade","uf","autorizacao_ref"];
req.forEach(id=>$(id).oninput=check);
function check(){$("go").disabled=!(pfxB64&&req.every(id=>$(id).value.trim())
  &&$("autorizacao_ref").value.trim().length>=3)}
function addStep(text,cls){const li=document.createElement("li");
  li.innerHTML=`<span class="dot ${cls||''}"></span><span>${text}</span>`;$("steps").append(li);}
$("go").onclick=async()=>{
  $("form").classList.add("hidden");$("progress").classList.remove("hidden");
  const cadastro={nome_esperado:$("nome").value,cpf_esperado:$("cpf").value,
    oab_numero:$("oab_numero").value,oab_uf:$("oab_uf").value,email:$("email").value,
    telefone:$("telefone").value,autorizacao_ref:$("autorizacao_ref").value,
    endereco:{cep:$("cep").value,logradouro:$("logradouro").value,numero:$("numero").value,
      complemento:$("complemento").value,bairro:$("bairro").value,cidade:$("cidade").value,uf:$("uf").value}};
  let es=null;
  const receive=ev=>{const m=JSON.parse(ev.data);
    if(m.kind==="step")addStep(m.text);
    else if(m.kind==="log")addStep(m.event.replace(/_/g," "),"ok");
    else if(m.kind==="code_request"){addStep(m.text,"warn");$("codebox").classList.remove("hidden");}
    else if(m.kind==="inspection_ready"){
      addStep(m.text,"warn");
      $("close_inspection").classList.remove("hidden");
      $("retry").classList.add("hidden");
    }
    else if(m.kind==="inspection_closed"){
      addStep(m.text,m.ok===false?"err":"ok");
      $("close_inspection").classList.add("hidden");
      $("retry").classList.remove("hidden");
      es.close();
    }
    else if(m.kind==="done"){const ok=["COMPLETED","ALREADY_REGISTERED","REHEARSAL_OK"].includes(m.status);
      show(ok,m.status+" — "+m.reason);if(MODE!=="ensaio")es.close();}
    else if(m.kind==="error"){show(false,m.reason);if(MODE!=="ensaio")es.close();}};
  const started=await fetch("/start",{method:"POST",body:JSON.stringify({pfx_base64:pfxB64,password:$("password").value,totp_secret:$("totp_secret").value,cadastro})});
  if(!started.ok){
    show(false,"Nao foi possivel iniciar a execucao.");
    return;
  }
  es=new EventSource("/events");
  es.onmessage=receive;
};
$("close_inspection").onclick=async()=>{
  const button=$("close_inspection");
  button.disabled=true;

  try{
    const response=await fetch("/close-inspection",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:"{}"
    });

    if(!response.ok)throw new Error("Falha ao encerrar inspecao");

    addStep("Fechamento do Edge solicitado.","warn");
  }catch(error){
    button.disabled=false;
    addStep("Nao foi possivel fechar o Edge pelo painel.","err");
  }
};
$("sendcode").onclick=async()=>{await fetch("/code",{method:"POST",body:JSON.stringify({code:$("code").value})});
  $("codebox").classList.add("hidden");};
function show(ok,text){const v=$("verdict");v.textContent=text;v.className="verdict "+(ok?"ok":"err");
  v.classList.remove("hidden");
  if(MODE!=="ensaio")$("retry").classList.remove("hidden");}
$("retry").onclick=()=>{
   $("form").classList.remove("hidden");$("progress").classList.add("hidden");
   $("steps").replaceChildren();$("codebox").classList.add("hidden");
   $("verdict").classList.add("hidden");$("retry").classList.add("hidden");
   check();
 };
</script></main></body></html>"""
