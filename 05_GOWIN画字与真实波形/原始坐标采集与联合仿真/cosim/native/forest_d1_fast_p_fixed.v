// P-only datapath: gains/P/D Q16; position bias Q20. D is never recomputed.
module forest_d1_fast_p_fixed(
 input clk,rst_n,sample_en,prime_history,running,base_valid,
 input [11:0] angle_adc12,input signed [15:0] encoder,input [31:0] sample_epoch,input [191:0] gains,
 input [31:0] base_angle_d,base_position_d,base_position_bias,base_position_diff,base_angle_diff,
 input signed [15:0] base_angle_delta,input [31:0] base_angle_epoch,base_position_epoch,
 output reg done,output wire busy,output reg [2:0] position_div,
 output reg signed [31:0] moto,balance_pwm,position_pwm,
 output reg [31:0] position_bias_bits,position_diff_bits,balance_bias_bits,angle_diff_bits,
 output reg [31:0] angle_p_bits,angle_d_bits,position_p_bits,position_d_bits,
 output wire [31:0] angle_i_bits,position_i_bits,output reg signed [15:0] angle_delta_counts,
 output reg [31:0] angle_d_epoch,position_d_epoch,position_p_epoch,output reg [191:0] applied_gains
);
 assign angle_i_bits=0;assign position_i_bits=0;
 reg [4:0] state;assign busy=state!=0;
 reg initialized,filter_tick,update_p,anchor;
 reg [191:0] prior_gains;reg [31:0] epoch;
 reg signed [31:0] kp_angle,kp_position;
 reg signed [16:0] angle_error,position_error;
 reg signed [39:0] bias,difference;
 reg signed [63:0] held_ad,held_pd,angle_p,position_p,angle_sum,position_sum;
 reg mul_start;reg signed [39:0] mul_a;reg [31:0] mul_b;reg [4:0] mul_return;
 wire mul_done,mul_busy;wire signed [71:0] mul_result;reg signed [71:0] product;
 forest_d1_serial_mul multiplier(clk,rst_n,mul_start,mul_a,mul_b,mul_done,mul_busy,mul_result);
 task launch_mul;
  input signed [39:0] a;input [31:0] b;input [4:0] next_state;
  begin mul_a<=a;mul_b<=b;mul_start<=1;mul_return<=next_state;state<=31;end
 endtask
 reg q_start,format_start;reg [31:0] q_input,anchor_bias_bits;
 reg [5:0] q_fractional,format_fractional;reg signed [47:0] format_input;
 reg [4:0] q_return,format_return;
 wire q_done,q_busy,format_done,format_busy;wire signed [63:0] q_result;wire [31:0] format_result;
 forest_d1_serial_q q_encoder(clk,rst_n,q_start,q_input,q_fractional,q_done,q_busy,q_result);
 forest_d1_serial_format formatter(clk,rst_n,format_start,format_input,format_fractional,format_done,format_busy,format_result);
 task launch_q;
  input [31:0] value;input [5:0] fractional;input [4:0] next_state;
  begin q_input<=value;q_fractional<=fractional;q_start<=1;q_return<=next_state;state<=30;end
 endtask
 task launch_format;
  input signed [47:0] value;input [5:0] fractional;input [4:0] next_state;
  begin format_input<=value;format_fractional<=fractional;format_start<=1;format_return<=next_state;state<=29;end
 endtask
 reg signed [63:0] filtered_delta;
 reg signed [31:0] combined;
 // round((1-0.8^(5/25))*2^24); the complementary coefficient sums to1.
 localparam [23:0] BETA_Q24=24'd732284;
 function signed [63:0] f32_q;
  input [31:0] value;input integer fractional;
  reg [63:0] magnitude;integer shift;
  begin
   magnitude=value[30:23]==0 ? 0:{40'd0,1'b1,value[22:0]};
   shift=$signed({1'b0,value[30:23]})-150+fractional;
   if(shift>=0)magnitude=magnitude<<shift;
   else if(shift<=-64)magnitude=0;
   else magnitude=magnitude>>(-shift);
   f32_q=value[31] ? -$signed(magnitude):$signed(magnitude);
  end
 endfunction
 function [31:0] fixed_f32;
  input signed [47:0] value;input integer fractional;
  reg [47:0] magnitude,remainder,half;reg [24:0] significand;
  integer i,leading,shift,exponent;
  begin
   magnitude=value[47] ? -value:value;leading=0;
   for(i=0;i<48;i=i+1)if(magnitude[i])leading=i;
   shift=leading-23;exponent=leading-fractional+127;remainder=0;half=0;
   if(shift>0)begin
    significand=magnitude>>shift;
    remainder=magnitude & ((48'd1<<shift)-1'b1);half=48'd1<<(shift-1);
    if(remainder>half || (remainder==half && significand[0]))significand=significand+1'b1;
   end else significand=magnitude<<(-shift);
   if(significand[24])begin significand=significand>>1;exponent=exponent+1;end
   fixed_f32=magnitude==0 ? 0:{value[47],exponent[7:0],significand[22:0]};
  end
 endfunction
 function signed [31:0] tz_q16;
  input signed [63:0] value;
  begin tz_q16=(value>>>16)+(value[63] && |value[15:0]);end
 endfunction
 function signed [63:0] rounded24;
  input signed [63:0] value;reg signed [63:0] q;
  begin q=value>>>24;
   if(value[23:0]>24'h800000 || (value[23:0]==24'h800000 && q[0]))q=q+1'b1;
   rounded24=q;
  end
 endfunction
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=0;initialized<=0;filter_tick<=0;update_p<=0;anchor<=1;prior_gains<=0;epoch<=0;
   kp_angle<=0;kp_position<=0;angle_error<=0;position_error<=0;bias<=0;difference<=0;
   held_ad<=0;held_pd<=0;angle_p<=0;position_p<=0;angle_sum<=0;position_sum<=0;
   mul_start<=0;mul_a<=0;mul_b<=0;mul_return<=0;product<=0;filtered_delta<=0;combined<=0;
   q_start<=0;format_start<=0;q_input<=0;anchor_bias_bits<=0;q_fractional<=0;format_fractional<=0;
   format_input<=0;q_return<=0;format_return<=0;
   done<=0;position_div<=4;moto<=0;balance_pwm<=0;position_pwm<=0;
   position_bias_bits<=0;position_diff_bits<=0;balance_bias_bits<=0;angle_diff_bits<=0;
   angle_p_bits<=0;angle_d_bits<=0;position_p_bits<=0;position_d_bits<=0;
   angle_delta_counts<=0;angle_d_epoch<=0;position_d_epoch<=0;position_p_epoch<=0;applied_gains<=0;
  end else begin
   done<=0;mul_start<=0;q_start<=0;format_start<=0;
   case(state)
    0:if(sample_en && base_valid)begin
     applied_gains<=gains;anchor_bias_bits<=base_position_bias;
     angle_d_bits<=base_angle_d;position_d_bits<=base_position_d;
     angle_diff_bits<=base_angle_diff;position_diff_bits<=base_position_diff;
     angle_delta_counts<=base_angle_delta;angle_d_epoch<=base_angle_epoch;position_d_epoch<=base_position_epoch;
     angle_error<=$signed({1'b0,angle_adc12})-17'sd3085;position_error<=$signed(encoder)-17'sd10000;
     epoch<=sample_epoch;filter_tick<=position_div==4;
     update_p<=position_div==4 || !initialized || gains!=prior_gains || prime_history;
     anchor<=!initialized || !running || prime_history;
     position_div<=position_div==4 ? 3'd0:position_div+3'd1;
     prior_gains<=gains;initialized<=1;launch_q(gains[31:0],16,16);
    end
    16:begin kp_angle<=q_result[31:0];launch_q(applied_gains[127:96],16,17);end
    17:begin kp_position<=q_result[31:0];launch_q(angle_d_bits,16,18);end
    18:begin held_ad<=q_result;launch_q(position_d_bits,16,19);end
    19:begin held_pd<=q_result;if(anchor)launch_q(anchor_bias_bits,20,20);else state<=1;end
    20:begin bias<=q_result[39:0];state<=1;end
    1:launch_format({{31{angle_error[16]}},angle_error},0,21);
    21:begin balance_bias_bits<=format_result;launch_mul(-$signed({{23{angle_error[16]}},angle_error}),kp_angle,2);end
    2:begin angle_p<=product[63:0];state<=3;end
    3:begin
     angle_sum<=angle_p+held_ad;
     launch_format(angle_p[47:0],16,22);
    end
    22:begin
     angle_p_bits<=angle_p==0 && kp_angle!=0 ? 32'h80000000:format_result;
     if(update_p && !anchor && filter_tick)begin difference<=($signed(position_error)<<<20)-bias;state<=4;end
     else if(update_p)state<=8;else state<=12;
    end
    4:launch_mul(difference,{8'd0,BETA_Q24},5);
    5:begin filtered_delta<=product[63:0];state<=6;end
    6:begin bias<=bias+rounded24(filtered_delta);state<=8;end
    8:launch_mul(bias,kp_position,9);
    9:begin position_p<=product>>>20;position_p_epoch<=epoch;state<=11;end
    11:launch_format(position_p[47:0],16,23);
    23:begin position_p_bits<=format_result;launch_format({{8{bias[39]}},bias},20,24);end
    24:begin position_bias_bits<=format_result;state<=12;end
    12:begin position_sum<=position_p+held_pd;balance_pwm<=tz_q16(angle_sum);state<=13;end
    13:begin position_pwm<=tz_q16(position_sum);state<=14;end
    14:begin
     combined=balance_pwm-position_pwm;
     if(combined < -6900)combined=-6900;
     if(combined > 6900)combined=6900;
     moto<=combined;state<=15;
    end
    15:begin done<=1;state<=0;end
    29:if(format_done)state<=format_return;
    30:if(q_done)state<=q_return;
    31:if(mul_done)begin product<=mul_result;state<=mul_return;end
    default:state<=0;
   endcase
  end
 end
endmodule
