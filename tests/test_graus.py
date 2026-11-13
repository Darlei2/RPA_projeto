"""Tribunais com um cadastro por grau e uma credencial so (PJe-JT): os dois graus juntos e a mesma senha."""
import shutil
from pathlib import Path

import pytest

from pje_rpa import graus, lote
from pje_rpa.config import load_config
from pje_rpa.entrega import LinhaCredencial
from pje_rpa.execucao import ResultadoCadastro
from pje_rpa.lote import Dependencias, OpcaoTribunal, executar_tribunais, listar_tribunais, run_executar
from pje_rpa.planilha import ler_planilha

PROJECT = Path(__file__).resolve().parents[1]
CABECALHO = ["Nome completo", "CPF", "OAB", "UF da OAB", "E-mail", "Celular", "CEP", "Endereco",
             "Numero", "Complemento", "Bairro", "Municipio", "UF", "Autorizacao", "Certificado",
             "Senha do certificado", "Executar"]


def _planilha(pasta: Path, advogados=(("ADVOGADA DE TESTE", "52998224725"),)) -> Path:
    from openpyxl import Workbook

    (pasta / "adv.pfx").write_bytes(b"pfx-falso")
    wb = Workbook()
    ws = wb.active
    ws.append(CABECALHO)
    for nome, cpf in advogados:
        ws.append([nome, cpf, "12345", "SP", "adv@exemplo.com.br", "11999999999", "01001000", "RUA A", "10", "",
                   "CENTRO", "SAO PAULO", "SP", "CONTRATO-1", "adv.pfx", "", ""])
    wb.save(pasta / "a.xlsx")
    return pasta / "a.xlsx"


class Fake:
    """Credencial falsa: usa a senha preferida quando recebe uma; senao cria 'senha-N'."""

    def __init__(self, falha_em=(), recusa_preferida_em=(), respostas=()):
        self.falha_em = set(falha_em)                      # tribunais em que o login de teste falha
        self.recusa_preferida_em = set(recusa_preferida_em)  # tribunais que nao aceitam a senha repetida
        self.respostas = list(respostas)
        self.log, self.pedidos, self.cadastros, self.entregue = [], [], [], None
        self.n = 0

    def deps(self):
        def cadastrar(config, record, pfx, pwd, code_provider, audit_root, **kw):
            self.cadastros.append((config.tribunal, kw.get("senha_preferida", "")))
            return ResultadoCadastro("REHEARSAL_OK" if kw.get("rehearsal") else "COMPLETED", "ok", "run")

        def credencial(config, audit_root, lawyer, inbox, secret_2fa):
            preferida = lawyer.get("senha_preferida", "")
            self.pedidos.append((config.tribunal, lawyer["cpf"], preferida))
            linha = LinhaCredencial(config.tribunal, config.base_url, lawyer["nome"], lawyer["cpf"], lawyer["email"])
            if config.tribunal in self.falha_em:
                linha.observacao = "login falhou"
                return linha
            if preferida and config.tribunal not in self.recusa_preferida_em:
                linha.senha = preferida
            else:
                self.n += 1
                linha.senha = f"senha-{self.n}"
            linha.senha_cadastrada = linha.login_testado = True
            return linha

        return Dependencias(
            ask=lambda p: self.respostas.pop(0) if self.respostas else "",
            secret=lambda p: "senha-a1", yes=lambda p, d=False: True,
            cadastrar=cadastrar, credencial=credencial, abrir_caixa=lambda *a, **k: None,
            entregar=lambda linhas, saida: setattr(self, "entregue", list(linhas)),
            store_factory=lambda: type("S", (), {"remove": lambda self, t: None})(),
            log=self.log.append,
            # rota pelo e-mail: a senha sai do passo "credencial" (como no uso com -ViaEmail)
            opcoes=lote.Opcoes(perguntar_2fa=False, senha_via_email=True),
        )


def _cfg(nome: str):
    return load_config(PROJECT / "tribunais" / f"{nome}.toml")


