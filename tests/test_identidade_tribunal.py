"""Identidade do tribunal desacoplada do endereco de acesso (registro.py + enderecos.py).

Tudo com tribunais e enderecos FICTICIOS numa pasta temporaria: nada acessa a rede.
"""
import dataclasses
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from pje_rpa import enderecos, exigencias, graus, lote, registro
from pje_rpa.config import ConfigurationError, assert_live_ready, assert_rehearsal_ready, load_config
from pje_rpa.lote import executar_tribunais, listar_tribunais
from pje_rpa.planilha import ler_planilha

PROJECT = Path(__file__).resolve().parents[1]

# hosts ficticios (terminam em .jus.br para passar a regra de dominio oficial)
H_TRT_ANTIGO, H_TRT_NOVO = "pje.trabalho-ficticio.jus.br", "acesso.trabalho-ficticio-novo.jus.br"
H_TJ = "pje.justica-ficticia.jus.br"
REGISTRO = f'''
[[tribunal]]
id = "trtx"
nome = "TRTX - 1o grau"
ramo = "TRT"
sigla = "TRTX"
grau = "1G"
grupo_credencial = "TRTX"
endereco_padrao = "https://{H_TRT_ANTIGO}/primeiro/login.seam"
hosts_autorizados = ["{H_TRT_ANTIGO}", "{H_TRT_NOVO}"]

[[tribunal]]
id = "trtx-2g"
nome = "TRTX - 2o grau"
ramo = "TRT"
sigla = "TRTX"
grau = "2G"
grupo_credencial = "TRTX"
endereco_padrao = "https://{H_TRT_ANTIGO}/segundo/login.seam"
hosts_autorizados = ["{H_TRT_ANTIGO}", "{H_TRT_NOVO}"]

[[tribunal]]
id = "tjx"
nome = "TJX - 1o grau"
ramo = "TJ"
sigla = "TJX"
grau = "1G"
endereco_padrao = "https://{H_TJ}/1g/login.seam"
hosts_autorizados = ["{H_TJ}"]

[[tribunal]]
id = "tjx-2g"
nome = "TJX - 2o grau"
ramo = "TJ"
sigla = "TJX"
grau = "2G"
endereco_padrao = "https://{H_TJ}/2g/login.seam"
hosts_autorizados = ["{H_TJ}"]
'''
ENDERECOS = {"trtx": f"https://{H_TRT_ANTIGO}/primeiro/login.seam",
             "trtx-2g": f"https://{H_TRT_ANTIGO}/segundo/login.seam",
             "tjx": f"https://{H_TJ}/1g/login.seam", "tjx-2g": f"https://{H_TJ}/2g/login.seam"}


def _toml(pasta: Path, chave: str, url: str, liberado: bool = False, extra: str = "") -> Path:
    nome = {"trtx": "TRTX - 1o grau", "trtx-2g": "TRTX - 2o grau", "tjx": "TJX - 1o grau",
            "tjx-2g": "TJX - 2o grau"}.get(chave, chave.upper())
    flag = "true" if liberado else "false"
    caminho = pasta / f"{chave}.toml"
    caminho.write_text(f'herda = "_comum.toml"\n{extra}\n[app]\ntribunal = "{nome}"\nbase_url = "{url}"\n'
                       f"selectors_approved = {flag}\nallow_live_submission = {flag}\n", encoding="utf-8")
    return caminho


@pytest.fixture
def pasta(tmp_path):
    trib = tmp_path / "tribunais"
    trib.mkdir()
    shutil.copy(PROJECT / "tribunais" / "_comum.toml", trib / "_comum.toml")
    (trib / registro.ARQUIVO).write_text(REGISTRO, encoding="utf-8")
    for chave, url in ENDERECOS.items():
        _toml(trib, chave, url)
    return trib


def _id(cfg):
    return (cfg.tribunal_id, cfg.tribunal, cfg.grau, cfg.grupo_credencial, cfg.ramo)


