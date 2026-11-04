onerror {resume}
quietly WaveActivateNextPane {} 0
add wave -noupdate -divider {GOWIN actual RTL + Simulink feedback}
add wave -noupdate -label {completed control transactions} -radix unsigned /word_closed_loop/g_completed
add wave -noupdate -label {motion state} -radix unsigned /word_closed_loop/g_state
add wave -noupdate -label {uploaded sample index} -radix unsigned /word_closed_loop/g_draw_index
add wave -noupdate -format Analog-Step -height 85 -label {angle ADC counts [-400,+400]} -max 400.0 -min -400.0 -radix decimal /word_closed_loop/g_error
add wave -noupdate -format Analog-Step -height 85 -label {RTL target counts [-200,+200]} -max 200.0 -min -200.0 -radix decimal /word_closed_loop/g_target
add wave -noupdate -format Analog-Step -height 85 -label {actual encoder counts [-200,+200]} -max 200.0 -min -200.0 -radix decimal /word_closed_loop/g_actual
add wave -noupdate -format Analog-Step -height 65 -label {tracking counts [-150,+150]} -max 150.0 -min -150.0 -radix decimal /word_closed_loop/g_tracking
add wave -noupdate -format Analog-Step -height 65 -label {RTL motor request [-7000,+7000]} -max 7000.0 -min -7000.0 -radix decimal /word_closed_loop/g_motor
add wave -noupdate -format Analog-Step -height 65 -label {input lead correction counts} -max 256.0 -min -256.0 -radix decimal /word_closed_loop/g_lane/planner/follow_correction
add wave -noupdate -label {planner fault} /word_closed_loop/g_fault
add wave -noupdate -label {reference complete} /word_closed_loop/g_done
add wave -noupdate -label {tracking settled} /word_closed_loop/g_settled
TreeUpdate [SetDefaultTree]
WaveRestoreCursors {{Cursor 1} {0 ns} 0}
quietly wave cursor active 0
configure wave -namecolwidth 310
configure wave -valuecolwidth 90
configure wave -justifyvalue left
configure wave -signalnamewidth 0
configure wave -snapdistance 10
configure wave -datasetprefix 0
configure wave -rowmargin 4
configure wave -childrowmargin 2
configure wave -gridoffset 0
configure wave -gridperiod 1
configure wave -griddelta 40
configure wave -timeline 0
configure wave -timelineunits sec
update
WaveRestoreZoom {0 ns} {120 sec}
