@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" "%~dp0原版完整包\runtime\pythonw.exe" -B -X utf8 "%~dp0打开S1配套窗口.py"
