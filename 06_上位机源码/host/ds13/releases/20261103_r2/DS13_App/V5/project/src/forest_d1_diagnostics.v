// DS11 observation-only status. No output controls a motor or PID transaction.
module forest_d1_diagnostics #(
 parameter integer VARIANT=5,WIRE_VERSION=5,
 parameter [31:0] SCHEMA=32'h44535001,FIRMWARE_ID=32'h44533131
)(
 input clk,rst_n,tick_1ms,sent,input [31:0] uptime_ms,
 input kit_ip570,kit_changed,zero_valid,input [31:0] encoder_raw,input [15:0] invalid_count,
 input control_done,input [31:0] control_epoch,control_ms,input [7:0] live_flags,
 input signed [31:0] measured_velocity,reference_velocity,
 input signed [63:0] tracking_error,input [7:0] settle_ticks,stop_reason,
 input trajectory_done,tracking_settled,input [63:0] motion_origin,motion_final,
 input [31:0] planner_request,input [7:0] planner_action,input [3:0] planner_phase,
 output reg pending,output reg [2047:0] packet
);
 reg [31:0] diagnostic_sequence,last_completed_epoch,last_completed_ms;
 reg [5:0] period_ms;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin diagnostic_sequence<=0;pending<=1;period_ms<=0;last_completed_epoch<=0;last_completed_ms<=0;end
  else begin
   if(sent)begin diagnostic_sequence<=diagnostic_sequence+1'b1;pending<=0;end
   if(tick_1ms)begin
    if(period_ms==49)begin period_ms<=0;pending<=1;end else period_ms<=period_ms+1'b1;
   end
   if(kit_changed)pending<=1;
   if(control_done)begin last_completed_epoch<=control_epoch;last_completed_ms<=control_ms;end
  end
 end
 always @*begin
  packet=0;packet[31:0]=32'h44324446;packet[39:32]=WIRE_VERSION;packet[47:40]=VARIANT;
  packet[55:48]=1;packet[63:56]=live_flags;
  packet[95:64]=diagnostic_sequence;packet[127:96]=uptime_ms;packet[159:128]=SCHEMA;packet[191:160]=FIRMWARE_ID;
  packet[223:192]=last_completed_epoch;packet[255:224]=last_completed_ms;
  packet[263:256]={7'd0,kit_ip570};packet[271:264]={7'd0,zero_valid};
  packet[351:320]=encoder_raw;packet[367:352]=invalid_count;packet[383:368]=16'd30;
  packet[415:384]=kit_ip570 ? 32'd1040:32'd1080;packet[447:416]=32'd1040;
  packet[479:448]=measured_velocity;packet[511:480]=reference_velocity;packet[575:512]=tracking_error;
  packet[583:576]=settle_ticks;packet[591:584]=stop_reason;packet[599:592]={6'd0,tracking_settled,trajectory_done};
  packet[1903:1840]=motion_origin;packet[1967:1904]=motion_final;packet[1999:1968]=planner_request;
  packet[2007:2000]=planner_action;packet[2015:2008]={4'd0,planner_phase};
 end
endmodule
