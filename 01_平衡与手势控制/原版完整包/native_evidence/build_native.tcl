open_project ./forest_d1_v1.gprj
set_option -top_module forest_d1_top
set_option -global_freq 50
set_option -use_sspi_as_gpio 1
set_option -user_code 41444231
set_option -timing_driven 1
set_option -show_all_warn 1
set_option -opt_goal timing
set_option -map_option 2
set_option -place_option 2
set_option -route_option 0
set_option -route_maxfan 30
run syn
puts [exec {D:/Codex_Scratch/J280_AutoReturn_Stiction_Field_20261028_R1/pc/runtime/python.exe} ./synthesis_gate.py --project forest_d1_v1]
run pnr
exit
