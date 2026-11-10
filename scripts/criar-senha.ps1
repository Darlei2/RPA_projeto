param(
    [Parameter(Mandatory = $true)][string[]]$Tribunal,
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

# Depois do cadastro, para cada tribunal: pede o link de senha, le o link na caixa de e-mail
# (ou recebe colado), gera uma senha forte, cadastra e testa CPF + senha + 2FA. No fim grava
# entregas\credenciais-<data>.xlsx protegida por senha. Tudo sensivel e perguntado na hora.
# Ex.: .\scripts\criar-senha.ps1 -Tribunal tjap,trt8
$Configs = @()
foreach ($t in @($Tribunal)) {
    foreach ($name in ($t -split ",")) {
        $name = $name.Trim()
        if (-not $name) { continue }
        $Config = Join-Path "tribunais" ("{0}.toml" -f $name.ToLower())
        if (-not (Test-Path -LiteralPath $Config)) { throw "Tribunal $name nao configurado" }
        $Configs += $Config
    }
}
& $Python -m pje_rpa senha --config @Configs
