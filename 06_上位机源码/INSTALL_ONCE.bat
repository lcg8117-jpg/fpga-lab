@echo off
chcp 65001 >nul
set ROOT=%~dp0

echo [1/2] Installing Python dependencies...
cd /d "%ROOT%backend"
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [2/2] Installing frontend dependencies...
cd /d "%ROOT%frontend"
call npm install
if errorlevel 1 goto :fail

echo.
echo Installation complete. Run START_SHOWCASE.bat next.
pause
exit /b 0

:fail
echo Installation failed. Please send this window screenshot.
pause
exit /b 1
