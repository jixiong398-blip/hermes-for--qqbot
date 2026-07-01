@echo off
chcp 65001 >nul 2>nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Hermes not installed -- run install.bat first
    pause
    exit /b 1
)

:: Kill anything already on port 8899
echo Stopping previous Dashboard instance...
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8899 "') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 >nul 2>nul

:: Check port is free
netstat -ano 2>nul | findstr ":8899 " >nul
if %errorlevel% equ 0 (
    echo [WARNING] Port 8899 still in use -- close other apps using it
)

echo Starting Dashboard on http://127.0.0.1:8899 ...
start "QQBot Dashboard" ".venv\Scripts\python.exe" "modules\dashboard\server.py"

timeout /t 2 >nul 2>nul

:: Open browser
start http://127.0.0.1:8899 2>nul

echo.
echo   Dashboard: http://127.0.0.1:8899
echo   Use the web panel to start NapCat / Gateway / Live2D.
echo.
echo   Press any key to stop Dashboard...
pause >nul

:: Kill Dashboard on port 8899
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8899 "') do (
    taskkill /F /PID %%a >nul 2>&1
)
echo Stopped.
