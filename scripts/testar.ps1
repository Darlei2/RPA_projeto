param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
if (-not $Python) {
    $venv = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $Python = if (Test-Path $venv) { $venv } else { "python" }
}
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $Python -m pytest -q tests
exit $LASTEXITCODE