RECORD = SimpleNamespace(nome_esperado="FULANA DE TAL", email="fulana@adv.com.br", telefone="",
                         rg_numero="", rg_orgao="")


# 1 ------------------------------------------------------------------------------------------
def test_1_identidade_constante_quando_o_endereco_muda(pasta):
    antes = load_config(pasta / "trtx.toml")
    _toml(pasta, "trtx", f"https://{H_TRT_NOVO}/novo-caminho/entrada.seam")
    depois = load_config(pasta / "trtx.toml")
    assert depois.base_url != antes.base_url
    assert _id(depois) == _id(antes) == ("trtx", "TRTX - 1o grau", "1G", "TRTX", "TRT")
    assert depois.identidade_origem == "registro"


# 2 ------------------------------------------------------------------------------------------
def test_2_duas_urls_ficticias_mesma_identidade(pasta, tmp_path):
    outra = tmp_path / "copia"
    shutil.copytree(pasta, outra)
    _toml(pasta, "tjx", f"https://{H_TJ}/1g/login.seam")
    _toml(outra, "tjx", f"https://{H_TJ}/portal/primeiro-grau/login.seam?origem=menu")
    a, b = load_config(pasta / "tjx.toml"), load_config(outra / "tjx.toml")
    assert a.base_url != b.base_url and _id(a) == _id(b)
    assert registro.identidade(a) == registro.identidade(b)


# 3 ------------------------------------------------------------------------------------------
def test_3_primeiro_e_segundo_grau_diferenciados(pasta):
    um, dois = load_config(pasta / "tjx.toml"), load_config(pasta / "tjx-2g.toml")
    assert (um.tribunal_id, um.grau) == ("tjx", "1G") and (dois.tribunal_id, dois.grau) == ("tjx-2g", "2G")
    assert um.grupo_credencial == dois.grupo_credencial == ""   # TJ: cada grau e independente
    opcoes = listar_tribunais(pasta)
    porid = {o.chave: o for o in opcoes}
    final, incluidos, _ = graus.completar([porid["tjx"]], opcoes)
    assert [o.chave for o in final] == ["tjx"] and incluidos == []   # pedir o 1G nao traz o 2G


# 4 ------------------------------------------------------------------------------------------
def test_4_regras_do_trt_independem_do_endereco(pasta):
    # endereco que o padrao antigo (pje.trtN.jus.br/primeirograu) nao reconheceria
    _toml(pasta, "trtx", f"https://{H_TRT_NOVO}/qualquer/login.seam")
    trt = load_config(pasta / "trtx.toml")
    assert graus.grupo(trt) == "TRTX" and graus.grau(trt) == "1G"
    assert exigencias.faltando(trt, RECORD, tabela=pasta / "nao-existe.csv") == [
        "telefone (celular com DDD)", "numero do RG", "orgao emissor do RG"]
    # e o contrario: um TJ num host com cara de TRT nao vira TRT
    reg = (pasta / registro.ARQUIVO).read_text(encoding="utf-8").replace(
        f'hosts_autorizados = ["{H_TJ}"]\n\n[[tribunal]]\nid = "tjx-2g"',
        f'hosts_autorizados = ["{H_TJ}", "pje.trt99.jus.br"]\n\n[[tribunal]]\nid = "tjx-2g"')
    (pasta / registro.ARQUIVO).write_text(reg, encoding="utf-8")
    _toml(pasta, "tjx", "https://pje.trt99.jus.br/primeirograu/login.seam")
    tj = load_config(pasta / "tjx.toml")
    assert graus.grupo(tj) == "" and exigencias.faltando(tj, RECORD, tabela=pasta / "nao-existe.csv") == []
    # opcoes: o par do TRT continua completo com os enderecos trocados
    opcoes = listar_tribunais(pasta)
    porid = {o.chave: o for o in opcoes}
    final, incluidos, incompletos = graus.completar([porid["trtx-2g"]], opcoes)
    assert [o.chave for o in final] == ["trtx", "trtx-2g"] and incompletos == []


