"""Testes que abrem o Chromium do Playwright.

Na VM o Chromium do Playwright pode nao estar instalado (o robo usa o Edge). Tentar abri-lo
dentro do pytest e falhar deixa o Playwright preso e derruba os testes seguintes com "Sync API
inside the asyncio loop". Por isso a checagem e feita UMA vez, num processo separado, e os
testes de navegador sao pulados quando nao ha Chromium.
"""
import inspect
import subprocess
import sys

import pytest

_CHROMIUM: bool | None = None
_CODIGO = ("from playwright.sync_api import sync_playwright\n"
           "with sync_playwright() as p:\n"
           "    p.chromium.launch().close()\n")


def chromium_disponivel() -> bool:
    global _CHROMIUM
    if _CHROMIUM is None:
        try:
            _CHROMIUM = subprocess.run([sys.executable, "-c", _CODIGO], capture_output=True,
                                       timeout=90).returncode == 0
        except Exception:  # noqa: BLE001
            _CHROMIUM = False
    return _CHROMIUM


def _usa_navegador(item) -> bool:
    if {"pagina", "browser"} & set(getattr(item, "fixturenames", ())):
        return True
    try:
        return "sync_playwright" in inspect.getsource(item.function)
    except (OSError, TypeError):
        return False


def pytest_collection_modifyitems(config, items):
    alvo = [i for i in items if _usa_navegador(i)]
    if alvo and not chromium_disponivel():
        pular = pytest.mark.skip(reason="Chromium do Playwright nao instalado (python -m playwright install chromium)")
        for item in alvo:
            item.add_marker(pular)


@pytest.fixture(autouse=True)
def _sem_tabela_do_mapeamento(monkeypatch, tmp_path_factory):
    """A tabela real do mapeamento (tribunais\\formularios.csv) muda a cada mapeamento: os testes
    usam uma tabela vazia, e os que testam a tabela passam a propria."""
    from pje_rpa import mapeamento

    pasta = tmp_path_factory.mktemp("mapa")
    monkeypatch.setattr(mapeamento, "TABELA", pasta / "formularios.csv")
    monkeypatch.setattr(mapeamento, "SITUACAO", pasta / "formularios-situacao.csv")
