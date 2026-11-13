"""O tester do Gmail escolhe as linhas certas: --linha, --linhas, ou todas as marcadas com S."""
from pathlib import Path

import pytest

from pje_rpa import gmail_web


def _planilha(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["nome", "email", "email_senha", "email_2fa", "executar"])
    ws.append(["Ana", "ana@gmail.com", "s", "k", "N"])     # 2
    ws.append(["Bia", "bia@gmail.com", "s", "k", "S"])     # 3
    ws.append(["Caio", "caio@gmail.com", "", "", "S"])     # 4 (marcada, mas sem dados do Gmail)
    ws.append(["Duda", "duda@gmail.com", "s", "k", "s"])   # 5
    caminho = tmp_path / "a.xlsx"; wb.save(caminho)
    return caminho


def _rodar(monkeypatch, caminho, argv):
    testadas = []
    monkeypatch.setattr(gmail_web, "_testar_conta",
                        lambda numero, dados, navegador: testadas.append(numero) or 0)
    codigo = gmail_web.main(["--planilha", str(caminho), *argv])
    return codigo, testadas


def test_sem_argumento_testa_todas_as_marcadas_com_s(tmp_path, monkeypatch):
    codigo, testadas = _rodar(monkeypatch, _planilha(tmp_path), [])
    assert testadas == [3, 4, 5] and codigo == 0  # a linha 2 (N) fica de fora


def test_linhas_especificas(tmp_path, monkeypatch):
    _, testadas = _rodar(monkeypatch, _planilha(tmp_path), ["--linhas", "3,5"])
    assert testadas == [3, 5]


def test_uma_linha_mesmo_marcada_n(tmp_path, monkeypatch):
    _, testadas = _rodar(monkeypatch, _planilha(tmp_path), ["--linha", "2"])
    assert testadas == [2]  # pedida explicitamente: testa mesmo com executar=N


def test_linha_inexistente_avisa(tmp_path, monkeypatch, capsys):
    _, testadas = _rodar(monkeypatch, _planilha(tmp_path), ["--linhas", "3,99"])
    assert testadas == [3] and "99" in capsys.readouterr().out
