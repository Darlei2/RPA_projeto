param(
    [string[]]$Tribunal = @(),
    [string]$Planilha = "",
    [switch]$Ensaio,
    [switch]$Real,
    [switch]$Modelo,
    [switch]$SoSenha,
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

# Execucao geral: pergunta o tribunal, le a planilha (um advogado por linha) e, para cada um,
# faz cadastro com o A1 + termo assinado + "Solicitar nova senha" + senha nova pelo link do
# e-mail + teste de CPF + senha + 2FA. No fim grava entregas\credenciais-<data>.xlsx protegida.
#   .\scripts\executar.ps1                          # pergunta tudo
#   .\scripts\executar.ps1 -Tribunal tjpe           # ja escolhe o tribunal
#   .\scripts\executar.ps1 -Tribunal "tremg,tjpa,trt5"  # varios, um depois do outro
#   .\scripts\executar.ps1 -Tribunal trt5 -SoSenha  # ja cadastrado: sem certificado, so nova senha
#   .\scripts\executar.ps1 -Modelo                  # cria planilhas\advogados.xlsx para preencher
# Abra o PJeOffice PRO antes do modo real.
$PyArgs = @("-m", "pje_rpa", "executar")
if ($Tribunal) { $PyArgs += @("--tribunal", ($Tribunal -join ",")) }
if ($Planilha) { $PyArgs += @("--planilha", $Planilha) }
if ($Ensaio) { $PyArgs += "--ensaio" }
if ($Real) { $PyArgs += "--real" }
if ($Modelo) { $PyArgs += "--modelo" }
if ($SoSenha) { $PyArgs += "--so-senha" }
& $Python @PyArgs
