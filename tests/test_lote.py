"""Execucao geral: escolha do tribunal, leitura da planilha e lote advogado por advogado."""
import io
from pathlib import Path

import pytest

from pje_rpa.config import ConfigurationError, load_config
from pje_rpa.entrega import LinhaCredencial
from pje_rpa.execucao import ResultadoCadastro
from pje_rpa.lote import Dependencias, escolher_tribunal, executar_lote, listar_tribunais, run_executar
from pje_rpa.planilha import gravar_modelo, ler_planilha

PROJECT = Path(__file__).resolve().parents[1]

CABECALHO = ["Nome completo", "CPF", "OAB", "UF da OAB", "E-mail", "Celular", "CEP", "Endereco",
             "Numero", "Complemento", "Bairro", "Municipio", "UF", "Autorizacao", "Certificado",
             "Senha do certificado", "Executar"]


def _linha(nome="ADVOGADA DE TESTE", cpf="52998224725", pfx="adv.pfx", executar="", senha="", cep="01001000"):
    return [nome, cpf, "12345", "SP", "adv@exemplo.com.br", "11999999999", cep, "RUA A", "10", "",
            "CENTRO", "SAO PAULO", "SP", "CONTRATO-1", pfx, senha, executar]


def _xlsx(path: Path, rows, numeric_cpf=False):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(CABECALHO)
    for row in rows:
        if numeric_cpf:
            row = list(row)
            row[1] = int(row[1])
            row[6] = int(row[6])
        ws.append(row)
    wb.save(path)
    return path


@pytest.fixture()
def pasta(tmp_path):
    (tmp_path / "adv.pfx").write_bytes(b"pfx-falso")
    return tmp_path


# ------------------------------------------------------------------ planilha
def test_le_xlsx_com_nomes_de_coluna_variados(pasta):
    path = _xlsx(pasta / "a.xlsx", [_linha(), _linha(nome="OUTRA PESSOA", executar="N")])
    linhas = ler_planilha(path)
    assert len(linhas) == 1  # executar = N foi pulada
    l = linhas[0]
    assert l.ok, l.erros
    assert l.record.cpf_esperado == "52998224725" and l.record.oab_uf == "SP"
    assert l.certificado == pasta / "adv.pfx"
    assert l.segredo("senha_certificado") == ""


def test_excel_numerico_nao_perde_zeros(pasta):
    path = _xlsx(pasta / "a.xlsx", [_linha(cpf="01234567890", cep="01001000")], numeric_cpf=True)
    l = ler_planilha(path)[0]
    assert l.dados["cpf"] == "01234567890" and l.dados["cep"] == "01001000"


def test_linha_com_erro_e_certificado_inexistente(pasta):
    path = _xlsx(pasta / "a.xlsx", [_linha(cpf="11111111111", pfx="nao-existe.pfx")])
    l = ler_planilha(path)[0]
    assert not l.ok
    assert any("cpf" in e for e in l.erros) and any("certificado nao encontrado" in e for e in l.erros)


def test_csv_com_ponto_e_virgula(pasta):
    path = pasta / "a.csv"
    path.write_text(";".join(CABECALHO) + "\n" + ";".join(_linha()) + "\n", encoding="utf-8-sig")
    assert ler_planilha(path)[0].ok


def test_coluna_obrigatoria_ausente(pasta):
    path = pasta / "a.csv"
    path.write_text("nome;cpf\nFULANO;52998224725\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Colunas obrigatorias ausentes"):
        ler_planilha(path)


def test_planilha_protegida_por_senha(pasta):
    msoffcrypto = pytest.importorskip("msoffcrypto")
    from msoffcrypto.format.ooxml import OOXMLFile

    plain = _xlsx(pasta / "p.xlsx", [_linha()])
    out = io.BytesIO()
    OOXMLFile(io.BytesIO(plain.read_bytes())).encrypt("abc123", out)
    plain.write_bytes(out.getvalue())
    assert ler_planilha(plain, password_provider=lambda: "abc123")[0].ok


