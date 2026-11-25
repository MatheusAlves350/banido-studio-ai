@echo off
chcp 65001 >nul
title Desinstalador - Banido Studio AI
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\Desinstalar.ps1"
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%
