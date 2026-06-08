@echo off
chcp 65001 >nul
cd /d "%~dp0"
cls

echo.
echo   ========================================
echo       Live2D - Install Dependencies
echo   ========================================
echo.

:: Check if already installed
if exist "modules\live2d\node_modules\electron\dist\electron.exe" (
    echo   Live2D already installed.
    pause
    exit /b 0
)

:: Step 1: Get Node.js if missing
if not exist "node\node.exe" (
    echo   [1/2] Downloading Node.js...
    powershell -Command "Invoke-WebRequest -Uri 'https://nodejs.org/dist/v22.11.0/node-v22.11.0-win-x64.zip' -OutFile '%TEMP%\node-l2d.zip'" 2>nul
    if %errorlevel% neq 0 (
        echo   [ERROR] Download failed - check internet
        pause & exit /b 1
    )
    powershell -Command "Expand-Archive -Path '%TEMP%\node-l2d.zip' -DestinationPath '%TEMP%\node-l2d' -Force"
    xcopy "%TEMP%\node-l2d\node-v22.11.0-win-x64\*" "node\" /E /Y /Q >nul
    echo   Node.js installed
) else (
    echo   [1/2] Node.js found
)

:: Step 2: npm install (with China mirror for faster download)
echo   [2/2] Installing Live2D dependencies...
echo   This downloads Electron (~150MB), may take a few minutes...
cd modules\live2d
set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
call ..\..\node\npm.cmd install --registry=https://registry.npmmirror.com 2>&1
cd ..\..

if exist "modules\live2d\node_modules\electron\dist\electron.exe" (
    echo.
    echo   ========================================
    echo       Live2D Ready!
    echo   ========================================
) else (
    echo.
    echo   [ERROR] Installation failed
    echo   Run manually:
    echo     cd modules\live2d
    echo     ..\..\node\npm.cmd install
)

pause