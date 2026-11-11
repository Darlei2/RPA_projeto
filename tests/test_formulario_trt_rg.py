"""TRT (ex.: TRT15): confirmacao de e-mail, RG e orgao expedidor obrigatorios, avisados so
depois de tentar enviar ("campo obrigatorio" na linha do campo)."""
import dataclasses
from pathlib import Path

import pytest

from pje_rpa import adaptive_form
from pje_rpa.adaptive_form import field_key
from pje_rpa.config import load_lawyer

PROJECT = Path(__file__).resolve().parents[1]
RECORD = load_lawyer(PROJECT / "cadastro.example.json")
COM_RG = dataclasses.replace(RECORD, rg_numero="12.345.678-9", rg_orgao="SSP/BA", rg_data_expedicao="20/03/2015")

AVISO = '<td><span class="erro">campo obrigat&oacute;rio</span></td>'


def _form(depois_de_enviar: bool) -> str:
    aviso = AVISO if depois_de_enviar else "<td></td>"
    return f"""<html><meta charset=utf-8><body><table>
 <tr><td>CPF</td><td>529.982.247-25</td></tr>
 <tr><td>Nome</td><td>ADVOGADO DE TESTE</td></tr>
 <tr><td>OAB</td><td>12345/BA</td></tr>
 <tr><td>Nome do pai</td><td><input id=pai></td></tr>
 <tr><td>CEP</td><td>40000000</td></tr>
 <tr><td>Munic&iacute;pio/UF</td><td>SALVADOR/BA</td></tr>
 <tr><td>Endere&ccedil;o de e-mail</td><td><input id=em readonly value="advogado@example.com"></td></tr>
 <tr><td>Endere&ccedil;o de e-mail (confirma&ccedil;&atilde;o)</td><td><input id=emc></td>{aviso}</tr>
 <tr><td>Telefone celular</td><td><input id=cel value="71999999999"></td></tr>
 <tr><td>N&uacute;mero do RG</td><td><input id=rg></td>{aviso}</tr>
 <tr><td>&Oacute;rg&atilde;o de expedi&ccedil;&atilde;o do RG</td><td><input id=org></td>{aviso}</tr>
 <tr><td>Data de expedi&ccedil;&atilde;o do RG</td><td><input id=dt></td></tr>
 <tr><td>UF de nascimento</td><td><select id=ufn><option>Selecione...</option><option>BA</option></select></td></tr>
 <tr><td>Estado civil</td><td><select id=ec><option>Selecione...</option><option>Casado</option></select></td></tr>
</table><button>Confirmar dados e prosseguir</button></body></html>"""


@pytest.mark.parametrize("label,key", [
    ("Endereço de e-mail", "email"),
    ("Endereço de e-mail (confirmação)", "email_confirm"),
    ("Número do RG", "rg_numero"),
    ("Órgão de expedição do RG", "rg_orgao"),
    ("Data de expedição do RG", "rg_data"),
    ("Número", "address_number"),
    ("Endereço", "logradouro"),
    ("UF do RG", "uf"),
])
def test_rotulos_do_trt(label, key):
    assert field_key({"label": label})[0] == key


@pytest.fixture()
def pagina(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"chromium indisponivel: {exc}")

        def abrir(html):
            path = tmp_path / "trt.html"
            path.write_text(html, encoding="utf-8")
            page = browser.new_page()
            page.goto(path.as_uri())
            return page

        yield abrir
        browser.close()


def test_confirmacao_de_email_e_rg_preenchidos(pagina):
    page = pagina(_form(depois_de_enviar=True))
    report = adaptive_form.operate(page, COM_RG)
    assert not report["blocked"] and report["pending"] == [], report
    assert page.input_value("#emc") == "advogado@example.com"
    assert page.input_value("#rg") == "12.345.678-9"
    assert page.input_value("#org") == "SSP/BA"
    assert page.input_value("#dt") == "20/03/2015"
    assert page.input_value("#pai") == ""  # "Nome do pai" nao e o nome do advogado


def test_rg_sem_dado_no_ensaio_vira_aviso(pagina):
    page = pagina(_form(depois_de_enviar=False))
    report = adaptive_form.operate(page, RECORD)  # planilha sem RG
    assert report["pending"] == [], report
    assert {"rg_numero", "rg_orgao"} <= set(report.get("sem_dado", []))
    assert page.input_value("#emc") == "advogado@example.com"


