@echo off
chcp 65001 >nul 2>nul
set PYTHONIOENCODING=utf-8
title QQBot Installer v0.10.0
cd /d "%~dp0"
cls

set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
set "PY_ROOT=%LocalAppData%\Programs\Python\Python312"
set "SCRIPT_DIR=%~dp0"

echo.
echo   ================================================
echo         QQBot - Offline-First Installer v0.10.0
echo   ================================================
echo.
echo   Built-in offline packages:
echo     [*] Python 3.12  - python-installer.exe
echo     [*] Node.js      - nodejs.zip
echo     [*] Live2D models (Cubism 4/5) - pre-installed
echo     [*] Live2D engine - electron-offline.zip (if present)
echo.
echo   Internet needed (once):
echo     [*] Hermes Python deps (~100MB)
echo     [*] Live2D Electron (only if offline bundle missing, ~150MB)
echo.
echo   Press any key to start...
pause >nul
cls

:: ===== Step 1: Python 3.12 =====
echo.
echo   [1/6] Python 3.12...
echo.

set PY_OK=0
if exist "%PY%" (
    for /f "tokens=2" %%v in ('""%PY%" --version" 2^>^&1') do (
        echo %%v | findstr "3.12" >nul && set PY_OK=1
    )
)
if %PY_OK% equ 0 (
    where python >nul 2>&1
    if %errorlevel% equ 0 (
        for /f "tokens=2" %%v in ('python --version 2^>^&1') do (
            echo %%v | findstr "3.12" >nul && set PY_OK=1
        )
    )
)
if %PY_OK% equ 1 (
    echo   Python 3.12 already installed -- skipped
    goto :step2
)

if not exist "python-installer.exe" (
    echo   [ERROR] python-installer.exe missing
    pause & exit /b 1
)
echo   Installing Python 3.12 (offline)...
python-installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
if %errorlevel% neq 0 (
    echo   [ERROR] Installation failed - try running as Admin
    pause & exit /b 1
)
set "PATH=%PY_ROOT%;%PY_ROOT%\Scripts;%PATH%"
echo   Python 3.12 installed -- OK
:step2

:: Live2D Models are now bundled in models/ directory (Cubism 4/5), no extraction needed.
echo.
echo   [2/6] Live2D Models — download via Dashboard
echo.
echo   Live2D models are included in modules\live2d\assets\models\
echo   No extraction needed.
echo.
:step3

:: ===== Step 3: Node.js =====
echo.
echo   [3/6] Node.js...
echo.
if exist "node\node.exe" (
    echo   Already set up -- skipped
    goto :step4
)
if not exist "nodejs.zip" (
    echo   [SKIP] nodejs.zip not found
    goto :step4
)
echo   Extracting Node.js (offline)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path 'nodejs.zip' -DestinationPath 'node' -Force" 2>nul
if %errorlevel% neq 0 (
    tar -xf "nodejs.zip" -C "node" 2>nul
)
for /d %%d in (node\node-*) do (
    if exist "%%d\node.exe" (
        xcopy "%%d\*" "node\" /E /Y /Q >nul
        rmdir /s /q "%%d" 2>nul
    )
)
if exist "node\node.exe" (echo   Node.js extracted -- OK) else if exist "node\npm.cmd" (echo   Node.js extracted -- OK) else (echo   [WARNING] Extraction may have failed)
:step4

