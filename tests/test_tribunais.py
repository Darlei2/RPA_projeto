import json
from pathlib import Path

import pytest

from pje_rpa.config import ConfigurationError, load_config
from pje_rpa.tribunais import format_relatorio, ramo, relatorio

PROJECT = Path(__file__).resolve().parents[1]
SEM_TABELA = PROJECT / "tests" / "nao-existe.csv"  # so a regra fixa (sem a tabela do mapeamento)
TRIBUNAIS = sorted(p for p in (PROJECT / "tribunais").glob("*.toml") if p.name != "_comum.toml")


@pytest.mark.parametrize("path", TRIBUNAIS, ids=lambda p: p.stem)
def test_todo_tribunal_carrega_com_base_comum(path):
    config = load_config(path)
    assert config.base_url.startswith("https://")
    assert config.base_url.split("/")[2].endswith(".jus.br")
    assert config.tribunal != "DEFINIR_NO_ARQUIVO_DO_TRIBUNAL"
    assert "completion" in config.selectors and "confirm_data" in config.selectors


def test_cada_endereco_e_cada_nome_de_tribunal_aparecem_uma_vez():
    configs = [load_config(p) for p in TRIBUNAIS]
    enderecos = [c.base_url for c in configs]
    assert len(enderecos) == len(set(enderecos))
    assert len({c.tribunal for c in configs}) == len(configs)


# PJe dos TJs em que cada grau tem endereco, cadastro e credencial proprios (dominios <TJ>-PJE-1G/2G).
SEGUNDO_GRAU_DOS_TJS = {
    "tjap-2g": ("tjap", "https://pje.tjap.jus.br/2g/login.seam"),
    "tjba-2g": ("tjba", "https://pje2g.tjba.jus.br/pje/login.seam"),
    "tjmg-2g": ("tjmg", "https://pjerecursal.tjmg.jus.br/pje/login.seam"),
    "tjmt-2g": ("tjmt", "https://pje2.tjmt.jus.br/pje2/login.seam"),
    "tjce-2g": ("tjce", "https://pje.tjce.jus.br/pje2grau/login.seam"),
    "tjdft-2g": ("tjdft", "https://pje2i.tjdft.jus.br/pje/login.seam"),
    "tjes-2g": ("tjes", "https://pje.tjes.jus.br/pje2g/login.seam"),
    "tjma-2g": ("tjma", "https://pje2.tjma.jus.br/pje2g/login.seam"),
    "tjpa-2g": ("tjpa", "https://pje.tjpa.jus.br/pje-2g/login.seam"),
    "tjpb-2g": ("tjpb", "https://pje.tjpb.jus.br/pje2g/login.seam"),
    "tjpe-2g": ("tjpe", "https://pje.cloud.tjpe.jus.br/2g/login.seam"),
    "tjrn-2g": ("tjrn", "https://pje2g.tjrn.jus.br/pje/login.seam"),
    "tjro-2g": ("tjro", "https://pjesg.tjro.jus.br/pje/login.seam"),
}


@pytest.mark.parametrize("chave", sorted(SEGUNDO_GRAU_DOS_TJS))
def test_segundo_grau_dos_tjs_herda_o_primeiro_e_fica_fora_da_regra_dos_trts(chave):
    from pje_rpa import exigencias, graus

    primeiro, endereco = SEGUNDO_GRAU_DOS_TJS[chave]
    segundo, base = load_config(PROJECT / "tribunais" / f"{chave}.toml"), load_config(PROJECT / "tribunais" / f"{primeiro}.toml")
    assert segundo.base_url == endereco and segundo.base_url != base.base_url
    assert segundo.tribunal == base.tribunal.replace("1o grau", "2o grau") != base.tribunal
    assert segundo.selectors == base.selectors
    # TJ nao entra na regra "os dois graus numa credencial" nem na exigencia de telefone dos TRTs
    assert graus.grupo(segundo.base_url) == "" and exigencias.faltando(segundo.base_url, object(), tabela=SEM_TABELA) == []


def test_tribunais_novos_sem_primeiro_grau_no_robo():
    for chave, tribunal, endereco in (("tjma", "TJMA - 1o grau", "https://pje.tjma.jus.br/pje/login.seam"),
                                      ("tjro", "TJRO - 1o grau", "https://pjepg.tjro.jus.br/pje/login.seam"),
                                      ("tjpi-2g", "TJPI - 2o grau", "https://pje.tjpi.jus.br/2g/login.seam"),
                                      ("tse", "TSE", "https://pje.tse.jus.br/pje/login.seam")):
        config = load_config(PROJECT / "tribunais" / f"{chave}.toml")
        assert (config.tribunal, config.base_url) == (tribunal, endereco)


UFS = "AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO".split()