def test_rg_obrigatorio_depois_de_enviar_vira_pendencia(pagina):
    page = pagina(_form(depois_de_enviar=True))
    report = adaptive_form.operate(page, RECORD)
    assert "campo_obrigatorio_sem_dado:rg_numero" in report["pending"]
    assert "campo_obrigatorio_sem_dado:rg_orgao" in report["pending"]


def test_planilha_le_colunas_de_rg(tmp_path):
    from openpyxl import Workbook

    from pje_rpa.planilha import ler_planilha
    wb = Workbook()
    ws = wb.active
    ws.append(["nome", "cpf", "oab_numero", "oab_uf", "email", "telefone", "cep", "logradouro", "numero",
               "bairro", "cidade", "uf", "RG", "Orgao expedidor", "Data expedicao RG"])
    ws.append(["MARIA DA SILVA", "52998224725", "12345", "SP", "m@exemplo.com.br", "11999999999", "01001000",
               "RUA A", "10", "CENTRO", "SAO PAULO", "SP", "1234567", "ssp/sp", "2015-03-20 00:00:00"])
    wb.save(tmp_path / "a.xlsx")
    rec = ler_planilha(tmp_path / "a.xlsx")[0].record
    assert (rec.rg_numero, rec.rg_orgao, rec.rg_data_expedicao) == ("1234567", "SSP/SP", "20/03/2015")


# ------------------------------------------- PJe devolve o formulario depois de enviar
def _flow_devolvido(monkeypatch, reports, assist, devolucoes=1):
    import tempfile
    from datetime import datetime, timezone

    from pje_rpa.audit import AuditLog
    from pje_rpa.browser import SimulatedBrowser
    from pje_rpa.certificate import CertificateInfo
    from pje_rpa.config import load_config
    from pje_rpa.flow import PJeRegistrationFlow
    from pje_rpa.signer import SimulatedSigner

    queue = list(reports)
    monkeypatch.setattr(adaptive_form, "operate", lambda page, record, **kw: queue.pop(0))
    monkeypatch.setattr(adaptive_form, "page_messages", lambda page: ["campo obrigatorio"])

    class Devolve(SimulatedBrowser):
        """Na(s) primeira(s) vez(es) que clica em avancar, o PJe devolve o formulario."""
        page = None
        recusas = 0

        def click(self, group):
            if group == "confirm_data" and self.recusas < devolucoes:
                self.recusas += 1
                return  # continua em "registration": formulario devolvido
            return super().click(group)

    browser = Devolve(RECORD)
    info = CertificateInfo(RECORD.nome_esperado, RECORD.cpf_esperado, "AC",
                           datetime(2099, 1, 1, tzinfo=timezone.utc), "0" * 40)
    audit = AuditLog(Path(tempfile.mkdtemp()))
    flow = PJeRegistrationFlow(load_config(PROJECT / "config.example.toml"), RECORD, info, "x", browser,
                               SimulatedSigner(browser), audit, code_provider=lambda m: "0",
                               rehearsal=False, assist=assist)
    return flow.run(), audit.path.read_text(encoding="utf-8")


def _rep(pending=(), filled=(), sem_dado=()):
    return {"verified": ["cpf", "nome"], "filled": list(filled), "preserved": [], "absent": [],
            "unknown_optional": 0, "pending": list(pending), "blocked": [], "sem_dado": list(sem_dado)}


def test_formulario_devolvido_operador_preenche_rg_e_o_robo_reenvia(monkeypatch):
    from pje_rpa.models import RunStatus

    perguntas = []
    result, log = _flow_devolvido(
        monkeypatch,
        [_rep(sem_dado=["rg_numero"]),                                   # 1a avaliacao: sem RG na planilha
         _rep(["campo_obrigatorio_sem_dado:rg_numero"], ["email_confirm"]),  # PJe devolveu: agora e obrigatorio
         _rep(filled=[])],                                               # operador preencheu no Edge
        lambda report: perguntas.append(report["pending"]) or "reavaliar")
    assert result.status == RunStatus.COMPLETED, result.reason
    assert perguntas == [["campo_obrigatorio_sem_dado:rg_numero"]]
    assert "form_returned_by_pje" in log


def test_formulario_devolvido_sem_campo_indicado_para_com_mensagem(monkeypatch):
    from pje_rpa.models import RunStatus

    result, _ = _flow_devolvido(monkeypatch, [_rep(), _rep()], None)
    assert result.status == RunStatus.NEEDS_HUMAN and "campo obrigatorio" in result.reason.lower()
