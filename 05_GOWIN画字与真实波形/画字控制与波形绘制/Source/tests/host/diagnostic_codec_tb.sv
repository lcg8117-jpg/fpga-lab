`timescale 1ns/1ps
module diagnostic_codec_case #(parameter integer VARIANT=3)(input clk,rst_n,done,sent,output [2047:0] packet);
 wire pending;
 localparam [191:0] CHANGED={32'h44188000,32'd0,32'h41c80000,32'h43816c00,32'd0,32'h42427800};
 forest_d1_diagnostics #(.VARIANT(VARIANT),.FIRMWARE_ID(VARIANT==2?32'h46443332:32'h46443230+VARIANT)) dut(
  .clk(clk),.rst_n(rst_n),.tick_1ms(1'b0),.sent(sent),.uptime_ms(32'd123500),
  .kit_ip570(1'b1),.kit_changed(1'b0),.zero_valid(1'b1),.encoder_raw(32'h80000000),.invalid_count(16'd29),
  .control_done(done),.control_gains(CHANGED),.control_epoch(32'd1000),.control_ms(32'd123445),
  .control_feature_epoch(32'd994),.active_revision(32'd4),.stored_revision(32'd5),.adoption_count(32'd200),
  .control_adaptive_used(1'b1),.control_freeze(8'd10),.live_flags(8'h14),
  .gesture_map(8'd3),.left_positive(1'b1),.cw_positive(1'b0),
  .measured_velocity(-32'sd1040),.reference_velocity(32'sd520),.tracking_error(-64'sd910),
  .settle_ticks(8'd45),.stop_reason(8'd11),.trajectory_done(1'b1),.tracking_settled(1'b0),
  .learning_pulse(done),.learning_seq(32'd10),.learning_epoch(32'd999),
  .learning_code(8'd9),.learning_reason(8'd10),.center_before(8'sd8),.center_after(8'sd4),
  .reference_energy(32'd25600),.window_energy(32'd6400),.reference_peak(16'd30),.window_peak(16'd40),
  .learning_phase(2'd3),.window_samples(6'd0),.cooldown(4'd4),.event_samples(7'd64),
  .candidate_epoch(32'd999),.candidate_revision(32'd4),
  .swing_finished(done),.swing_failed(1'b0),.handoff_started(1'b1),.handoff_pulse(done),.handoff_timeout(1'b0),
  .source_ready(1'b1),.handoff_wait_ms(9'd250),.delta_q4(-16'sd120),
  .source_alpha(-32'sd100),.source_alpha_rate(32'sd200),.source_arm_rate(-32'sd300),
  .source_capture_flags(8'h57),.source_reason(8'd2),.capture_hold_ms(6'd6),.pending(pending),.packet(packet));
endmodule

module diagnostic_codec_tb;
 reg clk=0;always #10 clk=~clk;
 reg rst_n=0,done=0,sent=0;
 wire [2047:0] p1,p2,p3;
 diagnostic_codec_case #(.VARIANT(1)) one(clk,rst_n,done,sent,p1);
 diagnostic_codec_case #(.VARIANT(2)) two(clk,rst_n,done,sent,p2);
 diagnostic_codec_case #(.VARIANT(3)) three(clk,rst_n,done,sent,p3);
 integer file;
 initial begin
  repeat(3)@(negedge clk);rst_n=1;done=1;
  @(negedge clk);done=0;sent=1;
  @(negedge clk);sent=0;repeat(2)@(negedge clk);
  file=$fopen("packet_v1.hex","w");$fdisplay(file,"%0512h",p1);$fclose(file);
  file=$fopen("packet_v2.hex","w");$fdisplay(file,"%0512h",p2);$fclose(file);
  file=$fopen("packet_v3.hex","w");$fdisplay(file,"%0512h",p3);$fclose(file);
  $display("PASS DIAGNOSTIC_CODEC_BRIDGE variants=3 actual_registered_adoption_and_learning");$finish;
 end
endmodule