:: ===== Step 4: Live2D Electron =====
echo.
echo   [4/6] Live2D Engine (Electron)...
echo.
if exist "modules\live2d\node_modules\electron\" (
    echo   Already installed -- skipped
    goto :step5
)
if exist "electron-offline.zip.001" (
    echo   Combining split archive (offline)...
    copy /b "electron-offline.zip.001"+"electron-offline.zip.002" "electron-offline.zip" >nul
    if exist "electron-offline.zip" (
        echo   Extracting Live2D engine (offline)...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path 'electron-offline.zip' -DestinationPath 'modules\live2d' -Force" 2>nul
        if %errorlevel% neq 0 (
            tar -xf "electron-offline.zip" -C "modules\live2d" 2>nul
        )
        if exist "modules\live2d\node_modules\electron\" (
            echo   Live2D engine extracted -- OK
            echo   Installing JS dependencies...
            cd modules\live2d
            call ..\..\node\npm.cmd install --registry=https://registry.npmmirror.com 2>&1
            cd ..\..
            del "electron-offline.zip" 2>nul
            goto :step5
        )
    )
)
if exist "electron-offline.zip" (
    echo   Extracting Live2D engine (offline)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path 'electron-offline.zip' -DestinationPath 'modules\live2d' -Force" 2>nul
    if %errorlevel% neq 0 (
        tar -xf "electron-offline.zip" -C "modules\live2d" 2>nul
    )
    if exist "modules\live2d\node_modules\electron\" (
        echo   Live2D engine extracted -- OK
        goto :step5
    )
)
if not exist "node\npm.cmd" (
    echo   [SKIP] Node.js missing - cannot install Live2D engine
    goto :step5
)
echo   Downloading Electron via npm (~150MB, internet required)...
cd modules\live2d
set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
call ..\..\node\npm.cmd install --registry=https://registry.npmmirror.com
cd ..\..
if exist "modules\live2d\node_modules\electron\" (echo   Live2D engine installed -- OK) else (echo   [SKIP] Install failed - retry with internet: cd modules\live2d ^&^& ..\..\node\npm.cmd install)
:step5

:: ===== Step 5: Hermes Engine (modified) =====
echo.
echo   [5/6] Hermes Engine (our modified version)...
echo.
if exist ".venv\Scripts\python.exe" (
    echo   Already installed -- skipped
    goto :step6
)
set "PYTHON_BIN=python"
if exist "%PY%" (set "PYTHON_BIN=%PY%")
echo   Creating venv...
"%PYTHON_BIN%" -m venv .venv 2>nul
if %errorlevel% neq 0 (
    echo   [ERROR] Cannot create venv - is Python 3.12 installed?
    pause & exit /b 1
)
call .venv\Scripts\activate.bat
echo   Installing Hermes engine (with memory system, OneBot adapter)...
pip install -e "%SCRIPT_DIR%hermes" --no-deps 2>&1
if %errorlevel% neq 0 (pip install -e "%SCRIPT_DIR%hermes" 2>&1)
echo   Installing Python dependencies (~100MB, internet required)...
pip install -r "%SCRIPT_DIR%hermes\requirements.txt" 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] Dependency install failed - check internet
    echo   Try mirror: pip install -r hermes\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause & exit /b 1
)
echo   Hermes engine installed -- OK
:step6

:: ===== Step 6: Base Config =====
echo.
echo   Config...
echo.
if not exist ".venv\Scripts\python.exe" (
    echo   [ERROR] Hermes not installed
    pause & exit /b 1
)
.venv\Scripts\python "%SCRIPT_DIR%scripts\install.py" 2>&1
echo   Base config created -- OK

:: ===== Final =====
echo.
echo   ================================================
echo         Installation Complete!
echo   ================================================
echo.
echo   Environment status:
if exist ".venv\Scripts\python.exe" (echo     [OK] Hermes Engine) else (echo     [!!] Hermes Engine MISSING)
if exist "node\node.exe" (echo     [OK] Node.js) else (echo     [--] Node.js (optional))
if exist "modules\live2d\assets\models\" (echo     [OK] Live2D Models) else (echo     [--] Live2D Models)
if exist "modules\live2d\node_modules\electron\" (echo     [OK] Live2D Engine) else (echo     [--] Live2D Engine)
echo.
echo   Next Steps:
echo     1. Start NapCat:   napcat\napcat.bat ^(scan QR to login^)
echo     2. Open dashboard: start.bat ^(configure WS:3001/HTTP:3000 ports per guide^)
echo     3. Set API keys:   "PeiZhiAPI.bat"
echo     4. Create SOUL:    Edit templates\SOUL.md ^> run soul replacer
echo     5. Start bot:      start.bat
echo.
pause
