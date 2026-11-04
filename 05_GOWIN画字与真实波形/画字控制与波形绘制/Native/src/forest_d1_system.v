// One physical owner, one quiet measurement chain and one serial owner.
module forest_d1_system #(
 parameter integer VARIANT=3,STARTUP_CYCLES=100000000,KEY_STABLE_CYCLES=500000,ZERO_SAMPLES=1024,
 parameter integer WATCHDOG_CYCLES=25000000,WIRE_VERSION=5,BLE_TELEMETRY=0,
 parameter [31:0] SCHEMA=32'h46443405,
 parameter [31:0] FIRMWARE_ID=32'h46443233
)(
 input clk_50m,rst_n,input [3:0] key_sw,input uart_rx,output uart_tx,
 input bt_rx,bt_state,output bt_tx,
 output adc_clk,adc_oe_n,input [9:0] adc_data_in,input adc_otr,enc1_a,enc1_b,
 output AN1,AN2,PWMA,BN1,BN2,PWMB,inout gesture_scl,gesture_sda
);
 localparam [191:0] DEFAULT_GAINS={32'h44188000,32'd0,32'h41c80000,32'h437b0000,32'd0,32'h42440000};
 localparam SWING_ENABLED=(VARIANT==1 || VARIANT==2 || VARIANT==4);
 wire kit_ip570,kit_changed;
 wire reset_n;forest_d1_reset resetter(clk_50m,rst_n,reset_n);
 reg [31:0] startup_count;
 wire ready=STARTUP_CYCLES==0 || startup_count>=STARTUP_CYCLES;
 always @(posedge clk_50m or negedge reset_n)
  if(!reset_n)startup_count<=0;else if(!ready)startup_count<=startup_count+1'b1;
 wire control_rst_n=reset_n && ready;
 reg [15:0] millisecond_div;reg [31:0] uptime_ms;
 wire tick_1ms=millisecond_div==49999;
 always @(posedge clk_50m or negedge control_rst_n)
  if(!control_rst_n)begin millisecond_div<=0;uptime_ms<=0;end
  else if(tick_1ms)begin millisecond_div<=0;uptime_ms<=uptime_ms+1'b1;end
  else millisecond_div<=millisecond_div+1'b1;
 wire [9:0] raw,clean;wire [13:0] filtered;
 wire [31:0] actuator_diag,adc_cycle;
 j280_adc_capture_trace #(.DIAGNOSTIC_SWITCHES(1),.QUIET_CACHE_WORDS(1)) adc(
  .clk_50m(clk_50m),.rst_n(reset_n),.adc_data_in(adc_data_in),.average_enable(1'b1),
  .trace_adc_otr(adc_otr),.trace_actuator_diag(actuator_diag),.trace_motor_pins({BN1,BN2,PWMB}),
  .adc_clk(adc_clk),.adc_oe_n(adc_oe_n),.adc_raw(raw),.adc_clean_stage(clean),.adc_filtered_q4(filtered),
  .trace_valid(),.trace_record(),.trace_time_counter(adc_cycle));
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
 wire [2:0] mode;wire [7:0] fault;wire zero_valid,capture_active,run_session;
 wire balance_permission;
 wire swing_permission,source_ready,source_frame_ready;
 wire [13:0] zero_q4;wire signed [31:0] encoder_zero;
 wire [191:0] stored_gains;wire [31:0] stored_revision,last_mutation,config_revision,gateway_errors;
 wire manual_write,adapt_enable_write,adapt_enable_value;
 wire [1:0] selected_profile;wire [2:0] gesture_map;
 wire left_positive,cw_positive,gesture_enabled;
 wire cfg_write;wire [7:0] cfg_group,cfg_id;wire [31:0] cfg_value,cfg_read_value,swing_cfg_revision;
 wire cfg_ok,cfg_error,swing_cfg_ok,swing_cfg_error,planner_cfg_ok,planner_cfg_error;
 wire [31:0] swing_cfg_read,planner_cfg_read,planner_cfg_revision;
 wire [31:0] draw_cfg_read,draw_cfg_revision;
 wire draw_cfg_ok,draw_cfg_error,draw_committed,draw_tracking;
 wire [9:0] draw_length;wire [8:0] draw_address;wire signed [15:0] draw_sample;
 wire planner_upload_ready;
 wire draw_upload_permitted;
 wire motion_active,motion_fault;wire [2:0] motion_state;wire signed [1:0] motion_direction;
 assign cfg_read_value=cfg_group==252 ? draw_cfg_read:cfg_group==253 ? planner_cfg_read:swing_cfg_read;
 assign cfg_ok=swing_cfg_ok || planner_cfg_ok || draw_cfg_ok;
 assign cfg_error=swing_cfg_error || planner_cfg_error || draw_cfg_error;
 wire [1:0] action_kind;wire [2:0] action_index;wire [31:0] motion_request_id;
 wire remote_motion;wire [2:0] remote_motion_command;
 wire motion_accepted,motion_rejected;wire [7:0] motion_reason;
 wire [383:0] command,usb_command,ble_command;wire command_valid,usb_command_valid,ble_command_valid;
 wire [1:0] usb_command_emergency,ble_command_emergency;
 wire [623:0] route_reply;wire usb_reply_valid,ble_reply_valid,ble_reply_ready;
 wire urgent_stop,urgent_estop;wire [31:0] logical_session,last_event,duplicates,route_rejects,ingress_overflow,last_routed_request;
 wire [1:0] last_routed_source;
 reg [1:0] bt_sync;always @(posedge clk_50m or negedge control_rst_n)
  if(!control_rst_n)bt_sync<=0;else bt_sync<={bt_sync[0],bt_state};
 wire [31:0] ble_rx_errors,ble_tlm_dropped,ble_reply_dropped;
 reg ble_tlm_valid;reg [2047:0] ble_tlm_data;reg [8:0] ble_tlm_length;wire ble_tlm_ready;
 reg [8:0] ble_diag_ms;reg ble_diag_due;
 wire [63:0] motion_origin,motion_final;wire [31:0] planner_request;
 wire [7:0] planner_action;wire [3:0] planner_phase;
 wire signed [31:0] planner_velocity_q,planner_acceleration_q;
 wire [623:0] reply;wire reply_valid,reply_ready;
 wire [31:0] rx_errors,tlm_dropped,reply_dropped;
 reg tlm_valid;reg [2047:0] tlm_data;wire tlm_ready,link_busy;
 wire [31:0] active_revision,adaptive_epoch;
 wire [31:0] frames;
 wire adaptation_enabled,adaptation_active;
 wire [7:0] gesture_state;wire gesture_ready,gesture_busy,gesture_event;wire [3:0] gesture_code;
 wire [7:0] gesture_error,gesture_id0,gesture_id1,gesture_init,raw43,raw44,decode_reason;
 wire [15:0] gesture_errors;wire [31:0] gesture_poll,gesture_sequence,suppressed_gestures;
 wire gesture_wave=SWING_ENABLED && gesture_event && gesture_code==9;
 wire physical_run=key_pulse[1] && (source_ready || run_session);
 wire stopping=urgent_stop || remote_stop || watchdog_stop || gesture_wave || (physical_run && run_session);
 wire estop=physical_estop || serial_estop;
 // Staging is data-only. Pose/fault/STOP cannot enable any motor path.
 // Do not overwrite the single committed RAM while a finite job uses it.
 assign draw_upload_permitted=!capture_active && (VARIANT==5 ? planner_upload_ready:!motion_active);
 wire gesture_start=SWING_ENABLED && gesture_event && gesture_code==3 && gesture_ready &&
  !run_session && zero_valid && !capture_active && fault==0 && !estop && !stopping;
 wire swing_request=remote_swing || gesture_start;
 reg [31:0] prior_config_revision;
 always @(posedge clk_50m or negedge control_rst_n)
  if(!control_rst_n)prior_config_revision<=0;else prior_config_revision<=config_revision;
 wire config_changed=config_revision!=prior_config_revision;
 wire [31:0] snapshot_epoch;
 forest_d1_gateway #(.VARIANT(VARIANT),.WIRE_VERSION(WIRE_VERSION),.BLE_TELEMETRY(BLE_TELEMETRY),.WATCHDOG_CYCLES(WATCHDOG_CYCLES),.FIRMWARE_ID(FIRMWARE_ID)) gateway(
  .clk(clk_50m),.rst_n(control_rst_n),.urgent_stop(urgent_stop),.urgent_estop(urgent_estop),.command_valid(command_valid),.command_data(command),
  .reply_valid(reply_valid),.reply_data(reply),.mode(mode),.fault(fault),.zero_valid(zero_valid),
  .capture_active(capture_active),.run_session(run_session),.motor_permitted(balance_permission || swing_permission),
  .physical_estop_active(physical_estop),.source_ready(source_ready),.balance_active(mode==3 && balance_permission),
  .adapt_enabled(adaptation_enabled),.adapt_active(adaptation_active),.physical_start_event(physical_run || gesture_start),
  .active_revision(active_revision),.control_epoch(snapshot_epoch),.adaptive_epoch(adaptive_epoch),
  .stop_pulse(remote_stop),.zero_pulse(remote_zero),.run_pulse(remote_run),.clear_pulse(remote_clear),
  .swing_start_pulse(remote_swing),.serial_estop_latched(serial_estop),.watchdog_stop(watchdog_stop),
  .gains_stored(stored_gains),.gain_revision(stored_revision),.last_mutation_id(last_mutation),
  .manual_write(manual_write),.enable_write(adapt_enable_write),.enable_value(adapt_enable_value),
  .selected_profile(selected_profile),.gesture_map_flags(gesture_map),.left_positive(left_positive),
  .cw_positive(cw_positive),.gesture_enable(gesture_enabled),.kit_ip570(kit_ip570),.kit_changed(kit_changed),.cfg_write(cfg_write),.cfg_group(cfg_group),
  .cfg_id(cfg_id),.cfg_value(cfg_value),.cfg_read_value(cfg_read_value),.external_cfg_revision(swing_cfg_revision+planner_cfg_revision+draw_cfg_revision),.upload_permitted(draw_upload_permitted),
  .cfg_result_valid(cfg_ok || cfg_error),.cfg_result_ok(cfg_ok),
  .motion_valid(remote_motion),.motion_command(remote_motion_command),.action_kind(action_kind),.action_index(action_index),.motion_request_id(motion_request_id),
  .motion_result_valid(motion_accepted || motion_rejected),.motion_result_ok(motion_accepted),
  .config_revision(config_revision),.gateway_errors(gateway_errors));
 forest_command_router #(.VARIANT(VARIANT),.WIRE_VERSION(WIRE_VERSION),.PREDECODED_EMERGENCY(1),.FIRMWARE_ID(FIRMWARE_ID)) router(
  .clk(clk_50m),.rst_n(control_rst_n),.usb_valid(usb_command_valid),.usb_data(usb_command),
  .ble_valid(ble_command_valid),.ble_data(ble_command),.ble_connected(bt_sync[1]),.session_expire(watchdog_stop),
  .usb_emergency_decode(usb_command_emergency),.ble_emergency_decode(ble_command_emergency),
  .gateway_valid(command_valid),.gateway_data(command),.gateway_reply_valid(reply_valid),.gateway_reply(reply),
  .usb_reply_valid(usb_reply_valid),.ble_reply_valid(ble_reply_valid),.reply_data(route_reply),
  .usb_reply_ready(reply_ready),.ble_reply_ready(ble_reply_ready),.urgent_stop(urgent_stop),.urgent_estop(urgent_estop),
  .mode(mode),.fault(fault),.run_session(run_session),.motor_permitted(balance_permission || swing_permission),.capture_active(capture_active),
  .stored_gains(stored_gains),.gain_revision(stored_revision),.mutation_highwater(last_mutation),
  .control_epoch(snapshot_epoch),.active_revision(active_revision),.adaptive_epoch(adaptive_epoch),.config_revision(config_revision),
  .active_session(logical_session),.last_event(last_event),.duplicate_count(duplicates),.rejected_count(route_rejects),
  .ingress_overflow(ingress_overflow),.last_routed_request(last_routed_request),.last_routed_source(last_routed_source));
 forest_d1_link #(.EMERGENCY_WIRE_VERSION(WIRE_VERSION)) link(.clk(clk_50m),.rst_n(control_rst_n),.uart_rx(uart_rx),.uart_tx(uart_tx),
  .command_valid(usb_command_valid),.command_data(usb_command),.command_emergency(usb_command_emergency),.reply_valid(usb_reply_valid),.reply_data(route_reply),.reply_ready(reply_ready),
  .tlm_valid(tlm_valid),.tlm_data(tlm_data),.tlm_length(9'd256),.tlm_ready(tlm_ready),.rx_error_count(rx_errors),
  .tlm_dropped(tlm_dropped),.reply_dropped(reply_dropped),.busy(link_busy));
 generate if(VARIANT>=4)begin:ble_hardware
  forest_d1_link #(.CLKS_PER_BIT(217),.EMERGENCY_WIRE_VERSION(WIRE_VERSION)) ble_link(.clk(clk_50m),.rst_n(control_rst_n),.uart_rx(bt_rx),.uart_tx(bt_tx),
   .command_valid(ble_command_valid),.command_data(ble_command),.command_emergency(ble_command_emergency),.reply_valid(ble_reply_valid),.reply_data(route_reply),.reply_ready(ble_reply_ready),
   .tlm_valid(ble_tlm_valid),.tlm_data(ble_tlm_data),.tlm_length(ble_tlm_length),.tlm_ready(ble_tlm_ready),.rx_error_count(ble_rx_errors),
   .tlm_dropped(ble_tlm_dropped),.reply_dropped(ble_reply_dropped),.busy());
 end else begin:no_ble
  assign bt_tx=1;assign ble_command_valid=0;assign ble_command=0;assign ble_command_emergency=0;assign ble_reply_ready=1;
  assign ble_tlm_ready=0;assign ble_rx_errors=0;assign ble_tlm_dropped=0;assign ble_reply_dropped=0;
 end endgenerate
 generate if(VARIANT==1 || VARIANT==2)begin:gesture_hardware
  forest_d1_gesture_reader reader(.clk(clk_50m),.rst_n(control_rst_n),.restart(!gesture_enabled),
   .flush(stopping || estop || fault!=0 || config_changed),.map_flags(gesture_map),
   .scl(gesture_scl),.sda(gesture_sda),.ready(gesture_ready),.busy(gesture_busy),.error_code(gesture_error),
   .error_count(gesture_errors),.id0(gesture_id0),.id1(gesture_id1),.init_index(gesture_init),
   .raw43(raw43),.raw44(raw44),.poll_seq(gesture_poll),.event_seq(gesture_sequence),
   .event_valid(gesture_event),.event_code(gesture_code),.suppressed_count(suppressed_gestures),.decode_reason(decode_reason),.diagnostic_state(gesture_state));
 end else begin:no_gesture
  assign gesture_scl=1'bz;assign gesture_sda=1'bz;
  assign gesture_state=0;assign gesture_ready=0;assign gesture_busy=0;assign gesture_event=0;assign gesture_code=0;
  assign gesture_error=0;assign gesture_errors=0;assign gesture_id0=0;assign gesture_id1=0;assign gesture_init=0;
  assign raw43=0;assign raw44=0;assign gesture_poll=0;assign gesture_sequence=0;assign suppressed_gestures=0;assign decode_reason=0;
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
 wire swing_active,swing_finished,swing_failed;wire [7:0] swing_stage,swing_pair;
 wire [1:0] swing_profile;wire [31:0] swing_elapsed,swing_diag;
 wire signed [15:0] swing_duty;wire swing_bn1,swing_bn2,swing_pwm;
 reg swing_start_pending,source_start;
 reg handoff_started;reg [4:0] prime_reset_count;reg [8:0] handoff_wait_ms;
 reg hold_pulse,handoff_pulse;reg prime_pending;
 // Fifteen-bit modular subtraction is explicitly folded into the ADC period.
 wire signed [15:0] delta_unwrapped=$signed({2'd0,filtered})-$signed({2'd0,zero_q4});
 wire signed [15:0] delta_q4=delta_unwrapped>8191 ? delta_unwrapped-16'sd16384:
  delta_unwrapped< -8192 ? delta_unwrapped+16'sd16384:delta_unwrapped;
 wire [15:0] delta_abs=delta_q4[15] ? -delta_q4:delta_q4;
 wire handoff_timeout=handoff_started && handoff_wait_ms>=250;
 wire parent_swing_failed=swing_failed || handoff_timeout;
 wire [11:0] mapped_angle,raw_mapped_angle,angle,raw_guard_mean;
 wire signed [15:0] mapped_encoder,encoder_sample,control_encoder;
 wire mapping_fault,angle_capture,angle_valid,raw_angle_valid,acquisition_fault,sample_en,frame_start;
 wire source_rst_n;
 reg [3:0] mapping_settle;
 always @(posedge clk_50m or negedge control_rst_n)
  if(!control_rst_n)mapping_settle<=0;
  else if(!zero_valid || capture_active || prime_reset_count!=0 || hold_pulse)mapping_settle<=0;
  else mapping_settle<={mapping_settle[2:0],1'b1};
 wire mapping_valid=&mapping_settle;
 assign source_rst_n=control_rst_n && zero_valid && mapping_valid && prime_reset_count==0;
 wire busy,done;wire signed [31:0] moto,balance_pwm,position_pwm,target;
 wire [2:0] position_div;
 reg source_frame_ready_reg;
 assign source_frame_ready=source_frame_ready_reg;
 always @(posedge clk_50m or negedge source_rst_n)
  if(!source_rst_n)source_frame_ready_reg<=0;
  else if(sample_en)source_frame_ready_reg<=0;else if(done)source_frame_ready_reg<=1;
 assign source_ready=source_frame_ready && !mapping_fault;
 always @(posedge clk_50m or negedge control_rst_n)begin
  if(!control_rst_n)begin swing_start_pending<=0;source_start<=0;handoff_started<=0;prime_reset_count<=0;
   handoff_wait_ms<=0;hold_pulse<=0;handoff_pulse<=0;prime_pending<=0;end
  else begin
   source_start<=0;hold_pulse<=0;handoff_pulse<=0;
   if(prime_reset_count!=0)prime_reset_count<=prime_reset_count-1'b1;
   if(stopping || estop || fault!=0 || !zero_valid)begin
    swing_start_pending<=0;handoff_started<=0;handoff_wait_ms<=0;prime_pending<=0;
   end else begin
    if(swing_request && !run_session)begin swing_start_pending<=1;handoff_started<=0;handoff_wait_ms<=0;end
    if(swing_start_pending && mode==5 && swing_permission)begin source_start<=1;swing_start_pending<=0;end
    if(mode==5 && swing_finished && !handoff_started)begin
     hold_pulse<=1;handoff_started<=1;prime_reset_count<=15;prime_pending<=1;handoff_wait_ms<=0;
    end
    if(handoff_started && mode==5)begin
     if(tick_1ms && handoff_wait_ms<511)handoff_wait_ms<=handoff_wait_ms+1'b1;
     if(source_frame_ready && delta_abs<=272 && raw_guard_mean>=2285 && raw_guard_mean<=3885 &&
        angle>=2285 && angle<=3885 && !acquisition_fault && !mapping_fault)handoff_pulse<=1;
    end
    if(mode!=5 && !swing_request)begin handoff_started<=0;handoff_wait_ms<=0;end
    if(sample_en && prime_reset_count==0 && !hold_pulse && !
       (mode==5 && swing_finished && !handoff_started))prime_pending<=0;
   end
  end
 end
 generate if(SWING_ENABLED)begin:swing_hardware
  forest_d1_swingup #(.THREE_PROFILES(VARIANT==1 || VARIANT>=4)) swing(.clk(clk_50m),.rst_n(control_rst_n),.tick_1ms(tick_1ms),
   .start(source_start),.cancel(stopping || estop || fault!=0),.session_enable(mode==5 && swing_permission),
   .profile(selected_profile),.upright_valid(zero_valid),.zero_q4(zero_q4),.quiet_q4(filtered),.adc_raw(raw),.encoder_count(enc),
   .cfg_write(cfg_write && cfg_group<3),.cfg_profile(cfg_group[1:0]),.cfg_id(cfg_id),.cfg_value(cfg_value),
   .read_profile(cfg_group[1:0]),.read_id(cfg_id),.cfg_read_value(swing_cfg_read),.cfg_revision(swing_cfg_revision),
   .cfg_write_ok(swing_cfg_ok),.cfg_write_error(swing_cfg_error),.active(swing_active),.finished(swing_finished),.failed(swing_failed),
   .stage(swing_stage),.selected_profile(swing_profile),.pair(swing_pair),.elapsed_ms(swing_elapsed),
   .kit_ip570(kit_ip570),.source_alpha_q20(source_alpha_q20),.source_alpha_rate_q20(source_alpha_rate_q20),
   .source_arm_rate_q20(source_arm_rate_q20),.source_capture_flags(source_capture_flags),.source_reason(source_reason),
   .capture_hold_ms(capture_hold_ms),.source_capture_request(source_capture_request),
   .duty_q15(swing_duty),.motor_bn1(swing_bn1),.motor_bn2(swing_bn2),.motor_pwmb(swing_pwm),.applied_diag(swing_diag));
 end else begin:no_swing
  assign swing_cfg_read=32'hffffffff;assign swing_cfg_revision=0;assign swing_cfg_ok=0;assign swing_cfg_error=0;
  assign swing_active=0;assign swing_finished=0;assign swing_failed=0;assign swing_stage=0;assign swing_profile=0;
  assign source_alpha_q20=0;assign source_alpha_rate_q20=0;assign source_arm_rate_q20=0;
  assign source_capture_flags=0;assign source_reason=0;assign capture_hold_ms=0;assign source_capture_request=0;
  assign swing_pair=0;assign swing_elapsed=0;assign swing_duty=0;assign swing_bn1=0;assign swing_bn2=0;assign swing_pwm=0;assign swing_diag=0;
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
 wire [31:0] motion_events;
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
    if(gesture_code==9)last_action_reason<=6;
    else if(gesture_code==3)last_action_reason<=gesture_start ? 1:2;
    else if(VARIANT==2 && mode==3 && balance_permission && gesture_ready && !stopping && !estop)begin
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
 wire reanchor=zero_valid && !last_zero_valid;
 generate if(VARIANT==2 || VARIANT>=4)begin:motion_hardware
  forest_draw_buffer draw_buffer(.clk(clk_50m),.rst_n(control_rst_n),.tick_5ms(frame_start),
   .upload_permitted(draw_upload_permitted),.abort(1'b0),
   .cfg_write(cfg_write && cfg_group==252),.cfg_id(cfg_id),.cfg_value(cfg_value),
   .cfg_ok(draw_cfg_ok),.cfg_error(draw_cfg_error),.cfg_revision(draw_cfg_revision),.cfg_read_value(draw_cfg_read),
   .committed(draw_committed),.tracking_mode(draw_tracking),.length(draw_length),.read_address(draw_address),.sample(draw_sample));
  forest_motion_planner #(.VARIANT(VARIANT)) motion(.clk(clk_50m),.rst_n(control_rst_n),.tick_5ms(frame_start),.reanchor(reanchor),
   .draw_valid(draw_committed),.draw_tracking(draw_tracking),.draw_length(draw_length),.draw_sample(draw_sample),.draw_address(draw_address),.upload_hold_ready(planner_upload_ready),
   .capture_hold(hold_pulse),.cancel(stopping || estop || mode!=3 || !run_session || fault!=0),
   .balance_active(mode==3 && balance_permission),.encoder_raw(enc),
   .command_valid((remote_motion || gesture_motion_valid) && !stopping && !estop),
   .command(remote_motion ? remote_motion_command:gesture_motion_command),
   .action_kind(remote_motion ? action_kind:2'd0),.action_index(remote_motion ? action_index:3'd0),.request_id(remote_motion ? motion_request_id:32'd0),
   .cfg_write(cfg_write && cfg_group==253),.cfg_id(cfg_id),.cfg_value(cfg_value),.cfg_ok(planner_cfg_ok),.cfg_error(planner_cfg_error),
   .cfg_revision(planner_cfg_revision),.cfg_read_value(planner_cfg_read),.origin_wide(motion_origin),.final_wide(motion_final),
   .last_request_id(planner_request),.last_action(planner_action),.planner_phase(planner_phase),
   .velocity_q(planner_velocity_q),.acceleration_q(planner_acceleration_q),.left_positive(left_positive),.cw_positive(cw_positive),
   .enc_wide_physical(wide_physical),.encoder_wide(wide_encoder),.target_wide(wide_target),
   .encoder_local(motion_encoder),.target_local(motion_target),.active(motion_active),.state(motion_state),
   .direction(motion_direction),.fault(motion_fault),.accepted(motion_accepted),.rejected(motion_rejected),
   .reason(motion_reason),.event_count(motion_events),.kit_ip570(kit_ip570),
   .trajectory_done(trajectory_done),.tracking_settled(tracking_settled),.measured_velocity_cps(measured_velocity_cps),
   .reference_velocity_cps(reference_velocity_cps),.tracking_error(tracking_error),
   .settle_ticks(settle_ticks),.stop_reason(motion_stop_reason));
  assign control_encoder=motion_encoder;assign mapping_fault=motion_fault;
 end else begin:no_motion
  assign planner_cfg_ok=0;assign planner_cfg_error=0;assign planner_cfg_revision=0;assign planner_cfg_read=32'hffffffff;
  assign motion_origin=0;assign motion_final=0;assign planner_request=0;assign planner_action=0;assign planner_phase=0;assign planner_velocity_q=0;assign planner_acceleration_q=0;
  assign wide_physical={{32{encoder_relative[31]}},encoder_relative};
  assign wide_encoder=$signed(mapped_encoder)-64'sd10000;assign wide_target=0;
  assign motion_encoder=mapped_encoder;assign motion_target=10000;
  assign motion_active=0;assign motion_state=0;assign motion_direction=0;assign motion_fault=0;
  assign trajectory_done=0;assign tracking_settled=0;assign measured_velocity_cps=0;assign reference_velocity_cps=0;
  assign tracking_error=0;assign settle_ticks=0;assign motion_stop_reason=0;
  assign motion_accepted=0;assign motion_rejected=0;assign motion_reason=0;assign motion_events=0;
  assign control_encoder=mapped_encoder;assign mapping_fault=original_mapping_fault;
 end endgenerate
 wire raw_source_mean_bad=raw_guard_mean<2285 || raw_guard_mean>3885;
 wire [11:0] source_guard_mean=raw_source_mean_bad ? raw_guard_mean:angle;
 forest_d1_safety #(.ALLOW_SWING(SWING_ENABLED),.ZERO_SAMPLES(ZERO_SAMPLES)) safety(
  .clk(clk_50m),.rst_n(control_rst_n),.tick_1ms(tick_1ms),.kit_ip570(kit_ip570),.invalidate_calibration(kit_changed),.zero_pulse(key_pulse[0] || remote_zero),
  .run_pulse(physical_run || remote_run),.stop_pulse(stopping),.clear_pulse(key_pulse[2] || remote_clear),.estop_raw(estop),
  .start_swing_pulse(swing_request),.hold_pulse(hold_pulse),.handoff_pulse(handoff_pulse),.swing_failed(parent_swing_failed),
  .adc_raw(raw),.adc_q4(filtered),.adc_otr_qualified(otr_qualified),.encoder_count(enc),.invalid_count(invalid),
  .arithmetic_fault(zero_valid && ((mapping_valid && mapping_fault) || (sample_en && busy) || acquisition_fault)),
  .source_sample_en(sample_en),.source_angle_adc12(source_guard_mean),.mode(mode),.fault(fault),.zero_valid(zero_valid),
  .capture_active(capture_active),.zero_q4(zero_q4),.encoder_zero(encoder_zero),.run_session(run_session),
  .motor_permitted(balance_permission),.swing_permitted(swing_permission));
 forest_d1_window_sampler #(.SEPARATE_RAW_GUARD(1)) sampler(.clk(clk_50m),.rst_n(source_rst_n),
  .angle_in(mapped_angle),.angle_valid(angle_valid),.raw_angle_in(raw_mapped_angle),.raw_angle_valid(raw_angle_valid),
  .encoder_in(control_encoder),.angle_capture(angle_capture),.frame_start(frame_start),.sample_en(sample_en),
  .angle_adc12(angle),.encoder_sample(encoder_sample),.frame_number(frames),.acquisition_fault(acquisition_fault),.raw_guard_mean(raw_guard_mean));
 reg active_adaptive_used;reg [31:0] active_feature_epoch;
 reg [191:0] active_gains;reg [31:0] active_revision_reg;reg first_epoch;
 assign active_revision=active_revision_reg;
 wire [191:0] adaptive_target;wire [7:0] freeze_reason;
 wire signed [15:0] center_p,center_d,fast_p,fast_d;
 wire [31:0] learning_revision,candidate_epoch,candidate_revision;
 wire commit_bank=first_epoch || position_div==4;
 wire adaptive_candidate=VARIANT==3 && adaptation_active && candidate_revision==stored_revision && !manual_write;
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
 forest_d1_pid core(.clk(clk_50m),.rst_n(source_rst_n),.sample_en(sample_en),
  .integrator_clear(mode!=3 || !balance_permission),.prime_history(prime_pending),.angle_adc12(angle),.encoder(encoder_sample),
  .gain_write(1'b1),.bkp_bits(core_gains[31:0]),.bki_bits(core_gains[63:32]),.bkd_bits(core_gains[95:64]),
  .pkp_bits(core_gains[127:96]),.pki_bits(core_gains[159:128]),.pkd_bits(core_gains[191:160]),
  .done(done),.moto(moto),.balance_pwm(balance_pwm),.position_pwm(position_pwm),.target(target),
  .position_bias_bits(position_bias),.position_diff_bits(position_diff),.balance_bias_bits(bias_bits),.angle_diff_bits(angle_difference),
  .angle_p_bits(angle_p),.angle_i_bits(angle_i),.angle_d_bits(angle_d),.position_p_bits(position_p),
  .position_i_bits(position_i),.position_d_bits(position_d),.position_div(position_div),.busy(busy));
 wire balance_bn1,balance_bn2,balance_pwm_pin;wire [31:0] balance_diag;
 wire actual_balance_permission=balance_permission && mapping_valid && !mapping_fault && !estop;
 forest_d1_run_actuator actuator(.clk(clk_50m),.rst_n(control_rst_n),.motor_permitted(actual_balance_permission),
  .core_moto(moto),.BN1(balance_bn1),.BN2(balance_bn2),.PWMB(balance_pwm_pin),.duty_actual_q15(),.diag(balance_diag));
 assign AN1=0;assign AN2=0;assign PWMA=0;
 // Both drivers already gate their pins immediately; mode3/mode5 are exclusive.
 assign BN1=swing_bn1 | balance_bn1;
 assign BN2=swing_bn2 | balance_bn2;
 assign PWMB=swing_pwm | balance_pwm_pin;
 assign actuator_diag=mode==5 ? swing_diag:balance_diag;
 wire signed [15:0] actual_q15=(!actuator_diag[16] || actuator_diag[18]) ? 16'sd0:
  actuator_diag[17] ? -$signed(actuator_diag[15:0]):$signed(actuator_diag[15:0]);
 reg captured_adaptive_used,captured_adaptive_enabled;reg [7:0] captured_freeze;
 reg [31:0] captured_feature_epoch;
 reg [31:0] captured_ms,captured_epoch,captured_revision,captured_stored_revision,captured_adaptive_epoch;
 reg [15:0] captured_raw,captured_zero,captured_q4,captured_angle,captured_guard;
 reg signed [15:0] captured_encoder,captured_error,captured_difference,previous_error;
 reg [31:0] frame_encoder_raw,captured_encoder_raw;
 reg signed [63:0] aligned_wide_encoder,aligned_wide_target,aligned_wide_physical;
 always @(posedge clk_50m or negedge control_rst_n)
  if(!control_rst_n)begin aligned_wide_encoder<=0;aligned_wide_target<=0;aligned_wide_physical<=0;end
  else begin aligned_wide_encoder<=wide_encoder;aligned_wide_target<=wide_target;aligned_wide_physical<=wide_physical;end
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
   if(frame_start)begin
    frame_encoder_raw<=enc;
    frame_wide_encoder<=(VARIANT==2 || VARIANT>=4) ? aligned_wide_encoder:wide_encoder;
    frame_wide_target<=(VARIANT==2 || VARIANT>=4) ? aligned_wide_target:wide_target;
    frame_wide_physical<=(VARIANT==2 || VARIANT>=4) ? aligned_wide_physical:wide_physical;
   end
   if(sample_en)begin
    captured_adaptive_used<=commit_bank ? adaptive_candidate:active_adaptive_used;
    captured_feature_epoch<=commit_bank ? (adaptive_candidate ? candidate_epoch:0):active_feature_epoch;
    captured_adaptive_enabled<=adaptation_enabled;captured_freeze<=freeze_reason;
    captured_ms<=uptime_ms;captured_epoch<=frames;captured_revision<=core_revision;captured_stored_revision<=stored_revision;
    captured_adaptive_epoch<=adaptive_epoch+(adaptive_commit ? 32'd1:32'd0);
    captured_raw<={6'd0,raw};captured_zero<={2'd0,zero_q4};captured_q4<={2'd0,filtered};
    captured_angle<={4'd0,angle};captured_guard<={4'd0,raw_guard_mean};captured_encoder<=encoder_sample;
    captured_error<=$signed({1'b0,angle})-16'sd3085;
    captured_difference<=prime_pending ? 16'sd0:($signed({1'b0,angle})-16'sd3085)-previous_error;
    previous_error<=$signed({1'b0,angle})-16'sd3085;
    captured_encoder_raw<=frame_encoder_raw;captured_wide_encoder<=frame_wide_encoder;
    captured_wide_target<=frame_wide_target;captured_wide_physical<=frame_wide_physical;
    captured_gains<=core_gains;captured_base<=stored_gains;captured_target_gains<=adaptive_target;
    captured_position_update<=position_div==4;captured_center_p<=center_p;captured_center_d<=center_d;
    captured_fast_p<=fast_p;captured_fast_d<=fast_d;captured_learning_revision<=learning_revision;
    captured_candidate_epoch<=candidate_epoch;
   end
   if(done && captured_position_update)position_component_epoch<=captured_epoch;
  end
 end
 wire learning_pulse;wire [31:0] learning_seq,learning_event_epoch,diagnostic_reference_energy,diagnostic_window_energy;
 wire [7:0] learning_code,learning_reason;wire signed [7:0] diagnostic_center_before,diagnostic_center_after;
 wire [15:0] diagnostic_reference_peak,diagnostic_window_peak;wire [1:0] diagnostic_phase;
 wire [5:0] diagnostic_window_samples;wire [3:0] diagnostic_cooldown;wire [6:0] diagnostic_event_samples;
 generate if(VARIANT==3)begin:adaptive_hardware
  forest_d1_adaptive adaptive(.clk(clk_50m),.rst_n(control_rst_n && zero_valid && !capture_active),
   .sample_valid(done && source_rst_n),.commit_tick(adaptive_commit),.run_balance(mode==3 && actual_balance_permission),
   .stop(stopping || estop || !run_session),.manual_write(manual_write),.enable_write(adapt_enable_write),.enable_value(adapt_enable_value),
   .gains_base(stored_gains),.manual_revision(stored_revision),.sample_epoch(captured_epoch),
   .angle_error(captured_error),.angle_diff(captured_difference),
   .position_error($signed(captured_encoder)-32'sd10000),.requested_moto(moto),.actual_q15(actual_q15),
   .saturating(moto>=6900 || moto<=-6900),.fault(fault!=0),.target_gains(adaptive_target),
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
  assign learning_pulse=0;assign learning_seq=0;assign learning_event_epoch=0;assign learning_code=0;assign learning_reason=0;
  assign diagnostic_center_before=0;assign diagnostic_center_after=0;assign diagnostic_reference_energy=0;assign diagnostic_window_energy=0;
  assign diagnostic_reference_peak=0;assign diagnostic_window_peak=0;assign diagnostic_phase=0;
  assign diagnostic_window_samples=0;assign diagnostic_cooldown=0;assign diagnostic_event_samples=0;
  assign adaptive_target=stored_gains;assign adaptation_enabled=0;assign adaptation_active=0;assign freeze_reason=0;
  assign center_p=0;assign center_d=0;assign fast_p=0;assign fast_d=0;assign learning_revision=0;
  assign adaptive_epoch=0;assign candidate_epoch=0;assign candidate_revision=stored_revision;
 end endgenerate
 reg [31:0] packet_sequence;reg [6:0] status_ms;
 wire publish_control=source_rst_n && done;
 wire publish_status=tick_1ms && status_ms==99 && !source_rst_n;
 always @(posedge clk_50m or negedge control_rst_n)begin
  if(!control_rst_n)status_ms<=0;
  else if(tick_1ms)status_ms<=status_ms==99 ? 0:status_ms+1'b1;
 end
 wire [7:0] flags={rx_errors!=0,(publish_control ? captured_adaptive_used:adaptation_active),
  (publish_control ? captured_adaptive_enabled:adaptation_enabled),zero_valid,capture_active,
  actual_balance_permission || swing_permission,run_session,publish_control};
 wire diagnostic_pending;wire [2047:0] diagnostic_packet;
 // Sideband packets use idle controller time and leave a guard before the
 // fixed millisecond status boundary. A registered valid occupies one slot.
 wire publish_diag=diagnostic_pending && !publish_control && !publish_status && tlm_ready &&
  !tlm_valid && !busy && millisecond_div<49000;
 forest_d1_diagnostics #(.VARIANT(VARIANT),.WIRE_VERSION(WIRE_VERSION),.SCHEMA(SCHEMA),.FIRMWARE_ID(FIRMWARE_ID)) diagnostics(
  .clk(clk_50m),.rst_n(control_rst_n),.tick_1ms(tick_1ms),.sent(publish_diag),.uptime_ms(uptime_ms),
  .kit_ip570(kit_ip570),.kit_changed(kit_changed),.zero_valid(zero_valid),.encoder_raw(enc),.invalid_count(invalid),
  .control_done(done && source_rst_n),.control_gains(captured_gains),.control_epoch(captured_epoch),.control_ms(captured_ms),
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
  .swing_finished(swing_finished),.swing_failed(swing_failed),.handoff_started(handoff_started),.handoff_pulse(handoff_pulse),
  .handoff_timeout(handoff_timeout),.source_ready(source_ready),.handoff_wait_ms(handoff_wait_ms),.delta_q4(delta_q4),
  .source_alpha(source_alpha_q20),.source_alpha_rate(source_alpha_rate_q20),.source_arm_rate(source_arm_rate_q20),
  .source_capture_flags(source_capture_flags),.source_reason(source_reason),.capture_hold_ms(capture_hold_ms),.motion_origin(motion_origin),.motion_final(motion_final),.planner_request(planner_request),
  .planner_action(planner_action),.planner_phase(planner_phase),.gesture_state(gesture_state),.decode_reason(decode_reason),
  .pending(diagnostic_pending),.packet(diagnostic_packet));
 always @(posedge clk_50m or negedge control_rst_n)begin
  if(!control_rst_n)begin packet_sequence<=0;tlm_valid<=0;tlm_data<=0;end
  else begin
   tlm_valid<=0;
   if(publish_control || publish_status || publish_diag)begin
    if(!publish_diag)packet_sequence<=packet_sequence+1'b1;tlm_valid<=1;tlm_data<=0;
    if(publish_diag)tlm_data<=diagnostic_packet;else begin
    tlm_data[31:0]<=32'h54324446;tlm_data[39:32]<=WIRE_VERSION;tlm_data[47:40]<=VARIANT;
    tlm_data[55:48]<=flags;tlm_data[63:56]<={5'd0,mode};tlm_data[95:64]<=packet_sequence;
    tlm_data[127:96]<=publish_control ? captured_ms:uptime_ms;
    tlm_data[159:128]<=publish_control ? captured_epoch:0;
    tlm_data[191:160]<=publish_control ? captured_revision:active_revision;
    tlm_data[223:192]<=publish_control ? captured_stored_revision:stored_revision;
    tlm_data[255:224]<=publish_control ? captured_adaptive_epoch:adaptive_epoch;
    tlm_data[287:256]<=FIRMWARE_ID;tlm_data[319:288]<=SCHEMA;
    tlm_data[327:320]<=fault;tlm_data[335:328]<=mode==5 ? {6'd0,swing_profile}:{6'd0,selected_profile};
    tlm_data[343:336]<=swing_stage;tlm_data[351:344]<={5'd0,motion_state};
    tlm_data[359:352]<=last_gesture;tlm_data[367:360]<=last_action_reason;tlm_data[375:368]<=publish_control ? captured_freeze:freeze_reason;
    tlm_data[383:376]<={{6{motion_direction[1]}},motion_direction};
    tlm_data[399:384]<=publish_control ? captured_raw:{6'd0,raw};
    tlm_data[415:400]<=publish_control ? captured_zero:{2'd0,zero_q4};
    tlm_data[431:416]<=publish_control ? captured_q4:{2'd0,filtered};
    tlm_data[447:432]<=publish_control ? captured_angle:16'd0;
    tlm_data[463:448]<=publish_control ? captured_guard:16'd0;
    tlm_data[479:464]<=publish_control ? captured_error:16'd0;
    tlm_data[495:480]<=publish_control ? captured_difference:16'd0;
    tlm_data[511:496]<=actual_q15;
    tlm_data[543:512]<=publish_control ? captured_encoder_raw:enc;
    tlm_data[559:544]<=publish_control ? captured_encoder:16'd0;
    tlm_data[575:560]<=16'd10000;
    tlm_data[639:576]<=publish_control ? captured_wide_encoder:wide_encoder;
    tlm_data[703:640]<=publish_control ? captured_wide_target:wide_target;
    tlm_data[767:704]<=publish_control ? captured_wide_physical:wide_physical;
    tlm_data[799:768]<=publish_control ? ($signed(captured_encoder)-32'sd10000):32'd0;
    tlm_data[831:800]<=publish_control ? moto:32'd0;
    tlm_data[863:832]<=publish_control ? balance_pwm:32'd0;
    tlm_data[895:864]<=publish_control ? position_pwm:32'd0;
    tlm_data[911:896]<=16'd3085;tlm_data[927:912]<=publish_control ? 16'd5000:16'd0;
    tlm_data[959:928]<=publish_control ? (captured_position_update ? captured_epoch:position_component_epoch):32'd0;
    tlm_data[991:960]<=gesture_sequence;tlm_data[1023:992]<=gesture_poll;
    tlm_data[1215:1024]<=publish_control ? captured_base:stored_gains;
    tlm_data[1407:1216]<=publish_control ? captured_gains:active_gains;
    tlm_data[1599:1408]<=publish_control ? captured_target_gains:adaptive_target;
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
 generate if(VARIANT>=4 && BLE_TELEMETRY)begin:ble_telemetry_hardware
 wire signed [64:0] compact_reference_offset=$signed({captured_wide_target[63],captured_wide_target})-$signed({captured_wide_encoder[63],captured_wide_encoder});
 wire signed [64:0] compact_final_offset=$signed({motion_final[63],motion_final})-$signed({captured_wide_encoder[63],captured_wide_encoder});
 wire compact_range_ok=compact_reference_offset[64:31]=={34{compact_reference_offset[31]}} && compact_final_offset[64:31]=={34{compact_final_offset[31]}};
 always @(posedge clk_50m or negedge control_rst_n)begin
  if(!control_rst_n)begin ble_diag_ms<=0;ble_diag_due<=0;ble_tlm_valid<=0;ble_tlm_data<=0;ble_tlm_length<=48;end
  else begin
   ble_tlm_valid<=0;
   if(tick_1ms)begin
    if(ble_diag_ms==499)begin ble_diag_ms<=0;ble_diag_due<=1;end else ble_diag_ms<=ble_diag_ms+1'b1;
   end
   if(VARIANT>=4 && (publish_control || publish_status))begin
    ble_tlm_valid<=1;ble_tlm_length<=48;ble_tlm_data<=0;
    ble_tlm_data[31:0]<=32'h42324446;ble_tlm_data[39:32]<=WIRE_VERSION;ble_tlm_data[47:40]<=VARIANT;
    ble_tlm_data[55:48]<={compact_range_ok,flags[6:0]};ble_tlm_data[63:56]<={5'd0,mode};
    ble_tlm_data[95:64]<=packet_sequence;ble_tlm_data[127:96]<=publish_control ? captured_ms:uptime_ms;
    ble_tlm_data[159:128]<=publish_control ? captured_epoch:0;
    ble_tlm_data[223:160]<=publish_control ? captured_wide_encoder:wide_encoder;
    ble_tlm_data[255:224]<=compact_reference_offset[31:0];ble_tlm_data[287:256]<=compact_final_offset[31:0];
    ble_tlm_data[303:288]<=publish_control ? captured_error:0;ble_tlm_data[319:304]<=0;
    ble_tlm_data[335:320]<=actual_q15;ble_tlm_data[351:336]<=measured_velocity_cps[15:0];
    ble_tlm_data[359:352]<=fault;ble_tlm_data[367:360]<={5'd0,motion_state};
   end else if(VARIANT>=4 && ble_diag_due && ble_tlm_ready && !ble_tlm_valid && !busy && millisecond_div<49000)begin
    ble_tlm_valid<=1;ble_tlm_length<=256;ble_tlm_data<=diagnostic_packet;ble_diag_due<=0;
   end
  end
 end
 end else begin:ble_telemetry_not_built
  always @(posedge clk_50m)begin
   ble_tlm_valid<=0;ble_tlm_data<=0;ble_tlm_length<=48;ble_diag_ms<=0;ble_diag_due<=0;
  end
 end endgenerate

endmodule

