<#
  Usado pelo instalador grafico (Inno Setup): roda Instalar.ps1 sem fazer perguntas no console.
  Toda pergunta recebe a resposta padrao (ex.: reaproveitar modelos ja baixados = Sim).
  Arquivo ASCII de proposito (PowerShell 5.1 le arquivos sem BOM como ANSI).
#>
param(
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [Parameter(Mandatory = $true)][string]$FonteDir,
    [string]$DadosDir = '',
    [switch]$SemAtalho
)

# Uma funcao com este nome tem precedencia sobre o cmdlet; devolve vazio => Instalar.ps1 usa o padrao.
function Read-Host { param([string]$Prompt) return '' }

$args2 = @{ InstallDir = $InstallDir; FonteDir = $FonteDir }
if ($DadosDir) { $args2['DadosDir'] = $DadosDir }
if ($SemAtalho) { $args2['SemAtalho'] = $true }

& (Join-Path $PSScriptRoot 'Instalar.ps1') @args2
exit $LASTEXITCODE
