// Minimal V4 with the supplied ADC64 / fast-P balance implementation.
// P runs at1/5ms. The original kernel remains the sole D producer at5/25ms.
module forest_d1_system #(
 parameter integer VARIANT=4,STARTUP_CYCLES=100000000,KEY_STABLE_CYCLES=500000,
 parameter integer ZERO_SAMPLES=1024,WATCHDOG_CYCLES=25000000,
 parameter integer WIRE_VERSION=4,BLE_TELEMETRY=1,BLE_CLKS_PER_BIT=217,
 parameter [31:0] SCHEMA=32'h56345208,FIRMWARE_ID=32'h56345239
)(
 input clk_50m,rst_n,input [3:0] key_sw,input uart_rx,output uart_tx,
 input bt_rx,bt_state,output bt_tx,
 output adc_clk,adc_oe_n,input [9:0] adc_data_in,input adc_otr,enc1_a,enc1_b,
 output AN1,AN2,PWMA,BN1,BN2,PWMB,inout gesture_scl,gesture_sda
);
 localparam [191:0] DEFAULT_GAINS={32'h441a270a,32'd0,32'h418d5c29,32'h435223d7,32'd0,32'h42571eb8};
 wire reset_n;
 forest_d1_reset resetter(clk_50m,rst_n,reset_n);
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
 wire [9:0] raw,clean;wire [13:0] filtered;wire [31:0] actuator_diag,adc_cycle;
 wire adc_batch_valid;
 reg [31:0] adc_batch_sequence;
 always @(posedge clk_50m or negedge reset_n)
  if(!reset_n)adc_batch_sequence<=0;else if(adc_batch_valid)adc_batch_sequence<=adc_batch_sequence+1'b1;
 j280_adc_capture_trace #(.DIAGNOSTIC_SWITCHES(1),.QUIET_CACHE_WORDS(1),.ADC_WORD_GUARD(1),.WINDOW_SAMPLES(64)) adc(
  .clk_50m(clk_50m),.rst_n(reset_n),.adc_data_in(adc_data_in),.average_enable(1'b1),
  .trace_adc_otr(adc_otr),.trace_actuator_diag(actuator_diag),.trace_motor_pins({BN1,BN2,PWMB}),
  .adc_clk(adc_clk),.adc_oe_n(adc_oe_n),.adc_raw(raw),.adc_clean_stage(clean),.adc_filtered_q4(filtered),
  .control_sample_valid(adc_batch_valid),.trace_valid(),.trace_record(),.trace_time_counter(adc_cycle));
 wire signed [31:0] enc;wire [15:0] invalid;
 j280_encoder_x4 encoder(.clk_50m(clk_50m),.rst_n(reset_n),.enc_a(enc1_a),.enc_b(enc1_b),
  .count(enc),.invalid_transition_count(invalid));
 wire [3:0] pressed,key_pulse;
 genvar ki;generate for(ki=0;ki<4;ki=ki+1)begin:keys
  j280_key_debounce #(.STABLE_CYCLES(KEY_STABLE_CYCLES)) key(
   .clk_50m(clk_50m),.rst_n(reset_n),.key_n(key_sw[ki]),.pressed(pressed[ki]),.press_pulse(key_pulse[ki]));
 end endgenerate
 wire physical_estop=pressed[3] || !key_sw[3];
 wire remote_stop,remote_zero,remote_run,remote_clear,serial_estop,watchdog_stop,urgent_stop,urgent_estop;
 wire [2:0] mode;wire [7:0] fault;wire zero_valid,capture_active,run_session,balance_permission;
 wire [13:0] zero_q4;wire signed [31:0] encoder_zero;
 wire [191:0] stored_gains;wire [31:0] stored_revision,last_mutation,config_revision,gateway_errors;
 wire kit_ip570,kit_changed,left_positive,cw_positive;
 wire cfg_write;wire [7:0] cfg_group,cfg_id;wire [31:0] cfg_value,planner_cfg_read,planner_cfg_revision;
 wire planner_cfg_ok,planner_cfg_error;
 wire remote_motion;wire [2:0] remote_motion_command,action_index;wire [1:0] action_kind;
 wire [31:0] motion_request_id;wire motion_accepted,motion_rejected;wire [7:0] motion_reason;
 wire [383:0] command,ble_command;wire command_valid,ble_command_valid;wire [1:0] ble_emergency;
 wire [623:0] reply,route_reply;wire reply_valid,ble_reply_valid,ble_reply_ready;
 wire [31:0] logical_session,last_event,duplicates,route_rejects,ingress_overflow,last_routed_request;
 wire [1:0] last_routed_source;
 reg [1:0] bt_sync;
 always @(posedge clk_50m or negedge control_rst_n)
  if(!control_rst_n)bt_sync<=0;else bt_sync<={bt_sync[0],bt_state};
 wire source_ready,source_frame_ready;
 wire physical_run=key_pulse[1] && (source_ready || run_session);
 wire stopping=urgent_stop || remote_stop || watchdog_stop || (physical_run && run_session);
 wire estop=physical_estop || serial_estop;
 reg [31:0] active_revision_reg;
 wire [31:0] active_revision=active_revision_reg;
 reg [31:0] captured_epoch;
 forest_d1_gateway #(.VARIANT(4),.WIRE_VERSION(WIRE_VERSION),.BLE_TELEMETRY(1),
  .WATCHDOG_CYCLES(WATCHDOG_CYCLES),.FIRMWARE_ID(FIRMWARE_ID),.RESET_GAINS(DEFAULT_GAINS)) gateway(
  .clk(clk_50m),.rst_n(control_rst_n),.urgent_stop(urgent_stop),.urgent_estop(urgent_estop),
  .command_valid(command_valid),.command_data(command),.reply_valid(reply_valid),.reply_data(reply),
  .mode(mode),.fault(fault),.zero_valid(zero_valid),.capture_active(capture_active),.run_session(run_session),
  .motor_permitted(balance_permission),.physical_estop_active(physical_estop),.source_ready(source_ready),
  .balance_active(mode==3 && balance_permission),.adapt_enabled(1'b0),.adapt_active(1'b0),
  .physical_start_event(physical_run),.active_revision(active_revision),.control_epoch(captured_epoch),.adaptive_epoch(32'd0),
  .stop_pulse(remote_stop),.zero_pulse(remote_zero),.run_pulse(remote_run),.clear_pulse(remote_clear),
  .swing_start_pulse(),.serial_estop_latched(serial_estop),.watchdog_stop(watchdog_stop),
  .gains_stored(stored_gains),.gain_revision(stored_revision),.last_mutation_id(last_mutation),
  .manual_write(),.enable_write(),.enable_value(),.selected_profile(),.gesture_map_flags(),
  .left_positive(left_positive),.cw_positive(cw_positive),.gesture_enable(),.kit_ip570(kit_ip570),.kit_changed(kit_changed),
  .cfg_write(cfg_write),.cfg_group(cfg_group),.cfg_id(cfg_id),.cfg_value(cfg_value),.cfg_read_value(planner_cfg_read),
  .external_cfg_revision(planner_cfg_revision),.cfg_result_valid(planner_cfg_ok || planner_cfg_error),.cfg_result_ok(planner_cfg_ok),
  .motion_valid(remote_motion),.motion_command(remote_motion_command),.action_kind(action_kind),.action_index(action_index),
  .motion_request_id(motion_request_id),.motion_result_valid(motion_accepted || motion_rejected),.motion_result_ok(motion_accepted),
  .config_revision(config_revision),.gateway_errors(gateway_errors));
 forest_command_router #(.VARIANT(4),.WIRE_VERSION(WIRE_VERSION),.PREDECODED_EMERGENCY(1),.FIRMWARE_ID(FIRMWARE_ID)) router(
  .clk(clk_50m),.rst_n(control_rst_n),.usb_valid(1'b0),.usb_data(384'd0),.ble_valid(ble_command_valid),.ble_data(ble_command),
  .ble_connected(bt_sync[1]),.session_expire(watchdog_stop),.usb_emergency_decode(2'd0),.ble_emergency_decode(ble_emergency),
  .gateway_valid(command_valid),.gateway_data(command),.gateway_reply_valid(reply_valid),.gateway_reply(reply),
  .usb_reply_valid(),.ble_reply_valid(ble_reply_valid),.reply_data(route_reply),.usb_reply_ready(1'b1),.ble_reply_ready(ble_reply_ready),
  .urgent_stop(urgent_stop),.urgent_estop(urgent_estop),.mode(mode),.fault(fault),.run_session(run_session),
  .motor_permitted(balance_permission),.capture_active(capture_active),.stored_gains(stored_gains),.gain_revision(stored_revision),
  .mutation_highwater(last_mutation),.control_epoch(captured_epoch),.active_revision(active_revision),.adaptive_epoch(32'd0),
  .config_revision(config_revision),.active_session(logical_session),.last_event(last_event),.duplicate_count(duplicates),
  .rejected_count(route_rejects),.ingress_overflow(ingress_overflow),.last_routed_request(last_routed_request),.last_routed_source(last_routed_source));
 reg ble_tlm_valid;reg [2047:0] ble_tlm_data;reg [8:0] ble_tlm_length;wire ble_tlm_ready;
 wire [31:0] ble_rx_errors,ble_tlm_dropped,ble_reply_dropped;
 forest_d1_link #(.CLKS_PER_BIT(BLE_CLKS_PER_BIT),.EMERGENCY_WIRE_VERSION(WIRE_VERSION)) ble_link(
  .clk(clk_50m),.rst_n(control_rst_n),.uart_rx(bt_rx),.uart_tx(bt_tx),
  .command_valid(ble_command_valid),.command_data(ble_command),.command_emergency(ble_emergency),
  .reply_valid(ble_reply_valid),.reply_data(route_reply),.reply_ready(ble_reply_ready),
  .tlm_valid(ble_tlm_valid),.tlm_data(ble_tlm_data),.tlm_length(ble_tlm_length),.tlm_ready(ble_tlm_ready),
  .rx_error_count(ble_rx_errors),.tlm_dropped(ble_tlm_dropped),.reply_dropped(ble_reply_dropped),.busy());
 assign uart_tx=1'b1;
 assign gesture_scl=1'bz;assign gesture_sda=1'bz;
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
 reg [3:0] mapping_settle;
 always @(posedge clk_50m or negedge control_rst_n)
  if(!control_rst_n)mapping_settle<=0;
  else if(!zero_valid || capture_active)mapping_settle<=0;else mapping_settle<={mapping_settle[2:0],1'b1};
 wire mapping_valid=&mapping_settle;
 wire source_rst_n=control_rst_n && zero_valid && mapping_valid;
 wire [11:0] mapped_angle,raw_mapped_angle,angle,raw_guard_mean;
 wire signed [15:0] mapped_encoder,encoder_sample,motion_encoder,motion_target;
 wire original_mapping_fault,angle_capture,angle_valid,raw_angle_valid,acquisition_fault,sample_en,frame_start;
 forest_d1_run_inputs #(.USE_Q4_INPUT(1)) inputs(.clk(clk_50m),.rst_n(control_rst_n),.angle_epoch_valid(source_rst_n),
  .angle_capture(angle_capture),.kit_ip570(kit_ip570),.adc_raw(raw),.adc_q4(filtered),.zero_q4(zero_q4),
  .encoder_count(enc),.encoder_zero(encoder_zero),.angle_adc12(mapped_angle),.encoder_forest(mapped_encoder),
  .encoder_range_fault(original_mapping_fault),.angle_valid(angle_valid));
 forest_d1_run_inputs raw_inputs(.clk(clk_50m),.rst_n(control_rst_n),.angle_epoch_valid(source_rst_n),
  .angle_capture(angle_capture),.kit_ip570(kit_ip570),.adc_raw(raw),.adc_q4(filtered),.zero_q4(zero_q4),
  .encoder_count(enc),.encoder_zero(encoder_zero),.angle_adc12(raw_mapped_angle),.encoder_forest(),.encoder_range_fault(),.angle_valid(raw_angle_valid));
 reg last_zero_valid;
 always @(posedge clk_50m or negedge control_rst_n)
  if(!control_rst_n)last_zero_valid<=0;else last_zero_valid<=zero_valid;
 wire reanchor=zero_valid && !last_zero_valid;
 wire signed [63:0] wide_physical,wide_encoder,wide_target,motion_origin,motion_final,nominal_target,cancelled_final,tracking_error;
 wire [15:0] nominal_fraction,control_fraction,parking_fraction;
 wire [31:0] planner_request,motion_events;
 wire [7:0] planner_action,motion_stop_reason,settle_ticks;wire [3:0] planner_phase;
 wire signed [31:0] planner_velocity_q,planner_acceleration_q,nominal_v,nominal_a,measured_velocity_cps,reference_velocity_cps;
 wire motion_active,motion_fault,trajectory_done,tracking_settled,nominal_done,control_done,transition_active,cancelled_valid;
 wire [2:0] motion_state;wire signed [1:0] motion_direction;
 forest_motion_v4_r21 motion(.clk(clk_50m),.rst_n(control_rst_n),.tick_5ms(frame_start),.reanchor(reanchor),
  .capture_hold(1'b0),.cancel(stopping || estop || mode!=3 || !run_session || fault!=0),
  .balance_active(mode==3 && balance_permission),.encoder_raw(enc),.command_valid(remote_motion && !stopping && !estop),
  .command(remote_motion_command),.action_kind(action_kind),.action_index(action_index),.request_id(motion_request_id),
  .cfg_write(cfg_write && cfg_group==253),.cfg_id(cfg_id),.cfg_value(cfg_value),.cfg_ok(planner_cfg_ok),.cfg_error(planner_cfg_error),
  .cfg_revision(planner_cfg_revision),.cfg_read_value(planner_cfg_read),.origin_wide(motion_origin),.final_wide(motion_final),
  .last_request_id(planner_request),.last_action(planner_action),.planner_phase(planner_phase),
  .velocity_q(planner_velocity_q),.acceleration_q(planner_acceleration_q),.left_positive(left_positive),.cw_positive(cw_positive),
  .enc_wide_physical(wide_physical),.encoder_wide(wide_encoder),.target_wide(wide_target),
  .encoder_local(motion_encoder),.target_local(motion_target),.active(motion_active),.state(motion_state),
  .direction(motion_direction),.fault(motion_fault),.accepted(motion_accepted),.rejected(motion_rejected),.reason(motion_reason),
  .event_count(motion_events),.kit_ip570(kit_ip570),.trajectory_done(trajectory_done),.tracking_settled(tracking_settled),
  .measured_velocity_cps(measured_velocity_cps),.reference_velocity_cps(reference_velocity_cps),.tracking_error(tracking_error),
  .nominal_target_wide(nominal_target),.nominal_fraction(nominal_fraction),.control_fraction(control_fraction),
  .nominal_velocity_q(nominal_v),.nominal_acceleration_q(nominal_a),.cancelled_final_wide(cancelled_final),.parking_fraction(parking_fraction),
  .nominal_done(nominal_done),.control_reference_done(control_done),.transition_active(transition_active),.cancelled_goal_valid(cancelled_valid),
  .settle_ticks(settle_ticks),.stop_reason(motion_stop_reason));
 wire busy,done,source_mean_bad;wire [31:0] frames;
 wire control_fast_p,compare_ready,compare_deadline_fault;
 reg fast_acquisition_fault;
 wire signed [31:0] moto,balance_pwm,position_pwm,target;
 wire [2:0] position_div;
 reg source_frame_ready_reg;
 assign source_frame_ready=source_frame_ready_reg && compare_ready;
 always @(posedge clk_50m or negedge source_rst_n)
  if(!source_rst_n)source_frame_ready_reg<=0;else if(sample_en)source_frame_ready_reg<=0;else if(done)source_frame_ready_reg<=1;
 assign source_ready=source_frame_ready && !motion_fault;
 wire [11:0] source_guard_mean=source_mean_bad ? 12'd0:12'd3085;
 forest_d1_safety #(.ALLOW_SWING(0),.ZERO_SAMPLES(ZERO_SAMPLES)) safety(
  .clk(clk_50m),.rst_n(control_rst_n),.tick_1ms(tick_1ms),.kit_ip570(kit_ip570),.invalidate_calibration(kit_changed),
  .zero_pulse(key_pulse[0] || remote_zero),.run_pulse(physical_run || remote_run),.stop_pulse(stopping),
  .clear_pulse(key_pulse[2] || remote_clear),.estop_raw(estop),.start_swing_pulse(1'b0),.hold_pulse(1'b0),.handoff_pulse(1'b0),.swing_failed(1'b0),
  .adc_raw(raw),.adc_q4(filtered),.adc_otr_qualified(otr_qualified),.encoder_count(enc),.invalid_count(invalid),
  // Independent slow/fast kernels may overlap. Their own repeated-launch
  // guards retain the old slow deadline and add the fast1ms deadline.
  .arithmetic_fault(zero_valid && ((mapping_valid && motion_fault) || compare_deadline_fault || fast_acquisition_fault || acquisition_fault)),
  .source_sample_en(sample_en),.source_angle_adc12(source_guard_mean),.mode(mode),.fault(fault),.zero_valid(zero_valid),
  .capture_active(capture_active),.zero_q4(zero_q4),.encoder_zero(encoder_zero),.run_session(run_session),
  .motor_permitted(balance_permission),.swing_permitted());
 forest_d1_window_sampler #(.SEPARATE_RAW_GUARD(1)) sampler(.clk(clk_50m),.rst_n(source_rst_n),
  .angle_in(mapped_angle),.angle_valid(angle_valid),.raw_angle_in(raw_mapped_angle),.raw_angle_valid(raw_angle_valid),
  .encoder_in(motion_encoder),.angle_capture(angle_capture),.frame_start(frame_start),.sample_en(sample_en),
  .angle_adc12(angle),.encoder_sample(encoder_sample),.frame_number(frames),.acquisition_fault(acquisition_fault),
  .raw_guard_mean(raw_guard_mean),.source_mean_bad(source_mean_bad));
 reg [191:0] active_gains;reg first_epoch;
 wire commit_bank=first_epoch || position_div==4;
 wire [191:0] core_gains=commit_bank ? stored_gains:active_gains;
 wire [31:0] core_revision=commit_bank ? stored_revision:active_revision;
 always @(posedge clk_50m or negedge source_rst_n)
  if(!source_rst_n)begin active_gains<=DEFAULT_GAINS;active_revision_reg<=0;first_epoch<=1;end
  else if(sample_en)begin first_epoch<=0;if(commit_bank)begin active_gains<=stored_gains;active_revision_reg<=stored_revision;end end
 wire [31:0] position_bias,position_diff,bias_bits,angle_difference,angle_p,angle_i,angle_d,position_p,position_i,position_d;
 wire actual_balance_permission=balance_permission && mapping_valid && !motion_fault && !estop;
 wire fast_request=adc_batch_valid && source_rst_n;
 wire [11:0] fast_mapped_angle;
 wire fast_mapping_valid,fast_mapping_fault;
 reg [3:0] expected_fast_valid;
 reg signed [15:0] fast_control_encoder;
 wire observation_start;wire [11:0] observation_angle;
 wire signed [15:0] observation_encoder,observation_delta;
 wire [191:0] observation_gains;wire [31:0] observation_revision;
 wire [31:0] control_angle_d_epoch,control_position_d_epoch,control_position_p_epoch;
 wire [2:0] fast_position_div;
 always @(posedge clk_50m or negedge source_rst_n)
  if(!source_rst_n)begin expected_fast_valid<=0;fast_acquisition_fault<=0;fast_control_encoder<=10000;end
  else begin
   expected_fast_valid<={expected_fast_valid[2:0],fast_request};
   // motion_encoder already is10000+actual-control_reference. Never remap it
   // through raw ENC: doing so would silently bypass V4's dynamic target.
   if(fast_request)fast_control_encoder<=motion_encoder;
   if(fast_mapping_valid!=expected_fast_valid[3])fast_acquisition_fault<=1;
  end
 forest_d1_run_inputs #(.USE_Q4_INPUT(1)) fast_inputs(
  .clk(clk_50m),.rst_n(control_rst_n),.angle_epoch_valid(source_rst_n),.angle_capture(fast_request),
  .kit_ip570(kit_ip570),.adc_raw(raw),.adc_q4(filtered),.zero_q4(zero_q4),.encoder_count(enc),.encoder_zero(encoder_zero),
  .angle_adc12(fast_mapped_angle),.encoder_forest(),.encoder_range_fault(fast_mapping_fault),.angle_valid(fast_mapping_valid));
 // The independent V4 wide-coordinate guard owns ENC range validity.
 // fast_inputs' unused16-bit raw-relative map is not the control coordinate.
 forest_d1_p_compare core(.clk(clk_50m),.rst_n(source_rst_n),.cfg_rst_n(control_rst_n),.sample_en(sample_en),
  .fast_sample_en(fast_mapping_valid && !fast_acquisition_fault),.run_session(mode==3 && actual_balance_permission),
  .requested_fast(1'b1),.active_fast(control_fast_p),.ready(compare_ready),.deadline_fault(compare_deadline_fault),
  .sample_epoch(uptime_ms),.gains(core_gains),.gain_revision(core_revision),
  .integrator_clear(mode!=3 || !balance_permission),.prime_history(1'b0),.angle_adc12(angle),.encoder(encoder_sample),
  .fast_angle(fast_mapped_angle),.fast_encoder(fast_control_encoder),
  .observation_start(observation_start),.observation_angle(observation_angle),.observation_encoder(observation_encoder),
  .observation_delta(observation_delta),.observation_gains(observation_gains),.observation_revision(observation_revision),
  .angle_d_epoch(control_angle_d_epoch),.position_d_epoch(control_position_d_epoch),.position_p_epoch(control_position_p_epoch),
  .done(done),.moto(moto),.balance_pwm(balance_pwm),.position_pwm(position_pwm),.target(target),
  .position_bias_bits(position_bias),.position_diff_bits(position_diff),.balance_bias_bits(bias_bits),.angle_diff_bits(angle_difference),
  .angle_p_bits(angle_p),.angle_i_bits(angle_i),.angle_d_bits(angle_d),.position_p_bits(position_p),
  .position_i_bits(position_i),.position_d_bits(position_d),.position_div(position_div),.fast_position_div(fast_position_div),.busy(busy),.unused_terminal());
 r25_run_actuator_rom actuator(.clk(clk_50m),.rst_n(control_rst_n),.motor_permitted(actual_balance_permission),
  .core_moto(moto),.BN1(BN1),.BN2(BN2),.PWMB(PWMB),.duty_actual_q15(),.diag(actuator_diag));
 assign AN1=0;assign AN2=0;assign PWMA=0;
 wire signed [15:0] actual_q15=(!actuator_diag[16] || actuator_diag[18]) ? 16'sd0:
  actuator_diag[17] ? -$signed(actuator_diag[15:0]):$signed(actuator_diag[15:0]);
 // Capture motion and angle together at the actual controller sample.
 // Completed packets never mix a later live goal with an earlier angle.
 reg signed [63:0] aligned_encoder,aligned_reference,aligned_final,aligned_nominal;
 reg [15:0] aligned_fraction;
 reg signed [31:0] aligned_velocity,frame_velocity;
 reg [7:0] aligned_motion_flags,frame_motion_flags;
 reg [2:0] aligned_motion_state,frame_motion_state;
 reg signed [63:0] frame_encoder,frame_reference,frame_final,frame_nominal;
 reg signed [63:0] captured_wide_encoder,captured_wide_target,captured_final,captured_nominal;
 reg [15:0] frame_fraction,captured_fraction,captured_angle,captured_zero,captured_q4,captured_raw;
 reg signed [15:0] captured_error;reg signed [31:0] captured_velocity;
 reg [31:0] captured_ms,captured_revision;reg [191:0] captured_gains;
 reg [7:0] captured_motion_flags;reg [2:0] captured_motion_state;
 reg [31:0] frame_batch_sequence,captured_batch_sequence;
 reg [31:0] completed_angle_d_epoch,completed_position_d_epoch,completed_position_p_epoch,completed_sample_epoch,completed_gain_revision;
 reg [2:0] publication_div;
 reg captured_publish,captured_control_fast;
 always @(posedge clk_50m or negedge source_rst_n)
  if(!source_rst_n)begin publication_div<=0;captured_publish<=0;end
  else begin
   if(observation_start)begin
    captured_publish<=!control_fast_p || publication_div==0;
    if(control_fast_p)publication_div<=publication_div==4 ? 0:publication_div+1'b1;else publication_div<=0;
   end
  end
 // ZERO can abort a calculation between observation_start and done. In
 // layout5 it clears the entire observation/epoch/revision/profile snapshot
 // together, rather than exposing a new launch with an older completed D.
 always @(posedge clk_50m or negedge source_rst_n)
  if(!source_rst_n)begin captured_control_fast<=0;
   completed_angle_d_epoch<=0;completed_position_d_epoch<=0;completed_position_p_epoch<=0;
   completed_sample_epoch<=0;completed_gain_revision<=0;end
  else begin
   if(observation_start && source_rst_n)captured_control_fast<=control_fast_p;
   if(done && source_rst_n)begin
    completed_angle_d_epoch<=control_angle_d_epoch;completed_position_d_epoch<=control_position_d_epoch;
    completed_position_p_epoch<=control_position_p_epoch;completed_sample_epoch<=captured_epoch;completed_gain_revision<=captured_revision;
   end
  end
 always @(posedge clk_50m or negedge source_rst_n)begin
  if(!source_rst_n)begin
   aligned_encoder<=0;aligned_reference<=0;aligned_final<=0;aligned_nominal<=0;aligned_fraction<=0;
   aligned_velocity<=0;frame_velocity<=0;aligned_motion_flags<=0;frame_motion_flags<=0;
   aligned_motion_state<=0;frame_motion_state<=0;
   frame_encoder<=0;frame_reference<=0;frame_final<=0;frame_nominal<=0;frame_fraction<=0;
   frame_batch_sequence<=0;captured_batch_sequence<=0;
   captured_wide_encoder<=0;captured_wide_target<=0;captured_final<=0;captured_nominal<=0;captured_fraction<=0;
   captured_epoch<=0;captured_ms<=0;captured_revision<=0;captured_gains<=DEFAULT_GAINS;
   captured_angle<=3085;captured_error<=0;captured_zero<=0;captured_q4<=0;captured_raw<=0;
   captured_velocity<=0;captured_motion_state<=0;captured_motion_flags<=0;
  end else begin
   // Coordinate encoder_local is registered; align metadata to its input edge.
   aligned_encoder<=wide_encoder;aligned_reference<=wide_target;aligned_final<=motion_final;
   aligned_nominal<=nominal_target;aligned_fraction<=control_fraction;aligned_velocity<=measured_velocity_cps;
   aligned_motion_state<=motion_state;
   aligned_motion_flags<={motion_active,motion_fault,cancelled_valid,transition_active,control_done,nominal_done,tracking_settled,trajectory_done};
   if((control_fast_p ? fast_request:frame_start) && source_rst_n)begin frame_encoder<=aligned_encoder;frame_reference<=aligned_reference;
    frame_final<=aligned_final;frame_nominal<=aligned_nominal;frame_fraction<=aligned_fraction;
    frame_velocity<=aligned_velocity;frame_motion_state<=aligned_motion_state;frame_motion_flags<=aligned_motion_flags;
    frame_batch_sequence<=adc_batch_sequence+(fast_request ? 32'd1:32'd0);end
   if(observation_start && source_rst_n)begin
    captured_wide_encoder<=frame_encoder;captured_wide_target<=frame_reference;captured_final<=frame_final;
    captured_nominal<=frame_nominal;captured_fraction<=frame_fraction;
    captured_epoch<=uptime_ms;captured_ms<=uptime_ms;captured_revision<=observation_revision;captured_gains<=observation_gains;
    captured_angle<={4'd0,observation_angle};captured_error<=$signed({1'b0,observation_angle})-16'sd3085;
    captured_zero<={2'd0,zero_q4};captured_q4<={2'd0,filtered};captured_raw<={6'd0,raw};
    captured_velocity<=frame_velocity;captured_motion_state<=frame_motion_state;captured_motion_flags<=frame_motion_flags;
    captured_batch_sequence<=frame_batch_sequence;
   end
  end
 end
 wire publish_control=done && source_rst_n && captured_publish;
 reg [6:0] status_ms;reg [8:0] identity_ms;reg identity_due;
 reg [31:0] packet_sequence;
 wire publish_status=tick_1ms && status_ms==99 && !source_rst_n;
 wire [7:0] flags={1'b0,2'b0,zero_valid,capture_active,actual_balance_permission,run_session,publish_control};
 wire signed [64:0] reference_offset=$signed({captured_wide_target[63],captured_wide_target})-$signed({captured_wide_encoder[63],captured_wide_encoder});
 wire signed [64:0] final_offset=$signed({captured_final[63],captured_final})-$signed({captured_wide_encoder[63],captured_wide_encoder});
 wire compact_range_ok=reference_offset[64:31]=={34{reference_offset[31]}} && final_offset[64:31]=={34{final_offset[31]}};
 always @(posedge clk_50m or negedge control_rst_n)begin
  if(!control_rst_n)begin status_ms<=0;identity_ms<=0;identity_due<=1;packet_sequence<=0;
   ble_tlm_valid<=0;ble_tlm_data<=0;ble_tlm_length<=48;end
  else begin
   ble_tlm_valid<=0;
   if(tick_1ms)begin
    status_ms<=status_ms==99 ? 0:status_ms+1'b1;
    if(identity_ms==499)begin identity_ms<=0;identity_due<=1;end else identity_ms<=identity_ms+1'b1;
   end
   if(publish_control || publish_status)begin
    packet_sequence<=packet_sequence+1'b1;ble_tlm_valid<=1;ble_tlm_length<=48;ble_tlm_data<=0;
    ble_tlm_data[31:0]<=32'h42324446;ble_tlm_data[39:32]<=WIRE_VERSION;ble_tlm_data[47:40]<=4;
    ble_tlm_data[55:48]<={compact_range_ok,flags[6:0]};ble_tlm_data[63:56]<={5'd0,mode};
    ble_tlm_data[95:64]<=packet_sequence;ble_tlm_data[127:96]<=publish_control ? captured_ms:uptime_ms;
    ble_tlm_data[159:128]<=publish_control ? captured_epoch:0;
    ble_tlm_data[223:160]<=captured_wide_encoder;ble_tlm_data[255:224]<=reference_offset[31:0];
    ble_tlm_data[287:256]<=final_offset[31:0];ble_tlm_data[303:288]<=publish_control ? captured_error:0;
    ble_tlm_data[319:304]<=0;ble_tlm_data[335:320]<=actual_q15;
    ble_tlm_data[351:336]<=captured_velocity[15:0];ble_tlm_data[359:352]<=fault;
    ble_tlm_data[367:360]<={5'd0,captured_motion_state};
   end else if(identity_due && ble_tlm_ready && !ble_tlm_valid && !busy && !done && !observation_start && millisecond_div<49000)begin
    identity_due<=0;ble_tlm_valid<=1;ble_tlm_length<=256;ble_tlm_data<=0;
    ble_tlm_data[31:0]<=32'h44324446;ble_tlm_data[39:32]<=WIRE_VERSION;ble_tlm_data[47:40]<=4;
    ble_tlm_data[55:48]<=5;ble_tlm_data[63:56]<=flags;
    ble_tlm_data[95:64]<=packet_sequence;ble_tlm_data[127:96]<=uptime_ms;
    ble_tlm_data[159:128]<=SCHEMA;ble_tlm_data[191:160]<=FIRMWARE_ID;
    ble_tlm_data[223:192]<=captured_epoch;ble_tlm_data[255:224]<=captured_ms;
    ble_tlm_data[263:256]<={7'd0,kit_ip570};ble_tlm_data[271:264]<={7'd0,zero_valid};
    ble_tlm_data[351:320]<=enc;ble_tlm_data[367:352]<=invalid;
    ble_tlm_data[415:384]<=kit_ip570 ? 1040:1080;ble_tlm_data[447:416]<=1040;
    ble_tlm_data[479:448]<=captured_velocity;ble_tlm_data[511:480]<=reference_velocity_cps;
    ble_tlm_data[543:512]<=captured_revision;ble_tlm_data[575:544]<=stored_revision;
    ble_tlm_data[607:576]<={captured_angle,captured_zero};
    ble_tlm_data[639:608]<={captured_raw,captured_q4};
    ble_tlm_data[671:640]<=captured_epoch;ble_tlm_data[703:672]<=captured_ms;
    ble_tlm_data[767:704]<=captured_final;ble_tlm_data[831:768]<=captured_nominal;
    ble_tlm_data[895:832]<=captured_wide_target;ble_tlm_data[959:896]<=captured_wide_encoder;
    ble_tlm_data[975:960]<=captured_fraction;ble_tlm_data[983:976]<=captured_motion_flags;
    ble_tlm_data[991:984]<={5'd0,mode};ble_tlm_data[1023:992]<=moto;
    ble_tlm_data[1215:1024]<=captured_gains;
    ble_tlm_data[1247:1216]<=ble_rx_errors;ble_tlm_data[1279:1248]<=ble_tlm_dropped;
    ble_tlm_data[1311:1280]<=ble_reply_dropped;ble_tlm_data[1343:1312]<=logical_session;
    ble_tlm_data[1375:1344]<=config_revision;ble_tlm_data[1383:1376]<=fault;
    ble_tlm_data[1415:1384]<=position_bias;ble_tlm_data[1447:1416]<=position_diff;
    ble_tlm_data[1479:1448]<=balance_pwm;ble_tlm_data[1511:1480]<=position_pwm;
    ble_tlm_data[1519:1512]<={7'd0,captured_control_fast};
    ble_tlm_data[1535:1520]<=captured_control_fast ? 16'd1000:16'd5000;
    ble_tlm_data[1551:1536]<=captured_control_fast ? 16'd5000:16'd25000;
    ble_tlm_data[1567:1552]<=16'd5000;ble_tlm_data[1583:1568]<=16'd25000;ble_tlm_data[1599:1584]<=16'd5000;
    ble_tlm_data[1631:1600]<=captured_batch_sequence;
    ble_tlm_data[1663:1632]<=completed_angle_d_epoch;ble_tlm_data[1695:1664]<=completed_position_d_epoch;
    ble_tlm_data[1727:1696]<=completed_position_p_epoch;ble_tlm_data[1759:1728]<=completed_sample_epoch;
    ble_tlm_data[1791:1760]<=completed_gain_revision;
   end
  end
 end
endmodule
