"""Caixa de e-mail (IMAP simulado), senha forte e planilha de entrega criptografada."""
import builtins
import getpass
import string
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

import pytest

from pje_rpa.email_inbox import ImapInbox, extract_password_link, find_link_in_messages, guess_imap_host
from pje_rpa.senha_forte import DIGITS, SPECIAL, gerar_senha

AGORA = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
LINK = "https://sso.cloud.pje.jus.br/auth/realms/pje/login-actions/action-token?key=abc.def&amp;client_id=pje"


def _msg(subject="Atualizar senha", sender="naoresponda@cnj.jus.br", when=AGORA + timedelta(minutes=1),
         body=None, html=True):
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["Date"] = subject, sender, format_datetime(when)
    body = body or f'<p>Clique <a href="{LINK}">aqui</a>. Veja https://www.tjap.jus.br/portal</p>'
    msg.set_content(body, subtype="html" if html else "plain")
    return msg


def test_extrai_link_de_acao_do_sso():
    assert extract_password_link(_msg()) == LINK.replace("&amp;", "&")


def test_ignora_link_fora_do_jus_br():
    msg = _msg(body='<a href="https://sso.cloud.pje.jus.br.golpe.com/login-actions/action-token?key=x">x</a>')
    assert extract_password_link(msg) is None


def test_ignora_email_antigo_e_email_sem_relacao():
    antigo = _msg(when=AGORA - timedelta(hours=1))
    propaganda = _msg(subject="Promocao", sender="loja@exemplo.com")
    assert find_link_in_messages([antigo, propaganda], AGORA) is None
    assert find_link_in_messages([antigo, propaganda, _msg()], AGORA) == LINK.replace("&amp;", "&")


def test_host_imap_por_dominio():
    assert guess_imap_host("adv@gmail.com") == "imap.gmail.com"
    assert guess_imap_host("adv@empresa.com.br") is None


class FakeImap:
    def __init__(self, messages):
        self.messages = messages
        self.logged = False

    def __call__(self, host):
        return self

    def login(self, user, password):
        if password != "app-pass":
            raise OSError("auth")
        self.logged = True

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Junk) "/" "[Gmail]/Spam"']

    def select(self, folder, readonly=True):
        assert readonly
        self.folder = folder
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        ids = [str(i + 1).encode() for i, (f, _) in enumerate(self.messages) if f'"{f}"' == self.folder]
        return "OK", [b" ".join(ids)]

    def fetch(self, msg_id, parts):
        assert "PEEK" in parts  # nao marca como lida
        return "OK", [(b"1 (BODY[]", self.messages[int(msg_id) - 1][1].as_bytes())]

    def logout(self):
        pass


def test_imap_acha_link_ate_no_spam():
    fake = FakeImap([("INBOX", _msg(subject="Outra coisa", sender="x@y.com")), ("[Gmail]/Spam", _msg())])
    inbox = ImapInbox("imap.gmail.com", "adv@gmail.com", "app-pass", poll_s=0, connect=fake)
    assert inbox.wait_for_password_link(AGORA, timeout_s=1) == LINK.replace("&amp;", "&")


def test_imap_sem_email_desiste_no_prazo():
    inbox = ImapInbox("h", "u", "app-pass", poll_s=0, connect=FakeImap([]))
    assert inbox.wait_for_password_link(AGORA, timeout_s=0) is None


def test_senha_forte():
    senhas = {gerar_senha() for _ in range(200)}
    assert len(senhas) == 200
    for s in senhas:
        assert len(s) == 16 and s[0].isalpha()
        assert sum(c in string.ascii_uppercase for c in s) >= 2
        assert sum(c in string.ascii_lowercase for c in s) >= 2
        assert sum(c in DIGITS for c in s) >= 2
        assert sum(c in SPECIAL for c in s) >= 2
        assert not set(s) & set("O0Il1 \"'\\<>=+")


def test_planilha_criptografada(tmp_path):
    pytest.importorskip("openpyxl")
    pytest.importorskip("msoffcrypto")
    from pje_rpa.entrega import LinhaCredencial, abrir_planilha, salvar_planilha

    linhas = [
        LinhaCredencial("TJAP - 1o grau", "https://pje.tjap.jus.br/1g/login.seam", "Fulano de Tal",
                        "01234567890", "adv@gmail.com", senha="Ab3$xY9!kLm2#pQr", chave_2fa="JBSWY3DPEHPK3PXP",
                        senha_cadastrada=True, login_testado=True),
        LinhaCredencial("TRT8", "https://pje.trt8.jus.br", "Fulano de Tal", "01234567890", "adv@gmail.com",
                        senha="nao-deve-aparecer", observacao="Link de senha nao recebido"),
    ]
    destino = salvar_planilha(linhas, tmp_path / "c.xlsx", "senha-da-planilha")
    assert b"Ab3$xY9" not in destino.read_bytes()  # cifrado: nada em claro no arquivo
    with pytest.raises(Exception):
        abrir_planilha(destino, "errada")
    ws = abrir_planilha(destino, "senha-da-planilha").active
    assert ws["A2"].value == "TJAP - 1o grau" and ws["C2"].value == "Fulano de Tal"
    assert ws["D2"].value == "01234567890" and ws["F2"].value == "Ab3$xY9!kLm2#pQr"
    assert ws["F3"].value in (None, "") and ws["H3"].value == "Nao"
    with pytest.raises(ValueError):
        salvar_planilha(linhas, tmp_path / "d.xlsx", "")


def test_assistente_completo_gera_senha_e_planilha(tmp_path, monkeypatch):
    pytest.importorskip("openpyxl")
    pytest.importorskip("msoffcrypto")
    from types import SimpleNamespace

    from pje_rpa import browser as browser_mod
    from pje_rpa import senha_cli
    from pje_rpa.credencial import StepResult
    from pje_rpa.entrega import abrir_planilha

    used = {}

    class FakeBrowser:
        def __init__(self, config):
            pass

        def close(self):
            pass

    class FakeInbox:
        def wait_for_password_link(self, since, timeout_s, on_wait=None):
            return "https://sso.cloud.pje.jus.br/x/login-actions/action-token?key=1"

    def fake_set(browser, config, link, password):
        used["senha"] = password
        return StepResult(True, "Senha cadastrada")

    monkeypatch.setattr(browser_mod, "PlaywrightBrowser", FakeBrowser)
    monkeypatch.setattr(senha_cli, "request_password_link", lambda *a: StepResult(True, "enviado"))
    monkeypatch.setattr(senha_cli, "set_password", fake_set)
    monkeypatch.setattr(senha_cli, "test_login", lambda *a: StepResult(True, "login ok"))
    monkeypatch.setattr(senha_cli, "_setup_inbox", lambda email: FakeInbox())
    answers = iter(["Fulano de Tal", "012.345.678-90", "adv@gmail.com", ""])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
    secrets_ = iter(["", "planilha", "planilha"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(secrets_))

    config = SimpleNamespace(tribunal="TJAP - 1o grau", base_url="https://pje.tjap.jus.br/1g/login.seam")
    code = senha_cli.run_password_wizard([config], tmp_path / "audit", tmp_path / "entregas")
    assert code == 0
    [arquivo] = list((tmp_path / "entregas").glob("credenciais-*.xlsx"))
    ws = abrir_planilha(arquivo, "planilha").active
    assert ws["F2"].value == used["senha"] and len(used["senha"]) == 16
    audit_text = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "audit").rglob("*.json*"))
    assert used["senha"] not in audit_text and "action-token" not in audit_text
