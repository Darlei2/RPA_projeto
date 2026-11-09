"""Etapas que transformam o cadastro em credencial utilizavel: CPF + senha + 2FA.

Sem estas etapas o PJe so aceita login por certificado (ou trata o usuario como
jus postulandi). Depois do cadastro concluido:

1. sair do PJe (descarta a sessao) e abrir a pagina inicial;
2. "Esqueceu a senha?" -> informar CPF (e e-mail, se o tribunal pedir) -> enviar;
3. o PJe manda ao e-mail cadastrado um link de criacao de senha;
4. abrir o link e cadastrar a senha;
5. testar o login com CPF + senha + codigo do 2FA.

As etapas 1-2 rodam sozinhas ao fim de cada cadastro. As etapas 3-5 dependem do
e-mail do advogado e rodam pelo comando `senha` (scripts\\criar-senha.ps1).
Nenhuma senha, link ou chave e gravada em disco ou na auditoria.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

# Candidatos genericos. O SSO do PJe (Keycloak, sso.cloud.pje.jus.br) e o mesmo em
# quase todos os tribunais; os demais cobrem o login antigo (login.seam).
DEFAULT_SELECTORS: dict[str, list[str]] = {
    "forgot_password_link": [
        "css=a[href*='reset-credentials']",
        "text=Esqueceu a senha",
        "text=Esqueceu sua senha",
        "text=Esqueci minha senha",
        "text=Esqueci a senha",
        "text=Solicitar nova senha",
        "text=Recuperar senha",
    ],
    "reset_cpf": [
        "css=#username",
        "css=input[name='username']",
        "label=CPF",
        "css=input[name*='cpf' i]",
        "css=input[id*='cpf' i]",
    ],
    "reset_email": [
        "css=input[type='email']",
        "label=E-mail",
        "css=input[name*='email' i]",
    ],
    "reset_submit": [
        "css=#kc-form-buttons input[type='submit']",
        "css=#kc-form-buttons button",
        "role=button|name=Enviar",
        "role=button|name=Solicitar",
        "role=button|name=Confirmar",
        "css=input[type='submit']",
    ],
    "reset_confirmation": [
        "text=receber um e-mail",
        "text=receber um email",
        "text=mais instru",
        "text=enviado para o seu e-mail",
        "text=enviado para o e-mail",
        "text=verifique seu e-mail",
        "text=verifique o seu e-mail",
        "text=foi enviad",
        "text=e-mail enviado",
        "text=email enviado",
        "text=enviamos",
        "text=link para redefin",
        "text=link de redefin",
    ],
    "password_link_proceed": [
        "text=Clique aqui para prosseguir",
        "text=clique aqui para continuar",
        "text=Click here to proceed",
    ],
    "new_password": ["css=#password-new", "css=input[name='password-new']", "label=Nova senha"],
    "confirm_password": [
        "css=#password-confirm",
        "css=input[name='password-confirm']",
        "label=Confirme a senha",
        "label=Confirmar senha",
    ],
    "password_submit": [
        "css=#kc-form-buttons input[type='submit']",
        "css=#kc-form-buttons button",
        "role=button|name=Enviar",
        "role=button|name=Salvar",
        "role=button|name=Confirmar",
    ],
    "password_done": [
        "text=conta foi atualizada",
        "text=senha foi alterada",
        "text=senha foi atualizada",
        "text=senha cadastrada",
        "text=Your account has been updated",
    ],
    "login_username": ["css=#username", "css=input[name='username']", "label=CPF"],
    "login_password": ["css=#password", "css=input[name='password']", "label=Senha"],
    "login_submit": ["css=#kc-login", "role=button|name=Entrar", "css=input[type='submit']"],
}


def ensure_selectors(config) -> None:
    """Completa a configuracao (inclusive .toml antigos) com os candidatos genericos."""
    for group, specs in DEFAULT_SELECTORS.items():
        config.selectors.setdefault(group, list(specs))


@dataclass
class StepResult:
    ok: bool
    detail: str


def _messages(browser) -> list[str]:
    if not hasattr(browser, "page"):
        return []
    from .adaptive_form import page_messages

    return page_messages(browser.page)


def _fail(browser, default: str) -> StepResult:
    messages = _messages(browser)
    return StepResult(False, default + (": " + " | ".join(messages) if messages else ""))


def open_login(browser, config) -> bool:
    """Abre a pagina inicial do tribunal e chega na tela de login do SSO."""
    browser.open(config.base_url)
    if "login_sso_button" in config.selectors and browser.visible("login_sso_button", 5000):
        browser.click("login_sso_button")
    return browser.visible("login_certificate_button", 15000) or browser.visible("forgot_password_link", 3000)


def request_password_link(browser, config, cpf: str, email: str) -> StepResult:
    """Etapas 1-2: sai do PJe e pede o link de criacao de senha para o e-mail cadastrado."""
    ensure_selectors(config)
    browser.reset_session()
    if not open_login(browser, config):
        return _fail(browser, "Tela de login do tribunal nao abriu")
    if not browser.visible("forgot_password_link", 8000):
        return _fail(browser, "Link 'Esqueceu a senha?' nao localizado")
    browser.click("forgot_password_link")
    if not browser.visible("reset_cpf", 10000):
        return _fail(browser, "Campo de CPF da recuperacao de senha nao localizado")
    browser.fill("reset_cpf", "".join(ch for ch in cpf if ch.isdigit()))
    if email and browser.visible("reset_email", 1500):
        browser.fill("reset_email", email.strip())
    browser.click("reset_submit")
    if browser.visible("reset_confirmation", 15000):
        return StepResult(True, "Link de criacao de senha enviado ao e-mail cadastrado")
    return _fail(browser, "O PJe nao confirmou o envio do link de senha")


def set_password(browser, config, link: str, password: str) -> StepResult:
    """Etapa 4: abre o link recebido por e-mail e cadastra a senha."""
    ensure_selectors(config)
    host = (urlparse(link).hostname or "").lower()
    if urlparse(link).scheme != "https" or not host.endswith(".jus.br"):
        return StepResult(False, "O link precisa ser https e de um dominio .jus.br")
    browser.reset_session()
    browser.open(link.strip())
    if not browser.visible("new_password", 5000) and browser.visible("password_link_proceed", 5000):
        browser.click("password_link_proceed")
    if not browser.visible("new_password", 15000):
        return _fail(browser, "Tela de nova senha nao abriu (link expirado ou ja usado?)")
    browser.fill("new_password", password)
    if browser.visible("confirm_password", 1500):
        browser.fill("confirm_password", password)
    browser.click("password_submit")
    if browser.visible("password_done", 10000):
        return StepResult(True, "Senha cadastrada")
    if browser.wait_hidden("new_password", 10000) and not _messages(browser):
        return StepResult(True, "Senha cadastrada (a tela de nova senha foi concluida)")
    return _fail(browser, "O PJe nao aceitou a nova senha")


def test_login(browser, config, cpf: str, password: str,
               code_provider: Callable[[str], str | None]) -> StepResult:
    """Etapa 5: login com CPF + senha + 2FA, sem certificado."""
    ensure_selectors(config)
    browser.reset_session()
    if not open_login(browser, config) or not browser.visible("login_username", 8000):
        return _fail(browser, "Tela de login com CPF e senha nao localizada")
    browser.fill("login_username", "".join(ch for ch in cpf if ch.isdigit()))
    browser.fill("login_password", password)
    browser.click("login_submit")
    if browser.visible("second_factor_input", 10000):
        code = code_provider("Codigo do 2FA para testar o login com senha")
        if not code:
            return StepResult(False, "Codigo do 2FA nao informado")
        browser.fill("second_factor_input", code.strip())
        browser.click("second_factor_submit")
        if not browser.wait_hidden("second_factor_input", 20000):
            return _fail(browser, "Codigo do 2FA recusado")
    else:
        messages = _messages(browser)
        if messages or browser.visible("login_password", 1500):
            return StepResult(False, "Login com senha recusado" + (": " + " | ".join(messages) if messages else ""))
        return StepResult(False, "O PJe nao pediu o 2FA: a credencial ainda nao e usuario + senha + 2FA")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if browser.visible("lawyer_panel", 1000):
            return StepResult(True, "Login com CPF + senha + 2FA funcionou (painel do advogado)")
        if not browser.visible("login_password", 200) and "sso.cloud.pje.jus.br" not in _url(browser):
            return StepResult(True, "Login com CPF + senha + 2FA funcionou")
    return _fail(browser, "Login com senha nao saiu da tela do SSO")


def _url(browser) -> str:
    return getattr(getattr(browser, "page", None), "url", "") or ""


def diagnose(browser, shot_path=None) -> dict:
    """Retrato da tela atual para entender uma falha: titulos, rotulos, botoes,
    campos (sem valores) e um trecho do texto com os numeros mascarados."""
    import re

    info: dict = {"url": _url(browser)}
    try:
        info.update(browser.outline())
        text = browser.page.locator("body").inner_text(timeout=2000)
        info["texto"] = re.sub(r"\d", "*", " ".join(text.split()))[:700]
    except Exception as exc:  # noqa: BLE001
        info["erro_diagnostico"] = type(exc).__name__
    if shot_path is not None:
        try:
            browser.page.screenshot(path=str(shot_path), full_page=True)
            info["print"] = str(shot_path)
        except Exception:  # noqa: BLE001
            pass
    return info

