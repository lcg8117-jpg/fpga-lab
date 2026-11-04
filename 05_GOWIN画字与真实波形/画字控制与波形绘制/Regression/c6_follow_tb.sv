`timescale 1ns/1ps
module c6_follow_tb;
 reg clk=0;always #10 clk=~clk;
 reg rst_n=0,start=0,abort=0,tick=0,enabled=1,valid=0;
 reg signed[31:0]v=0,a=0;wire signed[15:0]correction;wire fault;
 forest_word_follow dut(.clk(clk),.rst_n(rst_n),.start(start),.abort(abort),.tick_5ms(tick),
  .enabled(enabled),.valid(valid),.velocity_q16(v),.acceleration_q16(a),.correction(correction),.fault(fault));
 integer checks=0,n;reg signed[15:0]held_correction;
 task step(input signed[31:0]velocity,input signed[31:0]accel);begin
  @(negedge clk);v=velocity;a=accel;valid=1;@(negedge clk);valid=0;repeat(5)@(negedge clk);checks++;
 end endtask
 task reset_follow;begin abort=1;@(negedge clk);abort=0;@(negedge clk);
  if(correction!=0 || fault)$fatal(1,"abort did not clear");checks++;end endtask
 initial begin
  repeat(4)@(negedge clk);rst_n=1;repeat(3)@(negedge clk);
  repeat(80)step(6554,0);
  if(correction<105 || correction>107 || fault)$fatal(1,"positive lead scale/sign");checks++;
  held_correction=correction;repeat(20)@(negedge clk);if(correction!==held_correction)$fatal(1,"unqualified reference updated correction");checks++;
  reset_follow();repeat(80)step(-6554,0);
  if(correction> -105 || correction< -107 || fault)$fatal(1,"negative lead scale/sign");checks++;
  reset_follow();step(19664,0);if(!fault)$fatal(1,"velocity overrange not rejected");checks++;
  reset_follow();step(0,525);if(!fault)$fatal(1,"acceleration overrange not rejected");checks++;
  reset_follow();repeat(80)step(19663,0);if(!fault)$fatal(1,"correction bound not latched");checks++;
  reset_follow();@(negedge clk);v=6554;valid=1;@(negedge clk);valid=0;abort=1;@(negedge clk);abort=0;
  repeat(8)@(negedge clk);if(correction!=0 || fault)$fatal(1,"stale pipeline after abort");checks++;
  $display("PASS C6_FOLLOW checks=%0d signedScale=1 finiteBounds=1 correctionBound=1 abortPipeline=1",checks);$finish;
 end
 initial begin #1000000;$fatal(1,"timeout");end
endmodule
