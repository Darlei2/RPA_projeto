"""Assistente de console para as etapas de senha (scripts\\criar-senha.ps1).

Fluxo por tribunal: pede o link ("Solicitar nova senha"), le o link na caixa de e-mail (IMAP)
ou recebe o link colado, gera uma senha forte, cadastra a senha e testa CPF + senha + 2FA.
No fim, grava uma planilha protegida por senha com uma linha por tribunal.

Senha da caixa de e-mail, senha do PJe, chave do 2FA e link nunca vao para a linha de
comando, o historico do PowerShell, o log ou a auditoria. A senha do PJe so vai para a
planilha criptografada (e para a tela, se a planilha nao puder ser gravada).
"""
from __future__ import annotations

import getpass
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .audit import AuditLog
from .credencial import diagnose, request_password_link, set_password, test_login
from .entrega import LinhaCredencial, salvar_planilha
from .senha_forte import gerar_senha
from .totp import gerar_totp, normalizar_chave

LINK_TIMEOUT_S = 300


def _ask(prompt: str) -> str:
    return input(prompt).strip()


def _yes(prompt: str, default: bool = False) -> bool:
    hint = "[S/n]" if default else "[s/N]"
    answer = _ask(f"{prompt} {hint}: ").casefold()
    return default if not answer else answer in {"s", "sim", "y"}


def _code_provider(secret: str):
    def provide(message: str) -> str | None:
        if not secret:
            return _ask(f"{message}: ") or None
        if 30 - (time.time() % 30) < 8:  # evita codigo prestes a expirar
            time.sleep(30 - (time.time() % 30) + 0.3)
        print("Codigo TOTP gerado localmente")
        return gerar_totp(secret)
    return provide


