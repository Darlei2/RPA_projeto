"""Tribunais com um cadastro por grau e uma credencial so.

Na Justica do Trabalho (PJe-JT) o 1o e o 2o grau sao sistemas separados no mesmo endereco
(/primeirograu e /segundograu). O advogado precisa do cadastro nos DOIS, e a credencial so
serve se a senha for a MESMA nos dois. Por isso, para esses tribunais o robo:

  1. sempre faz os dois graus juntos, o 1o antes do 2o (pedir um traz o outro);
  2. usa no segundo a senha que ficou no primeiro;
  3. nao segue para o outro grau do advogado se o primeiro ficou sem credencial pronta.

Para incluir outra familia de tribunais na regra, acrescente o padrao do endereco em
ENDERECOS_COM_DOIS_GRAUS: o grupo 1 e o nome do tribunal e o grupo 2 e o trecho do grau.
"""
from __future__ import annotations

import re

# endereco de login -> (tribunal, grau). Um padrao por familia de tribunais.
ENDERECOS_COM_DOIS_GRAUS = [
    re.compile(r"^https://pje\.(trt\d+)\.jus\.br/(primeirograu|segundograu)(?:/|$)", re.I),
]
_GRAU = {"primeirograu": "1G", "segundograu": "2G"}
ORDEM = ("1G", "2G")


def _partes(url: str) -> tuple[str, str]:
    for padrao in ENDERECOS_COM_DOIS_GRAUS:
        achou = padrao.match((url or "").strip())
        if achou:
            return achou.group(1).upper(), _GRAU.get(achou.group(2).lower(), "")
    return "", ""


def grupo(url: str) -> str:
    """'TRT5' para os dois graus do TRT5; '' para tribunal fora da regra."""
    return _partes(url)[0]


def grau(url: str) -> str:
    """'1G' ou '2G' para tribunal da regra; '' fora dela."""
    return _partes(url)[1]


def completar(escolhidos: list, opcoes: list) -> tuple[list, list[str], list[str]]:
    """Aplica a regra 1 a lista de tribunais escolhidos (objetos com .url e .nome).

    Devolve (lista final, nomes incluidos pela regra, grupos sem o arquivo do outro grau).
    Cada tribunal da regra vira o par 1o grau + 2o grau, nessa ordem, na posicao em que foi
    pedido. Grupo sem o arquivo do outro grau sai da lista: fazer um grau so nao gera credencial.
    """
    final: list = []
    incluidos: list[str] = []
    incompletos: list[str] = []
    for opcao in escolhidos:
        nome_grupo = grupo(getattr(opcao, "url", ""))
        if not nome_grupo:
            if opcao not in final:
                final.append(opcao)
            continue
        par = {grau(o.url): o for o in opcoes if grupo(getattr(o, "url", "")) == nome_grupo}
        if any(g not in par for g in ORDEM):
            if nome_grupo not in incompletos:
                incompletos.append(nome_grupo)
            continue
        for g in ORDEM:
            if par[g] not in final:
                final.append(par[g])
                if par[g] not in escolhidos:
                    incluidos.append(par[g].nome)
    return final, incluidos, incompletos


def so_grupos_inteiros(escolhidos: list) -> tuple[list, list[str]]:
    """Tira da lista os tribunais da regra cujo par nao esta inteiro nela (ex.: um dos graus
    sem envio real liberado). Devolve (lista, grupos retirados)."""
    presentes: dict[str, set[str]] = {}
    for opcao in escolhidos:
        nome_grupo = grupo(getattr(opcao, "url", ""))
        if nome_grupo:
            presentes.setdefault(nome_grupo, set()).add(grau(opcao.url))
    fora = [g for g, graus_ in presentes.items() if any(x not in graus_ for x in ORDEM)]
    return [o for o in escolhidos if grupo(getattr(o, "url", "")) not in fora], fora