def test_modelo_e_lido_sem_executar_a_linha_de_exemplo(tmp_path):
    path = gravar_modelo(tmp_path / "modelo.xlsx")
    assert ler_planilha(path) == []  # exemplo vem com executar = N


# ----------------------------------------------------------------- tribunais
def test_escolha_do_tribunal_por_numero_chave_ou_nome(monkeypatch):
    monkeypatch.chdir(PROJECT)
    opcoes = listar_tribunais()
    chaves = [o.chave for o in opcoes]
    assert "tjap" in chaves and "trf6" in chaves and "_comum" not in chaves
    assert escolher_tribunal(opcoes, "tjap").chave == "tjap"
    assert escolher_tribunal(opcoes, "TJAP - 1o grau").chave == "tjap"
    assert escolher_tribunal(opcoes, str(chaves.index("trf6") + 1)).chave == "trf6"
    assert escolher_tribunal(opcoes, "xyz") is None


# --------------------------------------------------------------------- lote
class Fake:
    def __init__(self, respostas=(), cadastro=("COMPLETED",), yes=True):
        self.respostas = list(respostas)
        self.cadastro = list(cadastro)
        self.yes_default = yes
        self.log = []
        self.cadastros = []
        self.credenciais = []
        self.entregue = None

    def deps(self):
        def cadastrar(config, record, pfx, pwd, code_provider, audit_root, **kw):
            self.cadastros.append((record.nome_esperado, pwd, kw.get("rehearsal")))
            status = self.cadastro.pop(0) if self.cadastro else "COMPLETED"
            if status == "BOOM":
                raise RuntimeError("falha no navegador")
            return ResultadoCadastro(status, "ok", "run")

        def credencial(config, audit_root, lawyer, inbox, secret_2fa):
            self.credenciais.append(lawyer["cpf"])
            linha = LinhaCredencial(config.tribunal, config.base_url, lawyer["nome"], lawyer["cpf"], lawyer["email"])
            linha.senha_cadastrada = linha.login_testado = True
            return linha

        return Dependencias(
            ask=lambda p: self.respostas.pop(0) if self.respostas else "",
            secret=lambda p: "senha-a1",
            yes=lambda p, d=False: self.yes_default,
            cadastrar=cadastrar,
            credencial=credencial,
            abrir_caixa=lambda *a, **k: None,
            entregar=lambda linhas, saida: setattr(self, "entregue", list(linhas)),
            store_factory=lambda: _Store(),
            log=self.log.append,
            extras={"perguntar_2fa": False},
        )


class _Store:
    def remove(self, t):
        pass


def _config():
    return load_config(PROJECT / "tribunais" / "tjap.toml")


def test_fluxo_inteiro_um_advogado_por_vez(pasta):
    linhas = ler_planilha(_xlsx(pasta / "a.xlsx", [_linha(), _linha(nome="SEGUNDO ADVOGADO", cpf="01234567890")]))
    fake = Fake()
    res = executar_lote(_config(), linhas, pasta / "audit", pasta / "out", fake.deps(),
                        rehearsal=False, nova_senha_se_ja_cadastrado=False)
    assert [r.cadastro for r in res] == ["COMPLETED", "COMPLETED"]
    assert fake.credenciais == ["52998224725", "01234567890"]  # senha feita para cada um, em ordem
    assert len(fake.entregue) == 2 and all(r.login_ok for r in res)


def test_ja_cadastrado_nao_troca_senha_sem_autorizacao(pasta):
    linhas = ler_planilha(_xlsx(pasta / "a.xlsx", [_linha()]))
    fake = Fake(cadastro=["ALREADY_REGISTERED"])
    res = executar_lote(_config(), linhas, pasta / "audit", pasta / "out", fake.deps(),
                        rehearsal=False, nova_senha_se_ja_cadastrado=False)
    assert fake.credenciais == [] and "senha nao alterada" in res[0].motivo

    fake = Fake(cadastro=["ALREADY_REGISTERED"])
    executar_lote(_config(), linhas, pasta / "audit", pasta / "out", fake.deps(),
                  rehearsal=False, nova_senha_se_ja_cadastrado=True)
    assert fake.credenciais == ["52998224725"]


