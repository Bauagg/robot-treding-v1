@echo off
echo Menghentikan Robot Trading...

wmic process where "name='python.exe' and commandline like '%%main.py%%'" delete >nul 2>&1

if %errorlevel% equ 0 (
    echo Bot berhasil dihentikan.
) else (
    echo Bot tidak sedang berjalan.
)
pause
