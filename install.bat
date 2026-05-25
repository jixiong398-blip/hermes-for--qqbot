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
echo   Press any key to start...
pause >nul
cls

:: === Step 1: Python 3.12 ===
echo.
echo   [1/5] Installing Python 3.12...
echo.
if not exist "python-installer.exe" (
    echo   [ERROR] python-installer.exe not found
    pause & exit /b 1
)
python-installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
if %errorlevel% neq 0 (
    echo   [ERROR] Python install failed
    pause & exit /b 1
)
set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%PATH%"
echo   Python 3.12 OK

:: === Step 2: Node.js + Live2D ===
echo.
echo   [2/5] Setting up Live2D...
echo.
if exist "node\npm.cmd" (
    echo   Installing Live2D dependencies...
    cd modules\live2d
    call ..\..\node\npm.cmd install 2>nul
    cd ..\..
    if exist "modules\live2d\node_modules\electron" (
        echo   Live2D OK
    ) else (
        echo   [WARNING] Live2D install failed
        echo   Run: cd modules\live2d ^&^& ..\..\node\npm.cmd install
    )
) else (
    echo   [WARNING] Node.js not found - Live2D unavailable
)

:: === Step 3: Virtual env ===
echo.
echo   [3/5] Creating venv...
echo.
python -m venv .venv 2>nul
if %errorlevel% neq 0 (
    echo   [ERROR] venv failed
    pause & exit /b 1
)
echo   venv created

:: === Step 4: Hermes ===
echo.
echo   [4/5] Installing Hermes...
echo.
call .venv\Scripts\activate.bat
pip install -e hermes\ --no-deps 2>&1
pip install -r hermes\requirements.txt 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] pip install failed
    pause & exit /b 1
)
echo   Hermes installed

:: === Step 5: Config ===
echo.
echo   [5/5] Creating config...
echo.
python scripts\install.py 2>&1
if %errorlevel% neq 0 (
    echo   [WARNING] Config setup had issues
)

echo.
echo   ========================================
echo         Installation Complete!
echo   ========================================
echo.
echo   Next: PeiZhiAPI.bat - select LLM provider
echo   Then: start.bat
echo.
pause