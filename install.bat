@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo   QQBot Installer
echo   ================
echo.

echo   [1/4] Creating venv...
python -m venv .venv 2>nul
if %errorlevel% neq 0 (
    python3 -m venv .venv 2>nul
    if %errorlevel% neq 0 (
        echo   [ERROR] Python not found. Install from https://python.org
        pause
        exit /b 1
    )
)

echo   [2/4] Installing dependencies...
call .venv\Scripts\activate.bat
pip install -e hermes\ -q
if %errorlevel% neq 0 (
    echo   [ERROR] pip install failed
    pause
    exit /b 1
)

echo   [3/4] Running setup...
python scripts\install.py
if %errorlevel% neq 0 (
    echo   [ERROR] setup failed
    pause
    exit /b 1
)

echo   [4/4] Done.
echo.
echo   Next steps:
echo     1. Edit config.yaml  - set your API key
echo     2. Edit SOUL.md      - write character persona
echo     3. Run start.bat
echo.
pause