def test_ensaio_nao_mexe_em_senha(pasta):
    linhas = ler_planilha(_xlsx(pasta / "a.xlsx", [_linha()]))
    fake = Fake(cadastro=["REHEARSAL_OK"])
    executar_lote(_config(), linhas, pasta / "audit", pasta / "out", fake.deps(),
                  rehearsal=True, nova_senha_se_ja_cadastrado=True)
    assert fake.cadastros[0][2] is True and fake.credenciais == [] and fake.entregue is None


def test_erro_em_um_advogado_nao_para_o_lote(pasta):
    linhas = ler_planilha(_xlsx(pasta / "a.xlsx", [_linha(), _linha(nome="SEGUNDO ADVOGADO", cpf="01234567890")]))
    fake = Fake(cadastro=["BOOM", "COMPLETED"])
    res = executar_lote(_config(), linhas, pasta / "audit", pasta / "out", fake.deps(),
                        rehearsal=False, nova_senha_se_ja_cadastrado=False)
    assert res[0].cadastro == "ERROR" and res[1].cadastro == "COMPLETED"
    assert fake.credenciais == ["01234567890"]


def test_senha_do_certificado_vem_da_planilha_ou_e_perguntada(pasta):
    linhas = ler_planilha(_xlsx(pasta / "a.xlsx", [_linha(senha="da-planilha"), _linha(nome="SEGUNDO ADVOGADO", cpf="01234567890")]))
    fake = Fake()
    executar_lote(_config(), linhas, pasta / "audit", pasta / "out", fake.deps(),
                  rehearsal=False, nova_senha_se_ja_cadastrado=False)
    assert [c[1] for c in fake.cadastros] == ["da-planilha", "senha-a1"]


def test_assistente_pergunta_tribunal_e_planilha(pasta, monkeypatch):
    monkeypatch.chdir(PROJECT)
    planilha = _xlsx(pasta / "a.xlsx", [_linha()])
    fake = Fake(respostas=["nao-existe", "tjpe", str(planilha)], cadastro=["REHEARSAL_OK"])
    code = run_executar(pasta / "audit", pasta / "out", deps=fake.deps())
    texto = "\n".join(fake.log)
    assert "Nao reconhecido(s): nao-existe" in texto
    assert "TJPE" in texto and "ENSAIO" in texto  # tjpe nao liberado no repositorio -> ensaio
    assert fake.cadastros and fake.cadastros[0][2] is True
    assert code == 0


