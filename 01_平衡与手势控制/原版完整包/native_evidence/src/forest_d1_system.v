// One physical owner, one quiet measurement chain and one serial owner.
module forest_d1_system #(
 parameter integer VARIANT=3,STARTUP_CYCLES=100000000,KEY_STABLE_CYCLES=500000,ZERO_SAMPLES=1024,
 parameter integer WATCHDOG_CYCLES=25000000,
 parameter integer FEATURE_SWING=(VARIANT!=3),FEATURE_THREE_PROFILES=(VARIANT==1),
 parameter integer FEATURE_GESTURE=(VARIANT!=3),FEATURE_MOTION=(VARIANT==2),
 parameter integer FEATURE_ADAPTIVE=(VARIANT==3),FEATURE_AUTORETURN=(VARIANT==1),
 parameter integer PROTOCOL_VERSION=2,PROTOCOL_VARIANT=VARIANT,
 parameter [31:0] SCHEMA=32'h46443203,CAPABILITIES=32'd0,
 parameter [31:0] FIRMWARE_ID=32'h46443233,
 parameter signed [31:0] SWING_CAPTURE_ALPHA_Q20=32'sd274517,
 parameter signed [31:0] SWING_CAPTURE_AD_Q20=32'sd3660218,
 parameter signed [31:0] SWING_CAPTURE_THD_Q20=32'sd2196131,
 parameter [15:0] SWING_HANDOFF_ENTER_Q4=16'd272
)(
 input clk_50m,rst_n,input [3:0] key_sw,input uart_rx,output uart_tx,
 output adc_clk,adc_oe_n,input [9:0] adc_data_in,input adc_otr,enc1_a,enc1_b,
 output AN1,AN2,PWMA,BN1,BN2,PWMB,inout gesture_scl,gesture_sda
);
 localparam [191:0] DEFAULT_GAINS={32'h441b0000,32'd0,32'h41c80000,32'h43820000,32'd0,32'h42440000};
 wire kit_ip570,kit_changed,kit_changed_motor;
 wire reset_n;forest_d1_reset resetter(clk_50m,rst_n,reset_n);
 reg [31:0] startup_count;
 wire ready=STARTUP_CYCLES==0 || startup_count>=STARTUP_CYCLES;
 always @(posedge clk_50m or negedge reset_n)
  if(!reset_n)startup_count<=0;else if(!ready)startup_count<=startup_count+1'b1;
 wire control_rst_n /* synthesis syn_maxfan=64 */=reset_n && ready;
 reg [15:0] millisecond_div;reg [31:0] uptime_ms;
 wire tick_1ms=millisecond_div==49999;
 always @(posedge clk_50m or negedge control_rst_n)
  if(!control_rst_n)begin millisecond_div<=0;uptime_ms<=0;end
  else if(tick_1ms)begin millisecond_div<=0;uptime_ms<=uptime_ms+1'b1;end
  else millisecond_div<=millisecond_div+1'b1;
 wire [9:0] raw,clean;wire [13:0] filtered;
 wire [31:0] actuator_diag,adc_cycle;wire adc_batch_valid;
 j280_adc_capture_trace #(.DIAGNOSTIC_SWITCHES(1),.QUIET_CACHE_WORDS(1),.ADC_WORD_GUARD(1),.WINDOW_SAMPLES(64)) adc(
  .clk_50m(clk_50m),.rst_n(reset_n),.adc_data_in(adc_data_in),.average_enable(1'b1),
  .trace_adc_otr(adc_otr),.trace_actuator_diag(actuator_diag),.trace_motor_pins({BN1,BN2,PWMB}),
  .adc_clk(adc_clk),.adc_oe_n(adc_oe_n),.adc_raw(raw),.adc_clean_stage(clean),.adc_filtered_q4(filtered),
  .control_sample_valid(adc_batch_valid),.trace_valid(),.trace_record(),.trace_time_counter(adc_cycle));
 wire signed [31:0] enc;wire [15:0] invalid;
 j280_encoder_x4 encoder(.clk_50m(clk_50m),.rst_n(reset_n),.enc_a(enc1_a),.enc_b(enc1_b),
  .count(enc),.invalid_transition_count(invalid));
 wire [3:0] pressed,key_pulse;
 genvar key_index;generate for(key_index=0;key_index<4;key_index=key_index+1)begin:keys
  j280_key_debounce #(.STABLE_CYCLES(KEY_STABLE_CYCLES)) key(
   .clk_50m(clk_50m),.rst_n(reset_n),.key_n(key_sw[key_index]),
   .pressed(pressed[key_index]),.press_pulse(key_pulse[key_index]));
 end endgenerate
 wire physical_estop=pressed[3] || !key_sw[3];
 wire remote_stop,remote_zero,remote_run,remote_clear,remote_swing,serial_estop,watchdog_stop;
 wire [2:0] mode;wire [7:0] fault;wire zero_valid,capture_active,run_session;wire run_session_controls;
 wire balance_permission,swing_permission,source_ready,source_frame_ready;
 wire [13:0] zero_q4;wire signed [31:0] encoder_zero;
 wire [191:0] stored_gains;wire [31:0] stored_revision,last_mutation,config_revision,gateway_errors;
 wire manual_write,adapt_enable_write,adapt_enable_value;
 wire [1:0] selected_profile;wire [2:0] gesture_map;
 wire left_positive,cw_positive,gesture_enabled;
 wire cfg_write;wire [7:0] cfg_group,cfg_id;wire [31:0] cfg_value,cfg_read_value,swing_cfg_revision;
 wire cfg_ok,cfg_error;
 wire [31:0] swing_cfg_read_value,auto_cfg_read_value,auto_cfg_revision;
 wire swing_cfg_ok,swing_cfg_error,auto_cfg_ok,auto_cfg_error;
 wire [7:0] swing_pair;
 wire auto_enabled,auto_reference_valid,auto_start_source,auto_preparing,auto_failed,auto_return_active,auto_launch_qualified;
 wire [7:0] auto_state,auto_result;wire [31:0] auto_attempt_seq,auto_source_start_ms;
 wire signed [31:0] auto_anchor,auto_error;wire [13:0] auto_downward;wire [15:0] auto_still_ms;
 wire signed [15:0] auto_return_duty;
 wire [31:0] planner_cfg_read_value,planner_cfg_revision;
 wire planner_cfg_ok,planner_cfg_error;
 wire [31:0] external_cfg_revision=swing_cfg_revision+auto_cfg_revision+planner_cfg_revision;
 assign cfg_read_value=(FEATURE_MOTION && cfg_group==251) ? planner_cfg_read_value:
  (FEATURE_AUTORETURN && cfg_group==253) ? (cfg_id==9 ? {24'd0,swing_pair}:auto_cfg_read_value):swing_cfg_read_value;
 function source_read_invalid;
  input [1:0] profile;input [7:0] id;
  begin
   source_read_invalid=1;
   if(profile<3 && (FEATURE_THREE_PROFILES || profile==0))case(id)
    0,1,2,3,4,5,6,7,8,9,64,65,66:source_read_invalid=0;
    default:begin end
   endcase
   if(profile==2 && id!=0 && id!=7 && id!=64 && id!=65 && id!=66)source_read_invalid=1;
   if(profile<2 && (id==64 || id==65 || id==66))source_read_invalid=1;
  end
 endfunction
 reg planner_read_invalid;
 always @*begin
  planner_read_invalid=1;
  case(cfg_id)0,1,2,3,4,5,6,7,8,9,10:planner_read_invalid=0;default:begin end endcase
 end
 wire cfg_read_invalid=(FEATURE_MOTION && cfg_group==251) ? planner_read_invalid:
  (FEATURE_AUTORETURN && cfg_group==253) ? (cfg_read_value==32'hffffffff):source_read_invalid(cfg_group[1:0],cfg_id);
 assign cfg_ok=swing_cfg_ok || auto_cfg_ok || planner_cfg_ok;
 assign cfg_error=swing_cfg_error || auto_cfg_error || planner_cfg_error;
 wire remote_motion;wire [2:0] remote_motion_command;
 wire motion_accepted,motion_rejected;wire [7:0] motion_reason;
 wire [383:0] command;wire command_valid;
 wire [623:0] reply;wire reply_valid,reply_ready;
 wire [31:0] rx_errors,tlm_dropped,reply_dropped;
 reg tlm_valid;reg [2047:0] tlm_data;wire tlm_ready,link_busy;
 wire [31:0] active_revision,adaptive_epoch;
 wire [31:0] frames;
 wire adaptation_enabled,adaptation_active;
 wire gesture_ready,gesture_busy,gesture_event;wire [3:0] gesture_code;
 wire motion_guard_active;
 wire [7:0] gesture_error,gesture_id0,gesture_id1,gesture_init,raw43,raw44,decode_reason;
 wire [7:0] gesture_decode_state;
 wire [15:0] gesture_errors;wire [31:0] gesture_poll,gesture_sequence,suppressed_gestures;
 wire gesture_stop=FEATURE_GESTURE && gesture_event && gesture_code==4;
 wire physical_run=key_pulse[1] && (source_ready || run_session);
 wire stopping=remote_stop || watchdog_stop || gesture_stop || (key_pulse[1] && run_session_controls);
 wire estop=physical_estop || serial_estop;
 wire gesture_start=FEATURE_GESTURE && gesture_event && gesture_code==3 && gesture_ready &&
  !run_session && zero_valid && !capture_active && fault==0 && !estop && !stopping;
 wire swing_request=remote_swing || gesture_start;
 reg [31:0] prior_config_revision;
 always @(posedge clk_50m or negedge control_rst_n)
  if(!control_rst_n)prior_config_revision<=0;else prior_config_revision<=config_revision;
 wire config_changed=config_revision!=prior_config_revision;
 wire [31:0] snapshot_epoch;
 forest_d1_gateway #(.USE_EXTERNAL_READ_INVALID(1),.VARIANT(VARIANT),.WATCHDOG_CYCLES(WATCHDOG_CYCLES),.FIRMWARE_ID(FIRMWARE_ID),.AUTO_RETURN_CAPABLE(FEATURE_AUTORETURN),
  .SWING_CAPABLE(FEATURE_SWING),.THREE_PROFILES(FEATURE_THREE_PROFILES),
  .GESTURE_CAPABLE(FEATURE_GESTURE),.MOTION_CAPABLE(FEATURE_MOTION),.ADAPTIVE_CAPABLE(FEATURE_ADAPTIVE),
  .PROTOCOL_VERSION(PROTOCOL_VERSION),.PROTOCOL_VARIANT(PROTOCOL_VARIANT),.SCHEMA(SCHEMA),.CAPABILITIES(CAPABILITIES)) gateway(
  .clk(clk_50m),.rst_n(control_rst_n),.command_valid(command_valid),.command_data(command),
  .reply_valid(reply_valid),.reply_data(reply),.mode(mode),.fault(fault),.zero_valid(zero_valid),
  .capture_active(capture_active),.run_session(run_session),.motor_permitted(balance_permission || swing_permission),
  .physical_estop_active(physical_estop),.source_ready(source_ready),.balance_active(mode==3 && balance_permission),
  .adapt_enabled(adaptation_enabled),.adapt_active(adaptation_active),.physical_start_event(physical_run || gesture_start),
  .auto_return_enabled(auto_enabled),.auto_reference_valid(auto_reference_valid),
  .active_revision(active_revision),.control_epoch(snapshot_epoch),.adaptive_epoch(adaptive_epoch),
  .stop_pulse(remote_stop),.zero_pulse(remote_zero),.run_pulse(remote_run),.clear_pulse(remote_clear),
  .swing_start_pulse(remote_swing),.serial_estop_latched(serial_estop),.watchdog_stop(watchdog_stop),
  .gains_stored(stored_gains),.gain_revision(stored_revision),.last_mutation_id(last_mutation),
  .manual_write(manual_write),.enable_write(adapt_enable_write),.enable_value(adapt_enable_value),
  .selected_profile(selected_profile),.gesture_map_flags(gesture_map),.left_positive(left_positive),
  .cw_positive(cw_positive),.gesture_enable(gesture_enabled),.kit_ip570(kit_ip570),.kit_changed(kit_changed),.kit_changed_motor(kit_changed_motor),.cfg_write(cfg_write),.cfg_group(cfg_group),
  .cfg_id(cfg_id),.cfg_value(cfg_value),.cfg_read_invalid(cfg_read_invalid),.cfg_read_value(cfg_read_value),.external_cfg_revision(external_cfg_revision),
  .cfg_result_valid(cfg_ok || cfg_error),.cfg_result_ok(cfg_ok),
  .motion_valid(remote_motion),.motion_command(remote_motion_command),
  .motion_result_valid(motion_accepted || motion_rejected),.motion_result_ok(motion_accepted),
  .config_revision(config_revision),.gateway_errors(gateway_errors));
 forest_d1_link link(.clk(clk_50m),.rst_n(control_rst_n),.uart_rx(uart_rx),.uart_tx(uart_tx),
  .command_valid(command_valid),.command_data(command),.reply_valid(reply_valid),.reply_data(reply),.reply_ready(reply_ready),
  .tlm_valid(tlm_valid),.tlm_data(tlm_data),.tlm_ready(tlm_ready),.rx_error_count(rx_errors),
  .tlm_dropped(tlm_dropped),.reply_dropped(reply_dropped),.busy(link_busy));
 generate if(FEATURE_GESTURE)begin:gesture_hardware
  forest_d1_gesture_reader reader(.clk(clk_50m),.rst_n(control_rst_n),.restart(!gesture_enabled),
   .flush(stopping || estop || fault!=0 || config_changed),.motion_inhibit(motion_guard_active),.map_flags(gesture_map),
   .scl(gesture_scl),.sda(gesture_sda),.ready(gesture_ready),.busy(gesture_busy),.error_code(gesture_error),
   .error_count(gesture_errors),.id0(gesture_id0),.id1(gesture_id1),.init_index(gesture_init),
   .raw43(raw43),.raw44(raw44),.poll_seq(gesture_poll),.event_seq(gesture_sequence),
   .event_valid(gesture_event),.event_code(gesture_code),.suppressed_count(suppressed_gestures),.decode_reason(decode_reason),.diagnostic_state(gesture_decode_state));
 end else begin:no_gesture
  assign gesture_scl=1'bz;assign gesture_sda=1'bz;
  assign gesture_ready=0;assign gesture_busy=0;assign gesture_event=0;assign gesture_code=0;
  assign gesture_error=0;assign gesture_errors=0;assign gesture_id0=0;assign gesture_id1=0;assign gesture_init=0;
  assign gesture_decode_state=0;assign raw43=0;assign raw44=0;assign gesture_poll=0;assign gesture_sequence=0;assign suppressed_gestures=0;assign decode_reason=0;
 end endgenerate
 reg [1:0] otr_sync;reg [6:0] otr_count;reg otr_qualified;
 always @(posedge clk_50m or negedge reset_n)begin
  if(!reset_n)begin otr_sync<=0;otr_count<=0;otr_qualified<=0;end
  else begin
   otr_sync<={otr_sync[0],adc_otr};
   if(otr_sync[1] && raw>8 && raw<1015)begin
    if(otr_count<63)otr_count<=otr_count+1'b1;else otr_qualified<=1;
   end else begin otr_count<=0;otr_qualified<=0;end
  end
 end
 wire signed [31:0] source_alpha_q20,source_alpha_rate_q20,source_arm_rate_q20;
 wire [7:0] source_capture_flags,source_reason;wire [5:0] capture_hold_ms;wire source_capture_request;
 wire swing_active,swing_finished,swing_failed;wire [7:0] swing_stage;
 wire [1:0] swing_profile;wire [31:0] swing_elapsed,swing_diag;
 wire signed [15:0] swing_duty;wire swing_bn1,swing_bn2,swing_pwm;
 reg swing_start_pending,source_start;
 wire qualified_source_start=source_start && (!auto_enabled || auto_launch_qualified);
 reg handoff_started,handoff_primed;reg [4:0] prime_reset_count;reg [8:0] handoff_wait_ms;
 reg hold_pulse,handoff_pulse;reg prime_pending;
 // Fifteen-bit modular subtraction is explicitly folded into the ADC period.
 wire signed [15:0] delta_unwrapped=$signed({2'd0,filtered})-$signed({2'd0,zero_q4});
 wire signed [15:0] delta_q4=delta_unwrapped>8191 ? delta_unwrapped-16'sd16384:
  delta_unwrapped< -8192 ? delta_unwrapped+16'sd16384:delta_unwrapped;
 wire [15:0] delta_abs=delta_q4[15] ? -delta_q4:delta_q4;
 wire handoff_timeout=handoff_started && handoff_wait_ms>=250;
 wire parent_swing_failed=swing_failed || handoff_timeout || (auto_enabled && auto_failed);
 wire auto_blocks_source=auto_enabled && auto_state>=1 && auto_state<=3;
 wire [7:0] visible_swing_stage=auto_enabled && auto_preparing ? (8'd239+auto_state):swing_stage;
 wire [11:0] mapped_angle,raw_mapped_angle,angle,raw_guard_mean;

 wire signed [15:0] mapped_encoder,encoder_sample,control_encoder;
 wire mapping_fault,angle_capture,angle_valid,raw_angle_valid,acquisition_fault,sample_en,frame_start;
 wire source_rst_n /* synthesis syn_maxfan=64 */;
 reg [3:0] mapping_settle;
 always @(posedge clk_50m or negedge control_rst_n)
  if(!control_rst_n)mapping_settle<=0;
  else if(!zero_valid || capture_active || prime_reset_count!=0 || hold_pulse)mapping_settle<=0;
  else mapping_settle<={mapping_settle[2:0],1'b1};
 wire mapping_valid=&mapping_settle;
 assign source_rst_n=control_rst_n && zero_valid && mapping_valid && prime_reset_count==0;
 wire busy,done;wire signed [31:0] moto,balance_pwm,position_pwm,target;
 wire control_fast_p,compare_ready,compare_deadline_fault;
 reg fast_acquisition_fault;
 wire actual_balance_permission=balance_permission && mapping_valid && !mapping_fault && !estop;
 wire [31:0] control_angle_d_epoch,control_position_d_epoch,control_position_p_epoch;
 wire [2:0] fast_position_div;
 wire [2:0] position_div;
 // Observer values continue updating while finished waits for the balance prime.
 // Recheck all three current source conditions in addition to the quiet/raw guards.
 wire handoff_capture_window=delta_abs<=SWING_HANDOFF_ENTER_Q4 && source_capture_flags[3] && raw_guard_mean>=2285 && raw_guard_mean<=3885 &&
  angle>=2285 && angle<=3885 && !acquisition_fault && !mapping_fault;
 wire handoff_capture_request=mode==5 && handoff_started && !handoff_primed && handoff_capture_window;
 reg source_frame_ready_reg;
 assign source_frame_ready=source_frame_ready_reg && compare_ready;
 always @(posedge clk_50m or negedge source_rst_n)
  if(!source_rst_n)source_frame_ready_reg<=0;
  else if(sample_en)source_frame_ready_reg<=0;else if(done)source_frame_ready_reg<=1;
 assign source_ready=source_frame_ready && !mapping_fault;
 always @(posedge clk_50m or negedge control_rst_n)begin
  if(!control_rst_n)begin swing_start_pending<=0;source_start<=0;handoff_started<=0;handoff_primed<=0;prime_reset_count<=0;
   handoff_wait_ms<=0;hold_pulse<=0;handoff_pulse<=0;prime_pending<=0;end
  else begin
   source_start<=0;hold_pulse<=0;handoff_pulse<=0;
   if(prime_reset_count!=0)prime_reset_count<=prime_reset_count-1'b1;
   if(stopping || estop || fault!=0 || !zero_valid)begin
    swing_start_pending<=0;handoff_started<=0;handoff_primed<=0;handoff_wait_ms<=0;prime_pending<=0;
   end else begin
    if(swing_request && !run_session)begin swing_start_pending<=1;handoff_started<=0;handoff_primed<=0;handoff_wait_ms<=0;end
    if(mode==5 && swing_permission && (auto_enabled ?
       (auto_start_source && auto_launch_qualified):swing_start_pending))begin source_start<=1;swing_start_pending<=0;end
    if(mode==5 && swing_finished && !handoff_started)begin
     handoff_started<=1;handoff_primed<=0;handoff_wait_ms<=0;
    end
    if(handoff_started && mode==5)begin
     if(tick_1ms && handoff_wait_ms<511)handoff_wait_ms<=handoff_wait_ms+1'b1;
      // The source can finish before a fresh safe balance frame is available.
     // Capture the current arm position only when qualified, then require
     // a fresh numerical frame from the existing reset/prime sequence.
     if(!handoff_capture_window)handoff_primed<=0;
     if(handoff_capture_request)begin
      hold_pulse<=1;handoff_primed<=1;prime_reset_count<=15;prime_pending<=1;
     end
     if(handoff_primed && source_frame_ready && handoff_capture_window)handoff_pulse<=1;
    end
    if(mode!=5 && !swing_request)begin handoff_started<=0;handoff_primed<=0;handoff_wait_ms<=0;end
    if(sample_en && prime_reset_count==0 && !hold_pulse && !handoff_capture_request && !
       (mode==5 && swing_finished && !handoff_started))prime_pending<=0;
   end
  end
 end
 generate if(FEATURE_SWING)begin:swing_hardware
  forest_d1_swingup #(.THREE_PROFILES(FEATURE_THREE_PROFILES)) swing(.clk(clk_50m),.rst_n(control_rst_n),.tick_1ms(tick_1ms),
   .start(qualified_source_start),.cancel(stopping || estop || fault!=0 || auto_blocks_source),.session_enable(mode==5 && swing_permission && !auto_blocks_source),
   .profile(selected_profile),.upright_valid(zero_valid),.zero_q4(zero_q4),.quiet_q4(filtered),.adc_raw(raw),.encoder_count(enc),
   .cfg_write(cfg_write && cfg_group<3),.cfg_profile(cfg_group[1:0]),.cfg_id(cfg_id),.cfg_value(cfg_value),
   .read_profile(cfg_group[1:0]),.read_id(cfg_id),.cfg_read_value(swing_cfg_read_value),.cfg_revision(swing_cfg_revision),
   .cfg_write_ok(swing_cfg_ok),.cfg_write_error(swing_cfg_error),.active(swing_active),.finished(swing_finished),.failed(swing_failed),
   .stage(swing_stage),.selected_profile(swing_profile),.pair(swing_pair),.elapsed_ms(swing_elapsed),
   .kit_ip570(kit_ip570),.source_alpha_q20(source_alpha_q20),.source_alpha_rate_q20(source_alpha_rate_q20),
   .source_arm_rate_q20(source_arm_rate_q20),.source_capture_flags(source_capture_flags),.source_reason(source_reason),
   .capture_hold_ms(capture_hold_ms),.source_capture_request(source_capture_request),
   .duty_q15(swing_duty),.motor_bn1(swing_bn1),.motor_bn2(swing_bn2),.motor_pwmb(swing_pwm),.applied_diag(swing_diag));
 end else begin:no_swing
  assign swing_cfg_read_value=32'hffffffff;assign swing_cfg_revision=0;assign swing_cfg_ok=0;assign swing_cfg_error=0;
  assign swing_active=0;assign swing_finished=0;assign swing_failed=0;assign swing_stage=0;assign swing_profile=0;
  assign source_alpha_q20=0;assign source_alpha_rate_q20=0;assign source_arm_rate_q20=0;
  assign source_capture_flags=0;assign source_reason=0;assign capture_hold_ms=0;assign source_capture_request=0;
  assign swing_pair=0;assign swing_elapsed=0;assign swing_duty=0;assign swing_bn1=0;assign swing_bn2=0;assign swing_pwm=0;assign swing_diag=0;
 end endgenerate
 // Independent pre-swing automation. It has no output to the balance PID,
 // mapper, sampler, gain bank, integrators, or existing balance actuator.
 generate if(FEATURE_AUTORETURN)begin:auto_return_hardware
  j280_swing_autoreturn auto_return(.clk(clk_50m),.rst_n(control_rst_n),.tick_1ms(tick_1ms),
   .request_start(swing_request),.source_started(qualified_source_start),.cancel(stopping || estop || (remote_run && run_session)),
   .zero_event(key_pulse[0] || remote_zero),.session_permitted(swing_permission),
   .mode(mode),.fault(fault),.zero_valid(zero_valid),.capture_active(capture_active),.kit_ip570(kit_ip570),.kit_changed(kit_changed),
   .quiet_q4(filtered),.zero_q4(zero_q4),.encoder_count(enc),.uptime_ms(uptime_ms),
   .cfg_write(cfg_write && cfg_group==253),.cfg_id(cfg_id),.read_id(cfg_id),.cfg_value(cfg_value),
   .cfg_read_value(auto_cfg_read_value),.cfg_revision(auto_cfg_revision),.cfg_ok(auto_cfg_ok),.cfg_error(auto_cfg_error),
   .enabled(auto_enabled),.reference_valid(auto_reference_valid),.anchor_encoder(auto_anchor),.downward_q4(auto_downward),
   .state(auto_state),.result(auto_result),.attempt_seq(auto_attempt_seq),.source_start_ms(auto_source_start_ms),
   .still_ms(auto_still_ms),.return_error(auto_error),.start_source_pulse(auto_start_source),.launch_qualified(auto_launch_qualified),
   .return_active(auto_return_active),.preparing(auto_preparing),.failed(auto_failed),.return_duty_q15(auto_return_duty));
 end else begin:no_auto_return
  assign auto_enabled=0;assign auto_reference_valid=0;assign auto_cfg_read_value=32'hffffffff;assign auto_cfg_revision=0;
  assign auto_cfg_ok=0;assign auto_cfg_error=0;assign auto_state=0;assign auto_result=0;assign auto_anchor=0;
  assign auto_downward=0;assign auto_attempt_seq=0;assign auto_source_start_ms=0;assign auto_still_ms=0;
  assign auto_error=0;assign auto_start_source=0;assign auto_return_active=0;assign auto_preparing=0;assign auto_failed=0;
  assign auto_return_duty=0;assign auto_launch_qualified=0;
 end endgenerate
 wire original_mapping_fault;
 forest_d1_run_inputs #(.USE_Q4_INPUT(1)) inputs(.clk(clk_50m),.rst_n(control_rst_n),.angle_epoch_valid(source_rst_n),
  .angle_capture(angle_capture),.kit_ip570(kit_ip570),.adc_raw(raw),.adc_q4(filtered),.zero_q4(zero_q4),.encoder_count(enc),.encoder_zero(encoder_zero),
  .angle_adc12(mapped_angle),.encoder_forest(mapped_encoder),.encoder_range_fault(original_mapping_fault),.angle_valid(angle_valid));
 forest_d1_run_inputs raw_inputs(.clk(clk_50m),.rst_n(control_rst_n),.angle_epoch_valid(source_rst_n),
  .angle_capture(angle_capture),.kit_ip570(kit_ip570),.adc_raw(raw),.adc_q4(filtered),.zero_q4(zero_q4),.encoder_count(enc),.encoder_zero(encoder_zero),
  .angle_adc12(raw_mapped_angle),.encoder_forest(),.encoder_range_fault(),.angle_valid(raw_angle_valid));
 wire signed [31:0] encoder_relative=enc-encoder_zero;
 wire signed [63:0] wide_physical,wide_encoder,wide_target;
 wire signed [15:0] motion_encoder,motion_target;
 wire motion_active,motion_fault;wire [2:0] motion_state;wire signed [1:0] motion_direction;
 wire [31:0] motion_events,planner_request;
 wire [7:0] planner_action;wire [3:0] planner_phase;
 wire signed [63:0] motion_origin,motion_final;
 wire signed [31:0] planner_velocity_q,planner_acceleration_q;
 wire signed [31:0] nominal_velocity_q,nominal_acceleration_q;
 wire motion_velocity_valid;
 wire reanchor;
 reg motion_session;
 always @(posedge clk_50m or negedge control_rst_n)
  if(!control_rst_n)motion_session<=0;
  else if(hold_pulse || reanchor || !zero_valid || capture_active ||
     ((physical_run || remote_run) && !run_session && source_ready && fault==0 && !estop))motion_session<=0;
  else if(motion_accepted)motion_session<=1;
 wire trajectory_done,tracking_settled;wire signed [31:0] measured_velocity_cps,reference_velocity_cps;
 wire signed [63:0] tracking_error;wire [7:0] settle_ticks,motion_stop_reason;
 reg gesture_motion_valid;reg [2:0] gesture_motion_command;
 reg [7:0] last_gesture,last_action_reason;
 always @(posedge clk_50m or negedge control_rst_n)begin
  if(!control_rst_n)begin gesture_motion_valid<=0;gesture_motion_command<=0;last_gesture<=0;last_action_reason<=0;end
  else begin
   gesture_motion_valid<=0;
   if(gesture_event)begin
    last_gesture<={4'd0,gesture_code};
    if(gesture_code==4)last_action_reason<=6;
    else if(gesture_code==3)last_action_reason<=gesture_start ? 1:2;
    else if(FEATURE_MOTION && mode==3 && balance_permission && gesture_ready && !stopping && !estop)begin
     case(gesture_code)
      7:begin gesture_motion_valid<=1;gesture_motion_command<=1;end
      8:begin gesture_motion_valid<=1;gesture_motion_command<=2;end
      2:begin gesture_motion_valid<=1;gesture_motion_command<=3;end
      1:begin gesture_motion_valid<=1;gesture_motion_command<=4;end
      default:last_action_reason<=5;
     endcase
    end else last_action_reason<=5;
   end
   if(motion_accepted)last_action_reason<=3;
   if(motion_rejected)last_action_reason<=4;
  end
 end
 reg last_zero_valid;
 always @(posedge clk_50m or negedge control_rst_n)
  if(!control_rst_n)last_zero_valid<=0;else last_zero_valid<=zero_valid;
 assign reanchor=zero_valid && !last_zero_valid;
 generate if(FEATURE_MOTION)begin:motion_hardware
  forest_motion_planner #(.VARIANT(2)) motion(.clk(clk_50m),.rst_n(control_rst_n),.tick_5ms(frame_start),.reanchor(reanchor),
   .capture_hold(hold_pulse),.cancel(stopping || estop || mode!=3 || !run_session || fault!=0),
   .balance_active(mode==3 && balance_permission),.encoder_raw(enc),
   .command_valid((remote_motion || gesture_motion_valid) && !stopping && !estop),
   .command(remote_motion ? remote_motion_command:gesture_motion_command),
   .action_kind(2'd0),.action_index(3'd0),.request_id(last_mutation),
   .cfg_write(cfg_write && cfg_group==251),.cfg_id(cfg_id),.cfg_value(cfg_value),
   .cfg_ok(planner_cfg_ok),.cfg_error(planner_cfg_error),.cfg_revision(planner_cfg_revision),.cfg_read_value(planner_cfg_read_value),
   .origin_wide(motion_origin),.final_wide(motion_final),.last_request_id(planner_request),.last_action(planner_action),
   .planner_phase(planner_phase),.velocity_q(nominal_velocity_q),.acceleration_q(nominal_acceleration_q),
   .controller_velocity_q(planner_velocity_q),.controller_acceleration_q(planner_acceleration_q),
   .velocity_valid(motion_velocity_valid),.left_positive(left_positive),.cw_positive(cw_positive),
   .enc_wide_physical(wide_physical),.encoder_wide(wide_encoder),.target_wide(wide_target),
   .encoder_local(motion_encoder),.target_local(motion_target),.active(motion_active),.motion_guard_active(motion_guard_active),.state(motion_state),
   .direction(motion_direction),.fault(motion_fault),.accepted(motion_accepted),.rejected(motion_rejected),
   .reason(motion_reason),.event_count(motion_events),.kit_ip570(kit_ip570),
   .trajectory_done(trajectory_done),.tracking_settled(tracking_settled),.measured_velocity_cps(measured_velocity_cps),
   .reference_velocity_cps(reference_velocity_cps),.tracking_error(tracking_error),
   .settle_ticks(settle_ticks),.stop_reason(motion_stop_reason));
  assign control_encoder=motion_session ? motion_encoder:mapped_encoder;
  assign mapping_fault=motion_session ? motion_fault:original_mapping_fault;
 end else begin:no_motion
  assign planner_cfg_read_value=32'hffffffff;assign planner_cfg_revision=0;assign planner_cfg_ok=0;assign planner_cfg_error=0;
  assign motion_origin=0;assign motion_final=0;assign planner_request=0;assign planner_action=0;assign planner_phase=0;
  assign planner_velocity_q=0;assign planner_acceleration_q=0;assign nominal_velocity_q=0;assign nominal_acceleration_q=0;assign motion_velocity_valid=0;
  assign wide_physical={{32{encoder_relative[31]}},encoder_relative};
  assign wide_encoder=$signed(mapped_encoder)-64'sd10000;assign wide_target=0;
  assign motion_encoder=mapped_encoder;assign motion_target=10000;
  assign motion_active=0;assign motion_state=0;assign motion_direction=0;assign motion_fault=0;
  assign motion_guard_active=0;
  assign trajectory_done=0;assign tracking_settled=0;assign measured_velocity_cps=0;assign reference_velocity_cps=0;
  assign tracking_error=0;assign settle_ticks=0;assign motion_stop_reason=0;
  assign motion_accepted=0;assign motion_rejected=0;assign motion_reason=0;assign motion_events=0;
  assign control_encoder=mapped_encoder;assign mapping_fault=original_mapping_fault;
 end endgenerate
 wire raw_source_mean_bad=raw_guard_mean<2285 || raw_guard_mean>3885;
 wire source_window_bad;
 wire source_mean_combined_bad=source_window_bad;
 // V1's private safety input is only range-tested, never used as an ADC value.
 // Canonical range coding removes a redundant value mux without delaying faults.
 wire [11:0] source_guard_mean=VARIANT==1 ? (source_mean_combined_bad ? 12'd0:12'd3085) :
  (raw_source_mean_bad ? raw_guard_mean:angle);
 forest_d1_safety #(.ALLOW_SWING(FEATURE_SWING),.ZERO_SAMPLES(ZERO_SAMPLES),.HANDOFF_ENTER_Q4(SWING_HANDOFF_ENTER_Q4),.USE_SPLIT_MOTOR_INVALIDATE(1)) safety(
  .clk(clk_50m),.rst_n(control_rst_n),.tick_1ms(tick_1ms),.kit_ip570(kit_ip570),.invalidate_calibration(kit_changed),.motor_invalidate_calibration(kit_changed_motor),.zero_pulse(key_pulse[0] || remote_zero),
  .run_pulse(physical_run || remote_run),.stop_pulse(stopping),.clear_pulse(key_pulse[2] || remote_clear),.estop_raw(estop),
  .start_swing_pulse(swing_request),.hold_pulse(hold_pulse),.handoff_pulse(handoff_pulse && handoff_capture_window),.swing_failed(parent_swing_failed),
  .adc_raw(raw),.adc_q4(filtered),.adc_otr_qualified(otr_qualified),.encoder_count(enc),.invalid_count(invalid),
  .arithmetic_fault(zero_valid && ((mapping_valid && mapping_fault) || compare_deadline_fault || fast_acquisition_fault || acquisition_fault)),
  .source_sample_en(sample_en),.source_angle_adc12(source_guard_mean),.mode(mode),.fault(fault),.zero_valid(zero_valid),
  .capture_active(capture_active),.zero_q4(zero_q4),.encoder_zero(encoder_zero),.run_session(run_session),.run_session_controls(run_session_controls),
  .motor_permitted(balance_permission),.swing_permitted(swing_permission));
 forest_d1_window_sampler #(.SEPARATE_RAW_GUARD(1)) sampler(.clk(clk_50m),.rst_n(source_rst_n),
  .angle_in(mapped_angle),.angle_valid(angle_valid),.raw_angle_in(raw_mapped_angle),.raw_angle_valid(raw_angle_valid),
  .encoder_in(control_encoder),.angle_capture(angle_capture),.frame_start(frame_start),.sample_en(sample_en),
  .angle_adc12(angle),.encoder_sample(encoder_sample),.frame_number(frames),.acquisition_fault(acquisition_fault),.raw_guard_mean(raw_guard_mean),.source_window_bad(source_window_bad));
 reg active_adaptive_used;reg [31:0] active_feature_epoch;
 reg [191:0] active_gains;reg [31:0] active_revision_reg;reg first_epoch;
 assign active_revision=active_revision_reg;
 wire [191:0] adaptive_target;wire [7:0] freeze_reason;
 wire signed [15:0] center_p,center_d,fast_p,fast_d;
 wire [31:0] learning_revision,candidate_epoch,candidate_revision;
 wire commit_bank=first_epoch || position_div==4;
 wire adaptive_candidate=FEATURE_ADAPTIVE && adaptation_active && candidate_revision==stored_revision && !manual_write;
 wire [191:0] desired_gains=adaptive_candidate ? adaptive_target:stored_gains;
 wire [191:0] core_gains=commit_bank ? desired_gains:active_gains;
 wire [31:0] core_revision=commit_bank ? stored_revision:active_revision;
 wire adaptive_commit=sample_en && commit_bank && adaptive_candidate;
 always @(posedge clk_50m or negedge source_rst_n)
  if(!source_rst_n)begin active_gains<=DEFAULT_GAINS;active_revision_reg<=0;first_epoch<=1;active_adaptive_used<=0;active_feature_epoch<=0;end
  else if(sample_en)begin
   first_epoch<=0;
   if(commit_bank)begin active_gains<=desired_gains;active_revision_reg<=stored_revision;active_adaptive_used<=adaptive_candidate;active_feature_epoch<=adaptive_candidate ? candidate_epoch:0;end
  end
 wire [31:0] bias_bits,position_bias,position_diff,angle_difference;
 wire [31:0] angle_p,angle_i,angle_d,position_p,position_i,position_d;
 // Original core produces completed D at5/25ms; fast P computes at1/5ms.
 wire fast_request=adc_batch_valid && source_rst_n;
 wire [11:0] fast_mapped_angle;wire signed [15:0] unused_fast_raw_encoder;
 wire fast_mapping_fault,fast_mapping_valid;
 reg [3:0] expected_fast_valid;
 reg signed [15:0] fast_control_encoder;
 wire observation_start;wire [11:0] observation_angle;
 wire signed [15:0] observation_encoder,observation_delta;
 wire [191:0] observation_gains;wire [31:0] observation_revision;
 reg [2:0] publication_div;reg captured_publish,captured_control_fast;
 reg [31:0] captured_policy_epoch;
 always @(posedge clk_50m or negedge source_rst_n)
  if(!source_rst_n)begin expected_fast_valid<=0;fast_acquisition_fault<=0;fast_control_encoder<=10000;end
  else begin
   expected_fast_valid<={expected_fast_valid[2:0],fast_request};
   if(fast_request)fast_control_encoder<=control_encoder;
   if(fast_mapping_valid!=expected_fast_valid[3] || (!motion_session && fast_mapping_fault))fast_acquisition_fault<=1;
  end
 forest_d1_run_inputs #(.USE_Q4_INPUT(1)) fast_inputs(
  .clk(clk_50m),.rst_n(control_rst_n),.angle_epoch_valid(source_rst_n),.angle_capture(fast_request),
  .kit_ip570(kit_ip570),.adc_raw(raw),.adc_q4(filtered),.zero_q4(zero_q4),.encoder_count(enc),.encoder_zero(encoder_zero),
  .angle_adc12(fast_mapped_angle),.encoder_forest(unused_fast_raw_encoder),.encoder_range_fault(fast_mapping_fault),.angle_valid(fast_mapping_valid));
 always @(posedge clk_50m or negedge source_rst_n)
  if(!source_rst_n)begin publication_div<=0;captured_publish<=0;captured_control_fast<=0;captured_policy_epoch<=0;end
  else if(observation_start)begin
   captured_publish<=!control_fast_p || publication_div==0;captured_control_fast<=control_fast_p;
   captured_policy_epoch<=frames;
   if(control_fast_p)publication_div<=publication_div==4 ? 0:publication_div+1'b1;else publication_div<=0;
  end
 forest_d1_p_compare core(.clk(clk_50m),.rst_n(source_rst_n),.cfg_rst_n(control_rst_n),.sample_en(sample_en),
  .fast_sample_en(fast_mapping_valid && !fast_acquisition_fault),.run_session(mode==3 && actual_balance_permission),
  .requested_fast(1'b1),.active_fast(control_fast_p),.ready(compare_ready),.deadline_fault(compare_deadline_fault),
  .sample_epoch(uptime_ms),.gains(core_gains),.gain_revision(core_revision),
  .integrator_clear(mode!=3 || !balance_permission),.prime_history(prime_pending),.angle_adc12(angle),.encoder(encoder_sample),
  .fast_angle(fast_mapped_angle),.fast_encoder(fast_control_encoder),
  .observation_start(observation_start),.observation_angle(observation_angle),.observation_encoder(observation_encoder),
  .observation_delta(observation_delta),.observation_gains(observation_gains),.observation_revision(observation_revision),
  .angle_d_epoch(control_angle_d_epoch),.position_d_epoch(control_position_d_epoch),.position_p_epoch(control_position_p_epoch),
  .done(done),.moto(moto),.balance_pwm(balance_pwm),.position_pwm(position_pwm),.target(target),
  .position_bias_bits(position_bias),.position_diff_bits(position_diff),.balance_bias_bits(bias_bits),.angle_diff_bits(angle_difference),
  .angle_p_bits(angle_p),.angle_i_bits(angle_i),.angle_d_bits(angle_d),.position_p_bits(position_p),
  .position_i_bits(position_i),.position_d_bits(position_d),.position_div(position_div),.fast_position_div(fast_position_div),.busy(busy),.unused_terminal());
 wire balance_bn1,balance_bn2,balance_pwm_pin;wire [31:0] balance_diag;
 forest_d1_run_actuator actuator(.clk(clk_50m),.rst_n(control_rst_n),.motor_permitted(actual_balance_permission),
  .core_moto(moto),.BN1(balance_bn1),.BN2(balance_bn2),.PWMB(balance_pwm_pin),.duty_actual_q15(),.diag(balance_diag));
 assign AN1=0;assign AN2=0;assign PWMA=0;
 wire return_bn1,return_bn2,return_pwm;wire [31:0] return_diag;
 j280_pwm_motor_safe #(.PWM_PERIOD(1250),.COMMAND_PERIODS(4),.SOFT_COMMAND_RISE(1),.DIAGNOSTIC_SWITCHES(1)) return_driver(
  .clk_50m(clk_50m),.rst_n(control_rst_n),.motor_enable(auto_return_active && swing_permission && !stopping && !estop),
  .duty_q15(auto_return_duty),.soft_rise_enable(1'b1),.AN1(return_bn1),.AN2(return_bn2),.PWMA(return_pwm),.diagnostic_applied(return_diag));
 // Both drivers already gate their pins immediately; mode3/mode5 are exclusive.
 assign BN1=swing_bn1 | balance_bn1 | return_bn1;
 assign BN2=swing_bn2 | balance_bn2 | return_bn2;
 assign PWMB=swing_pwm | balance_pwm_pin | return_pwm;
 assign actuator_diag=mode==5 ? (auto_preparing ? return_diag:swing_diag):balance_diag;
 // Calculate each signed diagnostic before the late mode selection. This is
 // the same combinational value at the same clock edge; no actuator/ADC/PID
 // input, state, sample period or protection predicate changes.
 function signed [15:0] diagnostic_actual_q15;
  input [31:0] value;
  begin diagnostic_actual_q15=(!value[16] || value[18]) ? 16'sd0:
   value[17] ? -$signed(value[15:0]):$signed(value[15:0]);end
 endfunction
 wire signed [15:0] actual_balance_q15 /* synthesis syn_keep=1 */;
 wire signed [15:0] actual_swing_q15 /* synthesis syn_keep=1 */;
 wire signed [15:0] actual_return_q15 /* synthesis syn_keep=1 */;
 assign actual_balance_q15=diagnostic_actual_q15(balance_diag);
 assign actual_swing_q15=diagnostic_actual_q15(swing_diag);
 assign actual_return_q15=diagnostic_actual_q15(return_diag);
 wire signed [15:0] actual_q15=mode==5 ?
  (auto_preparing ? actual_return_q15:actual_swing_q15):actual_balance_q15;
 reg captured_adaptive_used,captured_adaptive_enabled;reg [7:0] captured_freeze;
 reg [31:0] captured_feature_epoch;
 reg [31:0] captured_ms,captured_epoch,captured_revision,captured_stored_revision,captured_adaptive_epoch;
 reg [15:0] captured_raw,captured_zero,captured_q4,captured_angle,captured_guard;
 reg signed [15:0] captured_encoder,captured_error,captured_difference,previous_error;
 reg [31:0] frame_encoder_raw,captured_encoder_raw;
 reg signed [63:0] aligned_wide_encoder,aligned_wide_target,aligned_wide_physical;
 reg signed [63:0] aligned_motion_final,aligned_motion_origin,frame_motion_final,frame_motion_origin,captured_motion_final,captured_motion_origin;
 reg signed [31:0] aligned_reference_velocity,aligned_measured_velocity,aligned_velocity_q,aligned_acceleration_q;
 reg signed [31:0] frame_reference_velocity,frame_measured_velocity,frame_velocity_q,frame_acceleration_q;
 reg signed [31:0] captured_reference_velocity,captured_measured_velocity,captured_velocity_q,captured_acceleration_q;
 reg [2:0] aligned_motion_state,frame_motion_state,captured_motion_state;
 reg signed [1:0] aligned_motion_direction,frame_motion_direction,captured_motion_direction;
 reg [3:0] aligned_planner_phase,frame_planner_phase,captured_planner_phase;
 reg [7:0] aligned_stop_reason,frame_stop_reason,captured_stop_reason,aligned_settle_ticks,frame_settle_ticks,captured_settle_ticks;
 reg [3:0] aligned_motion_flags,frame_motion_flags,captured_motion_flags;
 reg aligned_velocity_valid,frame_velocity_valid,captured_velocity_valid;
 reg signed [15:0] captured_local_target,captured_actual_at_launch;
 reg aligned_motion_session,frame_motion_session,captured_motion_session;
 reg [7:0] aligned_planner_action,frame_planner_action,captured_planner_action;
 reg [7:0] aligned_motion_reason,frame_motion_reason,captured_motion_reason;
 reg [7:0] aligned_last_gesture,frame_last_gesture,captured_last_gesture;
 reg [7:0] aligned_action_reason,frame_action_reason,captured_action_reason;
 reg [31:0] aligned_motion_events,frame_motion_events,captured_motion_events;
 reg [31:0] aligned_planner_request,frame_planner_request,captured_planner_request;
 reg signed [15:0] adaptive_actual_q15;reg signed [31:0] adaptive_requested_moto;reg adaptive_saturating;
 wire feature_valid,feature_freeze;wire [7:0] feature_reason;wire [31:0] feature_epoch;
 wire signed [15:0] feature_error,feature_difference;wire signed [31:0] feature_position;
 wire signed [31:0] captured_tracking_error=$signed(captured_encoder)-$signed(captured_local_target);
 always @(posedge clk_50m or negedge control_rst_n)
  if(!control_rst_n)begin aligned_wide_encoder<=0;aligned_wide_target<=0;aligned_wide_physical<=0;end
  else begin aligned_wide_encoder<=wide_encoder;aligned_wide_target<=wide_target;aligned_wide_physical<=wide_physical;end
 always @(posedge clk_50m or negedge control_rst_n)begin
  if(!control_rst_n)begin
   aligned_motion_session<=0;aligned_planner_action<=0;aligned_motion_reason<=0;
   aligned_last_gesture<=0;aligned_action_reason<=0;aligned_motion_events<=0;aligned_planner_request<=0;
   aligned_motion_final<=0;aligned_motion_origin<=0;aligned_reference_velocity<=0;aligned_measured_velocity<=0;
   aligned_velocity_q<=0;aligned_acceleration_q<=0;aligned_motion_state<=0;aligned_motion_direction<=0;
   aligned_planner_phase<=0;aligned_stop_reason<=0;aligned_settle_ticks<=0;aligned_motion_flags<=0;aligned_velocity_valid<=0;
  end else begin
   aligned_motion_session<=motion_session;aligned_planner_action<=planner_action;aligned_motion_reason<=motion_reason;
   aligned_last_gesture<=last_gesture;aligned_action_reason<=last_action_reason;
   aligned_motion_events<=motion_events;aligned_planner_request<=planner_request;
   aligned_motion_final<=motion_final;aligned_motion_origin<=motion_origin;
   aligned_reference_velocity<=reference_velocity_cps;aligned_measured_velocity<=measured_velocity_cps;
   aligned_velocity_q<=planner_velocity_q;aligned_acceleration_q<=planner_acceleration_q;
   aligned_motion_state<=motion_state;aligned_motion_direction<=motion_direction;aligned_planner_phase<=planner_phase;
   aligned_stop_reason<=motion_stop_reason;aligned_settle_ticks<=settle_ticks;
   aligned_motion_flags<={motion_fault,tracking_settled,trajectory_done,motion_active};aligned_velocity_valid<=motion_velocity_valid;
  end
 end
 always @(posedge clk_50m or negedge source_rst_n)begin
  if(!source_rst_n)begin
   frame_motion_session<=0;frame_planner_action<=0;frame_motion_reason<=0;frame_last_gesture<=0;frame_action_reason<=0;
   frame_motion_events<=0;frame_planner_request<=0;captured_motion_session<=0;captured_planner_action<=0;
   captured_motion_reason<=0;captured_last_gesture<=0;captured_action_reason<=0;captured_motion_events<=0;
   captured_planner_request<=0;captured_actual_at_launch<=0;
   frame_motion_final<=0;frame_motion_origin<=0;frame_reference_velocity<=0;frame_measured_velocity<=0;
   frame_velocity_q<=0;frame_acceleration_q<=0;frame_motion_state<=0;frame_motion_direction<=0;frame_planner_phase<=0;
   frame_stop_reason<=0;frame_settle_ticks<=0;frame_motion_flags<=0;frame_velocity_valid<=0;
   captured_motion_final<=0;captured_motion_origin<=0;captured_reference_velocity<=0;captured_measured_velocity<=0;
   captured_velocity_q<=0;captured_acceleration_q<=0;captured_motion_state<=0;captured_motion_direction<=0;captured_planner_phase<=0;
   captured_stop_reason<=0;captured_settle_ticks<=0;captured_motion_flags<=0;captured_velocity_valid<=0;captured_local_target<=10000;
   adaptive_actual_q15<=0;adaptive_requested_moto<=0;adaptive_saturating<=0;
  end else begin
   if(control_fast_p ? fast_request:frame_start)begin
    frame_motion_session<=aligned_motion_session;frame_planner_action<=aligned_planner_action;frame_motion_reason<=aligned_motion_reason;
    frame_last_gesture<=aligned_last_gesture;frame_action_reason<=aligned_action_reason;
    frame_motion_events<=aligned_motion_events;frame_planner_request<=aligned_planner_request;
    frame_motion_final<=aligned_motion_final;frame_motion_origin<=aligned_motion_origin;
    frame_reference_velocity<=aligned_reference_velocity;frame_measured_velocity<=aligned_measured_velocity;
    frame_velocity_q<=aligned_velocity_q;frame_acceleration_q<=aligned_acceleration_q;
    frame_motion_state<=aligned_motion_state;frame_motion_direction<=aligned_motion_direction;frame_planner_phase<=aligned_planner_phase;
    frame_stop_reason<=aligned_stop_reason;frame_settle_ticks<=aligned_settle_ticks;frame_motion_flags<=aligned_motion_flags;
    frame_velocity_valid<=aligned_velocity_valid;
   end
   if(observation_start)begin
    captured_motion_session<=frame_motion_session;captured_planner_action<=frame_planner_action;captured_motion_reason<=frame_motion_reason;
    captured_last_gesture<=frame_last_gesture;captured_action_reason<=frame_action_reason;
    captured_motion_events<=frame_motion_events;captured_planner_request<=frame_planner_request;captured_actual_at_launch<=actual_q15;
    captured_motion_final<=frame_motion_final;captured_motion_origin<=frame_motion_origin;
    captured_reference_velocity<=frame_reference_velocity;captured_measured_velocity<=frame_measured_velocity;
    captured_velocity_q<=frame_velocity_q;captured_acceleration_q<=frame_acceleration_q;
    captured_motion_state<=frame_motion_state;captured_motion_direction<=frame_motion_direction;captured_planner_phase<=frame_planner_phase;
    captured_stop_reason<=frame_stop_reason;captured_settle_ticks<=frame_settle_ticks;captured_motion_flags<=frame_motion_flags;
    captured_velocity_valid<=frame_velocity_valid;captured_local_target<=target[15:0];
   end
   if(done && captured_publish)begin adaptive_actual_q15<=actual_q15;adaptive_requested_moto<=moto;adaptive_saturating<=moto>=6900 || moto<=-6900;end
  end
 end
 reg signed [63:0] frame_wide_encoder,frame_wide_target,frame_wide_physical;
 reg signed [63:0] captured_wide_encoder,captured_wide_target,captured_wide_physical;
 reg [191:0] captured_gains,captured_base,captured_target_gains;
 reg captured_position_update;reg [31:0] position_component_epoch;
 reg signed [15:0] captured_center_p,captured_center_d,captured_fast_p,captured_fast_d;
 reg [31:0] captured_learning_revision,captured_candidate_epoch;
 assign snapshot_epoch=captured_epoch;
 always @(posedge clk_50m or negedge source_rst_n)begin
  if(!source_rst_n)begin
   captured_adaptive_used<=0;captured_adaptive_enabled<=0;captured_freeze<=0;captured_feature_epoch<=0;
   captured_ms<=0;captured_epoch<=0;captured_revision<=0;captured_stored_revision<=0;captured_adaptive_epoch<=0;
   captured_raw<=0;captured_zero<=0;captured_q4<=0;captured_angle<=3085;captured_guard<=3085;
   captured_encoder<=10000;captured_error<=0;captured_difference<=0;previous_error<=0;
   frame_encoder_raw<=0;captured_encoder_raw<=0;frame_wide_encoder<=0;frame_wide_target<=0;frame_wide_physical<=0;
   captured_wide_encoder<=0;captured_wide_target<=0;captured_wide_physical<=0;
   captured_gains<=DEFAULT_GAINS;captured_base<=DEFAULT_GAINS;captured_target_gains<=DEFAULT_GAINS;
   captured_position_update<=0;position_component_epoch<=0;captured_center_p<=0;captured_center_d<=0;
   captured_fast_p<=0;captured_fast_d<=0;captured_learning_revision<=0;captured_candidate_epoch<=0;
  end else begin
   if(control_fast_p ? fast_request:frame_start)begin
    frame_encoder_raw<=enc;
    frame_wide_encoder<=FEATURE_MOTION ? aligned_wide_encoder:wide_encoder;
    frame_wide_target<=FEATURE_MOTION ? aligned_wide_target:wide_target;
    frame_wide_physical<=FEATURE_MOTION ? aligned_wide_physical:wide_physical;
   end
   if(observation_start)begin
    captured_adaptive_used<=commit_bank ? adaptive_candidate:active_adaptive_used;
    captured_feature_epoch<=commit_bank ? (adaptive_candidate ? candidate_epoch:0):active_feature_epoch;
    captured_adaptive_enabled<=adaptation_enabled;captured_freeze<=freeze_reason;
    captured_ms<=uptime_ms;captured_epoch<=uptime_ms;captured_revision<=observation_revision;captured_stored_revision<=stored_revision;
    captured_adaptive_epoch<=adaptive_epoch+(adaptive_commit ? 32'd1:32'd0);
    captured_raw<={6'd0,raw};captured_zero<={2'd0,zero_q4};captured_q4<={2'd0,filtered};
    captured_angle<={4'd0,observation_angle};captured_guard<={4'd0,raw_guard_mean};captured_encoder<=observation_encoder;
    captured_error<=$signed({1'b0,observation_angle})-16'sd3085;
    captured_difference<=observation_delta;
    previous_error<=$signed({1'b0,angle})-16'sd3085;
    captured_encoder_raw<=frame_encoder_raw;captured_wide_encoder<=frame_wide_encoder;
    captured_wide_target<=frame_wide_target;captured_wide_physical<=frame_wide_physical;
    captured_gains<=observation_gains;captured_base<=stored_gains;captured_target_gains<=adaptive_target;
    captured_position_update<=control_fast_p ? fast_position_div==4:position_div==4;captured_center_p<=center_p;captured_center_d<=center_d;
    captured_fast_p<=fast_p;captured_fast_d<=fast_d;captured_learning_revision<=learning_revision;
    captured_candidate_epoch<=candidate_epoch;
   end
   if(done)position_component_epoch<=control_position_p_epoch;
  end
 end
 wire learning_pulse;wire [31:0] learning_seq,learning_event_epoch,diagnostic_reference_energy,diagnostic_window_energy;
 wire [7:0] learning_code,learning_reason;wire signed [7:0] diagnostic_center_before,diagnostic_center_after;
 wire [15:0] diagnostic_reference_peak,diagnostic_window_peak;wire [1:0] diagnostic_phase;
 wire [5:0] diagnostic_window_samples;wire [3:0] diagnostic_cooldown;wire [6:0] diagnostic_event_samples;
 reg numerical_calibrated,numerical_history_clear;
 always @(posedge clk_50m or negedge control_rst_n)begin
  if(!control_rst_n)begin numerical_calibrated<=0;numerical_history_clear<=1;end
  else begin
   numerical_calibrated<=zero_valid && !capture_active;
   numerical_history_clear<=mode!=3 || !actual_balance_permission || prime_pending || hold_pulse || manual_write || motion_accepted;
  end
 end
 generate if(FEATURE_ADAPTIVE)begin:adaptive_hardware
  forest_d1_adaptive_features features(.clk(clk_50m),.rst_n(control_rst_n && numerical_calibrated),
   .control_done(done && source_rst_n && captured_publish),
   .history_clear(numerical_history_clear),
   .transaction_epoch(captured_policy_epoch),.angle_adc12(captured_angle[11:0]),
   .sampled_encoder_local(captured_encoder),.transaction_target_local(captured_local_target),
   .reference_velocity_cps(captured_reference_velocity),.reference_acceleration_q(captured_acceleration_q),
   .motion_braking(captured_motion_state==3),.feature_valid(feature_valid),.feature_epoch(feature_epoch),
   .angle_error(feature_error),.angle_diff(feature_difference),.position_error(feature_position),
   .evidence_freeze(feature_freeze),.evidence_reason(feature_reason));
  forest_d1_adaptive_motion adaptive(.clk(clk_50m),.rst_n(control_rst_n && numerical_calibrated),
   .sample_valid(motion_session ? feature_valid:(done && source_rst_n && captured_publish)),.commit_tick(adaptive_commit),.run_balance(mode==3 && actual_balance_permission),
   .stop(stopping || estop || !run_session),.manual_write(manual_write),.enable_write(adapt_enable_write),.enable_value(adapt_enable_value),
   .gains_base(stored_gains),.manual_revision(stored_revision),.sample_epoch(motion_session ? feature_epoch:captured_policy_epoch),
   .angle_error(motion_session ? feature_error:captured_error),.angle_diff(motion_session ? feature_difference:captured_difference),
   .position_error(motion_session ? feature_position:($signed(captured_encoder)-32'sd10000)),.requested_moto(motion_session ? adaptive_requested_moto:moto),.actual_q15(motion_session ? adaptive_actual_q15:actual_q15),
   .saturating(motion_session ? adaptive_saturating:(moto>=6900 || moto<=-6900)),.fault(fault!=0),.evidence_freeze(motion_session && feature_freeze),.evidence_reason(feature_reason),.target_gains(adaptive_target),
   .enabled(adaptation_enabled),.active(adaptation_active),.freeze_reason(freeze_reason),
   .center_p_q8(center_p),.center_d_q8(center_d),.fast_p_q8(fast_p),.fast_d_q8(fast_d),
   .learn_revision(learning_revision),.adapt_epoch(adaptive_epoch),.candidate_epoch(candidate_epoch),.candidate_revision(candidate_revision),
   .diagnostic_pulse(learning_pulse),.diagnostic_seq(learning_seq),.diagnostic_epoch(learning_event_epoch),
   .diagnostic_code(learning_code),.diagnostic_reason(learning_reason),
   .diagnostic_center_before(diagnostic_center_before),.diagnostic_center_after(diagnostic_center_after),
   .diagnostic_reference_energy(diagnostic_reference_energy),.diagnostic_window_energy(diagnostic_window_energy),
   .diagnostic_reference_peak(diagnostic_reference_peak),.diagnostic_window_peak(diagnostic_window_peak),
   .diagnostic_phase(diagnostic_phase),.diagnostic_window_samples(diagnostic_window_samples),.diagnostic_cooldown(diagnostic_cooldown),
   .diagnostic_event_samples(diagnostic_event_samples));
 end else begin:no_adaptive
  assign feature_valid=0;assign feature_freeze=0;assign feature_reason=0;assign feature_epoch=0;
  assign feature_error=0;assign feature_difference=0;assign feature_position=0;
  assign learning_pulse=0;assign learning_seq=0;assign learning_event_epoch=0;assign learning_code=0;assign learning_reason=0;
  assign diagnostic_center_before=0;assign diagnostic_center_after=0;assign diagnostic_reference_energy=0;assign diagnostic_window_energy=0;
  assign diagnostic_reference_peak=0;assign diagnostic_window_peak=0;assign diagnostic_phase=0;
  assign diagnostic_window_samples=0;assign diagnostic_cooldown=0;assign diagnostic_event_samples=0;
  assign adaptive_target=stored_gains;assign adaptation_enabled=0;assign adaptation_active=0;assign freeze_reason=0;
  assign center_p=0;assign center_d=0;assign fast_p=0;assign fast_d=0;assign learning_revision=0;
  assign adaptive_epoch=0;assign candidate_epoch=0;assign candidate_revision=stored_revision;
 end endgenerate
 reg [31:0] packet_sequence;reg [6:0] status_ms;
 wire publish_control=source_rst_n && done && captured_publish;
 wire publish_status=tick_1ms && status_ms==99 && !source_rst_n;
 always @(posedge clk_50m or negedge control_rst_n)begin
  if(!control_rst_n)status_ms<=0;
  else if(tick_1ms)status_ms<=status_ms==99 ? 0:status_ms+1'b1;
 end
 wire [7:0] flags={rx_errors!=0,(publish_control ? captured_adaptive_used:adaptation_active),
  (publish_control ? captured_adaptive_enabled:adaptation_enabled),zero_valid,capture_active,
  actual_balance_permission || swing_permission,run_session,publish_control};
 wire extension_send;
 reg extension_pending;reg [5:0] extension_ms;reg [31:0] extension_sequence;
 reg [2047:0] extended_packet;
 always @(posedge clk_50m or negedge control_rst_n)begin
  if(!control_rst_n)begin extension_pending<=1;extension_ms<=25;extension_sequence<=0;end
  else begin
   if(extension_send)begin extension_pending<=0;extension_sequence<=extension_sequence+1'b1;end
   if(tick_1ms)begin
    if(extension_ms==49)begin extension_ms<=0;extension_pending<=1;end
    else extension_ms<=extension_ms+1'b1;
   end
  end
 end
 always @*begin
  extended_packet=0;
  extended_packet[31:0]=32'h45324446;extended_packet[39:32]=PROTOCOL_VERSION;extended_packet[47:40]=PROTOCOL_VARIANT;
  extended_packet[55:48]=flags;extended_packet[63:56]={5'd0,mode};
  extended_packet[95:64]=extension_sequence;extended_packet[127:96]=uptime_ms;
  extended_packet[159:128]=SCHEMA;extended_packet[191:160]=FIRMWARE_ID;extended_packet[223:192]=CAPABILITIES;
  extended_packet[255:224]=captured_epoch;extended_packet[287:256]=captured_ms;
  extended_packet[319:288]={25'd0,captured_adaptive_used,adaptation_active,adaptation_enabled,captured_velocity_valid,feature_freeze,captured_position_update,captured_motion_session};
  extended_packet[383:320]=captured_wide_physical;extended_packet[447:384]=captured_motion_origin;
  extended_packet[511:448]=captured_motion_final;extended_packet[575:512]=captured_wide_encoder;
  extended_packet[639:576]=captured_wide_target;extended_packet[703:640]={{32{captured_tracking_error[31]}},captured_tracking_error};
  extended_packet[735:704]=captured_reference_velocity;extended_packet[767:736]=captured_measured_velocity;
  extended_packet[799:768]=captured_velocity_q;extended_packet[831:800]=captured_acceleration_q;
  extended_packet[839:832]={5'd0,captured_motion_state};extended_packet[847:840]={{6{captured_motion_direction[1]}},captured_motion_direction};
  extended_packet[855:848]={4'd0,captured_planner_phase};extended_packet[863:856]=captured_stop_reason;
  extended_packet[871:864]=captured_settle_ticks;extended_packet[879:872]={4'd0,captured_motion_flags};
  extended_packet[887:880]=captured_planner_action;extended_packet[895:888]=captured_motion_reason;
  extended_packet[927:896]=captured_motion_events;extended_packet[959:928]=captured_planner_request;
  extended_packet[991:960]=gesture_sequence;extended_packet[1023:992]=gesture_poll;
  extended_packet[1031:1024]=raw43;extended_packet[1039:1032]=raw44;extended_packet[1047:1040]=decode_reason;
  extended_packet[1055:1048]=gesture_decode_state;extended_packet[1063:1056]={7'd0,gesture_ready};
  extended_packet[1071:1064]={7'd0,gesture_busy};extended_packet[1079:1072]=gesture_error;extended_packet[1087:1080]=gesture_init;
  extended_packet[1103:1088]=gesture_errors;extended_packet[1111:1104]=gesture_id0;extended_packet[1119:1112]=gesture_id1;
  extended_packet[1151:1120]=suppressed_gestures;extended_packet[1183:1152]=feature_epoch;
  extended_packet[1215:1184]=candidate_epoch;extended_packet[1247:1216]=candidate_revision;extended_packet[1279:1248]=adaptive_epoch;
  extended_packet[1287:1280]=freeze_reason;extended_packet[1295:1288]={7'd0,feature_freeze};
  extended_packet[1303:1296]={7'd0,adaptation_enabled};extended_packet[1311:1304]={7'd0,adaptation_active};
  extended_packet[1343:1312]=captured_revision;extended_packet[1375:1344]=stored_revision;extended_packet[1407:1376]=captured_feature_epoch;
  extended_packet[1423:1408]=SWING_HANDOFF_ENTER_Q4;extended_packet[1439:1424]=16'd272;
  extended_packet[1447:1440]=source_capture_flags;extended_packet[1455:1448]=source_reason;
  extended_packet[1463:1456]={2'd0,capture_hold_ms};extended_packet[1471:1464]={6'd0,selected_profile};
  extended_packet[1479:1472]={7'd0,kit_ip570};extended_packet[1487:1480]=CAPABILITIES[7:0];
  extended_packet[1495:1488]=captured_control_fast ? 8'd1:8'd5;extended_packet[1503:1496]=captured_control_fast ? 8'd5:8'd25;extended_packet[1535:1504]=last_mutation;
  extended_packet[1551:1536]={2'd0,zero_q4};extended_packet[1567:1552]=16'd3085;
  extended_packet[1607:1600]=auto_state;extended_packet[1615:1608]=auto_result;
  extended_packet[1623:1616]={7'd0,auto_enabled};extended_packet[1631:1624]={7'd0,auto_reference_valid};
  extended_packet[1663:1632]=auto_error;extended_packet[1695:1664]=auto_anchor;extended_packet[1727:1696]=auto_attempt_seq;
  extended_packet[1743:1728]=auto_still_ms;extended_packet[1759:1744]={2'd0,auto_downward};
  extended_packet[1791:1760]=32'h41465031;
  extended_packet[1807:1792]=captured_control_fast ? 16'd1000:16'd5000;
  extended_packet[1815:1808]=captured_control_fast ? 8'd1:8'd5;extended_packet[1823:1816]=8'd5;
  extended_packet[1831:1824]=captured_control_fast ? 8'd5:8'd25;extended_packet[1839:1832]=8'd25;
  extended_packet[1847:1840]={7'd0,captured_control_fast};extended_packet[1855:1848]=captured_control_fast ? 8'd5:8'd1;
  extended_packet[1887:1856]=control_angle_d_epoch;extended_packet[1919:1888]=control_position_d_epoch;
  extended_packet[1951:1920]=control_position_p_epoch;extended_packet[1983:1952]=captured_policy_epoch;
  extended_packet[2015:1984]=captured_epoch;

 end
 wire diagnostic_pending;wire [2047:0] diagnostic_packet;
 // Sideband packets use idle controller time and leave a guard before the
 // fixed millisecond status boundary. A registered valid occupies one slot.
 wire publish_diag=diagnostic_pending && !publish_control && !publish_status && tlm_ready &&
  !tlm_valid && !busy && millisecond_div<49000;
 assign extension_send=PROTOCOL_VERSION>=3 && extension_pending && !diagnostic_pending && !publish_control && !publish_status &&
  tlm_ready && !tlm_valid && !busy && millisecond_div<49000;
 forest_d1_diagnostics #(.VARIANT(VARIANT),.FIRMWARE_ID(FIRMWARE_ID),
  .PROTOCOL_VERSION(PROTOCOL_VERSION),.PROTOCOL_VARIANT(PROTOCOL_VARIANT),.SCHEMA(SCHEMA),.AUTO_RETURN_CAPABLE(FEATURE_AUTORETURN)) diagnostics(
  .clk(clk_50m),.rst_n(control_rst_n),.tick_1ms(tick_1ms),.sent(publish_diag),.uptime_ms(uptime_ms),
  .kit_ip570(kit_ip570),.kit_changed(kit_changed),.zero_valid(zero_valid),.encoder_raw(enc),.invalid_count(invalid),
  .control_done(done && source_rst_n && captured_publish),.control_gains(captured_gains),.control_epoch(captured_epoch),.control_ms(captured_ms),
  .control_feature_epoch(captured_feature_epoch),.active_revision(captured_revision),.stored_revision(captured_stored_revision),
  .adoption_count(captured_adaptive_epoch),.control_adaptive_used(captured_adaptive_used),.control_freeze(captured_freeze),
  .live_flags(flags),.gesture_map({5'd0,gesture_map}),.left_positive(left_positive),.cw_positive(cw_positive),
  .measured_velocity(measured_velocity_cps),.reference_velocity(reference_velocity_cps),.tracking_error(tracking_error),
  .settle_ticks(settle_ticks),.stop_reason(motion_stop_reason),.trajectory_done(trajectory_done),.tracking_settled(tracking_settled),
  .learning_pulse(learning_pulse),.learning_seq(learning_seq),.learning_epoch(learning_event_epoch),
  .learning_code(learning_code),.learning_reason(learning_reason),.center_before(diagnostic_center_before),.center_after(diagnostic_center_after),
  .reference_energy(diagnostic_reference_energy),.window_energy(diagnostic_window_energy),
  .reference_peak(diagnostic_reference_peak),.window_peak(diagnostic_window_peak),.learning_phase(diagnostic_phase),
  .window_samples(diagnostic_window_samples),.cooldown(diagnostic_cooldown),.event_samples(diagnostic_event_samples),.candidate_epoch(candidate_epoch),.candidate_revision(candidate_revision),
  .swing_finished(swing_finished),.swing_failed(swing_failed || auto_failed),.handoff_started(handoff_started),.handoff_pulse(handoff_pulse),
  .handoff_timeout(handoff_timeout),.source_ready(source_ready),.handoff_wait_ms(handoff_wait_ms),.delta_q4(delta_q4),
  .source_alpha(source_alpha_q20),.source_alpha_rate(source_alpha_rate_q20),.source_arm_rate(source_arm_rate_q20),
  .source_capture_flags(source_capture_flags),.source_reason(source_reason),.capture_hold_ms(capture_hold_ms),
  .auto_state(auto_state),.auto_pairs(auto_preparing ? 8'd0:swing_pair),.auto_result(auto_result),
  .auto_flags({4'd0,auto_return_active,1'b1,auto_enabled,auto_reference_valid}),
  .auto_attempt_seq(auto_attempt_seq),.auto_return_error(auto_error),.auto_anchor(auto_anchor),
  .auto_still_ms(auto_still_ms),.auto_downward(auto_downward),
  .pending(diagnostic_pending),.packet(diagnostic_packet));
 always @(posedge clk_50m or negedge control_rst_n)begin
  if(!control_rst_n)begin packet_sequence<=0;tlm_valid<=0;tlm_data<=0;end
  else begin
   tlm_valid<=0;
   if(publish_control || publish_status || publish_diag || extension_send)begin
    if(!publish_diag && !extension_send)packet_sequence<=packet_sequence+1'b1;tlm_valid<=1;tlm_data<=0;
    if(publish_diag)tlm_data<=diagnostic_packet;else if(extension_send)tlm_data<=extended_packet;else begin
    tlm_data[31:0]<=32'h54324446;tlm_data[39:32]<=PROTOCOL_VERSION;tlm_data[47:40]<=PROTOCOL_VARIANT;
    tlm_data[55:48]<=flags;tlm_data[63:56]<={5'd0,mode};tlm_data[95:64]<=packet_sequence;
    tlm_data[127:96]<=publish_control ? captured_ms:uptime_ms;
    tlm_data[159:128]<=publish_control ? captured_epoch:0;
    tlm_data[191:160]<=publish_control ? captured_revision:active_revision;
    tlm_data[223:192]<=publish_control ? captured_stored_revision:stored_revision;
    tlm_data[255:224]<=publish_control ? captured_adaptive_epoch:adaptive_epoch;
    tlm_data[287:256]<=FIRMWARE_ID;tlm_data[319:288]<=SCHEMA;
    tlm_data[327:320]<=fault;tlm_data[335:328]<=mode==5 ? {6'd0,swing_profile}:{6'd0,selected_profile};
    tlm_data[343:336]<=visible_swing_stage;tlm_data[351:344]<={5'd0,(publish_control ? captured_motion_state:motion_state)};
    tlm_data[359:352]<=publish_control ? captured_last_gesture:last_gesture;tlm_data[367:360]<=publish_control ? captured_action_reason:last_action_reason;tlm_data[375:368]<=publish_control ? captured_freeze:freeze_reason;
    tlm_data[383:376]<=publish_control ? {{6{captured_motion_direction[1]}},captured_motion_direction}:{{6{motion_direction[1]}},motion_direction};
    tlm_data[399:384]<=publish_control ? captured_raw:{6'd0,raw};
    tlm_data[415:400]<=publish_control ? captured_zero:{2'd0,zero_q4};
    tlm_data[431:416]<=publish_control ? captured_q4:{2'd0,filtered};
    tlm_data[447:432]<=publish_control ? captured_angle:16'd0;
    tlm_data[463:448]<=publish_control ? captured_guard:16'd0;
    tlm_data[479:464]<=publish_control ? captured_error:16'd0;
    tlm_data[495:480]<=publish_control ? captured_difference:16'd0;
    tlm_data[511:496]<=publish_control ? captured_actual_at_launch:actual_q15;
    tlm_data[543:512]<=publish_control ? captured_encoder_raw:enc;
    tlm_data[559:544]<=publish_control ? captured_encoder:16'd0;
    tlm_data[575:560]<=publish_control ? captured_local_target:16'd10000;
    tlm_data[639:576]<=publish_control ? captured_wide_encoder:wide_encoder;
    tlm_data[703:640]<=publish_control ? captured_wide_target:wide_target;
    tlm_data[767:704]<=PROTOCOL_VERSION>=3 ? (publish_control ? captured_motion_final:motion_final):(publish_control ? captured_wide_physical:wide_physical);
    tlm_data[799:768]<=publish_control ? captured_tracking_error:32'd0;
    tlm_data[831:800]<=publish_control ? moto:32'd0;
    tlm_data[863:832]<=publish_control ? balance_pwm:32'd0;
    tlm_data[895:864]<=publish_control ? position_pwm:32'd0;
    tlm_data[911:896]<=16'd3085;tlm_data[927:912]<=publish_control ? (captured_control_fast ? 16'd1000:16'd5000):16'd0;
    tlm_data[959:928]<=publish_control ? control_position_p_epoch:32'd0;
    tlm_data[991:960]<=gesture_sequence;tlm_data[1023:992]<=gesture_poll;
    tlm_data[1215:1024]<=publish_control ? captured_base:stored_gains;
    tlm_data[1407:1216]<=publish_control ? captured_gains:active_gains;
    // A status-only frame requires !source_rst_n; adaptation is then ineligible
    // and its public target equals the stored manual bank. Keep the late
    // protection/eligibility cone out of this otherwise redundant live mux.
    tlm_data[1599:1408]<=publish_control ? captured_target_gains:stored_gains;
    tlm_data[1791:1600]<=publish_control ? {position_d,position_i,position_p,angle_d,angle_i,angle_p}:192'd0;
    tlm_data[1823:1792]<=tlm_dropped;tlm_data[1855:1824]<=rx_errors+gateway_errors;
    tlm_data[1887:1856]<=config_revision;tlm_data[1919:1888]<=publish_control ? captured_learning_revision:learning_revision;
    tlm_data[1951:1920]<=publish_control ? captured_candidate_epoch:candidate_epoch;
    tlm_data[1967:1952]<=gesture_errors;tlm_data[1975:1968]<=raw43;tlm_data[1983:1976]<=raw44;
    tlm_data[1999:1984]<=publish_control ? captured_center_p:center_p;
    tlm_data[2015:2000]<=publish_control ? captured_center_d:center_d;
    tlm_data[2023:2016]<=publish_control ? captured_fast_p[7:0]:fast_p[7:0];
    tlm_data[2031:2024]<=publish_control ? captured_fast_d[7:0]:fast_d[7:0];
    end
   end
  end
 end
endmodule
