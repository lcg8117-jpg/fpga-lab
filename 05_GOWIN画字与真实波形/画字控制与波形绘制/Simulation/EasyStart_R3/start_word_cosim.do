onerror {quit -code 2}
transcript file logs/modelsim_server.log
source cosim_setup.tcl
if {![file exists modelsim.ini]} {vmap -c}
if {![file isdirectory work]} {vlib work}
vmap work work
vlog -sv +incdir+rtl +incdir+data rtl/forest_d1_fp64.v rtl/forest_d1_pid.v rtl/forest_motion_coordinates.v rtl/forest_motion_tables.v rtl/forest_motion_planner.v rtl/forest_draw_buffer.v rtl/forest_draw_clocked.v rtl/forest_word_follow.v word_closed_loop.sv
vsimulink -t 1ns -voptargs=+acc -socket 4478 -wlf results/word_closed_loop_live.wlf work.word_closed_loop
foreach prefix {g e} {
 foreach suffix {error raw request motor target actual tracking vref vmeas state fault done settled completed accepted draw_index} {
  log /word_closed_loop/${prefix}_${suffix}
 }
}
foreach prefix {g e} {log /word_closed_loop/${prefix}_lane/planner/follow_correction}
puts {WORD_COSIM_SERVER_READY port4478}




