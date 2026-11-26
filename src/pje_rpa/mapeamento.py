"""Mapa do formulario de cadastro de cada PJe: que campos tem e quais sao obrigatorios.

O robo entra com o certificado de UM advogado (com autorizacao), chega ao formulario de cadastro e
le os campos. Como muitos PJe (os TRTs, por exemplo) so dizem o que e obrigatorio DEPOIS do clique em
"Confirmar dados", o robo faz esse clique COM TRAVA: apaga so na tela o e-mail (e a confirmacao), que
todo PJe exige, clica, le os avisos de "campo obrigatorio" do formulario devolvido e sai. Com o e-mail
vazio o PJe recusa o envio e nada e gravado. Se mesmo assim o formulario passar para o termo de
compromisso, o robo para ali (nao assina), avisa e interrompe o mapeamento para revisao.
Sem campo de e-mail editavel para travar, o clique nao e feito (aviso_ao_enviar = nao_testado).
Onde o advogado ja e cadastrado, o formulario de cadastro nao aparece; o robo le entao a tela
"Alteracao de dados cadastrais" (so le; ali nao clica em nada, porque salvar alteraria o cadastro).

Saida (sem nenhum valor digitado ou trazido pelo PJe; nome, e-mail, endereco, telefone e numeros
do advogado sao mascarados nos rotulos e nas opcoes):
  tribunais\\formularios.csv           um campo por linha (tribunal, origem, rotulo, obrigatorio...)
  tribunais\\formularios-situacao.csv  um tribunal por linha (mapeado, ja cadastrado, falhou...)
Cada execucao substitui so as linhas dos tribunais mapeados nela.

A tabela alimenta exigencias.py: campo obrigatorio no formulario de cadastro (marcado na tela ou
cobrado no envio com trava), vazio quando o PJe abriu e que vem da planilha (telefone, RG, estado
civil...) passa a ser exigido naquele tribunal.

  python -m pje_rpa mapear --tribunal trt                  (familia: trt, trf, tj, tre ou todos)
  python -m pje_rpa mapear --tribunal tjmt,tjmt-2g --planilha planilhas\\advogados.xlsx --linha 2
"""
from __future__ import annotations

import csv
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PASTA = Path(__file__).resolve().parents[2] / "tribunais"
TABELA = PASTA / "formularios.csv"
SITUACAO = PASTA / "formularios-situacao.csv"

# obrigatorio = marcado na tela antes do envio OU cobrado pelo PJe no envio com trava
COLUNAS = ["tribunal", "nome", "url", "origem", "data", "ordem", "campo", "identificador", "rotulo", "secao",
           "tipo", "obrigatorio", "marcado_na_tela", "aviso_ao_enviar", "veio_preenchido", "bloqueado", "opcoes"]
COLUNAS_SITUACAO = ["tribunal", "nome", "url", "data", "situacao", "origem", "campos", "obrigatorios",
                    "avisos_do_pje", "motivo"]
NAO_TESTADO = "nao_testado"

# Origem das linhas da tabela
CADASTRO = "cadastro"                 # formulario de primeiro acesso (o que vale para as exigencias)
DADOS_CADASTRAIS = "dados_cadastrais"  # "Alteracao de dados cadastrais" de quem ja e cadastrado
TELA_DESCONHECIDA = "tela_nao_reconhecida"

FAMILIAS = {
    "trt": r"^trt\d", "trts": r"^trt\d",
    "trf": r"^trf\d", "trfs": r"^trf\d",
    "tj": r"^tj", "tjs": r"^tj",
    "tre": r"^tre[a-z]{2}(?:-|$)", "tres": r"^tre[a-z]{2}(?:-|$)",
    "todos": r".", "todas": r".",
}
MAX_OPCOES = 20


# ------------------------------------------------------------------- leitura da tela (sem valores)
def _ascii(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in texto if not unicodedata.combining(c)).casefold()


# Palavras de endereco que tambem sao rotulos de campo: nao sao mascaradas.
GENERICAS = {"rua", "avenida", "travessa", "alameda", "rodovia", "estrada", "praca", "endereco", "bairro",
             "centro", "jardim", "vila", "setor", "quadra", "lote", "casa", "apto", "apartamento", "sala",
             "bloco", "edificio", "andar", "fundos", "loja", "conjunto", "residencial", "parque", "numero"}