# --------------------------------------------------------------------- regra
def test_grupo_e_grau_pelo_endereco():
    assert graus.grupo("https://pje.trt5.jus.br/primeirograu/login.seam") == "TRT5"
    assert graus.grau("https://pje.trt5.jus.br/primeirograu/login.seam") == "1G"
    assert graus.grupo("https://pje.trt15.jus.br/segundograu/login.seam") == "TRT15"
    assert graus.grau("https://pje.trt15.jus.br/segundograu/login.seam") == "2G"
    # fora da regra: cada grau e uma credencial propria
    for url in ("https://pje1g.trf1.jus.br/pje/login.seam", "https://pje2g.trf1.jus.br/pje/login.seam",
                "https://pje1g-mg.tse.jus.br/pje/login.seam", "https://pje.tjap.jus.br/1g/login.seam", ""):
        assert graus.grupo(url) == "" and graus.grau(url) == ""


def test_todo_trt_tem_os_dois_graus_configurados(monkeypatch):
    monkeypatch.chdir(PROJECT)
    por_grupo: dict[str, set[str]] = {}
    for o in listar_tribunais():
        if graus.grupo(o.url):
            por_grupo.setdefault(graus.grupo(o.url), set()).add(graus.grau(o.url))
    assert sorted(por_grupo, key=lambda g: int(g[3:])) == [f"TRT{n}" for n in range(1, 25)]
    assert all(g == {"1G", "2G"} for g in por_grupo.values())
    # o 2o grau herda os seletores do 1o e nasce sem envio real liberado
    segundo = _cfg("trt2-2g")
    assert segundo.selectors == _cfg("trt2").selectors and segundo.tribunal == "TRT2 - 2o grau"
    assert not (segundo.selectors_approved or segundo.allow_live_submission)


def test_pedir_um_grau_traz_o_outro_na_ordem_certa(monkeypatch):
    monkeypatch.chdir(PROJECT)
    opcoes = listar_tribunais()
    chave = {o.chave: o for o in opcoes}
    final, incluidos, incompletos = graus.completar([chave["tremg"], chave["trt15"], chave["trt5-2g"]], opcoes)
    assert [o.chave for o in final] == ["tremg", "trt15", "trt15-2g", "trt5", "trt5-2g"]
    assert incluidos == ["TRT15 - 2o grau", "TRT5 - 1o grau"] and incompletos == []
    # pedir os dois nao duplica; tribunal fora da regra nao ganha par
    final, incluidos, _ = graus.completar([chave["trt5-2g"], chave["trt5"], chave["trf1"]], opcoes)
    assert [o.chave for o in final] == ["trt5", "trt5-2g", "trf1"] and incluidos == []


def test_grupo_sem_o_arquivo_do_outro_grau_fica_de_fora(tmp_path):
    so_um = OpcaoTribunal("trt5", tmp_path / "a.toml", "TRT5 - 1o grau", True,
                          "https://pje.trt5.jus.br/primeirograu/login.seam")
    outro = OpcaoTribunal("tjap", tmp_path / "b.toml", "TJAP - 1o grau", True, "https://pje.tjap.jus.br/1g/login.seam")
    final, incluidos, incompletos = graus.completar([so_um, outro], [so_um, outro])
    assert final == [outro] and incluidos == [] and incompletos == ["TRT5"]


def test_grupo_com_um_grau_so_liberado_sai_inteiro(tmp_path):
    a = OpcaoTribunal("trt5", tmp_path / "a.toml", "TRT5 - 1o grau", True, "https://pje.trt5.jus.br/primeirograu/login.seam")
    c = OpcaoTribunal("tjap", tmp_path / "c.toml", "TJAP - 1o grau", True, "https://pje.tjap.jus.br/1g/login.seam")
    assert graus.so_grupos_inteiros([a, c]) == ([c], ["TRT5"])


