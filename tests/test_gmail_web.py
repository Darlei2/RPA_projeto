"""Gmail lido pelo navegador (perfil salvo por conta) contra um Google e um Gmail simulados."""
import json
from datetime import datetime, timezone

import pytest

from pje_rpa import gmail_web
from pje_rpa.gmail_senha_app import GmailError
from pje_rpa.gmail_web import GmailWebInbox, pasta_do_perfil

CHAVE = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"  # 32 caracteres Base32 (exemplo publico)
LINK_VELHO = "https://sso.cloud.pje.jus.br/auth/realms/pje/login-actions/action-token?key=velho"
LINK_NOVO = "https://sso.cloud.pje.jus.br/auth/realms/pje/login-actions/action-token?key=novo"

LOGIN = """<html><meta charset=utf-8><body><div id=tela></div><script>
const tela = document.getElementById('tela');
function passo(n) {
  if (n === 'email') tela.innerHTML = '<input type=email id=identifierId>';
  if (n === 'senha') tela.innerHTML = '<input type=password name=Passwd>';
  if (n === 'totp') tela.innerHTML = '<input name=totpPin type=tel>';
  window.n = n;
}
passo('email');
document.addEventListener('keydown', async e => {
  if (e.key !== 'Enter') return;
  if (window.n === 'email') passo('senha');
  else if (window.n === 'senha') passo('totp');
  else if (window.n === 'totp') { await fetch('/logar'); location.href = 'https://mail.google.com/mail/u/0/'; }
});
</script></body></html>"""

GMAIL = """<html><meta charset=utf-8><body><input name=q><div role=main id=m></div><script>
async function desenhar() {
  const msgs = await (await fetch('/mail/u/0/api')).json();
  const partes = location.hash.split('/');
  const m = document.getElementById('m');
  if (location.hash.startsWith('#search/') && partes.length === 3) {
    const msg = msgs[Number(partes[2])];
    m.innerHTML = '<div role=listitem>' + msg.links.map(l => '<a href="' + l + '">abrir</a>').join(' ') + '</div>';
  } else if (location.hash.startsWith('#search/')) {
    m.innerHTML = '<table>' + msgs.map((x, i) =>
      '<tr role=row class=zA onclick="location.hash += \\'/' + i + '\\'"><td>' + x.assunto + '</td></tr>').join('') + '</table>';
  } else m.innerHTML = 'Caixa de entrada';
}
window.addEventListener('hashchange', desenhar); desenhar();
</script></body></html>"""


@pytest.fixture
def pagina(tmp_path):
    """Google/Gmail simulados; `caixa(**kw)` cria a GmailWebInbox ligada a eles."""
    pytest.importorskip("playwright.sync_api")
    estado = {"logado": False, "bloqueia": False, "edge_normal": 0, "contextos": 0,
              "mensagens": [{"assunto": "PJe - senha", "links": [
                  LINK_VELHO, "https://www.tre-mg.jus.br/institucional", "https://exemplo.com/reset"]}]}

    def contas(route):
        if route.request.url.endswith("/logar"):
            estado["logado"] = True
            return route.fulfill(status=200, body="ok")
        if estado["bloqueia"]:
            return route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                 body="<html><body>Este navegador ou app pode não ser seguro</body></html>")
        if estado["logado"]:
            return route.fulfill(status=200, content_type="text/html",
                                 body="<script>location.href='https://mail.google.com/mail/u/0/'</script>")
        return route.fulfill(status=200, content_type="text/html", body=LOGIN)

    def gmail(route):
        if route.request.url.endswith("/api"):
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(estado["mensagens"]))
        return route.fulfill(status=200, content_type="text/html", body=GMAIL)

    def abrir_contexto(pw, perfil, navegador):
        estado["contextos"] += 1
        contexto = pw.chromium.launch().new_context()
        contexto.route("https://accounts.google.com/**", contas)
        contexto.route("https://mail.google.com/**", gmail)
        return contexto

    def caixa(**kw):
        kw.setdefault("abrir_edge_normal", lambda perfil, url: estado.__setitem__("edge_normal", estado["edge_normal"] + 1))
        kw.setdefault("abrir_contexto", abrir_contexto)
        return GmailWebInbox("fulana@gmail.com", "senha-do-email", CHAVE, perfil=tmp_path / "perfil", poll_s=1, **kw)

    return estado, caixa


