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

:: ---- Detect legacy layout (v0.14.2 or older: flat hermes/, no hermes\core) ----
set "MIGRATED=0"
if exist "hermes\gateway\" if not exist "hermes\core\" (
    echo.
    echo   ============================================
    echo     [MIGRATION] Legacy layout detected!
    echo   ============================================
    echo   Your engine uses the OLD flat layout (hermes\gateway\...).
    echo   v0.14.3+ moved the engine into hermes\core\.
    echo   Your data in ~\.hermes and config files are NOT touched.
    echo   The migration will:
    echo     1. Back up  hermes\  -^>  hermes.bak.^<date^>  (rollback point)
    echo     2. Ask you BEFORE removing the old flat files
    echo     3. Copy the new hermes\core structure
    echo     4. Rebuild the Python environment
    echo.
    choice /c yn /m "Run automatic migration now"
    if errorlevel 2 (
        echo   [ABORT] Migration skipped. Run it later:
        echo   python extras\scripts\migrate_legacy.py
        pause & exit /b 1
    )
    if exist ".venv\Scripts\python.exe" (
        .venv\Scripts\python "%SRC_DIR%\extras\scripts\migrate_legacy.py" "%SRC_DIR%" "%CD%"
    ) else (
        python "%SRC_DIR%\extras\scripts\migrate_legacy.py" "%SRC_DIR%" "%CD%"
    )
    if %errorlevel% neq 0 (
        echo   [ERROR] Migration failed - restore from hermes.bak.* and retry.
        echo   Manual: python extras\scripts\migrate_legacy.py
        pause & exit /b 1
    )
    set "MIGRATED=1"
)

:: ---- Copy updated files ----
if "%MIGRATED%"=="1" (
    echo   Engine migrated (hermes\core) -- skipping flat copy
    goto :skip_engine_copy
)
echo   Updating hermes engine...
robocopy "%SRC_DIR%\hermes" "hermes" /E /NFL /NDL /NJH /NJS /NC /NS >nul
:skip_engine_copy

echo   Updating modules...
robocopy "%SRC_DIR%\modules\dashboard" "modules\dashboard" /E /NFL /NDL /NJH /NJS /NC /NS >nul

echo   Updating templates...
robocopy "%SRC_DIR%\templates" "templates" /E /NFL /NDL /NJH /NJS /NC /NS >nul

echo   Updating scripts...
robocopy "%SRC_DIR%\extras\scripts" "extras\scripts" /E /NFL /NDL /NJH /NJS /NC /NS >nul

echo   Updating batch files...
for %%f in (install.bat start.bat FixNapCat.bat Stop-All.bat "配置API.bat") do (
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
if "%MIGRATED%"=="1" goto :skip_deps
echo.
echo   [4/4] Updating Python dependencies...
if exist ".venv\Scripts\pip.exe" (
    echo   Upgrading pip packages...
    call .venv\Scripts\pip.exe install --upgrade -r "%SRC_DIR%\hermes\core\requirements.txt" 2>&1
    echo   Dependencies updated.
) else (
    echo   [SKIP] venv not found - run install.bat for fresh install
)
:skip_deps

:: ===== Step 5: Run upgrade.py (sync to ~/.hermes) =====
if "%MIGRATED%"=="1" goto :skip_upgrade
echo.
echo   [5/5] Applying upgrade to HERMES_HOME...
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python "%SRC_DIR%\extras\scripts\upgrade.py" "%SRC_DIR%" 2>&1
) else (
    echo   [SKIP] venv not found - run install.bat first
)
:skip_upgrade

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