# ------------------------------------------------------------------ execucao
def test_segundo_grau_repete_a_senha_do_primeiro(tmp_path):
    linhas = ler_planilha(_planilha(tmp_path, [("ADVOGADA DE TESTE", "52998224725"), ("SEGUNDO ADVOGADO", "01234567890")]))
    fake = Fake()
    por = executar_tribunais([_cfg("trt5"), _cfg("trt5-2g")], linhas, tmp_path / "audit", tmp_path / "out",
                             fake.deps(), rehearsal=False, nova_senha_se_ja_cadastrado=True)
    # 1o grau: senha nova para cada advogado; 2o grau: a mesma senha de cada um
    assert fake.pedidos == [("TRT5 - 1o grau", "52998224725", ""), ("TRT5 - 1o grau", "01234567890", ""),
                            ("TRT5 - 2o grau", "52998224725", "senha-1"), ("TRT5 - 2o grau", "01234567890", "senha-2")]
    senhas = {(c.tribunal, c.cpf): c.senha for c in fake.entregue}
    assert senhas[("TRT5 - 1o grau", "52998224725")] == senhas[("TRT5 - 2o grau", "52998224725")] == "senha-1"
    assert senhas[("TRT5 - 1o grau", "01234567890")] == senhas[("TRT5 - 2o grau", "01234567890")] == "senha-2"
    assert all(r.login_ok for rs in por.values() for r in rs)
    assert "senha-1" not in "\n".join(fake.log)  # a senha nunca vai para o log


def test_primeiro_grau_sem_credencial_nao_faz_o_segundo(tmp_path):
    linhas = ler_planilha(_planilha(tmp_path, [("ADVOGADA DE TESTE", "52998224725"), ("SEGUNDO ADVOGADO", "01234567890")]))
    fake = Fake(falha_em={"TRT5 - 1o grau"})
    por = executar_tribunais([_cfg("trt5"), _cfg("trt5-2g")], linhas, tmp_path / "audit", tmp_path / "out",
                             fake.deps(), rehearsal=False, nova_senha_se_ja_cadastrado=True)
    assert [t for t, _, _ in fake.pedidos] == ["TRT5 - 1o grau"] * 2      # nada foi pedido ao 2o grau
    assert [t for t, _ in fake.cadastros] == ["TRT5 - 1o grau"] * 2       # nem login com certificado
    assert [r.cadastro for r in por["TRT5 - 2o grau"]] == ["PULADO", "PULADO"]
    assert "os dois graus do TRT5 precisam da mesma senha" in por["TRT5 - 2o grau"][0].motivo


def test_erro_inesperado_no_primeiro_grau_tambem_segura_o_segundo(tmp_path):
    linhas = ler_planilha(_planilha(tmp_path))
    fake = Fake()
    deps = fake.deps()
    original = deps.credencial

    def credencial(config, *a):
        if config.tribunal == "TRT5 - 1o grau":
            raise RuntimeError("navegador caiu")
        return original(config, *a)

    deps.credencial = credencial
    por = executar_tribunais([_cfg("trt5"), _cfg("trt5-2g")], linhas, tmp_path / "audit", tmp_path / "out",
                             deps, rehearsal=False, nova_senha_se_ja_cadastrado=True)
    assert por["TRT5 - 1o grau"][0].cadastro == "ERROR" and por["TRT5 - 2o grau"][0].cadastro == "PULADO"


def test_senha_diferente_no_segundo_grau_fica_marcada(tmp_path):
    linhas = ler_planilha(_planilha(tmp_path))
    fake = Fake(recusa_preferida_em={"TRT5 - 2o grau"})
    por = executar_tribunais([_cfg("trt5"), _cfg("trt5-2g")], linhas, tmp_path / "audit", tmp_path / "out",
                             fake.deps(), rehearsal=False, nova_senha_se_ja_cadastrado=True)
    segundo = por["TRT5 - 2o grau"][0]
    assert "senha DIFERENTE da do TRT5 - 1o grau" in segundo.credencial.observacao
    assert "senha DIFERENTE" in segundo.motivo and "ATENCAO" in "\n".join(fake.log)


