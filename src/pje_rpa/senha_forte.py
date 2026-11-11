"""Gera a senha do PJe: forte, aleatoria (modulo secrets) e facil de digitar pelo analista."""
from __future__ import annotations

import secrets
import string

# Sem caracteres que se confundem na leitura (O/0, I/l/1) nem os que costumam quebrar
# formularios ou planilhas (aspas, barra invertida, espaco, <, >, =, +).
UPPER = "".join(c for c in string.ascii_uppercase if c not in "OI")
LOWER = "".join(c for c in string.ascii_lowercase if c not in "l")
DIGITS = "23456789"
SPECIAL = "@#$%&*!?-_"


def gerar_senha(tamanho: int = 16, especiais: bool = True, permitir_curta: bool = False) -> str:
    """Pelo menos 2 de cada classe (maiuscula, minuscula, numero e, se pedido, especial).

    Sem especiais (ex.: o TJAP aceita so letras e numeros) use 20 caracteres: continua forte.
    """
    # permitir_curta: so quando o proprio PJe limita o tamanho (ex.: "entre 8 e 10 caracteres")
    if tamanho < (8 if permitir_curta else 12):
        raise ValueError("Use pelo menos 12 caracteres")
    groups = (UPPER, LOWER, DIGITS, SPECIAL) if especiais else (UPPER, LOWER, DIGITS)
    rng = secrets.SystemRandom()
    chars = [rng.choice(group) for group in groups for _ in range(2)]
    pool = "".join(groups)
    chars += [rng.choice(pool) for _ in range(tamanho - len(chars))]
    rng.shuffle(chars)
    if not chars[0].isalpha():
        first = next(i for i, c in enumerate(chars) if c.isalpha())
        chars[0], chars[first] = chars[first], chars[0]
    return "".join(chars)
