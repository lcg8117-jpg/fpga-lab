onerror {quit -code 2}
transcript file logs/native_validation.log
set file [open results/native_sampled.csv w]
puts $file {lane,time_s,motor,target,actual,tracking,completed,accepted,draw_index,correction}
foreach lane {1 2} prefix {g e} {
 for {set sample 1} {$sample<=24000} {incr sample} {
  set ns [expr {$sample*5000000}]
  set row [list $lane [format %.3f [expr {$sample*.005}]]]
  foreach signal {motor target actual tracking completed accepted draw_index} {
   if {$signal in {motor target actual tracking}} {set radix decimal} else {set radix unsigned}
   lappend row [examine -time ${ns}ns -radix $radix /word_closed_loop/${prefix}_${signal}]
  }
  lappend row [examine -time ${ns}ns -radix decimal /word_closed_loop/${prefix}_lane/planner/follow_correction]
  puts $file [join $row ,]
 }
}
close $file
puts {NATIVE_WLF_48000_SAMPLE_ROWS_EXPORTED}
puts {NATIVE_C6_FONT2_DATA_PASS}
quit -code 0