# 5 ------------------------------------------------------------------------------------------
def test_5_registros_antigos_com_endereco_sao_reconhecidos_e_os_novos_levam_o_id(pasta, monkeypatch):
    from pje_rpa.entrega import COLUNAS, LinhaCredencial

    monkeypatch.chdir(pasta.parent)
    # planilha antiga (so o endereco de referencia) -> identidade pelo registro, sem padrao de URL
    with pytest.warns(DeprecationWarning):
        antiga = registro.identidade(ENDERECOS["trtx-2g"])
    assert (antiga.tribunal_id, antiga.grupo_credencial) == ("trtx-2g", "TRTX")
    with pytest.warns(DeprecationWarning):
        assert registro.identidade(f"https://{H_TRT_NOVO}/x").tribunal_id == ""   # nao adivinha
    assert registro.identidade("tjx-2g").grau == "2G"                                  # pelo id
    linha = LinhaCredencial("TJX - 2o grau", "https://qualquer.jus.br/", "A", "1", "a@b.c", tribunal_id="tjx-2g")
    assert linha.valores()[COLUNAS.index("ID do tribunal")] == "tjx-2g"


# 6 ------------------------------------------------------------------------------------------
def test_6_historico_de_credenciais_consistente_com_endereco_trocado(pasta, tmp_path):
    from test_graus import Fake, _planilha

    for chave in ("trtx", "trtx-2g"):
        _toml(pasta, chave, f"https://{H_TRT_NOVO}/{chave}/login.seam", liberado=True)
    configs = [load_config(pasta / "trtx.toml"), load_config(pasta / "trtx-2g.toml")]
    fake = Fake()
    por = executar_tribunais(configs, ler_planilha(_planilha(tmp_path)), tmp_path / "audit", tmp_path / "out",
                             fake.deps(), rehearsal=False, nova_senha_se_ja_cadastrado=True)
    # a credencial unica do grupo continua valendo: o 2o grau repete a senha do 1o
    assert fake.pedidos == [("TRTX - 1o grau", "52998224725", ""), ("TRTX - 2o grau", "52998224725", "senha-1")]
    assert [r.credencial.senha for rs in por.values() for r in rs] == ["senha-1", "senha-1"]


