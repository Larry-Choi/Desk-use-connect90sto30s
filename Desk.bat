@echo off
cd /d "%~dp0"
start "Desk" "%~dp0.venv\Scripts\pythonw.exe" -m desk
