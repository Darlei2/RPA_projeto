"""Codigo de segundo fator: o campo tem de ficar preenchido e, se a tela continuar pedindo o codigo,
o robo tenta de novo com um codigo novo (poucas vezes, para nao bloquear o usuario)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from pje_rpa import flow as flow_mod
from pje_rpa.audit import AuditLog
from pje_rpa.browser import PlaywrightBrowser, SimulatedBrowser
from pje_rpa.certificate import CertificateInfo
from pje_rpa.config import load_config, load_lawyer
from pje_rpa.flow import PJeRegistrationFlow
from pje_rpa.models import RunStatus
from pje_rpa.signer import SimulatedSigner

PROJECT = Path(__file__).resolve().parents[1]


class Tela2FA(SimulatedBrowser):
    """A tela so aceita o codigo a partir da tentativa `aceita_na`; antes disso continua pedindo."""

    def __init__(self, record, aceita_na: int):
        super().__init__(record, "second_factor")
        self.aceita_na, self.codigos, self.preenchimentos = aceita_na, [], []

    def fill_checked(self, group, value):
        self.codigos.append(value)
        ficou = len(self.codigos) >= self.aceita_na      # antes disso o campo "ficou em branco"
        self.preenchimentos.append(ficou)
        self.fill(group, value)
        return ficou

    def human_completed_second_factor(self):
        if len(self.codigos) >= self.aceita_na:
            super().human_completed_second_factor()


def _rodar(tmp_path, monkeypatch, aceita_na):
    monkeypatch.setattr(flow_mod, "ESPERA_CODIGO_2FA_MS", 50)
    monkeypatch.setattr(flow_mod, "LOGIN_TIMEOUT_MS", 50)
    record = load_lawyer(PROJECT / "cadastro.example.json")
    info = CertificateInfo(record.nome_esperado, record.cpf_esperado, "AC SIMULADA",
                           datetime(2099, 1, 1, tzinfo=timezone.utc), "0" * 40)
    tela = Tela2FA(record, aceita_na)
    gerados = iter(["111111", "222222", "333333"])
    audit = AuditLog(tmp_path)
    resultado = PJeRegistrationFlow(load_config(PROJECT / "config.example.toml"), record, info, "senha", tela,
                                    SimulatedSigner(tela), audit, code_provider=lambda m: next(gerados)).run()
    return resultado, tela, audit.path.read_text(encoding="utf-8")


def test_campo_em_branco_na_primeira_vez_robo_tenta_de_novo_com_codigo_novo(tmp_path, monkeypatch):
    resultado, tela, auditoria = _rodar(tmp_path, monkeypatch, aceita_na=2)
    assert resultado.status == RunStatus.COMPLETED
    assert tela.codigos == ["111111", "222222"]                 # um codigo NOVO na segunda tentativa
    assert "second_factor_retry" in auditoria and '"campo_preenchido": false' in auditoria
    assert "111111" not in auditoria and "222222" not in auditoria   # o codigo nunca vai para a auditoria


def test_codigo_aceito_de_primeira_nao_gera_segundo_codigo(tmp_path, monkeypatch):
    resultado, tela, auditoria = _rodar(tmp_path, monkeypatch, aceita_na=1)
    assert resultado.status == RunStatus.COMPLETED and tela.codigos == ["111111"]
    assert "second_factor_retry" not in auditoria


def test_tela_que_nunca_aceita_para_depois_de_duas_tentativas(tmp_path, monkeypatch):
    """Chave errada: o robo nao insiste alem de duas tentativas, para nao bloquear o usuario."""
    resultado, tela, _ = _rodar(tmp_path, monkeypatch, aceita_na=99)
    assert resultado.status == RunStatus.NEEDS_HUMAN and len(tela.codigos) == flow_mod.CODIGOS_2FA == 2
    assert "recusado ou expirado (2 tentativas" in resultado.reason


def test_campo_que_nao_guarda_o_valor_e_digitado_tecla_por_tecla(tmp_path):
    """Campo redesenhado pela pagina: o fill nao fica; o robo confere e digita de novo, tecla por tecla."""
    playwright = pytest.importorskip("playwright.sync_api")
    html = """<html><body><input id=otp autocomplete="one-time-code"><button id=kc-login>Entrar</button>
<script>
  const campo = document.getElementById('otp'); let teclas = 0;
  campo.addEventListener('keydown', () => { teclas++; });
  // quem escreve o valor de uma vez (sem teclas) tem o campo limpo, como a tela fazia
  campo.addEventListener('input', () => { if (!teclas) campo.value = ''; });
</script></body></html>"""
    (tmp_path / "otp.html").write_text(html, encoding="utf-8")
    with playwright.sync_playwright() as pw:
        try:
            navegador = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"chromium indisponivel: {exc}")
        page = navegador.new_page()
        page.goto((tmp_path / "otp.html").as_uri())
        robo = PlaywrightBrowser.__new__(PlaywrightBrowser)       # so o que o preenchimento usa
        robo.page, robo.last_spec = page, ""
        robo._specs = lambda group: ["css=input#otp"]
        assert robo.fill_checked("second_factor_input", "654321") is True
        assert page.input_value("#otp") == "654321"
        navegador.close()
