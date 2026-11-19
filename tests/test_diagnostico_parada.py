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


def test_mensagem_de_recusa_do_pje_e_lida_da_tela(tmp_path):
    """A tela de login do PJe depois de uma autenticacao recusada (como no TRT11 e no TRT23)."""
    import pytest

    playwright = pytest.importorskip("playwright.sync_api")
    from types import SimpleNamespace

    from pje_rpa.browser import PlaywrightBrowser
    html = """<html><meta charset=utf-8><body><h1>Processo Judicial Eletr&ocirc;nico - 1&ordm; grau</h1>
      <h2>Tribunal Regional do Trabalho da 11&ordf; Regi&atilde;o</h2>
      <div class=erro>N&atilde;o foi poss&iacute;vel realizar a autentica&ccedil;&atilde;o: O usu&aacute;rio 529.982.247-25 n&atilde;o est&aacute; ativo.</div>
      <div><button>Entrar com PDPJ</button><a>Esqueci minha senha</a></div></body></html>"""
    (tmp_path / "login.html").write_text(html, encoding="utf-8")
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"chromium indisponivel: {exc}")
        page = browser.new_page()
        page.goto((tmp_path / "login.html").as_uri())
        b = PlaywrightBrowser.__new__(PlaywrightBrowser)
        b.page, b.config = page, SimpleNamespace(selectors={})
        linha = b.error_line(["nao foi possivel realizar a autenticacao"])
        assert linha == "Não foi possível realizar a autenticação: O usuário 529.982.247-25 não está ativo."
        assert b.error_line(["mensagem que nao esta na tela"]) == ""
        browser.close()
