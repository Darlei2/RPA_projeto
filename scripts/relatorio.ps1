param(
    [int]$Meta = 5,
    [string]$Python = ".venv\Scripts\python.exe"
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

# Melhor resultado por tribunal (runtime\audit) e situacao da meta de validacao.
& $Python -m pje_rpa relatorio --meta $Meta
