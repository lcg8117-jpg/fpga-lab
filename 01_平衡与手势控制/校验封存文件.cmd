@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0原版完整包\runtime\python.exe" -B -X utf8 "%~dp0verify_archive.py"
pause
