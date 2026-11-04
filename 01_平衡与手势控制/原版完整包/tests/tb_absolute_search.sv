`timescale 1ns/1ps
module tb_absolute_search;
 reg clk=0;always #5 clk=~clk;
 reg rst_n=0,sample=0,commit=0,run_balance=1,stop=0,manual_write=0,enable_write=0,enable_value=1,pause=0;
 reg [191:0] base={32'h441b0000,32'd0,32'h41c80000,32'h43160000,32'd0,32'h42440000};
 reg [31:0] revision=0,epoch=0;
 reg signed [15:0] angle_error=8,angle_diff=1,actual=1000;
 reg signed [31:0] position_error=4,requested=100;
 reg saturating=0,fault=0;
 wire [191:0] target;wire enabled,active;wire [7:0] reason;
 wire signed [15:0] center_p,center_d;wire [31:0] learned;
 integer checks=0,n,optimal_d=260,optimal_p=49,total_samples=0;
 integer err_d,err_p,error_value,old_d,old_p,held_p,held_d;
 real actual_d,actual_p;
 forest_d1_adaptive dut(.clk(clk),.rst_n(rst_n),.sample_valid(sample),.commit_tick(commit),
  .run_balance(run_balance),.stop(stop),.manual_write(manual_write),.enable_write(enable_write),
  .enable_value(enable_value),.gains_base(base),.manual_revision(revision),.sample_epoch(epoch),
  .angle_error(angle_error),.angle_diff(angle_diff),.position_error(position_error),.requested_moto(requested),
  .actual_q15(actual),.saturating(saturating),.fault(fault),.evidence_pause(pause),
  .target_gains(target),.enabled(enabled),.active(active),.freeze_reason(reason),
  .center_p_q8(center_p),.center_d_q8(center_d),.learn_revision(learned));
 task check(input bit condition,input string message);begin
  checks++;if(!condition)begin $display("FAIL absolute %s P=%f D=%f center=%0d/%0d phase=%0d reason=%0d",message,actual_p,actual_d,center_p,center_d,dut.phase,reason);$fatal(1);end
 end endtask
 task one_sample;begin
  old_d=dut.applied_d;old_p=dut.applied_p;
  @(negedge clk);commit=(total_samples%5)==0;
  @(negedge clk);commit=0;
  actual_d=$bitstoshortreal(target[95:64]);actual_p=$bitstoshortreal(target[31:0]);
  err_d=$rtoi(actual_d-optimal_d);if(err_d<0)err_d=-err_d;
  err_p=$rtoi((actual_p-optimal_p)*16);if(err_p<0)err_p=-err_p;
  error_value=8+err_d/4+err_p/8;
  angle_error=(total_samples%200)<100 ? error_value:-error_value;
  angle_diff=1;position_error=4;requested=100;actual=1000;
  epoch=epoch+1;sample=1;
  @(negedge clk);sample=0;repeat(12)@(negedge clk);
  total_samples++;
  if(active)begin
   check(actual_p>=32 && actual_p<=80 && actual_d>=96 && actual_d<=448,"absolute bounds");
   check(dut.applied_d<=old_d+16 && dut.applied_d+16>=old_d,"D slew <=1 per25ms bank");
   check(dut.applied_p<=old_p+2 && dut.applied_p+2>=old_p,"P slew <=0.125 per25ms bank");
   check(target[191:96]==base[191:96] && target[63:32]==base[63:32],"I and position bank untouched");
  end
 end endtask
 task scenario(input integer initial_d,input integer optimum_d,input integer optimum_p);shortreal d;begin
  @(negedge clk);rst_n=0;repeat(4)@(negedge clk);rst_n=1;
  d=initial_d;base[95:64]=$shortrealtobits(d);optimal_d=optimum_d;optimal_p=optimum_p;
  @(negedge clk);enable_write=1;@(negedge clk);enable_write=0;
  repeat(30000)one_sample();
  check(center_d>=(optimal_d-6)*16 && center_d<=(optimal_d+6)*16,"known response minimum reached from independent manual D");
  check(center_p>=(optimal_p-1)*16 && center_p<=(optimal_p+1)*16,"P response minimum reached");
  $display("SEARCH_RESULT initialD=%0d optimumD=%0d optimumP=%0d learnedP=%f learnedD=%f retained=%0d virtual_seconds=150",initial_d,optimal_d,optimal_p,center_p/16.0,center_d/16.0,learned);
 end endtask
 initial begin
  scenario(150,260,49);scenario(260,260,49);scenario(300,180,49);scenario(150,332,56);
  n=0;while(dut.phase!=1 && n<5000)begin one_sample();n++;end
  check(dut.phase==1,"a real trial is active before interruption");
  held_p=center_p;held_d=center_d;stop=1;
  repeat(4)@(negedge clk);
  check(!active && dut.applied_p==held_p && dut.applied_d==held_d,"STOP discards unverified trial before any later RUN");
  stop=0;one_sample();
  check(active && dut.phase==0 && dut.wanted_p==held_p && dut.wanted_d==held_d,"next RUN begins with accepted best without stale trial");
  pause=1;
  repeat(300)begin one_sample();check(active && center_p==held_p && center_d==held_d,"motion pauses learning but preserves learned centre and control ownership");end
  pause=0;saturating=1;repeat(10)one_sample();
  check(reason==5 && center_p==held_p && center_d==held_d,"saturated evidence never changes accepted best");saturating=0;
  @(negedge clk);manual_write=1;revision++;@(negedge clk);manual_write=0;
  check(!active && !enabled && target==base,"manual write cancels adaptive ownership immediately");
  base[63:32]=32'h3f800000;enable_write=1;@(negedge clk);enable_write=0;repeat(5)@(negedge clk);
  check(!active && reason==3,"nonzero integral remains explicit fixed-mode qualification");
  $display("PASS ABSOLUTE_SEARCH checks=%0d independent_optima=4 absolute_P_D=1 bidirectional=1 pause_preserves_learning=1",checks);$finish;
 end
 initial begin #50000000;$fatal(1,"absolute search timeout");end
endmodule
