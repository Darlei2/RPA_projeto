param(
    [int]$Linha = 0,
    [string]$Linhas = "",
    [string]$Planilha = "planilhas\advogados.xlsx",
    [string]$Navegador = "edge",
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

# Testa a leitura do Gmail pelo navegador (perfil salvo por conta). Nao toca no PJe.
# Sem -Linha/-Linhas: testa todas as linhas marcadas com executar=S.
#   .\scripts\testar-gmail-web.ps1                 # todas as marcadas com S
#   .\scripts\testar-gmail-web.ps1 -Linhas 17,32   # so essas
#   .\scripts\testar-gmail-web.ps1 -Linha 32       # so uma
$a = @("-m", "pje_rpa.gmail_web", "--planilha", $Planilha, "--navegador", $Navegador)
if ($Linha -gt 0) { $a += @("--linha", "$Linha") }
if ($Linhas)      { $a += @("--linhas", $Linhas) }
& $Python @a
