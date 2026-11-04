`timescale 1ns/1ps
module tb_motion_coordinates;
 reg clk=0;always #5 clk=~clk;
 reg rst_n=0,tick=0,reanchor=0,cancel=0,capture_hold=0,balance_active=1,kit=0;
 reg signed [31:0] raw=0;
 reg signed [63:0] target=0;
 wire signed [63:0] physical,actual,error;
 wire signed [15:0] local_encoder,local_target;
 wire signed [31:0] speed;wire fault,valid;
 integer checks=0,i,relative,expected;
 forest_motion_coordinates dut(.clk(clk),.rst_n(rst_n),.tick_5ms(tick),.reanchor(reanchor),
  .cancel(cancel),.capture_hold(capture_hold),.balance_active(balance_active),.kit_ip570(kit),
  .encoder_raw(raw),.target_wide(target),.enc_wide_physical(physical),.encoder_wide(actual),
  .encoder_local(local_encoder),.target_local(local_target),.fault(fault),.tracking_error(error),
  .measured_velocity_cps(speed),.velocity_valid(valid));
 task check;input condition;input [511:0] message;begin
  checks=checks+1;if(condition!==1'b1)begin $display("FAIL coordinates %0s",message);$fatal(1);end
 end endtask
 task anchor;begin
  @(negedge clk);reanchor=1;target=0;
  @(negedge clk);reanchor=0;repeat(3)@(negedge clk);
 end endtask
 task edge_step;input integer delta;begin
  @(negedge clk);raw=raw+delta;repeat(3)@(negedge clk);
 end endtask
 task pulse_tick;begin @(negedge clk);tick=1;@(negedge clk);tick=0;repeat(3)@(negedge clk);end endtask
 initial begin
  repeat(3)@(negedge clk);rst_n=1;anchor();relative=0;
  for(i=0;i<2160;i=i+1)begin
   edge_step(1);relative=relative+1;expected=(relative*26)/27;
   check(actual==expected && physical==relative && local_encoder==10000+expected && !fault,"1080 forward TZ mapping");
  end
  check(actual==2080,"two physical1080 turns equal2080 control units");
  for(i=0;i<4320;i=i+1)begin
   edge_step(-1);relative=relative-1;expected=(relative*26)/27;
   check(actual==expected && physical==relative && local_encoder==10000+expected && !fault,"reverse and negative TZ mapping");
  end
  check(actual==-2080,"negative physical1080 turns");
  @(negedge clk);raw=32'sh7ffffff0;anchor();
  for(i=0;i<32;i=i+1)edge_step(1);
  check(physical==32 && actual==30 && !fault,"signed32 raw rollover retains valid +1 edges");
  kit=1;anchor();for(i=0;i<1040;i=i+1)edge_step(1);
  check(physical==1040 && actual==1040 && !fault,"IP570 physical1040 mapping");
  anchor();for(i=0;i<20;i=i+1)begin edge_step(1);edge_step(1);pulse_tick();end
  check(valid && speed==400,"measured velocity is40control units per100ms=400cps");
  cancel=1;@(negedge clk);cancel=0;repeat(2)@(negedge clk);
  check(!valid && speed==0,"cancel invalidates velocity history");
  anchor();target=64'sd42768;repeat(4)@(negedge clk);
  check(!fault && local_encoder==-32768,"negative local endpoint accepted");
  target=64'sd42769;repeat(4)@(negedge clk);check(fault,"negative local overflow faults");
  target=0;cancel=1;@(negedge clk);cancel=0;repeat(3)@(negedge clk);
  check(!fault && local_encoder==10000,"cancel clears local fault with recentered reference");
  target=-64'sd22767;repeat(4)@(negedge clk);
  check(!fault && local_encoder==32767,"positive local endpoint accepted");
  target=-64'sd22768;repeat(4)@(negedge clk);check(fault,"positive local overflow faults");
  anchor();edge_step(2);check(fault,"illegal physical jump is not silently clipped");
  cancel=1;@(negedge clk);cancel=0;repeat(3)@(negedge clk);
  check(fault,"cancel preserves physical tracker fault");
  anchor();check(!fault,"explicit reanchor clears tracker fault");
  target=64'sh8000000000000000;repeat(4)@(negedge clk);
  check(fault,"65bit error detects signed64 subtraction overflow");
  $display("PASS coordinates checks=%0d",checks);$finish;
 end
 initial begin #10000000;$fatal(1,"coordinate timeout");end
endmodule
