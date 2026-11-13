"""Dados da planilha que so alguns tribunais exigem no cadastro.

A planilha tem poucas colunas obrigatorias para todos; o resto depende do formulario de cada
tribunal. Quando ja se sabe que uma familia de tribunais exige um dado, a regra fica aqui: a linha
sem esse dado e pulada NAQUELE tribunal antes de qualquer acesso, em vez de o robo entrar com o
certificado e parar no meio do formulario.

Hoje: a Justica do Trabalho (PJe-JT, TRTs) exige telefone no cadastro, nos dois graus.
Para outra exigencia, acrescente (coluna, descricao, padroes de endereco) em EXIGENCIAS.
"""
from __future__ import annotations

import re

# (atributo do registro do advogado, como aparece para o operador, enderecos em que e exigido)
EXIGENCIAS = [
    ("telefone", "telefone (celular com DDD)", [re.compile(r"^https://pje\.trt\d+\.jus\.br/", re.I)]),
]


def faltando(url: str, record) -> list[str]:
    """O que este tribunal exige e o registro do advogado nao tem (descricoes para o operador)."""
    url = (url or "").strip()
    return [descricao for atributo, descricao, padroes in EXIGENCIAS
            if any(p.match(url) for p in padroes) and not str(getattr(record, atributo, "") or "").strip()]
