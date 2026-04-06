@echo off
cd /d "%~dp0"
echo === Robot Trading Update ===
echo.

echo [1/3] Menghentikan bot...
wmic process where "name='python.exe' and commandline like '%%main.py%%'" delete >nul 2>&1
timeout /t 2 >nul

echo [2/3] Pull update dari GitHub...
git pull

echo [3/3] Menjalankan bot kembali...
timeout /t 2 >nul
wscript "%~dp0start_hidden.vbs"

echo.
echo Done! Bot sudah diupdate dan berjalan kembali.
pause