# 7 ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("estrago, trecho", [
    (lambda r: r + '\n[[tribunal]]\nid = "tjx"\nnome = "dup"\nramo = "TJ"\ngrau = "1G"\n'
     'hosts_autorizados = ["pje.dup.jus.br"]\n', "id repetido"),
    (lambda r: r.replace('grau = "2G"\ngrupo_credencial = "TRTX"', 'grau = "1G"\ngrupo_credencial = "TRTX"'),
     "precisa de um 1G e um 2G"),
    (lambda r: r.replace('id = "tjx"\nnome', 'id = "tjx"\ngrua = "1G"\nnome'), "campo(s) desconhecido(s) grua"),
    (lambda r: r.replace(f'hosts_autorizados = ["{H_TJ}"]', 'hosts_autorizados = ["sso.cloud.pje.jus.br"]', 1),
     "login unificado"),
    (lambda r: r.replace('grau = "1G"\nendereco_padrao', 'grau = "3G"\nendereco_padrao', 1), "grau '3G' invalido"),
])
def test_7_registro_invalido_e_recusado_explicitamente(pasta, estrago, trecho):
    caminho = pasta / registro.ARQUIVO
    caminho.write_text(estrago(caminho.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(ConfigurationError, match=trecho.replace("(", r"\(").replace(")", r"\)")):
        load_config(pasta / "tjx.toml")
    erros = []
    assert listar_tribunais(pasta, erros) == [] and erros and all(trecho in e for e in erros)


def test_7_configs_invalidos_aparecem_no_diagnostico(pasta):
    _toml(pasta, "tjnovo", f"https://{H_TJ}/novo/login.seam")                         # fora do registro
    _toml(pasta, "tjx-2g", ENDERECOS["tjx-2g"], extra='[tribunal]\ngrau = "1G"\n')      # diverge do registro
    _toml(pasta, "tjy", f"https://{H_TJ}/y", extra='[tribunal]\nid = "outro"\n')        # id != arquivo
    (pasta / "quebrado.toml").write_text("isto nao e toml = = =", encoding="utf-8")
    erros = []
    assert sorted(o.chave for o in listar_tribunais(pasta, erros)) == ["tjx", "trtx", "trtx-2g"]
    texto = "\n".join(erros)
    assert "tjnovo.toml: ConfigurationError" in texto and "nao esta no registro" in texto
    assert "diverge do registro" in texto and "tem de ser igual ao nome do arquivo" in texto
    assert "quebrado.toml: TOMLDecodeError" in texto
    log = []
    lote.avisar_problemas([], erros, log.append)
    assert len(log) == 4 and all(linha.startswith("ATENCAO: tribunal ignorado") for linha in log)


def test_7_secao_tribunal_fora_do_registro_vale_e_nao_e_herdada(pasta):
    extra = (f'[tribunal]\nnome = "TJNOVO"\nramo = "TJ"\ngrau = "1G"\nhosts_autorizados = ["{H_TJ}"]\n')
    _toml(pasta, "tjnovo", f"https://{H_TJ}/novo/login.seam", extra=extra)
    cfg = load_config(pasta / "tjnovo.toml")
    assert (cfg.tribunal_id, cfg.grau, cfg.identidade_origem) == ("tjnovo", "1G", "arquivo")
    filho = pasta / "tjnovo-2g.toml"
    filho.write_text('herda = "tjnovo.toml"\n[app]\ntribunal = "TJNOVO 2G"\n'
                     f'base_url = "https://{H_TJ}/novo2/login.seam"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="nao esta no registro"):
        load_config(filho)   # a identidade do pai NAO passa para o filho


# 8 ------------------------------------------------------------------------------------------
def test_8_endereco_alterado_nao_libera_o_envio_real_sozinho(pasta, monkeypatch):
    record = SimpleNamespace(nome_esperado="FULANA DE TAL", email="fulana@adv.com.br")
    _toml(pasta, "tjx", ENDERECOS["tjx"], liberado=True)
    assert_live_ready(load_config(pasta / "tjx.toml"), record)          # referencia: liberado
    _toml(pasta, "tjx", f"https://{H_TJ}/entrada-nova/login.seam", liberado=True)
    cfg = load_config(pasta / "tjx.toml")
    assert_rehearsal_ready(cfg, record)                                  # ensaio continua possivel
    with pytest.raises(ConfigurationError, match="ainda nao foi aprovado"):
        assert_live_ready(cfg, record)
    opcao = {o.chave: o for o in listar_tribunais(pasta)}["tjx"]
    assert not opcao.liberado and "enderecos --aprovar tjx" in opcao.pendencia
    # aprovacao explicita do operador: grava no .toml e libera so ESTE endereco
    assert enderecos.aprovar(pasta, "tjx", lambda p: False, log=lambda *_: None,
                             trilha=pasta / "t.jsonl") == 1
    assert enderecos.aprovar(pasta, "tjx", lambda p: True, log=lambda *_: None, trilha=pasta / "t.jsonl") == 0
    assert_live_ready(load_config(pasta / "tjx.toml"), record)
    assert "tjx" in (pasta / "t.jsonl").read_text(encoding="utf-8")
    # mudou de novo: bloqueia de novo
    texto = (pasta / "tjx.toml").read_text(encoding="utf-8").replace(
        'base_url = "https://' + H_TJ + '/entrada-nova/', 'base_url = "https://' + H_TJ + '/outra/')
    assert 'endereco_aprovado = "https://' + H_TJ + '/entrada-nova/login.seam"' in texto
    (pasta / "tjx.toml").write_text(texto, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="ainda nao foi aprovado"):
        assert_live_ready(load_config(pasta / "tjx.toml"), record)


@pytest.mark.parametrize("url, trecho", [
    ("https://host-nao-autorizado.jus.br/pje/login.seam", "nao esta entre os autorizados"),
    ("https://sso.cloud.pje.jus.br/auth/realms/pje/protocol/openid-connect/auth?response_type=code"
     "&client_id=pje-tjx-1g&redirect_uri=x&state=abc&login=true&scope=openid", "login unificado"),
    (f"https://{H_TJ}/1g/login.seam?state=abc&session_state=def", "parametros de uma sessao"),
    (f"https://usuario:senha@{H_TJ}/1g/login.seam", "usuario ou senha"),
    (f"https://{H_TJ}:8443/1g/login.seam", "porta 8443"),
    (f"http://{H_TJ}/1g/login.seam", "https"),
])
def test_8_endereco_recusado_bloqueia_ensaio_e_real_sem_mudar_a_identidade(pasta, url, trecho):
    record = SimpleNamespace(nome_esperado="FULANA DE TAL", email="fulana@adv.com.br")
    _toml(pasta, "tjx", url, liberado=True)
    try:
        cfg = load_config(pasta / "tjx.toml")
    except ConfigurationError:
        assert url.startswith("http://")      # load_config ja recusa o que nao e https
        return
    assert (cfg.tribunal_id, cfg.grau) == ("tjx", "1G")     # identidade intacta
    for checagem in (assert_rehearsal_ready, assert_live_ready):
        with pytest.raises(ConfigurationError, match=trecho):
            checagem(cfg, record)
    assert enderecos.aprovar(pasta, "tjx", lambda p: True, log=lambda *_: None, trilha=pasta / "t.jsonl") == 6
    opcao = {o.chave: o for o in listar_tribunais(pasta)}["tjx"]
    assert opcao.problemas and not opcao.liberado
    # a mensagem nunca repete os valores da consulta (state/sessao)
    assert "abc" not in " ".join(opcao.problemas) and "def" not in " ".join(opcao.problemas)


def test_8_tribunal_com_endereco_recusado_nao_e_executado(pasta, monkeypatch):
    from test_graus import Fake, _planilha

    _toml(pasta, "tjx", f"https://{H_TJ}/x?state=1", liberado=True)
    _toml(pasta, "tjx-2g", ENDERECOS["tjx-2g"], liberado=True)
    original = lote.listar_tribunais
    monkeypatch.setattr(lote, "listar_tribunais", lambda p=pasta, erros=None: original(pasta, erros))
    fake = Fake(respostas=[str(_planilha(pasta.parent))])
    lote.run_executar(pasta.parent / "audit", pasta.parent / "out", tribunal="tjx, tjx-2g", ensaio=False,
                      deps=fake.deps())
    texto = "\n".join(fake.log)
    assert "TJX - 1o grau ficou de fora: endereco de login recusado" in texto
    assert [t for t, _, _ in fake.pedidos] == ["TJX - 2o grau"]


# 9 ------------------------------------------------------------------------------------------
def test_9_controles_de_seguranca_continuam_ativos(pasta):
    record = SimpleNamespace(nome_esperado="FULANA DE TAL", email="fulana@adv.com.br")
    cfg = load_config(pasta / "tjx.toml")
    # envio real continua exigindo producao + seletores homologados + envio liberado
    for campo, valor, trecho in (("environment", "simulation", "production"),
                                 ("selectors_approved", False, "homologados"),
                                 ("allow_live_submission", False, "bloqueado")):
        liberado = dataclasses.replace(cfg, selectors_approved=True, allow_live_submission=True)
        with pytest.raises(ConfigurationError, match=trecho):
            assert_live_ready(dataclasses.replace(liberado, **{campo: valor}), record)
    # dados de exemplo, dominio nao oficial e endereco de exemplo continuam barrados
    with pytest.raises(ConfigurationError):
        assert_rehearsal_ready(cfg, SimpleNamespace(nome_esperado="TESTE", email="x@example.com"))
    with pytest.raises(ConfigurationError):
        assert_rehearsal_ready(dataclasses.replace(cfg, base_url="https://pje.tjx.com.br/login",
                                                   hosts_autorizados=("pje.tjx.com.br",)), record)
    # todo tribunal real continua com as paradas de CAPTCHA, certificado e bloqueio
    for caminho in sorted((PROJECT / "tribunais").glob("*.toml")):
        if caminho.name.startswith("_"):
            continue
        real = load_config(caminho)
        assert {"captcha", "certificate_error", "access_blocked"} <= real.stop_markers.keys()
        assert real.identidade_origem == "registro" and real.tribunal_id == caminho.stem
    # (o endereco de cada .toml da maquina e conferido por `python -m pje_rpa enderecos`: na VM um .toml
    # pode estar editado e e la que a recusa tem de aparecer, nao como falha de teste)


def test_registro_do_repositorio_e_a_fonte_unica():
    """O registro carrega (cada endereco_padrao passa na validacao dos enderecos), cobre todo .toml e
    junta exatamente os dois graus de cada TRT."""
    reg = registro.carregar(PROJECT / "tribunais")
    arquivos = {p.stem for p in (PROJECT / "tribunais").glob("*.toml") if not p.name.startswith("_")}
    assert set(reg) == arquivos
    grupos = {}
    for t in reg.values():
        if t.grupo_credencial:
            grupos.setdefault(t.grupo_credencial, set()).add(t.grau)
        assert (t.ramo == "TRT") == bool(t.grupo_credencial), t.id
    assert sorted(grupos, key=lambda g: int(g[3:])) == [f"TRT{n}" for n in range(1, 25)]
    assert all(g == {"1G", "2G"} for g in grupos.values())


def test_entrega_leva_id_e_grau_e_texto_com_igual_nao_vira_formula(tmp_path):
    from pje_rpa.entrega import COLUNAS, LinhaCredencial, abrir_planilha, salvar_planilha

    linha = LinhaCredencial("TJX - 2o grau", "https://x.jus.br/", "=HYPERLINK(\"http://mal\")", "1", "a@b.c",
                            tribunal_id="tjx-2g", grau="2G", observacao="=1+1")
    caminho = salvar_planilha([linha], tmp_path / "e.xlsx", "senha-arquivo")
    ws = abrir_planilha(caminho, "senha-arquivo").active
    valores = {COLUNAS[i]: c for i, c in enumerate(ws[2])}
    assert valores["ID do tribunal"].value == "tjx-2g" and valores["Grau"].value == "2G"
    for coluna in ("Advogado", "Observacao"):
        assert valores[coluna].data_type == "s" and valores[coluna].value.startswith("=")


def test_grau_no_toml_divergente_e_recusado_e_domain_vira_aviso(pasta):
    _toml(pasta, "tjx", ENDERECOS["tjx"])
    texto = (pasta / "tjx.toml").read_text(encoding="utf-8").replace("[app]\n", '[app]\ngrau = "2G"\n')
    (pasta / "tjx.toml").write_text(texto, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="app.grau"):
        load_config(pasta / "tjx.toml")
    texto = texto.replace('grau = "2G"', 'grau = "1G"\ndomain = "TJX-PJE-1G"')
    (pasta / "tjx.toml").write_text(texto, encoding="utf-8")
    cfg = load_config(pasta / "tjx.toml")
    assert cfg.grau == "1G" and any("app.domain e ignorado" in a for a in cfg.avisos)
