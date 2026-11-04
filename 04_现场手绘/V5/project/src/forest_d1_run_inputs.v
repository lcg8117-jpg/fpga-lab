// Inherit the deployed active angle scale 6921 Q20 radians/raw count.
// Convert to Forest nominal ADC12 units (4096 codes/345 electrical degrees).
// k=6921*180/(4096*345*pi); RNE(k*2^25)=9415911.
// Exhaustive 16384 signed Q4 deltas match the high-precision reference TZ.
module forest_d1_run_inputs #(
 parameter [23:0] ANGLE_SCALE_Q25=24'd9415911,
 parameter integer USE_Q4_INPUT=0
)(
 input clk,input rst_n,input angle_epoch_valid,input angle_capture,input kit_ip570,
 input [9:0] adc_raw,input [13:0] adc_q4,input [13:0] zero_q4,
 input signed [31:0] encoder_count,encoder_zero,
 output reg [11:0] angle_adc12,output wire angle_valid,
 output reg signed [15:0] encoder_forest,
 output reg encoder_range_fault
);
 reg signed [15:0] delta_q4;
 reg [13:0] angle_magnitude;
 reg angle_negative;
 reg product_negative;
 reg [37:0] angle_product;
 reg [3:0] valid_pipe;
 reg signed [15:0] centered;
 reg signed [15:0] captured_delta;
 always @* begin
  // Q4 is already raw-code times16. Keep the original scale constant;
  // do not shift the filtered sum again or divide the D gain.
  captured_delta=(USE_Q4_INPUT ? $signed({2'b0,adc_q4}) :
      $signed({1'b0,adc_raw,4'b0}))-$signed({2'b0,zero_q4});
  if(captured_delta>8191)captured_delta=captured_delta-16'sd16384;
  else if(captured_delta< -8192)captured_delta=captured_delta+16'sd16384;
  centered=16'sd3085+(product_negative ? -$signed({3'b0,angle_product[37:25]}) :
      $signed({3'b0,angle_product[37:25]}));
 end
 assign angle_valid=valid_pipe[3];
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin delta_q4<=0;angle_magnitude<=0;angle_negative<=0;
   product_negative<=0;angle_product<=0;angle_adc12<=3085;valid_pipe<=0;end
  else begin
   valid_pipe<=angle_epoch_valid ? {valid_pipe[2:0],angle_capture} : 4'b0;
   if(angle_capture && angle_epoch_valid)delta_q4<=captured_delta;
   angle_magnitude<=delta_q4[15] ? -delta_q4 : delta_q4;
   angle_negative<=delta_q4[15];
   angle_product<=angle_magnitude*ANGLE_SCALE_Q25;
   product_negative<=angle_negative;
   if(centered<0)angle_adc12<=0;
   else if(centered>4095)angle_adc12<=12'd4095;
   else angle_adc12<=centered[11:0];
  end
 end
 // Modulo subtraction remains correct across a signed 32-bit counter wrap
 // while the permitted relative travel is much less than half its range.
 wire signed [31:0] relative_count=encoder_count-encoder_zero;
 wire [31:0] relative_abs=relative_count[31] ? -relative_count : relative_count;
 reg [16:0] magnitude;
 reg [21:0] times26;
 reg [43:0] reciprocal_product;
 reg [2:0] negative_pipe,range_pipe;
 wire [17:0] scaled_magnitude=reciprocal_product[43:26];
 wire signed [19:0] mapped_count=20'sd10000+
   (negative_pipe[2] ? -$signed({2'b0,scaled_magnitude}) : $signed({2'b0,scaled_magnitude}));
 // floor(n/27)=floor(n*2485514/2^26) for n<=26*65536:
 // 27*2485514-2^26=14 and 14*n<2^26. The residual cannot cross
 // the next quotient boundary (multiples of 1/27). Full range tested.
 always @(posedge clk or negedge rst_n) begin
  if(!rst_n)begin
   magnitude<=0;times26<=0;reciprocal_product<=0;
   negative_pipe<=0;range_pipe<=0;encoder_forest<=16'sd10000;encoder_range_fault<=0;
  end else begin
   magnitude<=relative_abs<=65536 ? relative_abs[16:0] : 17'd0;
   // Both kits retain 1040 Forest units/revolution; raw x4 counts stay physical.
   times26<=kit_ip570 ? {5'd0,magnitude}:
       ({5'd0,magnitude}<<4)+({5'd0,magnitude}<<3)+({5'd0,magnitude}<<1);
   reciprocal_product<=kit_ip570 ? {times26[17:0],26'd0}:times26*22'd2485514;
   negative_pipe<={negative_pipe[1:0],relative_count[31]};
   range_pipe<={range_pipe[1:0],relative_abs>65536};
   encoder_range_fault<=range_pipe[2] || mapped_count>32767 || mapped_count< -32768;
   if(range_pipe[2])encoder_forest<=encoder_forest;
   else if(mapped_count>32767)encoder_forest<=16'sh7fff;
   else if(mapped_count< -32768)encoder_forest<=16'sh8000;
   else encoder_forest<=mapped_count[15:0];
  end
 end
endmodule
