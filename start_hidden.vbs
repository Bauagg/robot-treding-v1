Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c cd /d C:\Users\lenovo\Desktop\robot-treding-v1 && .venv\Scripts\pip.exe install -r requirements.txt && .venv\Scripts\python.exe main.py", 0, False
