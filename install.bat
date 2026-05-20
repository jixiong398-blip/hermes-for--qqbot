@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo   ◆ QQBot Installer
echo   ==================
echo.
echo   [1/4] Creating venv...
python -m venv .venv
if %errorlevel% neq 0 ( echo Python not found! Install python.org & pause & exit /b 1 )
echo   [2/4] Installing dependencies...
call .venv\Scripts\activate.bat
pip install -e hermes\ -q
echo   [3/4] Running setup...
python scripts\install.py
echo   [4/4] Done!
echo.
echo   ┌─────────────────────────────────────┐
echo   │  1. Edit SOUL.md   (character)      │
echo   │  2. Edit config.yaml (API key)      │
echo   │  3. Run start.bat                   │
echo   └─────────────────────────────────────┘
pause
