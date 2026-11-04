// Original5/25ms PID remains the producer of ALL D terms in both modes.
// The fast path changes P timing; it cannot overwrite original D history.
module forest_d1_fast_balance(
 input clk,rst_n,cfg_rst_n,sample_en,fast_sample_en,integrator_clear,prime_history,run_session,requested_fast,
 input [11:0] angle_adc12,fast_angle,input signed [15:0] encoder,fast_encoder,
 input [31:0] sample_epoch,gain_revision,input [191:0] gains,
 output wire done,busy,output wire active_fast,output reg deadline_fault,output wire ready,
 output wire observation_start,output wire [11:0] observation_angle,
 output wire signed [15:0] observation_encoder,observation_delta,
 output wire [191:0] observation_gains,output wire [31:0] observation_revision,
 output wire [31:0] angle_d_epoch,position_d_epoch,position_p_epoch,
 output wire [2:0] position_div,output wire signed [31:0] target,
 output wire signed [31:0] moto,
 output wire signed [31:0] balance_pwm,
 output wire signed [31:0] position_pwm,
 output wire [31:0] position_bias_bits,
 output wire [31:0] position_diff_bits,
 output wire [31:0] balance_bias_bits,
 output wire [31:0] angle_diff_bits,
 output wire [31:0] angle_p_bits,
 output wire [31:0] angle_i_bits,
 output wire [31:0] angle_d_bits,
 output wire [31:0] position_p_bits,
 output wire [31:0] position_i_bits,
 output wire [31:0] position_d_bits,
 output wire unused_terminal
);
 assign unused_terminal=0;assign target=10000;
 wire bd,bb,fd,fb;wire [2:0] fdiv;
 wire [31:0] f_angle_epoch,f_position_epoch,f_p_epoch;
 wire signed [31:0] b_moto,f_moto;
 wire signed [31:0] b_balance_pwm,f_balance_pwm;
 wire signed [31:0] b_position_pwm,f_position_pwm;
 wire [31:0] b_position_bias_bits,f_position_bias_bits;
 wire [31:0] b_position_diff_bits,f_position_diff_bits;
 wire [31:0] b_balance_bias_bits,f_balance_bias_bits;
 wire [31:0] b_angle_diff_bits,f_angle_diff_bits;
 wire [31:0] b_angle_p_bits,f_angle_p_bits;
 wire [31:0] b_angle_i_bits,f_angle_i_bits;
 wire [31:0] b_angle_d_bits,f_angle_d_bits;
 wire [31:0] b_position_p_bits,f_position_p_bits;
 wire [31:0] b_position_i_bits,f_position_i_bits;
 wire [31:0] b_position_d_bits,f_position_d_bits;
 reg base_seen,fast_seen;reg [5:0] warm;
 reg [191:0] pending_gains,completed_gains;
 reg [31:0] pending_revision,completed_revision,pending_epoch,last_angle_epoch,slow_position_epoch;
 reg signed [15:0] previous_error,slow_delta,completed_delta;
 reg [31:0] completed_angle_epoch,completed_position_epoch;
 reg [31:0] completed_angle_d_bits,completed_position_d_bits;
 reg [31:0] completed_position_bias_bits,completed_position_diff_bits,completed_angle_diff_bits;
 reg position_prime_pending;
 wire fast_launch=fast_sample_en && base_seen;
 wire eligible=gains[63:32]==0 && gains[159:128]==0;
 assign ready=base_seen && fast_seen && warm==50;
 assign busy=bb || fb;
 assign active_fast=1'b1;
 wire handoff=1'b0;
 reg observation_armed,observation_profile;
 assign observation_start=active_fast ? fast_launch:sample_en;
 assign observation_angle=active_fast ? fast_angle:angle_adc12;
 assign observation_encoder=active_fast ? fast_encoder:encoder;
 assign observation_delta=active_fast ? completed_delta:(prime_history ? 16'sd0:($signed({1'b0,angle_adc12})-16'sd3085)-previous_error);
 assign observation_gains=active_fast ? completed_gains:gains;
 assign observation_revision=active_fast ? completed_revision:gain_revision;
 assign done=(active_fast ? fd:bd) && observation_armed && observation_profile==active_fast;
 assign angle_d_epoch=active_fast ? f_angle_epoch:pending_epoch;
 assign position_d_epoch=active_fast ? f_position_epoch:slow_position_epoch;
 assign position_p_epoch=active_fast ? f_p_epoch:slow_position_epoch;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   base_seen<=0;fast_seen<=0;warm<=0;deadline_fault<=0;
   pending_gains<=0;completed_gains<=0;pending_revision<=0;completed_revision<=0;
   pending_epoch<=0;last_angle_epoch<=0;slow_position_epoch<=0;
   previous_error<=0;slow_delta<=0;completed_delta<=0;
   completed_angle_epoch<=0;completed_position_epoch<=0;position_prime_pending<=0;
   completed_angle_d_bits<=0;completed_position_d_bits<=0;
   completed_position_bias_bits<=0;completed_position_diff_bits<=0;completed_angle_diff_bits<=0;
   observation_armed<=0;observation_profile<=0;
  end else begin
   if((sample_en && bb) || (fast_launch && fb))deadline_fault<=1;
   if(sample_en)begin
    pending_epoch<=sample_epoch;last_angle_epoch<=sample_epoch;
    pending_gains<=gains;pending_revision<=gain_revision;
    slow_delta<=prime_history ? 0:($signed({1'b0,angle_adc12})-16'sd3085)-previous_error;
    previous_error<=$signed({1'b0,angle_adc12})-16'sd3085;
    if(prime_history)position_prime_pending<=1;
    if(position_div==4)begin slow_position_epoch<=sample_epoch;position_prime_pending<=0;end
   end
   if(bd)begin
    base_seen<=1;completed_gains<=pending_gains;completed_revision<=pending_revision;
    completed_delta<=slow_delta;completed_angle_epoch<=pending_epoch;
    completed_position_epoch<=slow_position_epoch;
    completed_angle_d_bits<=b_angle_d_bits;completed_position_d_bits<=b_position_d_bits;
    completed_position_bias_bits<=b_position_bias_bits;
    completed_position_diff_bits<=b_position_diff_bits;completed_angle_diff_bits<=b_angle_diff_bits;
   end
   if(fd)fast_seen<=1;
   if(fast_launch && warm<50)warm<=warm+1'b1;
   if(observation_start)begin observation_armed<=1;observation_profile<=active_fast;end
   if(done)observation_armed<=0;
   // V5 always precomputes while stopped so source_frame_ready can qualify
   // RUN/handoff. Reset and each completed transaction own this valid bit.
  end
 end
 wire signed [15:0] unused_f_delta;
 forest_d1_pid original(
  .clk(clk),.rst_n(rst_n),.sample_en(sample_en),.integrator_clear(integrator_clear),.prime_history(prime_history),
  .angle_adc12(angle_adc12),.encoder(encoder),.gain_write(1'b1),
  .bkp_bits(gains[31:0]),.bki_bits(32'd0),.bkd_bits(gains[95:64]),
  .pkp_bits(gains[127:96]),.pki_bits(32'd0),.pkd_bits(gains[191:160]),
  .done(bd),.busy(bb),.position_div(position_div),.target(),
  .moto(b_moto),
  .balance_pwm(b_balance_pwm),
  .position_pwm(b_position_pwm),
  .position_bias_bits(b_position_bias_bits),
  .position_diff_bits(b_position_diff_bits),
  .balance_bias_bits(b_balance_bias_bits),
  .angle_diff_bits(b_angle_diff_bits),
  .angle_p_bits(b_angle_p_bits),
  .angle_i_bits(b_angle_i_bits),
  .angle_d_bits(b_angle_d_bits),
  .position_p_bits(b_position_p_bits),
  .position_i_bits(b_position_i_bits),
  .position_d_bits(b_position_d_bits)
 );
 forest_d1_fast_p_fixed fast(
  .clk(clk),.rst_n(rst_n),.sample_en(fast_launch),.base_valid(base_seen),
  .prime_history(prime_history),.running(run_session),.angle_adc12(fast_angle),.encoder(fast_encoder),
  .sample_epoch(sample_epoch),.gains(completed_gains),
  .base_angle_d(completed_angle_d_bits),.base_position_d(completed_position_d_bits),
  .base_position_bias(completed_position_bias_bits),.base_position_diff(completed_position_diff_bits),.base_angle_diff(completed_angle_diff_bits),
  .base_angle_delta(completed_delta),.base_angle_epoch(completed_angle_epoch),.base_position_epoch(completed_position_epoch),
  .done(fd),.busy(fb),.position_div(fdiv),.angle_delta_counts(unused_f_delta),
  .angle_d_epoch(f_angle_epoch),.position_d_epoch(f_position_epoch),.position_p_epoch(f_p_epoch),.applied_gains(),
  .moto(f_moto),
  .balance_pwm(f_balance_pwm),
  .position_pwm(f_position_pwm),
  .position_bias_bits(f_position_bias_bits),
  .position_diff_bits(f_position_diff_bits),
  .balance_bias_bits(f_balance_bias_bits),
  .angle_diff_bits(f_angle_diff_bits),
  .angle_p_bits(f_angle_p_bits),
  .angle_i_bits(f_angle_i_bits),
  .angle_d_bits(f_angle_d_bits),
  .position_p_bits(f_position_p_bits),
  .position_i_bits(f_position_i_bits),
  .position_d_bits(f_position_d_bits)
 );
 assign moto=active_fast ? f_moto:b_moto;
 assign balance_pwm=active_fast ? f_balance_pwm:b_balance_pwm;
 assign position_pwm=active_fast ? f_position_pwm:b_position_pwm;
 assign position_bias_bits=active_fast ? f_position_bias_bits:b_position_bias_bits;
 assign position_diff_bits=active_fast ? f_position_diff_bits:b_position_diff_bits;
 assign balance_bias_bits=active_fast ? f_balance_bias_bits:b_balance_bias_bits;
 assign angle_diff_bits=active_fast ? f_angle_diff_bits:b_angle_diff_bits;
 assign angle_p_bits=active_fast ? f_angle_p_bits:b_angle_p_bits;
 assign angle_i_bits=active_fast ? f_angle_i_bits:b_angle_i_bits;
 assign angle_d_bits=active_fast ? f_angle_d_bits:b_angle_d_bits;
 assign position_p_bits=active_fast ? f_position_p_bits:b_position_p_bits;
 assign position_i_bits=active_fast ? f_position_i_bits:b_position_i_bits;
 assign position_d_bits=active_fast ? f_position_d_bits:b_position_d_bits;
endmodule
