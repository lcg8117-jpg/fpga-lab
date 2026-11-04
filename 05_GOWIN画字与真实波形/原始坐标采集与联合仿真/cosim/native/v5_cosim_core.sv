`timescale 1ns/1ps

// Only this adapter schedules transactions. Production RTL remains unchanged.
// A tick is a real20ns clock period; quiescent clock cycles are omitted.
module v5_cosim_core #(
 parameter INIT_FILE="case_init.hex", PID_FILE="pid_state.hex",
 parameter integer BASE_BOARD_MS=0,BASE_EPOCH=0,
 parameter integer POINTS=438,BANK=1,ACTION=7,KIT=1,FAST=0,
 parameter DATA_FILE="program.hex",
 parameter [31:0] CHECKSUM=0
)(
 input signed [15:0] measured_error,fast_error,
 input signed [31:0] measured_raw,measured_raw_now,
 input signed [15:0] measured_guard,
 input [31:0] request_index,
 input [1:0] observation_source_faults,
 input observation_permission,observation_stop,
 input [9:0] observation_adc_raw,
 input [13:0] observation_adc_q4,
 input signed [31:0] raw_ms1,raw_ms2,raw_ms3,raw_ms4,raw_ms5,
 input [31:0] age_ms1,age_ms2,age_ms3,age_ms4,age_ms5,
 output wire [7:0] outer_fault_code,
 output reg signed [31:0] motor_command=0,
 output wire signed [31:0] target_position,actual_position,position_error,
 output wire [2:0] motion_state,
 output wire fault,trajectory_done,tracking_settled,
 output reg [31:0] completed_transactions=0,accepted_count=0,
 output wire [8:0] draw_index,
 output reg signed [31:0] physical_average_q15_q8=0,applied_snapshot_q15=0,
 output reg signed [31:0] angle_observed=0,
 output wire signed[31:0] prefix1_q8,prefix2_q8,steady_q8,
 output reg signed[31:0] frame_actual=0,frame_target=0,guard_observed=0,
 output wire signed [31:0] reference_velocity,measured_velocity,virtual_input_error,lead_inhibited,raw_edges,edge_budget
);
 reg [31:0] init_words[0:63];
 reg [31:0] pid_words[0:31];
 reg clk=0,rst_n=0,tick_5ms=0,reanchor=0,command_valid=0;
 reg signed [31:0] encoder_raw=0;
 reg cfg_write=0;
 reg [7:0] cfg_id=0;
 reg [31:0] cfg_value=0;
 wire cfg_ok,cfg_error,committed,tracking_mode;
 wire [9:0] draw_length;
 wire signed [15:0] draw_sample;
 reg [15:0] program_data[0:511];
 wire signed [63:0] physical_wide,encoder_wide,target_wide,origin_wide,final_wide,tracking_error;
 wire signed [15:0] encoder_local,target_local;
 wire accepted,rejected,active;
 wire [7:0] reason;
 reg checkpoint_loaded=0;
 wire guard_permission;
 wire effective_permission=checkpoint_loaded ? observation_permission && guard_permission:1'b1;
 wire planner_fault;
 assign fault=planner_fault || (checkpoint_loaded && ((|observation_source_faults) || !effective_permission));
 wire [3:0] planner_phase;
 wire signed [31:0] velocity_q,acceleration_q;
 forest_program_store buffer(.clk(clk),.rst_n(rst_n),.tick_5ms(tick_5ms),
  .upload_permitted(1'b1),.abort(1'b0),.cfg_write(cfg_write),.cfg_id(cfg_id),.cfg_value(cfg_value),
  .cfg_ok(cfg_ok),.cfg_error(cfg_error),.cfg_revision(),.cfg_read_value(),
  .committed(committed),.tracking_mode(tracking_mode),.length(draw_length),.read_address(draw_index),.sample(draw_sample));
 forest_motion_planner #(.VARIANT(5)) planner(
  .task_clear(1'b0),.source_faults(observation_source_faults),.board_ms(BASE_BOARD_MS+completed_transactions),.control_epoch(BASE_EPOCH+completed_transactions/5),.calibration_fault(),
  .clk(clk),.rst_n(rst_n),.tick_5ms(tick_5ms),.reanchor(reanchor),.cancel(observation_stop),.capture_hold(1'b0),
  .balance_active(effective_permission),.kit_ip570(KIT[0]),.encoder_raw(encoder_raw),
  .command_valid(command_valid),.command(3'd0),.action_kind(2'd2),.action_index(ACTION[2:0]),
  .request_id(32'd1),.left_positive(1'b1),.cw_positive(1'b1),
  .draw_valid(committed),.draw_tracking(tracking_mode),.draw_preset_bank(BANK[1:0]),.draw_length(draw_length),.draw_sample(draw_sample),.draw_address(draw_index),
  .upload_hold_ready(),.cfg_write(1'b0),.cfg_id(8'd0),.cfg_value(32'd0),
  .cfg_ok(),.cfg_error(),.cfg_revision(),.cfg_read_value(),
  .enc_wide_physical(physical_wide),.encoder_wide(encoder_wide),
  .target_wide(target_wide),.origin_wide(origin_wide),.final_wide(final_wide),
  .encoder_local(encoder_local),.target_local(target_local),.active(active),.fault(planner_fault),
  .state(motion_state),.direction(),.accepted(accepted),.rejected(rejected),.reason(reason),
  .stop_reason(),.event_count(),.last_request_id(),.last_action(),
  .trajectory_done(trajectory_done),.tracking_settled(tracking_settled),
  .measured_velocity_cps(measured_velocity),.reference_velocity_cps(reference_velocity),
  .tracking_error(tracking_error),.settle_ticks(),.planner_phase(planner_phase),
  .velocity_q(velocity_q),.acceleration_q(acceleration_q));
 reg sample_en=0,prime_first=1;
 reg signed[15:0] source_encoder_sample=10000;
 reg signed[15:0] encoder_sample=10000;
 v5_actuator_interval drive();
 reg [11:0] angle_adc12=3085;
 wire done,busy,base_done,base_busy,fast_done,fast_busy;
 reg fast_sample_en=0;
 wire signed[31:0] moto,balance_pwm,position_pwm,base_moto,base_balance,base_position,fast_moto,fast_balance,fast_position;
 assign done=FAST ? fast_done:base_done;assign busy=base_busy|fast_busy;
 assign moto=FAST ? fast_moto:base_moto;assign balance_pwm=FAST ? fast_balance:base_balance;assign position_pwm=FAST ? fast_position:base_position;

 forest_d1_pid controller(.clk(clk),.rst_n(rst_n),.sample_en(sample_en),
  .integrator_clear(fault || !effective_permission),.prime_history(prime_first),
  .angle_adc12(angle_adc12),.encoder(encoder_sample),.gain_write(1'b0),
  .bkp_bits(32'h42440000),.bki_bits(32'd0),.bkd_bits(32'h437b0000),
  .pkp_bits(32'h41c80000),.pki_bits(32'd0),.pkd_bits(32'h44188000),
  .position_bias_bits(),.position_diff_bits(),.balance_bias_bits(),.angle_diff_bits(),.angle_p_bits(),.angle_i_bits(),.angle_d_bits(),.position_p_bits(),.position_i_bits(),.position_d_bits(),.position_div(),.done(base_done),.moto(base_moto),.balance_pwm(base_balance),.position_pwm(base_position),.target(),.busy(base_busy));

 forest_d1_fast_balance fast_controller(.clk(clk),.rst_n(rst_n),.cfg_rst_n(rst_n),
  .sample_en(sample_en),.fast_sample_en(fast_sample_en),.integrator_clear(fault || !effective_permission),
  .prime_history(prime_first),.run_session(effective_permission),.requested_fast(1'b1),
  .angle_adc12(angle_adc12),.encoder(encoder_sample),.fast_angle(12'd3085+$signed(fast_error)),
  .fast_encoder(encoder_local),.sample_epoch(completed_transactions),.gain_revision(32'd0),
  .gains({32'h44188000,32'd0,32'h41c80000,32'h437b0000,32'd0,32'h42440000}),
  .done(fast_done),.busy(fast_busy),.moto(fast_moto),.balance_pwm(fast_balance),.position_pwm(fast_position));
 reg guard_tick=0;
 reg signed [31:0] guard_raw=0;
 reg [31:0] guard_age=0;
 wire signed [31:0] guard_velocity;
 wire [11:0] safety_mean=(measured_guard< -800 || measured_guard>800) ? 3085+$signed(measured_guard):3085+$signed(measured_error);
 outer_guard_macro guard(.clk(clk),.rst_n(rst_n),.tick_1ms(guard_tick),.kit_ip570(KIT[0]),.kit_changed(1'b0),
  .zero_pulse(1'b0),.run_pulse(1'b0),.stop_pulse(observation_stop),.clear_pulse(1'b0),.estop(1'b0),
  .adc_raw(observation_adc_raw),.adc_q4(checkpoint_loaded ? observation_adc_q4:init_words[34][13:0]),.adc_otr_qualified(1'b0),
  .encoder_raw(guard_raw),.invalid_count(16'd0),.physical_edge_age_cycles(guard_age),.illegal_edge_seen(1'b0),
  .mapping_valid(1'b1),.mapping_fault(observation_source_faults[1]),.motion_calibration_fault(planner.calibration_fault),
  .core_busy(busy),.acquisition_fault(observation_source_faults[0]),.source_sample_en(sample_en),.source_guard_mean(safety_mean),
  .fault(outer_fault_code),.motor_permitted(guard_permission),.legacy_velocity_q20(guard_velocity));
 task guard_ms(input integer raw,input integer age);begin
  guard_raw=raw;guard_age=age;guard_tick=1;tick();guard_tick=0;repeat(6)tick();
 end endtask
 assign prefix1_q8=drive.prefix1_q8;assign prefix2_q8=drive.prefix2_q8;assign steady_q8=drive.steady_q8;
 assign virtual_input_error=$signed(encoder_local)-$signed(target_local);
 assign lead_inhibited=planner.word_follow.lead_inhibited;
 assign raw_edges=planner.job_edges;
 assign edge_budget=planner.job_budget;
 assign target_position=target_wide[31:0];
 assign actual_position=encoder_wide[31:0];
 assign position_error=tracking_error[31:0];
 integer tick_total=0,fast_launch_cycle,fast_done_cycle,slow_launch_cycle,slow_done_cycle,timingfile;
 task tick;begin #10;clk=1;#10;clk=0;tick_total=tick_total+1;end endtask
 task store(input [7:0] id,input [31:0] value);begin
  cfg_id=id;cfg_value=value;cfg_write=1;tick();
  if(!cfg_ok || cfg_error)$fatal(1,"Word buffer write rejected id=%0d value=%h",id,value);
  cfg_write=0;tick();
 end endtask

 integer k,clocks,edge_count,fh,ret,wa,wr,wm,we,previous_wm,high_cycles,qavg,logfile;
 reg [31:0] previous_request=0;
 task raw_steps(input integer destination);begin
  edge_count=0;
  while(encoder_raw!=destination && edge_count<1000)begin
   encoder_raw=encoder_raw<destination ? encoder_raw+1:encoder_raw-1;
   tick();edge_count=edge_count+1;
  end
  if(encoder_raw!=destination)$fatal(1,"Encoder edge-rate bound exceeded");
  repeat(4)tick();
 end endtask
 task control_step(input integer a,input integer slow);begin
  fast_launch_cycle=-1;fast_done_cycle=-1;slow_launch_cycle=-1;slow_done_cycle=-1;
  if(completed_transactions%5==0)begin
   source_encoder_sample=encoder_local;tick_5ms=1;tick();tick_5ms=0;repeat(128)tick();
   clocks=0;while(planner_phase!=0 && clocks<32)begin tick();clocks=clocks+1;end
   if(planner_phase!=0)$fatal(1,"Planner update timeout");
   repeat(3)tick();
  end
  encoder_sample=source_encoder_sample;angle_adc12=a;
  if(FAST)begin
   raw_steps(measured_raw_now);frame_actual=actual_position;frame_target=target_position;
   if(fast_controller.base_seen)begin
    fast_launch_cycle=tick_total;fast_sample_en=1;tick();fast_sample_en=0;clocks=0;
    while(!fast_done && clocks<5000)begin tick();clocks=clocks+1;end
    if(!fast_done)$fatal(1,"Fast P timeout");fast_done_cycle=tick_total;repeat(8)tick();
   end
  end
  if(slow)begin
   slow_launch_cycle=tick_total;sample_en=1;tick();sample_en=0;clocks=0;
   while(!base_done && clocks<2000)begin tick();clocks=clocks+1;end
   if(!base_done)$fatal(1,"PID timeout");slow_done_cycle=tick_total;repeat(16)tick();
  end
  if(slow)prime_first=0;
 end endtask
 initial begin
  repeat(4)tick();rst_n=1;repeat(1035)tick();
  reanchor=1;tick();reanchor=0;repeat(3)tick();store(8,BANK);
  if(BANK==0)begin
   $readmemh(DATA_FILE,program_data);
   store(7,1);store(0,POINTS);
   for(k=0;k<POINTS;k=k+1)store(1,(k<<16)|{16'd0,program_data[k]});
   store(2,CHECKSUM);
  end
  if(!committed || draw_length!=POINTS || !tracking_mode)$fatal(1,"Program mismatch");
  timingfile=$fopen("transaction_timing.csv","w");
  $fdisplay(timingfile,"ms,fast_launch_cycle,fast_done_cycle,slow_launch_cycle,slow_done_cycle,used_D_epoch,used_position_D_epoch");
  logfile=$fopen("rtl_prefix.csv","w");
  $fdisplay(logfile,"sample,angle,raw,actual,target,moto,physical_average_q15_q8,applied_snapshot_q15,state,fault,lead_inhibited,virtual_error,tracking,completed");
  $readmemh(INIT_FILE,init_words);
  $readmemh(PID_FILE,pid_words);
  if(init_words[0]!==32'h56354331 || pid_words[0]!==32'h50494431)
   $fatal(1,"Explicit current-case checkpoint missing or wrong schema");
  if(pid_words[15]!==0 || pid_words[16]!==0)
   $fatal(1,"Checkpoint must be a verified idle PID state");
  if(init_words[20]!==0 && init_words[20]!==4)
   $fatal(1,"Checkpoint planner is not ready HOLD/SETTLING");
  encoder_raw=init_words[13];
  guard_raw=init_words[13];
  guard.load_known_RUN(init_words[34],init_words[35]);
  planner.coordinates.previous_raw=init_words[12];
  planner.coordinates.anchored=1;
  planner.coordinates.enc_wide_physical={init_words[8],init_words[7]};
  planner.coordinates.quotient_zero={init_words[10],init_words[9]};
  planner.coordinates.remainder=init_words[11];
  planner.coordinates.velocity_tick=init_words[2];
  planner.coordinates.velocity_position={init_words[4],init_words[3]};
  planner.coordinates.measured_velocity_cps=init_words[5];
  planner.coordinates.velocity_valid=init_words[6];
  planner.target_wide={init_words[15],init_words[14]};
  planner.origin_wide={init_words[17],init_words[16]};
  planner.final_wide={init_words[19],init_words[18]};
  planner.state=init_words[20];
  planner.trajectory_done=init_words[21];
  planner.tracking_settled=init_words[22];
  planner.word_follow.trim_q16=init_words[23];
  planner.word_follow.correction=init_words[24];
  planner.word_follow.offset_low_limit=-345-$signed(init_words[24]);
  planner.word_follow.offset_high_limit=345-$signed(init_words[24]);
  planner.word_follow.negative_low_limit=$signed(init_words[24])-346;
  planner.word_follow.negative_high_limit=$signed(init_words[24])+345;
  planner.word_follow.trim_ticks=init_words[25];
  planner.word_follow.phase=init_words[26];
  prime_first=init_words[32];
  for(k=1;k<32;k=k+1)if(pid_words[k]!==0)$fatal(1,"Expected declared reset PID state");
  repeat(4)tick();
  drive.advance($signed(pid_words[11]),init_words[33],1,high_cycles,qavg);
  if(init_words[30]>=5000)$fatal(1,"Invalid native actuator phase");
  repeat(init_words[30])drive.tick();
  checkpoint_loaded=1;
  $display("CURRENT_CASE_CHECKPOINT_LOADED base_ms=%0d epoch=%0d",BASE_BOARD_MS,BASE_EPOCH);
  forever begin
   wait(request_index!=previous_request && request_index!=0);
   previous_request=request_index;
   // Unsafe means are admitted to the protection sideband, not hidden by an early fatal.
   applied_snapshot_q15=drive.diag[18] || !drive.diag[16] ? 0:
      drive.diag[17] ? -$signed({1'b0,drive.diag[15:0]}):$signed({1'b0,drive.diag[15:0]});
   guard_ms(raw_ms5,age_ms5);
   raw_steps(measured_raw_now);
   frame_actual=actual_position;frame_target=target_position;encoder_sample=encoder_local;guard_observed=measured_guard;
   if(completed_transactions==0)begin
    command_valid=1;tick();
    if(!accepted || rejected)$fatal(1,"PLAY rejected reason=%0d",reason);
    accepted_count=accepted_count+1;command_valid=0;tick();
    if(origin_wide!=={init_words[28],init_words[27]})$fatal(1,"PLAY origin mismatch");
   end
   angle_observed=FAST ? fast_error:measured_error;control_step(3085+$signed(measured_error),(completed_transactions%5)==3);
   frame_actual=actual_position;frame_target=target_position;
   motor_command=(fault || !effective_permission || observation_stop) ? 0:moto;
   drive.advance(motor_command,!fault && effective_permission && !observation_stop,1,high_cycles,qavg);
   physical_average_q15_q8=qavg;
   $fdisplay(timingfile,"%0d,%0d,%0d,%0d,%0d,%0d,%0d",completed_transactions,fast_launch_cycle,fast_done_cycle,slow_launch_cycle,slow_done_cycle,fast_controller.f_angle_epoch,fast_controller.f_position_epoch);
   completed_transactions=completed_transactions+1;
   if(completed_transactions%1000==0)$display("F2_PROGRESS sample=%0d state=%0d actual=%0d target=%0d fault=%0d",completed_transactions,motion_state,actual_position,target_position,fault);
   if(request_index<=400)$fdisplay(logfile,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
      request_index,measured_error,measured_raw,actual_position,target_position,motor_command,
      physical_average_q15_q8,applied_snapshot_q15,motion_state,fault,lead_inhibited,virtual_input_error,position_error,completed_transactions);
   if(fault)$display("NATIVE_FAULT ms=%0d outer=%0d first_causes=%h live_causes=%h reason=%0d stopreason=%0d actual=%0d target=%0d low=%0d high=%0d",completed_transactions,outer_fault_code,planner.first_causes,planner.live_causes,planner.reason,planner.stop_reason,actual_position,target_position,planner.envelope_low,planner.envelope_high);
   $fflush(logfile);
  end
 end
endmodule