def _palavras_do_advogado(record: Any) -> set[str]:
    """Palavras que identificam o advogado: nao podem ir para a tabela (que vai para o Git)."""
    valores = [getattr(record, "nome_esperado", ""), getattr(record, "rg_orgao", "")]
    endereco = getattr(record, "endereco", None)
    if endereco is not None:
        valores += [endereco.logradouro, endereco.complemento, endereco.bairro]
    palavras = set()
    for valor in valores:
        palavras |= {p for p in re.findall(r"\w+", _ascii(valor)) if len(p) > 3}
    email = _ascii(getattr(record, "email", ""))
    if email:
        palavras.add(email)
        palavras |= {p for p in re.findall(r"\w+", email.split("@")[0]) if len(p) > 3}
    return palavras - GENERICAS


def limpar(texto: Any, palavras: set[str]) -> str:
    """Texto da tela sem dados pessoais: e-mails, sequencias de numeros e palavras do advogado."""
    texto = re.sub(r"\s+", " ", str(texto or "")).strip()
    texto = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "***", texto)
    texto = re.sub(r"\d[\d.\-/ ]{2,}\d", "***", texto)
    if palavras:
        texto = re.sub(r"\w+", lambda m: "***" if _ascii(m.group(0)) in palavras else m.group(0), texto)
    return texto


def campos_do_formulario(snapshot: dict, record: Any) -> list[dict[str, str]]:
    """Controles visiveis do formulario (de adaptive_form.scan) como linhas da tabela, SEM valores."""
    from .adaptive_form import field_key, has_value, other_subject

    palavras = _palavras_do_advogado(record)
    linhas = []
    for ordem, c in enumerate(snapshot.get("controls", []) or [], start=1):
        chave, _ = field_key(c)
        if chave and other_subject(chave, c):
            chave = "outro:" + chave   # ex.: "UF" do orgao expedidor do RG (nao e o endereco)
        rotulo = c.get("label") or c.get("placeholder") or c.get("title") or c.get("name") or c.get("id") or ""
        tipo = c.get("tag") if c.get("tag") in ("select", "textarea") else (c.get("type") or c.get("tag") or "")
        opcoes = []
        for o in c.get("options") or []:
            texto = limpar(o.get("label", ""), palavras)
            if texto and "***" not in texto and not re.match(r"(?i)^\W*(selecione|escolha|--)", texto):
                opcoes.append(texto)
        sobra = len(opcoes) - MAX_OPCOES
        preenchido = bool(c.get("checked")) if tipo in ("checkbox", "radio") else has_value(c)
        linhas.append({
            "ordem": str(ordem),
            "campo": chave or "",
            "identificador": limpar(c.get("id") or c.get("name") or "", palavras)[:80],
            "rotulo": limpar(rotulo, palavras)[:100],
            "secao": limpar(c.get("section", ""), palavras)[:80],
            "tipo": tipo,
            "obrigatorio": "sim" if c.get("required") else "nao",
            "marcado_na_tela": "sim" if c.get("required") else "nao",
            "aviso_ao_enviar": NAO_TESTADO,
            "veio_preenchido": "sim" if preenchido else "nao",
            "bloqueado": "sim" if (c.get("readonly") or c.get("disabled")) else "nao",
            "opcoes": " | ".join(opcoes[:MAX_OPCOES]) + (f" | (+{sobra})" if sobra > 0 else ""),
        })
    return linhas


# Apaga (so na tela, sem salvar) os controles indicados e avisa o formulario, como se o operador
# tivesse apagado. Os indices sao os do adaptive_form.scan (input, select e textarea na ordem da pagina).
JS_ESVAZIAR = r"""(indices) => {
  const all = [...document.querySelectorAll('input,select,textarea')];
  let feitos = 0;
  for (const i of indices) {
    const e = all[i];
    if (!e || e.readOnly || e.disabled) continue;
    e.focus();
    e.value = '';
    for (const tipo of ['input', 'change', 'blur']) e.dispatchEvent(new Event(tipo, {bubbles: true}));
    if (e.value === '') feitos += 1;
  }
  return feitos;
}"""


