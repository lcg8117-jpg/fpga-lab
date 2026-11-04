view wave
if {[llength [find signals /word_closed_loop/g_lane/origin_wide]] > 0} {
 set status_file [open results/native_final_status.txt w]
 foreach prefix {g e} {
  puts $status_file "$prefix origin=[examine -radix decimal /word_closed_loop/${prefix}_lane/origin_wide] final=[examine -radix decimal /word_closed_loop/${prefix}_lane/final_wide] target=[examine -radix decimal /word_closed_loop/${prefix}_target] encoder=[examine -radix decimal /word_closed_loop/${prefix}_actual] done=[examine -radix unsigned /word_closed_loop/${prefix}_done] settled=[examine -radix unsigned /word_closed_loop/${prefix}_settled] transactions=[examine -radix unsigned /word_closed_loop/${prefix}_completed]"
 }
 close $status_file
}
foreach pair {{g GOWIN} {e EasyStart}} {
 set prefix [lindex $pair 0]
 set word [lindex $pair 1]
 delete wave *
 add wave -noupdate -divider "$word actual RTL + Simulink feedback"
 add wave -noupdate -radix unsigned -label {completed control transactions} /word_closed_loop/${prefix}_completed
 add wave -noupdate -radix unsigned -label {motion state} /word_closed_loop/${prefix}_state
 add wave -noupdate -radix unsigned -label {uploaded sample index} /word_closed_loop/${prefix}_draw_index
 add wave -noupdate -format Analog-Step -height 85 -min -120 -max 120 -radix decimal -label {angle ADC counts [-120,+120]} /word_closed_loop/${prefix}_error
 add wave -noupdate -format Analog-Step -height 85 -min -200 -max 200 -radix decimal -label {RTL target counts [-200,+200]} /word_closed_loop/${prefix}_target
 add wave -noupdate -format Analog-Step -height 85 -min -200 -max 200 -radix decimal -label {actual encoder counts [-200,+200]} /word_closed_loop/${prefix}_actual
 add wave -noupdate -format Analog-Step -height 65 -min -150 -max 150 -radix decimal -label {tracking counts [-150,+150]} /word_closed_loop/${prefix}_tracking
 add wave -noupdate -format Analog-Step -height 65 -min -4000 -max 4000 -radix decimal -label {RTL motor request [-4000,+4000]} /word_closed_loop/${prefix}_motor
 add wave -noupdate -label {planner fault} /word_closed_loop/${prefix}_fault
 add wave -noupdate -label {reference complete} /word_closed_loop/${prefix}_done
 add wave -noupdate -label {tracking settled} /word_closed_loop/${prefix}_settled
 configure wave -namecolwidth 310 -valuecolwidth 90 -timelineunits sec
 update
 wave zoom range 0ns 60000000000ns
 write format wave native_exports/${word}_view.do
 write wave native_exports/${word}_RTL_overview.ps -start 0ns -end 60000000000ns -perpage 60000000000ns -width 14 -height 9 -portrait -margin 0.35
 wave zoom range 500000000ns 535000000ns
 write wave native_exports/${word}_RTL_detail.ps -start 500000000ns -end 535000000ns -perpage 35000000ns -width 14 -height 9 -portrait -margin 0.35
}
wave zoom full
puts {NATIVE_MODELSIM_WAVE_EXPORT_PASS}

