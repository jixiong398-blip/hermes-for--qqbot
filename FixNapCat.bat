@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] venv not found - run install.bat first
    pause
    exit /b 1
)

echo.
echo   ========================================
echo     Fix NapCat - Enable WS/HTTP Ports
echo   ========================================
echo.
echo   Run this AFTER logging into NapCat.
echo.

.venv\Scripts\python scripts\fix_napcat.py
pause