def test_regra_nao_alcanca_outros_tribunais_nem_o_ensaio(tmp_path):
    linhas = ler_planilha(_planilha(tmp_path))
    fake = Fake()
    executar_tribunais([_cfg("trf1"), _cfg("trf1-2g")], linhas, tmp_path / "audit", tmp_path / "out",
                       fake.deps(), rehearsal=False, nova_senha_se_ja_cadastrado=True)
    assert [p for _, _, p in fake.pedidos] == ["", ""]  # TRF: uma senha (e uma credencial) por grau
    fake = Fake()
    por = executar_tribunais([_cfg("trt5"), _cfg("trt5-2g")], linhas, tmp_path / "audit", tmp_path / "out",
                             fake.deps(), rehearsal=True, nova_senha_se_ja_cadastrado=False)
    assert [r.cadastro for rs in por.values() for r in rs] == ["REHEARSAL_OK", "REHEARSAL_OK"]


def test_senha_pelo_login_unico_tambem_e_repetida(tmp_path):
    linhas = ler_planilha(_planilha(tmp_path))
    fake = Fake()
    deps = fake.deps()
    deps.opcoes.senha_via_email = False

    def cadastrar(config, record, pfx, pwd, code_provider, audit_root, **kw):
        fake.cadastros.append((config.tribunal, kw.get("senha_preferida", "")))
        return ResultadoCadastro("COMPLETED", "ok", "run", senha=kw.get("senha_preferida") or "senha-sso")

    def testar(config, audit_root, lawyer, secret_2fa, senha):
        linha = LinhaCredencial(config.tribunal, config.base_url, lawyer["nome"], lawyer["cpf"], lawyer["email"], senha=senha)
        linha.senha_cadastrada = linha.login_testado = True
        return linha

    deps.cadastrar, deps.testar_senha = cadastrar, testar
    executar_tribunais([_cfg("trt5"), _cfg("trt5-2g")], linhas, tmp_path / "audit", tmp_path / "out",
                       deps, rehearsal=False, nova_senha_se_ja_cadastrado=True)
    assert fake.cadastros == [("TRT5 - 1o grau", ""), ("TRT5 - 2o grau", "senha-sso")]
    assert [c.senha for c in fake.entregue] == ["senha-sso", "senha-sso"]


# ----------------------------------------------------------------- assistente
def _tribunais_de_teste(tmp_path, liberados):
    trib = tmp_path / "tribunais"
    trib.mkdir()
    for nome in ("_comum.toml", "trt5.toml", "trt5-2g.toml", "tjap.toml"):
        texto = (PROJECT / "tribunais" / nome).read_text(encoding="utf-8")
        if nome[:-5] in liberados:
            texto = texto.replace("selectors_approved = false", "selectors_approved = true") \
                         .replace("allow_live_submission = false", "allow_live_submission = true")
        (trib / nome).write_text(texto, encoding="utf-8")
    return trib


def test_assistente_inclui_o_outro_grau_e_roda_os_dois(tmp_path, monkeypatch):
    trib = _tribunais_de_teste(tmp_path, {"trt5", "trt5-2g"})
    original = lote.listar_tribunais
    monkeypatch.setattr(lote, "listar_tribunais", lambda pasta=trib: original(trib))
    fake = Fake(respostas=[str(_planilha(tmp_path))])
    assert run_executar(tmp_path / "audit", tmp_path / "out", tribunal="trt5", ensaio=False, deps=fake.deps()) == 0
    texto = "\n".join(fake.log)
    assert "Regra dos dois graus: incluido TRT5 - 2o grau" in texto
    assert "TRIBUNAL 1/2: TRT5 - 1o grau" in texto and "TRIBUNAL 2/2: TRT5 - 2o grau" in texto
    assert [c.senha for c in fake.entregue] == ["senha-1", "senha-1"]


