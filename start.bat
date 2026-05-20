@echo off
chcp 65001 >nul
cd /d "%~dp0"
title QQBot Control Center

:menu
cls
echo.
echo   ╔══════════════════════════════════╗
echo   ║     QQBot Control Center         ║
echo   ╠══════════════════════════════════╣
echo   ║  1. Start All Services           ║
echo   ║  2. Start NapCat (QQ)            ║
echo   ║  3. Start Gateway+Dashboard      ║
echo   ║  4. Stop All Services            ║
echo   ║  5. Open Dashboard               ║
echo   ║  0. Exit                         ║
echo   ╚══════════════════════════════════╝
echo.
set /p choice="  Choice: "

if "%choice%"=="1" goto start_all
if "%choice%"=="2" goto start_napcat
if "%choice%"=="3" goto start_gateway
if "%choice%"=="4" goto stop_all
if "%choice%"=="5" goto open_dash
if "%choice%"=="0" exit /b
goto menu

:start_all
call :start_napcat_impl
call :start_gateway_impl
call :open_dash_impl
echo All started. Dashboard: http://127.0.0.1:8899
pause
goto menu

:start_napcat
call :start_napcat_impl
pause
goto menu

:start_napcat_impl
if exist "napcat\launcher.bat" (
    start "NapCat" /D "napcat" launcher.bat
    echo [NapCat] Launched - scan QR code
) else (
    echo [NapCat] NOT FOUND
)
timeout /t 3 >nul
goto :eof

:start_gateway
call :start_gateway_impl
pause
goto menu

:start_gateway_impl
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
    start "Hermes Gateway" cmd /c "hermes gateway"
    echo [Gateway] Launched
)
if exist "modules\dashboard\server.py" (
    start "Dashboard" .venv\Scripts\python.exe modules\dashboard\server.py
    echo [Dashboard] Launched on :8899
)
timeout /t 3 >nul
goto :eof

:stop_all
taskkill /F /FI "WINDOWTITLE eq NapCat*" 2>nul
taskkill /F /FI "WINDOWTITLE eq Hermes Gateway*" 2>nul
taskkill /F /FI "WINDOWTITLE eq Dashboard*" 2>nul
echo Stopped.
pause
goto menu

:open_dash
call :open_dash_impl
goto menu

:open_dash_impl
start http://127.0.0.1:8899
goto :eof
