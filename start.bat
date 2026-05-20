@echo off
chcp 65001 >nul
cd /d "%~dp0"
title QQBot Control Center

:: Check prerequisites
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   [ERROR] venv not found!
    echo   Please run install.bat first.
    echo.
    pause
    exit /b 1
)

:menu
cls
echo.
echo   ╔══════════════════════════════════╗
echo   ║     QQBot Control Center         ║
echo   ╠══════════════════════════════════╣
echo   ║  1. Start All                    ║
echo   ║  2. Start NapCat (QQ bridge)     ║
echo   ║  3. Start Gateway + Dashboard    ║
echo   ║  4. Stop All                     ║
echo   ║  5. Open Dashboard (localhost:8899)║
echo   ║  0. Exit                         ║
echo   ╚══════════════════════════════════╝
echo.
set "choice="
set /p "choice=  Choice: "

if "%choice%"=="1" call :start_all & goto menu
if "%choice%"=="2" call :start_napcat & goto menu
if "%choice%"=="3" call :start_gateway & goto menu
if "%choice%"=="4" call :stop_all & goto menu
if "%choice%"=="5" call :open_dash & goto menu
if "%choice%"=="0" exit /b
goto menu

:: ─── Start all ───
:start_all
echo.
echo   === Starting All Services ===
call :start_napcat_impl
call :start_gateway_impl
echo.
echo   All started. Opening Dashboard...
start http://127.0.0.1:8899
pause
goto :eof

:: ─── NapCat ───
:start_napcat
call :start_napcat_impl
pause
goto :eof

:start_napcat_impl
if exist "napcat\launcher.bat" (
    echo   [NapCat] Launching...
    start "NapCat" /D "napcat" launcher.bat
    echo   [NapCat] Scan QR code to login
) else (
    echo   [NapCat] NOT FOUND in napcat\
)
goto :eof

:: ─── Gateway + Dashboard ───
:start_gateway
call :start_gateway_impl
pause
goto :eof

:start_gateway_impl
set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo   [ERROR] %PY% not found - run install.bat first
    goto :eof
)

echo   [Gateway] Launching...
start "Hermes Gateway" "%PY%" -m hermes_cli.main gateway
echo   [Gateway] Launched

if exist "modules\dashboard\server.py" (
    echo   [Dashboard] Launching on :8899...
    start "Dashboard" "%PY%" modules\dashboard\server.py
    echo   [Dashboard] Launched
) else (
    echo   [Dashboard] NOT FOUND
)
goto :eof

:: ─── Stop All ───
:stop_all
echo.
echo   === Stopping All Services ===

:: Kill by window title (Hermes Gateway)
taskkill /FI "WINDOWTITLE eq Hermes Gateway" /F 2>nul
if %errorlevel% equ 0 (echo   [Gateway] Stopped) else (echo   [Gateway] Not running)

:: Kill by window title (Dashboard)
taskkill /FI "WINDOWTITLE eq Dashboard" /F 2>nul
if %errorlevel% equ 0 (echo   [Dashboard] Stopped) else (echo   [Dashboard] Not running)

:: Kill NapCat (QQ.exe started by launcher)
taskkill /FI "WINDOWTITLE eq QQ*" /F 2>nul
if %errorlevel% equ 0 (echo   [NapCat/QQ] Stopped) else (echo   [NapCat/QQ] Not running)

:: Kill python processes for gateway/dashboard
taskkill /IM python.exe /FI "WINDOWTITLE eq Hermes Gateway" /F 2>nul

echo   Done.
pause
goto :eof

:: ─── Open Dashboard ───
:open_dash
start http://127.0.0.1:8899
goto :eof
