@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] venv not found - run install.bat first
    pause
    exit /b 1
)

echo.
echo   ========================================
echo     Fix NapCat - Enable WS/HTTP ports
echo   ========================================
echo.
echo   Make sure NapCat is running and you have logged in.
echo.

.venv\Scripts\python scripts\fix_napcat.py
pause