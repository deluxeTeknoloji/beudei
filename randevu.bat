@echo off
start pythonw fidel.py
timeout /t 2 >nul
start chrome --new-window --app=http://127.0.0.1:5000
exit
