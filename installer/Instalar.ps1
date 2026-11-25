<#
  Banido Studio AI - instalador para Windows.

  O que faz (o programa fica em uma unica pasta, sem mexer no resto do computador):
    1. copia o programa para <InstallDir>\codigo
    2. pergunta onde guardar os DADOS (gravacoes, cortes, modelos), com lista numerada e janela de escolha
    3. baixa um Python portatil (runtime\) e instala as dependencias (PyTorch com CUDA, se houver placa NVIDIA)
    4. baixa FFmpeg e MKVToolNix portateis (tools\)
    5. cria o .env, o arquivo Banido_Studio.bat e um atalho na Area de Trabalho

  Pode ser executado de novo a qualquer momento: ele atualiza o codigo e completa o que faltar,
  sem apagar a pasta de dados (gravacoes, banco de dados, cortes, vozes).

  Obs.: este arquivo usa apenas caracteres ASCII de proposito (o PowerShell 5.1 le arquivos sem BOM como ANSI).
#>
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'BanidoStudio'),
    [string]$FonteDir = (Split-Path -Parent $PSScriptRoot),
    [string]$DadosDir = '',
    [switch]$SemAtalho,
    [switch]$ForcarCpu
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$PythonVersion   = '3.11.9'
$PythonZipUrl    = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$PythonZipSha256 = '009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b'
$GetPipUrl       = 'https://bootstrap.pypa.io/get-pip.py'
$TorchIndexUrl   = 'https://download.pytorch.org/whl/cu121'
$FfmpegAsset     = 'ffmpeg-master-latest-win64-gpl.zip'
$FfmpegUrl       = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/$FfmpegAsset"
$FfmpegSumsUrl   = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/checksums.sha256'
$SevenZipUrl     = 'https://www.7-zip.org/a/7zr.exe'
$MkvListUrl      = 'https://mkvtoolnix.download/windows/releases/'
$MkvFallback     = '86.0'

# Espacos minimos recomendados (em GB)
$GbPrograma = 10   # Python + PyTorch/CUDA + FFmpeg + MKVToolNix + downloads temporarios
$GbDados    = 15   # modelos (Whisper ~3 GB, SpeechBrain etc.) + folga para gravacoes e cortes

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

