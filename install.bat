@echo off
if not defined IS_ADMIN (
    title QQBot Installer
)
cd /d "%~dp0"
cls

echo.
echo   ========================================
echo         QQBot - One-Click Installer
echo   ========================================
echo.
echo   This will install:
echo     - Python 3.12 (if not found)
echo     - Virtual environment
echo     - Modified Hermes (22 dependencies)
echo     - Default config files
echo.
echo   Press any key to start, or close this window to cancel...
pause >nul
cls

echo.
echo   [Step 1/4] Checking Python...
echo.

where python >nul 2>&1
if %errorlevel% equ 0 (
    python --version 2>&1
    echo   Python found - skip installation
    goto :create_venv
)

where python3 >nul 2>&1
if %errorlevel% equ 0 (
    python3 --version 2>&1
    echo   Python3 found - skip installation
    goto :create_venv
)

echo   Python not found. Installing Python 3.12...
echo   This may take 1-2 minutes...
if exist "python-installer.exe" (
    python-installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
    if %errorlevel% neq 0 (
        echo.
        echo   [ERROR] Python installation failed.
        echo   Please install manually from https://python.org
        echo   Make sure to check "Add to PATH" during installation.
        pause
        exit /b 1
    )
    set "PATH=%PATH%;%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts"
    echo   Python 3.12 installed.
) else (
    echo   [ERROR] python-installer.exe not found in this folder.
    echo   Please install Python manually from https://python.org
    pause
    exit /b 1
)

:create_venv
echo.
echo   [Step 2/4] Creating virtual environment...
echo.
python -m venv .venv 2>nul
if %errorlevel% neq 0 (
    python3 -m venv .venv 2>nul
    if %errorlevel% neq 0 (
        echo   [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)
echo   Virtual environment created.

echo.
echo   [Step 3/4] Installing Hermes...
echo   This may take a few minutes (downloading ~22 packages)...
echo.
call .venv\Scripts\activate.bat
pip install -e hermes\ --no-deps 2>&1
pip install -r hermes\requirements.txt 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   [ERROR] Package installation failed.
    echo   Check your internet connection and try again.
    pause
    exit /b 1
)
echo.
echo   Hermes installed successfully.

echo.
echo   [Step 4/4] Creating config files...
echo.
python scripts\install.py 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   [WARNING] Config setup had issues, but installation may still work.
    echo   You can manually copy templates\*.yaml and templates\*.md to %%USERPROFILE%%\.hermes\
)

echo.
echo   ========================================
echo         Installation Complete!
echo   ========================================
echo.
echo   Next steps:
echo     1. Edit config.yaml  (set your API key)
echo     2. Edit SOUL.md      (write character persona)
echo     3. Run start.bat     (launch the bot)
echo.
echo   Config files location: %%USERPROFILE%%\.hermes\
echo.
pause
