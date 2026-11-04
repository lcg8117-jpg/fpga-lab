// A reference planner only: the frozen parallel controller always owns PWM.
module forest_motion_planner # (parameter integer VARIANT=2)(
 input clk,rst_n,tick_5ms,reanchor,cancel,capture_hold,balance_active,kit_ip570,
 input signed [31:0] encoder_raw,
 input command_valid,input [2:0] command,input [1:0] action_kind,
 input [2:0] action_index,input [31:0] request_id,input left_positive,cw_positive,
 input draw_valid,input [9:0] draw_length,input signed [15:0] draw_sample,
 output wire [8:0] draw_address,
 output wire upload_hold_ready,
 input cfg_write,input [7:0] cfg_id,input [31:0] cfg_value,
 output reg cfg_ok,cfg_error,output reg [31:0] cfg_revision,
 output reg [31:0] cfg_read_value,
 output wire signed [63:0] enc_wide_physical,encoder_wide,
 output reg signed [63:0] target_wide,origin_wide,final_wide,
 output wire signed [15:0] encoder_local,target_local,
 output wire active,fault,output reg [2:0] state,
 output reg signed [1:0] direction,output reg accepted,rejected,
 output reg [7:0] reason,stop_reason,output reg [31:0] event_count,last_request_id,
 output reg [7:0] last_action,output reg trajectory_done,tracking_settled,
 output wire signed [31:0] measured_velocity_cps,
 output reg signed [31:0] reference_velocity_cps,
 output wire signed [63:0] tracking_error,output reg [7:0] settle_ticks,
 output reg [3:0] planner_phase,
 output reg signed [31:0] velocity_q,acceleration_q
);
 localparam [2:0] HOLD=0,FINITE=1,CONTINUOUS=2,BRAKING=3,SETTLING=4,
  WAVE_CAPTURE=5,WAVE_PLAY=6;
 wire coordinate_fault,velocity_valid,draw_player_fault,admitted_request;
 reg planner_fault;
 reg [2:0] wave_shape;
 reg [15:0] job_ticks;
 reg [12:0] job_edges;
 reg signed [31:0] observed_raw;
 // A bounded command is not proof of physical tracking or stopping distance.
 reg trajectory_envelope_bad;
 reg signed [63:0] envelope_low,envelope_high;
 wire trajectory_envelope_outside=VARIANT==5 && balance_active &&
  active && (encoder_wide>envelope_high || encoder_wide<envelope_low ||
   target_wide>envelope_high || target_wide<envelope_low ||
   job_ticks>=16'd30000 || job_edges>=(kit_ip570 ? 13'd4160:13'd4320));
 // This new supervisory guard observes each50MHz clock and latches in one
 // clock. Existing STOP/ESTOP and frozen measurement inhibits stay direct.
 // Each accepted job has its own bounded origin; ZERO still clears faults.
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)trajectory_envelope_bad<=0;
  else if(reanchor)trajectory_envelope_bad<=0;
  else if(trajectory_envelope_outside)trajectory_envelope_bad<=1;
 end
 assign fault=coordinate_fault || planner_fault || trajectory_envelope_bad ||
  (active && wave_shape==7 && !draw_valid) || draw_player_fault;
 assign active=state!=HOLD;
 forest_motion_coordinates coordinates(
  .clk(clk),.rst_n(rst_n),.tick_5ms(tick_5ms),.reanchor(reanchor),
  .cancel(cancel),.capture_hold(capture_hold),.balance_active(balance_active),.kit_ip570(kit_ip570),
  .encoder_raw(encoder_raw),.target_wide(target_wide),.enc_wide_physical(enc_wide_physical),
  .encoder_wide(encoder_wide),.encoder_local(encoder_local),.target_local(target_local),
  .fault(coordinate_fault),.tracking_error(tracking_error),
  .measured_velocity_cps(measured_velocity_cps),.velocity_valid(velocity_valid));

 reg [9:0] finite_speed,amplitude;
 reg [15:0] acceleration_limit_q,jerk_limit_q,period_ms;
 reg [13:0] period_ticks;
 reg [3:0] cycles;
 reg positive;
 wire stopped_for_config=!balance_active && !active;
 wire [28:0] finite_speed_product=finite_speed*19'd335544;
 wire [17:0] finite_speed_q=finite_speed_product[27:10];
 always @*begin
  case(cfg_id)
   0:cfg_read_value={22'd0,finite_speed};
   1:case(acceleration_limit_q)16'd131:cfg_read_value=80;16'd262:cfg_read_value=160;
      16'd524:cfg_read_value=320;16'd1049:cfg_read_value=640;default:cfg_read_value=1200;endcase
   2:case(jerk_limit_q)16'd3:cfg_read_value=400;16'd7:cfg_read_value=800;
      16'd13:cfg_read_value=1600;16'd26:cfg_read_value=3200;default:cfg_read_value=12000;endcase
   3:cfg_read_value=600;4:cfg_read_value=200;5:cfg_read_value=12;
   6:cfg_read_value={31'd0,positive};7:cfg_read_value={22'd0,amplitude};
   8:cfg_read_value={16'd0,period_ms};9:cfg_read_value={28'd0,cycles};
   default:cfg_read_value=32'hffffffff;
  endcase
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   finite_speed<=260;acceleration_limit_q<=524;jerk_limit_q<=13;
   amplitude<=416;period_ms<=24000;period_ticks<=4800;cycles<=2;positive<=1;
   cfg_ok<=0;cfg_error<=0;cfg_revision<=0;
  end else begin
   cfg_ok<=0;cfg_error<=0;
   if(cfg_write)begin
    cfg_error<=1;
    if(stopped_for_config)begin
     case(cfg_id)
      0:if(cfg_value>=60 && cfg_value<=260)begin finite_speed<=cfg_value[9:0];cfg_ok<=1;cfg_error<=0;end
      1:case(cfg_value)
       80,160,320:begin
        acceleration_limit_q<=cfg_value==80 ? 131:cfg_value==160 ? 262:cfg_value==320 ? 524:cfg_value==640 ? 1049:1966;
        cfg_ok<=1;cfg_error<=0;end
       default:;
      endcase
      2:case(cfg_value)
       400,800,1600:begin
        jerk_limit_q<=cfg_value==400 ? 3:cfg_value==800 ? 7:cfg_value==1600 ? 13:cfg_value==3200 ? 26:98;
        cfg_ok<=1;cfg_error<=0;end
       default:;
      endcase
      6:if(cfg_value<2)begin positive<=cfg_value[0];cfg_ok<=1;cfg_error<=0;end
      7:if(cfg_value>=10 && cfg_value<=416)begin amplitude<=cfg_value[9:0];cfg_ok<=1;cfg_error<=0;end
      // Exact millisecond choices avoid a wide combinational divider in the
      // 50MHz control domain. These are configuration values, not new clocks.
      8:case(cfg_value)
       16000,20000,24000,30000,40000,48000,60000:begin
        period_ms<=cfg_value[15:0];cfg_ok<=1;cfg_error<=0;
        case(cfg_value)
         4000:period_ticks<=800;5000:period_ticks<=1000;6000:period_ticks<=1200;
         8000:period_ticks<=1600;10000:period_ticks<=2000;12000:period_ticks<=2400;
         16000:period_ticks<=3200;20000:period_ticks<=4000;24000:period_ticks<=4800;
         30000:period_ticks<=6000;40000:period_ticks<=8000;48000:period_ticks<=9600;
         default:period_ticks<=12000;
        endcase
       end
       default:;
      endcase
      9:if(cfg_value>=1 && cfg_value<=2)begin cycles<=cfg_value[3:0];cfg_ok<=1;cfg_error<=0;end
      default:;
     endcase
    end
   end
   if(cfg_ok)cfg_revision<=cfg_revision+1'b1;
  end
 end

 reg [15:0] fraction;
 reg [11:0] settling_age;
 reg [13:0] wave_tick,wave_remainder;
 reg [7:0] wave_phase;
 reg [3:0] wave_cycle;
 reg signed [63:0] wave_goal;
 reg [8:0] draw_index;
 reg [4:0] draw_tick;
 wire [8:0] draw_player_address;
 wire draw_player_valid,draw_player_done;
 wire signed [31:0] draw_position_q16,draw_velocity_q16,draw_acceleration_q16;
 wire draw_clocked_play=VARIANT==5 && state==WAVE_PLAY && wave_shape==7;
 wire draw_player_start=VARIANT==5 && wave_shape==7 && state==WAVE_CAPTURE && tick_5ms &&
  !admitted_request && velocity_q==0 && acceleration_q==0 && target_wide==final_wide;
 wire signed [31:0] signed_draw_position=positive ? draw_position_q16:-draw_position_q16;
 wire signed [31:0] signed_draw_velocity=positive ? draw_velocity_q16:-draw_velocity_q16;
 wire signed [31:0] signed_draw_acceleration=positive ? draw_acceleration_q16:-draw_acceleration_q16;
 wire signed [63:0] draw_position_counts=$signed({{32{signed_draw_position[31]}},signed_draw_position}) >>> 16;
 forest_draw_clocked clocked_draw(.clk(clk),.rst_n(rst_n),.start(draw_player_start),
  .abort(reanchor || cancel || capture_hold || !balance_active ||
   (state!=WAVE_PLAY && !draw_player_start) || wave_shape!=7),
  .tick_5ms(tick_5ms),.committed(draw_valid),.length(draw_length),.sample(draw_sample),
  .acceleration_limit_q(acceleration_limit_q),.jerk_limit_q(jerk_limit_q),
  .read_address(draw_player_address),.active(),.valid(draw_player_valid),.done(draw_player_done),.fault(draw_player_fault),
  .position_q16(draw_position_q16),.velocity_q16(draw_velocity_q16),.acceleration_q16(draw_acceleration_q16));
 assign draw_address=wave_shape==7 ? draw_player_address:draw_index;
 wire signed [63:0] draw_delta={{48{draw_sample[15]}},draw_sample};
 wire [14:0] phase_sum={1'b0,wave_remainder}+15'd256;
 wire signed [15:0] wave_q14;
 wire signed [26:0] wave_product=$signed({1'b0,amplitude})*$signed(wave_q14);
 wire signed [63:0] wave_delta=$signed({{37{wave_product[26]}},wave_product})>>>14;

 reg [31:0] calculation_epoch;
 reg signed [64:0] reference_error_ext,actual_error_ext;
 reg signed [31:0] error_q,desired_velocity,desired_acceleration,next_acceleration,next_velocity;
 reg signed [31:0] jerk_upper,jerk_lower,negative_velocity;
 reg [31:0] error_magnitude,speed_magnitude;
 reg [17:0] prepared_cap;
 reg [11:0] prepared_stop_distance;
 reg [8:0] distance_index;
 reg [7:0] speed_index;
 wire [17:0] table_cap;
 wire [11:0] table_stop_distance;
 forest_motion_tables #(.WITH_WAVES(VARIANT==5)) tables(.clk(clk),.distance_index(distance_index),.speed_index(speed_index),
  .shape(wave_shape),.phase(wave_phase),.reference_cap_q(table_cap),
  .stopping_distance(table_stop_distance),.wave_q14(wave_q14));

 wire signed [63:0] current_goal=VARIANT==5 && state==WAVE_PLAY ? wave_goal:final_wide;
 wire signed [64:0] current_reference_error=
  $signed({current_goal[63],current_goal})-$signed({target_wide[63],target_wide});
 wire signed [64:0] current_actual_error=
  $signed({current_goal[63],current_goal})-$signed({encoder_wide[63],encoder_wide});
 wire signed [31:0] velocity_cps_now=($signed(velocity_q)*32'sd200)>>>16;
 wire [31:0] measured_magnitude=measured_velocity_cps[31] ? -measured_velocity_cps:measured_velocity_cps;
 wire [31:0] reference_magnitude=velocity_cps_now[31] ? -velocity_cps_now:velocity_cps_now;
 wire [31:0] larger_velocity=measured_magnitude>reference_magnitude ? measured_magnitude:reference_magnitude;
 wire [31:0] desired_magnitude=desired_velocity[31] ? -desired_velocity:desired_velocity;
 wire signed [32:0] velocity_error=$signed({desired_velocity[31],desired_velocity})-
   $signed({velocity_q[31],velocity_q});
 wire signed [31:0] proportional_acceleration=velocity_error[32] ?
   -$signed(((-velocity_error)+33'sd63)>>>6):$signed((velocity_error+33'sd63)>>>6);
 wire [15:0] used_acceleration_limit=acceleration_limit_q;
 wire [15:0] used_jerk_limit=jerk_limit_q;
 wire signed [33:0] position_sum=$signed({18'd0,fraction})+
   $signed({{2{next_velocity[31]}},next_velocity});
 wire signed [64:0] integrated_reference=$signed({target_wide[63],target_wide})+
   $signed({{47{position_sum[33]}},position_sum[33:16]});
 wire reference_overflow=integrated_reference[64]!=integrated_reference[63];
 wire settled_now=velocity_valid && tracking_error>=-64'sd5 && tracking_error<=64'sd5 &&
   measured_velocity_cps>=-32'sd10 && measured_velocity_cps<=32'sd10;
 wire capture_arrived=velocity_valid && current_actual_error>=-65'sd5 && current_actual_error<=65'sd5 &&
   measured_velocity_cps>=-32'sd10 && measured_velocity_cps<=32'sd10;
 wire wave_ready=state==HOLD || (state==SETTLING && trajectory_done && velocity_q==0 && acceleration_q==0);
 assign upload_hold_ready=wave_ready;

 reg signed [1:0] requested_direction;
 reg signed [63:0] relative_delta;
 always @*begin
  requested_direction=positive ? 2'sd1:-2'sd1;
  case(command)
   1:if(action_kind==0)requested_direction=cw_positive ? 2'sd1:-2'sd1;
   2:if(action_kind==0)requested_direction=cw_positive ? -2'sd1:2'sd1;
   3:if(action_kind==0)requested_direction=left_positive ? 2'sd1:-2'sd1;
   4:if(action_kind==0)requested_direction=left_positive ? -2'sd1:2'sd1;
   default:;
  endcase
  case(action_index)
   1:relative_delta=173;2:relative_delta=347;3:relative_delta=520;
   4:relative_delta=693;5:relative_delta=867;6:relative_delta=1040;
   default:relative_delta=0;
  endcase
  if(!positive)relative_delta=-relative_delta;
 end
 wire extended_action=(action_kind==1 && VARIANT==4) || (action_kind==2 && VARIANT==5);
 wire legacy_action=action_kind==0 && VARIANT==2 && command>=1 && command<=5;
 wire cancel_action=action_kind==3 && (VARIANT==4 || VARIANT==5);
 wire legal_action=cancel_action || (extended_action &&
  (VARIANT==5 ? (action_index==3 || action_index==4 || action_index==7):(action_index>=1 && action_index<=6))) || legacy_action;
 wire ready_legacy=state==HOLD || (state==SETTLING && trajectory_done && velocity_q==0);
 wire legacy_start=legacy_action && command<5 && ready_legacy;
 wire start_error_bad=tracking_error< -64'sd200 || tracking_error>64'sd200;
 wire signed [64:0] proposed_goal=legacy_start && command<=2 ?
  $signed({target_wide[63],target_wide})+(requested_direction[1] ? -65'sd1040:65'sd1040):
  $signed({encoder_wide[63],encoder_wide})+$signed({relative_delta[63],relative_delta});
 wire goal_overflow=proposed_goal[64]!=proposed_goal[63];
 wire busy_legacy_reject=legacy_action && !ready_legacy && command<5 &&
  !((command==3 || command==4) && requested_direction!=direction);
 wire idle_cancel_reject=(cancel_action || (legacy_action && (command==5 || !ready_legacy))) &&
  (state==BRAKING || ready_legacy);
 // A finished reference may retain a real physical tracking offset. A new
 // explicit job can capture that current position without claiming rest.
 wire fixed_action=VARIANT==5 && action_kind==2 && (action_index==3 || action_index==4);
 wire wave_start_bad=VARIANT==5 && action_kind==2 &&
  (!wave_ready ||
   (action_index==7 && (!draw_valid || draw_length<3 || draw_length>512)));
 assign admitted_request=command_valid && legal_action && !wave_start_bad && !(legacy_start && start_error_bad) &&
  !((extended_action || (legacy_start && command<=2)) && goal_overflow) && !busy_legacy_reject && !idle_cancel_reject;

 // Count every decoded physical edge, including reversals, for each finite job.
 // Coordinates independently reject impossible multi-edge source jumps.
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin job_ticks<=0;job_edges<=0;observed_raw<=0;end
  else begin
   observed_raw<=encoder_raw;
   if(reanchor || (admitted_request && action_kind==2 && !fault && balance_active &&
                   !cancel && !capture_hold))begin job_ticks<=0;job_edges<=0;end
   else if(active)begin
    if(tick_5ms && job_ticks<16'd30000)job_ticks<=job_ticks+1'b1;
    if(encoder_raw!=observed_raw && job_edges<13'd8191)job_edges<=job_edges+1'b1;
   end
  end
 end

 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   target_wide<=0;origin_wide<=0;final_wide<=0;state<=HOLD;direction<=0;
   envelope_low<=-64'sd345;envelope_high<=64'sd345;
   accepted<=0;rejected<=0;reason<=0;stop_reason<=0;event_count<=0;last_request_id<=0;last_action<=0;
   planner_fault<=0;fraction<=0;velocity_q<=0;acceleration_q<=0;reference_velocity_cps<=0;
   trajectory_done<=0;tracking_settled<=0;settle_ticks<=0;settling_age<=0;
   wave_shape<=0;wave_tick<=0;wave_remainder<=0;wave_phase<=0;wave_cycle<=0;wave_goal<=0;
   draw_index<=0;draw_tick<=0;
   calculation_epoch<=0;reference_error_ext<=0;actual_error_ext<=0;error_q<=0;error_magnitude<=0;
   speed_magnitude<=0;distance_index<=0;speed_index<=0;prepared_cap<=0;prepared_stop_distance<=0;
   desired_velocity<=0;desired_acceleration<=0;next_acceleration<=0;next_velocity<=0;planner_phase<=0;
   jerk_upper<=0;jerk_lower<=0;negative_velocity<=0;
  end else begin
   accepted<=0;rejected<=0;
   if(reanchor || cancel || capture_hold)begin
    target_wide<=reanchor ? 64'sd0:encoder_wide;
    origin_wide<=reanchor ? 64'sd0:encoder_wide;final_wide<=reanchor ? 64'sd0:encoder_wide;
    state<=HOLD;direction<=0;planner_fault<=0;fraction<=0;velocity_q<=0;acceleration_q<=0;
    planner_phase<=0;reference_velocity_cps<=0;trajectory_done<=0;tracking_settled<=0;
    settle_ticks<=0;settling_age<=0;wave_shape<=0;wave_tick<=0;wave_remainder<=0;wave_phase<=0;
    wave_cycle<=0;stop_reason<=reanchor || capture_hold ? 7:6;
    draw_index<=0;draw_tick<=0;
    if(command_valid)begin rejected<=1;reason<=6;end
   end else if(fault || !balance_active)begin
    if(trajectory_envelope_bad || draw_player_fault)begin planner_fault<=1;reason<=16;stop_reason<=16;end
    if(active && wave_shape==7 && !draw_valid)begin planner_fault<=1;reason<=17;stop_reason<=17;end
    state<=HOLD;direction<=0;velocity_q<=0;acceleration_q<=0;fraction<=0;planner_phase<=0;
    reference_velocity_cps<=0;trajectory_done<=0;tracking_settled<=0;settle_ticks<=0;
    if(command_valid)begin rejected<=1;reason<=fault ? 8:1;end
   end else begin
    if(command_valid)begin
     if(!legal_action)begin rejected<=1;reason<=4;end
     else if(wave_start_bad)begin rejected<=1;reason<=15;end
     else if(legacy_start && start_error_bad)begin rejected<=1;reason<=12;end
     else if((extended_action || (legacy_start && command<=2)) && goal_overflow)begin rejected<=1;reason<=5;end
     else if(legacy_action && !ready_legacy && command<5 &&
             !((command==3 || command==4) && requested_direction!=direction))begin
      rejected<=1;reason<=requested_direction==direction ? 3:2;
     end else if((cancel_action || (legacy_action && (command==5 || !ready_legacy))) &&
                 (state==BRAKING || ready_legacy))begin rejected<=1;reason<=3;end
     else begin
      accepted<=1;reason<=0;stop_reason<=0;event_count<=event_count+1'b1;
      last_request_id<=request_id;last_action<=action_kind==0 ? {5'd0,command}:
       action_kind==1 ? (8'h10+{5'd0,action_index}):action_kind==2 ? (8'h20+{5'd0,action_index}):8'h30;
      origin_wide<=encoder_wide;
      // Only fixed profiles have the larger, still bounded excursion. The
      // uploaded lettering envelope and every original motor guard remain.
      envelope_low<=encoder_wide-(fixed_action ? 64'sd480:64'sd345);
      envelope_high<=encoder_wide+(fixed_action ? 64'sd480:64'sd345);
      trajectory_done<=0;tracking_settled<=0;settle_ticks<=0;settling_age<=0;
      // Retain reference, velocity and acceleration when replacing a plan.
      if(cancel_action || (legacy_action && (command==5 || !ready_legacy)))begin
       state<=BRAKING;stop_reason<=cancel_action || command==5 ? 14:13;wave_shape<=0;
      end else if(VARIANT==5 && action_kind==2)begin
       state<=WAVE_CAPTURE;wave_shape<=action_index;final_wide<=encoder_wide;wave_goal<=encoder_wide;
       draw_index<=0;draw_tick<=0;
       wave_tick<=0;wave_phase<=0;wave_remainder<=0;wave_cycle<=0;direction<=positive ? 2'sd1:-2'sd1;
      end else if(legacy_start && command>=3)begin
       state<=CONTINUOUS;direction<=requested_direction;wave_shape<=0;
      end else begin
       state<=FINITE;direction<=action_kind==1 ? (positive ? 2'sd1:-2'sd1):requested_direction;
       final_wide<=proposed_goal[63:0];wave_shape<=0;
      end
      // Recompute a pending tick instead of committing an obsolete goal.
      if(planner_phase!=0 || tick_5ms)planner_phase<=1;
     end
    end
    if(tick_5ms && !admitted_request)begin
     if(state==HOLD || state==SETTLING)begin
      if(trajectory_done)begin
       if(settled_now)begin
        if(settle_ticks<100)settle_ticks<=settle_ticks+1'b1;
        if(settle_ticks>=99)begin tracking_settled<=1;state<=HOLD;stop_reason<=10;end
       end else begin settle_ticks<=0;tracking_settled<=0;if(state==HOLD)state<=SETTLING;end
       if(settling_age<3000)settling_age<=settling_age+1'b1;
       if(settling_age>=2999 && !settled_now)stop_reason<=11;
      end
     end else if(VARIANT==5 && state==WAVE_CAPTURE && velocity_q==0 && acceleration_q==0 &&
                 target_wide==final_wide)begin
      // Align only the bounded reference to this job origin. Measured arm
      // speed is feedback for braking, not a gate requiring physical rest.
      state<=WAVE_PLAY;settle_ticks<=0;wave_goal<=origin_wide;
     end else if(!draw_clocked_play)begin
      if(state==WAVE_CAPTURE)settle_ticks<=0;
      if(planner_phase==0)planner_phase<=1;
     end
     if(VARIANT==5 && state==WAVE_PLAY)begin
      if(wave_shape==7)begin
       // The uploaded C2 reference owns its clock; do not run the finite
       // destination/terminal-creep algorithm on each lettering sample.
      end else begin
       wave_goal<=positive ? origin_wide+wave_delta:origin_wide-wave_delta;
       if(wave_tick+1>=period_ticks)begin
       wave_tick<=0;wave_phase<=0;wave_remainder<=0;
       if(wave_cycle+1>=cycles)begin state<=FINITE;final_wide<=origin_wide;wave_goal<=origin_wide;wave_shape<=0;end
       else wave_cycle<=wave_cycle+1'b1;
      end else begin
       wave_tick<=wave_tick+1'b1;
       if(phase_sum>={1'b0,period_ticks})begin
        wave_remainder<=phase_sum-{1'b0,period_ticks};wave_phase<=wave_phase+1'b1;
       end else wave_remainder<=phase_sum[13:0];
       end
      end
     end
    end
    if(draw_clocked_play && draw_player_valid && !admitted_request)begin
     target_wide<=origin_wide+draw_position_counts;fraction<=signed_draw_position[15:0];
     wave_goal<=origin_wide+draw_position_counts;
     velocity_q<=signed_draw_velocity;acceleration_q<=signed_draw_acceleration;
     reference_velocity_cps<=($signed(signed_draw_velocity)*32'sd200)>>>16;
     planner_phase<=0;
     if(draw_player_done)begin
      state<=FINITE;final_wide<=origin_wide;wave_goal<=origin_wide;wave_shape<=0;
     end
    end
    if(!admitted_request && !draw_clocked_play)begin
     case(planner_phase)
      1:begin
       calculation_epoch<=event_count;reference_error_ext<=current_reference_error;actual_error_ext<=current_actual_error;
       negative_velocity<=-velocity_q;
       speed_magnitude<=larger_velocity;
       // ROM addresses use the same values captured by this phase; the
       // synchronous result is ready for the existing phase3 consumer.
       distance_index<=(current_reference_error>2047 || current_reference_error< -2047) ? 9'd511:
        (current_reference_error[64] ? (-current_reference_error)>>2:current_reference_error>>2);
       speed_index<=larger_velocity>=4096 ? 8'd255:larger_velocity[11:4];planner_phase<=2;
      end
      2:begin
       if(reference_error_ext>4095)error_q<=32'sh0fffffff;
       else if(reference_error_ext< -4095)error_q<=-32'sh0fffffff;
       else error_q<=($signed(reference_error_ext[31:0])<<<16)-$signed({16'd0,fraction});
       planner_phase<=3;
      end
      3:begin
       prepared_cap<=table_cap>finite_speed_q ? finite_speed_q:table_cap;
       prepared_stop_distance<=table_stop_distance;error_magnitude<=error_q[31] ? -error_q:error_q;
       planner_phase<=4;
      end
      4:begin
       if(state==CONTINUOUS)desired_velocity<=direction[1] ? -32'sd170394:32'sd170394;
       else if(state==BRAKING)desired_velocity<=0;
       else if(error_magnitude<32'd2097152)begin
        // Terminal creep is small and exponentially approaches the exact goal.
        if(measured_magnitude>40 && state!=WAVE_PLAY && state!=WAVE_CAPTURE)desired_velocity<=0;
        else if(error_q>32'sd419430)desired_velocity<=32'sd6553;
        else if(error_q< -32'sd419430)desired_velocity<=-32'sd6553;
        else desired_velocity<=error_q[31] ? -$signed((-error_q)>>>6):error_q>>>6;
       end else if(state!=WAVE_PLAY && state!=WAVE_CAPTURE &&
          ((actual_error_ext>=0 && !measured_velocity_cps[31] && measured_velocity_cps>0 && actual_error_ext<=$signed({53'd0,prepared_stop_distance})) ||
           (actual_error_ext<0 && measured_velocity_cps[31] && -actual_error_ext<=$signed({53'd0,prepared_stop_distance}))))begin
        desired_velocity<=0;
       end else desired_velocity<=error_q[31] ? -$signed({14'd0,prepared_cap}):$signed({14'd0,prepared_cap});
       planner_phase<=5;
      end
      5:begin
       jerk_upper<=acceleration_q+$signed({16'd0,used_jerk_limit});jerk_lower<=acceleration_q-$signed({16'd0,used_jerk_limit});
       if(proportional_acceleration>$signed({16'd0,used_acceleration_limit}))desired_acceleration<=$signed({16'd0,used_acceleration_limit});
       else if(proportional_acceleration< -$signed({16'd0,used_acceleration_limit}))desired_acceleration<=-$signed({16'd0,used_acceleration_limit});
       else desired_acceleration<=proportional_acceleration;
       planner_phase<=6;
      end
      6:begin
       if(desired_acceleration>jerk_upper)next_acceleration<=jerk_upper;
       else if(desired_acceleration<jerk_lower)next_acceleration<=jerk_lower;
       else next_acceleration<=desired_acceleration;
       planner_phase<=7;
      end
      7:begin
       if(desired_velocity==0 && velocity_q<=7 && velocity_q>=-7 &&
          negative_velocity<=jerk_upper && negative_velocity>=jerk_lower)begin
        next_velocity<=0;next_acceleration<=negative_velocity;
       end else next_velocity<=velocity_q+next_acceleration;
       planner_phase<=8;
      end
      8:begin
       if(calculation_epoch!=event_count)planner_phase<=1;
       else begin
        planner_phase<=0;
        if(reference_overflow || (VARIANT==5 &&
           ($signed(integrated_reference)>$signed({envelope_high[63],envelope_high}) || $signed(integrated_reference)<$signed({envelope_low[63],envelope_low}) ||
            next_velocity>32'sd85196 || next_velocity< -32'sd85196)))begin
         planner_fault<=1;reason<=16;stop_reason<=16;state<=HOLD;velocity_q<=0;acceleration_q<=0;
        end
        else begin
         target_wide<=integrated_reference[63:0];fraction<=position_sum[15:0];
         velocity_q<=next_velocity;acceleration_q<=next_acceleration;
         reference_velocity_cps<=($signed(next_velocity)*32'sd200)>>>16;
         if(state==BRAKING && next_velocity==0 && next_acceleration==0)begin
          final_wide<=integrated_reference[63:0];fraction<=0;state<=SETTLING;direction<=0;
          trajectory_done<=1;settle_ticks<=0;settling_age<=0;
         end else if(state!=CONTINUOUS && state!=WAVE_PLAY && state!=BRAKING &&
                     next_velocity==0 && next_acceleration==0 && error_magnitude<=64)begin
          target_wide<=final_wide;fraction<=0;direction<=0;
          if(state!=WAVE_CAPTURE)begin state<=SETTLING;trajectory_done<=1;stop_reason<=9;settle_ticks<=0;settling_age<=0;end
         end
        end
       end
      end
      default:;
     endcase
    end
   end
  end
 end
endmodule
