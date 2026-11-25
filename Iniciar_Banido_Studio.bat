@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Banido Studio AI - 4K 60fps AV1 Pipeline

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
cd /d "%APP_DIR%"
set "PYTHONPATH=%APP_DIR%"

set "HOST=127.0.0.1"
set "PORT=8765"
if exist ".env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
        set "K=%%~A"
        set "V=%%~B"
        for /f "tokens=* delims= " %%K in ("!K!") do set "K=%%K"
        if defined V (
            set "V=!V:"=!"
            set "V=!V:'=!"
            for /f "tokens=1 delims=#" %%V in ("!V!") do set "V=%%V"
            for /f "tokens=* delims= " %%V in ("!V!") do set "V=%%V"
        )
        if /i "!K!"=="BANIDO_PORT" if not "!V!"=="" set "PORT=!V!"
        if /i "!K!"=="BANIDO_HOST" if not "!V!"=="" set "HOST=!V!"
    )
)

set "PYTHON_EXE="
if exist "%APP_DIR%\.venv\Scripts\python.exe" set "PYTHON_EXE=%APP_DIR%\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%APP_DIR%\venv\Scripts\python.exe" set "PYTHON_EXE=%APP_DIR%\venv\Scripts\python.exe"
if not defined PYTHON_EXE (
    where python >nul 2>&1
    if !errorlevel! equ 0 set "PYTHON_EXE=python"
)

rem --- Se as dependencias nao estao instaladas, chama o instalador (Windows) e abre o programa instalado ---
set "NEEDS_INSTALL="
if not defined PYTHON_EXE set "NEEDS_INSTALL=1"
if defined PYTHON_EXE (
    "%PYTHON_EXE%" -c "import uvicorn, fastapi" >nul 2>&1
    if !errorlevel! neq 0 set "NEEDS_INSTALL=1"
)
if defined NEEDS_INSTALL if not defined BANIDO_NO_AUTOINSTALL if exist "%APP_DIR%\Instalar_Banido_Studio.bat" (
    echo.
    echo Este programa ainda nao foi instalado neste computador.
    echo Vou abrir o instalador agora. Ele faz tudo sozinho.
    echo.
    set "BANIDO_CHAMADO_PELO_INICIAR=1"
    call "%APP_DIR%\Instalar_Banido_Studio.bat"
    if !errorlevel! neq 0 exit /b 1
    if exist "%LOCALAPPDATA%\BanidoStudio\Banido_Studio.bat" (
        set "BANIDO_NO_AUTOINSTALL=1"
        call "%LOCALAPPDATA%\BanidoStudio\Banido_Studio.bat"
    )
    exit /b 0
)

if not defined PYTHON_EXE (
    echo [ERRO] Python nao encontrado. Crie uma venv e instale as dependencias:
    echo   python -m venv .venv
    echo   .venv\Scripts\activate
    echo   pip install -r requirements.txt
    pause
    exit /b 1
)

echo =======================================================
echo           INICIANDO BANIDO STUDIO AI
echo =======================================================
echo Diretorio: %APP_DIR%
echo Python:    %PYTHON_EXE%
echo Endereco:  http://%HOST%:%PORT%
echo.
echo [1/2] Verificando porta %PORT% e agendando abertura do navegador...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    set "IS_PY="
    for /f "tokens=1" %%p in ('tasklist /FI "PID eq %%a" /NH 2^>nul ^| findstr /I "python uvicorn"') do set "IS_PY=1"
    if defined IS_PY (
        echo Encerrando instancia anterior do servidor Python na porta %PORT% ^(PID %%a^)...
        taskkill /F /T /PID %%a >nul 2>&1
    ) else (
        echo [AVISO] A porta %PORT% esta em uso por outro processo ^(PID %%a^). Altere BANIDO_PORT no .env se necessario.
    )
)

set "OPEN_HOST=%HOST%"
if "%OPEN_HOST%"=="0.0.0.0" set "OPEN_HOST=localhost"
if "%OPEN_HOST%"=="127.0.0.1" set "OPEN_HOST=localhost"

start /b powershell -NoProfile -Command "for($i=0;$i -lt 25;$i++){ Start-Sleep -Milliseconds 500; try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/status' -UseBasicParsing -TimeoutSec 1; if($r.StatusCode -eq 200){ Start-Process 'http://%OPEN_HOST%:%PORT%'; break } } catch {} }"

echo [2/2] Servidor ativo em: http://%OPEN_HOST%:%PORT%
echo.
echo (Mantenha esta janela minimizada enquanto usa o sistema)
echo =======================================================
echo.

rem Codigo de saida 3 = o painel pediu reinicio (ex.: apos atualizar pelo GitHub).
rem O laco inteiro fica num bloco ( ... ): o cmd le o bloco todo antes de executar,
rem entao uma atualizacao que reescreva este .bat nao quebra a execucao em andamento.
set "BANIDO_LAUNCHER=1"
for /l %%N in (0,0,1) do (
    "%PYTHON_EXE%" -m uvicorn app.main:app --host %HOST% --port %PORT%
    if !errorlevel! neq 3 (
        echo.
        echo Servidor encerrado.
        pause
        exit
    )
    echo.
    echo [ATUALIZACAO] Reiniciando o Banido Studio com a nova versao...
    echo.
)
