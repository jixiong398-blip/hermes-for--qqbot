@echo off
chcp 65001 >nul
cd /d "%~dp0"
title QQBot

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] venv not found - run install.bat first
    pause
    exit /b 1
)

echo.
echo   ◆ QQBot
echo   =========
echo.

:: ── Clean up old processes ──
echo   [Clean] Stopping old services...
taskkill /FI "WINDOWTITLE eq Hermes Gateway" /F 2>nul
taskkill /FI "WINDOWTITLE eq Dashboard" /F 2>nul
taskkill /FI "WINDOWTITLE eq NapCat" /F 2>nul
timeout /t 2 >nul

set "PY=.venv\Scripts\python.exe"

:: ── Start services ──
echo   [NapCat] Launching...
if exist "napcat\launcher.bat" (
    start "NapCat" /D "napcat" launcher.bat
    echo   [NapCat] Scan QR code to login
)

echo   [Gateway] Launching...
start "Hermes Gateway" "%PY%" -m hermes_cli.main gateway

echo   [Dashboard] Launching...
start "Dashboard" "%PY%" modules\dashboard\server.py

timeout /t 3 >nul
start http://127.0.0.1:8899

echo.
echo   Dashboard: http://127.0.0.1:8899
echo   Press any key to stop...
pause >nul

taskkill /FI "WINDOWTITLE eq Hermes Gateway" /F 2>nul
taskkill /FI "WINDOWTITLE eq Dashboard" /F 2>nul
taskkill /FI "WINDOWTITLE eq NapCat" /F 2>nul
echo   Stopped.
