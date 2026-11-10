import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pje_rpa.audit import AuditLog
from pje_rpa.browser import SimulatedBrowser
from pje_rpa.certificate import CertificateInfo
from pje_rpa.config import load_config, load_lawyer
from pje_rpa.flow import PJeRegistrationFlow
from pje_rpa.models import RunStatus
from pje_rpa.signer import SignerError, SimulatedSigner

PROJECT = Path(__file__).resolve().parents[1]


class SilentSigner(SimulatedSigner):
    """PJeOffice que assina sem abrir janela: o sign() do assinador real estoura o prazo."""

    def __init__(self, browser, page_completes: bool) -> None:
        super().__init__(browser)
        self.page_completes = page_completes

    def sign(self, certificate, password) -> None:
        if not self.page_completes:
            self.browser.stage = "term"  # nada aconteceu na pagina
        raise SignerError("Janela 'Selecao de certificado' do PJeOffice (assinatura) nao apareceu")


class AssinaturaSemJanelaTest(unittest.TestCase):
    def _run(self, page_completes: bool):
        config = load_config(PROJECT / "config.example.toml")
        record = load_lawyer(PROJECT / "cadastro.example.json")
        browser = SimulatedBrowser(record)
        info = CertificateInfo(record.nome_esperado, record.cpf_esperado, "AC",
                               datetime(2099, 1, 1, tzinfo=timezone.utc), "0" * 40)
        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditLog(Path(tmp))
            flow = PJeRegistrationFlow(config, record, info, "senha", browser,
                                       SilentSigner(browser, page_completes), audit,
                                       code_provider=lambda m: "000000")
            result = flow.run()
            events = [line for line in audit.path.read_text(encoding="utf-8").splitlines()]
        return result, "\n".join(events)

    def test_pje_concluiu_sem_janela_conta_como_assinado(self):
        result, events = self._run(page_completes=True)
        self.assertEqual(result.status, RunStatus.COMPLETED, result.reason)
        self.assertIn("term_signed_without_dialog", events)

    def test_sem_janela_e_sem_conclusao_falha(self):
        result, events = self._run(page_completes=False)
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertIn("nao apareceu", result.reason)
