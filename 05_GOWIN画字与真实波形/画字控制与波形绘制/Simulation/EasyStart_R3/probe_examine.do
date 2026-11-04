onerror {quit -code 2}
set paths [list /word_closed_loop/g_motor /word_closed_loop/g_target /word_closed_loop/g_actual /word_closed_loop/g_tracking /word_closed_loop/g_completed /word_closed_loop/g_accepted /word_closed_loop/g_draw_index /word_closed_loop/g_lane/planner/follow_correction]
set values [eval [linsert $paths 0 examine -time 70000000000ns -radix decimal]]
puts "BULK_NATIVE_VALUES=$values LENGTH=[llength $values]"
set values [eval [linsert $paths 0 examine -time 70000000000ns -radix unsigned]]
puts "BULK_UNSIGNED_VALUES=$values LENGTH=[llength $values]"
quit -code 0
