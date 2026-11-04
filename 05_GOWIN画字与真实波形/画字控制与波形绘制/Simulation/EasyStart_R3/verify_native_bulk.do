onerror {quit -code 2}
set file [open results/native_sampled.csv w]
puts $file {lane,time_s,motor,target,actual,tracking,completed,accepted,draw_index,correction}
foreach lane {1 2} prefix {g e} {
 set paths [list]
 foreach signal {motor target actual tracking completed accepted draw_index} {lappend paths /word_closed_loop/${prefix}_${signal}}
 lappend paths /word_closed_loop/${prefix}_lane/planner/follow_correction
 for {set sample 1} {$sample<=24000} {incr sample} {
  set ns [expr {$sample*5000000}]
  set values [eval [linsert $paths 0 examine -time ${ns}ns -radix unsigned]]
  if {[llength $values] != 8} {error {Incomplete native value group}}
  foreach index {0 1 2 3} {
   set value [lindex $values $index]
   if {$value>=2147483648} {lset values $index [expr {$value-4294967296}]}
  }
  set value [lindex $values 7]
  if {$value>=32768} {lset values 7 [expr {$value-65536}]}
  puts $file [join [concat [list $lane [format %.3f [expr {$sample*.005}]]] $values] ,]
 }
}
close $file
puts {NATIVE_C6_FONT2_BULK_48000_ROWS_PASS}
quit -code 0