def test_real_com_um_grau_so_liberado_nao_faz_nenhum_dos_dois(tmp_path, monkeypatch):
    trib = _tribunais_de_teste(tmp_path, {"trt5", "tjap"})  # 2o grau do TRT5 ainda sem envio real
    original = lote.listar_tribunais
    monkeypatch.setattr(lote, "listar_tribunais", lambda pasta=trib: original(trib))
    fake = Fake(respostas=[str(_planilha(tmp_path))])
    run_executar(tmp_path / "audit", tmp_path / "out", tribunal="trt5, tjap", ensaio=False, deps=fake.deps())
    texto = "\n".join(fake.log)
    assert "Envio real NAO liberado em: TRT5 - 2o grau" in texto
    assert "TRT5 ficou de fora, so um dos graus tem envio real liberado" in texto
    assert [t for t, _, _ in fake.pedidos] == ["TJAP - 1o grau"]
    # so o TRT5 pedido: nada e executado
    fake = Fake(respostas=[str(_planilha(tmp_path))])
    assert run_executar(tmp_path / "audit", tmp_path / "out", tribunal="trt5", ensaio=False, deps=fake.deps()) == 2
    assert fake.pedidos == [] and fake.cadastros == []


# ------------------------------------------------------- passo real da senha
def test_passo_da_senha_usa_so_a_senha_preferida(tmp_path, monkeypatch):
    """No outro grau, o link de senha recebe a senha do primeiro grau e nenhuma reserva gerada."""
    from types import SimpleNamespace

    from pje_rpa import browser as browser_mod
    from pje_rpa import senha_cli
    from pje_rpa.credencial import StepResult

    class FakeBrowser:
        def __init__(self, config):
            pass

        def close(self):
            pass

    class FakeInbox:
        def known_links(self):
            return frozenset()

        def wait_for_password_link(self, since, timeout_s, on_wait=None, exclude=frozenset()):
            return "https://pje.trt5.jus.br/segundograu/Senha/ativacaoSenha.seam?codigo=x"

    recebidas = []

    def fake_set(browser, config, link, *senhas):
        recebidas.append(senhas)
        return StepResult(True, "Senha cadastrada", senhas[0])

    monkeypatch.setattr(browser_mod, "PlaywrightBrowser", FakeBrowser)
    monkeypatch.setattr(senha_cli, "request_password_link", lambda *a: StepResult(True, "enviado"))
    monkeypatch.setattr(senha_cli, "set_password", fake_set)
    monkeypatch.setattr(senha_cli, "test_login", lambda b, c, cpf, senha, code: StepResult(True, "login"))
    config = SimpleNamespace(tribunal="TRT5 - 2o grau", base_url="https://pje.trt5.jus.br/segundograu/login.seam")
    lawyer = {"nome": "Fulano de Tal", "cpf": "01234567890", "email": "adv@gmail.com", "chave_2fa_exibir": "X",
              "senha_preferida": "SenhaDoPrimeiroGrau1"}
    linha = senha_cli._one_tribunal(config, tmp_path / "audit", lawyer, FakeInbox(), "JBSWY3DPEHPK3PXP", pause=False)
    assert recebidas == [("SenhaDoPrimeiroGrau1", "")]
    assert linha.senha == "SenhaDoPrimeiroGrau1" and linha.login_testado
    # sem senha preferida: comportamento de sempre (duas senhas novas, diferentes)
    recebidas.clear()
    lawyer.pop("senha_preferida")
    senha_cli._one_tribunal(config, tmp_path / "audit", lawyer, FakeInbox(), "JBSWY3DPEHPK3PXP", pause=False)
    assert len(recebidas[0]) == 2 and all(recebidas[0]) and recebidas[0][0] != recebidas[0][1]


# ------------------------------------------------- advogado que ja tem credencial
def _com_senha_existente(fake, senha="senha-do-keymaker", entra_em=("TRT5 - 1o grau",)):
    """Deps em que o advogado ja tem credencial com `senha`; o login com ela so entra em `entra_em`."""
    deps = fake.deps()
    testes = []

    def testar(config, audit_root, lawyer, secret_2fa, tentada):
        testes.append((config.tribunal, tentada))
        linha = LinhaCredencial(config.tribunal, config.base_url, lawyer["nome"], lawyer["cpf"], lawyer["email"], senha=tentada)
        linha.senha_cadastrada = True
        linha.login_testado = config.tribunal in entra_em and tentada == senha
        if not linha.login_testado:
            linha.observacao = "Usuario nao cadastrado"
        return linha

    deps.testar_senha, deps.senha_existente = testar, lambda config, linha: senha
    return deps, testes


