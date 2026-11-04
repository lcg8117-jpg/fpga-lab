@echo off
cd /d "%~dp0"
start "" "%~dp0runtime\pythonw.exe" -B "%~dp0start_host.py"
