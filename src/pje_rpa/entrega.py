"""Planilha de entrega das credenciais (uma linha por tribunal), protegida por senha.

O arquivo .xlsx sai criptografado (padrao do Excel: pede a senha para abrir). A senha da
planilha e digitada pelo operador no fim e nao e gravada em lugar nenhum.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

COLUNAS = [
    "Tribunal", "Endereco de login", "Advogado", "CPF (usuario)", "E-mail cadastrado",
    "Senha", "Chave 2FA (TOTP)", "Senha cadastrada", "Login CPF+senha+2FA testado",
    "Data/hora", "Observacao",
]


@dataclass
class LinhaCredencial:
    tribunal: str
    url_login: str
    advogado: str
    cpf: str
    email: str
    senha: str = ""
    chave_2fa: str = ""
    senha_cadastrada: bool = False
    login_testado: bool = False
    quando: datetime = field(default_factory=datetime.now)
    observacao: str = ""

    def valores(self) -> list:
        return [
            self.tribunal, self.url_login, self.advogado, self.cpf, self.email,
            self.senha if self.senha_cadastrada else "", self.chave_2fa,
            "Sim" if self.senha_cadastrada else "Nao", "OK" if self.login_testado else "Falhou",
            self.quando.strftime("%d/%m/%Y %H:%M"), self.observacao,
        ]


def _workbook_bytes(linhas: list[LinhaCredencial]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Credenciais PJe"
    ws.append(COLUNAS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    for linha in linhas:
        ws.append(linha.valores())
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.number_format = "@"  # texto: CPF e senha nao viram numero/formula
    for idx, titulo in enumerate(COLUNAS, start=1):
        largura = max([len(titulo)] + [len(str(ws.cell(r, idx).value or "")) for r in range(2, ws.max_row + 1)])
        ws.column_dimensions[get_column_letter(idx)].width = min(largura + 2, 60)
    ws.freeze_panes = "A2"
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def salvar_planilha(linhas: list[LinhaCredencial], destino: Path, senha_arquivo: str) -> Path:
    """Grava a planilha criptografada. Sem senha de arquivo, recusa (nao grava senha em claro)."""
    if not senha_arquivo:
        raise ValueError("A planilha precisa de uma senha para abrir")
    from msoffcrypto.format.ooxml import OOXMLFile

    destino.parent.mkdir(parents=True, exist_ok=True)
    plano = io.BytesIO(_workbook_bytes(linhas))
    cifrado = io.BytesIO()
    OOXMLFile(plano).encrypt(senha_arquivo, cifrado)
    destino.write_bytes(cifrado.getvalue())
    return destino


def abrir_planilha(caminho: Path, senha_arquivo: str):
    """Usado nos testes e para conferencia: devolve o workbook decifrado."""
    import msoffcrypto
    from openpyxl import load_workbook

    office = msoffcrypto.OfficeFile(io.BytesIO(caminho.read_bytes()))
    office.load_key(password=senha_arquivo)
    plano = io.BytesIO()
    office.decrypt(plano)
    return load_workbook(plano)
