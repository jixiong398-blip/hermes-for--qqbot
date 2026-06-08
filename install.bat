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
echo   This installs everything needed:
echo     - Python 3.12 (bundled)
echo     - Node.js v22.11 (bundled)
echo     - Live2D assets (bundled)
echo     - Hermes engine (needs internet ~5min)
echo     - Live2D Electron (needs internet ~150MB)
echo     - Default config files
echo.
echo   Requires: internet for steps 4 and 5
echo.
echo   Press any key to start...
pause >nul
cls

:: ===== Step 1: Python 3.12 (bundled, no internet) =====
echo.
echo   [1/6] Installing Python 3.12...
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
echo   Python 3.12 installed

:: ===== Step 2: Extract Live2D figures (no internet) =====
echo.
echo   [2/6] Extracting Live2D models...
echo.
if exist "modules\live2d\assets\figures.zip" (
    if not exist "modules\live2d\assets\figure\" (
        powershell -Command "Expand-Archive -Path 'modules\live2d\assets\figures.zip' -DestinationPath 'modules\live2d\assets\figure' -Force"
        echo   11 characters extracted
    ) else (
        echo   Already extracted
    )
) else (
    echo   [WARNING] figures.zip not found - Live2D unavailable
)

:: ===== Step 3: Node.js (bundled, no internet) =====
echo.
echo   [3/6] Setting up Node.js...
echo.
if not exist "node\node.exe" (
    if exist "nodejs.zip" (
        powershell -Command "Expand-Archive -Path 'nodejs.zip' -DestinationPath 'node' -Force"
        if exist "node\node-v22.11.0-win-x64\node.exe" (
            xcopy "node\node-v22.11.0-win-x64\*" "node\" /E /Y /Q >nul
            rmdir /s /q "node\node-v22.11.0-win-x64" 2>nul
        )
        echo   Node.js extracted from bundle
    ) else (
        echo   [ERROR] nodejs.zip not found
        pause & exit /b 1
    )
) else (
    echo   Node.js already installed
)

:: ===== Step 4: Live2D deps (needs internet ~150MB, or pre-installed) =====
echo.
echo   [4/6] Installing Live2D...
if exist "modules\live2d\node_modules\electron\" (
    echo   Live2D already installed (bundled in release)
) else (
    echo   Downloading Electron (~150MB), please wait...
    cd modules\live2d
    set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
    call ..\..\node\npm.cmd install --registry=https://registry.npmmirror.com
    cd ..\..
    if exist "modules\live2d\node_modules\electron" (
        echo   Live2D installed
    ) else (
        echo   [ERROR] Live2D install failed
        echo   Check internet or run: cd modules\live2d ^&^& ..\..\node\npm.cmd install
        pause
    )
)

:: ===== Step 5: Python venv + Hermes (needs internet ~5min, or pre-installed) =====
echo.
echo   [5/6] Installing Hermes...
if exist ".venv\Scripts\python.exe" (
    echo   Hermes already installed (bundled in release)
) else (
    python -m venv .venv 2>nul
    if %errorlevel% neq 0 (
        echo   [ERROR] venv failed
        pause & exit /b 1
    )
    call .venv\Scripts\activate.bat
    pip install -e hermes\ --no-deps 2>&1
    pip install -r hermes\requirements.txt 2>&1
    if %errorlevel% neq 0 (
        echo   [ERROR] pip install failed
        pause & exit /b 1
    )
    echo   Hermes installed
)

:: ===== Step 6: Config =====
echo.
echo   [6/6] Creating config...
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
echo   Verification:
python --version 2>nul && echo     Python: OK || echo     Python: MISSING
if exist "node\node.exe" (echo     Node.js: OK) else (echo     Node.js: MISSING)
if exist "modules\live2d\node_modules\electron\" (echo     Live2D: OK) else (echo     Live2D: install later)
if exist ".venv\Scripts\python.exe" (echo     Hermes: OK) else (echo     Hermes: MISSING)
echo.
echo   Next steps:
echo     1. Run PeiZhiAPI.bat - set up API + bot QQ
echo     2. Run start.bat
echo     3. Login to NapCat (scan QR)
echo     -> WS :3001 + HTTP :3000 auto-configured!
echo.
pause