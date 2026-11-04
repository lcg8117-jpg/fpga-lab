// Align adaptive evidence to completed controller transactions only.
module forest_d1_adaptive_features(
 input clk,rst_n,control_done,history_clear,
 input [31:0] transaction_epoch,input [11:0] angle_adc12,
 input signed [15:0] sampled_encoder_local,transaction_target_local,
 input signed [31:0] reference_velocity_cps,reference_acceleration_q,
 input motion_braking,
 output reg feature_valid,output reg [31:0] feature_epoch,
 output reg signed [15:0] angle_error,angle_diff,
 output reg signed [31:0] position_error,
 output reg evidence_freeze,output reg [7:0] evidence_reason
);
 localparam [7:0] REFERENCE_TRANSITION=11,HISTORY=12;
 reg history;
 reg [2:0] stable_transactions;
 reg [31:0] previous_epoch;
 reg signed [15:0] previous_error;
 reg signed [31:0] previous_reference_velocity;
 wire signed [15:0] current_error=$signed({4'd0,angle_adc12})-16'sd3085;
 // The local encoder is already rebased against the current trajectory. Its
 // target10000 is a local controller origin, not a fixed world position.
 wire signed [31:0] local_measured={{16{sampled_encoder_local[15]}},sampled_encoder_local};
 wire signed [31:0] local_reference={{16{transaction_target_local[15]}},transaction_target_local};
 wire consecutive=history && transaction_epoch==previous_epoch+32'd1;
 // Q16 control units/5ms^2 is tested before any integer conversion: even the
 // smallest commanded acceleration invalidates a comparison window.
 wire reference_transition=reference_acceleration_q!=0 || motion_braking ||
  reference_velocity_cps!=previous_reference_velocity;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   feature_valid<=0;feature_epoch<=0;angle_error<=0;angle_diff<=0;position_error<=0;
   evidence_freeze<=1;evidence_reason<=HISTORY;history<=0;stable_transactions<=0;
   previous_epoch<=0;previous_error<=0;previous_reference_velocity<=0;
  end else begin
   feature_valid<=0;
   if(history_clear)begin
    history<=0;stable_transactions<=0;evidence_freeze<=1;evidence_reason<=HISTORY;
    previous_error<=0;previous_reference_velocity<=0;
   end else if(control_done)begin
    feature_valid<=1;feature_epoch<=transaction_epoch;
    angle_error<=current_error;
    angle_diff<=consecutive ? current_error-previous_error:16'sd0;
    position_error<=local_measured-local_reference;
    previous_epoch<=transaction_epoch;previous_error<=current_error;
    previous_reference_velocity<=reference_velocity_cps;history<=1;
    if(!consecutive)begin
     stable_transactions<=0;evidence_freeze<=1;evidence_reason<=HISTORY;
    end else if(reference_transition)begin
     stable_transactions<=0;evidence_freeze<=1;evidence_reason<=REFERENCE_TRANSITION;
    end else begin
     if(stable_transactions<5)stable_transactions<=stable_transactions+1'b1;
     evidence_freeze<=stable_transactions<4;
     evidence_reason<=stable_transactions<4 ? REFERENCE_TRANSITION:8'd0;
    end
   end
  end
 end
endmodule