def indices_do_email(snapshot: dict) -> list[int]:
    """Campos de e-mail (e confirmacao) editaveis: apaga-los garante que o PJe recuse o envio."""
    from .adaptive_form import field_key

    indices = []
    for c in snapshot.get("controls", []) or []:
        chave, _ = field_key(c)
        if chave in ("email", "email_confirm") and not (c.get("readonly") or c.get("disabled")):
            indices.append(c["index"])
    return indices


def juntar_envio(antes: list[dict], depois: list[dict], avisos: list[str]) -> list[dict]:
    """Junta a leitura antes do clique com a do formulario devolvido pelo PJe: campo marcado como
    obrigatorio depois do envio (aviso "campo obrigatorio" na linha) ou citado num aviso do PJe vira
    obrigatorio. Campo que so apareceu depois do envio entra no fim."""
    citados = set()
    for aviso in avisos:
        if "obrigat" in _ascii(aviso):
            for trecho in re.split(r"[|;\n]", aviso):
                rotulo = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", _ascii(trecho.split(":")[0]))).strip()
                if rotulo and "obrigat" not in rotulo:
                    citados.add(rotulo)

    def chave(c):
        return c["identificador"] or (c["rotulo"], c["tipo"])

    def citado(c):
        rotulo = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", _ascii(c["rotulo"]))).strip()
        return bool(rotulo) and any(rotulo == r or (len(r) > 3 and r in rotulo) for r in citados)

    por_chave = {chave(c): c for c in depois}
    usados = set()
    saida = []
    for c in antes:
        d = por_chave.get(chave(c))
        if d is not None:
            usados.add(chave(c))
        avisado = (d is not None and d["obrigatorio"] == "sim" and c["marcado_na_tela"] != "sim") or citado(c)
        aviso = "sim" if avisado else "nao"
        saida.append({**c, "aviso_ao_enviar": aviso,
                      "obrigatorio": "sim" if (c["marcado_na_tela"] == "sim" or aviso == "sim") else "nao"})
    for d in depois:
        if chave(d) in usados:
            continue
        aviso = "sim" if (d["obrigatorio"] == "sim" or citado(d)) else "nao"
        saida.append({**d, "ordem": str(len(saida) + 1), "marcado_na_tela": "nao", "aviso_ao_enviar": aviso,
                      "obrigatorio": aviso})
    return saida


def url_dados_cadastrais(base_url: str) -> list[str]:
    """Enderecos possiveis da tela 'Alteracao de dados cadastrais' do advogado ja cadastrado: no
    contexto do proprio PJe (/primeirograu, /1g, /pje2grau...) e, se nao abrir, em /pje."""
    partes = urlparse(base_url)
    caminho = [p for p in partes.path.split("/") if p]
    contextos = []
    if len(caminho) > 1:
        contextos.append("/" + "/".join(caminho[:-1]))
    contextos.append("/pje")
    vistos = []
    for contexto in contextos:
        url = f"{partes.scheme}://{partes.netloc}{contexto}/PessoaAdvogado/AlteracaoCadastro/listView.seam"
        if url not in vistos:
            vistos.append(url)
    return vistos


# ------------------------------------------------------------------- tabela
def _ler(caminho: Path) -> list[dict[str, str]]:
    try:
        with caminho.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f, delimiter=";"))
    except FileNotFoundError:
        return []


