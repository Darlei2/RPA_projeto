param(
    [string]$Tribunal = "",
    [string]$Planilha = "planilhas\advogados.xlsx",
    [int]$Linha = 0,
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

# Mapeamento ASSISTIDO dos tribunais fora do PJe (eproc, e-SAJ, Projudi, sistemas proprios), da lista
# tribunais\outros-sistemas.csv: o robo abre a pagina de cadastro no Edge e voce faz o caminho ate o
# formulario (no eproc: Cadastre-se aqui > Cadastrar advogado > Cadastro com certificado digital >
# certificado do advogado). Com o formulario na tela, tecle Enter: o robo le os campos (rotulo,
# obrigatorio, opcoes; nunca valores). O robo nao clica, nao preenche e nao envia nada.
# Opcional: para ver o que o tribunal cobra no envio, apague SO o e-mail na tela, clique em Finalizar
# voce mesmo e tecle Enter; o robo le os avisos. Saida em tribunais\formularios.csv, como no mapear.ps1.
#   .\scripts\mapear-tela.ps1 -Tribunal tjsc-eproc-1g -Planilha planilhas\mapeamento.xlsx
$Argumentos = @("-m", "pje_rpa", "mapear-tela", "--planilha", $Planilha)
if ($Tribunal) { $Argumentos += @("--tribunal", $Tribunal) }
if ($Linha -gt 0) { $Argumentos += @("--linha", $Linha) }
& $Python @Argumentos