def _clipboard_text() -> str:
    """Le o texto da area de transferencia do Windows (ctypes; fallback: Get-Clipboard)."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32")
        kernel32 = ctypes.WinDLL("kernel32")
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        if user32.OpenClipboard(None):
            try:
                handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
                if handle:
                    pointer = kernel32.GlobalLock(handle)
                    try:
                        return ctypes.wstring_at(pointer) if pointer else ""
                    finally:
                        kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
    except Exception:  # noqa: BLE001
        pass
    try:
        import subprocess

        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def _clear_clipboard() -> None:
    try:
        import ctypes

        user32 = ctypes.WinDLL("user32")
        if user32.OpenClipboard(None):
            user32.EmptyClipboard()
            user32.CloseClipboard()
    except Exception:  # noqa: BLE001
        pass


def _secret(prompt: str, allow_empty: bool = False) -> str:
    """Segredo sem eco. No console do Windows, colar num campo oculto nao funciona: o operador
    copia o valor e aperta Enter (campo vazio) e ele e lido da area de transferencia."""
    typed = getpass.getpass(prompt + "\n  -> COPIE o valor e aperte Enter aqui (ou digite e Enter): ")
    typed = "".join(ch for ch in typed if ch.isprintable()).strip()
    if len(typed) >= 4:
        return typed
    value = _clipboard_text().strip()
    if value:
        size = len(re.sub(r"\s", "", value))
        print(f"  lido da area de transferencia: {size} caracteres (sem espacos)")
        _clear_clipboard()
        return value
    if not allow_empty:
        print("  area de transferencia vazia")
    return ""


def _report_failure(browser, audit, step_name: str, pause: bool = True) -> None:
    """Mostra o que estava na tela, salva um print local e deixa o Edge aberto para conferir."""
    import json

    info = diagnose(browser, audit.run_dir / f"falha-{step_name}.png")
    print("\n--- Tela no momento da falha (numeros mascarados) ---")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print("Print salvo em:", info.get("print", "(nao foi possivel salvar)"))
    if pause:
        _ask("\nO Edge continua aberto para voce conferir. Pressione Enter para continuar... ")


def _setup_inbox(email_address: str):
    """Pergunta como acessar a caixa. Devolve None quando o operador vai colar o link."""
    from .email_inbox import ImapInbox, guess_imap_host

    if not _yes("Ler o link automaticamente na caixa de e-mail (IMAP)?", default=True):
        return None
    host = os.environ.get("PJE_RPA_IMAP_HOST") or guess_imap_host(email_address) or ""
    host = _ask(f"Servidor IMAP [{host}]: ") or host if host else _ask("Servidor IMAP (ex.: imap.gmail.com): ")
    user = os.environ.get("PJE_RPA_IMAP_USER") or email_address
    user = _ask(f"Usuario da caixa [{user}]: ") or user
    env_secret = os.environ.get("PJE_RPA_IMAP_SENHA")
    for attempt in range(1, 4):
        secret = env_secret if (env_secret and attempt == 1) else _secret(
            "Senha de app da caixa de e-mail (16 letras; Gmail: myaccount.google.com/apppasswords)")
        secret = re.sub(r"\s", "", secret or "")
        if not secret:
            break
        inbox = ImapInbox(host=host, user=user, password=secret)
        try:
            inbox.check_login()
            print("OK: caixa de e-mail acessada")
            return inbox
        except Exception as exc:  # noqa: BLE001
            print(f"FALHA ao entrar na caixa de e-mail: {_imap_reason(exc, len(secret))}")
        if attempt < 3 and not _yes("Tentar a senha de app de novo?", default=True):
            break
    print("O link sera colado manualmente.")
    return None


def _imap_reason(exc: Exception, size: int) -> str:
    """Explica a recusa do servidor sem mostrar a senha (o servidor nunca a ecoa)."""
    text = str(exc.args[0] if exc.args else exc)
    if isinstance(exc.args[0] if exc.args else None, bytes):
        text = exc.args[0].decode(errors="replace")
    hint = ""
    if "AUTHENTICATIONFAILED" in text.upper() or "INVALID CREDENTIALS" in text.upper():
        hint = " -> senha recusada: use a senha de app (16 letras), nao a senha normal do Gmail"
        if size != 16:
            hint += f"; voce colou {size} caracteres (esperado 16: colou mais de uma vez?)"
    elif "APPLICATION-SPECIFIC" in text.upper():
        hint = " -> o Gmail exige senha de app (ative a verificacao em 2 etapas e gere em apppasswords)"
    elif isinstance(exc, OSError):
        hint = " -> sem conexao com o servidor IMAP (rede/firewall da VM?)"
    return f"{type(exc).__name__}: {text[:150]}{hint}"


def _clean_2fa_key(raw: str) -> str:
    """Aceita a chave pura (com espacos, hifens ou '=' no fim) ou o link otpauth:// do QR code."""
    from urllib.parse import parse_qs, unquote, urlparse

    raw = (raw or "").strip()
    if raw.lower().startswith("otpauth://"):
        raw = (parse_qs(urlparse(raw).query).get("secret") or [""])[0]
    return re.sub(r"[\s=-]+", "", unquote(raw)).upper()


