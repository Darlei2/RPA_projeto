"""Integracao real: o robo abre o Chrome (nao pelo Playwright), conecta por CDP, loga uma vez,
salva a sessao e le o link. Usa Google/Gmail simulados; nao toca na internet."""
import time
from pathlib import Path
from datetime import datetime, timezone

import pytest

from pje_rpa import gmail_web
from pje_rpa.gmail_web import GmailWebInbox, abrir_chrome_cdp

CHAVE = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
LINK = "https://sso.cloud.pje.jus.br/auth/realms/pje/login-actions/action-token?key=abc"

LOGIN = """<html><meta charset=utf-8><body><div id=t></div><script>
const t = document.getElementById('t');
function passo(n){window.n=n;t.innerHTML={email:'<input type=email id=identifierId>',
  senha:'<input type=password name=Passwd>',totp:'<input name=totpPin type=tel>'}[n]||'';}
passo('email');
document.addEventListener('keydown',async e=>{if(e.key!=='Enter')return;
  if(window.n==='email')passo('senha');else if(window.n==='senha')passo('totp');
  else{await fetch('/logar');location.href='https://mail.google.com/mail/u/0/';}});
</script></body></html>"""

GMAIL = """<html><meta charset=utf-8><body><input name=q><div role=main id=m></div><script>
async function d(){const c=await(await fetch('/api')).json();const p=location.hash.split('/');const m=document.getElementById('m');
if(location.hash.startsWith('#search/')&&p.length===3){m.innerHTML='<div><a href="'+c[+p[2]].link+'">x</a></div>';}
else if(location.hash.startsWith('#search/')){m.innerHTML='<table>'+c.map((x,i)=>'<tr role=row class=zA onclick="location.hash=\\'#search/q/'+i+'\\'"><td>'+x.a+'</td></tr>').join('')+'</table>';}
else m.innerHTML='Caixa';}
window.addEventListener('hashchange',d);d();
</script></body></html>"""


@pytest.fixture
def chrome_real(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            chromium_bin = pw.chromium.executable_path
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"sem chromium: {exc}")
    if not chromium_bin or not Path(chromium_bin).exists():
        pytest.skip("Chromium do Playwright nao instalado (a VM usa Edge; este teste so roda onde ha Chromium)")
    estado = {"logins": 0, "sessao_valida": False, "api": [{"a": "PJe senha", "link": LINK}]}

    def contexto_roteado(pw, perfil, navegador, headless=False):
        ctx = abrir_chrome_cdp(pw, perfil, navegador, headless=True)  # headless so no teste

        def contas(route):
            if route.request.url.endswith("/logar"):
                estado["logins"] += 1
                return route.fulfill(status=200, body="ok")
            if estado["sessao_valida"]:  # simula a sessao ja salva no perfil (Google joga na caixa)
                return route.fulfill(status=200, content_type="text/html",
                                     body="<script>location.href='https://mail.google.com/mail/u/0/'</script>")
            return route.fulfill(status=200, content_type="text/html", body=LOGIN)

        def gmail(route):
            import json as _j
            if route.request.url.endswith("/api"):
                return route.fulfill(status=200, content_type="application/json", body=_j.dumps(estado["api"]))
            return route.fulfill(status=200, content_type="text/html", body=GMAIL)

        ctx.route("https://accounts.google.com/**", contas)
        ctx.route("https://mail.google.com/**", gmail)
        return ctx

    return estado, contexto_roteado, tmp_path, chromium_bin


def test_chrome_real_loga_uma_vez_salva_sessao_e_le_o_link(chrome_real):
    estado, contexto_roteado, tmp_path, chromium_bin = chrome_real
    perfil = tmp_path / "perfil"

    def nova(**kw):
        return GmailWebInbox("fulana@gmail.com", "senha", CHAVE, perfil=perfil, headless=True,
                             navegador=chromium_bin,
                             abrir_contexto=contexto_roteado, abrir_navegador_normal=lambda *a, **k: None, **kw)

    # 1a vez: sem sessao, o robo abre o Chrome real, conecta por CDP e faz o login (e-mail, senha, 2FA)
    caixa = nova()
    links = caixa.known_links()
    assert estado["logins"] == 1 and caixa.digitado == ["email", "senha", "totp"] and not caixa.login_manual
    assert links == frozenset({LINK})  # abriu o Chrome real, logou e leu o link

    # com a sessao ja valida no perfil, o Google cai direto na caixa: o robo NAO loga de novo
    estado["sessao_valida"] = True
    caixa2 = nova()
    assert caixa2.known_links() == frozenset({LINK})
    assert caixa2.digitado == [] and estado["logins"] == 1  # nao digitou nada: usou a sessao salva


def test_sem_chave_nao_abre_navegador(chrome_real):
    estado, contexto_roteado, tmp_path, chromium_bin = chrome_real
    abriu = []
    caixa = GmailWebInbox("fulana@gmail.com", "senha", "", perfil=tmp_path / "p", headless=True,
                          abrir_contexto=lambda *a, **k: abriu.append(1))
    with pytest.raises(gmail_web.GmailError):
        caixa.check_login()
    assert not abriu
