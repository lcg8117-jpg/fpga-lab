@echo off
cd /d "%~dp0Simulation\EasyStart_R3"
"E:\Moildesim\win64\vsim.exe" -view results/word_font2.wlf -do native_exports/EasyStart_view.do
