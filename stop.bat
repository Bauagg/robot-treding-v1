@echo off
cd /d "%~dp0"
echo Menghentikan Robot Trading...

wmic process where "name='python.exe'" delete >nul 2>&1
timeout /t 2 >nul

for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo Bot berhasil dihentikan.
pause
