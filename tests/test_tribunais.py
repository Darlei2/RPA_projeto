import json
from pathlib import Path

import pytest

from pje_rpa.config import ConfigurationError, load_config
from pje_rpa.tribunais import format_relatorio, ramo, relatorio

PROJECT = Path(__file__).resolve().parents[1]
TRIBUNAIS = sorted(p for p in (PROJECT / "tribunais").glob("*.toml") if p.name != "_comum.toml")


@pytest.mark.parametrize("path", TRIBUNAIS, ids=lambda p: p.stem)
def test_todo_tribunal_carrega_com_base_comum(path):
    config = load_config(path)
    assert config.base_url.startswith("https://")
    assert config.base_url.split("/")[2].endswith(".jus.br")
    assert config.tribunal != "DEFINIR_NO_ARQUIVO_DO_TRIBUNAL"
    assert "completion" in config.selectors and "confirm_data" in config.selectors


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
    assert data["meta"]["ramos_faltando"] == ["TRF", "TRT"]
    assert not data["meta"]["atingida"]
    assert "Ainda falta" in format_relatorio(data)

    _run(tmp_path, "4", "TRT2 - 1o grau", "COMPLETED")
    _run(tmp_path, "5", "TRF6 - 1o grau", "COMPLETED")
    assert relatorio(tmp_path, goal=3)["meta"]["atingida"]
