@echo off
chcp 65001 >nul
title Instalador - Banido Studio AI
echo =======================================================
echo   INSTALADOR DO BANIDO STUDIO AI
echo   Nao precisa instalar Python, FFmpeg ou CUDA antes.
echo =======================================================
echo.
rem O instalador pergunta onde instalar e grava o caminho escolhido neste arquivo.
del "%TEMP%\banido_ultima_instalacao.txt" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\Instalar.ps1"
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" (
    echo [ERRO] A instalacao nao terminou. Veja o arquivo instalacao.log na pasta de instalacao.
    pause
    exit /b %RC%
)
rem Quando chamado pelo Iniciar_Banido_Studio.bat, quem abre o programa e ele.
if defined BANIDO_CHAMADO_PELO_INICIAR exit /b 0
echo Abrindo o Banido Studio AI...
set "LANCADOR=%LOCALAPPDATA%\BanidoStudio\Banido_Studio.bat"
if exist "%TEMP%\banido_ultima_instalacao.txt" set /p LANCADOR=<"%TEMP%\banido_ultima_instalacao.txt"
if exist "%LANCADOR%" start "" "%LANCADOR%"
exit /b 0