def _ask_2fa_key() -> str:
    for _ in range(3):
        if not _yes("Voce tem a CHAVE do 2FA do PJe (Base32) para o robo gerar os codigos?", default=True):
            return ""
        raw = _secret("Chave do 2FA do PJe em Base32 (letras A-Z e numeros 2-7)", allow_empty=True)
        key = _clean_2fa_key(raw)
        if not key:
            return ""
        try:
            return normalizar_chave(key)
        except ValueError:
            if key.isdigit() and len(key) in (6, 8):
                print("Isso parece o CODIGO de 6 digitos, nao a CHAVE. Aperte Enter vazio para digitar o codigo na hora.")
            else:
                bad = sorted({c for c in key if c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"})
                why = f"tem caracteres que nao existem em Base32 ({len(bad)} tipo(s), ex.: 0, 1, 8, 9 ou simbolos)" if bad else "formato invalido"
                print(f"Chave recusada: {len(key)} caracteres, {why}. Colou mais de uma vez? Tente de novo.")
    print("Seguindo sem a chave: o codigo do 2FA sera pedido na hora.")
    return ""


def _get_link(inbox, since: datetime, known: frozenset[str] = frozenset()) -> tuple[str, bool]:
    if inbox is not None:
        print(f"Procurando o e-mail do PJe na caixa (ate {LINK_TIMEOUT_S // 60} min)...")
        link = inbox.wait_for_password_link(
            since, LINK_TIMEOUT_S, on_wait=lambda left: print(f"  ainda nao chegou ({left}s restantes)"),
            exclude=known)
        if link:
            print("OK: link de senha encontrado no e-mail")
            return link, True
        print("O e-mail nao chegou no prazo (confira tambem o spam).")
    return _ask("Cole o link recebido no e-mail (Enter vazio = pular este tribunal): "), False


def _passwords() -> tuple[str, str]:
    """Primeiro so letras e numeros (20, aceita ate pelo TJAP); reserva com simbolos (16)."""
    return gerar_senha(20, especiais=False), gerar_senha(16)


def _one_tribunal(config, audit_root: Path, lawyer: dict, inbox, secret_2fa: str,
                  pause: bool = True) -> LinhaCredencial:
    from .browser import PlaywrightBrowser

    print(f"\n== {config.tribunal} ==")
    linha = LinhaCredencial(config.tribunal, config.base_url, lawyer["nome"], lawyer["cpf"], lawyer["email"],
                            chave_2fa=lawyer["chave_2fa_exibir"])
    audit = AuditLog(audit_root)
    audit.record("credential_wizard_started", "CREDENTIAL", {"tribunal": config.tribunal})
    browser = PlaywrightBrowser(config)
    status = "CREDENTIAL_FAILED"
    try:
        # Sempre pede um link novo: links de pedidos anteriores sao invalidados pelo PJe.
        known = frozenset()
        if inbox is not None:
            try:
                known = inbox.known_links()  # o link novo tem de ser diferente dos que ja estao na caixa
            except Exception as exc:  # noqa: BLE001
                print(f"  aviso: nao foi possivel ler a caixa antes do pedido ({type(exc).__name__})")
        since = datetime.now(timezone.utc)
        requested = request_password_link(browser, config, lawyer["cpf"], lawyer["email"])
        audit.record("password_link_requested" if requested.ok else "password_link_request_failed",
                     "CREDENTIAL", {"ok": requested.ok})
        print(("OK: " if requested.ok else "FALHA: ") + requested.detail)
        if not requested.ok:
            linha.observacao = requested.detail
            _report_failure(browser, audit, "pedido-link", pause)
            return linha
        link, from_inbox = _get_link(inbox, since, known)
        audit.record("password_link_obtained", "CREDENTIAL", {"ok": bool(link), "from_inbox": from_inbox})
        if not link:
            linha.observacao = "Link de senha nao recebido"
            return linha
        step = set_password(browser, config, link, *_passwords())
        link = ""

        password = step.value
        audit.record("password_set" if step.ok else "password_set_failed", "CREDENTIAL",
                     {"ok": step.ok, "uncertain": step.uncertain})
        print(("OK: " if step.ok else "FALHA: ") + step.detail)
        if step.uncertain:
            # Pode ter sido gravada: vai para a planilha marcada para conferencia, nunca se perde.
            linha.senha, linha.senha_incerta = password, True
            print("A senha tentada vai para a planilha marcada como 'Incerto: conferir'.")
        if not step.ok:
            linha.observacao = step.detail
            _report_failure(browser, audit, "nova-senha", pause)
            return linha
        linha.senha, linha.senha_cadastrada = password, True

        step = test_login(browser, config, lawyer["cpf"], password, _code_provider(secret_2fa))
        audit.record("password_login_tested", "CREDENTIAL", {"ok": step.ok})
        print(("OK: " if step.ok else "FALHA: ") + step.detail)
        linha.login_testado = step.ok
        if not step.ok:
            linha.observacao = step.detail
            _report_failure(browser, audit, "login-senha", pause)
        status = "CREDENTIAL_OK" if step.ok else "CREDENTIAL_FAILED"
        return linha
    finally:
        audit.write_result({"run_id": audit.run_id, "kind": "credencial", "status": status,
                            "reason": linha.observacao or "ok", "details": {"tribunal": config.tribunal}})
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass


def _deliver(linhas: list[LinhaCredencial], output_dir: Path) -> None:
    if not any(l.senha_cadastrada or l.senha_incerta for l in linhas):
        print("\nNenhuma senha foi cadastrada; nada para entregar.")
        return
    destino = output_dir / f"credenciais-{datetime.now():%Y%m%d-%H%M%S}.xlsx"
    print("\nA planilha sai protegida: o Excel pede uma senha para abrir.")
    while True:
        file_password = getpass.getpass("Senha para abrir a planilha (nao aparece na tela): ")
        if file_password and file_password == getpass.getpass("Repita a senha da planilha: "):
            break
        print("As senhas nao conferem, tente de novo.")
    try:
        salvar_planilha(linhas, destino, file_password)
        print(f"OK: planilha gravada em {destino}")
    except Exception as exc:  # noqa: BLE001
        # As senhas ja foram trocadas no PJe: sem a planilha elas se perderiam.
        print(f"FALHA ao gravar a planilha ({type(exc).__name__}: {exc}).")
        print("Instale com: .venv\\Scripts\\python.exe -m pip install openpyxl msoffcrypto-tool")
        print("Anote agora as credenciais abaixo (nao serao mostradas de novo):")
        for l in linhas:
            if l.senha_cadastrada or l.senha_incerta:
                print(f"  {l.tribunal} | {l.url_login} | {l.advogado} | CPF {l.cpf} | senha: {l.senha}")
    finally:
        for l in linhas:
            l.senha = ""


def run_password_wizard(configs, audit_root: Path, output_dir: Path = Path("entregas")) -> int:
    configs = configs if isinstance(configs, list) else [configs]
    print("\n== Credencial CPF + senha + 2FA: " + ", ".join(c.tribunal for c in configs) + " ==\n")
    nome = _ask("Nome do advogado: ")
    cpf = re.sub(r"\D", "", _ask("CPF do advogado: "))
    if len(cpf) != 11 or not nome:
        print("Nome ou CPF invalido")
        return 2
    email_address = _ask("E-mail cadastrado no PJe: ")
    inbox = _setup_inbox(email_address)
    secret_2fa = _ask_2fa_key()
    lawyer = {"nome": nome, "cpf": cpf, "email": email_address, "chave_2fa_exibir": secret_2fa}
    if lawyer["chave_2fa_exibir"] and not _yes("Incluir a chave do 2FA na planilha (o analista precisa dela para logar)?",
                                               default=True):
        lawyer["chave_2fa_exibir"] = ""

    audit_root.mkdir(parents=True, exist_ok=True)
    linhas: list[LinhaCredencial] = []
    try:
        for config in configs:
            linhas.append(_one_tribunal(config, audit_root, lawyer, inbox, secret_2fa))
    except KeyboardInterrupt:
        print("\nInterrompido pelo operador.")
    finally:
        _deliver(linhas, output_dir)
        secret_2fa = lawyer["chave_2fa_exibir"] = ""

    print("\nResumo:")
    for l in linhas:
        print(f"  {l.tribunal}: senha {'cadastrada' if l.senha_cadastrada else 'NAO cadastrada'}, "
              f"login {'OK' if l.login_testado else 'falhou'}" + (f" ({l.observacao})" if l.observacao else ""))
    return 0 if linhas and all(l.login_testado for l in linhas) else 3
