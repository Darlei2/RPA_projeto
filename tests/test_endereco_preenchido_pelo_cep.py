"""O PJe preenche (e trava) o bairro sozinho logo depois do CEP. Quando isso acontece bem na hora em
que o robo escreve no campo, a escrita falha - e o campo esta certo. O robo olha de novo antes de
parar com "falha_preenchimento:bairro" (TRT18, 19/11/2026)."""
import dataclasses
from pathlib import Path

import pytest

from pje_rpa import adaptive_form
from pje_rpa.config import load_lawyer

PROJECT = Path(__file__).resolve().parents[1]
RECORD = load_lawyer(PROJECT / "cadastro.example.json")
FORM = """<html><meta charset=utf-8><body><table>
 <tr><td>CPF</td><td>529.982.247-25</td></tr>
 <tr><td>Nome</td><td>ADVOGADO DE TESTE</td></tr>
 <tr><td>OAB</td><td>12345/BA</td></tr>
 <tr><td>CEP</td><td><input id=cep></td></tr>
 <tr><td>Munic&iacute;pio/UF</td><td>SALVADOR/BA</td></tr>
 <tr><td>Endere&ccedil;o</td><td><input id=rua></td></tr>
 <tr><td>N&uacute;mero</td><td><input id=num></td></tr>
 <tr><td>Bairro</td><td><input id=bairro></td></tr>
 <tr><td>Endere&ccedil;o de e-mail</td><td><input id=em value="advogado@example.com"></td></tr>
 <tr><td>Telefone celular</td><td><input id=cel value="71999999999"></td></tr>
</table><button>Confirmar dados e prosseguir</button></body></html>"""


class _Campo:
    """Um campo da tela cuja primeira escrita falha, como quando o PJe mexe nele naquele instante."""
    def __init__(self, real, ao_falhar):
        self._real, self._ao_falhar = real, ao_falhar

    def fill(self, *args, **kwargs):
        if self._ao_falhar():
            raise TimeoutError("Timeout 4000ms exceeded: element is not editable")
        return self._real.fill(*args, **kwargs)

    def __getattr__(self, nome):
        return getattr(self._real, nome)


class _Pagina:
    """Pagina de verdade, mas a escrita no bairro falha na 1a vez e, nesse instante, roda o `efeito`
    (o que o PJe faz por conta propria com o campo)."""
    def __init__(self, page, efeito):
        self._page, self._efeito, self.falhas = page, efeito, 0

    def _ao_falhar(self) -> bool:
        if self.falhas:
            return False
        self.falhas += 1
        self._page.evaluate(self._efeito)
        return True

    def locator(self, seletor):
        real, pagina = self._page.locator(seletor), self

        class _Lista:
            def nth(self, indice):
                campo = real.nth(indice)
                eh_bairro = campo.evaluate("e => e.id === 'bairro'")
                return _Campo(campo, pagina._ao_falhar) if eh_bairro else campo

            def __getattr__(self, nome):
                return getattr(real, nome)
        return _Lista()

    def __getattr__(self, nome):
        return getattr(self._page, nome)


@pytest.fixture()
def pagina(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"chromium indisponivel: {exc}")

        def abrir(efeito):
            path = tmp_path / "cep.html"
            path.write_text(FORM, encoding="utf-8")
            page = browser.new_page()
            page.goto(path.as_uri())
            return _Pagina(page, efeito)

        yield abrir
        browser.close()


def test_bairro_que_o_pje_preencheu_e_travou_com_o_mesmo_valor_nao_para(pagina):
    page = pagina("() => { const b = document.getElementById('bairro'); b.value = 'BAIRRO DE TESTE'; b.readOnly = true; }")
    report = adaptive_form.operate(page, RECORD)
    assert page.falhas == 1
    assert report["pending"] == [] and not report["blocked"], report
    assert "bairro" in report["preserved"] and "reavaliado_apos_falha:bairro" in report["notes"]
    assert page.input_value("#bairro") == "BAIRRO DE TESTE"


def test_bairro_que_o_pje_travou_com_outra_grafia_vale_o_do_pje(pagina):
    page = pagina("() => { const b = document.getElementById('bairro'); b.value = 'CENTRO HISTORICO'; b.disabled = true; }")
    report = adaptive_form.operate(page, RECORD)
    assert report["pending"] == [], report
    assert "bairro" in report.get("preserved_divergent", [])
    assert page.input_value("#bairro") == "CENTRO HISTORICO"  # travado pelo PJe: o robo nao mexe


def test_falha_passageira_o_robo_escreve_de_novo(pagina):
    page = pagina("() => {}")  # a escrita falhou, mas o campo continua livre e vazio
    report = adaptive_form.operate(page, RECORD)
    assert report["pending"] == [], report
    assert "bairro" in report["filled"]
    assert page.input_value("#bairro") == "BAIRRO DE TESTE"


def test_campo_travado_e_vazio_continua_parando(pagina):
    page = pagina("() => { document.getElementById('bairro').readOnly = true; }")
    report = adaptive_form.operate(page, RECORD)
    assert "falha_preenchimento:bairro" in report["pending"], report