def test_credencial_existente_completa_so_o_grau_que_falta(tmp_path):
    """O caso do TRT5: 1o grau ja funciona; o robo so cadastra o 2o grau, com a MESMA senha."""
    linhas = ler_planilha(_planilha(tmp_path))
    fake = Fake()
    deps, testes = _com_senha_existente(fake)
    por = executar_tribunais([_cfg("trt5"), _cfg("trt5-2g")], linhas, tmp_path / "audit", tmp_path / "out",
                             deps, rehearsal=False, nova_senha_se_ja_cadastrado=True)
    assert testes == [("TRT5 - 1o grau", "senha-do-keymaker"), ("TRT5 - 2o grau", "senha-do-keymaker")]
    # 1o grau: nenhum login com certificado e nenhum pedido de senha; 2o grau: cadastro + a mesma senha
    assert [t for t, _ in fake.cadastros] == ["TRT5 - 2o grau"]
    assert fake.pedidos == [("TRT5 - 2o grau", "52998224725", "senha-do-keymaker")]
    assert [r.cadastro for rs in por.values() for r in rs] == ["JA_TEM_ACESSO", "COMPLETED"]
    assert [c.senha for c in fake.entregue] == ["senha-do-keymaker", "senha-do-keymaker"]
    assert "senha-do-keymaker" not in "\n".join(fake.log)


def test_credencial_existente_que_ja_entra_nos_dois_graus_nao_altera_nada(tmp_path):
    linhas = ler_planilha(_planilha(tmp_path))
    fake = Fake()
    deps, testes = _com_senha_existente(fake, entra_em=("TRT5 - 1o grau", "TRT5 - 2o grau"))
    por = executar_tribunais([_cfg("trt5"), _cfg("trt5-2g")], linhas, tmp_path / "audit", tmp_path / "out",
                             deps, rehearsal=False, nova_senha_se_ja_cadastrado=True)
    assert fake.cadastros == [] and fake.pedidos == [] and len(testes) == 2
    assert [r.cadastro for rs in por.values() for r in rs] == ["JA_TEM_ACESSO", "JA_TEM_ACESSO"]


def test_senha_existente_que_nao_entra_no_primeiro_grau_nao_altera_nada(tmp_path):
    """Sem confirmar a senha em nenhum grau, o robo nao troca senha nem cadastra: nao quebra a credencial."""
    linhas = ler_planilha(_planilha(tmp_path))
    fake = Fake()
    deps, testes = _com_senha_existente(fake, entra_em=())
    por = executar_tribunais([_cfg("trt5"), _cfg("trt5-2g")], linhas, tmp_path / "audit", tmp_path / "out",
                             deps, rehearsal=False, nova_senha_se_ja_cadastrado=True)
    assert testes == [("TRT5 - 1o grau", "senha-do-keymaker")]          # o 2o grau nem e tentado
    assert fake.cadastros == [] and fake.pedidos == [] and not fake.entregue
    primeiro, segundo = por["TRT5 - 1o grau"][0], por["TRT5 - 2o grau"][0]
    assert primeiro.cadastro == "SENHA_NAO_CONFERE" and "Nada foi alterado" in primeiro.motivo
    assert primeiro.credencial is None and segundo.cadastro == "PULADO"


def test_senha_existente_fora_da_regra_dos_dois_graus_so_confere(tmp_path):
    linhas = ler_planilha(_planilha(tmp_path))
    fake = Fake()
    deps, testes = _com_senha_existente(fake, entra_em=())
    por = executar_tribunais([_cfg("tjap")], linhas, tmp_path / "audit", tmp_path / "out",
                             deps, rehearsal=False, nova_senha_se_ja_cadastrado=True)
    assert fake.cadastros == [] and fake.pedidos == []
    assert por["TJAP - 1o grau"][0].cadastro == "SENHA_NAO_CONFERE"
    # e o ensaio nao consulta a senha existente
    fake = Fake()
    deps, testes = _com_senha_existente(fake)
    executar_tribunais([_cfg("trt5")], linhas, tmp_path / "audit", tmp_path / "out",
                       deps, rehearsal=True, nova_senha_se_ja_cadastrado=False)
    assert testes == []