# ---------------------------------------------- cadastro com certificado (simulado)
def _pfx(nome, cpf, senha):
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{nome}:{cpf}")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
            .sign(key, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(
        b"a1", key, cert, None, serialization.BestAvailableEncryption(senha.encode()))


def test_cadastrar_instala_e_remove_o_certificado_e_nao_pede_senha_no_fluxo(tmp_path):
    import dataclasses

    from pje_rpa.browser import SimulatedBrowser
    from pje_rpa.certificate import SimulatedCertificateStore
    from pje_rpa.execucao import cadastrar, pending_path
    from pje_rpa.signer import SimulatedSigner

    class Store(SimulatedCertificateStore):
        def contains(self, thumbprint):
            return thumbprint in self.installed

    config = dataclasses.replace(_config(), selectors_approved=True, allow_live_submission=True)
    pasta = tmp_path
    (pasta / "adv.pfx").write_bytes(_pfx("MARIA DA SILVA", "52998224725", "s3nha"))
    linha = ler_planilha(_xlsx(pasta / "a.xlsx", [_linha(nome="MARIA DA SILVA")]))[0]
    store = Store()
    browsers = []

    def browser_factory(cfg):
        browsers.append(SimulatedBrowser(linha.record))
        return browsers[-1]

    res = cadastrar(config, linha.record, (pasta / "adv.pfx").read_bytes(), "s3nha",
                    lambda m: "000000", pasta / "audit", store=store,
                    browser_factory=browser_factory,
                    signer_factory=lambda cfg: SimulatedSigner(browsers[-1]), notify=lambda m: None)
    assert res.status == "COMPLETED", res.reason
    assert "senha" not in res.reason.lower()  # o lote cuida da senha depois
    assert store.installed == set() and not pending_path(pasta / "audit").exists()

    errado = cadastrar(config, linha.record, (pasta / "adv.pfx").read_bytes(), "errada",
                       lambda m: "000000", pasta / "audit", store=store, notify=lambda m: None)
    assert errado.status == "ERROR" and "Senha incorreta" in errado.reason


# --------------------------------------------------------- varios tribunais
def test_escolha_de_varios_tribunais(monkeypatch):
    from pje_rpa.lote import escolher_tribunais

    monkeypatch.chdir(PROJECT)
    opcoes = listar_tribunais()
    idx = {o.chave: i + 1 for i, o in enumerate(opcoes)}
    for resposta in ("tremg, tjpa, trt5", "tremg tjpa trt5", f"{idx['tremg']},{idx['tjpa']};{idx['trt5']}",
                     "tremg, tjpa, trt5, tjpa"):
        escolhidos, invalidos = escolher_tribunais(opcoes, resposta)
        assert [o.chave for o in escolhidos] == ["tremg", "tjpa", "trt5"] and invalidos == []
    escolhidos, invalidos = escolher_tribunais(opcoes, "tremg, xpto")
    assert invalidos == ["xpto"]
    assert [o.chave for o in escolher_tribunais(opcoes, "TJAP - 1o grau")[0]] == ["tjap"]


def test_varios_tribunais_em_sequencia_com_uma_entrega(pasta, monkeypatch):
    monkeypatch.chdir(PROJECT)
    planilha = _xlsx(pasta / "a.xlsx", [_linha(senha=""), _linha(nome="SEGUNDO ADVOGADO", cpf="01234567890")])
    fake = Fake(respostas=["tremg, tjpa, trt5", str(planilha)],
                cadastro=["REHEARSAL_OK"] * 6)
    perguntas = []
    deps = fake.deps()
    deps.secret = lambda p: perguntas.append(p) or "senha-a1"
    # ensaio explicito: na VM algum destes tribunais pode estar liberado para envio real
    assert run_executar(pasta / "audit", pasta / "out", ensaio=True, deps=deps) == 0
    # ordem: tribunal por tribunal, todos os advogados em cada um
    assert [c[0] for c in fake.cadastros] == ["ADVOGADA DE TESTE", "SEGUNDO ADVOGADO"] * 3
    # a senha do certificado e pedida uma vez por advogado, nao por tribunal
    assert len([p for p in perguntas if "certificado" in p]) == 2
    texto = "\n".join(fake.log)
    assert "TRIBUNAL 1/3: TRE-MG" in texto and "TRIBUNAL 3/3: TRT5" in texto and "RESUMO GERAL" in texto
    assert "cache" not in deps.extras  # segredos saem da memoria no fim


def test_real_com_tribunal_nao_liberado_segue_so_com_os_liberados(pasta, monkeypatch, tmp_path):
    import shutil

    from pje_rpa import lote

    trib = tmp_path / "tribunais"
    trib.mkdir()
    for nome in ("_comum.toml", "tjpe.toml"):
        shutil.copy(PROJECT / "tribunais" / nome, trib / nome)
    texto = (PROJECT / "tribunais" / "tremg.toml").read_text(encoding="utf-8")
    (trib / "tremg.toml").write_text(texto.replace("selectors_approved = false", "selectors_approved = true")
                                     .replace("allow_live_submission = false", "allow_live_submission = true"),
                                     encoding="utf-8")
    original = lote.listar_tribunais
    monkeypatch.setattr(lote, "listar_tribunais", lambda pasta=trib: original(trib))
    planilha = _xlsx(pasta / "a.xlsx", [_linha()])
    fake = Fake(respostas=["tremg, tjpe", str(planilha)], cadastro=["COMPLETED"])
    run_executar(pasta / "audit", pasta / "out", ensaio=False, deps=fake.deps())
    texto = "\n".join(fake.log)
    assert "Envio real NAO liberado em: TJPE" in texto
    assert len(fake.cadastros) == 1 and fake.credenciais == ["52998224725"]
