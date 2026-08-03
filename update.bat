@echo off
chcp 65001 >nul 2>nul
set PYTHONIOENCODING=utf-8
title QQBot Updater
cd /d "%~dp0"

set "GITHUB_REPO=jixiong398-blip/hermes-for--qqbot"
set "TEMP_DIR=%TEMP%\qqbot-update-%RANDOM%"
set "ZIP_FILE=%TEMP_DIR%\bot-template.zip"

echo.
echo   ========================================
echo       QQBot - One-Click Updater
echo   ========================================
echo.
echo   Downloads latest version from GitHub,
echo   updates code while keeping your config.
echo.
echo   Protected (not overwritten):
echo     - config.yaml
echo     - SOUL.md
echo     - .env
echo.
echo   Press any key to start...
pause >nul
cls

:: ===== Step 1: Clean temp =====
echo.
echo   [1/4] Preparing...
if exist "%TEMP_DIR%" rmdir /s /q "%TEMP_DIR%" >nul
mkdir "%TEMP_DIR%" >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] Cannot create temp directory
    pause & exit /b 1
)

:: ===== Step 2: Download =====
echo.
echo   [2/4] Downloading latest release...
echo   This may take a few minutes...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$url='https://github.com/%GITHUB_REPO%/archive/refs/heads/main.zip';" ^
  "Write-Host '  Downloading...';" ^
  "try { Invoke-WebRequest -Uri $url -OutFile '%ZIP_FILE%' -UseBasicParsing } catch {" ^
  "  Write-Host '  [ERROR] Download failed - check internet'; exit 1" ^
  "};" ^
  "Write-Host '  Download complete'"

if not exist "%ZIP_FILE%" (
    echo   [ERROR] Download failed
    rmdir /s /q "%TEMP_DIR%" >nul
    pause & exit /b 1
)

:: ===== Step 3: Extract and update =====
echo.
echo   [3/4] Extracting and updating files...
echo.

:: Extract to temp
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Expand-Archive -Path '%ZIP_FILE%' -DestinationPath '%TEMP_DIR%' -Force"

:: Find the extracted directory (GitHub adds '-main' suffix)
for /d %%d in ("%TEMP_DIR%\hermes-for--qqbot-*") do set "SRC_DIR=%%d"
if not exist "%SRC_DIR%" (
    echo   [ERROR] Extraction failed
    rmdir /s /q "%TEMP_DIR%" >nul
    pause & exit /b 1
)
echo   Source: %SRC_DIR%

:: ---- Copy updated files ----
echo   Updating hermes engine...
robocopy "%SRC_DIR%\hermes" "hermes" /E /NFL /NDL /NJH /NJS /NC /NS >nul

echo   Updating modules...
robocopy "%SRC_DIR%\modules\dashboard" "modules\dashboard" /E /NFL /NDL /NJH /NJS /NC /NS >nul

echo   Updating templates...
robocopy "%SRC_DIR%\templates" "templates" /E /NFL /NDL /NJH /NJS /NC /NS >nul

echo   Updating scripts...
robocopy "%SRC_DIR%\extras\scripts" "extras\scripts" /E /NFL /NDL /NJH /NJS /NC /NS >nul

echo   Updating batch files...
for %%f in (install.bat start.bat FixNapCat.bat Stop-All.bat "PeiZhiAPI.bat") do (
    if exist "%SRC_DIR%\%%f" copy /y "%SRC_DIR%\%%f" "%%f" >nul
)

echo   Updating version file...
if exist "%SRC_DIR%\VERSION" copy /y "%SRC_DIR%\VERSION" "VERSION" >nul

:: ---- Copy new files (electron bundles, installers that may have changed) ----
echo   Checking for updated bundles...
if exist "%SRC_DIR%\electron-offline.zip.001" copy /y "%SRC_DIR%\extras\electron-offline.zip.001" "extras\electron-offline.zip.001" >nul
if exist "%SRC_DIR%\electron-offline.zip.002" copy /y "%SRC_DIR%\extras\electron-offline.zip.002" "extras\electron-offline.zip.002" >nul
if exist "%SRC_DIR%\python-installer.exe" copy /y "%SRC_DIR%\extras\python-installer.exe" "extras\python-installer.exe" >nul
if exist "%SRC_DIR%\nodejs.zip" copy /y "%SRC_DIR%\extras\nodejs.zip" "extras\nodejs.zip" >nul

echo   Done updating files.

:: ===== Step 4: Update Python dependencies =====
echo.
echo   [4/4] Updating Python dependencies...
if exist ".venv\Scripts\pip.exe" (
    echo   Upgrading pip packages...
    call .venv\Scripts\pip.exe install --upgrade -r "%SRC_DIR%\hermes\requirements.txt" 2>&1
    echo   Dependencies updated.
) else (
    echo   [SKIP] venv not found - run install.bat for fresh install
)

:: ===== Step 5: Run upgrade.py (sync to ~/.hermes) =====
echo.
echo   [5/5] Applying upgrade to HERMES_HOME...
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python "%SRC_DIR%\scripts\upgrade.py" "%SRC_DIR%" 2>&1
) else (
    echo   [SKIP] venv not found - run install.bat first
)

:: ===== Cleanup =====
rmdir /s /q "%TEMP_DIR%" >nul

echo.
echo   ========================================
echo         Update Complete!
echo   ========================================
echo.
echo   Your config files are safe:
echo     config.yaml ^- untouched
echo     SOUL.md    ^- untouched
echo     .env       ^- untouched
echo.
echo   Restart your bot to apply changes.
echo   (Run start.bat to launch Dashboard)
echo.
pause
