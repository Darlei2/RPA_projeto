"""Formularios diferentes (ex.: Justica do Trabalho) sem travar: contexto, desempate, campos
fixados no .toml e modo assistido (o operador resolve no Edge e o robo reavalia)."""
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pje_rpa import adaptive_form
from pje_rpa.adaptive_form import break_tie, field_key, other_subject
from pje_rpa.config import load_lawyer

PROJECT = Path(__file__).resolve().parents[1]
RECORD = load_lawyer(PROJECT / "cadastro.example.json")  # ADVOGADO DE TESTE, 52998224725, OAB 12345/BA


def _f(label, index=0, section="", value="", **kw):
    return {"label": label, "index": index, "section": section, "value": value, "id": kw.get("id", ""),
            "name": kw.get("name", ""), "tag": kw.get("tag", "input"), "options": kw.get("options", [])}


# ------------------------------------------------------------------ regras
def test_estado_civil_nao_e_uf():
    assert field_key(_f("Estado civil"))[0] is None
    assert field_key(_f("Estado"))[0] == "uf"


@pytest.mark.parametrize("label", ["UF de nascimento", "UF do orgao expedidor", "UF do RG", "Cidade natal (naturalidade)"])
def test_campos_de_outro_assunto_nao_sao_endereco(label):
    key = field_key(_f(label))[0]
    assert key in ("uf", "cidade") and other_subject(key, _f(label))


def test_desempate_pelo_bloco_endereco_e_pela_proximidade_do_cep():
    expected = {"uf": "BA"}
    a, b = _f("UF", 3, section="Dados pessoais"), _f("UF", 20, section="Endereco residencial")
    assert break_tie("uf", [a, b], expected, None) is b
    cep = _f("CEP", 18, section="")
    c, d = _f("UF", 2), _f("UF", 21)
    assert break_tie("uf", [c, d], expected, cep) is d
    # ja preenchido com o valor certo em um so: ele vence
    e, f = _f("UF", 2, value="BA"), _f("UF", 40)
    assert break_tie("uf", [e, f], expected, None) is e
    # sem evidencia: nao chuta
    assert break_tie("uf", [_f("UF", 2), _f("UF", 4)], expected, _f("CEP", 3)) is None


# ------------------------------------------- formulario no estilo da Justica do Trabalho
FORM = """<html><meta charset=utf-8><body>
<table>
 <tr><td><span>Nome</span></td><td><span>ADVOGADO DE TESTE</span></td></tr>
 <tr><td><span>CPF</span></td><td><span>529.982.247-25</span></td></tr>
 <tr><td><span>OAB</span></td><td><span>12345/BA</span></td></tr>
</table>
<fieldset><legend>Dados pessoais</legend>
 <label for=ec>Estado civil</label>
 <select id=ec required><option value="">Selecione</option><option value="1" %s>Solteiro(a)</option></select>
 <label for=ufn>UF de nascimento</label>
 <select id=ufn><option value="">Selecione</option><option value="BA">BA</option></select>
</fieldset>
<fieldset><legend>Endereco</legend>
 <label for=cep>CEP</label><input id=cep>
 <label for=log>Logradouro</label><input id=log readonly value="RUA OFICIAL DOS CORREIOS">
 <label for=num>Numero</label><input id=num>
 <label for=bai>Bairro</label><input id=bai>
 <label for=cid>Cidade</label><input id=cid>
 <label for=uf>UF</label><select id=uf><option value="">Selecione</option><option value="BA">BA</option></select>
</fieldset>
<label for=em>E-mail</label><input id=em type=email>
<label for=tel>Telefone</label><input id=tel>
<button>Confirmar dados e prosseguir</button>
</body></html>"""


@pytest.fixture()
def pagina(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"chromium indisponivel: {exc}")

        def abrir(html):
            path = tmp_path / "form.html"
            path.write_text(html, encoding="utf-8")
            page = browser.new_page()
            page.goto(path.as_uri())
            return page

        yield abrir
        browser.close()


