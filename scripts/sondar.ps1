param(
    [string[]]$Tribunal = @(),
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

# Abre so a tela de login de cada tribunal (nao usa certificado, nao entra no PJe).
# Sem -Tribunal, sonda todos os arquivos de tribunais\ (menos _comum.toml).
if ($Tribunal.Count -eq 0) {
    $Configs = Get-ChildItem tribunais -Filter *.toml | Where-Object { $_.Name -ne "_comum.toml" } |
        ForEach-Object { Join-Path "tribunais" $_.Name }
} else {
    $Configs = $Tribunal | ForEach-Object { Join-Path "tribunais" ("{0}.toml" -f $_.ToLower()) }
}
& $Python -m pje_rpa sondar --config @Configs