function VerificarSha256([string]$arquivo, [string]$esperado, [string]$nome) {
    if ([string]::IsNullOrWhiteSpace($esperado)) { return }
    $atual = (Get-FileHash -Path $arquivo -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($atual -ne $esperado.Trim().ToLowerInvariant()) {
        Remove-Item -LiteralPath $arquivo -Force -ErrorAction SilentlyContinue
        throw "Falha de integridade (SHA-256) em $nome. Esperado: $esperado | Obtido: $atual"
    }
    Write-Host "    integridade SHA-256 verificada ($nome)." -ForegroundColor Green
}

function ObterHashDeManifesto([string]$manifestoUrl, [string]$nomeArquivo) {
    try {
        $conteudo = (Invoke-WebRequest -Uri $manifestoUrl -UseBasicParsing).Content
        foreach ($linha in ($conteudo -split "`r?`n")) {
            if ($linha -match '^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$') {
                $hash = $Matches[1].ToLowerInvariant()
                $alvo = ($Matches[2].Trim() -split '[\\/]')[-1]
                if ($alvo -ieq $nomeArquivo) { return $hash }
            }
        }
    } catch {
        Write-Host "    aviso: nao foi possivel consultar manifesto SHA-256 em $manifestoUrl" -ForegroundColor Yellow
    }
    return $null
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

# Pergunta ao usuario; sem console interativo devolve o padrao.
function Perguntar([string]$texto, [string]$padrao) {
    try {
        $r = Read-Host $texto
        if ([string]::IsNullOrWhiteSpace($r)) { return $padrao }
        return $r.Trim().Trim('"')
    } catch { return $padrao }
}

function DiscosFixos {
    @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction SilentlyContinue | Where-Object { $_.FreeSpace -ne $null })
}

function GbLivresDe([string]$caminho) {
    try {
        $letra = ([IO.Path]::GetPathRoot((Resolve-Path -LiteralPath $caminho -ErrorAction Stop).Path)).TrimEnd('\')
    } catch {
        $letra = ([IO.Path]::GetPathRoot($caminho)).TrimEnd('\')
    }
    $d = DiscosFixos | Where-Object { $_.DeviceID -ieq $letra } | Select-Object -First 1
    if ($d) { return [math]::Round($d.FreeSpace / 1GB, 1) }
    return $null
}

# Escolha da pasta de dados sem precisar digitar caminho:
#   numero da lista (Enter = 1, o disco com mais espaco), letra do disco (ex.: E), P = janela de pastas,
#   ou, se preferir, um caminho completo.
function EscolherPastaDados($discos) {
    for ($tentativa = 1; $tentativa -le 3; $tentativa++) {
        Write-Host ''
        $i = 0
        foreach ($d in $discos) {
            $i++
            $marca = ''
            if ($i -eq 1) { $marca = '   <- recomendado (mais espaco livre)' }
            Write-Host ('    [{0}] Disco {1}   {2,7:N1} GB livres{3}' -f $i, $d.DeviceID, ($d.FreeSpace / 1GB), $marca)
        }
        Write-Host '    [P] Escolher outra pasta (abre uma janela)'
        $r = Perguntar 'Digite o numero e aperte Enter (apenas Enter = opcao 1)' '1'

        if ($r -match '^\d+$' -and [int]$r -ge 1 -and [int]$r -le $discos.Count) {
            return (Join-Path ($discos[[int]$r - 1].DeviceID + '\') 'BanidoStudio\data')
        }
        if ($r -match '^[pP]$') {
            try {
                Add-Type -AssemblyName System.Windows.Forms
                $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
                $dlg.Description = 'Escolha a pasta onde ficarao gravacoes, cortes e modelos de IA'
                $dlg.ShowNewFolderButton = $true
                if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
                    $p = $dlg.SelectedPath
                    if ($p.TrimEnd('\') -match '^[A-Za-z]:$') { $p = Join-Path $p 'BanidoStudio\data' }
                    return $p
                }
            } catch { Write-Host '    Nao consegui abrir a janela de pastas.' -ForegroundColor Yellow }
            continue
        }
        if ($r -match '^[A-Za-z]:?\\?$') {
            $letra = $r.Substring(0, 1).ToUpper()
            return ($letra + ':\BanidoStudio\data')
        }
        if ([IO.Path]::IsPathRooted($r)) { return $r }
        Write-Host "    Nao entendi '$r'. Digite o NUMERO da lista (por exemplo: 1)." -ForegroundColor Yellow
    }
    if ($discos.Count -gt 0) { return (Join-Path ($discos[0].DeviceID + '\') 'BanidoStudio\data') }
    return (Join-Path $CodigoDir 'data')
}

function LerEnv([string]$arq, [string]$chave) {
    if (-not (Test-Path $arq)) { return $null }
    foreach ($l in (Get-Content $arq)) {
        if ($l -match "^\s*$chave\s*=\s*(.*)$") { return $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return $null
}

# valor $null = comenta a linha (a pasta passa a ser derivada de BANIDO_BASE_DIR).
function GravarEnv([string]$arq, [string]$chave, [string]$valor) {
    $linhas = @()
    if (Test-Path $arq) { $linhas = @(Get-Content $arq) }
    $achou = $false
    $novas = New-Object System.Collections.Generic.List[string]
    foreach ($l in $linhas) {
        if ($l -match "^\s*$chave\s*=") {
            $achou = $true
            if ($null -ne $valor) { $novas.Add("$chave=`"$valor`"") } else { $novas.Add("# $l") }
        } else { $novas.Add($l) }
    }
    if (-not $achou -and $null -ne $valor) { $novas.Add("$chave=`"$valor`"") }
    [System.IO.File]::WriteAllLines($arq, $novas, (New-Object System.Text.UTF8Encoding($false)))
}

try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'E necessario Windows de 64 bits.' }

    # ------------------------------------------------------------------ 0. espaco em disco do programa
    $livreProg = GbLivresDe $InstallDir
    if ($null -ne $livreProg -and $livreProg -lt $GbPrograma) {
        Write-Host ""
        Write-Host "ATENCAO: o disco da instalacao tem so $livreProg GB livres e o programa precisa de cerca de $GbPrograma GB." -ForegroundColor Yellow
        Write-Host "Sugestao: libere espaco ou rode de novo com outro disco, por exemplo:" -ForegroundColor Yellow
        Write-Host "  powershell -ExecutionPolicy Bypass -File installer\Instalar.ps1 -InstallDir E:\BanidoStudio" -ForegroundColor Yellow
        $r = Perguntar 'Continuar mesmo assim? (S/N) [N]' 'N'
        if ($r -notmatch '^[sSyY]') { throw 'Instalacao cancelada por falta de espaco em disco.' }
    }

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

    # ------------------------------------------------------------------ 1b. pasta de dados (antes dos downloads grandes)
    $envFile = Join-Path $CodigoDir '.env'
    if (-not (Test-Path $envFile) -and (Test-Path (Join-Path $CodigoDir '.env.example'))) {
        Copy-Item (Join-Path $CodigoDir '.env.example') $envFile
    }
    $baseAtual = LerEnv $envFile 'BANIDO_BASE_DIR'
    $modelosAtual = LerEnv $envFile 'BANIDO_MODELS_DIR'
    $jaEscolhida = ($baseAtual -and [IO.Path]::IsPathRooted($baseAtual))

    if ($jaEscolhida) {
        Write-Host ""
        Write-Host "Pasta de dados ja configurada: $baseAtual"
    } else {
        Write-Host ""
        Write-Host 'Onde guardar os DADOS (gravacoes, cortes, modelos de IA, banco de dados)?' -ForegroundColor Cyan
        Write-Host "Recomendado: um disco com pelo menos $GbDados GB livres (de preferencia SSD)."
        $discos = @(DiscosFixos | Sort-Object FreeSpace -Descending)

        $escolha = if ($DadosDir) { $DadosDir } else { EscolherPastaDados $discos }
        if (-not [IO.Path]::IsPathRooted($escolha)) { throw "Informe um caminho completo (ex.: E:\BanidoStudio\data). Recebi: $escolha" }
        New-Item -ItemType Directory -Force -Path $escolha | Out-Null
        $teste = Join-Path $escolha '.teste_escrita'
        try { Set-Content -Path $teste -Value 'ok'; Remove-Item $teste -Force } catch { throw "Nao consigo gravar em $escolha" }

        $livreDados = GbLivresDe $escolha
        if ($null -ne $livreDados -and $livreDados -lt $GbDados) {
            Write-Host "ATENCAO: $escolha tem so $livreDados GB livres (recomendado: $GbDados GB). O modelo Whisper sozinho usa ~3,1 GB." -ForegroundColor Yellow
            $r = Perguntar 'Usar mesmo assim? (S/N) [N]' 'N'
            if ($r -notmatch '^[sSyY]') { throw 'Escolha outra pasta de dados com mais espaco e rode o instalador de novo.' }
        }

        GravarEnv $envFile 'BANIDO_BASE_DIR' ($escolha.Replace('\', '/'))
        # As demais pastas passam a ser derivadas de BANIDO_BASE_DIR (a menos que o usuario as defina depois).
        foreach ($k in 'BANIDO_OBS_DIR', 'BANIDO_CLIPS_DIR', 'BANIDO_VOICE_BANK_DIR', 'BANIDO_MODELS_DIR') {
            $v = LerEnv $envFile $k
            if ($v -and -not [IO.Path]::IsPathRooted($v)) { GravarEnv $envFile $k $null }
        }
        Write-Host "    dados em: $escolha" -ForegroundColor Green
        $baseAtual = $escolha
        $modelosAtual = LerEnv $envFile 'BANIDO_MODELS_DIR'
    }

    # Reaproveitar modelos ja baixados por uma instalacao antiga (evita baixar ~3 GB de novo).
    if (-not ($modelosAtual -and [IO.Path]::IsPathRooted($modelosAtual))) {
        $candidatos = @()
        foreach ($d in (DiscosFixos)) {
            foreach ($sub in 'Videos\BanidoStudio\models', 'BanidoStudio\models', 'BanidoStudio\data\models', 'data\models') {
                $p = Join-Path ($d.DeviceID + '\') $sub
                if ((Test-Path (Join-Path $p 'whisper')) -and ($candidatos -notcontains $p)) { $candidatos += $p }
            }
        }
        $destinoModelos = Join-Path $baseAtual 'models'
        $candidatos = @($candidatos | Where-Object { $_ -ne $destinoModelos })
        if ($candidatos.Count -gt 0) {
            Write-Host ""
            Write-Host "Encontrei modelos de IA ja baixados em: $($candidatos[0])" -ForegroundColor Cyan
            $r = Perguntar 'Reaproveitar esses modelos (evita baixar de novo)? (S/N) [S]' 'S'
            if ($r -match '^[sSyY]') {
                GravarEnv $envFile 'BANIDO_MODELS_DIR' ($candidatos[0].Replace('\', '/'))
                Write-Host "    modelos em: $($candidatos[0])" -ForegroundColor Green
            }
        }
    }

    # ------------------------------------------------------------------ 2. python
    Etapa '2/6' 'Preparando o Python portatil...'
    if (-not (Test-Path $PythonExe)) {
        $zip = Join-Path $CacheDir "python-$PythonVersion-embed-amd64.zip"
        Baixar $PythonZipUrl $zip
        VerificarSha256 $zip $PythonZipSha256 "Python $PythonVersion"
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
        $ffHash = ObterHashDeManifesto $FfmpegSumsUrl $FfmpegAsset
        VerificarSha256 $zip $ffHash 'FFmpeg'
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
        $mkvAsset = "mkvtoolnix-64-bit-$versao.7z"
        $arq = Join-Path $CacheDir "mkvtoolnix-$versao.7z"
        Baixar $SevenZipUrl $sevenZip
        Baixar "https://mkvtoolnix.download/windows/releases/$versao/$mkvAsset" $arq
        $mkvHash = ObterHashDeManifesto "https://mkvtoolnix.download/windows/releases/$versao/sha256sums.txt" $mkvAsset
        VerificarSha256 $arq $mkvHash "MKVToolNix $versao"
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

    # ------------------------------------------------------------------ 6. atalhos
    Etapa '6/6' 'Finalizando (atalhos e verificacao)...'

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

    # Libera o espaco dos downloads temporarios (zips). Uma nova execucao baixa de novo o que precisar.
    try { Get-ChildItem -Path $CacheDir -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue } catch {}

    Write-Host ""
    Write-Host 'INSTALACAO CONCLUIDA!' -ForegroundColor Green
    Write-Host "Dados (gravacoes, cortes, modelos): $baseAtual"
    Write-Host "Para abrir depois, use o atalho 'Banido Studio AI' na Area de Trabalho."
    Write-Host 'Na primeira transcricao o modelo Whisper (cerca de 3 GB) sera baixado automaticamente, se ainda nao existir.'
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
