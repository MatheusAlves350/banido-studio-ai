<#
  Banido Studio AI - instalador para Windows.

  O que faz (tudo dentro de uma unica pasta, sem mexer no resto do computador):
    1. copia o programa para <InstallDir>\codigo
    2. baixa um Python portatil (runtime\) e instala as dependencias (PyTorch com CUDA, se houver placa NVIDIA)
    3. baixa FFmpeg e MKVToolNix portateis (tools\)
    4. cria o .env, o arquivo Banido_Studio.bat e um atalho na Area de Trabalho

  Pode ser executado de novo a qualquer momento: ele atualiza o codigo e completa o que faltar,
  sem apagar a pasta data (gravacoes, banco de dados, cortes, vozes).

  Obs.: este arquivo usa apenas caracteres ASCII de proposito (o PowerShell 5.1 le arquivos sem BOM como ANSI).
#>
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'BanidoStudio'),
    [string]$FonteDir = (Split-Path -Parent $PSScriptRoot),
    [switch]$SemAtalho,
    [switch]$ForcarCpu
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$PythonVersion = '3.11.9'
$PythonZipUrl  = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$GetPipUrl     = 'https://bootstrap.pypa.io/get-pip.py'
$TorchIndexUrl = 'https://download.pytorch.org/whl/cu121'
$FfmpegUrl     = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
$SevenZipUrl   = 'https://www.7-zip.org/a/7zr.exe'
$MkvListUrl    = 'https://mkvtoolnix.download/windows/releases/'
$MkvFallback   = '86.0'

$CodigoDir  = Join-Path $InstallDir 'codigo'
$RuntimeDir = Join-Path $InstallDir 'runtime'
$ToolsDir   = Join-Path $InstallDir 'tools'
$CacheDir   = Join-Path $InstallDir 'cache'
$PythonExe  = Join-Path $RuntimeDir 'python.exe'

New-Item -ItemType Directory -Force -Path $InstallDir, $CacheDir, $ToolsDir | Out-Null
try { Start-Transcript -Path (Join-Path $InstallDir 'instalacao.log') -Append | Out-Null } catch {}

function Etapa([string]$n, [string]$texto) { Write-Host ""; Write-Host "[$n] $texto" -ForegroundColor Cyan }

function Baixar([string]$url, [string]$destino) {
    for ($i = 1; $i -le 3; $i++) {
        try {
            Write-Host "    baixando $url"
            Invoke-WebRequest -Uri $url -OutFile $destino -UseBasicParsing
            return
        } catch {
            Write-Host "    tentativa $i falhou: $($_.Exception.Message)" -ForegroundColor Yellow
            if ($i -eq 3) { throw }
            Start-Sleep -Seconds (3 * $i)
        }
    }
}

function ExecutarPython([string[]]$argumentos) {
    & $PythonExe @argumentos
    if ($LASTEXITCODE -ne 0) { throw "Falha ao executar: python $($argumentos -join ' ')" }
}

# No PowerShell 5.1, redirecionar o erro de um programa externo (2>$null) com
# $ErrorActionPreference='Stop' vira uma excecao fatal. Estas funcoes rodam o programa
# com 'Continue', juntam a saida e devolvem o codigo de saida em $script:CodigoSaida.
$script:CodigoSaida = 0
function ExecutarQuieto([string]$exe, [string[]]$argumentos) {
    $antes = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $saida = & $exe @argumentos 2>&1 | Out-String
        $script:CodigoSaida = $LASTEXITCODE
    } catch {
        $saida = ''
        $script:CodigoSaida = 1
    } finally {
        $ErrorActionPreference = $antes
    }
    return $saida.Trim()
}

