"""Senha de app do Gmail gerada pelo robo (uma vez) a partir de e-mail + senha + chave 2FA."""
from types import SimpleNamespace

import pytest

from pje_rpa import cofre_local, gmail_senha_app
from pje_rpa.gmail_senha_app import extrair_senha_de_app
from pje_rpa.totp import gerar_totp

CHAVE = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"  # 32 caracteres Base32 (exemplo publico)

GOOGLE = """<html><meta charset=utf-8><body><div id=tela></div><script>
const tela = document.getElementById('tela'); const log = window.log = [];
function passo(n) {
  if (n === 'email') tela.innerHTML = '<input type=email id=identifierId>';
  if (n === 'senha') tela.innerHTML = '<input type=password name=Passwd>';
  if (n === 'outro') tela.innerHTML = '<p>Verificacao em duas etapas</p><button>Tente outro método</button>';
  if (n === 'metodos') tela.innerHTML = '<button>Receber um código de verificação do app Google Authenticator</button>';
  if (n === 'totp') tela.innerHTML = '<input name=totpPin type=tel>';
  if (n === 'app') tela.innerHTML = '<label for=nm>Nome do app</label><input id=nm><button>Criar</button>';
  if (n === 'gerada') tela.innerHTML = '<div role=dialog>Senha de app gerada <span>abcd efgh ijkl mnop</span><button>Concluído</button></div>';
  window.n = n;
}
passo('email');
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const v = e.target.value; log.push(window.n);
  if (window.n === 'email') passo('senha');
  else if (window.n === 'senha') passo('outro');
  else if (window.n === 'totp') { window.totp = v; passo('app'); }
});
document.addEventListener('click', e => {
  const t = e.target.innerText || '';
  if (/outro/.test(t)) passo('metodos');
  else if (/Authenticator/.test(t)) passo('totp');
  else if (t === 'Criar') { window.nome = document.getElementById('nm').value; passo('gerada'); }
  else if (/Concluído/.test(t)) passo('fim');
});
</script></body></html>"""


def test_extrai_as_16_letras():
    assert extrair_senha_de_app("Sua senha de app\nabcd efgh ijkl mnop\nComo usar") == "abcdefghijklmnop"
    assert extrair_senha_de_app("nada aqui") == ""
    assert extrair_senha_de_app("gerada abcd efgh ijkl mnopConclu\u00eddo") == "abcdefghijklmnop"


def test_login_google_com_2fa_e_criacao_da_senha_de_app():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"chromium indisponivel: {exc}")
        page = browser.new_page()
        page.route("https://myaccount.google.com/**",
                   lambda route: route.fulfill(status=200, content_type="text/html", body=GOOGLE))
        assert gmail_senha_app.entrar_na_pagina_de_senhas_de_app(
            page, "fulana@gmail.com", "senha-do-email", CHAVE, assist=None, prazo_s=30) == ""
        assert page.evaluate("window.totp") == gerar_totp(CHAVE) or len(page.evaluate("window.totp")) == 6
        assert gmail_senha_app.criar_senha_de_app(page, "PJe RPA teste") == "abcdefghijklmnop"
        assert page.evaluate("window.nome") == "PJe RPA teste"
        browser.close()


def test_bloqueio_do_google_chama_o_operador():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"chromium indisponivel: {exc}")
        page = browser.new_page()
        page.route("https://myaccount.google.com/**", lambda route: route.fulfill(
            status=200, content_type="text/html",
            body="<html><body>Este navegador ou app pode não ser seguro</body></html>"))
        motivos = []
        resultado = gmail_senha_app.entrar_na_pagina_de_senhas_de_app(
            page, "fulana@gmail.com", "x", CHAVE, assist=lambda m: motivos.append(m) or "abcd efgh ijkl mnop")
        assert resultado == "abcd efgh ijkl mnop" and "captcha" in motivos[0]
        with pytest.raises(gmail_senha_app.GmailError):
            gmail_senha_app.entrar_na_pagina_de_senhas_de_app(page, "fulana@gmail.com", "x", CHAVE, assist=None)
        browser.close()


def test_cofre_guarda_cifrado_e_le_de_volta(tmp_path):
    arq = tmp_path / "c.json"
    cifra = lambda b: bytes(x ^ 0x5A for x in b)  # noqa: E731  (no Windows: DPAPI)
    assert cofre_local.guardar("Fulana@Gmail.com", "abcdefghijklmnop", arq, cifrar=cifra)
    assert "abcdefghijklmnop" not in arq.read_text()
    assert cofre_local.ler("fulana@gmail.com", arq, decifrar=cifra) == "abcdefghijklmnop"
    cofre_local.apagar("fulana@gmail.com", arq)
    assert cofre_local.ler("fulana@gmail.com", arq, decifrar=cifra) == ""
    assert not cofre_local.guardar("x@gmail.com", "y", arq, cifrar=lambda b: None)  # sem cofre: nao grava


def test_abrir_caixa_gera_senha_de_app_uma_vez_e_guarda(monkeypatch):
    from pje_rpa import email_inbox, lote

    guardado, geradas, logins = {}, [], []

    class FakeInbox:
        def __init__(self, host, user, password):
            self.password = password

        def check_login(self):
            logins.append(self.password)
            if self.password != "abcdefghijklmnop":
                raise RuntimeError("AUTHENTICATIONFAILED")

    monkeypatch.setattr(email_inbox, "ImapInbox", FakeInbox)
    monkeypatch.setattr(cofre_local, "ler", lambda email, *a, **k: guardado.get(email, ""))
    monkeypatch.setattr(cofre_local, "guardar", lambda email, s, *a, **k: guardado.__setitem__(email, s) or True)
    monkeypatch.setattr(cofre_local, "apagar", lambda email, *a, **k: guardado.pop(email, None))
    monkeypatch.setattr(gmail_senha_app, "gerar_senha_de_app",
                        lambda e, s, c, **k: geradas.append(e) or "abcdefghijklmnop")
    deps = SimpleNamespace(log=lambda m: None, opcoes=lote.Opcoes(), secret=lambda p: "")

    caixa = lote._abrir_caixa("fulana@gmail.com", "", "", "", deps, "senha-email", CHAVE)
    assert caixa.password == "abcdefghijklmnop" and geradas == ["fulana@gmail.com"]
    assert guardado == {"fulana@gmail.com": "abcdefghijklmnop"}
    # segunda vez: usa o cofre, nao entra no Gmail de novo
    lote._abrir_caixa("fulana@gmail.com", "", "", "", deps, "senha-email", CHAVE)
    assert geradas == ["fulana@gmail.com"]
    # senha de app revogada: apaga e gera outra
    guardado["fulana@gmail.com"] = "revogadarevogada"
    caixa = lote._abrir_caixa("fulana@gmail.com", "", "", "", deps, "senha-email", CHAVE)
    assert caixa.password == "abcdefghijklmnop" and len(geradas) == 2
