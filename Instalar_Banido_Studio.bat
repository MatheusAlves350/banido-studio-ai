@echo off
chcp 65001 >nul
title Instalador - Banido Studio AI
echo =======================================================
echo   INSTALADOR DO BANIDO STUDIO AI
echo   Nao precisa instalar Python, FFmpeg ou CUDA antes.
echo =======================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\Instalar.ps1"
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" echo [ERRO] A instalacao nao terminou. Veja o arquivo instalacao.log na pasta de instalacao.
pause
exit /b %RC%
