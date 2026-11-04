// Swing-only observer. It cannot feed or reset the quiet V3 balance chain.
module forest_d1_swing_observer (
 input wire clk,rst_n,tick_1ms,valid,
 input wire [13:0] sample_q4,zero_q4,
 input wire signed [31:0] encoder_count,
 output wire signed [15:0] delta_q4,
 output reg signed [31:0] alpha_q20,alpha_rate_q20,arm_rate_q20,
 input wire kit_ip570
);
 // Current calibrated angle scale is 6921 Q20 rad/raw code. The 8ms
 // difference is scaled to rad/s before the source's existing 1/4 filter.
 reg signed [15:0] angle_history[0:7];
 reg signed [31:0] arm_history[0:7];
 reg [3:0] filled;
 wire [13:0] modular_delta=sample_q4-zero_q4;
 assign delta_q4={{2{modular_delta[13]}},modular_delta};
 wire signed [47:0] angle_product=delta_q4*48'sd6921;
 // Both operands are sign-extended14-bit wrapped angles. Their difference
 // fits signed15, and its product fits signed35 without any truncation.
 wire signed [14:0] angle_difference=$signed(delta_q4)-$signed(angle_history[7]);
 wire signed [31:0] arm_difference=encoder_count-arm_history[7];
 // Exact combined constant scaling and IIR; no extra register or latency.
 // floor((3*A+floor(P/16))/4)=floor((48*A+P)/64).
 // The exact numerator fits signed38 for every signed32 A and legal D15.
 wire [37:0] derivative_d={{23{angle_difference[14]}},angle_difference};
 wire [37:0] accumulator_a={{6{alpha_rate_q20[31]}},alpha_rate_q20};
 wire [37:0] rate_term_0=derivative_d<<0;
 wire [37:0] rate_term_1=derivative_d<<2;
 wire [37:0] rate_term_2=derivative_d<<5;
 wire [37:0] rate_term_3=derivative_d<<6;
 wire [37:0] rate_term_4=derivative_d<<8;
 wire [37:0] rate_term_5=derivative_d<<9;
 wire [37:0] rate_term_6=derivative_d<<12;
 wire [37:0] rate_term_7=derivative_d<<13;
 wire [37:0] rate_term_8=derivative_d<<16;
 wire [37:0] rate_term_9=derivative_d<<18;
 wire [37:0] rate_term_10=derivative_d<<19;
 wire [37:0] rate_term_a4=accumulator_a<<4;
 wire [37:0] rate_term_a5=accumulator_a<<5;
 wire [37:0] rate_sum_0_0=rate_term_0^rate_term_1^rate_term_2;
 wire [37:0] rate_carry_0_0=((rate_term_0&rate_term_1)|(rate_term_0&rate_term_2)|(rate_term_1&rate_term_2))<<1;
 wire [37:0] rate_sum_0_1=rate_term_3^rate_term_4^rate_term_5;
 wire [37:0] rate_carry_0_1=((rate_term_3&rate_term_4)|(rate_term_3&rate_term_5)|(rate_term_4&rate_term_5))<<1;
 wire [37:0] rate_sum_0_2=rate_term_6^rate_term_7^rate_term_8;
 wire [37:0] rate_carry_0_2=((rate_term_6&rate_term_7)|(rate_term_6&rate_term_8)|(rate_term_7&rate_term_8))<<1;
 wire [37:0] rate_sum_0_3=rate_term_9^rate_term_10^rate_term_a4;
 wire [37:0] rate_carry_0_3=((rate_term_9&rate_term_10)|(rate_term_9&rate_term_a4)|(rate_term_10&rate_term_a4))<<1;
 wire [37:0] rate_sum_1_0=rate_sum_0_0^rate_carry_0_0^rate_sum_0_1;
 wire [37:0] rate_carry_1_0=((rate_sum_0_0&rate_carry_0_0)|(rate_sum_0_0&rate_sum_0_1)|(rate_carry_0_0&rate_sum_0_1))<<1;
 wire [37:0] rate_sum_1_1=rate_carry_0_1^rate_sum_0_2^rate_carry_0_2;
 wire [37:0] rate_carry_1_1=((rate_carry_0_1&rate_sum_0_2)|(rate_carry_0_1&rate_carry_0_2)|(rate_sum_0_2&rate_carry_0_2))<<1;
 wire [37:0] rate_sum_1_2=rate_sum_0_3^rate_carry_0_3^rate_term_a5;
 wire [37:0] rate_carry_1_2=((rate_sum_0_3&rate_carry_0_3)|(rate_sum_0_3&rate_term_a5)|(rate_carry_0_3&rate_term_a5))<<1;
 wire [37:0] rate_sum_2_0=rate_sum_1_0^rate_carry_1_0^rate_sum_1_1;
 wire [37:0] rate_carry_2_0=((rate_sum_1_0&rate_carry_1_0)|(rate_sum_1_0&rate_sum_1_1)|(rate_carry_1_0&rate_sum_1_1))<<1;
 wire [37:0] rate_sum_2_1=rate_carry_1_1^rate_sum_1_2^rate_carry_1_2;
 wire [37:0] rate_carry_2_1=((rate_carry_1_1&rate_sum_1_2)|(rate_carry_1_1&rate_carry_1_2)|(rate_sum_1_2&rate_carry_1_2))<<1;
 wire [37:0] rate_sum_3_0=rate_sum_2_0^rate_carry_2_0^rate_sum_2_1;
 wire [37:0] rate_carry_3_0=((rate_sum_2_0&rate_carry_2_0)|(rate_sum_2_0&rate_sum_2_1)|(rate_carry_2_0&rate_sum_2_1))<<1;
 wire [37:0] rate_sum_4_0=rate_sum_3_0^rate_carry_3_0^rate_carry_2_1;
 wire [37:0] rate_carry_4_0=((rate_sum_3_0&rate_carry_3_0)|(rate_sum_3_0&rate_carry_2_1)|(rate_carry_3_0&rate_carry_2_1))<<1;
 wire signed [37:0] rate_combined=$signed(rate_sum_4_0)+$signed(rate_carry_4_0);
 wire signed [37:0] angle_rate_next=rate_combined>>>6;
 // Arm-rate modulo32 semantics are unchanged by this angle-only candidate.
 wire signed [20:0] arm_rate_scale=kit_ip570 ? 21'sd791875 : 21'sd762546;
 wire signed [63:0] arm_rate_product=arm_difference*arm_rate_scale;
 integer i;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   filled<=0;alpha_q20<=0;alpha_rate_q20<=0;arm_rate_q20<=0;
   for(i=0;i<8;i=i+1)begin angle_history[i]<=0;arm_history[i]<=0;end
  end else if(!valid)begin
   filled<=0;alpha_q20<=0;alpha_rate_q20<=0;arm_rate_q20<=0;
   for(i=0;i<8;i=i+1)begin angle_history[i]<=0;arm_history[i]<=encoder_count;end
  end else if(tick_1ms)begin
   alpha_q20<=angle_product>>>4;
   for(i=7;i>0;i=i-1)begin angle_history[i]<=angle_history[i-1];arm_history[i]<=arm_history[i-1];end
   angle_history[0]<=delta_q4;arm_history[0]<=encoder_count;
   if(filled<8)begin filled<=filled+1'b1;alpha_rate_q20<=0;arm_rate_q20<=0;end
   else begin
    alpha_rate_q20<=angle_rate_next[31:0];
    arm_rate_q20<=arm_rate_q20+(($signed(arm_rate_product[31:0])-arm_rate_q20)>>>2);
   end
  end
 end
