"""Caixa do Gmail lida pelo NAVEGADOR, sem senha de app (sem IMAP).

Quem abre o Chrome e o proprio robo (Start-Process no Windows), com um PERFIL SALVO por conta de
e-mail (runtime\\gmail-perfis\\<codigo>, fora do Git) e uma porta de depuracao. O Playwright
NAO abre o navegador: ele apenas CONECTA (connect_over_cdp) ao Chrome ja aberto para digitar o
login e ler o link. Isso importa: o Chrome aberto pelo Playwright entra com a marca de automacao
(navigator.webdriver = true, flag --enable-automation) que a tela de login do Google reconhece;
o Chrome aberto normalmente, nao. O login (e-mail, senha e codigo do autenticador, gerado pelo
totp.py) e feito uma vez; nas proximas vezes a sessao ja esta salva no perfil e o robo so le a caixa.

Regras:
- nada de contornar CAPTCHA, verificacao por celular ou "navegador nao seguro": nesses casos o
  robo abre um Chrome normal com o mesmo perfil para o operador entrar a mao uma vez, e a sessao
  fica salva;
- senha, chave 2FA e links nunca aparecem em mensagens, logs ou auditoria;
- cada acesso roda numa thread propria: o Playwright sincrono nao aceita dois navegadores na
  mesma thread e o do PJe pode estar aberto ao mesmo tempo.

Teste de uma conta (nao toca no PJe):  python -m pje_rpa.gmail_web --linha 5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .email_inbox import _ACTION_HINT, is_pje_link
from .gmail_senha_app import Assist, GmailError, _visivel, entrar_no_google

LOGIN_URL = "https://accounts.google.com/ServiceLogin?service=mail&continue=https://mail.google.com/mail/u/0/"
CAIXA_URL = "https://mail.google.com/mail/u/0/"
# e-mails do PJe dos ultimos 2 dias, em qualquer pasta (o aviso de senha as vezes cai no spam)
BUSCA = 'in:anywhere newer_than:2d {senha pje "jus.br"}'
PERFIS = Path("runtime") / "gmail-perfis"
_LINHAS = "div[role=main] tr.zA, div[role=main] tr[role=row]"

# Onde o Chrome (ou o Edge) costuma estar no Windows. Pode ser trocado por --navegador <caminho>.
_CHROME_WIN = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def pasta_do_perfil(email: str, raiz: Path = PERFIS) -> Path:
    """Uma pasta de perfil do navegador por conta; o nome nao revela o e-mail."""
    return raiz / hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]


def achar_navegador(preferido: str = "") -> str:
    """Caminho do Chrome (ou Edge) na maquina. `preferido` pode ser um caminho ou 'chrome'/'edge'."""
    if preferido and os.path.sep in preferido and Path(os.path.expandvars(preferido)).exists():
        return os.path.expandvars(preferido)
    nomes = {"chrome": ("chrome", "google-chrome", "google-chrome-stable"),
             "edge": ("msedge", "microsoft-edge")}.get(preferido.lower(), ())
    for nome in nomes or ("chrome", "google-chrome", "google-chrome-stable", "msedge", "microsoft-edge"):
        achado = shutil.which(nome)
        if achado:
            return achado
    for caminho in _CHROME_WIN:
        expandido = os.path.expandvars(caminho)
        if (not preferido or preferido.lower() in expandido.lower()) and Path(expandido).exists():
            return expandido
    raise GmailError("nao encontrei o Chrome (nem o Edge) nesta maquina; passe --navegador <caminho do .exe>")


def _porta_livre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()
    return porta


def _esperar_cdp(porta: int, prazo_s: float = 25) -> str:
    """Espera o Chrome publicar o endpoint de depuracao e devolve a URL do WebSocket."""
    fim = time.monotonic() + prazo_s
    ultimo = ""
    while time.monotonic() < fim:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{porta}/json/version", timeout=1) as resp:
                dados = json.load(resp)
            if dados.get("webSocketDebuggerUrl"):
                return f"http://127.0.0.1:{porta}"
        except Exception as exc:  # noqa: BLE001 - ainda subindo
            ultimo = type(exc).__name__
        time.sleep(0.3)
    raise GmailError(f"o Chrome nao abriu a porta de depuracao a tempo ({ultimo or 'sem resposta'})")


def _args_navegador(navegador: str, perfil: Path, porta: int, url: str, headless: bool) -> list[str]:
    args = [navegador, f"--remote-debugging-port={porta}", f"--user-data-dir={perfil.resolve()}",
            "--no-first-run", "--no-default-browser-check", "--new-window"]
    if headless:  # so em teste/servidor; na VM o login roda com janela visivel
        args += ["--headless=new", "--disable-gpu"]
        if sys.platform != "win32":
            args.append("--no-sandbox")
    return args + [url]


def abrir_chrome_cdp(pw, perfil: Path, navegador: str, headless: bool = False):
    """Abre o Chrome REAL (nao pelo Playwright) e conecta por CDP. Devolve o contexto ja aberto;
    o processo do Chrome fica anexado em `_rpa_proc` para ser encerrado depois."""
    caminho = achar_navegador(navegador)
    porta = _porta_livre()
    perfil.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(_args_navegador(caminho, perfil, porta, LOGIN_URL, headless),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url_cdp = _esperar_cdp(porta)
        browser = pw.chromium.connect_over_cdp(url_cdp)
    except BaseException:
        proc.terminate()
        raise
    contexto = browser.contexts[0] if browser.contexts else browser.new_context()
    contexto._rpa_proc = proc  # encerrado no finally de _rodar
    contexto._rpa_browser = browser
    return contexto


def _pagina_controlada(contexto):
    """Uma aba para o robo dirigir. Prefere a ja aberta no Google; senao abre uma limpa."""
    paginas = list(getattr(contexto, "pages", []) or [])
    for pg in paginas:
        try:
            if "google.com" in (pg.url or ""):
                return pg
        except Exception:  # noqa: BLE001
            continue
    return paginas[0] if paginas else contexto.new_page()


def na_caixa(page) -> bool:
    try:
        if not page.url.startswith("https://mail.google.com/mail/"):
            return False
    except Exception:  # noqa: BLE001
        return False
    return _visivel(page, "input[name=q], div[role=main]") is not None


def _destino_real(url: str) -> str:
    """O Gmail pode embrulhar o link em google.com/url?q=<destino>."""
    parsed = urlparse(url)
    if (parsed.hostname or "").endswith("google.com") and parsed.path == "/url":
        return (parse_qs(parsed.query).get("q") or [url])[0]
    return url


def links_de_senha_na_tela(page) -> list[str]:
    try:
        hrefs = page.evaluate("() => [...document.querySelectorAll('a[href]')].map(a => a.href)")
    except Exception:  # noqa: BLE001
        return []
    saida: list[str] = []
    for href in hrefs:
        url = _destino_real(str(href or "")).rstrip(".,;")
        if is_pje_link(url) and _ACTION_HINT.search(url) and url not in saida:
            saida.append(url)
    return saida


def ler_links(page, max_conversas: int = 3) -> list[str]:
    """Abre as conversas mais recentes que parecem do PJe e devolve os links de senha (.jus.br)."""
    busca = CAIXA_URL + "#search/" + quote(BUSCA, safe="")
    page.goto(busca)
    linhas = page.locator(_LINHAS)
    fim = time.monotonic() + 10
    while time.monotonic() < fim and linhas.count() == 0:
        page.wait_for_timeout(500)
    links: list[str] = []
    for i in range(min(linhas.count(), max_conversas)):
        try:
            linhas.nth(i).click(timeout=5000)
        except Exception:  # noqa: BLE001
            break
        fim = time.monotonic() + 6
        achados: list[str] = []
        while time.monotonic() < fim and not achados:
            page.wait_for_timeout(500)
            achados = links_de_senha_na_tela(page)
        links += [u for u in achados if u not in links]
        page.goto(busca)  # volta para a lista
        page.wait_for_timeout(1500)
    return links


def _abrir_navegador_normal(perfil: Path, url: str, navegador: str = "") -> None:
    """Chrome comum (sem depuracao, sem automacao) no mesmo perfil, para o operador logar a mao."""
    if sys.platform != "win32":
        return
    try:
        caminho = achar_navegador(navegador)
    except GmailError:
        return
    subprocess.Popen([caminho, f"--user-data-dir={perfil.resolve()}", "--no-first-run", url])


@dataclass
class GmailWebInbox:
    """Mesma interface da caixa IMAP (known_links / wait_for_password_link), pelo navegador.

    O robo abre o Chrome real por conta (perfil salvo) e conecta por CDP. Login so na 1a vez."""

    email: str
    senha: str
    chave_2fa: str
    assist: Assist | None = None  # mostra o motivo; "" = ja fiz o login, qualquer texto = desistir
    perfil: Path | None = None
    navegador: str = "chrome"  # Chrome por padrao; "edge" ou o caminho do .exe tambem valem
    headless: bool = False
    poll_s: int = 15
    abrir_contexto: Callable = abrir_chrome_cdp
    abrir_navegador_normal: Callable[..., None] = _abrir_navegador_normal
    playwright_factory: Callable | None = None
    digitado: list[str] = field(default_factory=list)  # etapas digitadas no ultimo acesso
    login_manual: bool = False  # o ultimo acesso precisou do login a mao
    _vistos: set[str] = field(default_factory=set)

    def check_login(self) -> None:
        self._sessao(lambda page: None)

    def known_links(self) -> frozenset[str]:
        """Links de senha que ja estao na caixa: o link novo tem de ser diferente."""
        links = frozenset(self._sessao(ler_links))
        self._vistos |= links
        return links

    def wait_for_password_link(self, since: datetime, timeout_s: int = 300,
                               on_wait: Callable[[int], None] | None = None,
                               exclude: frozenset[str] = frozenset()) -> str | None:
        def esperar(page) -> str | None:
            deadline = time.monotonic() + timeout_s
            while True:
                try:
                    links = ler_links(page)
                except Exception:  # noqa: BLE001 - falha passageira da tela: tenta de novo
                    links = []
                novo = next((u for u in links if u not in exclude and u not in self._vistos), None)
                if novo:
                    return novo
                remaining = int(deadline - time.monotonic())
                if remaining <= 0:
                    return None
                if on_wait:
                    on_wait(remaining)
                time.sleep(min(self.poll_s, max(remaining, 1)))

        return self._sessao(esperar)

    # ------------------------------------------------------------------ navegador
    def _sessao(self, trabalho: Callable):
        resultado: dict = {}

        def rodar() -> None:
            try:
                resultado["valor"] = self._rodar(trabalho)
            except BaseException as exc:  # noqa: BLE001 - devolvido para a thread principal
                resultado["erro"] = exc

        thread = threading.Thread(target=rodar, daemon=True)
        thread.start()
        thread.join()
        if "erro" in resultado:
            raise resultado["erro"]
        return resultado.get("valor")

    def _rodar(self, trabalho: Callable):
        if not (self.email and self.senha and self.chave_2fa):
            raise GmailError("Faltam e-mail, senha do e-mail ou chave 2FA do e-mail na planilha")
        factory = self.playwright_factory
        if factory is None:
            from playwright.sync_api import sync_playwright
            factory = sync_playwright
        perfil = self.perfil or pasta_do_perfil(self.email)
        perfil.mkdir(parents=True, exist_ok=True)
        self.digitado, self.login_manual = [], False
        pw = factory().start()
        contexto = None
        try:
            contexto = self.abrir_contexto(pw, perfil, self.navegador, self.headless)
            page = _pagina_controlada(contexto)
            try:
                entrar_no_google(page, LOGIN_URL, na_caixa, self.email, self.senha, self.chave_2fa, None,
                                 destino="na caixa do Gmail", ao_digitar=self.digitado.append)
            except GmailError as exc:
                if self.assist is None:
                    raise
                # O Google barrou o login do robo. Nao contorna: o operador entra a mao num Chrome
                # normal, com o mesmo perfil, e a sessao fica salva para o robo.
                self._fechar(contexto)
                contexto = None
                self.abrir_navegador_normal(perfil, LOGIN_URL, self.navegador)
                if self.assist(str(exc)).strip():
                    raise GmailError("login do Gmail nao concluido") from None
                self.login_manual = True
                contexto = self.abrir_contexto(pw, perfil, self.navegador, self.headless)
                page = _pagina_controlada(contexto)
                entrar_no_google(page, LOGIN_URL, na_caixa, self.email, self.senha, self.chave_2fa, None,
                                 prazo_s=60, destino="na caixa do Gmail", ao_digitar=self.digitado.append)
            return trabalho(page)
        except GmailError:
            raise
        except Exception as exc:  # noqa: BLE001 - mensagem do Playwright pode conter o que foi digitado
            raise GmailError("Falha no navegador ao ler o Gmail: "
                             + self._motivo_limpo(exc, locals().get("page"))) from None
        finally:
            self._fechar(contexto)
            pw.stop()

    def _motivo_limpo(self, exc: Exception, page=None) -> str:
        """Mensagem do erro sem segredos (o Playwright repete o valor digitado em fill(...)), mais
        a tela onde parou (so host e caminho, nunca a query)."""
        msg = str(exc).split("\n", 1)[0]
        for seg in (self.senha, self.chave_2fa, self.email):
            if seg:
                msg = msg.replace(seg, "***")
        msg = re.sub(r'fill\((["\']).*?\1\)', "fill(***)", msg)
        onde = ""
        try:
            if page is not None:
                parsed = urlparse(page.url or "")
                if parsed.netloc:
                    onde = f" [tela: {parsed.netloc}{parsed.path}]"
        except Exception:  # noqa: BLE001
            pass
        return (f"{type(exc).__name__}: {msg}"[:280]) + onde

    @staticmethod
    def _fechar(contexto) -> None:
        if contexto is None:
            return
        browser = getattr(contexto, "_rpa_browser", None)
        proc = getattr(contexto, "_rpa_proc", None)
        try:
            (browser or contexto).close()  # Browser.close via CDP: faz o Chrome sair e gravar a sessao
        except Exception:  # noqa: BLE001
            pass
        if proc is not None:
            try:
                proc.wait(timeout=10)  # espera o Chrome gravar cookies/sessao no perfil antes de seguir
            except Exception:  # noqa: BLE001
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass


# ---------------------------------------------------------------------- teste de uma conta
def _todas_as_linhas(caminho: Path, senha_planilha: Callable[[], str]) -> list[tuple[int, dict[str, str]]]:
    from .planilha import _ALIAS, _norm, _rows_xlsx

    rows = _rows_xlsx(caminho, senha_planilha)
    if not rows:
        return []
    cab = [_ALIAS.get(_norm(h), "") for h in rows[0]]
    saida = []
    for numero, row in enumerate(rows[1:], start=2):
        dados = {col: (row[i] if i < len(row) else "").strip() for i, col in enumerate(cab) if col}
        if dados.get("nome") or dados.get("email"):
            saida.append((numero, dados))
    return saida


def _marcada(dados: dict) -> bool:
    from .planilha import _norm

    return _norm(dados.get("executar", "")) in {"s", "sim", "yes", "y", "1", "true"}


def _linha_da_planilha(caminho: Path, numero: int, senha_planilha: Callable[[], str]) -> dict[str, str]:
    from .planilha import _ALIAS, _norm, _rows_xlsx

    rows = _rows_xlsx(caminho, senha_planilha)
    if numero < 2 or numero > len(rows):
        raise GmailError(f"A planilha nao tem a linha {numero}")
    cab = [_ALIAS.get(_norm(h), "") for h in rows[0]]
    row = rows[numero - 1]
    return {col: (row[i] if i < len(row) else "").strip() for i, col in enumerate(cab) if col}


def _assist_console(motivo: str) -> str:
    print(f"\n  >>> Gmail: {motivo}.")
    print("      O robo NAO contorna isso. Abri o navegador normal com o perfil desta conta: faca o login nele")
    print("      (e-mail, senha e codigo do autenticador), espere a caixa abrir e FECHE essa janela.")
    return input("  [Enter] fiz o login e fechei a janela | [P] desistir: ")


def _testar_conta(numero: int, dados: dict, navegador: str) -> int:
    nome = dados.get("nome") or "?"
    email = dados.get("email", "")
    faltam = [c for c in ("email", "email_senha", "email_2fa") if not dados.get(c)]
    print(f"\n==================== Linha {numero}: {nome} ====================")
    if faltam:
        print("  PULADA: faltam na planilha: " + ", ".join(faltam)
              + "  (email_senha = senha do e-mail; email_2fa = chave de 32 caracteres do autenticador)")
        return 2
    caixa = GmailWebInbox(email, dados["email_senha"], dados["email_2fa"], assist=_assist_console, navegador=navegador)

    def como_entrou() -> str:
        if caixa.login_manual:
            return "login feito A MAO (o Google barrou o login automatico)"
        if caixa.digitado:
            return "login AUTOMATICO (digitou: " + ", ".join(caixa.digitado) + ")"
        return "sessao ja estava salva (nao digitou nada)"

    try:
        print("  1/3 Login no Gmail (o navegador vai abrir)...")
        caixa.check_login()
        print("      OK: " + como_entrou())
        print("  2/3 Reabrindo para conferir se a sessao ficou salva...")
        caixa.check_login()
        print("      " + ("OK: " if not caixa.digitado and not caixa.login_manual else "ATENCAO: ") + como_entrou())
        print("  3/3 Procurando e-mails do PJe com link de senha (ultimos 2 dias)...")
        links = caixa.known_links()
        print(f"      OK: {len(links)} link(s) de senha do PJe encontrado(s) (os links nao sao mostrados)")
    except GmailError as exc:
        print(f"  FALHA: {exc}")
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    import getpass

    parser = argparse.ArgumentParser(
        description="Testa a leitura do Gmail pelo navegador (nao toca no PJe). "
                    "Sem --linha/--linhas, testa todas as linhas marcadas com executar=S.")
    parser.add_argument("--linha", type=int, help="uma linha (como no Excel)")
    parser.add_argument("--linhas", default="", help="varias linhas, ex.: 17,32")
    parser.add_argument("--planilha", default="planilhas/advogados.xlsx")
    parser.add_argument("--navegador", default="chrome", help="chrome (padrao), edge ou o caminho do .exe")
    args = parser.parse_args(argv)
    pedidas = {int(n) for n in args.linhas.replace(";", ",").split(",") if n.strip().isdigit()}
    if args.linha:
        pedidas.add(args.linha)
    try:
        linhas = _todas_as_linhas(Path(args.planilha),
                                  lambda: getpass.getpass("Senha para abrir a planilha de advogados: "))
    except Exception as exc:  # noqa: BLE001
        print(f"FALHA ao ler a planilha: {exc}")
        return 2
    if pedidas:
        alvo = [(n, d) for n, d in linhas if n in pedidas]
        nao_achadas = pedidas - {n for n, _ in alvo}
        if nao_achadas:
            print("Linha(s) nao encontrada(s) na planilha: " + ", ".join(map(str, sorted(nao_achadas))))
    else:
        alvo = [(n, d) for n, d in linhas if _marcada(d)]
    if not alvo:
        print("Nenhuma linha para testar (passe --linha/--linhas ou marque executar=S).")
        return 2
    print(f"Gmail pelo navegador (teste, nao toca no PJe): {len(alvo)} conta(s) -> linhas "
          + ", ".join(str(n) for n, _ in alvo))
    piores = 0
    for numero, dados in alvo:
        piores = max(piores, _testar_conta(numero, dados, args.navegador))
    print("\n================ RESUMO ================")
    print("  Todas as contas testadas passaram." if piores == 0
          else "  Alguma conta falhou ou foi pulada; veja acima.")
    return piores


if __name__ == "__main__":
    raise SystemExit(main())
