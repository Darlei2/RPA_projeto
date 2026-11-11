"""Etapas de senha contra um SSO simulado (paginas locais no formato do Keycloak do PJe)."""
import dataclasses
from pathlib import Path

import pytest

from pje_rpa.config import load_config
from pje_rpa.credencial import request_password_link, set_password, test_login as login_with_password

PROJECT = Path(__file__).resolve().parents[1]
HOST = "https://pje.teste.jus.br"

PAGES = {
    "/login": """<h1>Login</h1>
      <p id=msg></p>
      <script>if(location.search.includes('sent'))msg.textContent='Voce devera receber um e-mail em breve com mais instrucoes.';
              if(location.search.includes('bad'))msg.innerHTML='<span id=input-error>Usuario ou senha invalidos.</span>';</script>
      <form action="/otp"><input id=username name=username><input id=password name=password type=password>
      <input id=kc-login type=submit value=Entrar></form>
      <input id=kc-pje-office type=button value="CERTIFICADO DIGITAL">
      <a href="/reset-credentials">Esqueceu sua senha?</a>""",
    "/reset-credentials": """<form action="/login"><input type=hidden name=sent value=1>
      <label for=username>CPF</label><input id=username name=username>
      <div id=kc-form-buttons><input type=submit value=Enviar></div></form>""",
    "/tjap-login": """<h1>Login</h1>
      <form action="/otp"><input id=username name=username><input id=password name=password type=password>
      <input id=kc-login type=submit value=Entrar></form>
      <input id=kc-pje-office type=button value="CERTIFICADO DIGITAL">
      <button type=button onclick="setTimeout(()=>{document.getElementById('modal').style.display='block'},800)">Solicitar nova senha</button>
      <div id=modal style="display:none"><h3>Solicitar nova senha</h3>
        <label for=login>CPF/CNPJ</label><input id=login name=login>
        <label for=email>Email</label><input id=email name=email type=email>
        <button type=button onclick="if(login.value&&email.value){msg.textContent='Foi enviado um e-mail com as instrucoes'}">SOLICITAR</button>
        <p id=msg></p></div>""",
    "/ativacao": """<p>Prezado(a) FULANO, para ativar seu cadastro, insira uma nova senha contendo letras, numeros
      e tamanho entre 8 e 64 caracteres.</p>
      <form id=cadastrarNovaSenhaForm onsubmit="event.preventDefault();
        var s=document.getElementById('cadastrarNovaSenhaForm:senha').value;
        var c=document.getElementById('cadastrarNovaSenhaForm:confirmacao').value;
        if(!/^[A-Za-z0-9]{8,64}$/.test(s)||s!==c){document.getElementById('err').innerHTML='<span class=rich-messages>Senha invalida: use apenas letras e numeros</span>';return;}
        document.body.innerHTML='<p>Senha cadastrada com sucesso</p>';">
        <label for="cadastrarNovaSenhaForm:senha">Digite a senha</label>
        <input type=password id="cadastrarNovaSenhaForm:senha" name="cadastrarNovaSenhaForm:senha">
        <label for="cadastrarNovaSenhaForm:confirmacao">Digite-a novamente</label>
        <input type=password id="cadastrarNovaSenhaForm:confirmacao" name="cadastrarNovaSenhaForm:confirmacao">
        <input type=submit id="cadastrarNovaSenhaForm:cadastrarSenhaButton" value=Cadastrar>
        <input type=submit id="cadastrarNovaSenhaForm:redirectPjeButton" value="Ir para PJE" onclick="location.href='/painel';return false;">
        <div id=err></div></form>""",
    "/ativacao-modal": """<p>Prezado(a) FULANO, insira uma nova senha contendo letras, numeros.</p>
      <form id=cadastrarNovaSenhaForm onsubmit="event.preventDefault();
        var s=document.getElementById('cadastrarNovaSenhaForm:senha').value;
        var txt = location.search.includes('velho') ? 'Usuario nao localizado.' :
                  (/^[A-Za-z0-9]+$/.test(s) ? 'Senha cadastrada com sucesso.' : 'Senha invalida.');
        document.getElementById('panelMsgAtivacaoSenhaContainer').style.display='block';
        document.getElementById('msgtxt').textContent=txt;">
        <label for="cadastrarNovaSenhaForm:senha">Digite a senha</label>
        <input type=password id="cadastrarNovaSenhaForm:senha">
        <label for="cadastrarNovaSenhaForm:confirmacao">Digite-a novamente</label>
        <input type=password id="cadastrarNovaSenhaForm:confirmacao">
        <input type=submit id="cadastrarNovaSenhaForm:cadastrarSenhaButton" value=Cadastrar></form>
      <div class="rich-modalpanel" id=panelMsgAtivacaoSenhaContainer style="display:none">
        <div class=rich-mpnl-mask-div style="position:fixed;inset:0;background:#0006"></div>
        <div class=rich-mpnl-body style="position:fixed;top:40%;left:40%;background:#fff"><span id=msgtxt></span>
        <input type=button value=OK onclick="this.closest('.rich-modalpanel').style.display='none'"></div></div>""",
    "/action": """<a href="/update">Clique aqui para prosseguir</a>""",
    "/update": """<form action="/updated"><input id=password-new type=password><input id=password-confirm type=password>
      <div id=kc-form-buttons><input type=submit value=Enviar></div></form>""",
    "/updated": """<p>Sua conta foi atualizada.</p>""",
    "/otp": """<form action="/painel"><input id=otp name=otp><input id=kc-login type=submit value=Entrar></form>""",
    "/painel": """<h1>Painel do advogado</h1>""",
}


