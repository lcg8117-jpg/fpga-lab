// Task-layer slow return trim; the uncalibrated predictive lead stays disabled.
// This changes the position input, not the frozen PID gains or true feedback.
module forest_word_follow(
 input clk,rst_n,start,abort,tick_5ms,enabled,valid,
 input settling_trim_enable,
 input signed [31:0] velocity_q16,acceleration_q16,
 input signed [63:0] tracking_error,
 output reg lead_inhibited,
 output reg signed [15:0] correction,
 output reg signed [10:0] offset_low_limit,offset_high_limit,
 output reg signed [10:0] negative_low_limit,negative_high_limit,
 output reg fault
);
 reg [2:0] phase;
 reg [2:0] trim_ticks;
 reg signed [5:0] sampled_error;
 reg signed [13:0] integral_step;
 reg signed [21:0] trim_q16;
 reg signed [22:0] proposed_q16;
 wire trim_eligible=settling_trim_enable && tracking_error>=-64'sd16 && tracking_error<=64'sd16;
 wire signed [13:0] error_extended={{8{sampled_error[5]}},sampled_error};
 wire signed [13:0] error_times200=(error_extended<<<7)+(error_extended<<<6)+(error_extended<<<3);
 // Phase4 follows the existing +/-16-count clamp. The signed integer part
 // plus one fractional rounding bit reproduces ties away from zero.
 wire round_up=proposed_q16[15] && (!proposed_q16[22] || |proposed_q16[14:0]);
 wire signed [5:0] rounded_correction=$signed(proposed_q16[21:16])+(round_up ? 6'sd1:6'sd0);
 wire signed [10:0] extended_correction={{5{rounded_correction[5]}},rounded_correction};
 // The accumulator itself saturates, so reversing the error has no windup delay.
 // Capture, narrow scaling, accumulation, clamp and publication use separate clocks.
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin phase<=0;trim_ticks<=0;sampled_error<=0;integral_step<=0;
   trim_q16<=0;proposed_q16<=0;correction<=0;offset_low_limit<=-11'sd345;offset_high_limit<=11'sd345;
   negative_low_limit<=-11'sd346;negative_high_limit<=11'sd345;fault<=0;lead_inhibited<=1;end
  else if(abort)begin phase<=0;trim_ticks<=0;sampled_error<=0;integral_step<=0;
   trim_q16<=0;proposed_q16<=0;correction<=0;offset_low_limit<=-11'sd345;offset_high_limit<=11'sd345;
   negative_low_limit<=-11'sd346;negative_high_limit<=11'sd345;fault<=0;lead_inhibited<=1;end
  else begin
   if(valid && enabled && (velocity_q16>19663 || velocity_q16< -19663 ||
       acceleration_q16>524 || acceleration_q16< -524))begin
    fault<=1;phase<=0;trim_ticks<=0;
   end else if(start || !trim_eligible || fault)begin
    // Pausing or starting another PLAY preserves the last published compensation.
    phase<=0;trim_ticks<=0;
   end else begin
    if(tick_5ms && phase==0)begin
     if(trim_ticks==4)begin
      trim_ticks<=0;
      if(tracking_error>64'sd2 || tracking_error< -64'sd2)begin
       sampled_error<=tracking_error[5:0];phase<=1;
      end
     end else trim_ticks<=trim_ticks+1'b1;
    end
    case(phase)
     1:begin integral_step<=error_times200;phase<=2;end
     2:begin proposed_q16<=$signed({trim_q16[21],trim_q16})-
       $signed({{9{integral_step[13]}},integral_step});phase<=3;end
     3:begin
      if(proposed_q16>23'sd1048576)proposed_q16<=23'sd1048576;
      else if(proposed_q16< -23'sd1048576)proposed_q16<=-23'sd1048576;
      phase<=4;
     end
     4:begin
      trim_q16<=proposed_q16[21:0];
      correction<={{10{rounded_correction[5]}},rounded_correction};
      offset_low_limit<=-11'sd345-extended_correction;
      offset_high_limit<=11'sd345-extended_correction;
      negative_low_limit<=extended_correction-11'sd346;
      negative_high_limit<=extended_correction+11'sd345;phase<=0;
     end
     default:;
    endcase
   end
  end
 end
endmodule
