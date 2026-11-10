"""Municipio/UF exibido pelo PJe: formatos aceitos e divergencia preservada (nao trava)."""
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pje_rpa.adaptive_form import static_location_matches

PROJECT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("texto", [
    "Belo Horizonte/MG", "BELO HORIZONTE - MG", "Belo Horizonte (MG)",
    "Belo Horizonte, Minas Gerais", "belo horizonte – mg",
])
def test_formatos_do_municipio_uf(texto):
    assert static_location_matches(texto, "Belo Horizonte", "MG")


def test_cidade_com_hifen_e_divergencias():
    assert static_location_matches("Embu-Guaçu/SP", "Embu-Guacu", "SP")
    assert not static_location_matches("Contagem/MG", "Belo Horizonte", "MG")
    assert not static_location_matches("Belo Horizonte/SP", "Belo Horizonte", "MG")


def test_divergencia_de_municipio_e_preservada_e_o_fluxo_segue(monkeypatch):
    from pje_rpa import adaptive_form
    from pje_rpa.audit import AuditLog
    from pje_rpa.browser import SimulatedBrowser
    from pje_rpa.certificate import CertificateInfo
    from pje_rpa.config import load_config, load_lawyer
    from pje_rpa.flow import PJeRegistrationFlow
    from pje_rpa.models import RunStatus
    from pje_rpa.signer import SimulatedSigner

    class PageBrowser(SimulatedBrowser):
        page = None  # faz o fluxo usar o formulario adaptativo

    monkeypatch.setattr(adaptive_form, "operate", lambda page, record: {
        "verified": ["cpf", "nome", "oab_numero"], "filled": ["email"], "preserved": [], "absent": [],
        "unknown_optional": 0, "pending": ["municipio_uf_divergente"], "blocked": []})
    config = load_config(PROJECT / "config.example.toml")
    record = load_lawyer(PROJECT / "cadastro.example.json")
    browser = PageBrowser(record)
    info = CertificateInfo(record.nome_esperado, record.cpf_esperado, "AC",
                           datetime(2099, 1, 1, tzinfo=timezone.utc), "0" * 40)
    with tempfile.TemporaryDirectory() as tmp:
        audit = AuditLog(Path(tmp))
        result = PJeRegistrationFlow(config, record, info, "x", browser, SimulatedSigner(browser), audit,
                                     code_provider=lambda m: "000000", rehearsal=True).run()
        log = audit.path.read_text(encoding="utf-8")
    assert result.status == RunStatus.REHEARSAL_OK, result.reason
    assert "municipio_uf" in log and "address_divergences_preserved" in log
