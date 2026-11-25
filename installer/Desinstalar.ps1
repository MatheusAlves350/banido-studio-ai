<#
  Banido Studio AI - desinstalador.

  Remove o programa instalado por Instalar.ps1 (Python portatil, bibliotecas, FFmpeg, MKVToolNix, atalho).
  Por padrao MANTEM os seus dados (gravacoes, cortes, vozes, banco de dados, modelos e o arquivo .env).
  Os dados so sao apagados se voce digitar APAGAR quando for perguntado.
  Nunca mexe em pastas de modelos que ficam FORA da pasta de dados (ex.: de uma instalacao antiga).

  -Quieto: sem perguntas; mantem os dados (usado pelo desinstalador do Windows / Inno Setup).

  Obs.: arquivo ASCII de proposito (PowerShell 5.1 le arquivos sem BOM como ANSI).
#>
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'BanidoStudio'),
    [switch]$Quieto,
    [switch]$DaTemp
)

$ErrorActionPreference = 'Stop'
$InstallDir = $InstallDir.TrimEnd('\')

# Roda a partir de uma copia em %TEMP% para poder apagar a propria pasta de instalacao.
if (-not $DaTemp) {
    $tmp = Join-Path $env:TEMP 'Desinstalar_Banido_Studio.ps1'
    Copy-Item -LiteralPath $PSCommandPath -Destination $tmp -Force
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $tmp, '-InstallDir', $InstallDir, '-DaTemp')
    if ($Quieto) { $a += '-Quieto' }
    & powershell.exe @a
    exit $LASTEXITCODE
}

function Perguntar([string]$texto, [string]$padrao) {
    try {
        $r = Read-Host $texto
        if ([string]::IsNullOrWhiteSpace($r)) { return $padrao }
        return $r.Trim()
    } catch { return $padrao }
}

function Remover([string]$caminho) {
    if (-not (Test-Path -LiteralPath $caminho)) { return }
    try { Remove-Item -LiteralPath $caminho -Recurse -Force -ErrorAction Stop }
    catch { Write-Host "    nao consegui remover $caminho ($($_.Exception.Message))" -ForegroundColor Yellow }
}

function LerEnv([string]$arq, [string]$chave) {
    if (-not (Test-Path $arq)) { return $null }
    foreach ($l in (Get-Content $arq)) {
        if ($l -match "^\s*$chave\s*=\s*(.*)$") { return $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return $null
}

try {
    if (-not (Test-Path -LiteralPath $InstallDir)) {
        Write-Host "Nada para remover: $InstallDir nao existe."
        exit 0
    }

    $codigo = Join-Path $InstallDir 'codigo'
    $envFile = Join-Path $codigo '.env'
    $dados = LerEnv $envFile 'BANIDO_BASE_DIR'
    if ($dados -and [IO.Path]::IsPathRooted($dados)) { $dados = [IO.Path]::GetFullPath($dados) }
    else { $dados = Join-Path $codigo 'data' }
    $dadosDentro = $dados.StartsWith($InstallDir + '\', [StringComparison]::OrdinalIgnoreCase)

    $apagarDados = $false
    if (-not $Quieto) {
        Write-Host ''
        Write-Host 'DESINSTALAR O BANIDO STUDIO AI' -ForegroundColor Cyan
        Write-Host "Programa: $InstallDir"
        Write-Host "Seus dados (gravacoes, cortes, banco): $dados"
        $r = Perguntar 'Remover o programa? Seus dados serao MANTIDOS. (S/N) [N]' 'N'
        if ($r -notmatch '^[sSyY]') { Write-Host 'Cancelado. Nada foi alterado.'; exit 0 }
        Write-Host ''
        Write-Host 'Deseja apagar TAMBEM os dados? Isso NAO pode ser desfeito.' -ForegroundColor Yellow
        $r = Perguntar 'Digite APAGAR para apagar os dados, ou apenas Enter para mantê-los' ''
        $apagarDados = ($r -ceq 'APAGAR')
    }

    # Fecha o que estiver rodando a partir da pasta de instalacao (servidor, workers).
    Write-Host 'Encerrando processos do Banido Studio...'
    Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $p = $null; try { $p = $_.Path } catch {}
        $p -and $p.StartsWith($InstallDir + '\', [StringComparison]::OrdinalIgnoreCase)
    } | ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch {} }
    Start-Sleep -Seconds 2

    Write-Host 'Removendo atalho...'
    Remover (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Banido Studio AI.lnk')

    Write-Host 'Removendo programa...'
    foreach ($sub in 'runtime', 'tools', 'cache', 'fonte') { Remover (Join-Path $InstallDir $sub) }
    foreach ($f in 'Banido_Studio.bat', 'instalacao.log') { Remover (Join-Path $InstallDir $f) }

    if ($apagarDados) {
        $raiz = [IO.Path]::GetPathRoot($dados)
        if ($dados.TrimEnd('\') -ieq $raiz.TrimEnd('\') -or $dados.Length -lt 8) {
            Write-Host "Por seguranca, nao apago $dados (parece uma pasta raiz). Apague manualmente se quiser." -ForegroundColor Yellow
        } else {
            Write-Host "Apagando dados em $dados ..."
            Remover $dados
        }
        Remover $codigo
    } else {
        # Mantem o .env (aponta para a pasta de dados) e a pasta de dados se estiver dentro da instalacao.
        if (Test-Path -LiteralPath $codigo) {
            Get-ChildItem -LiteralPath $codigo -Force | Where-Object {
                $_.Name -ne '.env' -and -not ($dadosDentro -and $_.FullName -ieq $dados)
            } | ForEach-Object { Remover $_.FullName }
        }
        Write-Host "Dados mantidos em: $dados" -ForegroundColor Green
    }

    # Remove a pasta de instalacao se ficou vazia.
    try { if (-not (Get-ChildItem -LiteralPath $InstallDir -Force)) { Remove-Item -LiteralPath $InstallDir -Force } } catch {}

    Write-Host ''
    Write-Host 'DESINSTALACAO CONCLUIDA.' -ForegroundColor Green
    exit 0
}
catch {
    Write-Host "ERRO: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
