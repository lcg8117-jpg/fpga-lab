@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0DS13_App\runtime\pythonw.exe" goto missing
start "" "%~dp0DS13_App\runtime\pythonw.exe" -I -B "%~dp0DS13_App\launch_gui.py"
exit /b 0
:missing
echo Please extract the complete ZIP before starting.
pause
exit /b 1
