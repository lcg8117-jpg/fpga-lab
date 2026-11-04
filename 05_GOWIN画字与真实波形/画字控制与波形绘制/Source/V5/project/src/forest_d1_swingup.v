module forest_d1_swingup #(
 parameter integer CLOCKS_PER_MS=50000,
 parameter integer ARCHIVE_SAMPLE_CLOCKS=3125,
 parameter integer THREE_PROFILES=1
)(
 input wire clk,rst_n,tick_1ms,start,cancel,session_enable,
 input wire [1:0] profile,
 input wire upright_valid,
 input wire [13:0] zero_q4,quiet_q4,
 input wire [9:0] adc_raw,
 input wire signed [31:0] encoder_count,
 input wire cfg_write,input wire [1:0] cfg_profile,
 input wire [7:0] cfg_id,input wire [31:0] cfg_value,
 input wire [1:0] read_profile,input wire [7:0] read_id,
 output reg [31:0] cfg_read_value,cfg_revision,
 output reg cfg_write_ok,cfg_write_error,
 output wire active,output reg finished,failed,
 output wire [7:0] stage,output reg [1:0] selected_profile,
 output wire [7:0] pair,output reg [31:0] elapsed_ms,
 output wire signed [15:0] duty_q15,
 output wire motor_bn1,motor_bn2,motor_pwmb,
 output wire [31:0] applied_diag,
 input wire kit_ip570,
 output wire signed [31:0] source_alpha_q20,source_alpha_rate_q20,source_arm_rate_q20,
 output wire [7:0] source_capture_flags,source_reason,
 output wire [5:0] capture_hold_ms,
 output wire source_capture_request
);
 reg [207:0] bank0,bank1,bank2,session_config;
 reg started;
 function [207:0] defaults;
  input [1:0] p;
  reg [207:0] v;
  begin
   v=0;v[7:0]=70;v[15:8]=(p==0)?60:70;
   v[31:16]=(p==0)?240:(p==1)?230:90;v[47:32]=(p==2)?90:230;
   v[63:48]=60;v[79:64]=3000;v[95:80]=1800;
   v[103:96]=1;v[111:104]=10;v[119:112]={6'd0,p};
   v[143:128]=90;v[159:144]=90;v[175:160]=90;
   defaults=v;
  end
 endfunction
 wire [207:0] requested_bank=profile==0?bank0:profile==1?bank1:bank2;
 wire [207:0] write_bank=cfg_profile==0?bank0:cfg_profile==1?bank1:bank2;
 wire [207:0] read_bank=read_profile==0?bank0:read_profile==1?bank1:bank2;
 reg [207:0] modified_bank;
 reg field_valid;
 always @*begin
  modified_bank=write_bank;field_valid=0;
  case(cfg_id)
   0:if(cfg_value>=1&&cfg_value<=90)begin modified_bank[7:0]=cfg_value[7:0];field_valid=1;end
   1:if(cfg_value>=1&&cfg_value<=90)begin modified_bank[15:8]=cfg_value[7:0];field_valid=1;end
   2:if(cfg_value>=1&&cfg_value<=1000)begin modified_bank[31:16]=cfg_value[15:0];field_valid=1;end
   3:if(cfg_value>=1&&cfg_value<=1000&&cfg_value<=write_bank[79:64])begin modified_bank[63:48]=cfg_value[15:0];field_valid=1;end
   4:if(cfg_value>=write_bank[63:48]&&cfg_value<=10000)begin modified_bank[79:64]=cfg_value[15:0];field_valid=1;end
   5:if(cfg_value>=1&&cfg_value<=1000)begin modified_bank[47:32]=cfg_value[15:0];field_valid=1;end
   6:if(cfg_value>=20&&cfg_value<=10000)begin modified_bank[95:80]=cfg_value[15:0];field_valid=1;end
   8:if(cfg_value>=1&&cfg_value<=10)begin modified_bank[103:96]=cfg_value[7:0];field_valid=1;end
   9:if(cfg_value<=50)begin modified_bank[111:104]=cfg_value[7:0];field_valid=1;end
   64:if(cfg_value>=1&&cfg_value<=255)begin modified_bank[143:128]=cfg_value[15:0];field_valid=1;end
   65:if(cfg_value>=1&&cfg_value<=255)begin modified_bank[159:144]=cfg_value[15:0];field_valid=1;end
   66:if(cfg_value>=1&&cfg_value<=254)begin modified_bank[175:160]=cfg_value[15:0];field_valid=1;end
   default:begin end
  endcase
  if(cfg_profile==2&&cfg_id!=0&&cfg_id!=64&&cfg_id!=65&&cfg_id!=66)field_valid=0;
  if(cfg_profile<2&&(cfg_id==64||cfg_id==65||cfg_id==66))field_valid=0;
 end
 always @*begin
  cfg_read_value=32'hffffffff;
  if(read_profile<3 && (THREE_PROFILES || read_profile==0))case(read_id)
   0:cfg_read_value={24'd0,read_bank[7:0]};
   1:cfg_read_value={24'd0,read_bank[15:8]};
   2:cfg_read_value={16'd0,read_bank[31:16]};
   3:cfg_read_value={16'd0,read_bank[63:48]};
   4:cfg_read_value={16'd0,read_bank[79:64]};
   5:cfg_read_value={16'd0,read_bank[47:32]};
   6:cfg_read_value={16'd0,read_bank[95:80]};
   7:cfg_read_value={30'd0,read_profile};
   8:cfg_read_value={24'd0,read_bank[103:96]};
   9:cfg_read_value={24'd0,read_bank[111:104]};
   64:cfg_read_value={16'd0,read_bank[143:128]};
   65:cfg_read_value={16'd0,read_bank[159:144]};
   66:cfg_read_value={16'd0,read_bank[175:160]};
   default:begin end
  endcase
  if(read_profile==2&&read_id!=0&&read_id!=7&&read_id!=64&&read_id!=65&&read_id!=66)cfg_read_value=32'hffffffff;
  if(read_profile<2&&(read_id==64||read_id==65||read_id==66))cfg_read_value=32'hffffffff;
 end
 // The root additionally gates cfg_write to STOP. Local ownership prevents
 // a delayed packet from changing any bank during an admitted swing session.
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin bank0<=defaults(0);bank1<=defaults(1);bank2<=defaults(2);cfg_revision<=0;cfg_write_ok<=0;cfg_write_error<=0;end
  else begin
   cfg_write_ok<=0;cfg_write_error<=0;
   if(cfg_write)begin
    if(!started&&!session_enable&&!cancel&&cfg_profile<3&&(THREE_PROFILES||cfg_profile==0)&&field_valid)begin
     case(cfg_profile)0:bank0<=modified_bank;1:bank1<=modified_bank;2:bank2<=modified_bank;default:begin end endcase
     cfg_revision<=cfg_revision+1'b1;cfg_write_ok<=1;
    end else cfg_write_error<=1;
   end
  end
 end
 wire source_run=started&&session_enable&&!cancel&&!finished&&!failed;
 wire [2:0] source_active,source_handoff,source_failed;
 wire signed [15:0] duty0,duty1,duty2;
 wire [6:0] state0,state1,state2;
 wire [5:0] hold0,hold1,hold2;
 wire [7:0] reason0,reason1;
 wire [1:0] archive_pair;
 wire archive_tick;
 wire selected_handoff=selected_profile==0?source_handoff[0]:selected_profile==1?source_handoff[1]:source_handoff[2];
 wire selected_failed=selected_profile==0?source_failed[0]:selected_profile==1?source_failed[1]:source_failed[2];
 assign active=started&&!finished&&!failed;
 assign stage={1'b0,selected_profile==0?state0:selected_profile==1?state1:state2};
 assign pair=selected_profile==2?{6'd0,archive_pair}:8'd0;
 wire signed [15:0] selected_duty=selected_profile==0?duty0:selected_profile==1?duty1:duty2;
 assign duty_q15=source_run?selected_duty:16'sd0;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin started<=0;finished<=0;failed<=0;selected_profile<=0;session_config<=0;elapsed_ms<=0;end
  else if(cancel||!session_enable)begin started<=0;finished<=0;failed<=0;elapsed_ms<=0;end
  else if(start&&!started)begin
   if(upright_valid&&profile<3&&(THREE_PROFILES||profile==0))begin
    started<=1;finished<=0;failed<=0;selected_profile<=profile;session_config<=requested_bank;elapsed_ms<=0;
   end else begin failed<=1;finished<=0;end
  end else if(started)begin
   if(selected_handoff)finished<=1;
   else if(selected_failed||!upright_valid)failed<=1;
   if(tick_1ms&&!finished&&!failed&&elapsed_ms!=32'hffffffff)elapsed_ms<=elapsed_ms+1'b1;
  end
 end
 wire signed [15:0] quiet_delta,archive_delta;
 wire signed [31:0] quiet_alpha,quiet_rate,quiet_arm_rate,archive_alpha,archive_rate,archive_arm_rate;
 wire [13:0] archive_q4;
 forest_d1_swing_archive_average #(.SAMPLE_CLOCKS(ARCHIVE_SAMPLE_CLOCKS)) archived_average(clk,rst_n,adc_raw,archive_q4);
 forest_d1_swing_observer quiet_observer(clk,rst_n,tick_1ms,upright_valid,quiet_q4,zero_q4,encoder_count,quiet_delta,quiet_alpha,quiet_rate,quiet_arm_rate,kit_ip570);
 // Both observers receive identical encoder epochs; reuse their exact arm rate.
 assign archive_arm_rate=quiet_arm_rate;
 forest_d1_swing_observer archived_observer(clk,rst_n,tick_1ms,upright_valid,archive_q4,zero_q4,encoder_count,archive_delta,archive_alpha,archive_rate,,kit_ip570);
 // Historical bottom predicates assume the upright sensor code is512.
 // Normalize only their swing-side raw coordinate, retaining physical zero.
 wire [9:0] normalized_raw=adc_raw-zero_q4[13:4]+10'd512;
 wire [511:0] timed_cfg={384'd0,16'd0,session_config[111:0]};
 wire [511:0] sa21_cfg={304'd0,session_config};
 j280_swing_source_swm4 #(.DYNAMIC_CONFIG(1),.HANDOFF_HOLD_MS(20),.CLOCKS_PER_MS(CLOCKS_PER_MS)) source_0(
  .clk_50m(clk),.rst_n(rst_n),.run_request(source_run&&selected_profile==0),.zero_valid(upright_valid),.estop_active(cancel),.adc_otr(1'b0),
  .pump_percent(8'd70),.final_pump_percent(8'd60),.final_trigger_deg(8'd85),.pulse_ms_cfg(8'd240),.swing_config(timed_cfg),.configuration_revision(cfg_revision),
  .adc_raw(normalized_raw),.adc_zero_q4(14'd8192),.alpha_raw_delta_q4(quiet_delta),.alpha_q20(quiet_alpha),.alpha_dot_q20(quiet_rate),.theta_dot_q20(quiet_arm_rate),.encoder_count(encoder_count),
  .swing_active(source_active[0]),.handoff_request(source_handoff[0]),.swing_duty_q15(duty0),.swing_state(state0),.final_fail(source_failed[0]),.handoff_hold_ms(hold0),.phase_end_reason(reason0));
 generate if(THREE_PROFILES)begin:extra_profiles
  j280_swing_source_sa21 #(.DYNAMIC_CONFIG(1),.HANDOFF_HOLD_MS(20),.CLOCKS_PER_MS(CLOCKS_PER_MS)) source_1(
   .clk_50m(clk),.rst_n(rst_n),.run_request(source_run&&selected_profile==1),.zero_valid(upright_valid),.estop_active(cancel),.adc_otr(1'b0),
   .pump_percent(8'd70),.final_pump_percent(8'd70),.final_trigger_deg(8'd85),.pulse_ms_cfg(8'd230),.swing_config(sa21_cfg[127:0]),
   .adc_raw(normalized_raw),.adc_zero_q4(14'd8192),.alpha_raw_delta_q4(quiet_delta),.alpha_q20(quiet_alpha),.alpha_dot_q20(quiet_rate),.theta_dot_q20(quiet_arm_rate),.encoder_count(encoder_count),
   .swing_active(source_active[1]),.handoff_request(source_handoff[1]),.swing_duty_q15(duty1),.swing_state(state1),.final_fail(source_failed[1]),.handoff_hold_ms(hold1),.phase_end_reason(reason1));
  reg watcher_bumped;
  always @(posedge clk or negedge rst_n)
   if(!rst_n)watcher_bumped<=0;
   else if(!source_run||selected_profile!=2)watcher_bumped<=0;
   else if(archive_pair==1&&(state2==24||state2==34))watcher_bumped<=1;
  wire [7:0] archive_pulse=watcher_bumped||archive_pair>=2?session_config[167:160]+8'd1:
   archive_pair==0?session_config[135:128]:session_config[151:144];
  j280_swing_source_144 #(.CLOCKS_PER_MS(CLOCKS_PER_MS)) source_2(
   .clk_50m(clk),.rst_n(rst_n),.run_request(source_run&&selected_profile==2),.zero_valid(upright_valid),.estop_active(cancel),.adc_otr(1'b0),
   .pump_percent(session_config[7:0]),.final_pump_percent(8'd70),.final_trigger_deg(8'd85),.pulse_ms_cfg(archive_pulse),
   .adc_raw(normalized_raw),.adc_zero_q4(14'd8192),.alpha_raw_delta_q4(archive_delta),.alpha_q20(archive_alpha),.alpha_dot_q20(archive_rate),.theta_dot_q20(archive_arm_rate),.encoder_count(encoder_count),
   .swing_active(source_active[2]),.handoff_request(source_handoff[2]),.swing_duty_q15(duty2),.swing_state(state2),.final_fail(source_failed[2]),.handoff_hold_ms(hold2),.observe_pair(archive_pair),.observe_tick(archive_tick));
 end else begin:no_extra_profiles
  assign source_active[2:1]=0;assign source_handoff[2:1]=0;assign source_failed[2:1]=0;
  assign hold1=0;assign hold2=0;assign reason1=0;
  assign duty1=0;assign duty2=0;assign state1=0;assign state2=0;assign archive_pair=0;assign archive_tick=0;
 end endgenerate
 // These are the selected source's actual observer values, never balanced
 // angle telemetry reused as a proxy. The parent latches failure history.
 assign source_alpha_q20=selected_profile==2?archive_alpha:quiet_alpha;
 assign source_alpha_rate_q20=selected_profile==2?archive_rate:quiet_rate;
 assign source_arm_rate_q20=selected_profile==2?archive_arm_rate:quiet_arm_rate;
 assign capture_hold_ms=selected_profile==0?hold0:selected_profile==1?hold1:hold2;
 assign source_reason=selected_profile==0?reason0:selected_profile==1?reason1:
  source_failed[2]?8'd254:source_handoff[2]?8'd4:8'd0;
 assign source_capture_request=selected_handoff;
 wire capture_angle_ok=source_alpha_q20>=-32'sd274517&&source_alpha_q20<=32'sd274517;
 wire capture_rate_ok=source_alpha_rate_q20>=-32'sd3660218&&source_alpha_rate_q20<=32'sd3660218;
 wire capture_arm_ok=source_arm_rate_q20>=-32'sd2196131&&source_arm_rate_q20<=32'sd2196131;
 assign source_capture_flags={upright_valid,selected_failed,selected_handoff,capture_hold_ms!=0,
  capture_angle_ok&&capture_rate_ok&&capture_arm_ok,capture_arm_ok,capture_rate_ok,capture_angle_ok};
 wire normal_bn1,normal_bn2,normal_pwm,archive_bn1,archive_bn2,archive_pwm;
 wire [31:0] normal_diag,archive_diag;
 wire output_enabled=source_run&&upright_valid;
 forest_d1_swing_pwm40k #(.SOFT_COMMAND_RISE(1),.DIAGNOSTIC_SWITCHES(1)) normal_driver(
  .clk_50m(clk),.rst_n(rst_n),.motor_enable(output_enabled&&selected_profile!=2),.duty_q15(duty_q15),.soft_rise_enable(1'b1),
  .AN1(normal_bn1),.AN2(normal_bn2),.PWMA(normal_pwm),.diagnostic_applied(normal_diag));
 j280_pwm_source_144 archived_driver(.clk_50m(clk),.rst_n(rst_n),.motor_enable(output_enabled&&selected_profile==2),.duty_q15(duty_q15),
  .AN1(archive_bn1),.AN2(archive_bn2),.PWMA(archive_pwm),.diagnostic_applied(archive_diag));
 assign motor_bn1=output_enabled?(selected_profile==2?archive_bn1:normal_bn1):1'b0;
 assign motor_bn2=output_enabled?(selected_profile==2?archive_bn2:normal_bn2):1'b0;
 assign motor_pwmb=output_enabled?(selected_profile==2?archive_pwm:normal_pwm):1'b0;
 assign applied_diag=selected_profile==2?archive_diag:normal_diag;
endmodule