# ------------------------------------------------------ o que cada tribunal exige
def test_trt_exige_telefone_e_os_outros_nao():
    from types import SimpleNamespace

    from pje_rpa import exigencias

    sem, com = SimpleNamespace(telefone=""), SimpleNamespace(telefone="71999998888")
    for url in ("https://pje.trt5.jus.br/primeirograu/login.seam", "https://pje.trt15.jus.br/segundograu/login.seam"):
        assert exigencias.faltando(url, sem) == ["telefone (celular com DDD)"] and exigencias.faltando(url, com) == []
    for url in ("https://pje1g-mg.tse.jus.br/pje/login.seam", "https://pje1g.trf1.jus.br/pje/login.seam",
                "https://pje.tjap.jus.br/1g/login.seam", ""):
        assert exigencias.faltando(url, sem) == []


def _sem_telefone(tmp_path):
    import dataclasses

    linhas = ler_planilha(_planilha(tmp_path))
    linhas[0].record = dataclasses.replace(linhas[0].record, telefone="")
    return linhas


def test_linha_sem_telefone_e_pulada_no_trt_antes_de_qualquer_acesso(tmp_path):
    fake = Fake()
    deps, testes = _com_senha_existente(fake)
    consultas = []
    deps.ja_feito = lambda config, linha: consultas.append(config.tribunal) or ""
    por = executar_tribunais([_cfg("trt5"), _cfg("trt5-2g")], _sem_telefone(tmp_path), tmp_path / "audit",
                             tmp_path / "out", deps, rehearsal=False, nova_senha_se_ja_cadastrado=True)
    assert [r.cadastro for rs in por.values() for r in rs] == ["PULADO", "PULADO"]
    assert "exige telefone (celular com DDD)" in por["TRT5 - 2o grau"][0].motivo
    assert fake.cadastros == [] and fake.pedidos == [] and testes == [] and consultas == []
    # no ensaio tambem: o formulario do TRT pediria o telefone do mesmo jeito
    fake = Fake()
    por = executar_tribunais([_cfg("trt5")], _sem_telefone(tmp_path), tmp_path / "audit", tmp_path / "out",
                             fake.deps(), rehearsal=True, nova_senha_se_ja_cadastrado=False)
    assert por["TRT5 - 1o grau"][0].cadastro == "PULADO" and fake.cadastros == []


def test_linha_sem_telefone_segue_normal_fora_dos_trts(tmp_path):
    fake = Fake()
    por = executar_tribunais([_cfg("tjap")], _sem_telefone(tmp_path), tmp_path / "audit", tmp_path / "out",
                             fake.deps(), rehearsal=False, nova_senha_se_ja_cadastrado=True)
    assert por["TJAP - 1o grau"][0].cadastro == "COMPLETED" and len(fake.pedidos) == 1


def test_assistente_avisa_antes_de_comecar_que_falta_o_telefone(tmp_path, monkeypatch):
    from openpyxl import load_workbook

    trib = _tribunais_de_teste(tmp_path, {"trt5", "trt5-2g", "tjap"})
    original = lote.listar_tribunais
    monkeypatch.setattr(lote, "listar_tribunais", lambda pasta=trib: original(trib))
    planilha = _planilha(tmp_path)
    wb = load_workbook(planilha)
    wb.active.cell(row=2, column=CABECALHO.index("Celular") + 1, value="")
    wb.save(planilha)
    fake = Fake(respostas=[str(planilha)])
    run_executar(tmp_path / "audit", tmp_path / "out", tribunal="trt5, tjap", ensaio=False, deps=fake.deps())
    texto = "\n".join(fake.log)
    assert "ATENCAO linha 2: sem telefone (celular com DDD), exigido em TRT5 - 1o grau" in texto
    assert "exigido em TRT5 - 2o grau" in texto and "exigido em TJAP" not in texto
    assert [t for t, _, _ in fake.pedidos] == ["TJAP - 1o grau"]   # so o TJAP e feito
