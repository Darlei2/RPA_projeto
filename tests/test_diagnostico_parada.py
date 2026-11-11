"""Quando o cadastro para (revisao/falha), fica um print da tela e o motivo detalhado."""
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pje_rpa import adaptive_form
from pje_rpa.audit import AuditLog
from pje_rpa.browser import SimulatedBrowser
from pje_rpa.certificate import CertificateInfo
from pje_rpa.config import load_config, load_lawyer
from pje_rpa.execucao import ResultadoCadastro
from pje_rpa.flow import PJeRegistrationFlow
from pje_rpa.lote import _mostrar_diagnostico
from pje_rpa.models import RunStatus
from pje_rpa.signer import SimulatedSigner

PROJECT = Path(__file__).resolve().parents[1]


class FakePage:
    def screenshot(self, path, full_page=True):
        Path(path).write_bytes(b"png")


class PageBrowser(SimulatedBrowser):
    page = FakePage()


def test_revisao_gera_print_e_mostra_campos(monkeypatch):
    monkeypatch.setattr(adaptive_form, "operate", lambda page, record: {
        "verified": ["cpf", "nome", "oab_numero"], "filled": [], "preserved": [], "absent": [],
        "unknown_optional": 0, "pending": ["campo_ambiguo:uf", "campo_nao_editavel:logradouro"], "blocked": [],
        "ambiguous": ["uf: UF | UF do orgao expedidor"],
        "non_editable": ["logradouro: preenchido pelo PJe"]})
    config = load_config(PROJECT / "config.example.toml")
    record = load_lawyer(PROJECT / "cadastro.example.json")
    browser = PageBrowser(record)
    info = CertificateInfo(record.nome_esperado, record.cpf_esperado, "AC",
                           datetime(2099, 1, 1, tzinfo=timezone.utc), "0" * 40)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        audit = AuditLog(root)
        result = PJeRegistrationFlow(config, record, info, "x", browser, SimulatedSigner(browser), audit,
                                     code_provider=lambda m: "000000", rehearsal=True).run()
        assert result.status == RunStatus.NEEDS_HUMAN
        assert (audit.run_dir / "tela-revisao.png").exists()
        log = []
        _mostrar_diagnostico(root, ResultadoCadastro("NEEDS_HUMAN", result.reason, audit.run_id), log.append)
    texto = "\n".join(log)
    assert "tela-revisao.png" in texto
    assert "Campo 'uf' ambiguo, candidatos: UF | UF do orgao expedidor" in texto
    assert "Campo 'logradouro' bloqueado para edicao (preenchido pelo PJe)" in texto