@pytest.fixture()
def browser():
    pytest.importorskip("playwright.sync_api")
    from pje_rpa.browser import PlaywrightBrowser

    config = load_config(PROJECT / "tribunais" / "trf6.toml")
    config = dataclasses.replace(
        config, base_url=HOST + "/login",
        browser=dataclasses.replace(config.browser, channel="", headless=True, timeout_ms=5000),
    )
    try:
        b = PlaywrightBrowser(config)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"chromium indisponivel: {exc}")

    def serve(route):
        path = "/" + route.request.url.split("/", 3)[3].split("?")[0]
        route.fulfill(status=200, content_type="text/html; charset=utf-8",
                      body="<html><body>" + PAGES.get(path, "nao encontrado") + "</body></html>")

    b._context.route(HOST + "/**", serve)
    b.config = config
    yield b
    b.close()


def test_pede_link_cadastra_senha_e_testa_login(browser):
    config = browser.config
    step = request_password_link(browser, config, "123.456.789-01", "adv@exemplo.com.br")
    assert step.ok, step.detail

    step = set_password(browser, config, HOST + "/action?key=abc", "S3nha-forte!")
    assert step.ok, step.detail

    codes = []
    step = login_with_password(browser, config, "12345678901", "S3nha-forte!",
                               lambda msg: codes.append(msg) or "123456")
    assert step.ok, step.detail
    assert codes, "o 2FA deveria ter sido pedido"


def test_link_fora_do_jus_br_e_recusado(browser):
    step = set_password(browser, browser.config, "https://phishing.example.com/reset", "x")
    assert not step.ok and ".jus.br" in step.detail


def test_diagnostico_mascara_numeros(browser, tmp_path):
    from pje_rpa.credencial import diagnose

    browser.open(HOST + "/reset-credentials")
    browser.page.evaluate("document.body.insertAdjacentText('beforeend', 'CPF 123.456.789-01')")
    info = diagnose(browser, tmp_path / "x.png")
    assert "123" not in info["texto"] and "***.***.***-**" in info["texto"]
    assert (tmp_path / "x.png").exists()


def test_janela_de_recuperacao_sobre_o_login_tjap(browser):
    """No TJAP o pedido de senha abre sobre a tela de login: nao pode usar #username/Entrar."""
    config = dataclasses.replace(browser.config, base_url=HOST + "/tjap-login")
    step = request_password_link(browser, config, "123.456.789-01", "adv@exemplo.com.br")
    assert step.ok, step.detail
    assert browser.page.input_value("#username") == ""
    assert browser.page.input_value("#login") == "12345678901"


def test_pagina_de_ativacao_do_pje_tjap_com_senha_reserva(browser):
    """TJAP: pagina propria (JSF) que so aceita letras e numeros; a senha com simbolo e recusada."""
    step = set_password(browser, browser.config, HOST + "/ativacao", "Ab3$xY9!kLm2#pQr", "Ab3xY9kLm2pQrT7uV8wZ")
    assert step.ok, step.detail
    assert step.value == "Ab3xY9kLm2pQrT7uV8wZ"


