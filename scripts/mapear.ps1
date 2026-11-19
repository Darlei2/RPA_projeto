param(
    [string]$Tribunal = "",
    [string]$Planilha = "planilhas\advogados.xlsx",
    [int]$Linha = 0,
    [double]$Pausa = 5,
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

# Mapa do formulario de cadastro de cada PJe, com o certificado de UM advogado (com autorizacao):
# entra, le os campos (rotulo, obrigatorio, opcoes; nunca valores) e SAI. Nao preenche, nao clica em
# "Confirmar dados" e nao assina nada. Onde o advogado ja e cadastrado, le "Alteracao de dados
# cadastrais" (so le, nao salva).
# -Tribunal: trt, trf, tj, tre, todos ou chaves (ex.: tjmt,tjmt-2g). Sem ele, o robo pergunta.
# Para no primeiro bloqueio ou 2FA recusado, para nao bloquear o acesso do advogado.
# Saida: tribunais\formularios.csv (um campo por linha) e tribunais\formularios-situacao.csv.
# Abra o PJeOffice PRO ANTES e deixe-o rodando na bandeja.
$Argumentos = @("-m", "pje_rpa", "mapear", "--planilha", $Planilha, "--pausa", $Pausa)
if ($Tribunal) { $Argumentos += @("--tribunal", $Tribunal) }
if ($Linha -gt 0) { $Argumentos += @("--linha", $Linha) }
& $Python @Argumentos
