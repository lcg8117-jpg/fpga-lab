@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch.ps1"
set "startup_result=%errorlevel%"
if not "%startup_result%"=="0" (
  echo V4 startup failed. Copy the error text above.
  pause
)
exit /b %startup_result%
