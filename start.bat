@echo off
cd /d "%~dp0"
call .venv\Scripts\activate
start "Robot Trading" /min python main.py
echo Bot started in background. Check http://localhost:8000/docs
