`timescale 1ns/1ps
module tb_optical_interval_980;
 reg clk=0;always #5 clk=~clk;
 reg rst_n=0,tick=0,reanchor=0,sample=0,serial_stop=0;
 reg [7:0] raw43=0;wire event_valid;wire [3:0] code;wire [31:0] event_sequence;
 wire inhibit,accepted,rejected,fault;wire [2:0] state;wire [7:0] decode_reason;
 integer k,checks=0,before_sequence;
 forest_d1_gesture_decode decoder(.clk(clk),.rst_n(rst_n),.flush(serial_stop),.sample_valid(sample),
  .motion_inhibit(inhibit),.raw43(raw43),.raw44(8'd0),.map_flags(3'd0),
  .event_valid(event_valid),.event_code(code),.event_seq(event_sequence),.decode_reason(decode_reason));
 forest_motion_planner #(.VARIANT(2)) motion(.clk(clk),.rst_n(rst_n),.tick_5ms(tick),
  .reanchor(reanchor),.cancel(serial_stop || (event_valid && code==4)),.capture_hold(1'b0),
  .balance_active(1'b1),.kit_ip570(1'b1),.encoder_raw(32'sd0),
  .command_valid(event_valid && (code==7 || code==8)),.command(code==7 ? 3'd1:3'd2),
  .action_kind(2'd0),.action_index(3'd0),.request_id(32'd1),.left_positive(1'b1),.cw_positive(1'b1),
  .cfg_write(1'b0),.cfg_id(8'd0),.cfg_value(32'd0),.motion_guard_active(inhibit),
  .accepted(accepted),.rejected(rejected),.fault(fault),.state(state));
 task check(input bit condition,input string message);begin
  checks++;if(!condition)begin $display("FAIL INTERVAL %s seq=%0d code=%0d inhibit=%0d",message,event_sequence,code,inhibit);$fatal(1);end
 end endtask
 task feed(input [7:0] value);begin
  @(negedge clk);raw43=value;sample=1;@(negedge clk);sample=0;repeat(12)@(negedge clk);
 end endtask
 task release_hand;begin feed(0);feed(0);end endtask
 task step;begin @(negedge clk);tick=1;@(negedge clk);tick=0;repeat(512)@(negedge clk);check(!fault,"no planner fault");end endtask
 initial begin
  repeat(3)@(negedge clk);rst_n=1;reanchor=1;@(negedge clk);reanchor=0;
  release_hand();feed(8'h80);check(state==1 && inhibit && event_sequence==1,"CCW arms full optical guard");
  release_hand();before_sequence=event_sequence;
  for(k=0;k<196;k++)step();
  feed(8'h08);check(inhibit && event_sequence==before_sequence && decode_reason==4 && state==1,"reported CCW then down980ms is blocked before event creation");
  for(k=0;k<5;k++)step();check(!inhibit,"configured interval eventually expires without extension");
  feed(8'h08);check(event_sequence==before_sequence,"held lowering hand is not replayed at expiry");
  release_hand();feed(8'h08);check(event_sequence==before_sequence+1 && code==4 && state==0,"fresh released down can STOP after interval");
  release_hand();feed(8'h80);check(inhibit && state==1,"new command starts another interval");
  serial_stop=1;@(negedge clk);repeat(3)@(negedge clk);
  check(state==0 && !inhibit,"explicit serial STOP is immediate during optical guard");
  $display("PASS OPTICAL_INTERVAL_980 checks=%0d reported980ms=blocked held_replay=0 explicit_STOP=immediate",checks);$finish;
 end
 initial begin #20000000;$fatal(1,"interval test timeout");end
endmodule
