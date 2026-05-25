@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] venv not found - run install.bat first
    pause
    exit /b 1
)

:: Clean up old processes
echo Stopping old services...
taskkill /F /FI "WINDOWTITLE eq Hermes Gateway" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Dashboard" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq NapCat" >nul 2>&1
timeout /t 2 >nul

echo Starting Dashboard on http://127.0.0.1:8899 ...
start "Dashboard" .venv\Scripts\python.exe modules\dashboard\server.py

timeout /t 2 >nul
start http://127.0.0.1:8899

echo.
echo Dashboard: http://127.0.0.1:8899
echo Use the web panel to start NapCat / Gateway / Live2D.
echo.
echo Press any key to stop Dashboard...
pause >nul

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8899 "') do taskkill /F /PID %%a >nul 2>&1
echo Stopped.
