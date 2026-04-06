@echo off
cd /d "%~dp0"

echo Menjalankan Robot Trading...
start "RobotTrading" /min .venv\Scripts\python.exe main.py

echo.
echo Bot sudah jalan di background (minimized).
echo Dashboard : http://localhost:8000/docs
echo Health    : http://localhost:8000/health
echo.
echo Untuk jalankan tanpa jendela sama sekali, gunakan start_hidden.vbs
pause
