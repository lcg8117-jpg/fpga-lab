@echo off
cd /d "%~dp0.."
python -m host.app --demo
if errorlevel 1 pause
