@echo off
cd /d "%~dp0"
echo === Robot Trading Update ===
echo.

echo [1/4] Menghentikan bot...
wmic process where "name='python.exe' and commandline like '%%main.py%%'" delete >nul 2>&1
timeout /t 2 >nul

echo [2/4] Pull update dari GitHub...
git pull origin main
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] git pull gagal! Periksa koneksi internet atau credential GitHub.
    pause
    exit /b 1
)

echo [3/4] Install/update package...
.venv\Scripts\pip.exe install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] pip install gagal!
    pause
    exit /b 1
)

echo [4/4] Menjalankan bot kembali...
timeout /t 2 >nul
wscript "%~dp0start_hidden.vbs"

echo.
echo Done! Bot sudah diupdate dan berjalan kembali.
pause
