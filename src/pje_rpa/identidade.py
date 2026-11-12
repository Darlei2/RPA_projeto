"""Comparacao de nomes para as checagens de identidade do PJe.

Regra unica usada em todos os pontos onde o PJe exige nome (nome do certificado, nome do
perfil detectado e nome do formulario de cadastro): o certificado/PJe as vezes traz o nome
sem um sobrenome (ex.: "ANA CAROLINA DE JESUS" vs "ANA CAROLINA DE JESUS ANDRADE"). Aceita
nomes iguais ou quando o mais curto e subsequencia (na ordem) do mais longo, com pelo menos
2 nomes em comum. O CPF e conferido a parte e e a garantia real de identidade.
"""
from __future__ import annotations

import re
import unicodedata


def _tokens(value: str) -> list[str]:
    texto = unicodedata.normalize("NFKD", str(value or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", texto.casefold()).strip().split()


def nomes_compativeis(a: str, b: str) -> bool:
    """True quando os nomes sao a mesma pessoa, tolerando um sobrenome a menos."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    curto, longo = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    i = 0
    for token in longo:
        if i < len(curto) and curto[i] == token:
            i += 1
    return i == len(curto) and len(curto) >= 2