try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'E necessario Windows de 64 bits.' }

    # ------------------------------------------------------------------ 1. codigo
    Etapa '1/6' 'Copiando o programa...'
    if (-not (Test-Path (Join-Path $FonteDir 'app\main.py'))) { throw "Nao encontrei app\main.py em $FonteDir" }
    if ((Resolve-Path $FonteDir).Path -ne (Resolve-Path $InstallDir -ErrorAction SilentlyContinue).Path -and
        (Resolve-Path $FonteDir).Path -ne $CodigoDir) {
        # /E copia subpastas; nada e apagado no destino (a pasta data fica intacta).
        & robocopy $FonteDir $CodigoDir /E /NFL /NDL /NJH /NJS /NP /XD .git .venv venv data __pycache__ .pytest_cache installer\build /XF .env | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy falhou (codigo $LASTEXITCODE)" }
        $global:LASTEXITCODE = 0
    }

    # ------------------------------------------------------------------ 2. python
    Etapa '2/6' 'Preparando o Python portatil...'
    if (-not (Test-Path $PythonExe)) {
        $zip = Join-Path $CacheDir "python-$PythonVersion-embed-amd64.zip"
        Baixar $PythonZipUrl $zip
        Expand-Archive -Path $zip -DestinationPath $RuntimeDir -Force
    }
    $pth = Get-ChildItem -Path $RuntimeDir -Filter 'python*._pth' | Select-Object -First 1
    if ($pth) {
        $linhas = Get-Content $pth.FullName
        $linhas = $linhas | ForEach-Object { if ($_ -match '^\s*#\s*import site') { 'import site' } else { $_ } }
        if ($linhas -notcontains '..\codigo') { $linhas += '..\codigo' }
        Set-Content -Path $pth.FullName -Value $linhas -Encoding ASCII
    }
    $null = ExecutarQuieto $PythonExe @('-m', 'pip', '--version')
    if ($script:CodigoSaida -ne 0) {
        Write-Host '    instalando o pip...'
        $getpip = Join-Path $CacheDir 'get-pip.py'
        Baixar $GetPipUrl $getpip
        ExecutarPython @($getpip, '--no-warn-script-location')
    } else { Write-Host '    pip ja instalado.' }

    # ------------------------------------------------------------------ 3. dependencias
    Etapa '3/6' 'Instalando bibliotecas de IA (pode levar 10 a 30 minutos; downloads grandes)...'
    $temNvidia = $false
    if (-not $ForcarCpu) {
        $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
        if ($smi) { $null = ExecutarQuieto 'nvidia-smi' @('-L'); $temNvidia = ($script:CodigoSaida -eq 0) }
    }
    if ($temNvidia) {
        Write-Host '    Placa NVIDIA detectada: instalando PyTorch com CUDA.'
        ExecutarPython @('-m', 'pip', 'install', '--no-warn-script-location', 'torch', 'torchaudio', '--index-url', $TorchIndexUrl)
    } else {
        Write-Host '    Nenhuma placa NVIDIA detectada: instalando PyTorch para CPU (a transcricao sera bem mais lenta).' -ForegroundColor Yellow
        ExecutarPython @('-m', 'pip', 'install', '--no-warn-script-location', 'torch', 'torchaudio')
    }

    # Trava as versoes do PyTorch recem instaladas: sem isso, o pip pode trocar o PyTorch com CUDA
    # por outro (sem CUDA) ao instalar as demais bibliotecas.
    $freeze = ExecutarQuieto $PythonExe @('-m', 'pip', 'freeze', '--all')
    $travas = @($freeze -split "`r?`n" | Where-Object { $_ -match '^(torch|torchaudio)==' })
    $depArgs = @('-m', 'pip', 'install', '--no-warn-script-location')
    if ($travas.Count -gt 0) {
        $arqTravas = Join-Path $CacheDir 'constraints.txt'
        Set-Content -Path $arqTravas -Value $travas -Encoding ASCII
        $depArgs += @('-c', $arqTravas)
    }
    $depArgs += @('-r', (Join-Path $CodigoDir 'requirements.txt'))
    ExecutarPython $depArgs

    # Rede de seguranca: se mesmo assim o PyTorch ficou sem CUDA, reinstala so ele (sem mexer nas outras bibliotecas).
    if ($temNvidia) {
        $ver = ExecutarQuieto $PythonExe @('-c', 'import torch; print(torch.__version__)')
        $ver = ($ver -split "`r?`n" | Select-Object -Last 1)
        if ($ver -notmatch '\+cu') {
            Write-Host "    PyTorch sem CUDA detectado ($ver). Reinstalando a versao com CUDA..." -ForegroundColor Yellow
            ExecutarPython @('-m', 'pip', 'install', '--no-warn-script-location', '--force-reinstall', '--no-deps', 'torch', 'torchaudio', '--index-url', $TorchIndexUrl)
        }
    }

    # ------------------------------------------------------------------ 4. ffmpeg
    Etapa '4/6' 'Baixando o FFmpeg...'
    $ffmpegDir = Join-Path $ToolsDir 'ffmpeg'
    if (-not (Test-Path (Join-Path $ffmpegDir 'ffmpeg.exe'))) {
        $zip = Join-Path $CacheDir 'ffmpeg.zip'
        $tmp = Join-Path $CacheDir 'ffmpeg_tmp'
        Baixar $FfmpegUrl $zip
        if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $bin = Get-ChildItem -Path $tmp -Recurse -Filter 'ffmpeg.exe' | Select-Object -First 1
        if (-not $bin) { throw 'ffmpeg.exe nao encontrado no arquivo baixado.' }
        New-Item -ItemType Directory -Force -Path $ffmpegDir | Out-Null
        Copy-Item -Path (Join-Path $bin.DirectoryName '*') -Destination $ffmpegDir -Force
        Remove-Item $tmp -Recurse -Force
    } else { Write-Host '    ja instalado.' }

    # ------------------------------------------------------------------ 5. mkvtoolnix
    Etapa '5/6' 'Baixando o MKVToolNix (capitulos)...'
    $mkvDir = Join-Path $ToolsDir 'mkvtoolnix'
    if (-not (Test-Path (Join-Path $mkvDir 'mkvpropedit.exe'))) {
        $versao = $MkvFallback
        try {
            $html = (Invoke-WebRequest -Uri $MkvListUrl -UseBasicParsing).Content
            $achadas = [regex]::Matches($html, 'href="(\d+\.\d+)/"') | ForEach-Object { [version]$_.Groups[1].Value }
            if ($achadas) { $versao = ($achadas | Sort-Object -Descending | Select-Object -First 1).ToString() }
        } catch { Write-Host "    nao consegui consultar a ultima versao; usando $MkvFallback" -ForegroundColor Yellow }
        $sevenZip = Join-Path $CacheDir '7zr.exe'
        $arq = Join-Path $CacheDir "mkvtoolnix-$versao.7z"
        Baixar $SevenZipUrl $sevenZip
        Baixar "https://mkvtoolnix.download/windows/releases/$versao/mkvtoolnix-64-bit-$versao.7z" $arq
        $tmp = Join-Path $CacheDir 'mkv_tmp'
        if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
        $null = ExecutarQuieto $sevenZip @('x', $arq, "-o$tmp", '-y')
        if ($script:CodigoSaida -ne 0) { throw 'Falha ao extrair o MKVToolNix.' }
        $exe = Get-ChildItem -Path $tmp -Recurse -Filter 'mkvpropedit.exe' | Select-Object -First 1
        if (-not $exe) { throw 'mkvpropedit.exe nao encontrado no arquivo baixado.' }
        New-Item -ItemType Directory -Force -Path $mkvDir | Out-Null
        Copy-Item -Path (Join-Path $exe.DirectoryName '*') -Destination $mkvDir -Recurse -Force
        Remove-Item $tmp -Recurse -Force
    } else { Write-Host '    ja instalado.' }

    # ------------------------------------------------------------------ 6. configuracao e atalhos
    Etapa '6/6' 'Finalizando (configuracao e atalhos)...'
    $envFile = Join-Path $CodigoDir '.env'
    if (-not (Test-Path $envFile) -and (Test-Path (Join-Path $CodigoDir '.env.example'))) {
        Copy-Item (Join-Path $CodigoDir '.env.example') $envFile
    }

    $launcher = Join-Path $InstallDir 'Banido_Studio.bat'
    $bat = @(
        '@echo off',
        'set "ROOT=%~dp0"',
        'set "PATH=%ROOT%runtime;%ROOT%runtime\Scripts;%ROOT%runtime\Lib\site-packages\torch\lib;%ROOT%tools\ffmpeg;%ROOT%tools\mkvtoolnix;%PATH%"',
        'set "BANIDO_PYTHON=%ROOT%runtime\python.exe"',
        'call "%ROOT%codigo\Iniciar_Banido_Studio.bat"'
    )
    Set-Content -Path $launcher -Value $bat -Encoding ASCII

    if (-not $SemAtalho) {
        try {
            $shell = New-Object -ComObject WScript.Shell
            $lnk = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Banido Studio AI.lnk'))
            $lnk.TargetPath = $launcher
            $lnk.WorkingDirectory = $InstallDir
            $lnk.Description = 'Banido Studio AI'
            $lnk.Save()
        } catch { Write-Host "    nao consegui criar o atalho: $($_.Exception.Message)" -ForegroundColor Yellow }
    }

    # Verificacao rapida
    $info = ExecutarQuieto $PythonExe @('-c', "import torch; print(torch.__version__, 'sim' if torch.cuda.is_available() else 'nao')")
    $info = ($info -split "`r?`n" | Select-Object -Last 1)
    Write-Host ""
    Write-Host "PyTorch / CUDA disponivel: $info"
    if ($temNvidia -and $info -notmatch 'sim$') { Write-Host 'Aviso: placa NVIDIA encontrada, mas o PyTorch nao a enxerga. Atualize o driver da NVIDIA.' -ForegroundColor Yellow }

    Write-Host ""
    Write-Host 'INSTALACAO CONCLUIDA!' -ForegroundColor Green
    Write-Host "Abra o atalho 'Banido Studio AI' na Area de Trabalho (ou $launcher)."
    Write-Host 'Na primeira transcricao o modelo Whisper (cerca de 3 GB) sera baixado automaticamente.'
    try { Stop-Transcript | Out-Null } catch {}
    exit 0
}
catch {
    Write-Host ""
    Write-Host "ERRO: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Rode o instalador novamente: ele continua de onde parou.'
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}
