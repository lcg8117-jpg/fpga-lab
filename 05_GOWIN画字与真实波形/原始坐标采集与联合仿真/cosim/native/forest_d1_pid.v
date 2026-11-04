// The zero-I path retains the frozen Forest operation/store sequence.
// Integral gains use source-PWM/(count*second); changes of I require STOP.
module forest_d1_pid(
 input clk,rst_n,sample_en,integrator_clear,prime_history,
 input [11:0] angle_adc12,input signed [15:0] encoder,
 input gain_write,input [31:0] bkp_bits,bki_bits,bkd_bits,pkp_bits,pki_bits,pkd_bits,
 output reg done,output reg signed [31:0] moto,balance_pwm,position_pwm,
 output wire signed [31:0] target,
 output reg [31:0] position_bias_bits,position_diff_bits,balance_bias_bits,angle_diff_bits,
 output reg [31:0] angle_p_bits,angle_i_bits,angle_d_bits,
 output reg [31:0] position_p_bits,position_i_bits,position_d_bits,
 output reg [2:0] position_div,output wire busy
);
`include "forest_d1_fp_functions.vh"
 assign target=32'sd10000;
 reg [7:0] state,return_state;
 assign busy=state!=0;
 reg fstart,fmul;reg [63:0] fa,fb,fvalue;
 reg [31:0] f32_value;
 wire fdone;wire [63:0] fresult;
 forest_d1_fp64 arithmetic(clk,rst_n,fstart,fmul,fa,fb,fdone,fresult);
 reg [31:0] bkp,bki,bkd,pkp,pki,pkd,last_bias,last_position;
 reg [31:0] dbias,least,t1,t2,pd_value,integral_delta;
 reg [31:0] angle_integral,position_integral;
 reg [11:0] sampled_angle;reg signed [15:0] sampled_encoder;
 reg sampled_clear,sampled_prime,prime_position_pending;
 reg signed [31:0] combined,least_integer;
 reg signed [31:0] angle_pd_pwm,position_pd_pwm;
 reg signed [31:0] pd_only_moto;
 wire signed [31:0] pd_difference=angle_pd_pwm-position_pd_pwm;
 reg [31:0] least_magnitude;reg [4:0] least_leading_bit;
 wire angle_i_enabled=bki!=0 && !sampled_clear;
 wire position_i_enabled=pki!=0 && !sampled_clear;
 // Both integral errors enter Moto with a negative sign. Freeze only an
 // outward update at the original output rail; inward unwinding remains live.
 wire angle_i_outward=(moto>=6900 && $signed(sampled_angle-32'd3085)<0) ||
                     (moto<=-6900 && $signed(sampled_angle-32'd3085)>0);
 wire position_i_outward=position_bias_bits[30:0]!=0 &&
  ((moto>=6900 && position_bias_bits[31]) || (moto<=-6900 && !position_bias_bits[31]));
 function [31:0] bounded_integral;
  input [31:0] value;
  begin bounded_integral=value[30:0]>31'h45d7a000 ? {value[31],31'h45d7a000}:value;end
 endfunction
 task launch;
  input mul;input [63:0] x,y;input [7:0] next_state;
  begin fa<=x;fb<=y;fmul<=mul;fstart<=1;return_state<=next_state;state<=99;end
 endtask
 task begin_position;
  begin
   if(position_div==4)begin position_div<=0;least_integer<=$signed(sampled_encoder)-target;state<=17;end
   else begin position_div<=position_div+1'b1;state<=30;end
  end
 endtask
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=0;return_state<=0;fstart<=0;fmul<=0;fa<=0;fb<=0;fvalue<=0;f32_value<=0;
   done<=0;moto<=0;balance_pwm<=0;position_pwm<=0;position_div<=0;
   position_bias_bits<=0;position_diff_bits<=0;balance_bias_bits<=0;angle_diff_bits<=0;
   angle_p_bits<=0;angle_i_bits<=0;angle_d_bits<=0;
   position_p_bits<=0;position_i_bits<=0;position_d_bits<=0;
   bkp<=32'h42440000;bki<=0;bkd<=32'h437b0000;
   pkp<=32'h41c80000;pki<=0;pkd<=32'h44188000;
   last_bias<=0;last_position<=0;dbias<=0;least<=0;t1<=0;t2<=0;
   angle_integral<=0;position_integral<=0;pd_value<=0;integral_delta<=0;
   sampled_angle<=0;sampled_encoder<=0;sampled_clear<=1;sampled_prime<=0;prime_position_pending<=0;
   least_integer<=0;least_magnitude<=0;least_leading_bit<=0;combined<=0;angle_pd_pwm<=0;position_pd_pwm<=0;pd_only_moto<=0;
  end else begin
   fstart<=0;done<=0;
   pd_only_moto<=pd_difference < -6900 ? -32'sd6900:pd_difference > 6900 ? 32'sd6900:pd_difference;
   if(integrator_clear)begin angle_integral<=0;position_integral<=0;angle_i_bits<=0;position_i_bits<=0;sampled_clear<=1;end
   case(state)
    0:if(sample_en)begin
     sampled_angle<=angle_adc12;sampled_encoder<=encoder;
     sampled_clear<=integrator_clear;sampled_prime<=prime_history;
     if(prime_history)prime_position_pending<=1;
     if(gain_write)begin bkp<=bkp_bits;bki<=bki_bits;bkd<=bkd_bits;pkp<=pkp_bits;pki<=pki_bits;pkd<=pkd_bits;end
     balance_bias_bits<=int_to_f32($signed({1'b0,angle_adc12})-32'sd3085);state<=1;
    end
    1:launch(0,f32_to_f64(balance_bias_bits),f32_to_f64((sampled_prime ? balance_bias_bits:last_bias)^32'h80000000),2);
    2:begin dbias<=f32_value;angle_diff_bits<=f32_value;last_bias<=balance_bias_bits;
     launch(1,f32_to_f64(bkp^32'h80000000),f32_to_f64(balance_bias_bits),3);end
    3:begin t1<=f32_value;angle_p_bits<=f32_value;launch(1,f32_to_f64(dbias),f32_to_f64(bkd),4);end
    4:begin t2<=f32_value;angle_d_bits<=f32_value^32'h80000000;state<=5;end
    5:launch(0,f32_to_f64(t1),f32_to_f64(t2^32'h80000000),6);
    6:begin
     angle_pd_pwm<=f32_to_int(f32_value);
     if(angle_i_enabled)begin pd_value<=f32_value;
      if(angle_i_outward)begin integral_delta<=0;state<=53;end
      else launch(1,f32_to_f64(balance_bias_bits),f32_to_f64(bki),51);
     end else begin angle_integral<=0;angle_i_bits<=0;balance_pwm<=f32_to_int(f32_value);begin_position;end
    end
    51:launch(1,fvalue,64'h3f747ae147ae147b,52);
    52:begin integral_delta<=f32_value;state<=53;end
    53:launch(0,f32_to_f64(angle_integral),f32_to_f64(integral_delta),54);
    54:begin
     angle_integral<=angle_i_enabled ? bounded_integral(f32_value):0;
     angle_i_bits<=angle_i_enabled ? (bounded_integral(f32_value)^32'h80000000):0;
     launch(0,f32_to_f64(pd_value),f32_to_f64(angle_i_enabled ? (bounded_integral(f32_value)^32'h80000000):0),55);
    end
    55:begin balance_pwm<=angle_i_enabled ? f32_to_int(f32_value):angle_pd_pwm;begin_position;end
    7:launch(1,f32_to_f64(position_bias_bits),64'h3fe999999999999a,8);
    8:begin position_bias_bits<=f32_value;launch(1,f32_to_f64(least),64'h3fc999999999999a,9);end
    9:launch(0,f32_to_f64(position_bias_bits),fvalue,10);
    10:begin position_bias_bits<=f32_value;state<=11;end
    11:launch(0,f32_to_f64(position_bias_bits),f32_to_f64(((sampled_prime || prime_position_pending) ? position_bias_bits:last_position)^32'h80000000),12);
    12:begin position_diff_bits<=f32_value;last_position<=position_bias_bits;prime_position_pending<=0;
     launch(1,f32_to_f64(position_bias_bits),f32_to_f64(pkp),13);end
    13:begin t1<=f32_value;position_p_bits<=f32_value;launch(1,f32_to_f64(position_diff_bits),f32_to_f64(pkd),14);end
    14:begin t2<=f32_value;position_d_bits<=f32_value;state<=15;end
    15:launch(0,f32_to_f64(t1),f32_to_f64(t2),16);
    16:begin
     position_pd_pwm<=f32_to_int(f32_value);
     if(position_i_enabled)begin pd_value<=f32_value;
      if(position_i_outward)begin integral_delta<=0;state<=63;end
      else launch(1,f32_to_f64(position_bias_bits),f32_to_f64(pki),61);
     end else begin position_integral<=0;position_i_bits<=0;position_pwm<=f32_to_int(f32_value);state<=30;end
    end
    61:launch(1,fvalue,64'h3f9999999999999a,62);
    62:begin integral_delta<=f32_value;state<=63;end
    63:launch(0,f32_to_f64(position_integral),f32_to_f64(integral_delta),64);
    64:begin
     position_integral<=position_i_enabled ? bounded_integral(f32_value):0;
     position_i_bits<=position_i_enabled ? bounded_integral(f32_value):0;
     launch(0,f32_to_f64(pd_value),f32_to_f64(position_i_enabled ? bounded_integral(f32_value):0),65);
    end
    65:begin position_pwm<=position_i_enabled ? f32_to_int(f32_value):position_pd_pwm;state<=30;end
    17:begin least_magnitude<=least_integer[31] ? (~least_integer+1'b1):least_integer;state<=18;end
    18:begin least_leading_bit<=highest_bit32(least_magnitude);state<=19;end
    19:begin least<=int_mag_to_f32(least_magnitude,least_leading_bit,least_integer[31]);state<=7;end
    30:begin
     // The later clear-priority assignment already owns Moto on cancellation.
     // Keep the normal difference here; the overridden scratch branch is dead.
     combined=balance_pwm-position_pwm;
     if(combined < -6900)combined=-6900;
     if(combined > 6900)combined=6900;
     moto<=combined;state<=31;
    end
    31:begin done<=1;state<=0;end
    99:if(fdone)begin fvalue<=fresult;state<=100;end
    100:begin f32_value<=f64_to_f32(fvalue);state<=return_state;end
    default:state<=0;
   endcase
   // An interrupted I transaction cannot publish a stale integral request.
   if((integrator_clear || sampled_clear) && (bki!=0 || pki!=0))begin
    if(state!=6)balance_pwm<=angle_pd_pwm;
    if(state!=16)position_pwm<=position_pd_pwm;
    angle_i_bits<=0;position_i_bits<=0;
    moto<=pd_only_moto;
   end
   // Latch cancellation before the next arithmetic phase. The physical
   // safety gate stays immediate without a guard-to-float-conversion path.
   if(integrator_clear)begin angle_integral<=0;position_integral<=0;sampled_clear<=1;end
  end
 end
endmodule
