@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
set "SHOWCASE_PYTHON=python"
if exist "%ROOT%.venv\Scripts\python.exe" set "SHOWCASE_PYTHON=%ROOT%.venv\Scripts\python.exe"

echo ============================================================
echo  J280 Showcase Upper Computer
echo  Two layouts: pendulum bench / vehicle remote control.
echo  Pendulum: run the matching V1-V5 Host on port 8371.
echo  Vehicle BLE: search, select and connect explicitly in the page.
echo ============================================================
echo.

if not exist "%ROOT%backend\main.py" (
  echo [ERROR] backend\main.py not found.
  pause
  exit /b 1
)

if not exist "%ROOT%frontend\package.json" (
  echo [ERROR] frontend\package.json not found.
  pause
  exit /b 1
)

"%SHOWCASE_PYTHON%" -B "%ROOT%check_showcase_startup.py"
if errorlevel 1 (
  pause
  exit /b 1
)

start "J280 Showcase Backend" /D "%ROOT%backend" cmd /k ""%SHOWCASE_PYTHON%" -B -m uvicorn main:app --host 127.0.0.1 --port 8000"
start "J280 Showcase Frontend" /D "%ROOT%frontend" cmd /k "npm run dev"

timeout /t 3 /nobreak >nul
start "" "http://localhost:5173"
exit /b 0
