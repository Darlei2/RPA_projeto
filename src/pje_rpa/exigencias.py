"""Dados da planilha que so alguns tribunais exigem no cadastro.

A planilha tem poucas colunas obrigatorias para todos; o resto depende do formulario de cada
tribunal. Quando ja se sabe que um tribunal exige um dado, a linha sem esse dado e pulada NAQUELE
tribunal antes de qualquer acesso, em vez de o robo entrar com o certificado e parar no meio do
formulario. Duas fontes, somadas:

1. Regra fixa (EXIGENCIAS): o que se sabe por envio real. A Justica do Trabalho (PJe-JT, ramo TRT
   no registro) exige telefone, numero do RG e orgao emissor do RG nos dois graus, mas so avisa ao
   enviar (o formulario nao marca esses campos como obrigatorios antes).
2. Tabela do mapeamento (tribunais\\formularios.csv, gerada por `python -m pje_rpa mapear`): campo
   do formulario de CADASTRO obrigatorio (marcado na tela ou cobrado pelo PJe no envio com trava,
   ver mapeamento.py), que veio vazio e editavel e que sai da
   planilha (telefone, RG, data do RG, estado civil). Nome, CPF, OAB, e-mail e endereco ja sao
   obrigatorios na planilha para todos e nao entram aqui.
Para outra exigencia so conhecida no envio, acrescente (coluna, descricao, ramos) em EXIGENCIAS.

O tribunal e reconhecido pela IDENTIDADE (registro.py): ramo na regra fixa e o id (coluna
`tribunal` da tabela) no mapeamento. O endereco de login nao entra em nenhuma das duas: mudar o
base_url nao muda o que o tribunal exige. Linhas antigas da tabela sem a coluna `tribunal` sao
reconhecidas pelo endereco de referencia do registro (registro.por_endereco_legado).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .registro import identidade, por_endereco_legado

# (atributo do registro do advogado, como aparece para o operador, ramos em que e exigido)
_TRT = frozenset({"TRT"})
EXIGENCIAS = [
    ("telefone", "telefone (celular com DDD)", _TRT),
    ("rg_numero", "numero do RG", _TRT),
    ("rg_orgao", "orgao emissor do RG", _TRT),
]

# Campo reconhecido no formulario (adaptive_form.PATTERNS) -> (atributo do registro, descricao)
CAMPO_DA_PLANILHA = {
    "phone": ("telefone", "telefone (celular com DDD)"),
    "ddd": ("telefone", "telefone (celular com DDD)"),
    "rg_numero": ("rg_numero", "numero do RG"),
    "rg_orgao": ("rg_orgao", "orgao emissor do RG"),
    "rg_data": ("rg_data_expedicao", "data de expedicao do RG"),
    "estado_civil": ("estado_civil", "estado civil"),
}


@lru_cache(maxsize=4)
def _da_tabela(caminho: str, versao: float) -> dict[str, tuple[tuple[str, str], ...]]:
    """id do tribunal -> (atributo, descricao) exigidos pelo formulario mapeado."""
    from .mapeamento import CADASTRO, ler_tabela

    exigidos: dict[str, list[tuple[str, str]]] = {}
    for linha in ler_tabela(Path(caminho)):
        par = CAMPO_DA_PLANILHA.get(linha.get("campo", ""))
        if (par and linha.get("origem") == CADASTRO and linha.get("obrigatorio") == "sim"
                and linha.get("veio_preenchido") != "sim" and linha.get("bloqueado") != "sim"):
            tid = (linha.get("tribunal") or "").strip()
            if not tid:  # linha antiga, so com o endereco
                antigo = por_endereco_legado(linha.get("url", ""))
                tid = antigo.id if antigo else ""
            if not tid:
                continue
            lista = exigidos.setdefault(tid, [])
            if par not in lista:
                lista.append(par)
    return {tid: tuple(pares) for tid, pares in exigidos.items()}


def da_tabela(alvo, tabela: Path | None = None) -> list[tuple[str, str]]:
    from . import mapeamento

    caminho = tabela or mapeamento.TABELA
    try:
        versao = caminho.stat().st_mtime_ns
    except OSError:
        return []
    return list(_da_tabela(str(caminho), versao).get(identidade(alvo).tribunal_id, ()))


def fixas(alvo) -> list[tuple[str, str]]:
    """Exigencias da regra fixa para o ramo do tribunal."""
    ramo = identidade(alvo).ramo
    return [(atributo, descricao) for atributo, descricao, ramos in EXIGENCIAS if ramo in ramos]


def faltando(alvo, record, tabela: Path | None = None) -> list[str]:
    """O que este tribunal exige e o registro do advogado nao tem (descricoes para o operador).
    alvo: AppConfig, OpcaoTribunal ou registro.Tribunal (a identidade, nao o endereco)."""
    exigidos = fixas(alvo)
    exigidos += [par for par in da_tabela(alvo, tabela) if par not in exigidos]
    falta = []
    for atributo, descricao in exigidos:
        if not str(getattr(record, atributo, "") or "").strip() and descricao not in falta:
            falta.append(descricao)
    return falta
