"""Rotas da caixa de e-mail: IMAP com um login so; Gmail pelo navegador quando nao ha senha IMAP."""
from datetime import datetime, timezone
from email.message import EmailMessage
from types import SimpleNamespace

from pje_rpa import cofre_local, email_inbox, gmail_web, lote
from pje_rpa.email_inbox import ImapInbox
from pje_rpa.gmail_senha_app import GmailError

LINK = "https://sso.cloud.pje.jus.br/auth/realms/pje/login-actions/action-token?key=abc"
CHAVE = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


def _msg(link=LINK):
    m = EmailMessage()
    m["From"], m["Subject"] = "naoresponda@tse.jus.br", "Nova senha"
    m["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    m.set_content("Acesse " + link)
    return m


class ImapComUid:
    def __init__(self):
        self.caixa, self.logins, self.baixadas, self.cai = [], 0, [], False

    def __call__(self, host):
        return self

    def login(self, user, password):
        self.logins += 1

    def noop(self):
        if self.cai:
            self.cai = False
            raise OSError("conexao caiu")
        return "OK", [b""]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"']

    def select(self, folder, readonly=True):
        return "OK", [b"1"]

    def uid(self, comando, *args):
        if comando == "SEARCH":
            return "OK", [b" ".join(str(100 + i).encode() for i in range(len(self.caixa)))]
        self.baixadas.append(args[0])
        return "OK", [(b"1 (UID BODY[]", self.caixa[int(args[0]) - 100].as_bytes())]

    def logout(self):
        pass


def test_imap_faz_um_login_so_para_conferir_ler_e_esperar():
    imap = ImapComUid()
    imap.caixa.append(_msg(LINK + "-velho"))
    inbox = ImapInbox("imap.gmail.com", "adv@gmail.com", "app-pass", poll_s=0, connect=imap)
    inbox.check_login()
    conhecidos = inbox.known_links()
    esperas = []

    def chega_na_terceira(restante):
        esperas.append(restante)
        if len(esperas) == 3:
            imap.caixa.append(_msg())

    desde = datetime.now(timezone.utc)
    assert inbox.wait_for_password_link(desde, timeout_s=30, on_wait=chega_na_terceira, exclude=conhecidos) == LINK
    assert imap.logins == 1 and inbox.logins == 1  # antes: um login por consulta
    assert imap.baixadas == [b"100", b"101"]  # cada mensagem e baixada uma vez so


def test_imap_religa_se_a_conexao_cair_e_fecha_no_fim():
    imap = ImapComUid()
    imap.caixa.append(_msg())
    inbox = ImapInbox("h", "u", "app-pass", poll_s=0, connect=imap, reconnect_s=0)
    inbox.check_login()
    imap.cai = True
    assert inbox.known_links() == frozenset({LINK}) and imap.logins == 2
    inbox.close()
    assert inbox._conn is None and not inbox._lidas


def _deps(**extras):
    logs = []
    return SimpleNamespace(log=logs.append, extras=extras, secret=lambda p: "", ask=lambda p: ""), logs


class CaixaWebFalsa:
    criadas = []

    def __init__(self, email, senha, chave, assist=None):
        self.email, self.assist, self.digitado, self.login_manual = email, assist, [], False
        CaixaWebFalsa.criadas.append(self)

    def check_login(self):
        if self.email.startswith("barrada"):
            raise GmailError("o Google pediu uma verificacao que o robo nao faz (captcha, celular ou navegador)")


def _preparar(monkeypatch, imap_ok=True):
    CaixaWebFalsa.criadas = []

    class ImapFalso:
        def __init__(self, host, user, password):
            self.password = password

        def check_login(self):
            if not imap_ok:
                raise RuntimeError("AUTHENTICATIONFAILED")

    monkeypatch.setattr(email_inbox, "ImapInbox", ImapFalso)
    monkeypatch.setattr(gmail_web, "GmailWebInbox", CaixaWebFalsa)
    monkeypatch.setattr(cofre_local, "ler", lambda email, *a, **k: "")
    return ImapFalso


def test_com_senha_imap_usa_imap_e_nao_abre_o_navegador(monkeypatch):
    imap = _preparar(monkeypatch)
    deps, _ = _deps(senha_via_email=True)
    caixa = lote._abrir_caixa("fulana@gmail.com", "", "", "abcd efgh ijkl mnop", deps, "senha-email", CHAVE)
    assert isinstance(caixa, imap) and caixa.password == "abcdefghijklmnop" and not CaixaWebFalsa.criadas


def test_sem_senha_imap_cai_na_rota_do_navegador(monkeypatch):
    _preparar(monkeypatch)
    deps, logs = _deps(senha_via_email=True)
    caixa = lote._abrir_caixa("fulana@gmail.com", "", "", "", deps, "senha-email", CHAVE)
    assert isinstance(caixa, CaixaWebFalsa) and caixa.assist is None  # sem modo assistido: nao pausa
    assert any("Rota reserva" in m for m in logs) and "senha-email" not in " ".join(logs)


def test_senha_imap_recusada_cai_na_rota_do_navegador(monkeypatch):
    _preparar(monkeypatch, imap_ok=False)
    deps, _ = _deps(senha_via_email=True, assistido=True)
    caixa = lote._abrir_caixa("fulana@gmail.com", "", "", "abcdefghijklmnop", deps, "senha-email", CHAVE)
    assert isinstance(caixa, CaixaWebFalsa) and caixa.assist is not None


def test_google_barra_a_conta_fica_pendente_e_o_pje_nao_recebe_pedido(monkeypatch):
    _preparar(monkeypatch)
    deps, logs = _deps(senha_via_email=True)
    caixa = lote._abrir_caixa("barrada@gmail.com", "", "", "", deps, "senha-email", CHAVE)
    assert isinstance(caixa, lote.CaixaPendente) and "verificacao" in caixa.motivo

    chamadas = []
    deps.credencial = lambda *a: chamadas.append(a)
    registro = SimpleNamespace(email="barrada@gmail.com", nome_esperado="Fulana", cpf_esperado="11122233344")
    linha = SimpleNamespace(record=registro, segredo=lambda c: "")
    config = SimpleNamespace(tribunal="TRE-MG - 1o grau", base_url="https://pje1g-mg.tse.jus.br/pje/login.seam")
    res = lote._etapa_senha(config, linha, None, deps, lote.ResultadoAdvogado(5, "Fulana"), {"inbox": caixa}, "")
    assert not chamadas and not res.login_ok and res.credencial.observacao.startswith("PENDENTE - Gmail")


def test_fecha_a_conexao_imap_ao_terminar_o_advogado():
    fechadas = []
    deps, _ = _deps(cache={7: {"inbox": SimpleNamespace(close=lambda: fechadas.append(7))}, 8: {"inbox": None}})
    lote._fechar_caixa(deps, 7)
    lote._fechar_caixa(deps, 8)
    lote._fechar_caixa(deps, 9)
    assert fechadas == [7]
