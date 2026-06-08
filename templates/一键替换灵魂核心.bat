@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo   ========================================
echo     Replace SOUL - Custom Character
echo   ========================================
echo.

set "TARGET=%USERPROFILE%\.hermes\SOUL.md"

copy /Y "SOUL.md" "%TARGET%" >nul
if %errorlevel% equ 0 (
    echo   已替换: %TARGET%
    echo   重启 Gateway 生效
) else (
    echo   [ERROR] 替换失败
)
echo.
pause
