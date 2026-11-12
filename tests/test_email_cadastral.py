"""Troca do e-mail em 'Alteracao de dados cadastrais' contra um PJe simulado.

Cadastro antigo sem telefone: o PJe recusa o ATUALIZAR enquanto o obrigatorio estiver vazio. O
robo preenche o telefone VAZIO com o da planilha junto com o e-mail, sem pausar e sem sobrescrever."""
import json
from types import SimpleNamespace

import pytest

from pje_rpa.credencial import ajustar_email_cadastral

BASE = "https://pje.teste.jus.br/pje/login.seam"
FORM = "https://pje.teste.jus.br/pje/PessoaAdvogado/AlteracaoCadastro/listView.seam"
RECORD = SimpleNamespace(
    nome_esperado="FULANA DE TAL", cpf_esperado="11122233344", oab_numero="123", oab_uf="MG",
    email="novo@exemplo.com", telefone="(31) 99999-0000",
    endereco=SimpleNamespace(cep="30000-000", logradouro="Rua A", numero="1", complemento="", bairro="Centro",
                             cidade="Belo Horizonte", uf="MG"))


def _html(estado: dict) -> str:
    v = lambda k: estado.get(k, "")  # noqa: E731
    estrela = "" if estado.get("sem_estrela") else "*"
    if estado.get("ddd_separado"):
        telefone = (f"<div><label for=cmpddd>DDD{estrela}</label><input id=cmpddd type=text value='{v('ddd')}'>"
                    f"<label for=cmptel>Telefone{estrela}</label><input id=cmptel type=text value='{v('telefone')}'></div>")
    else:
        telefone = f"<div><label for=tel>Telefone{estrela}</label><input id=tel type=text value='{v('telefone')}'></div>"
    return f"""<html><body><div class="alert-warning">* Campos obrigatórios</div>
      <div id=msgs></div>
      <div><label for=nome>Nome Completo*</label><input id=nome type=text disabled value='{v("nome")}'></div>
      <div><label for=nasc>Data de Nascimento*</label><input id=nasc type=text value='{v("nascimento")}'></div>
      <div><label for=sexo>Sexo*</label><select id=sexo><option value='noSelectionValue'>Selecione</option>
        <option value=F {'selected' if v('sexo') == 'F' else ''}>Feminino</option></select></div>
      {telefone}
      <div><label for=fax>Telefone comercial</label><input id=fax type=text value=''></div>
      <div><label for=email>E-mail*</label><input id=email type=text value='{v("email")}'></div>
      <input type=button value="ATUALIZAR" onclick="salvar()">
      <script>
      async function salvar() {{
        const pega = id => {{ const e = document.getElementById(id); return e ? e.value : null; }};
        const d = {{email: pega('email'), nascimento: pega('nasc'), sexo: pega('sexo'), ddd: pega('cmpddd'),
                   telefone: pega('cmptel') ?? pega('tel'), fax: pega('fax')}};
        if (d.sexo === 'noSelectionValue') d.sexo = '';
        const falta = [];
        if (!d.nascimento) falta.push('Data de Nascimento');
        if (!d.sexo) falta.push('Sexo');
        if (d.ddd === '') falta.push('DDD');
        if (!d.telefone) falta.push('Telefone');
        if (falta.length) {{
          msgs.innerHTML = '<div class="rich-messages">' + falta.map(c =>
            '<span class="rich-message">' + c + '*</span><span class="rich-message">Campo obrigatório</span>'
          ).join('') + '</div>';
          return;
        }}
        await fetch('/pje/salvar', {{method: 'POST', body: JSON.stringify(d)}});
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
        estado: dict = {"aberturas": 0, "salvamentos": 0}

        def formulario(route):
            estado["aberturas"] += 1
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=_html(estado))

        def salvar(route):
            estado["salvamentos"] += 1
            estado.update({k: v for k, v in json.loads(route.request.post_data).items() if v is not None})
            route.fulfill(status=200, body="ok")

        page.route(FORM, formulario)
        page.route("**/pje/salvar", salvar)
        yield page, estado
        browser.close()


COMPLETO = {"nome": "FULANA DE TAL", "nascimento": "01/01/1980", "sexo": "F", "telefone": "3133334444",
            "email": "antigo@exemplo.com"}


def test_cadastro_completo_troca_so_o_email(pje):
    page, estado = pje
    estado.update(COMPLETO)
    ok, detalhe = ajustar_email_cadastral(page, BASE, "novo@exemplo.com", record=RECORD)
    assert ok and detalhe == "e-mail do PJe atualizado para o da planilha"
    assert estado["email"] == "novo@exemplo.com"
    assert estado["telefone"] == "3133334444" and estado["fax"] == ""  # telefone do PJe nao e sobrescrito


def test_email_ja_correto_nao_clica_em_nada(pje):
    page, estado = pje
    estado.update(COMPLETO, email="Novo@Exemplo.com", telefone="")
    ok, detalhe = ajustar_email_cadastral(page, BASE, "novo@exemplo.com", record=RECORD)
    assert ok and "ja esta correto" in detalhe and estado["aberturas"] == 1 and estado["salvamentos"] == 0


def test_sem_telefone_e_sem_email_o_robo_preenche_os_dois(pje):
    """Caso real (TRE-MG): 'Usuario nao possui email cadastrado' e o cadastro sem telefone."""
    page, estado = pje
    estado.update(COMPLETO, email="", telefone="")
    ok, detalhe = ajustar_email_cadastral(page, BASE, "novo@exemplo.com", record=RECORD)
    assert ok and "telefone que faltava" in detalhe
    assert estado["email"] == "novo@exemplo.com" and estado["telefone"] == "31999990000"
    assert estado["fax"] == "" and estado["salvamentos"] == 1  # 'Telefone comercial' e outro assunto


def test_ddd_e_telefone_em_campos_separados(pje):
    page, estado = pje
    estado.update(COMPLETO, telefone="", ddd="", ddd_separado=True)
    ok, _ = ajustar_email_cadastral(page, BASE, "novo@exemplo.com", record=RECORD)
    assert ok and estado["ddd"] == "31" and estado["telefone"] == "999990000"


def test_ddd_ja_gravado_e_diferente_nao_completa_o_telefone(pje):
    page, estado = pje
    estado.update(COMPLETO, telefone="", ddd="11", ddd_separado=True)
    ok, detalhe = ajustar_email_cadastral(page, BASE, "novo@exemplo.com", record=RECORD)
    assert not ok and estado["telefone"] == "" and "Telefone*" in detalhe


def test_telefone_sem_estrela_e_preenchido_depois_da_recusa_do_pje(pje):
    page, estado = pje
    estado.update(COMPLETO, telefone="", sem_estrela=True)
    ok, detalhe = ajustar_email_cadastral(page, BASE, "novo@exemplo.com", record=RECORD)
    assert ok and "telefone que faltava" in detalhe and estado["telefone"] == "31999990000"


def test_sem_a_linha_da_planilha_nao_mexe_no_telefone(pje):
    page, estado = pje
    estado.update(COMPLETO, telefone="")
    ok, detalhe = ajustar_email_cadastral(page, BASE, "novo@exemplo.com")
    assert not ok and estado["telefone"] == "" and estado["email"] == "antigo@exemplo.com"
    assert "Telefone*" in detalhe and "vazios no cadastro: Telefone" in detalhe


def test_outros_obrigatorios_vazios_devolve_todas_as_mensagens(pje):
    """Cadastro em branco (sem data de nascimento, sexo...): o robo nao tem esses dados e nao inventa."""
    page, estado = pje
    estado.update(email="antigo@exemplo.com")
    ok, detalhe = ajustar_email_cadastral(page, BASE, "novo@exemplo.com", record=RECORD)
    assert not ok and estado["email"] == "antigo@exemplo.com" and estado["salvamentos"] == 0
    pje_disse = detalhe.split("(PJe: ")[1].split(")")[0]
    assert "Data de Nascimento*" in pje_disse and "Sexo*" in pje_disse  # nao corta em 3
    assert "Campos obrigatórios" not in pje_disse and "Campo obrigatório" not in pje_disse
    vazios = detalhe.split("vazios no cadastro: ")[1]
    assert "Sexo" in vazios and "Nome Completo" in vazios and "E-mail" not in vazios and not vazios.endswith(", ")
