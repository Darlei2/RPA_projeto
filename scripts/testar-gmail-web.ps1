param(
    [Parameter(Mandatory = $true)][int]$Linha,
    [string]$Planilha = "planilhas\advogados.xlsx",
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

# Testa a leitura do Gmail pelo navegador (perfil salvo por conta) para UMA linha da planilha.
# Nao toca no PJe. Precisa das colunas email, email_senha e email_2fa preenchidas nessa linha.
& $Python -m pje_rpa.gmail_web --linha $Linha --planilha $Planilha