@pytest.mark.parametrize("uf", UFS)
def test_cada_tre_tem_os_dois_graus(uf):
    """TREs (planilha "Testagem e Cadastro em Tribunais"): 1o grau no servidor do TSE, 2o grau no do TRE."""
    from pje_rpa import exigencias, graus

    primeiro = load_config(PROJECT / "tribunais" / f"tre{uf.lower()}.toml")
    segundo = load_config(PROJECT / "tribunais" / f"tre{uf.lower()}-2g.toml")
    assert primeiro.base_url == f"https://pje1g-{uf.lower()}.tse.jus.br/pje/login.seam"
    assert segundo.base_url == f"https://pje.tre-{uf.lower()}.jus.br/pje/login.seam"
    assert (primeiro.tribunal, segundo.tribunal) == (f"TRE-{uf} - 1o grau", f"TRE-{uf} - 2o grau")
    assert segundo.selectors == primeiro.selectors
    # cada grau tem cadastro e credencial proprios; a exigencia fixa de telefone/RG e so dos TRTs
    assert graus.grupo(segundo.base_url) == "" and exigencias.faltando(segundo.base_url, object(), tabela=SEM_TABELA) == []


def test_pje_da_planilha_que_faltavam_no_robo():
    for chave, tribunal, endereco in (("tjmt", "TJMT - 1o grau", "https://pje.tjmt.jus.br/pje/login.seam"),
                                      ("tjpi", "TJPI - 1o grau", "https://pje.tjpi.jus.br/1g/login.seam"),
                                      ("cnj", "CNJ", "https://www.cnj.jus.br/pjecnj/login.seam"),
                                      ("tst", "TST", "https://pje.tst.jus.br/tst/login.seam"),
                                      ("trf5", "TRF5 - PJe", "https://pje.trf5.jus.br/pje/login.seam")):
        config = load_config(PROJECT / "tribunais" / f"{chave}.toml")
        assert (config.tribunal, config.base_url) == (tribunal, endereco)
    assert len(TRIBUNAIS) == 144  # todos os dominios PJe da planilha tem arquivo


def test_heranca_sobrescreve_so_o_declarado(tmp_path):
    (tmp_path / "base.toml").write_text(
        (PROJECT / "tribunais" / "_comum.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "x.toml").write_text(
        'herda = "base.toml"\n[app]\ntribunal = "TRF9"\nbase_url = "https://pje.trf9.jus.br/pje/login.seam"\n'
        '[selectors]\nlogin_certificate_button = ["css=#outro"]\n',
        encoding="utf-8",
    )
    config = load_config(tmp_path / "x.toml")
    assert config.tribunal == "TRF9"
    assert config.selectors["login_certificate_button"] == ["css=#outro"]
    assert "url=cadastroConcluido" in config.selectors["completion"]
    assert config.selectors_approved is False


def test_heranca_circular(tmp_path):
    (tmp_path / "a.toml").write_text('herda = "b.toml"\n', encoding="utf-8")
    (tmp_path / "b.toml").write_text('herda = "a.toml"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(tmp_path / "a.toml")


def test_ramo():
    assert ramo("TRT2 - 1o grau") == "TRT"
    assert ramo("TJAP - 1o grau") == "TJ"
    assert ramo("TRF6 - 1o grau") == "TRF"


def _run(root, run_id, tribunal, status, ts="2026-11-06T13:00:00+00:00"):
    d = root / run_id
    d.mkdir()
    (d / "result.json").write_text(json.dumps({"run_id": run_id, "status": status, "details": {"tribunal": tribunal}}))
    (d / "audit.jsonl").write_text(json.dumps({"timestamp_utc": ts}) + "\n")


def test_relatorio_e_meta(tmp_path):
    _run(tmp_path, "1", "TJAP - 1o grau", "FAILED")
    _run(tmp_path, "2", "TJAP - 1o grau", "COMPLETED")
    _run(tmp_path, "3", "TRT2 - 1o grau", "REHEARSAL_OK")
    data = relatorio(tmp_path, goal=2)
    tjap = next(r for r in data["tribunais"] if r["tribunal"].startswith("TJAP"))
    assert tjap["status"] == "COMPLETED" and tjap["execucoes"] == 2
    assert data["meta"]["tribunais_com_cadastro_real"] == 1
    assert data["meta"]["ramos_faltando"] == ["TRF", "TRT", "TJ"]
    assert not data["meta"]["atingida"]
    assert "Ainda falta" in format_relatorio(data)

    _run(tmp_path, "4", "TRT2 - 1o grau", "COMPLETED")
    _run(tmp_path, "5", "TRF6 - 1o grau", "COMPLETED")
    data = relatorio(tmp_path, goal=3)
    assert data["meta"]["tribunais_com_cadastro_real"] == 3
    assert not data["meta"]["atingida"]  # falta testar senha + 2FA

    for n, tribunal in enumerate(["TJAP - 1o grau", "TRT2 - 1o grau", "TRF6 - 1o grau"]):
        d = tmp_path / f"c{n}"
        d.mkdir()
        (d / "result.json").write_text(json.dumps({"kind": "credencial", "status": "CREDENTIAL_OK",
                                                   "details": {"tribunal": tribunal}}))
    data = relatorio(tmp_path, goal=3)
    assert data["meta"]["tribunais_com_credencial_testada"] == 3
    assert data["meta"]["atingida"]
    assert "testada" in format_relatorio(data)
