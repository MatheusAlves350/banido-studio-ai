@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Banido Studio AI - 4K 60fps AV1 Pipeline

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
cd /d "%APP_DIR%"
set "PYTHONPATH=%APP_DIR%"

set "PORT=8765"
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        set "K=%%A"
        set "V=%%B"
        if /i "!K!"=="BANIDO_PORT" if not "!V!"=="" set "PORT=!V!"
    )
)

set "PYTHON_EXE="
if exist "%APP_DIR%\.venv\Scripts\python.exe" set "PYTHON_EXE=%APP_DIR%\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%APP_DIR%\venv\Scripts\python.exe" set "PYTHON_EXE=%APP_DIR%\venv\Scripts\python.exe"
if not defined PYTHON_EXE (
    where python >nul 2>&1
    if !errorlevel! equ 0 set "PYTHON_EXE=python"
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
echo Porta:     %PORT%
echo.
echo [1/2] Preparando porta %PORT% e agendando abertura do navegador...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    echo Encerrando instancia anterior na porta %PORT% (PID %%a^)...
    taskkill /F /T /PID %%a >nul 2>&1
)

start /b powershell -NoProfile -Command "for($i=0;$i -lt 25;$i++){ Start-Sleep -Milliseconds 500; try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/status' -UseBasicParsing -TimeoutSec 1; if($r.StatusCode -eq 200){ Start-Process 'http://localhost:%PORT%'; break } } catch {} }"

echo [2/2] Servidor ativo em: http://localhost:%PORT%
echo.
echo (Mantenha esta janela minimizada enquanto usa o sistema)
echo =======================================================
echo.

"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
pause
