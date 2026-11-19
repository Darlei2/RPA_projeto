"""Dados da planilha que so alguns tribunais exigem no cadastro.

A planilha tem poucas colunas obrigatorias para todos; o resto depende do formulario de cada
tribunal. Quando ja se sabe que um tribunal exige um dado, a linha sem esse dado e pulada NAQUELE
tribunal antes de qualquer acesso, em vez de o robo entrar com o certificado e parar no meio do
formulario. Duas fontes, somadas:

1. Regra fixa (EXIGENCIAS): o que se sabe por envio real. A Justica do Trabalho (PJe-JT, TRTs)
   exige telefone, numero do RG e orgao emissor do RG nos dois graus, mas so avisa ao enviar
   (o formulario nao marca esses campos como obrigatorios antes).
2. Tabela do mapeamento (tribunais\\formularios.csv, gerada por `python -m pje_rpa mapear`): campo
   do formulario de CADASTRO marcado como obrigatorio, que veio vazio e editavel e que sai da
   planilha (telefone, RG, data do RG, estado civil). Nome, CPF, OAB, e-mail e endereco ja sao
   obrigatorios na planilha para todos e nao entram aqui.
Para outra exigencia so conhecida no envio, acrescente (coluna, descricao, padroes) em EXIGENCIAS.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

# (atributo do registro do advogado, como aparece para o operador, enderecos em que e exigido)
_TRT = [re.compile(r"^https://pje\.trt\d+\.jus\.br/", re.I)]
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
    """url do tribunal -> (atributo, descricao) exigidos pelo formulario mapeado."""
    from .mapeamento import CADASTRO, ler_tabela

    exigidos: dict[str, list[tuple[str, str]]] = {}
    for linha in ler_tabela(Path(caminho)):
        par = CAMPO_DA_PLANILHA.get(linha.get("campo", ""))
        if (par and linha.get("origem") == CADASTRO and linha.get("obrigatorio") == "sim"
                and linha.get("veio_preenchido") != "sim" and linha.get("bloqueado") != "sim"):
            lista = exigidos.setdefault(linha.get("url", "").strip(), [])
            if par not in lista:
                lista.append(par)
    return {url: tuple(pares) for url, pares in exigidos.items()}


def da_tabela(url: str, tabela: Path | None = None) -> list[tuple[str, str]]:
    from .mapeamento import TABELA

    caminho = tabela or TABELA
    try:
        versao = caminho.stat().st_mtime
    except OSError:
        return []
    return list(_da_tabela(str(caminho), versao).get((url or "").strip(), ()))


def faltando(url: str, record, tabela: Path | None = None) -> list[str]:
    """O que este tribunal exige e o registro do advogado nao tem (descricoes para o operador)."""
    url = (url or "").strip()
    exigidos = [(atributo, descricao) for atributo, descricao, padroes in EXIGENCIAS
                if any(p.match(url) for p in padroes)]
    exigidos += [par for par in da_tabela(url, tabela) if par not in exigidos]
    falta = []
    for atributo, descricao in exigidos:
        if not str(getattr(record, atributo, "") or "").strip() and descricao not in falta:
            falta.append(descricao)
    return falta
