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

:: ===== 同步角色名到 .env（Python 方案，可靠处理中文） =====
if exist "%TARGET%" (
    if exist ".venv\Scripts\python.exe" (
        .venv\Scripts\python -c "import re,pathlib; s=pathlib.Path.home()/'.hermes'; e=s/'SOUL.md'; t=e.read_text(encoding='utf-8') if e.exists() else ''; m=re.search(r'[—-]\s*(.+)', t.splitlines()[0]) if t else None; n=m.group(1).strip() if m else 'Soyo'; f=s/'.env'; c=f.read_text(encoding='utf-8') if f.exists() else ''; c=re.sub(r'^ONEBOT_BOT_NAME=.*$', f'ONEBOT_BOT_NAME={n}', c, flags=re.M) if 'ONEBOT_BOT_NAME' in c else c+f'\nONEBOT_BOT_NAME={n}\n'; f.write_text(c, encoding='utf-8'); print('   角色名已同步:', n)"
    ) else (
        echo   [SKIP] venv 未找到 - 跳过角色名同步
    )
)
echo.
pause
