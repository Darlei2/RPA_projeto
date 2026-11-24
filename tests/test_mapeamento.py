"""Mapa do formulario de cadastro de cada PJe (campos e obrigatorios), sem preencher nada."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from pje_rpa import adaptive_form, exigencias, mapeamento
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


SNAPSHOT = {"controls": [dict(c, index=i) for i, c in enumerate([
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
])]}

# Formulario devolvido pelo PJe depois do envio com trava: "campo obrigatorio" no estado civil e um campo
# que so aparece depois (data de expedicao do RG).
SNAPSHOT_DEPOIS = {"controls": [
    dict(c, required=True) if c["label"] == "Estado civil" else dict(c)
    for c in SNAPSHOT["controls"]] + [_ctrl("Data de expedição", required=True, index=99)]}


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
    def __init__(self, snapshot=SNAPSHOT):
        self.visitas = []
        self.url = ""
        self.snapshot = snapshot
        self.enviado = False
        self.apagados = []
        self.mensagens = ["Estado civil: Campo obrigatório", "E-mail*: campo obrigatório"]

    def evaluate(self, script, *args):
        if script == mapeamento.JS_ESVAZIAR:
            self.apagados = list(args[0])
            return len(args[0])
        if script == adaptive_form.JS_MESSAGES:
            return self.mensagens if self.enviado else []
        return SNAPSHOT_DEPOIS if self.enviado else self.snapshot

    def goto(self, url, **kwargs):
        self.visitas.append(url)
        self.url = url

    def wait_for_timeout(self, ms):
        pass

    def screenshot(self, path, full_page=True):
        Path(path).write_bytes(b"png")


class PageBrowser(SimulatedBrowser):
    """recusa=True: o PJe devolve o formulario no envio (e-mail vazio); False: passa para o termo."""

    def __init__(self, record, scenario="success", recusa=True, snapshot=SNAPSHOT):
        super().__init__(record, scenario)
        self.page = FakePage(snapshot)
        self.clicks = []
        self.recusa = recusa

    def click(self, group):
        self.clicks.append(group)
        if group == "confirm_data" and self.recusa:
            self.page.enviado = True
            return
        super().click(group)

    def fill(self, group, value):
        raise AssertionError("o mapeamento nao preenche nada")


def _fluxo(tmp_path, scenario, **kw):
    config = load_config(PROJECT / "config.example.toml")
    browser = PageBrowser(RECORD, scenario, **kw)
    info = CertificateInfo(RECORD.nome_esperado, RECORD.cpf_esperado, "AC",
                           datetime(2099, 1, 1, tzinfo=timezone.utc), "0" * 40)
    flow = PJeRegistrationFlow(config, RECORD, info, "x", browser, SimulatedSigner(browser), AuditLog(tmp_path),
                               code_provider=lambda m: "000000", mapear=True)
    return flow.run(), browser


def test_envio_com_trava_mostra_o_que_o_pje_cobra(tmp_path, monkeypatch):
    monkeypatch.setattr(adaptive_form, "operate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("preencheu")))
    resultado, browser = _fluxo(tmp_path, "success")
    assert resultado.status == RunStatus.REHEARSAL_OK
    assert "envio com trava feito" in resultado.reason and "Nada foi gravado nem assinado" in resultado.reason
    # so o e-mail foi apagado (na tela), um clique em avancar e nenhuma assinatura
    assert browser.page.apagados == [2]
    assert browser.clicks == ["login_certificate_button", "confirm_data"]
    mapa = resultado.details["mapa"]
    por_rotulo = {c["rotulo"]: c for c in mapa["campos"]}
    civil = por_rotulo["Estado civil"]
    assert (civil["marcado_na_tela"], civil["aviso_ao_enviar"], civil["obrigatorio"]) == ("nao", "sim", "sim")
    data = por_rotulo["Data de expedição"]  # so apareceu depois do envio
    assert (data["campo"], data["aviso_ao_enviar"], data["obrigatorio"]) == ("rg_data", "sim", "sim")
    nome = por_rotulo["Nome"]
    assert (nome["aviso_ao_enviar"], nome["obrigatorio"]) == ("nao", "nao")
    assert mapa["avisos"] == ["ESTADO CIVIL: CAMPO OBRIGATORIO", "E-MAIL*: CAMPO OBRIGATORIO"]  # mascarados pelo fluxo


def test_formulario_que_passa_para_o_termo_para_sem_assinar(tmp_path):
    resultado, browser = _fluxo(tmp_path, "success", recusa=False)
    assert resultado.status == RunStatus.NEEDS_HUMAN and "termo de compromisso" in resultado.reason
    assert "sign_term" not in browser.clicks
    assert resultado.details["mapa"]["passou_para_termo"] is True
    estado = ResultadoCadastro("NEEDS_HUMAN", resultado.reason, mapa=resultado.details["mapa"])
    assert mapeamento.situacao(estado) == "PASSOU_PARA_O_TERMO" and mapeamento._parar_tudo(estado)


def test_sem_email_editavel_nao_clica(tmp_path):
    travado = {"controls": [dict(c, readonly=True) if c["label"] == "E-mail" else c for c in SNAPSHOT["controls"]]}
    resultado, browser = _fluxo(tmp_path, "success", snapshot=travado)
    assert resultado.status == RunStatus.REHEARSAL_OK and "sem o clique de envio" in resultado.reason
    assert "confirm_data" not in browser.clicks
    assert {c["aviso_ao_enviar"] for c in resultado.details["mapa"]["campos"]} == {"nao_testado"}


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


# ------------------------------------------------------------------ mapeamento assistido (eproc...)
def test_tabela_dos_outros_sistemas():
    opcoes = mapeamento.outros_sistemas()
    eproc = [o for o in opcoes if o["sistema"] == "eproc"]
    assert len(eproc) == 29 and all(o["url"].startswith("https://") for o in opcoes)
    tjsc = next(o for o in opcoes if o["tribunal"] == "tjsc-eproc-1g")
    assert tjsc["nome"] == "TJSC - eproc 1o grau" and "selecionar_tipo_advogado" in tjsc["url"]
    escolhidos, invalidos = mapeamento.escolher_outros(opcoes, "tjsc-eproc-1g, xyz")
    assert [o["tribunal"] for o in escolhidos] == ["tjsc-eproc-1g"] and invalidos == ["xyz"]
    assert len(mapeamento.escolher_outros(opcoes, "eproc")[0]) == 29


class NavegadorFalso:
    """O operador navega; o robo so abre e le. Depois do 'envio' pelo operador, a tela muda."""

    def __init__(self, config):
        self.config = config
        self.page = FakePage()
        self.avisos = []
        self.abertos = []
        self.fechado = False

    def abrir(self, url):
        self.abertos.append(url)

    def pagina(self):
        return self.page

    def fechar(self):
        self.fechado = True


def test_mapeamento_assistido_le_antes_e_depois_do_envio_do_operador(tmp_path, monkeypatch):
    from pje_rpa import lote

    linha = SimpleNamespace(ok=True, usa_windows=True, numero_linha=2, nome="ADV", record=RECORD, thumbprint="",
                            certificado=None, certificado_info="Windows", erros=[], segredo=lambda k: "")
    monkeypatch.setattr(lote, "_ler_advogados", lambda planilha, deps: ([linha], None))
    navegadores = []

    def fabrica(config):
        nav = NavegadorFalso(config)
        navegadores.append(nav)
        return nav

    respostas = iter(["", ""])  # Enter com o formulario na tela; Enter depois de tentar o envio

    def perguntar(prompt):
        resposta = next(respostas)
        if "tentei o envio" in prompt:  # o operador apagou o e-mail e clicou em Finalizar
            navegadores[-1].page.enviado = True
            navegadores[-1].avisos.append(f"Informe o e-mail {RECORD.email}")
        return resposta

    log = []
    deps = Dependencias(ask=perguntar, secret=lambda p: "", yes=lambda p, d=False: True, cadastrar=None,
                        credencial=None, abrir_caixa=None, entregar=None, log=log.append,
                        opcoes=Opcoes(perguntar_2fa=False))
    opcoes = [{"tribunal": "tjsc-eproc-1g", "nome": "TJSC - eproc 1o grau", "sistema": "eproc",
               "url": "https://eproc1g.tjsc.jus.br/eproc/externo_controlador.php?acao=selecionar_tipo_advogado_cadastro"}]
    codigo = mapeamento.run_mapear_tela("tjsc-eproc-1g", tabela=tmp_path / "f.csv", arquivo_situacao=tmp_path / "s.csv",
                                        deps=deps, navegador=fabrica, opcoes=opcoes)
    assert codigo == 0
    nav = navegadores[0]
    assert nav.abertos == [opcoes[0]["url"]] and nav.fechado and nav.page.apagados == []  # o robo nao apagou nada
    linhas = [l for l in mapeamento.ler_tabela(tmp_path / "f.csv") if l["tribunal"] == "tjsc-eproc-1g"]
    por_rotulo = {l["rotulo"]: l for l in linhas}
    assert por_rotulo["Estado civil"]["aviso_ao_enviar"] == "sim" and "Data de expedição" in por_rotulo
    situacao = {l["tribunal"]: l for l in mapeamento._ler(tmp_path / "s.csv")}["tjsc-eproc-1g"]
    assert situacao["situacao"] == "MAPEADO_ASSISTIDO" and RECORD.email not in situacao["avisos_do_pje"]
    assert "***" in situacao["avisos_do_pje"]


def test_operador_pode_pular_o_tribunal(tmp_path, monkeypatch):
    from pje_rpa import lote

    linha = SimpleNamespace(ok=True, usa_windows=True, numero_linha=2, nome="ADV", record=RECORD, thumbprint="",
                            certificado=None, certificado_info="Windows", erros=[], segredo=lambda k: "")
    monkeypatch.setattr(lote, "_ler_advogados", lambda planilha, deps: ([linha], None))
    deps = Dependencias(ask=lambda p: "p", secret=lambda p: "", yes=lambda p, d=False: True, cadastrar=None,
                        credencial=None, abrir_caixa=None, entregar=None, log=lambda m: None,
                        opcoes=Opcoes(perguntar_2fa=False))
    opcoes = [{"tribunal": "tjrs-eproc-1g", "nome": "TJRS - eproc 1o grau", "sistema": "eproc", "url": "https://x.jus.br"}]
    assert mapeamento.run_mapear_tela("tjrs-eproc-1g", tabela=tmp_path / "f.csv", arquivo_situacao=tmp_path / "s.csv",
                                      deps=deps, navegador=NavegadorFalso, opcoes=opcoes) == 3
    assert {l["tribunal"]: l["situacao"] for l in mapeamento._ler(tmp_path / "s.csv")} == {"tjrs-eproc-1g": "PULADO"}
