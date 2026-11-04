@echo off
setlocal
start "" "%~dp0runtime\pythonw.exe" -B "%~dp0start_host.py"
