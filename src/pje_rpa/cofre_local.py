"""Cofre local das senhas de app de e-mail (uma por advogado), cifradas pelo Windows (DPAPI).

So o mesmo usuario do Windows, na mesma maquina, consegue decifrar. O arquivo fica em
runtime\\ (fora do Git). Fora do Windows o cofre nao grava nada: a senha fica so em memoria.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ARQUIVO = Path("runtime") / "caixas-email.json"


def disponivel() -> bool:
    return sys.platform == "win32"


def _blob(data: bytes):
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):  # noqa: N801
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), DATA_BLOB, buf


def _dpapi(data: bytes, proteger: bool) -> bytes:
    import ctypes

    entrada, DATA_BLOB, _buf = _blob(data)
    saida = DATA_BLOB()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if proteger else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(entrada), None, None, None, None, 0, ctypes.byref(saida)):
        raise OSError("DPAPI falhou")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree(saida.pbData)


def _carregar(arquivo: Path) -> dict:
    try:
        return json.loads(arquivo.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def guardar(email: str, segredo: str, arquivo: Path = ARQUIVO, cifrar=None) -> bool:
    """Guarda cifrado. Devolve False quando nao ha cofre (fora do Windows)."""
    cifrar = cifrar or (lambda b: _dpapi(b, True) if disponivel() else None)
    blob = cifrar(segredo.encode())
    if blob is None:
        return False
    dados = _carregar(arquivo)
    dados[email.strip().lower()] = base64.b64encode(blob).decode()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps(dados, indent=2), encoding="utf-8")
    return True


def ler(email: str, arquivo: Path = ARQUIVO, decifrar=None) -> str:
    decifrar = decifrar or (lambda b: _dpapi(b, False) if disponivel() else None)
    valor = _carregar(arquivo).get(email.strip().lower())
    if not valor:
        return ""
    try:
        bruto = decifrar(base64.b64decode(valor))
        return bruto.decode() if bruto else ""
    except Exception:  # noqa: BLE001 - outro usuario/maquina: como se nao existisse
        return ""


def apagar(email: str, arquivo: Path = ARQUIVO) -> None:
    dados = _carregar(arquivo)
    if dados.pop(email.strip().lower(), None) is not None:
        arquivo.write_text(json.dumps(dados, indent=2), encoding="utf-8")
