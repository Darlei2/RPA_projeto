"""Planilha de entrada da execucao em lote: um advogado por linha.

Aceita .xlsx (inclusive protegido por senha do Excel) ou .csv (separador ; ou ,).
Os nomes das colunas sao flexiveis: acentos, maiusculas, espacos e alguns sinonimos
("CPF", "cpf_esperado", "Nome completo"...) sao reconhecidos.

Colunas sensiveis (senha do certificado, senha da caixa de e-mail, chave do 2FA) sao
opcionais: se ficarem vazias, o robo pergunta na hora, sem eco. Guarde a planilha na pasta
planilhas\\ (ignorada pelo Git) e, se preencher essas colunas, proteja o arquivo com senha.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .config import ConfigurationError, lawyer_from_dict
from .models import LawyerRecord

# coluna canonica -> sinonimos aceitos (ja normalizados: minusculas, sem acento, "_")
COLUNAS: dict[str, tuple[str, ...]] = {
    "nome": ("nome", "nome_completo", "nome_esperado", "advogado", "nome_do_advogado"),
    "cpf": ("cpf", "cpf_esperado", "cpf_do_advogado"),
    "oab_numero": ("oab", "oab_numero", "numero_oab", "n_oab", "inscricao_oab"),
    "oab_uf": ("oab_uf", "uf_oab", "seccional", "uf_da_oab"),
    "email": ("email", "e_mail", "email_pje", "e_mail_pje"),
    "telefone": ("telefone", "celular", "fone", "telefone_celular"),
    "cep": ("cep",),
    "logradouro": ("logradouro", "endereco", "rua"),
    "numero": ("numero", "numero_endereco", "n"),
    "complemento": ("complemento",),
    "bairro": ("bairro",),
    "cidade": ("cidade", "municipio"),
    "uf": ("uf", "estado", "uf_endereco"),
    "autorizacao_ref": ("autorizacao_ref", "autorizacao", "referencia_autorizacao", "termo_autorizacao"),
    "certificado": ("certificado", "arquivo_certificado", "pfx", "caminho_pfx", "certificado_a1"),
    "senha_certificado": ("senha_certificado", "senha_do_certificado", "senha_pfx", "senha_a1"),
    "chave_2fa": ("chave_2fa", "chave_totp", "totp", "segredo_2fa"),
    "imap_servidor": ("imap_servidor", "servidor_imap", "imap_host"),
    "imap_usuario": ("imap_usuario", "usuario_imap", "imap_user"),
    "imap_senha": ("imap_senha", "senha_imap", "senha_app_email", "senha_de_app"),
    "executar": ("executar", "processar", "ativo"),
}
OBRIGATORIAS = ("nome", "cpf", "oab_numero", "oab_uf", "email", "telefone", "cep",
                "logradouro", "numero", "bairro", "cidade", "uf")
# Coluna "certificado": vazia ou "windows" = A1 ja instalado no Windows, achado pelo CPF da linha;
# 40 caracteres hexadecimais = thumbprint de um certificado instalado; outro texto = caminho do .pfx.
_THUMBPRINT = re.compile(r"^[0-9A-Fa-f]{40}$")
SENSIVEIS = ("senha_certificado", "chave_2fa", "imap_senha")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold().strip()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


_ALIAS = {alias: canon for canon, aliases in COLUNAS.items() for alias in aliases}


@dataclass
class LinhaAdvogado:
    numero_linha: int                 # linha na planilha (cabecalho = 1)
    dados: dict[str, str]
    record: LawyerRecord | None = None
    certificado: Path | None = None   # arquivo .pfx; None = certificado instalado no Windows
    thumbprint: str = ""              # escolhido na planilha (opcional) ou conferido antes de rodar
    certificado_info: str = ""        # resumo do certificado do Windows que sera usado
    erros: list[str] = field(default_factory=list)

    @property
    def usa_windows(self) -> bool:
        return self.certificado is None

    @property
    def ok(self) -> bool:
        return not self.erros

    @property
    def nome(self) -> str:
        return self.dados.get("nome", "")

    def segredo(self, coluna: str) -> str:
        return (self.dados.get(coluna) or "").strip()


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # CPF/CEP/telefone salvos como numero no Excel
    return str(value).strip()


def _rows_xlsx(path: Path, password_provider) -> list[list[str]]:
    from openpyxl import load_workbook

    data = path.read_bytes()
    try:
        import msoffcrypto

        office = msoffcrypto.OfficeFile(io.BytesIO(data))
        if office.is_encrypted():
            office.load_key(password=password_provider())
            plain = io.BytesIO()
            office.decrypt(plain)
            data = plain.getvalue()
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        raise ConfigurationError(f"Nao foi possivel abrir a planilha protegida: {type(exc).__name__}") from None
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    return [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)]


def _rows_csv(path: Path) -> list[list[str]]:
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else ";", delimiters=";,\t")
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), dialect)]


def ler_planilha(path: Path, password_provider=lambda: "") -> list[LinhaAdvogado]:
    """Le e valida todas as linhas. Linhas com erro voltam com `erros` preenchido."""
    if not path.exists():
        raise ConfigurationError(f"Planilha nao encontrada: {path}")
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        rows = _rows_xlsx(path, password_provider)
    elif suffix in (".csv", ".txt"):
        rows = _rows_csv(path)
    else:
        raise ConfigurationError("Use uma planilha .xlsx ou .csv")
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        raise ConfigurationError("Planilha vazia")

    header = [_ALIAS.get(_norm(h), "") for h in rows[0]]
    faltando = [c for c in OBRIGATORIAS if c not in header]
    if faltando:
        raise ConfigurationError("Colunas obrigatorias ausentes: " + ", ".join(faltando))

    linhas = []
    for idx, row in enumerate(rows[1:], start=2):
        dados = {col: (row[i] if i < len(row) else "") for i, col in enumerate(header) if col}
        if _norm(dados.get("executar", "")) in {"n", "nao", "no", "0", "false"}:
            continue
        linhas.append(_validar(idx, dados, path.parent))
    return linhas


def _zeros(value: str, size: int) -> str:
    """Excel guarda CPF/CEP como numero e perde zeros a esquerda: devolve os zeros."""
    digits = re.sub(r"\D", "", value or "")
    return digits.zfill(size) if digits and len(digits) < size else (value or "")


def _validar(numero_linha: int, dados: dict[str, str], base: Path) -> LinhaAdvogado:
    dados = {**dados, "cpf": _zeros(dados.get("cpf", ""), 11), "cep": _zeros(dados.get("cep", ""), 8)}
    linha = LinhaAdvogado(numero_linha, dados)
    try:
        linha.record = lawyer_from_dict({
            "nome_esperado": dados.get("nome", ""),
            "cpf_esperado": dados.get("cpf", ""),
            "oab_numero": dados.get("oab_numero", ""),
            "oab_uf": dados.get("oab_uf", ""),
            "email": dados.get("email", ""),
            "telefone": dados.get("telefone", ""),
            "autorizacao_ref": dados.get("autorizacao_ref", ""),
            "endereco": {k: dados.get(k, "") for k in
                         ("cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf")},
        })
    except ConfigurationError as exc:
        linha.erros.extend(str(exc).split("; "))
    raw = dados.get("certificado", "").strip().strip('"')
    compact = raw.replace(" ", "")
    if not raw or raw.casefold() in ("windows", "instalado", "repositorio"):
        pass  # usa o certificado instalado no Windows com o CPF da linha
    elif _THUMBPRINT.match(compact):
        linha.thumbprint = compact.upper()
    else:
        pfx = Path(raw)
        pfx = pfx if pfx.is_absolute() else (base / pfx)
        if not pfx.is_file():
            linha.erros.append(f"certificado nao encontrado: {raw}")
        else:
            linha.certificado = pfx
    return linha


def gravar_modelo(destino: Path) -> Path:
    """Cria a planilha modelo com o cabecalho e uma linha de exemplo (ficticia)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    cabecalho = ["nome", "cpf", "oab_numero", "oab_uf", "email", "telefone", "cep", "logradouro",
                 "numero", "complemento", "bairro", "cidade", "uf", "autorizacao_ref", "certificado",
                 "senha_certificado", "chave_2fa", "imap_servidor", "imap_usuario", "imap_senha", "executar"]
    exemplo = ["ADVOGADO DE TESTE", "52998224725", "12345", "BA", "advogado@exemplo.com.br", "71999999999",
               "40000000", "RUA DE TESTE", "100", "", "CENTRO", "SALVADOR", "BA", "CONTRATO-123",
               "", "", "", "imap.gmail.com", "", "", "N"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Advogados"
    ws.append(cabecalho)
    ws.append(exemplo)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    for row in ws.iter_rows(min_row=1, max_row=200):
        for cell in row:
            cell.number_format = "@"  # texto: CPF, CEP e telefone nao perdem zeros
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = max(14, len(str(col[0].value)) + 4)
    ws.freeze_panes = "A2"
    notas = wb.create_sheet("Instrucoes")
    for texto in (
        "Uma linha por advogado. A linha de exemplo esta com executar = N (sera ignorada).",
        "Obrigatorias: nome, cpf, oab_numero, oab_uf, email, telefone, cep, logradouro, numero, bairro, cidade, uf.",
        "autorizacao_ref: referencia do documento em que o advogado autoriza o uso do A1 (obrigatoria no modo real).",
        "certificado: VAZIO = usa o A1 ja instalado no Windows com o CPF da linha (sem .pfx e sem senha).",
        "certificado: thumbprint (40 letras/numeros) = usa aquele certificado instalado no Windows.",
        "certificado: caminho do .pfx (ex.: C:\\certificados\\fulano.pfx) = instala so durante a execucao e remove no fim.",
        "senha_certificado: so para .pfx. chave_2fa, imap_senha: OPCIONAIS. Vazias = o robo pergunta na hora (recomendado).",
        "Se preencher colunas de senha, proteja o arquivo com senha no Excel (Arquivo > Informacoes > Proteger).",
        "imap_servidor/imap_usuario: caixa onde chega o link de senha do PJe (vazio = deduz pelo e-mail).",
        "executar: N para pular a linha.",
    ):
        notas.append([texto])
    notas.column_dimensions["A"].width = 120
    destino.parent.mkdir(parents=True, exist_ok=True)
    wb.save(destino)
    return destino
