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


def gerar_senha(tamanho: int = 16) -> str:
    """Pelo menos 2 de cada classe (maiuscula, minuscula, numero, especial); comeca por letra."""
    if tamanho < 12:
        raise ValueError("Use pelo menos 12 caracteres")
    rng = secrets.SystemRandom()
    chars = [rng.choice(group) for group in (UPPER, LOWER, DIGITS, SPECIAL) for _ in range(2)]
    pool = UPPER + LOWER + DIGITS + SPECIAL
    chars += [rng.choice(pool) for _ in range(tamanho - len(chars))]
    rng.shuffle(chars)
    if not chars[0].isalpha():
        first = next(i for i, c in enumerate(chars) if c.isalpha())
        chars[0], chars[first] = chars[first], chars[0]
    return "".join(chars)
