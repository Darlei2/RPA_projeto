"""Troca do e-mail em 'Alteracao de dados cadastrais' contra um PJe simulado.

Cadastro antigo incompleto: o PJe recusa o ATUALIZAR enquanto houver obrigatorio vazio. No modo
assistido o robo pausa com o formulario na tela, o operador completa e o robo salva e confere."""
import json

import pytest

from pje_rpa.credencial import ajustar_email_cadastral

BASE = "https://pje.teste.jus.br/pje/login.seam"
FORM = "https://pje.teste.jus.br/pje/PessoaAdvogado/AlteracaoCadastro/listView.seam"
CAMPOS = ("nascimento", "oab", "sexo", "cep", "telefone")
ROTULOS = {"nascimento": "Data de Nascimento", "oab": "OAB Inscrição", "sexo": "Sexo", "cep": "CEP",
           "telefone": "Telefone"}


def _html(estado: dict) -> str:
    def campo(nome: str) -> str:
        valor = estado.get(nome, "")
        if nome == "sexo":
            return ("<div><label>Sexo*</label><select id=sexo><option value='noSelectionValue'>Selecione</option>"
                    f"<option value=F {'selected' if valor == 'F' else ''}>Feminino</option></select></div>")
        return f"<div><label>{ROTULOS[nome]}*</label><input id={nome} type=text value='{valor}'></div>"

    return f"""<html><body><div class="alert-warning">* Campos obrigatórios</div>
      <div id=msgs></div>
      <div><label>Nome Completo*</label><input id=nome type=text disabled value='{estado.get("nome", "")}'></div>
      {''.join(campo(c) for c in CAMPOS)}
      <div><label>E-mail*</label><input id=email type=text value='{estado.get("email", "")}'></div>
      <input type=button value="ATUALIZAR" onclick="salvar()">
      <script>
      const rot = {json.dumps(ROTULOS)};
      async function salvar() {{
        const v = {{email: email.value}}; const falta = [];
        for (const c of Object.keys(rot)) {{
          const e = document.getElementById(c);
          v[c] = e.value === 'noSelectionValue' ? '' : e.value;
          if (!v[c]) falta.push(c);
        }}
        if (falta.length) {{
          msgs.innerHTML = '<div class="rich-messages">' + falta.map(c =>
            '<span class="rich-message">' + rot[c] + '*</span><span class="rich-message">Campo obrigatório</span>'
          ).join('') + '</div>';
          return;
        }}
        await fetch('/pje/salvar', {{method: 'POST', body: JSON.stringify(v)}});
        msgs.innerHTML = '<div class="rich-messages">Registro alterado com sucesso</div>';
      }}
      </script></body></html>"""


@pytest.fixture
def pje():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"chromium indisponivel: {exc}")
        page = browser.new_page()
        estado: dict = {"aberturas": 0}

        def formulario(route):
            estado["aberturas"] += 1
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=_html(estado))

        def salvar(route):
            estado.update(json.loads(route.request.post_data))
            route.fulfill(status=200, body="ok")

        page.route(FORM, formulario)
        page.route("**/pje/salvar", salvar)
        yield page, estado
        browser.close()


COMPLETO = {"nome": "FULANA DE TAL", "nascimento": "01/01/1980", "oab": "123", "sexo": "F",
            "cep": "30000000", "telefone": "31999990000", "email": "antigo@exemplo.com"}


def test_cadastro_completo_troca_o_email_sem_chamar_o_operador(pje):
    page, estado = pje
    estado.update(COMPLETO)
    chamadas = []
    ok, detalhe = ajustar_email_cadastral(page, BASE, "novo@exemplo.com",
                                          completar=lambda m, v: chamadas.append(1) or True)
    assert ok and detalhe == "e-mail do PJe atualizado para o da planilha"
    assert estado["email"] == "novo@exemplo.com" and not chamadas


def test_email_ja_correto_nao_clica_em_nada(pje):
    page, estado = pje
    estado.update(COMPLETO, email="Novo@Exemplo.com")
    ok, detalhe = ajustar_email_cadastral(page, BASE, "novo@exemplo.com")
    assert ok and "ja esta correto" in detalhe and estado["aberturas"] == 1


def test_cadastro_incompleto_sem_operador_devolve_todas_as_mensagens(pje):
    page, estado = pje
    estado.update(email="antigo@exemplo.com")
    ok, detalhe = ajustar_email_cadastral(page, BASE, "novo@exemplo.com")
    assert not ok and estado["email"] == "antigo@exemplo.com"
    for rotulo in ROTULOS.values():  # todas as mensagens do PJe, nao so as 3 primeiras
        assert rotulo + "*" in detalhe
    assert "Campos obrigatórios" not in detalhe.split("(PJe:")[1].split(")")[0]  # legenda nao e mensagem
    vazios = detalhe.split("vazios no cadastro: ")[1]
    assert "Sexo" in vazios and "Nome Completo" in vazios and "E-mail" not in vazios


def test_cadastro_incompleto_operador_completa_e_o_robo_salva(pje):
    page, estado = pje
    estado.update(email="antigo@exemplo.com")
    visto = {}

    def operador(mensagens, vazios):
        visto["mensagens"], visto["vazios"] = list(mensagens), list(vazios)
        visto["aberturas"] = estado["aberturas"]
        visto["email_na_tela"] = page.input_value("#email")
        for campo, valor in (("nascimento", "01/01/1980"), ("oab", "123"), ("cep", "30000000"),
                             ("telefone", "31999990000")):
            page.fill("#" + campo, valor)
        page.select_option("#sexo", "F")
        return True  # nao clica em ATUALIZAR: o robo salva

    ok, detalhe = ajustar_email_cadastral(page, BASE, "novo@exemplo.com", completar=operador)
    assert ok and "com o operador" in detalhe
    assert estado["email"] == "novo@exemplo.com" and estado["oab"] == "123"
    assert visto["aberturas"] == 1  # a tela recusada nao foi recarregada antes de o operador digitar
    assert visto["email_na_tela"] == "novo@exemplo.com"
    assert "Data de Nascimento*" in visto["mensagens"] and "Campo obrigatório" not in visto["mensagens"]
    assert "Sexo" in visto["vazios"]


def test_operador_esquece_um_campo_e_o_que_digitou_nao_se_perde(pje):
    page, estado = pje
    estado.update(email="antigo@exemplo.com")
    rodadas = []

    def operador(mensagens, vazios):
        rodadas.append(list(vazios))
        if len(rodadas) == 1:
            for campo, valor in (("nascimento", "01/01/1980"), ("oab", "123"), ("cep", "30000000")):
                page.fill("#" + campo, valor)
            page.select_option("#sexo", "F")
        else:
            assert page.input_value("#oab") == "123"  # a 1a rodada continua na tela
            page.fill("#telefone", "31999990000")
        return True

    ok, _ = ajustar_email_cadastral(page, BASE, "novo@exemplo.com", completar=operador)
    assert ok and len(rodadas) == 2 and rodadas[1] == ["Nome Completo", "Telefone"]


def test_operador_pula_o_advogado(pje):
    page, estado = pje
    estado.update(email="antigo@exemplo.com")
    ok, detalhe = ajustar_email_cadastral(page, BASE, "novo@exemplo.com", completar=lambda m, v: False)
    assert not ok and "nao salvou" in detalhe and estado["email"] == "antigo@exemplo.com"