def _gravar(caminho: Path, colunas: list[str], linhas: list[dict[str, str]]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(".tmp")
    with temporario.open("w", encoding="utf-8-sig", newline="") as f:
        saida = csv.DictWriter(f, fieldnames=colunas, delimiter=";", extrasaction="ignore")
        saida.writeheader()
        saida.writerows(linhas)
    temporario.replace(caminho)


def registrar(chave: str, config: Any, situacao: str, motivo: str, mapa: dict | None,
              tabela: Path | None = None, arquivo_situacao: Path | None = None,
              agora: datetime | None = None, avisos: list[str] | None = None) -> None:
    """Substitui, nas duas tabelas, o que havia deste tribunal pelo resultado desta execucao.
    Sem campos lidos (falha antes do formulario), as linhas de campos antigas ficam."""
    tabela = tabela or TABELA
    arquivo_situacao = arquivo_situacao or SITUACAO
    data = (agora or datetime.now()).strftime("%d/%m/%Y")
    mapa = mapa or {}
    campos = mapa.get("campos") or []
    origem = mapa.get("origem", "")
    base = {"tribunal": chave, "nome": config.tribunal, "url": config.base_url, "data": data}
    if campos:
        linhas = [l for l in _ler(tabela) if l.get("tribunal") != chave]
        linhas += [{**base, "origem": origem, **c} for c in campos]
        linhas.sort(key=lambda l: (l["tribunal"], l["origem"], int(l.get("ordem") or 0)))
        _gravar(tabela, COLUNAS, linhas)
    situacoes = [l for l in _ler(arquivo_situacao) if l.get("tribunal") != chave]
    situacoes.append({**base, "situacao": situacao, "origem": origem, "campos": str(len(campos)),
                      "obrigatorios": str(sum(c["obrigatorio"] == "sim" for c in campos)),
                      "avisos_do_pje": " | ".join(avisos or mapa.get("avisos") or [])[:500],
                      "motivo": motivo[:300]})
    situacoes.sort(key=lambda l: l["tribunal"])
    _gravar(arquivo_situacao, COLUNAS_SITUACAO, situacoes)


def ler_tabela(tabela: Path | None = None) -> list[dict[str, str]]:
    return _ler(tabela or TABELA)


def situacao(resultado) -> str:
    mapa = getattr(resultado, "mapa", None) or {}
    if mapa.get("passou_para_termo"):
        return "PASSOU_PARA_O_TERMO"
    if mapa.get("campos") and mapa.get("origem") == CADASTRO:
        return "MAPEADO"
    if resultado.status == "ALREADY_REGISTERED":
        return "JA_CADASTRADO" + ("_DADOS_CADASTRAIS_MAPEADOS" if mapa.get("campos") else "_SEM_FORMULARIO")
    if mapa.get("campos"):
        return "TELA_NAO_RECONHECIDA_MAPEADA"
    return resultado.status


# ------------------------------------------------------------------- execucao
def escolher(opcoes: list, texto: str) -> tuple[list, list[str]]:
    """Tribunais por chave/nome (como no executar) ou por familia: trt, trf, tj, tre, todos."""
    from .lote import escolher_tribunais

    escolhidos, invalidos = [], []
    resto = []
    for parte in [p for p in re.split(r"[,;\s]+", texto or "") if p]:
        padrao = FAMILIAS.get(parte.casefold())
        if padrao:
            escolhidos += [o for o in opcoes if re.search(padrao, o.chave) and o not in escolhidos]
        else:
            resto.append(parte)
    if resto:
        outros, invalidos = escolher_tribunais(opcoes, ",".join(resto))
        escolhidos += [o for o in outros if o not in escolhidos]
    return escolhidos, invalidos


def _parar_tudo(resultado) -> str:
    """Resultado que pede para nao seguir para o proximo tribunal (evita bloquear o login unico)."""
    if (getattr(resultado, "mapa", None) or {}).get("passou_para_termo"):
        return ("o formulario passou para o termo de compromisso mesmo com o e-mail vazio (nada foi "
                "assinado). Confira o cadastro neste tribunal antes de continuar")
    if resultado.status == "BLOCKED":
        return "bloqueio no tribunal: " + resultado.reason
    if "segundo fator" in _ascii(resultado.reason) and resultado.status == "NEEDS_HUMAN":
        return "o codigo 2FA nao foi aceito: " + resultado.reason
    return ""


def run_mapear(tribunal: str = "", planilha: str = "", linha: int = 0, pausa: float = 5.0,
               audit_root: Path = Path("runtime/audit"), tabela: Path | None = None,
               arquivo_situacao: Path | None = None, deps=None) -> int:
    """0 = todos mapeados ou ja cadastrados com dados lidos; 1 = cancelado; 2 = nada para fazer;
    3 = algum tribunal sem mapa; 4 = parou no meio (bloqueio ou 2FA recusado)."""
    from .config import load_config
    from .lote import _chave_2fa, _code_provider, _dependencias_padrao, _ler_advogados, listar_tribunais

    deps = deps or _dependencias_padrao()
    log = deps.log
    tabela, arquivo_situacao = tabela or TABELA, arquivo_situacao or SITUACAO
    from .lote import avisar_problemas

    erros: list[str] = []
    opcoes = listar_tribunais(erros=erros)
    avisar_problemas(opcoes, erros, log)
    opcoes = [o for o in opcoes if not o.problemas]
    escolhidos, invalidos = escolher(opcoes, tribunal) if tribunal else ([], [])
    while not escolhidos:
        if invalidos:
            log("Nao reconhecido(s): " + ", ".join(invalidos))
        escolhidos, invalidos = escolher(opcoes, deps.ask(
            "Quais tribunais mapear? (ex.: trt, tre, tj, trf, todos ou tjmt,tjmt-2g): "))
    validas, codigo = _ler_advogados(planilha, deps)
    if codigo is not None:
        return codigo
    if linha:
        validas = [l for l in validas if l.numero_linha == linha]
        if not validas:
            log(f"Linha {linha} nao esta entre os advogados prontos da planilha.")
            return 2
    while len(validas) > 1:
        resposta = deps.ask("Mapear com o certificado de qual linha da planilha? ").strip()
        validas = [l for l in validas if str(l.numero_linha) == resposta] or validas
    advogado = validas[0]
    log(f"\nMAPEAMENTO com o certificado de {advogado.nome} (linha {advogado.numero_linha}) em "
        f"{len(escolhidos)} tribunal(is). Nada e preenchido nem assinado; o envio e feito com o e-mail "
        "apagado na tela, para o PJe recusar e mostrar o que exige.")
    if not deps.yes("Comecar?", True):
        log("Cancelado.")
        return 1

    secret_2fa = _chave_2fa(advogado, deps)
    pfx, senha_pfx = None, ""
    if not advogado.usa_windows:
        pfx = advogado.certificado.read_bytes()
        senha_pfx = advogado.segredo("senha_certificado") or deps.secret(f"Senha do certificado A1 de {advogado.nome}")
    audit_root.mkdir(parents=True, exist_ok=True)
    contagem: dict[str, int] = {}
    codigo_saida = 0
    try:
        for i, opcao in enumerate(escolhidos, start=1):
            config = load_config(opcao.caminho)
            log(f"\n===== {i}/{len(escolhidos)} {config.tribunal} ({config.base_url}) =====")
            kwargs = {"rehearsal": True, "notify": deps.log, "thumbprint": advogado.thumbprint, "mapear": True}
            if deps.store_factory:
                kwargs["store"] = deps.store_factory()
            resultado = deps.cadastrar(config, advogado.record, pfx, senha_pfx,
                                       _code_provider(secret_2fa, deps), audit_root, **kwargs)
            estado = situacao(resultado)
            registrar(opcao.chave, config, estado, resultado.reason, getattr(resultado, "mapa", None),
                      tabela, arquivo_situacao)
            contagem[estado] = contagem.get(estado, 0) + 1
            campos = (getattr(resultado, "mapa", None) or {}).get("campos") or []
            log(f"{estado}: {len(campos)} campo(s), {sum(c['obrigatorio'] == 'sim' for c in campos)} obrigatorio(s)"
                + (f" - {resultado.reason}" if not campos else ""))
            if estado not in ("MAPEADO", "JA_CADASTRADO_DADOS_CADASTRAIS_MAPEADOS"):
                codigo_saida = 3
            if motivo := _parar_tudo(resultado):
                log(f"\nPAREI AQUI para nao bloquear o acesso: {motivo}. Os proximos tribunais nao foram tentados.")
                return 4
            if i < len(escolhidos) and pausa > 0:
                time.sleep(pausa)
    finally:
        senha_pfx = ""
        secret_2fa = ""
        log("\nResumo: " + ", ".join(f"{k} {v}" for k, v in sorted(contagem.items())))
        log(f"Tabela: {tabela}\nSituacao por tribunal: {arquivo_situacao}")
    return codigo_saida


# ------------------------------------------------------------------- mapeamento assistido (fora do PJe)
# eproc, e-SAJ, Projudi e sistemas proprios: o robo ainda nao sabe navegar neles. No mapeamento
# assistido ele so ABRE a pagina de cadastro e LE o formulario; quem navega e o operador (Cadastre-se
# aqui > Cadastrar advogado > Cadastro com certificado digital > escolhe o certificado). O robo nao
# clica, nao preenche e nao envia nada. Se o operador quiser ver o que o tribunal cobra no envio, ele
# mesmo apaga o e-mail na tela e clica em Finalizar; o robo so le os avisos que aparecerem.
OUTROS_SISTEMAS = PASTA / "outros-sistemas.csv"
ASSISTIDO = "MAPEADO_ASSISTIDO"


def outros_sistemas(caminho: Path | None = None) -> list[dict[str, str]]:
    """Tribunais fora do PJe: {'tribunal','nome','sistema','url'} (linhas com # sao comentario)."""
    caminho = caminho or OUTROS_SISTEMAS
    try:
        texto = caminho.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return []
    linhas = [l for l in texto.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    return [dict(d) for d in csv.DictReader(linhas, delimiter=";")]


def escolher_outros(opcoes: list[dict[str, str]], texto: str) -> tuple[list[dict], list[str]]:
    """Por chave (tjsc-eproc-1g) ou pelo sistema inteiro (eproc, esaj, projudi, proprio)."""
    escolhidos, invalidos = [], []
    for parte in [p.casefold() for p in re.split(r"[,;\s]+", texto or "") if p]:
        achados = [o for o in opcoes if parte in (o["tribunal"], o["sistema"])]
        if not achados:
            invalidos.append(parte)
        escolhidos += [o for o in achados if o not in escolhidos]
    return escolhidos, invalidos


class NavegadorAssistido:
    """Edge aberto para o operador: guarda os avisos (alert/confirm) das paginas e sempre le a aba
    mais recente (o cadastro com certificado pode abrir outra aba)."""

    def __init__(self, config) -> None:
        from .browser import PlaywrightBrowser

        self._browser = PlaywrightBrowser(config)
        self.avisos: list[str] = []
        self._vigiar(self._browser.page)
        try:
            self._browser._context.on("page", self._vigiar)
        except Exception:  # noqa: BLE001
            pass

    def _vigiar(self, page) -> None:
        def anotar(dialogo) -> None:
            try:
                self.avisos.append(str(dialogo.message or ""))
            finally:
                try:
                    dialogo.dismiss()   # so fecha o aviso; nunca confirma nada
                except Exception:  # noqa: BLE001
                    pass
        try:
            page.on("dialog", anotar)
        except Exception:  # noqa: BLE001
            pass

    def abrir(self, url: str) -> None:
        self._browser.open(url)

    def pagina(self):
        try:
            paginas = [p for p in self._browser._context.pages if not p.is_closed()]
            return paginas[-1] if paginas else self._browser.page
        except Exception:  # noqa: BLE001
            return self._browser.page

    def fechar(self) -> None:
        self._browser.close()


def _escolher_advogado(planilha: str, linha: int, deps):
    from .lote import _ler_advogados

    validas, codigo = _ler_advogados(planilha, deps)
    if codigo is not None:
        return None, codigo
    if linha:
        validas = [l for l in validas if l.numero_linha == linha]
        if not validas:
            deps.log(f"Linha {linha} nao esta entre os advogados prontos da planilha.")
            return None, 2
    while len(validas) > 1:
        resposta = deps.ask("Mapear com o certificado de qual linha da planilha? ").strip()
        validas = [l for l in validas if str(l.numero_linha) == resposta] or validas
    return validas[0], None


def run_mapear_tela(tribunal: str = "", planilha: str = "", linha: int = 0, tabela: Path | None = None,
                    arquivo_situacao: Path | None = None, deps=None, navegador=None,
                    opcoes: list[dict[str, str]] | None = None) -> int:
    """Mapeamento assistido. 0 = todos lidos; 1 = cancelado; 2 = nada para fazer; 3 = algum ficou sem mapa."""
    from dataclasses import replace

    from .adaptive_form import page_messages, scan
    from .config import load_config
    from .lote import _dependencias_padrao

    deps = deps or _dependencias_padrao()
    log = deps.log
    tabela, arquivo_situacao = tabela or TABELA, arquivo_situacao or SITUACAO
    opcoes = outros_sistemas() if opcoes is None else opcoes
    escolhidos, invalidos = escolher_outros(opcoes, tribunal) if tribunal else ([], [])
    while not escolhidos:
        if invalidos:
            log("Nao reconhecido(s): " + ", ".join(invalidos))
        escolhidos, invalidos = escolher_outros(opcoes, deps.ask(
            "Qual tribunal? (ex.: tjsc-eproc-1g; veja tribunais\\outros-sistemas.csv): "))
    advogado, codigo = _escolher_advogado(planilha, linha, deps)
    if advogado is None:
        return codigo
    palavras = _palavras_do_advogado(advogado.record)
    base = load_config(PASTA / "_comum.toml")
    log(f"\nMAPEAMENTO ASSISTIDO com o certificado de {advogado.nome} (linha {advogado.numero_linha}) em "
        f"{len(escolhidos)} tribunal(is). O robo so abre a pagina e le o formulario: quem navega e voce.")
    if not deps.yes("Comecar?", True):
        log("Cancelado.")
        return 1
    codigo_saida = 0
    for i, opcao in enumerate(escolhidos, start=1):
        host = (urlparse(opcao["url"]).hostname or "").lower()
        config = replace(base, tribunal=opcao["nome"], base_url=opcao["url"], tribunal_id=opcao["tribunal"],
                         identidade_origem="outros-sistemas", hosts_autorizados=(host,),
                         endereco_padrao=opcao["url"])
        log(f"\n===== {i}/{len(escolhidos)} {opcao['nome']} ({opcao['url']}) =====")
        from .enderecos import problemas as problemas_do_endereco

        recusado = problemas_do_endereco(config)
        if recusado:
            log("Endereco recusado (" + "; ".join(recusado) + "): corrija tribunais\\outros-sistemas.csv.")
            registrar(opcao["tribunal"], config, "PULADO", "endereco recusado", {}, tabela, arquivo_situacao)
            codigo_saida = 3
            continue
        nav = navegador(config) if navegador else NavegadorAssistido(config)
        try:
            nav.abrir(opcao["url"])
            log("No Edge aberto, faca o caminho ate o formulario de cadastro do advogado (no eproc: Cadastre-se "
                f"aqui > Cadastrar advogado > Cadastro com certificado digital > certificado de {advogado.nome}).")
            log("NAO clique em Finalizar/Enviar. O robo nao clica nem preenche nada.")
            if deps.ask("Com o formulario na tela, tecle Enter (P = pular este tribunal): ").strip().casefold() == "p":
                registrar(opcao["tribunal"], config, "PULADO", "pulado pelo operador", {}, tabela, arquivo_situacao)
                codigo_saida = 3
                continue
            campos = campos_do_formulario(scan(nav.pagina()), advogado.record)
            if len(campos) < 3:
                log(f"So {len(campos)} campo(s) na tela: nao parece o formulario de cadastro.")
            log(f"Formulario lido: {len(campos)} campo(s), {sum(c['obrigatorio'] == 'sim' for c in campos)} "
                "marcado(s) como obrigatorio(s).")
            log("Para ver o que o tribunal cobra no envio: apague SO o e-mail na tela, clique em Finalizar voce "
                "mesmo e tecle Enter aqui (com o e-mail vazio o tribunal recusa). Para terminar sem isso, digite F.")
            avisos: list[str] = []
            if deps.ask("Enter = ja tentei o envio com o e-mail vazio; F = terminar: ").strip().casefold() != "f":
                pagina = nav.pagina()
                avisos = [limpar(a, palavras) for a in list(nav.avisos) + page_messages(pagina) if a]
                depois = campos_do_formulario(scan(pagina), advogado.record)
                campos = juntar_envio(campos, depois, avisos)
                log(f"Depois do envio: {sum(c['aviso_ao_enviar'] == 'sim' for c in campos)} campo(s) cobrado(s); "
                    f"{len(avisos)} aviso(s) do tribunal.")
            estado = ASSISTIDO if len(campos) >= 3 else "TELA_NAO_RECONHECIDA_MAPEADA"
            if estado != ASSISTIDO:
                codigo_saida = 3
            registrar(opcao["tribunal"], config, estado, f"mapeamento assistido ({opcao['sistema']})",
                      {"origem": CADASTRO, "campos": campos, "avisos": avisos[:20]}, tabela, arquivo_situacao)
            log(f"{estado}: {len(campos)} campo(s), {sum(c['obrigatorio'] == 'sim' for c in campos)} obrigatorio(s)")
        except Exception as exc:  # noqa: BLE001
            log(f"FALHOU: {type(exc).__name__}")
            registrar(opcao["tribunal"], config, "FAILED", f"falha no mapeamento assistido ({type(exc).__name__})",
                      {}, tabela, arquivo_situacao)
            codigo_saida = 3
        finally:
            try:
                nav.fechar()
            except Exception:  # noqa: BLE001
                pass
    log(f"\nTabela: {tabela}\nSituacao por tribunal: {arquivo_situacao}")
    return codigo_saida
