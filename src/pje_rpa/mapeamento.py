"""Mapa do formulario de cadastro de cada PJe: que campos tem e quais sao obrigatorios.

O robo entra com o certificado de UM advogado (com autorizacao), chega ao formulario de cadastro,
le os campos e SAI: nao preenche, nao clica em "Confirmar dados" e nao assina nada. Onde o advogado
ja e cadastrado, o formulario de cadastro nao aparece; o robo le entao a tela "Alteracao de dados
cadastrais" (so le, nao salva), que tem quase os mesmos campos.

Saida (sem nenhum valor digitado ou trazido pelo PJe; nome, e-mail, endereco, telefone e numeros
do advogado sao mascarados nos rotulos e nas opcoes):
  tribunais\\formularios.csv           um campo por linha (tribunal, origem, rotulo, obrigatorio...)
  tribunais\\formularios-situacao.csv  um tribunal por linha (mapeado, ja cadastrado, falhou...)
Cada execucao substitui so as linhas dos tribunais mapeados nela.

A tabela alimenta exigencias.py: campo obrigatorio no formulario de cadastro, vazio quando o PJe
abriu e que vem da planilha (telefone, RG, estado civil...) passa a ser exigido naquele tribunal.

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

COLUNAS = ["tribunal", "nome", "url", "origem", "data", "ordem", "campo", "rotulo", "secao", "tipo",
           "obrigatorio", "veio_preenchido", "bloqueado", "opcoes"]
COLUNAS_SITUACAO = ["tribunal", "nome", "url", "data", "situacao", "origem", "campos", "obrigatorios", "motivo"]

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
            "rotulo": limpar(rotulo, palavras)[:100],
            "secao": limpar(c.get("section", ""), palavras)[:80],
            "tipo": tipo,
            "obrigatorio": "sim" if c.get("required") else "nao",
            "veio_preenchido": "sim" if preenchido else "nao",
            "bloqueado": "sim" if (c.get("readonly") or c.get("disabled")) else "nao",
            "opcoes": " | ".join(opcoes[:MAX_OPCOES]) + (f" | (+{sobra})" if sobra > 0 else ""),
        })
    return linhas


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
              agora: datetime | None = None) -> None:
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
                      "motivo": motivo[:300]})
    situacoes.sort(key=lambda l: l["tribunal"])
    _gravar(arquivo_situacao, COLUNAS_SITUACAO, situacoes)


def ler_tabela(tabela: Path | None = None) -> list[dict[str, str]]:
    return _ler(tabela or TABELA)


def situacao(resultado) -> str:
    mapa = getattr(resultado, "mapa", None) or {}
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
    opcoes = listar_tribunais()
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
        f"{len(escolhidos)} tribunal(is). Nada e preenchido, enviado nem assinado.")
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
