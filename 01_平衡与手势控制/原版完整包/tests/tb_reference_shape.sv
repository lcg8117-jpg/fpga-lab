`timescale 1ns/1ps
module tb_reference_shape;
 reg clk=0;always #5 clk=~clk;
 reg rst_n=0,tick=0,rebase=0,stationary=0;
 reg signed [63:0] reset_target=0,nominal=0;
 wire signed [63:0] target;wire signed [31:0] velocity,acceleration;wire fault;
 integer checks=0,k,sign,age;reg signed [63:0] previous;
 forest_motion_reference_shape dut(clk,rst_n,tick,rebase,stationary,reset_target,nominal,target,velocity,acceleration,fault);
 task check(input bit condition,input string message);begin
  checks++;if(!condition)begin $display("FAIL shape %s nominal=%0d target=%0d v=%0d",message,nominal,target,velocity);$fatal(1);end
 end endtask
 task step;begin @(negedge clk);tick=1;@(negedge clk);tick=0;repeat(8)@(negedge clk);end endtask
 initial begin
  repeat(3)@(negedge clk);rst_n=1;
  for(sign=-1;sign<=1;sign+=2)begin
   rebase=1;reset_target=64'sd9223372036854770000*sign;@(negedge clk);rebase=0;
   nominal=reset_target;previous=target;stationary=0;
   for(k=0;k<1040;k++)begin
    nominal=reset_target+sign*k;step();
    check(!fault,"wide coordinates remain valid");
    check(sign*(target-previous)>=0 && sign*(target-previous)<=2,"command output monotone and bounded");
    check(sign*(target-nominal)<=0,"no command beyond endpoint");previous=target;
   end
   stationary=1;age=0;
   while((target!=nominal || velocity!=0 || acceleration!=0) && age<2000)begin step();age++;check(!fault && sign*(target-previous)>=0,"tail cannot reverse");previous=target;end
   check(target==nominal && velocity==0 && age<2000,"exact endpoint with finite completion");
   for(k=0;k<100;k++)begin step();check(target==nominal && velocity==0,"no post-stop second target or reverse motion");end
  end
  rebase=1;reset_target=0;@(negedge clk);rebase=0;nominal=20000;step();check(fault,"unrepresentable relative jump faults explicitly");
  rebase=1;reset_target=-500;@(negedge clk);check(target==-500 && !fault && velocity==0,"capture/cancel clears pending calculation");
  $display("PASS REFERENCE_SHAPE checks=%0d both_directions=1 wide_coordinates=1 no_reverse_reference=1",checks);$finish;
 end
endmodule
