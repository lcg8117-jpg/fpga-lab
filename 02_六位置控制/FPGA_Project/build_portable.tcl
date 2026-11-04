set project_dir [file dirname [file normalize [info script]]]
cd $project_dir
set bundled_python [file normalize [file join $project_dir .. PC_Bluetooth runtime python.exe]]
open_project ./forest_d1_v4.gprj
set_option -top_module forest_d1_top
set_option -global_freq 50
set_option -use_sspi_as_gpio 1
set_option -user_code 56345239
set_option -timing_driven 1
set_option -show_all_warn 1
set_option -opt_goal timing
set_option -map_option 2
set_option -place_option 2
set_option -route_option 0
set_option -route_maxfan 30
run syn
puts [exec $bundled_python -E -s -S -B ./compiler_tools/synthesis_gate.py --project forest_d1_v4]
run pnr
exit
