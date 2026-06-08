@echo off
chcp 65001 >nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8899 "') do taskkill /F /PID %%a >nul 2>&1
echo Dashboard stopped.
pause
