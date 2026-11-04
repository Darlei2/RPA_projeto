"""TOTP RFC 6238 (HMAC-SHA1), sem dependencias externas.

A chave Base32 deve ser fornecida pelo titular autorizado. Nao salve em logs.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import struct
import time


def normalizar_chave(chave: str | None) -> str:
    """Valida uma chave Base32 opcional, sem inclui-la nas mensagens de erro."""
    if chave is None:
        return ""
    if not isinstance(chave, str):
        raise ValueError("Chave TOTP invalida")
    chave = re.sub(r"[\s-]+", "", chave.upper())
    if not chave:
        return ""
    if len(chave) > 128 or not re.fullmatch(r"[A-Z2-7]+", chave):
        raise ValueError("Chave TOTP invalida: use caracteres Base32 (A-Z e 2-7)")
    try:
        bruto = base64.b32decode(chave + "=" * (-len(chave) % 8), casefold=True)
    except (binascii.Error, ValueError):
        raise ValueError("Chave TOTP invalida: Base32 malformado") from None
    if len(bruto) < 10:
        raise ValueError("Chave TOTP muito curta")
    return chave


def gerar_totp(chave: str, agora: float | None = None, digitos: int = 6, periodo: int = 30) -> str:
    """Gera codigo HMAC-SHA1 no instante informado (UTC Unix time)."""
    if not chave or digitos not in (6, 8) or periodo <= 0:
        raise ValueError("Parametros TOTP invalidos")
    chave = normalizar_chave(chave)
    segredo = base64.b32decode(chave + "=" * (-len(chave) % 8))
    instante = time.time() if agora is None else agora
    contador = int(instante // periodo)
    digest = hmac.new(segredo, struct.pack(">Q", contador), hashlib.sha1).digest()
    offset = digest[-1] & 15
    valor = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff) % (10 ** digitos)
    return str(valor).zfill(digitos)
