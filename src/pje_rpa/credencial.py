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

import re

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
    # O SSO do PJe abre "Solicitar nova senha" como janela sobre a tela de login, onde o
    # formulario de login (#username, "Entrar") continua visivel: os campos e o botao da
    # recuperacao vem primeiro para o robo nao preencher/clicar no login por engano.
    # Campo exclusivo da janela "Solicitar nova senha" do SSO (TJAP e outros): enquanto a janela
    # ainda esta abrindo, o #username do login continua visivel atras dela e casaria primeiro.
    "reset_cpf_dialog": ["css=input#login", "label=CPF/CNPJ"],
    "reset_email_dialog": ["css=input#email"],
    "reset_cpf": [
        "css=input#login",
        "label=CPF/CNPJ",
        "css=input[name*='cpf' i]",
        "css=input[id*='cpf' i]",
        "css=#username",
        "css=input[name='username']",
        "label=CPF",
    ],
    "reset_email": [
        "css=input#email",
        "css=input[type='email']",
        "label=Email",
        "label=E-mail",
        "css=input[name*='email' i]",
    ],
    "reset_submit": [
        # Texto exato: o botao que abre a janela ("Solicitar nova senha") nao pode casar.
        "css=button:text-is('SOLICITAR'), button:text-is('Solicitar')",
        "css=input[type='submit'][value='SOLICITAR' i], input[type='button'][value='SOLICITAR' i]",
        "role=button|name=Enviar",
        "role=button|name=Confirmar",
        "css=#kc-form-buttons input[type='submit']",
        "css=#kc-form-buttons button",
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
    # SSO (Keycloak) e pagina propria do PJe (ex.: TJAP /Senha/ativacaoSenha.seam, JSF).
    "new_password": [
        "css=#password-new", "css=input[name='password-new']",
        "css=input[type='password'][id$=':senha']", "label=Digite a senha", "label=Nova senha",
    ],
    "confirm_password": [
        "css=#password-confirm",
        "css=input[name='password-confirm']",
        "css=input[type='password'][id$=':confirmacao']",
        "label=Digite-a novamente",
        "label=Confirme a senha",
        "label=Confirmar senha",
    ],
    "password_submit": [
        "css=input[type='submit'][id$=':cadastrarSenhaButton']",
        "css=#kc-form-buttons input[type='submit']",
        "css=#kc-form-buttons button",
        "role=button|name=Enviar",
        "role=button|name=Salvar",
        "role=button|name=Confirmar",
        "role=button|name=Cadastrar",
    ],
    "password_done": [
        "text=conta foi atualizada",
        "text=senha foi alterada",
        "text=senha foi atualizada",
        "text=senha cadastrada",
        "text=senha foi cadastrada",
        "text=cadastrada com sucesso",
        "text=ativado com sucesso",
        "text=ativada com sucesso",
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
    value: str = ""  # ex.: a senha efetivamente aceita (nunca vai para log/auditoria)
    expired: bool = False  # link vencido/usado: pedir outro
    uncertain: bool = False  # o PJe pode ter gravado a senha em value


_REJECTED = re.compile(r"erro|inv[a\u00e1]lid|n[a\u00e3]o (confere|atende|permit|aceit)|deve|obrigat|m[i\u00ed]nim|m[a\u00e1]xim|caracter", re.I)


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
    # TRTs: "Esqueci minha senha" fica na pagina do PJe, ao lado de "Entrar com PDPJ"; depois de
    # entrar no PDPJ ele nao existe mais. Entao procura ali ANTES de clicar no botao do PDPJ.
    browser.open(config.base_url)
    if not browser.visible("forgot_password_link", 5000):
        if not open_login(browser, config):
            return _fail(browser, "Tela de login do tribunal nao abriu")
        if not browser.visible("forgot_password_link", 8000):
            return _fail(browser, "Link 'Esqueceu a senha?' nao localizado")
    browser.click("forgot_password_link")
    # Primeiro espera o campo da janela de recuperacao; so depois aceita os genericos
    # (pagina propria de recuperacao, onde o login nao esta na tela).
    if browser.visible("reset_cpf_dialog", 8000):
        cpf_group, email_group = "reset_cpf_dialog", "reset_email_dialog"
    elif browser.visible("reset_cpf", 5000):
        cpf_group, email_group = "reset_cpf", "reset_email"
    else:
        return _fail(browser, "Campo de CPF da recuperacao de senha nao localizado")
    cpf_digits = "".join(ch for ch in cpf if ch.isdigit())
    browser.fill(cpf_group, cpf_digits)
    if email and browser.visible(email_group, 1500):
        browser.fill(email_group, email.strip())
    # Garante que os dados foram para os campos certos antes de enviar (sem mostrar os valores).
    try:
        if "".join(ch for ch in browser.read(cpf_group) if ch.isdigit()) != cpf_digits:
            return _fail(browser, "O CPF nao ficou no campo da recuperacao de senha")
        if email and browser.visible(email_group, 500) and browser.read(email_group).strip().lower() != email.strip().lower():
            return _fail(browser, "O e-mail nao ficou no campo da recuperacao de senha")
    except Exception:  # noqa: BLE001
        pass
    browser.click("reset_submit")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        said = _dialog_text(browser) or " | ".join(_messages(browser))
        if browser.visible("reset_confirmation", 500):
            return StepResult(True, "Link de criacao de senha enviado ao e-mail cadastrado"
                              + (f" (PJe: {said})" if said else ""))
        if said and (_REJECTED.search(said) or _NEGATION.search(said)):
            return StepResult(False, f"O PJe recusou o pedido de nova senha (PJe: {said})")
    return _fail(browser, "O PJe nao confirmou o envio do link de senha")


def set_password(browser, config, link: str, password: str, fallback_password: str | None = None) -> StepResult:
    """Etapa 4: abre o link recebido por e-mail e cadastra a senha.

    Se a pagina recusar a senha (ex.: o TJAP aceita so letras e numeros), tenta de novo na mesma
    tela com fallback_password. StepResult.value traz a senha que ficou cadastrada.
    """
    ensure_selectors(config)
    host = (urlparse(link).hostname or "").lower()
    if urlparse(link).scheme != "https" or not host.endswith(".jus.br"):
        return StepResult(False, "O link precisa ser https e de um dominio .jus.br")
    browser.reset_session()
    browser.open(link.strip())
    if not browser.visible("new_password", 5000) and browser.visible("password_link_proceed", 5000):
        browser.click("password_link_proceed")
    if not browser.visible("new_password", 15000):
        failed = _fail(browser, "Tela de nova senha nao abriu (link expirado ou ja usado?)")
        failed.expired = True
        return failed
    last = StepResult(False, "O PJe nao aceitou a nova senha")
    candidates = _fit_rule(browser, [p for p in (password, fallback_password) if p])
    for candidate in candidates:
        last = _submit_password(browser, candidate)
        if last.ok or last.uncertain or last.expired or not browser.visible("new_password", 1000):
            return last
        _close_dialog(browser)
    return last


def password_rule(text: str) -> tuple[int, int, bool] | None:
    """Regra escrita na tela de nova senha. Ex. (TRF5 Turmas Recursais): "insira uma nova senha
    contendo letras, numeros e tamanho entre 8 e 16 caracteres" -> (8, 16, True=so letras e numeros)."""
    t = " ".join((text or "").split())
    m = re.search(r"entre\s*(\d{1,2})\s*e\s*(\d{1,2})\s*caracteres", t, re.I)
    lo = hi = None
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
    else:
        mn = re.search(r"m[i\u00ed]nimo\s*(?:de)?\s*(\d{1,2})\s*caracteres", t, re.I)
        mx = re.search(r"m[a\u00e1]ximo\s*(?:de)?\s*(\d{1,2})\s*caracteres", t, re.I)
        lo, hi = (int(mn.group(1)) if mn else None), (int(mx.group(1)) if mx else None)
    alnum = bool(re.search(r"letras\s*(?:,|e)\s*n[u\u00fa]meros", t, re.I)) and not re.search(
        r"especia|s[i\u00ed]mbolo|caractere especial", t, re.I)
    if lo is None and hi is None and not alnum:
        return None
    return (lo or 1, hi or 64, alnum)


def _fits(password: str, rule: tuple[int, int, bool]) -> bool:
    lo, hi, alnum = rule
    return lo <= len(password) <= hi and (password.isalnum() or not alnum)


def _fit_rule(browser, candidates: list[str]) -> list[str]:
    """Se a tela diz o tamanho/tipo de senha aceito, garante uma senha que cumpre a regra."""
    page = getattr(browser, "page", None)
    if page is None:
        return candidates
    try:
        rule = password_rule(page.inner_text("body", timeout=3000))
    except Exception:  # noqa: BLE001
        return candidates
    if rule is None:
        return candidates
    ok = [p for p in candidates if _fits(p, rule)]
    if ok:
        return ok + [p for p in candidates if p not in ok]
    from .senha_forte import gerar_senha

    lo, hi, alnum = rule
    size = max(lo, min(hi, 16))
    return [gerar_senha(size, especiais=not alnum, permitir_curta=True)] + candidates


# Janelas de mensagem do PJe (RichFaces/JSF, Bootstrap) que aparecem por cima do formulario.
JS_DIALOG_TEXT = r"""() => {
  const vis = e => !!(e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden'
                 && getComputedStyle(e).display !== 'none';
  const sel = '.rich-mpnl-body,.rich-mpnl-content,.rich-modalpanel,[role=dialog],.modal.show,.ui-dialog-content';
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    if (!vis(el)) continue;
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
    if (t && !out.some(o => o.includes(t) || t.includes(o))) out.push(t.slice(0, 300));
  }
  return out.slice(0, 3);
}"""

_SUCCESS = re.compile(r"sucesso|ativad[ao]|cadastrad[ao]|alterad[ao]|atualizad[ao]", re.I)
_EXPIRED = re.compile(r"expirad|venceu|vencid|j[a\u00e1] (foi )?(utilizad|usad)|link inv[a\u00e1]lid|"
                      r"token inv[a\u00e1]lid|n[a\u00e3]o (foi )?(encontrad|localizad)|inexistente|"
                      r"n[a\u00e3]o foi poss[i\u00ed]vel encontrar o usu[a\u00e1]rio", re.I)
_NEGATION = re.compile(r"\bn[a\u00e3]o\b|erro|falha", re.I)


def _dialog_text(browser) -> str:
    page = getattr(browser, "page", None)
    if page is None:
        return ""
    try:
        return " | ".join(page.evaluate(JS_DIALOG_TEXT))
    except Exception:  # noqa: BLE001
        return ""


def _close_dialog(browser) -> None:
    page = getattr(browser, "page", None)
    if page is None:
        return
    for sel in (".rich-modalpanel :is(input,button,a):visible",
                "[role=dialog] :is(button,input,a):visible", ".modal.show :is(button,a):visible"):
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 6)):
                item = loc.nth(i)
                label = (item.inner_text(timeout=500) or item.get_attribute("value") or "").strip()
                if re.fullmatch(r"(?i)ok|fechar|voltar|continuar|x|\u00d7|close", label):
                    item.click(timeout=3000)
                    page.wait_for_timeout(500)
                    return
        except Exception:  # noqa: BLE001
            continue
    try:
        page.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        pass


