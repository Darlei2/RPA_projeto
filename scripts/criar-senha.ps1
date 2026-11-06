param(
    [Parameter(Mandatory = $true)][string]$Tribunal,
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

# Depois do cadastro: pede o link de senha (se ainda nao pediu), cadastra a senha com o
# link recebido no e-mail e testa o login com CPF + senha + 2FA. Tudo e perguntado na hora.
$Config = Join-Path "tribunais" ("{0}.toml" -f $Tribunal.ToLower())
if (-not (Test-Path -LiteralPath $Config)) { throw "Tribunal '$Tribunal' nao configurado" }
& $Python -m pje_rpa senha --config $Config
