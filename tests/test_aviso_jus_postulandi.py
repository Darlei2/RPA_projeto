"""TRF6: o Quadro de Avisos de quem ja e usuario cita "jus postulandi" num aviso do tribunal.
Isso nao e o perfil da pessoa: deve sair ALREADY_REGISTERED, e nao BLOCKED."""
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pje_rpa.audit import AuditLog
from pje_rpa.browser import SimulatedBrowser
from pje_rpa.certificate import CertificateInfo
from pje_rpa.config import load_config, load_lawyer
from pje_rpa.flow import PJeRegistrationFlow
from pje_rpa.models import RunStatus
from pje_rpa.signer import SimulatedSigner

PROJECT = Path(__file__).resolve().parents[1]


def _run(scenario, aviso_no_painel):
    config = load_config(PROJECT / "config.example.toml")
    record = load_lawyer(PROJECT / "cadastro.example.json")

    class Browser(SimulatedBrowser):
        def page_text_contains(self, markers):
            if aviso_no_painel and self.stage == "post_auth":
                return next((m for m in markers if "jus postulandi" in m.casefold()), None)
            return super().page_text_contains(markers)

    browser = Browser(record, scenario)
    info = CertificateInfo(record.nome_esperado, record.cpf_esperado, "AC",
                           datetime(2099, 1, 1, tzinfo=timezone.utc), "0" * 40)
    with tempfile.TemporaryDirectory() as tmp:
        return PJeRegistrationFlow(config, record, info, "x", browser, SimulatedSigner(browser),
                                   AuditLog(Path(tmp)), code_provider=lambda m: "0", rehearsal=True).run()


def test_aviso_no_quadro_de_avisos_nao_bloqueia_quem_ja_e_usuario():
    assert _run("already_registered", aviso_no_painel=True).status == RunStatus.ALREADY_REGISTERED


def test_jus_postulandi_fora_do_painel_continua_bloqueando():
    assert _run("jus_postulandi", aviso_no_painel=False).status == RunStatus.BLOCKED
