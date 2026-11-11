"""Uso do A1 ja instalado no Windows, escolhido pelo CPF (ou thumbprint) da linha da planilha."""
import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from pje_rpa.certificate import CertificateError, InstalledCertificate, find_installed
from pje_rpa.config import load_config
from pje_rpa.planilha import ler_planilha

PROJECT = Path(__file__).resolve().parents[1]
CPF = "52998224725"
OUTRO_CPF = "01234567890"


def _cert(nome, cpf, dias=30, emissor="AC TESTE"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{nome}:{cpf}")])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, emissor)])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(min(now, now + timedelta(days=dias)) - timedelta(days=1))
            .not_valid_after(now + timedelta(days=dias)).sign(key, hashes.SHA256()))
    return cert


def _installed(cert, chave=True, provider="Microsoft Enhanced RSA and AES Cryptographic Provider"):
    return InstalledCertificate(cert.public_bytes(serialization.Encoding.DER), chave, provider)


def _thumb(cert):
    return cert.fingerprint(hashes.SHA1()).hex().upper()


class FakeStore:
    def __init__(self, *items):
        self.items = list(items)
        self.installed = set()
        self.removed = []

    def list_installed(self):
        return self.items

    def contains(self, thumbprint):
        return True

    def install(self, data, password):  # nao deve ser chamado no modo Windows
        raise AssertionError("nao deveria instalar")

    def remove(self, thumbprint):
        self.removed.append(thumbprint)


# ------------------------------------------------------------- escolha do certificado
def test_escolhe_pelo_cpf_e_ignora_outros_titulares():
    meu, outro = _cert("MARIA DA SILVA", CPF), _cert("JOSE SOUZA", OUTRO_CPF)
    info, aviso = find_installed(FakeStore(_installed(outro), _installed(meu)), CPF)
    assert info.thumbprint_sha1 == _thumb(meu) and info.nome == "MARIA DA SILVA" and aviso == ""


def test_renovacao_usa_o_de_validade_mais_longa_e_avisa():
    antigo, novo = _cert("MARIA DA SILVA", CPF, dias=10), _cert("MARIA DA SILVA", CPF, dias=300)
    info, aviso = find_installed(FakeStore(_installed(antigo), _installed(novo)), CPF)
    assert info.thumbprint_sha1 == _thumb(novo) and "2 certificados" in aviso


def test_sem_chave_privada_ou_vencido_nao_serve():
    sem_chave = _cert("MARIA DA SILVA", CPF)
    vencido = _cert("MARIA DA SILVA", CPF, dias=-5)
    with pytest.raises(CertificateError, match="Nenhum certificado valido"):
        find_installed(FakeStore(_installed(sem_chave, chave=False), _installed(vencido)), CPF)


def test_thumbprint_da_planilha_escolhe_exatamente_aquele():
    a, b = _cert("MARIA DA SILVA", CPF, dias=300), _cert("MARIA DA SILVA", CPF, dias=10)
    info, _ = find_installed(FakeStore(_installed(a), _installed(b)), CPF, _thumb(b))
    assert info.thumbprint_sha1 == _thumb(b)  # mesmo sendo o de validade menor
    outro = _cert("JOSE SOUZA", OUTRO_CPF)
    with pytest.raises(CertificateError, match="outro CPF"):
        find_installed(FakeStore(_installed(outro)), CPF, _thumb(outro))


def test_chave_cng_gera_aviso():
    cert = _cert("MARIA DA SILVA", CPF)
    _, aviso = find_installed(FakeStore(_installed(cert, provider="CNG")), CPF)
    assert "CNG" in aviso


# ---------------------------------------------------------------- planilha
def _planilha(tmp_path, certificado):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["nome", "cpf", "oab_numero", "oab_uf", "email", "telefone", "cep", "logradouro",
               "numero", "bairro", "cidade", "uf", "autorizacao_ref", "certificado"])
    ws.append(["MARIA DA SILVA", CPF, "12345", "SP", "maria@exemplo.com.br", "11999999999", "01001000",
               "RUA A", "10", "CENTRO", "SAO PAULO", "SP", "CONTRATO-1", certificado])
    path = tmp_path / "a.xlsx"
    wb.save(path)
    return path


@pytest.mark.parametrize("valor", ["", "windows", "Windows"])
def test_certificado_vazio_usa_o_do_windows(tmp_path, valor):
    linha = ler_planilha(_planilha(tmp_path, valor))[0]
    assert linha.ok and linha.usa_windows and linha.thumbprint == ""


def test_thumbprint_na_coluna_certificado(tmp_path):
    thumb = "ab" * 20
    linha = ler_planilha(_planilha(tmp_path, thumb))[0]
    assert linha.ok and linha.usa_windows and linha.thumbprint == thumb.upper()


