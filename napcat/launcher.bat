@echo off
chcp 65001 >nul

set NAPCAT_PATCH_PACKAGE=%cd%\qqnt.json
set NAPCAT_LOAD_PATH=%cd%\loadNapCat.js
set NAPCAT_INJECT_PATH=%cd%\NapCatWinBootHook.dll
set NAPCAT_LAUNCHER_PATH=%cd%\NapCatWinBootMain.exe
set NAPCAT_MAIN_PATH=%cd%\napcat.mjs

:: ── 自动查找 QQ.exe ──
set QQPath=

:: 1) 注册表
for /f "tokens=2*" %%a in ('reg query "HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ" /v "UninstallString" 2^>nul') do (
    for %%c in ("%%~b") do set QQPath=%%~dpcQQ.exe
)
if exist "%QQPath%" goto :found

:: 2) 常见路径
for %%d in (
    "C:\Program Files\Tencent\QQNT\QQ.exe"
    "C:\Program Files (x86)\Tencent\QQNT\QQ.exe"
    "D:\Program Files\Tencent\QQNT\QQ.exe"
    "%LOCALAPPDATA%\Programs\Tencent\QQNT\QQ.exe"
) do (
    if exist %%d (
        set QQPath=%%~d
        goto :found
    )
)

echo QQ.exe not found — please edit launcher.bat and set QQPath manually
pause
exit /b 1

:found
set NAPCAT_MAIN_PATH=%NAPCAT_MAIN_PATH:\=/%
echo (async () =^> {await import("file:///%NAPCAT_MAIN_PATH%")})() > "%NAPCAT_LOAD_PATH%"
"%NAPCAT_LAUNCHER_PATH%" "%QQPath%" "%NAPCAT_INJECT_PATH%" %*
