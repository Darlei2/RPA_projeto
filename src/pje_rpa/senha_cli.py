"""Assistente de console para as etapas de senha (scripts\\criar-senha.ps1).

Pergunta CPF, e-mail, link recebido, senha e chave do 2FA na hora: nada vai para a
linha de comando, para o historico do PowerShell, para o disco ou para a auditoria.
"""
from __future__ import annotations

import getpass
import re
import time

from .audit import AuditLog
from .credencial import diagnose, request_password_link, set_password, test_login
from .totp import gerar_totp, normalizar_chave


def _ask(prompt: str) -> str:
    return input(prompt).strip()


def _yes(prompt: str) -> bool:
    return _ask(prompt + " [s/N]: ").casefold() in {"s", "sim", "y"}


def _code_provider(secret: str):
    def provide(message: str) -> str | None:
        if not secret:
            return _ask(f"{message}: ") or None
        if 30 - (time.time() % 30) < 8:  # evita codigo prestes a expirar
            time.sleep(30 - (time.time() % 30) + 0.3)
        print("Codigo TOTP gerado localmente")
        return gerar_totp(secret)
    return provide


def _report_failure(browser, audit, step_name: str) -> None:
    """Mostra o que estava na tela, salva um print local e deixa o Edge aberto para conferir."""
    import json

    info = diagnose(browser, audit.run_dir / f"falha-{step_name}.png")
    print("\n--- Tela no momento da falha (numeros mascarados) ---")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print("Print salvo em:", info.get("print", "(nao foi possivel salvar)"))
    _ask("\nO Edge continua aberto para voce conferir. Pressione Enter para fechar... ")


def run_password_wizard(config, audit_root) -> int:
    from .browser import PlaywrightBrowser

    print(f"\n== Credencial CPF + senha + 2FA: {config.tribunal} ==\n")
    cpf = re.sub(r"\D", "", _ask("CPF do advogado: "))
    if len(cpf) != 11:
        print("CPF invalido")
        return 2

    audit_root.mkdir(parents=True, exist_ok=True)
    audit = AuditLog(audit_root)
    audit.record("credential_wizard_started", "CREDENTIAL", {"tribunal": config.tribunal})
    browser = PlaywrightBrowser(config)
    status, reason = "CREDENTIAL_FAILED", "Interrompido"
    try:
        if _yes("Pedir agora o link de criacao de senha ('Esqueceu a senha?')?"):
            email = _ask("E-mail cadastrado no PJe: ")
            step = request_password_link(browser, config, cpf, email)
            audit.record("password_link_requested" if step.ok else "password_link_request_failed",
                         "CREDENTIAL", {"ok": step.ok})
            print(("OK: " if step.ok else "FALHA: ") + step.detail)
            if not step.ok:
                reason = step.detail
                _report_failure(browser, audit, "pedido-link")
                return 3

        link = _ask("\nCole o link recebido no e-mail (Enter vazio = parar aqui): ")
        if not link:
            reason = "Link de senha ainda nao recebido"
            print("Quando o e-mail chegar, rode este script de novo e responda N ao pedido de link.")
            return 3

        while True:
            password = getpass.getpass("Nova senha (nao aparece na tela): ")
            if password and password == getpass.getpass("Repita a senha: "):
                break
            print("As senhas nao conferem, tente de novo.")

        step = set_password(browser, config, link, password)
        audit.record("password_set" if step.ok else "password_set_failed", "CREDENTIAL", {"ok": step.ok})
        print(("OK: " if step.ok else "FALHA: ") + step.detail)
        if not step.ok:
            reason = step.detail
            _report_failure(browser, audit, "nova-senha")
            return 3

        secret = normalizar_chave(getpass.getpass(
            "Chave do 2FA em Base32 (Enter vazio = digitar o codigo na hora): ") or None)
        step = test_login(browser, config, cpf, password, _code_provider(secret))
        password = secret = ""
        audit.record("password_login_tested", "CREDENTIAL", {"ok": step.ok})
        print(("OK: " if step.ok else "FALHA: ") + step.detail)
        if not step.ok:
            _report_failure(browser, audit, "login-senha")
        status, reason = ("CREDENTIAL_OK" if step.ok else "CREDENTIAL_FAILED"), step.detail
        return 0 if step.ok else 3
    finally:
        audit.write_result({"run_id": audit.run_id, "kind": "credencial", "status": status,
                            "reason": reason, "details": {"tribunal": config.tribunal}})
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass
