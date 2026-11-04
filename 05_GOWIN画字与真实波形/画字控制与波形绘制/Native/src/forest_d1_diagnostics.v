// Observation only. No output is connected to a motor or controller decision.
module forest_d1_diagnostics #(
 parameter integer VARIANT=3,WIRE_VERSION=5,
 parameter [31:0] SCHEMA=32'h46443405,
 parameter [31:0] FIRMWARE_ID=32'h46443233
)(
 input clk,rst_n,tick_1ms,sent,input [31:0] uptime_ms,
 input kit_ip570,kit_changed,zero_valid,input [31:0] encoder_raw,input [15:0] invalid_count,
 input control_done,input [191:0] control_gains,
 input [31:0] control_epoch,control_ms,control_feature_epoch,active_revision,stored_revision,adoption_count,
 input control_adaptive_used,input [7:0] control_freeze,
 input [7:0] live_flags,gesture_map,input left_positive,cw_positive,
 input signed [31:0] measured_velocity,reference_velocity,
 input signed [63:0] tracking_error,
 input [7:0] settle_ticks,stop_reason,input trajectory_done,tracking_settled,
 input learning_pulse,input [31:0] learning_seq,learning_epoch,
 input [7:0] learning_code,learning_reason,input signed [7:0] center_before,center_after,
 input [31:0] reference_energy,window_energy,input [15:0] reference_peak,window_peak,
 input [1:0] learning_phase,input [5:0] window_samples,input [3:0] cooldown,input [6:0] event_samples,
 input [31:0] candidate_epoch,candidate_revision,
 input swing_finished,swing_failed,handoff_started,handoff_pulse,handoff_timeout,
 input source_ready,input [8:0] handoff_wait_ms,input signed [15:0] delta_q4,
 input signed [31:0] source_alpha,source_alpha_rate,source_arm_rate,
 input [7:0] source_capture_flags,source_reason,input [5:0] capture_hold_ms,
 input [63:0] motion_origin,motion_final,input [31:0] planner_request,
 input [7:0] planner_action,gesture_state,decode_reason,input [3:0] planner_phase,
 output reg pending,output reg [2047:0] packet
);
 reg [31:0] diagnostic_sequence,last_completed_epoch,last_completed_ms,last_feature_epoch;
 reg [31:0] completed_revision,completed_stored_revision,completed_adoption_count;
 reg [191:0] previous_gains,adopt_before,adopt_after;
 // Keep a wide observation comparison out of one long carry chain.
 (* syn_keep = 1 *) wire [23:0] gain_difference_groups;
 genvar gain_group;
 generate for(gain_group=0;gain_group<24;gain_group=gain_group+1)begin:gain_compare
  assign gain_difference_groups[gain_group]=
   control_gains[gain_group*8+:8]!=previous_gains[gain_group*8+:8];
 end endgenerate
 wire completed_gains_changed=|gain_difference_groups;
 reg [31:0] adopt_seq,adopt_ms,adopt_control_epoch,adopt_feature_epoch;
 reg [7:0] adopt_reason,last_freeze;
 reg last_used;
 reg [31:0] learning_ms,finish_ms,handoff_ms,failure_ms;
 reg previous_finished,previous_failed,previous_timeout;
 reg [7:0] failure_reason;
 reg [5:0] period_ms;
 localparam [191:0] DEFAULT_GAINS={32'h44188000,32'd0,32'h41c80000,32'h437b0000,32'd0,32'h42440000};
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   diagnostic_sequence<=0;pending<=1;period_ms<=0;last_completed_epoch<=0;last_completed_ms<=0;last_feature_epoch<=0;
   completed_revision<=0;completed_stored_revision<=0;completed_adoption_count<=0;
   previous_gains<=DEFAULT_GAINS;adopt_before<=DEFAULT_GAINS;adopt_after<=DEFAULT_GAINS;
   adopt_seq<=0;adopt_ms<=0;adopt_control_epoch<=0;adopt_feature_epoch<=0;adopt_reason<=0;
   last_used<=0;last_freeze<=0;learning_ms<=0;finish_ms<=0;handoff_ms<=0;failure_ms<=0;
   previous_finished<=0;previous_failed<=0;previous_timeout<=0;failure_reason<=0;
  end else begin
   if(sent)begin diagnostic_sequence<=diagnostic_sequence+1'b1;pending<=0;end
   if(tick_1ms)begin
    if(period_ms==49)begin period_ms<=0;pending<=1;end else period_ms<=period_ms+1'b1;
   end
   if(kit_changed)pending<=1;
   if(learning_pulse)begin learning_ms<=uptime_ms;pending<=1;end
   previous_finished<=swing_finished;previous_failed<=swing_failed;previous_timeout<=handoff_timeout;
   if(swing_finished && !previous_finished)begin finish_ms<=uptime_ms;pending<=1;end
   if(handoff_pulse)begin handoff_ms<=uptime_ms;pending<=1;end
   if((swing_failed && !previous_failed) || (handoff_timeout && !previous_timeout))begin
    failure_ms<=uptime_ms;failure_reason<=handoff_timeout ? 8'd2:8'd1;pending<=1;
   end
   if(control_done)begin
    last_completed_epoch<=control_epoch;last_completed_ms<=control_ms;last_feature_epoch<=control_feature_epoch;
    completed_revision<=active_revision;completed_stored_revision<=stored_revision;completed_adoption_count<=adoption_count;
    last_freeze<=control_freeze;last_used<=control_adaptive_used;
    // Compare completed transactions, not a live candidate bus. Preserve each
    // actual change in the 200Hz T stream; D reports the most recent change.
    if(completed_gains_changed)begin
     adopt_seq<=adopt_seq+1'b1;adopt_ms<=control_ms;adopt_control_epoch<=control_epoch;
     adopt_feature_epoch<=control_feature_epoch;adopt_before<=previous_gains;adopt_after<=control_gains;
     adopt_reason<=control_adaptive_used ? 8'd1:(control_freeze!=0 ? 8'd3:8'd2);pending<=1;
    end
    previous_gains<=control_gains;
   end
  end
 end
 always @*begin
  packet=0;packet[31:0]=32'h44324446;packet[39:32]=WIRE_VERSION;packet[47:40]=VARIANT;
  packet[55:48]=1;packet[63:56]=live_flags;
  packet[95:64]=diagnostic_sequence;packet[127:96]=uptime_ms;packet[159:128]=SCHEMA;packet[191:160]=FIRMWARE_ID;
  packet[223:192]=last_completed_epoch;packet[255:224]=last_completed_ms;
  packet[263:256]={7'd0,kit_ip570};packet[271:264]={7'd0,zero_valid};packet[279:272]=failure_reason;
  packet[287:280]={2'd0,source_ready,handoff_timeout,handoff_started,swing_failed,swing_finished,handoff_pulse};
  packet[303:288]={7'd0,handoff_wait_ms};packet[319:304]=delta_q4;
  packet[351:320]=encoder_raw;packet[367:352]=invalid_count;packet[383:368]=16'd30;
  packet[415:384]=kit_ip570 ? 32'd1040:32'd1080;packet[447:416]=32'd1040;
  packet[479:448]=measured_velocity;packet[511:480]=reference_velocity;packet[575:512]=tracking_error;
  packet[583:576]=settle_ticks;packet[591:584]=stop_reason;
  packet[599:592]={6'd0,tracking_settled,trajectory_done};packet[607:600]=source_reason;
  packet[615:608]=gesture_map;packet[623:616]={7'd0,left_positive};packet[631:624]={7'd0,cw_positive};
  packet[639:632]={2'd0,capture_hold_ms};
  packet[671:640]=adopt_seq;packet[703:672]=adopt_ms;packet[735:704]=adopt_control_epoch;
  packet[767:736]=adopt_feature_epoch;packet[775:768]=adopt_reason;packet[783:776]={7'd0,last_used};
  packet[791:784]=last_freeze;packet[799:792]=learning_code;packet[831:800]=learning_seq;
  packet[863:832]=learning_epoch;packet[895:864]=learning_ms;
  packet[903:896]=learning_reason;packet[911:904]=center_before;packet[919:912]=center_after;
  packet[927:920]={6'd0,learning_phase};packet[935:928]={2'd0,window_samples};
  packet[943:936]={4'd0,cooldown};packet[959:944]=reference_peak;packet[975:960]=window_peak;
  packet[1023:992]=reference_energy;packet[1055:1024]=window_energy;
  packet[1247:1056]=adopt_before;packet[1439:1248]=adopt_after;
  packet[1471:1440]=candidate_epoch;packet[1503:1472]=candidate_revision;
  packet[1535:1504]=last_feature_epoch;packet[1567:1536]=completed_stored_revision;
  packet[1599:1568]=completed_revision;packet[1631:1600]=completed_adoption_count;
  packet[1663:1632]=finish_ms;packet[1695:1664]=handoff_ms;packet[1727:1696]=failure_ms;
  packet[1759:1728]=source_alpha;packet[1791:1760]=source_alpha_rate;packet[1823:1792]=source_arm_rate;
  packet[1831:1824]=source_capture_flags;packet[1839:1832]={1'b0,event_samples};
  packet[1903:1840]=motion_origin;packet[1967:1904]=motion_final;packet[1999:1968]=planner_request;
  packet[2007:2000]=planner_action;packet[2015:2008]={4'd0,planner_phase};packet[2023:2016]=gesture_state;packet[2031:2024]=decode_reason;
 end
endmodule
