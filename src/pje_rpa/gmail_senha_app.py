"""Senha de app do Gmail gerada pelo robo, UMA vez por conta, para ler o link do PJe por IMAP.

Com o e-mail, a senha do e-mail e a chave do 2FA do Gmail (32 caracteres, Base32) da planilha,
o robo entra em myaccount.google.com/apppasswords pelo Edge, cria a senha de app e devolve as
16 letras. Ela e guardada no cofre local (cofre_local.py) e daqui em diante a caixa e lida por
IMAP, sem novo login no Gmail.

Regras:
- nada de contornar CAPTCHA, verificacao por celular ou "navegador nao seguro": nesses casos o
  robo pausa e o operador termina no navegador (ou digita a senha de app que gerou);
- senha, chave e senha de app nunca aparecem em mensagens, logs ou auditoria.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import datetime

from .totp import gerar_totp

APP_PASSWORDS_URL = "https://myaccount.google.com/apppasswords"
GMAIL_DOMAINS = ("gmail.com", "googlemail.com")

# 4 grupos de 4 letras minusculas (o texto seguinte pode vir colado, ex.: "mnopConcluido")
_APP_PASSWORD = re.compile(r"(?<![a-z])([a-z]{4})\s([a-z]{4})\s([a-z]{4})\s([a-z]{4})(?![a-z])")
_BLOQUEIO = re.compile(
    r"captcha|n[aã]o (e|é) seguro|may not be secure|navegador ou app|"
    r"confirme (que|seu) (e|é) voc[eê]|verify it.s you|n[uú]mero de telefone|phone number|"
    r"digite os caracteres|type the text|conta foi desativada|account disabled", re.I)
_OUTRO_METODO = re.compile(r"tente outro m[eé]todo|tentar de outra forma|try another way|mais op[cç][oõ]es", re.I)
_AUTHENTICATOR = re.compile(r"authenticator|autenticador|c[oó]digo de verifica[cç][aã]o do app", re.I)

Assist = Callable[[str], str]  # mostra o motivo; devolve "" (ja resolvi) ou a senha de app digitada


class GmailError(Exception):
    pass


def e_gmail(email: str) -> bool:
    return email.rsplit("@", 1)[-1].strip().lower() in GMAIL_DOMAINS


def extrair_senha_de_app(texto: str) -> str:
    """'abcd efgh ijkl mnop' (como o Google mostra) -> 'abcdefghijklmnop'."""
    m = _APP_PASSWORD.search(texto or "")
    return "".join(m.groups()) if m else ""


def _visivel(page, seletor: str):
    try:
        loc = page.locator(seletor)
        for i in range(min(loc.count(), 5)):
            if loc.nth(i).is_visible():
                return loc.nth(i)
    except Exception:  # noqa: BLE001
        return None
    return None


def _texto(page) -> str:
    try:
        return page.inner_text("body", timeout=3000)
    except Exception:  # noqa: BLE001
        return ""


def _campo_nome_do_app(page):
    for loc in (page.get_by_label(re.compile(r"nome do app|app name", re.I)),
                page.get_by_placeholder(re.compile(r"nome do app|app name", re.I))):
        try:
            for i in range(min(loc.count(), 3)):
                if loc.nth(i).is_visible():
                    return loc.nth(i)
        except Exception:  # noqa: BLE001
            continue
    return None


def _clicar_texto(page, padrao: re.Pattern) -> bool:
    for loc in (page.get_by_role("button", name=padrao), page.get_by_role("link", name=padrao),
                page.get_by_text(padrao)):
        try:
            for i in range(min(loc.count(), 5)):
                if loc.nth(i).is_visible():
                    loc.nth(i).click(timeout=4000)
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


def entrar_na_pagina_de_senhas_de_app(page, email: str, senha: str, chave_2fa: str,
                                      assist: Assist | None, prazo_s: int = 120) -> str:
    """Faz o login do Google (e-mail, senha, 2FA) ate a tela de senhas de app.

    Devolve "" quando chegou na tela, ou a senha de app que o operador digitou no modo assistido.
    """
    page.goto(APP_PASSWORDS_URL)
    fim = time.monotonic() + prazo_s
    ultimo_totp = ""
    while True:
        if _campo_nome_do_app(page) is not None:
            return ""
        texto = _texto(page)
        if (campo := _visivel(page, "input[type=email], #identifierId")) is not None and not campo.input_value():
            campo.fill(email)
            campo.press("Enter")
        elif (campo := _visivel(page, "input[type=password][name=Passwd], input[type=password]")) is not None:
            campo.fill(senha)
            campo.press("Enter")
        elif (campo := _visivel(page, "input[name=totpPin], #totpPin, input[autocomplete=one-time-code]")) is not None:
            codigo = gerar_totp(chave_2fa)
            if codigo == ultimo_totp:  # o mesmo codigo foi recusado: espera o proximo
                time.sleep(5)
                continue
            ultimo_totp = codigo
            campo.fill(codigo)
            campo.press("Enter")
        elif _BLOQUEIO.search(texto) or time.monotonic() > fim:
            motivo = ("o Google pediu uma verificacao que o robo nao faz (captcha, celular ou navegador)"
                      if _BLOQUEIO.search(texto) else "o login do Google nao chegou na tela de senhas de app")
            if assist is None:
                raise GmailError(motivo)
            resposta = assist(motivo).strip()
            if resposta:
                return resposta
            fim = time.monotonic() + prazo_s
            page.goto(APP_PASSWORDS_URL)
            continue
        elif _OUTRO_METODO.search(texto) and not _AUTHENTICATOR.search(texto):
            _clicar_texto(page, _OUTRO_METODO)
        elif _AUTHENTICATOR.search(texto) and _visivel(page, "input[name=totpPin], #totpPin") is None:
            _clicar_texto(page, _AUTHENTICATOR)
        page.wait_for_timeout(1500)


def criar_senha_de_app(page, nome_app: str) -> str:
    campo = _campo_nome_do_app(page)
    if campo is None:
        raise GmailError("Campo 'Nome do app' nao encontrado")
    campo.fill(nome_app)
    if not _clicar_texto(page, re.compile(r"^(criar|create)$", re.I)):
        campo.press("Enter")
    fim = time.monotonic() + 20
    while time.monotonic() < fim:
        senha_app = extrair_senha_de_app(_texto(page))
        if senha_app:
            _clicar_texto(page, re.compile(r"^(conclu[ií]do|done|ok)$", re.I))
            return senha_app
        page.wait_for_timeout(700)
    raise GmailError("O Google nao mostrou a senha de app")


def gerar_senha_de_app(email: str, senha: str, chave_2fa: str, *, assist: Assist | None = None,
                       navegador: str = "msedge", playwright_factory=None) -> str:
    """Entra no Gmail e cria uma senha de app. Devolve as 16 letras (sem espacos)."""
    if not (email and senha and chave_2fa):
        raise GmailError("Faltam e-mail, senha do e-mail ou chave 2FA do e-mail na planilha")
    if playwright_factory is None:
        from playwright.sync_api import sync_playwright
        playwright_factory = sync_playwright
    pw = playwright_factory().start()
    browser = None
    try:
        browser = pw.chromium.launch(channel=navegador, headless=False)
        page = browser.new_context(locale="pt-BR").new_page()
        digitada = entrar_na_pagina_de_senhas_de_app(page, email, senha, chave_2fa, assist)
        if digitada:
            senha_app = re.sub(r"\s", "", digitada).lower()
        else:
            senha_app = criar_senha_de_app(page, f"PJe RPA {datetime.now():%d-%m-%Y}")
        if not re.fullmatch(r"[a-z]{16}", senha_app):
            raise GmailError("A senha de app nao tem 16 letras")
        return senha_app
    except GmailError:
        raise
    except Exception as exc:  # noqa: BLE001 - mensagem do Playwright pode conter o que foi digitado
        raise GmailError(f"Falha no navegador ao gerar a senha de app ({type(exc).__name__})") from None
    finally:
        senha = chave_2fa = ""
        try:
            if browser is not None:
                browser.close()
        finally:
            pw.stop()
