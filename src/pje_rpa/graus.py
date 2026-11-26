"""Tribunais com um cadastro por grau e uma credencial so.

Na Justica do Trabalho (PJe-JT) o 1o e o 2o grau sao sistemas separados, mas o advogado precisa
do cadastro nos DOIS e a credencial so serve se a senha for a MESMA nos dois. Por isso, para os
tribunais de um mesmo grupo_credencial o robo:

  1. sempre faz os dois graus juntos, o 1o antes do 2o (pedir um traz o outro);
  2. usa no segundo a senha que ficou no primeiro;
  3. nao segue para o outro grau do advogado se o primeiro ficou sem credencial pronta.

A regra vem da IDENTIDADE do tribunal (registro.py: grau e grupo_credencial em
tribunais\\_registro.toml), nunca do endereco de login: mudar o base_url nao muda o grupo nem o
grau. Para incluir outra familia na regra, de o mesmo grupo_credencial ao 1G e ao 2G no registro.

Os argumentos (alvo) sao qualquer objeto com a identidade: AppConfig, OpcaoTribunal ou
registro.Tribunal. Texto e aceito so por compatibilidade com registros antigos (ver
registro.identidade).
"""
from __future__ import annotations

from .registro import identidade

ORDEM = ("1G", "2G")


def grupo(alvo) -> str:
    """'TRT5' para os dois graus do TRT5; '' para tribunal sem credencial compartilhada."""
    return identidade(alvo).grupo_credencial


def grau(alvo) -> str:
    """'1G', '2G' ou '' (instancia unica), pela identidade do tribunal."""
    return identidade(alvo).grau


def completar(escolhidos: list, opcoes: list) -> tuple[list, list[str], list[str]]:
    """Aplica a regra 1 a lista de tribunais escolhidos (OpcaoTribunal ou equivalentes, com .nome).

    Devolve (lista final, nomes incluidos pela regra, grupos sem o arquivo do outro grau).
    Cada tribunal da regra vira o par 1o grau + 2o grau, nessa ordem, na posicao em que foi
    pedido. Grupo sem o arquivo do outro grau sai da lista: fazer um grau so nao gera credencial.
    """
    final: list = []
    incluidos: list[str] = []
    incompletos: list[str] = []
    for opcao in escolhidos:
        nome_grupo = grupo(opcao)
        if not nome_grupo:
            if opcao not in final:
                final.append(opcao)
            continue
        par: dict[str, object] = {}
        for o in opcoes:
            if grupo(o) == nome_grupo:
                par.setdefault(grau(o), o)
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
        nome_grupo = grupo(opcao)
        if nome_grupo:
            presentes.setdefault(nome_grupo, set()).add(grau(opcao))
    fora = [g for g, graus_ in presentes.items() if any(x not in graus_ for x in ORDEM)]
    return [o for o in escolhidos if grupo(o) not in fora], fora