def test_janela_de_mensagem_do_tjap_sucesso(browser):
    step = set_password(browser, browser.config, HOST + "/ativacao-modal", "Ab3xY9kLm2pQrT7uV8wZ", "Ab3$xY9!kLm2#pQr")
    assert step.ok and step.value == "Ab3xY9kLm2pQrT7uV8wZ", step.detail


def test_janela_de_mensagem_do_tjap_usuario_nao_localizado(browser):
    """Link antigo: o TJAP abre a tela, mas responde 'Usuario nao localizado' -> pedir link novo."""
    step = set_password(browser, browser.config, HOST + "/ativacao-modal?velho=1", "Ab3xY9kLm2pQrT7uV8wZ")
    assert not step.ok and step.expired, step.detail


# ------------------------------------------- regra de senha escrita na tela (TRF5 Turmas Recursais)
def test_regra_de_senha_da_tela_gera_senha_que_cumpre():
    from pje_rpa.credencial import _fit_rule, _fits, password_rule
    from pje_rpa.senha_forte import gerar_senha

    texto = ("Prezado(a) FULANA, para ativar seu cadastro, insira uma nova senha contendo letras, "
             "numeros e tamanho entre 8 e 16 caracteres.")
    regra = password_rule(texto)
    assert regra == (8, 16, True)
    assert password_rule("Digite a senha  Digite-a novamente") is None

    class Pagina:
        def inner_text(self, sel, timeout=0):
            return texto

    class Navegador:
        page = Pagina()

    padrao = [gerar_senha(20, especiais=False), gerar_senha(16)]  # 20 e longa; a de 16 tem simbolos
    escolhidas = _fit_rule(Navegador(), padrao)
    assert _fits(escolhidas[0], regra) and escolhidas[0].isalnum() and len(escolhidas[0]) == 16
    assert escolhidas[-2:] == padrao  # as originais continuam como reserva
    assert escolhidas[1].isalnum() and escolhidas[1] == escolhidas[1].lower() and _fits(escolhidas[1], regra)

    curta = password_rule("senha com tamanho entre 6 e 10 caracteres, letras e numeros")
    assert _fits(_fit_rule(type("N", (), {"page": type("P", (), {"inner_text": lambda s, a, timeout=0:
                 "senha com tamanho entre 6 e 10 caracteres, letras e numeros"})()})(), padrao)[0], curta)


def test_erro_do_playwright_nunca_mostra_a_senha():
    from pje_rpa.senha_cli import safe_error
    exc = Exception('Locator.fill: Error: Element is not an <input>\nCall log:\n  - fill("n9wkcz8zWvLhU8GEiYsv")')
    assert "n9wkcz8z" not in safe_error(exc)
    assert "n9wkcz8z" not in safe_error(Exception('x fill("n9wkcz8zWvLhU8GEiYsv") y'))


def test_rotulo_que_acha_o_painel_preenche_o_campo_de_dentro(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    from types import SimpleNamespace

    from pje_rpa.browser import BrowserError, PlaywrightBrowser
    html = """<html><body><div class=rich-mpnl><div id=panelNovaSenhaHeader>Nova senha</div>
      <div role=region id=panelNovaSenhaContentDiv aria-labelledby=panelNovaSenhaHeader>
      <input type=password id=a><input type=password id=b></div></div></body></html>"""
    (tmp_path / "s.html").write_text(html, encoding="utf-8")
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"chromium indisponivel: {exc}")
        page = browser.new_page()
        page.goto((tmp_path / "s.html").as_uri())
        b = PlaywrightBrowser.__new__(PlaywrightBrowser)
        b.page = page
        b.config = SimpleNamespace(selectors={"new_password": ["label=Nova senha"],
                                              "confirm_password": ["css=[id*='NovaSenha' i] input[type='password'] >> nth=1"],
                                              "nada": ["css=#naoexiste"]})
        b.fill("new_password", "Segredo123")
        b.fill("confirm_password", "Segredo123")
        assert page.input_value("#a") == "Segredo123" and page.input_value("#b") == "Segredo123"
        with pytest.raises(BrowserError) as err:
            b.fill("nada", "Segredo123")
        assert "Segredo123" not in str(err.value)
        browser.close()


