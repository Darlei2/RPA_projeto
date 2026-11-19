"""Mapa do formulario de cadastro de cada PJe (campos e obrigatorios), sem preencher nada."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from pje_rpa import exigencias, mapeamento
from pje_rpa.audit import AuditLog
from pje_rpa.browser import SimulatedBrowser
from pje_rpa.certificate import CertificateInfo
from pje_rpa.config import load_config, load_lawyer
from pje_rpa.execucao import ResultadoCadastro
from pje_rpa.flow import PJeRegistrationFlow
from pje_rpa.lote import Dependencias, Opcoes
from pje_rpa.models import RunStatus
from pje_rpa.signer import SimulatedSigner

PROJECT = Path(__file__).resolve().parents[1]
RECORD = load_lawyer(PROJECT / "cadastro.example.json")


def _ctrl(label, **extra):
    base = {"tag": "input", "type": "text", "id": "", "name": "", "label": label, "section": "",
            "title": "", "placeholder": "", "autocomplete": "", "value": "", "required": False,
            "readonly": False, "disabled": False, "checked": False, "options": []}
    return {**base, **extra}


SNAPSHOT = {"controls": [
    _ctrl("Nome", value=RECORD.nome_esperado, readonly=True),
    _ctrl("CPF", value="529.982.247-25", readonly=True),
    _ctrl("E-mail", value=RECORD.email, required=True),
    _ctrl("Telefone celular *", required=True),
    _ctrl("Número do RG", required=True),
    _ctrl("Órgão expedidor", required=True),
    _ctrl("UF", tag="select", type="select-one", section="Documento de identificação RG",
          options=[{"value": "", "label": "Selecione"}, {"value": "BA", "label": "BA"}, {"value": "SP", "label": "SP"}]),
    _ctrl("Endereço principal", tag="select", type="select-one",
          options=[{"value": "1", "label": RECORD.endereco.logradouro + ", 10"}, {"value": "2", "label": "Outro"}]),
    _ctrl(f"Contato {RECORD.email} 71 99999-8888"),
    _ctrl("Estado civil", tag="select", type="select-one",
          options=[{"value": str(i), "label": f"Opcao {i}"} for i in range(25)]),
]}


# ------------------------------------------------------------------ leitura da tela
def test_campos_sem_valores_e_sem_dados_do_advogado():
    campos = mapeamento.campos_do_formulario(SNAPSHOT, RECORD)
    por_rotulo = {c["rotulo"]: c for c in campos}
    texto = repr(campos)
    # nenhum valor digitado ou trazido pelo PJe vai para a tabela
    assert RECORD.email not in texto and "529" not in texto and "99999" not in texto
    for parte in (p for p in RECORD.nome_esperado.split() if len(p) > 3):
        assert parte not in texto
    assert por_rotulo["Nome"]["veio_preenchido"] == "sim" and por_rotulo["Nome"]["bloqueado"] == "sim"
    telefone = por_rotulo["Telefone celular *"]
    assert (telefone["campo"], telefone["obrigatorio"], telefone["veio_preenchido"]) == ("phone", "sim", "nao")
    assert por_rotulo["Número do RG"]["campo"] == "rg_numero"
    assert por_rotulo["Órgão expedidor"]["campo"] == "rg_orgao"
    # "UF" no bloco do RG nao e o endereco
    assert por_rotulo["UF"]["campo"] == "outro:uf" and por_rotulo["UF"]["opcoes"] == "BA | SP"
    # opcao com o endereco do advogado nao entra; lista longa e cortada
    assert por_rotulo["Endereço principal"]["opcoes"] == "Outro"
    assert por_rotulo["Estado civil"]["opcoes"].endswith("| (+5)")
    assert "Contato *** ***" in por_rotulo  # e-mail e telefone mascarados


def test_endereco_dos_dados_cadastrais_no_contexto_do_pje():
    assert mapeamento.url_dados_cadastrais("https://pje.trt5.jus.br/primeirograu/login.seam") == [
        "https://pje.trt5.jus.br/primeirograu/PessoaAdvogado/AlteracaoCadastro/listView.seam",
        "https://pje.trt5.jus.br/pje/PessoaAdvogado/AlteracaoCadastro/listView.seam"]
    assert mapeamento.url_dados_cadastrais("https://pje1g-ba.tse.jus.br/pje/login.seam") == [
        "https://pje1g-ba.tse.jus.br/pje/PessoaAdvogado/AlteracaoCadastro/listView.seam"]


def test_familias_de_tribunais():
    from pje_rpa.lote import listar_tribunais

    opcoes = listar_tribunais()
    tres, _ = mapeamento.escolher(opcoes, "tre")
    assert len(tres) == 54 and all(o.chave.startswith("tre") for o in tres)
    trts, _ = mapeamento.escolher(opcoes, "trt")
    assert len(trts) == 48
    varios, invalidos = mapeamento.escolher(opcoes, "tjmt, tjmt-2g, xyz")
    assert [o.chave for o in varios] == ["tjmt", "tjmt-2g"] and invalidos == ["xyz"]
    assert len(mapeamento.escolher(opcoes, "todos")[0]) == len(opcoes)


# ------------------------------------------------------------------ tabela
def _config(chave):
    return load_config(PROJECT / "tribunais" / f"{chave}.toml")


def test_tabela_substitui_so_o_tribunal_mapeado(tmp_path):
    tabela, situacao = tmp_path / "f.csv", tmp_path / "s.csv"
    campos = mapeamento.campos_do_formulario(SNAPSHOT, RECORD)
    quando = datetime(2026, 11, 19)
    mapeamento.registrar("tjmt", _config("tjmt"), "MAPEADO", "ok", {"origem": "cadastro", "campos": campos},
                         tabela, situacao, quando)
    mapeamento.registrar("tjba", _config("tjba"), "MAPEADO", "ok", {"origem": "cadastro", "campos": campos[:2]},
                         tabela, situacao, quando)
    # nova tentativa no TJMT que falhou antes do formulario: os campos antigos ficam, a situacao muda
    mapeamento.registrar("tjmt", _config("tjmt"), "FAILED", "botao nao localizado", {}, tabela, situacao, quando)
    linhas = mapeamento.ler_tabela(tabela)
    assert [l["tribunal"] for l in linhas].count("tjmt") == len(campos)
    assert [l["tribunal"] for l in linhas].count("tjba") == 2
    assert linhas[0]["data"] == "19/11/2026" and linhas[0]["url"] == _config("tjba").base_url
    estados = {l["tribunal"]: l for l in mapeamento._ler(situacao)}
    assert estados["tjmt"]["situacao"] == "FAILED" and estados["tjba"]["campos"] == "2"
    # remapeado: so as linhas novas do TJMT
    mapeamento.registrar("tjmt", _config("tjmt"), "MAPEADO", "ok", {"origem": "cadastro", "campos": campos[:3]},
                         tabela, situacao, quando)
    assert [l["tribunal"] for l in mapeamento.ler_tabela(tabela)].count("tjmt") == 3


def test_tabela_alimenta_as_exigencias(tmp_path):
    tabela = tmp_path / "f.csv"
    tjmt, tjba, trt5 = _config("tjmt"), _config("tjba"), _config("trt5")
    campos = mapeamento.campos_do_formulario(SNAPSHOT, RECORD)
    mapeamento.registrar("tjmt", tjmt, "MAPEADO", "", {"origem": "cadastro", "campos": campos}, tabela, tmp_path / "s")
    # dados cadastrais (quem ja e cadastrado) nao viram exigencia de cadastro
    mapeamento.registrar("tjba", tjba, "JA_CADASTRADO", "", {"origem": "dados_cadastrais", "campos": campos},
                         tabela, tmp_path / "s")
    mapeamento.registrar("trt5", trt5, "MAPEADO", "", {"origem": "cadastro", "campos": campos}, tabela, tmp_path / "s")
    sem = SimpleNamespace(telefone="", rg_numero="", rg_orgao="", rg_data_expedicao="", estado_civil="")
    com = SimpleNamespace(telefone="71999998888", rg_numero="1", rg_orgao="SSP/BA", rg_data_expedicao="", estado_civil="")
    assert exigencias.faltando(tjmt.base_url, sem, tabela) == [
        "telefone (celular com DDD)", "numero do RG", "orgao emissor do RG"]
    assert exigencias.faltando(tjmt.base_url, com, tabela) == []
    assert exigencias.faltando(tjba.base_url, sem, tabela) == []
    # TRT: regra fixa + tabela, sem repetir
    assert exigencias.faltando(trt5.base_url, sem, tabela) == [
        "telefone (celular com DDD)", "numero do RG", "orgao emissor do RG"]
    # campo que veio preenchido pelo PJe nao e exigido da planilha
    preenchido = [dict(c, veio_preenchido="sim") if c["campo"] == "phone" else c for c in campos]
    mapeamento.registrar("tjmt", tjmt, "MAPEADO", "", {"origem": "cadastro", "campos": preenchido},
                         tabela, tmp_path / "s")
    assert "telefone (celular com DDD)" not in exigencias.faltando(tjmt.base_url, sem, tabela)


# ------------------------------------------------------------------ fluxo
class FakePage:
    def __init__(self):
        self.visitas = []
        self.url = ""

    def evaluate(self, script, *args):
        return SNAPSHOT

    def goto(self, url, **kwargs):
        self.visitas.append(url)
        self.url = url

    def wait_for_timeout(self, ms):
        pass

    def screenshot(self, path, full_page=True):
        Path(path).write_bytes(b"png")


class PageBrowser(SimulatedBrowser):
    def __init__(self, record, scenario="success"):
        super().__init__(record, scenario)
        self.page = FakePage()
        self.clicks = []

    def click(self, group):
        self.clicks.append(group)
        super().click(group)

    def fill(self, group, value):
        raise AssertionError("o mapeamento nao preenche nada")


def _fluxo(tmp_path, scenario):
    config = load_config(PROJECT / "config.example.toml")
    browser = PageBrowser(RECORD, scenario)
    info = CertificateInfo(RECORD.nome_esperado, RECORD.cpf_esperado, "AC",
                           datetime(2099, 1, 1, tzinfo=timezone.utc), "0" * 40)
    flow = PJeRegistrationFlow(config, RECORD, info, "x", browser, SimulatedSigner(browser), AuditLog(tmp_path),
                               code_provider=lambda m: "000000", mapear=True)
    return flow.run(), browser


def test_fluxo_le_o_formulario_e_sai_sem_enviar(tmp_path, monkeypatch):
    from pje_rpa import adaptive_form

    monkeypatch.setattr(adaptive_form, "operate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("preencheu")))
    resultado, browser = _fluxo(tmp_path, "success")
    assert resultado.status == RunStatus.REHEARSAL_OK
    assert "Formulario mapeado" in resultado.reason and "Nada foi preenchido" in resultado.reason
    assert "confirm_data" not in browser.clicks and "sign_term" not in browser.clicks
    mapa = resultado.details["mapa"]
    assert mapa["origem"] == "cadastro" and len(mapa["campos"]) == len(SNAPSHOT["controls"])


def test_ja_cadastrado_le_os_dados_cadastrais_sem_salvar(tmp_path):
    resultado, browser = _fluxo(tmp_path, "already_registered")
    assert resultado.status == RunStatus.ALREADY_REGISTERED
    assert "dados cadastrais mapeada" in resultado.reason
    assert browser.page.visitas[0].endswith("/PessoaAdvogado/AlteracaoCadastro/listView.seam")
    assert resultado.details["mapa"]["origem"] == "dados_cadastrais"


# ------------------------------------------------------------------ execucao
def test_run_mapear_grava_e_para_no_bloqueio(tmp_path, monkeypatch):
    from pje_rpa import lote

    linha = SimpleNamespace(ok=True, usa_windows=True, numero_linha=2, nome="ADV", record=RECORD, thumbprint="",
                            certificado=None, certificado_info="Windows", erros=[], segredo=lambda k: "")
    monkeypatch.setattr(lote, "_ler_advogados", lambda planilha, deps: ([linha], None))
    chamadas = []

    def cadastrar(config, record, pfx, senha, code, audit_root, **kw):
        chamadas.append((config.tribunal, kw))
        if config.tribunal.startswith("TJMT - 2"):
            return ResultadoCadastro("BLOCKED", "CAPTCHA detectado")
        return ResultadoCadastro("REHEARSAL_OK", "Formulario mapeado",
                                 mapa={"origem": "cadastro", "campos": mapeamento.campos_do_formulario(SNAPSHOT, RECORD)})

    log = []
    deps = Dependencias(ask=lambda p: "", secret=lambda p: "", yes=lambda p, d=False: True, cadastrar=cadastrar,
                        credencial=None, abrir_caixa=None, entregar=None, log=log.append,
                        opcoes=Opcoes(perguntar_2fa=False))
    codigo = mapeamento.run_mapear("tjmt,tjmt-2g,tjba", pausa=0, audit_root=tmp_path / "audit",
                                   tabela=tmp_path / "f.csv", arquivo_situacao=tmp_path / "s.csv", deps=deps)
    assert codigo == 4  # parou no bloqueio: o TJBA nem foi tentado
    assert [t for t, _ in chamadas] == ["TJMT - 1o grau", "TJMT - 2o grau"]
    assert all(kw["mapear"] and kw["rehearsal"] for _, kw in chamadas)
    estados = {l["tribunal"]: l["situacao"] for l in mapeamento._ler(tmp_path / "s.csv")}
    assert estados == {"tjmt": "MAPEADO", "tjmt-2g": "BLOCKED"}
    assert any("PAREI AQUI" in l for l in log)