def test_login_automatico_le_os_links_e_a_sessao_fica_salva(pagina):
    estado, caixa = pagina
    from playwright.sync_api import sync_playwright
    with sync_playwright() as outro:  # o navegador do PJe aberto na thread principal nao atrapalha
        pje = outro.chromium.launch()
        inbox = caixa()
        assert inbox.known_links() == frozenset({LINK_VELHO})  # so link de acao em .jus.br
        assert inbox.digitado == ["email", "senha", "totp"] and not inbox.login_manual
        inbox.check_login()
        assert inbox.digitado == []  # segundo acesso: sessao salva, nada digitado
        pje.close()


def test_espera_o_link_novo_e_ignora_o_que_ja_estava_na_caixa(pagina):
    estado, caixa = pagina
    inbox = caixa()
    conhecidos = inbox.known_links()
    esperas = []

    def chegou_depois(restante):
        esperas.append(restante)
        estado["mensagens"].insert(0, {"assunto": "PJe - nova senha", "links": [LINK_NOVO]})

    desde = datetime.now(timezone.utc)
    assert inbox.wait_for_password_link(desde, timeout_s=30, on_wait=chegou_depois, exclude=conhecidos) == LINK_NOVO
    assert len(esperas) == 1


def test_sem_email_novo_devolve_none(pagina):
    estado, caixa = pagina
    inbox = caixa()
    inbox.known_links()
    assert inbox.wait_for_password_link(datetime.now(timezone.utc), timeout_s=2) is None


def test_google_barra_o_robo_e_o_operador_entra_num_edge_normal(pagina):
    estado, caixa = pagina
    estado["bloqueia"] = True
    motivos = []

    def operador(motivo):
        motivos.append(motivo)
        estado.update(bloqueia=False, logado=True)  # login feito a mao no Edge normal, mesmo perfil
        return ""

    inbox = caixa(assist=operador)
    assert inbox.known_links() == frozenset({LINK_VELHO})
    assert inbox.login_manual and inbox.digitado == [] and estado["edge_normal"] == 1
    assert "captcha" in motivos[0] and estado["contextos"] == 2  # fechou o do robo antes do login manual


def test_google_barra_e_sem_operador_ou_com_desistencia_falha_sem_insistir(pagina):
    estado, caixa = pagina
    estado["bloqueia"] = True
    with pytest.raises(GmailError):
        caixa().check_login()
    with pytest.raises(GmailError):
        caixa(assist=lambda motivo: "p").check_login()
    assert estado["logado"] is False


def test_pasta_do_perfil_nao_revela_o_email(tmp_path):
    pasta = pasta_do_perfil("Fulana@Gmail.com", tmp_path)
    assert pasta == pasta_do_perfil("fulana@gmail.com ", tmp_path) and "fulana" not in str(pasta).lower()


def test_teste_de_uma_conta_avisa_as_colunas_que_faltam(monkeypatch, capsys):
    monkeypatch.setattr(gmail_web, "_linha_da_planilha",
                        lambda caminho, numero, senha: {"nome": "Fulana", "email": "fulana@gmail.com"})
    assert gmail_web.main(["--linha", "5"]) == 2
    saida = capsys.readouterr().out
    assert "email_senha" in saida and "email_2fa" in saida


def test_erro_do_navegador_nunca_mostra_a_senha(pagina):
    estado, caixa = pagina

    def quebra(pw, perfil, navegador):
        raise RuntimeError('fill("senha-do-email") falhou')

    with pytest.raises(GmailError) as erro:
        caixa(abrir_contexto=quebra).check_login()
    assert "senha-do-email" not in str(erro.value)