def _submit_password(browser, password: str) -> StepResult:
    browser.fill("new_password", password)
    if browser.visible("confirm_password", 1500):
        browser.fill("confirm_password", password)
    try:
        browser.click("password_submit")
    except Exception as exc:  # noqa: BLE001
        dialog = _dialog_text(browser)
        return StepResult(False, "Nao foi possivel clicar em cadastrar"
                          + (f" (janela do PJe: {dialog})" if dialog else f" ({type(exc).__name__})"))
    deadline = time.monotonic() + 15
    hidden_since = None
    while time.monotonic() < deadline:
        dialog = _dialog_text(browser)
        if dialog:
            # Le a janela de mensagem do PJe e decide pelo texto.
            if _EXPIRED.search(dialog):
                return StepResult(False, f"Link expirado ou ja usado (PJe: {dialog})", expired=True)
            if _SUCCESS.search(dialog):
                if not _NEGATION.search(dialog):
                    return StepResult(True, f"Senha cadastrada (PJe: {dialog})", password)
                return StepResult(False, f"Resultado incerto (PJe: {dialog})", password, uncertain=True)
            if _REJECTED.search(dialog) or _NEGATION.search(dialog):
                return StepResult(False, f"O PJe recusou a senha (PJe: {dialog})")
        if browser.visible("password_done", 500):
            return StepResult(True, "Senha cadastrada", password)
        if not browser.visible("new_password", 300):
            # A pagina JSF recarrega apos o clique: so conta se o formulario sumir de vez.
            hidden_since = hidden_since or time.monotonic()
            if time.monotonic() - hidden_since >= 3 and not any(_REJECTED.search(m) for m in _messages(browser)):
                return StepResult(True, "Senha cadastrada (a tela de nova senha foi concluida)", password)
            continue
        hidden_since = None
        messages = _messages(browser)
        if any(_EXPIRED.search(m) for m in messages):
            return StepResult(False, "Link expirado ou ja usado: " + " | ".join(messages), expired=True)
        if any(_REJECTED.search(m) for m in messages):
            return StepResult(False, "O PJe recusou a senha: " + " | ".join(messages))
    # Nao da para saber se o PJe gravou: a senha vai para a planilha marcada como incerta.
    dialog = _dialog_text(browser)
    return StepResult(False, "Resultado incerto: o PJe nao confirmou nem recusou a senha"
                      + (f" (janela: {dialog})" if dialog else ""), password, uncertain=True)


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

