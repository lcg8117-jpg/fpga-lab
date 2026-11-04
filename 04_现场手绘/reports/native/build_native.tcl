open_project ./forest_d1_v5.gprj
set_option -top_module forest_d1_top
set_option -global_freq 50
set_option -use_sspi_as_gpio 1
set_option -user_code 44533133
set_option -timing_driven 1
set_option -show_all_warn 1
set_option -opt_goal timing
set_option -map_option 2
set_option -place_option 2
set_option -route_option 1
set_option -route_maxfan 60
run syn
puts [exec {D:/Espressif/tools/python/python.exe} ./synthesis_gate.py --project forest_d1_v5]
run pnr
exit
