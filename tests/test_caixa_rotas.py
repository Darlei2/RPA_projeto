"""Caixa de e-mail: IMAP com um login so; sem IMAP utilizavel a conta fica pendente (rota pelo e-mail)."""
from datetime import datetime, timezone
from email.message import EmailMessage
from types import SimpleNamespace

from pje_rpa import cofre_local, email_inbox, lote, senha_cli
from pje_rpa.email_inbox import ImapInbox

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


def _preparar(monkeypatch, imap_ok=True):
    class ImapFalso:
        def __init__(self, host, user, password):
            self.password = password

        def check_login(self):
            if not imap_ok:
                raise RuntimeError("AUTHENTICATIONFAILED")

    monkeypatch.setattr(email_inbox, "ImapInbox", ImapFalso)
    monkeypatch.setattr(cofre_local, "ler", lambda email, *a, **k: "")
    monkeypatch.setattr(senha_cli, "_setup_inbox", lambda email: "PERGUNTOU_AO_OPERADOR")
    return ImapFalso


def test_com_senha_imap_usa_imap(monkeypatch):
    imap = _preparar(monkeypatch)
    deps, _ = _deps(senha_via_email=True)
    caixa = lote._abrir_caixa("fulana@gmail.com", "", "", "abcd efgh ijkl mnop", deps, "senha-email", CHAVE)
    assert isinstance(caixa, imap) and caixa.password == "abcdefghijklmnop"


def test_sem_senha_imap_fica_pendente_na_rota_do_email(monkeypatch):
    _preparar(monkeypatch)
    deps, logs = _deps(senha_via_email=True)
    # mesmo com a senha e a chave 2FA do e-mail na planilha: o robo nao entra no Gmail
    caixa = lote._abrir_caixa("fulana@gmail.com", "", "", "", deps, "senha-email", CHAVE)
    assert isinstance(caixa, lote.CaixaPendente) and "sem senha IMAP" in caixa.motivo
    assert "senha-email" not in " ".join(logs)


def test_senha_imap_recusada_fica_pendente_na_rota_do_email(monkeypatch):
    _preparar(monkeypatch, imap_ok=False)
    deps, logs = _deps(senha_via_email=True, assistido=True)
    caixa = lote._abrir_caixa("fulana@gmail.com", "", "", "abcdefghijklmnop", deps, "senha-email", CHAVE)
    assert isinstance(caixa, lote.CaixaPendente) and "recusou a senha IMAP" in caixa.motivo
    assert any("FALHA na caixa de e-mail" in m for m in logs)


def test_fora_da_rota_do_email_o_operador_informa_a_caixa(monkeypatch):
    _preparar(monkeypatch)
    deps, _ = _deps()
    assert lote._abrir_caixa("fulana@outlook.com", "", "", "", deps, "", "") == "PERGUNTOU_AO_OPERADOR"


def test_conta_pendente_nao_gera_pedido_de_senha_no_pje(monkeypatch):
    _preparar(monkeypatch)
    deps, logs = _deps(senha_via_email=True)
    caixa = lote._abrir_caixa("semimap@gmail.com", "", "", "", deps, "", "")
    assert isinstance(caixa, lote.CaixaPendente)

    chamadas = []
    deps.credencial = lambda *a: chamadas.append(a)
    registro = SimpleNamespace(email="semimap@gmail.com", nome_esperado="Fulana", cpf_esperado="11122233344")
    linha = SimpleNamespace(record=registro, segredo=lambda c: "")
    config = SimpleNamespace(tribunal="TRE-MG - 1o grau", base_url="https://pje1g-mg.tse.jus.br/pje/login.seam")
    res = lote._etapa_senha(config, linha, None, deps, lote.ResultadoAdvogado(5, "Fulana"), {"inbox": caixa}, "")
    assert not chamadas and not res.login_ok and res.credencial.observacao.startswith("PENDENTE - e-mail")
    assert any("Pedido de senha NAO enviado ao PJe" in m for m in logs)


def test_fecha_a_conexao_imap_ao_terminar_o_advogado():
    fechadas = []
    deps, _ = _deps(cache={7: {"inbox": SimpleNamespace(close=lambda: fechadas.append(7))}, 8: {"inbox": None}})
    lote._fechar_caixa(deps, 7)
    lote._fechar_caixa(deps, 8)
    lote._fechar_caixa(deps, 9)
    assert fechadas == [7]