def test_formulario_trt_sem_ambiguidade_e_logradouro_preservado(pagina):
    page = pagina(FORM % "selected")  # estado civil ja escolhido no PJe
    report = adaptive_form.operate(page, RECORD)
    assert not report["blocked"], report
    assert not any(p.startswith("campo_ambiguo") for p in report["pending"]), report
    assert report["pending"] == [], report
    assert "logradouro" in report["preserved"] and "logradouro" in report.get("preserved_divergent", [])
    assert page.input_value("#uf") == "BA" and page.input_value("#ufn") == ""  # endereco, nao nascimento
    assert any("nascimento" in item for item in report.get("other_subject", []))


def test_obrigatorio_sem_dado_vira_pendencia_com_rotulo(pagina):
    page = pagina(FORM % "")  # estado civil vazio e obrigatorio
    report = adaptive_form.operate(page, RECORD)
    assert "campo_obrigatorio_desconhecido" in report["pending"]
    assert any("Estado civil" in item for item in report.get("unknown_required", []))


def test_campo_fixado_no_toml_vence_a_heuristica(pagina):
    page = pagina(FORM % "selected")
    report = adaptive_form.operate(page, RECORD, overrides={"uf": ["id=ufn"]})
    assert page.input_value("#ufn") == "BA"  # o operador mandou usar este


# ----------------------------------------------------------------- modo assistido
def _flow(monkeypatch, reports, assist):
    from pje_rpa.audit import AuditLog
    from pje_rpa.browser import SimulatedBrowser
    from pje_rpa.certificate import CertificateInfo
    from pje_rpa.config import load_config
    from pje_rpa.flow import PJeRegistrationFlow
    from pje_rpa.signer import SimulatedSigner

    queue = list(reports)
    monkeypatch.setattr(adaptive_form, "operate", lambda page, record, **kw: queue.pop(0))

    class PageBrowser(SimulatedBrowser):
        page = None

    browser = PageBrowser(RECORD)
    info = CertificateInfo(RECORD.nome_esperado, RECORD.cpf_esperado, "AC",
                           datetime(2099, 1, 1, tzinfo=timezone.utc), "0" * 40)
    tmp = tempfile.mkdtemp()
    audit = AuditLog(Path(tmp))
    flow = PJeRegistrationFlow(load_config(PROJECT / "config.example.toml"), RECORD, info, "x", browser,
                               SimulatedSigner(browser), audit, code_provider=lambda m: "0",
                               rehearsal=True, assist=assist)
    return flow.run(), audit.path.read_text(encoding="utf-8")


def _rep(pending=(), blocked=()):
    return {"verified": ["cpf", "nome"], "filled": [], "preserved": [], "absent": [], "unknown_optional": 0,
            "pending": list(pending), "blocked": list(blocked)}


def test_operador_corrige_no_edge_e_o_robo_reavalia(monkeypatch):
    from pje_rpa.models import RunStatus

    perguntas = []
    result, log = _flow(monkeypatch, [_rep(["campo_obrigatorio_desconhecido"]), _rep()],
                        lambda report: perguntas.append(report["pending"]) or "reavaliar")
    assert result.status == RunStatus.REHEARSAL_OK and perguntas == [["campo_obrigatorio_desconhecido"]]
    assert "operator_fixed_form" in log


def test_operador_confirma_que_o_formulario_esta_certo(monkeypatch):
    from pje_rpa.models import RunStatus

    result, log = _flow(monkeypatch, [_rep(["campo_ambiguo:uf"])], lambda report: "seguir")
    assert result.status == RunStatus.REHEARSAL_OK and "operator_confirmed_form" in log


def test_divergencia_de_identidade_nunca_passa_pelo_modo_assistido(monkeypatch):
    from pje_rpa.models import RunStatus

    chamado = []
    result, _ = _flow(monkeypatch, [_rep(blocked=["identidade_divergente:cpf"])],
                      lambda report: chamado.append(1) or "seguir")
    assert result.status == RunStatus.BLOCKED and chamado == []


def test_sem_operador_continua_parando_para_revisao(monkeypatch):
    from pje_rpa.models import RunStatus

    result, _ = _flow(monkeypatch, [_rep(["campo_ambiguo:uf"])], None)
    assert result.status == RunStatus.NEEDS_HUMAN
