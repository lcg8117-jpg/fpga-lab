onerror {quit -code 2 -force}
source cosim_setup.tcl
vsimulink -t 1ns -L work -voptargs=+acc -gPOINTS=500 -gBANK=0 -gACTION=7 -gKIT=1 -gCHECKSUM=32'h311DB923 -gFAST=1 -gBASE_BOARD_MS=0 -gBASE_EPOCH=1 -socket 4869 -wlf actual_rtl.wlf work.v5_cosim_core
foreach name {motor_command frame_actual frame_target motion_state fault outer_fault_code completed_transactions} {log /v5_cosim_core/$name}
puts {V5_CURRENT_FIELD_SERVER_READY}