def test_planilha_sem_coluna_certificado(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("nome;cpf;oab_numero;oab_uf;email;telefone;cep;logradouro;numero;bairro;cidade;uf\n"
                    f"MARIA DA SILVA;{CPF};12345;SP;m@exemplo.com.br;11999999999;01001000;RUA A;10;CENTRO;SAO PAULO;SP\n",
                    encoding="utf-8")
    assert ler_planilha(path)[0].usa_windows


# ------------------------------------------------------------ cadastro e lote
def test_cadastro_com_certificado_do_windows_nao_instala_nem_remove(tmp_path):
    from pje_rpa.browser import SimulatedBrowser
    from pje_rpa.execucao import cadastrar
    from pje_rpa.signer import SimulatedSigner

    config = dataclasses.replace(load_config(PROJECT / "tribunais" / "tjap.toml"),
                                 selectors_approved=True, allow_live_submission=True)
    linha = ler_planilha(_planilha(tmp_path, ""))[0]
    store = FakeStore(_installed(_cert("MARIA DA SILVA", CPF)))
    browsers, mensagens = [], []

    def browser_factory(cfg):
        browsers.append(SimulatedBrowser(linha.record))
        return browsers[-1]

    res = cadastrar(config, linha.record, None, "", lambda m: "000000", tmp_path / "audit", store=store,
                    browser_factory=browser_factory, signer_factory=lambda cfg: SimulatedSigner(browsers[-1]),
                    notify=mensagens.append)
    assert res.status == "COMPLETED", res.reason
    assert store.removed == []  # nao era nosso: continua instalado
    assert any("instalado no Windows" in m for m in mensagens)


def test_lote_confere_os_certificados_antes_de_comecar(tmp_path, monkeypatch):
    from pje_rpa.lote import run_executar
    import importlib.util

    spec = importlib.util.spec_from_file_location("test_lote_helpers", PROJECT / "tests" / "test_lote.py")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    Fake = helpers.Fake

    monkeypatch.chdir(PROJECT)
    planilha = _planilha(tmp_path, "")
    fake = Fake(respostas=["tjpe", str(planilha)], cadastro=["REHEARSAL_OK"])
    deps = fake.deps()
    deps.store_factory = lambda: FakeStore(_installed(_cert("MARIA DA SILVA", CPF)))
    assert run_executar(tmp_path / "audit", tmp_path / "out", ensaio=True, deps=deps) == 0
    texto = "\n".join(fake.log)
    assert "certificado: Windows, AC TESTE" in texto
    assert fake.cadastros and fake.cadastros[0][1] == ""  # sem senha de certificado

    fake = Fake(respostas=["tjpe", str(planilha)])
    deps = fake.deps()
    deps.store_factory = lambda: FakeStore(_installed(_cert("JOSE SOUZA", OUTRO_CPF)))
    assert run_executar(tmp_path / "audit", tmp_path / "out", ensaio=True, deps=deps) == 2
    assert "Nenhum certificado valido" in "\n".join(fake.log) and not fake.cadastros


def test_sessao_do_login_unico_cai_depois_do_cadastro_robo_entra_de_novo(tmp_path, monkeypatch):
    """Cadastro concluido, mas o SSO pede login de novo: o robo reentra com o certificado
    (so confere que ja e cadastrado) e cria a senha na segunda tentativa."""
    from pje_rpa import credencial
    from pje_rpa.browser import SimulatedBrowser
    from pje_rpa.credencial import StepResult
    from pje_rpa.execucao import cadastrar
    from pje_rpa.signer import SimulatedSigner

    class Navegador(SimulatedBrowser):
        def reset_session(self):
            self.stage = "closed"

    config = dataclasses.replace(load_config(PROJECT / "tribunais" / "tjap.toml"),
                                 selectors_approved=True, allow_live_submission=True)
    linha = ler_planilha(_planilha(tmp_path, ""))[0]
    store = FakeStore(_installed(_cert("MARIA DA SILVA", CPF)))
    nav = Navegador(linha.record)
    chamadas, mensagens = [], []

    def falso_sso(browser, cfg, senha, reserva=None):
        chamadas.append(browser.stage)
        if len(chamadas) == 1:
            browser.page = None                       # (no robo real o navegador tem pagina)
            browser.scenario = "already_registered"   # agora o advogado ja esta cadastrado
            return StepResult(False, "A sessao do login unico nao esta mais ativa")
        return StepResult(True, "Senha cadastrada pelo login unico (sem e-mail)", "SenhaNova123")

    monkeypatch.setattr(credencial, "set_password_via_sso", falso_sso)
    res = cadastrar(config, linha.record, None, "", lambda m: "000000", tmp_path / "audit", store=store,
                    browser_factory=lambda cfg: nav, signer_factory=lambda cfg: SimulatedSigner(nav),
                    notify=mensagens.append, senha_sso=True)
    assert res.status == "COMPLETED" and res.senha == "SenhaNova123", (res, mensagens)
    assert len(chamadas) == 2
    assert any("entrando de novo com o certificado" in m for m in mensagens)
    assert "SenhaNova123" not in " ".join(mensagens)