endmodule

// The physical ADC remains the mandatory quiet V3 3.125MHz chain. Only
// the archived swing-side 16-point circular 1ms averaging is retained here.
// This does not claim the archived25MHz input sampling edge is unchanged.
module forest_d1_swing_archive_average #(
 parameter integer SAMPLE_CLOCKS=3125
)(
 input wire clk,rst_n,input wire [9:0] raw,
 output reg [13:0] filtered_q4
);
 reg [11:0] timer;
 reg [3:0] count;
 reg [9:0] anchor;
 reg signed [15:0] sum;
 reg signed [11:0] difference;
 reg signed [11:0] unwrapped;
 wire signed [15:0] final_sum=sum+{{4{unwrapped[11]}},unwrapped};
 always @*begin
  difference=$signed({1'b0,raw})-$signed({1'b0,anchor});
  if(difference>511)difference=difference-12'sd1024;
  else if(difference< -512)difference=difference+12'sd1024;
  unwrapped=$signed({1'b0,anchor})+difference;
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin timer<=0;count<=0;anchor<=0;sum<=0;filtered_q4<=0;end
  else if(timer==SAMPLE_CLOCKS-1)begin
   timer<=0;
   if(count==0)begin anchor<=raw;sum<=$signed({6'd0,raw});count<=1;end
   else if(count==15)begin filtered_q4<=final_sum[13:0];sum<=0;count<=0;end
   else begin sum<=final_sum;count<=count+1'b1;end
  end else timer<=timer+1'b1;
 end
endmodule
