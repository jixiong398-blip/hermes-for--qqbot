@echo off
chcp 65001 >nul 2>nul
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8899 "') do taskkill /F /PID %%a >nul 2>&1
echo All services stopped.
pause
