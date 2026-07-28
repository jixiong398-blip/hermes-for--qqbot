@echo off
chcp 65001 >nul 2>nul
echo Stopping all services...

:: Kill Dashboard (port 8899)
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8899 "') do taskkill /F /PID %%a >nul 2>&1

:: Kill Gateway (hermes_cli.main gateway processes)
for /f "tokens=2" %%a in ('tasklist /fi "imagename eq python.exe" /fo csv /nh 2^>nul ^| findstr /i "hermes_cli.main"') do taskkill /F /PID %%a >nul 2>&1

:: Kill Live2D (node processes on port 19919)
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":19919 "') do taskkill /F /PID %%a >nul 2>&1

:: Kill NapCat (port 6099)
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":6099 "') do taskkill /F /PID %%a >nul 2>&1

echo All services stopped.
pause