def test_botao_desconhecido_da_janela_criar_nova_senha(tmp_path):
    """TRT/PDPJ: janela 'Criar nova senha' com botao de rotulo desconhecido e um 'Cancelar'."""
    playwright = pytest.importorskip("playwright.sync_api")
    from pje_rpa.credencial import _click_submit_near_password
    html = """<html><body><div class=rich-mpnl id=panelNovaSenha><h3>Criar nova senha</h3>
      <form onsubmit="return false"><input type=password id=a><input type=password id=b>
      <button type=button onclick="window.cancelou=1">Cancelar</button>
      <button type=button onclick="window.ok=1">Alterar</button></form></div></body></html>"""
    (tmp_path / "s.html").write_text(html, encoding="utf-8")
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"chromium indisponivel: {exc}")
        page = browser.new_page()
        page.goto((tmp_path / "s.html").as_uri())
        assert _click_submit_near_password(type("B", (), {"page": page})())
        assert page.evaluate("window.ok") == 1 and page.evaluate("window.cancelou") is None
        browser.close()


def test_janela_do_proprio_formulario_de_senha_nao_e_mensagem(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    from pje_rpa.credencial import _dialog_text
    html = """<html><body><div class=rich-mpnl-body>Criar nova senha. A senha precisa ser modificada porque
      nao atende aos requisitos minimos de seguranca. <input type=password><input type=password>
      <button>Alterar</button></div><div role=dialog>Senha alterada com sucesso</div></body></html>"""
    (tmp_path / "s.html").write_text(html, encoding="utf-8")
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"chromium indisponivel: {exc}")
        page = browser.new_page()
        page.goto((tmp_path / "s.html").as_uri())
        texto = _dialog_text(type("B", (), {"page": page})())
        assert "sucesso" in texto and "requisitos" not in texto
        browser.close()


# ------------------------------------------- senha pelo login unico (PDPJ/Keycloak), sem e-mail
KC_FORM = """<html><meta charset=utf-8><body><form id=f>
 <input type=text id=username name=username value=x style="display:none">
 <input type=password id=password name=password style="display:none">
 <label for=password-new>Nova senha</label><input type=password id=password-new name=password-new>
 <label for=password-confirm>Confirme a senha</label><input type=password id=password-confirm name=password-confirm>
 <input type=checkbox id=logout-sessions checked>
 <div id=erro class="alert-error" style="display:none"></div>
 <button type=submit name=cancel-aia value=true>Cancelar</button><input type=submit value=Enviar>
</form><script>
document.getElementById('f').addEventListener('submit', e => {
  e.preventDefault();
  if (e.submitter && e.submitter.name === 'cancel-aia') { location = '/painel?kc_action_status=cancelled'; return; }
  const a = document.getElementById('password-new').value, b = document.getElementById('password-confirm').value;
  window.tentativas = (window.tentativas || 0) + 1;
  if (a !== b || a.length > 16 || !/^[A-Za-z0-9]+$/.test(a)) {
    const d = document.getElementById('erro'); d.style.display = '';
    d.innerText = 'Senha invalida: use letras, numeros e tamanho entre 8 e 16 caracteres.';
  } else { location = '/painel?kc_action_status=success'; }
});</script></body></html>"""


def test_senha_pelo_login_unico_sem_email(browser):
    from urllib.parse import quote

    from pje_rpa.credencial import set_password_via_sso
    from pje_rpa.senha_forte import gerar_senha

    def keycloak(route):
        corpo = KC_FORM if "kc_action=UPDATE_PASSWORD" in route.request.url else "<html><body>login do SSO</body></html>"
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=corpo)

    browser._context.route(HOST + "/auth/**", keycloak)
    # sem ter passado pelo SSO nesta sessao: nao tenta nada
    assert not set_password_via_sso(browser, browser.config, "x" * 12).ok
    browser.open(HOST + "/auth/realms/pje/protocol/openid-connect/auth?client_id=pje-teste-1g&state=abc&nonce=n"
                 "&redirect_uri=" + quote(HOST + "/painel", safe=""))
    assert browser.sso_auth_url.endswith("|pje-teste-1g|" + HOST + "/painel") and "abc" not in browser.sso_auth_url

    primeira, reserva = gerar_senha(20, especiais=False), gerar_senha(16)  # 20 e longa; a reserva tem simbolos
    step = set_password_via_sso(browser, browser.config, primeira, reserva)
    assert step.ok, step.detail
    assert step.value not in (primeira, reserva) and step.value.isalnum() and 8 <= len(step.value) <= 16
    assert step.tried[:2] == (primeira, reserva) and step.tried[-1] == step.value
    assert browser.kc_action_status == "success"
    assert step.value not in step.detail
