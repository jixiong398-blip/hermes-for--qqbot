@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title QQBot Installer
cd /d "%~dp0"
cls

echo.
echo   ========================================
echo         QQBot - One-Click Installer
echo   ========================================
echo.
echo   This will install:
echo     - Python 3.12 (bundled)
echo     - Virtual environment
echo     - Hermes engine
echo     - Default config files
echo.
echo   Press any key to start, or close this window to cancel...
pause >nul
cls

:: ══════════════════════════════════════════════
echo.
echo   [Step 1/4] Installing Python 3.12...
echo.

if not exist "python-installer.exe" (
    echo   [ERROR] python-installer.exe not found.
    pause
    exit /b 1
)

python-installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
if %errorlevel% neq 0 (
    echo   [ERROR] Python installation failed.
    pause
    exit /b 1
)
echo   Python 3.12 installed.

:: Refresh PATH - Python installer adds to user PATH, needs new shell
:: We'll manually add to this session's PATH
set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%PATH%"

:: ══════════════════════════════════════════════
echo.
echo   [Step 2/4] Creating virtual environment...
echo.

python -m venv .venv 2>nul
if %errorlevel% neq 0 (
    echo   [ERROR] Failed to create virtual environment.
    pause
    exit /b 1
)
echo   Virtual environment created.

:: ══════════════════════════════════════════════
echo.
echo   [Step 3/4] Installing Hermes...
echo   This may take a few minutes (downloading packages)...
echo.

call .venv\Scripts\activate.bat
pip install -e hermes\ 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] Package installation failed.
    pause
    exit /b 1
)
echo   Hermes installed.

:: ══════════════════════════════════════════════
echo.
echo   [Step 4/4] Creating config files...
echo.

python scripts\install.py 2>&1
if %errorlevel% neq 0 (
    echo   [WARNING] Config setup had issues. Run manually:
    echo     scripts\install.py
)

echo.
echo   ========================================
echo         Installation Complete!
echo   ========================================
echo.
echo   Next step: double-click 配置API.bat
echo   to select your LLM provider and enter API key.
echo.
echo   Then: double-click start.bat to launch everything.
echo.
echo   Config files: %%USERPROFILE%%\.hermes\
echo.
pause
