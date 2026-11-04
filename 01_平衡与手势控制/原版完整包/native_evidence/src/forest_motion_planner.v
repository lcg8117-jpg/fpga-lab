// A reference planner only: the frozen parallel controller always owns PWM.
module forest_motion_planner # (parameter integer VARIANT=2)(
 input clk,rst_n,tick_5ms,reanchor,cancel,capture_hold,balance_active,kit_ip570,
 input signed [31:0] encoder_raw,
 input command_valid,input [2:0] command,input [1:0] action_kind,
 input [2:0] action_index,input [31:0] request_id,input left_positive,cw_positive,
 input cfg_write,input [7:0] cfg_id,input [31:0] cfg_value,
 output reg cfg_ok,cfg_error,output reg [31:0] cfg_revision,
 output reg [31:0] cfg_read_value,
 output wire signed [63:0] enc_wide_physical,encoder_wide,
 output wire signed [63:0] target_wide,
 output reg signed [63:0] origin_wide,final_wide,
 output wire signed [15:0] encoder_local,target_local,
 output wire active,fault,motion_guard_active,output reg [2:0] state,
 output reg signed [1:0] direction,output reg accepted,rejected,
 output reg [7:0] reason,stop_reason,output reg [31:0] event_count,last_request_id,
 output reg [7:0] last_action,output wire trajectory_done,output reg tracking_settled,
 output wire signed [31:0] measured_velocity_cps,output wire velocity_valid,
 output reg signed [31:0] reference_velocity_cps,
 output wire signed [31:0] controller_velocity_q,controller_acceleration_q,
 output wire signed [63:0] tracking_error,output reg [7:0] settle_ticks,
 output reg [3:0] planner_phase,
 output reg signed [31:0] velocity_q,acceleration_q
);
 localparam [2:0] HOLD=0,FINITE=1,CONTINUOUS=2,BRAKING=3,SETTLING=4,
  WAVE_CAPTURE=5,WAVE_PLAY=6;
 wire coordinate_fault;
 reg planner_fault;
 wire shape_fault;
 reg signed [63:0] nominal_target_wide;
 reg nominal_done,shape_mode;
 reg numerical_seed_nominal;
 wire signed [63:0] shaped_target;
 wire signed [31:0] shaped_velocity_q,shaped_acceleration_q;
 reg numerical_cancel,numerical_rebase,numerical_finite_start;
 reg signed [63:0] numerical_reset_target;
 reg signed [31:0] nominal_reference_velocity_cps;
 // Isolate the finite/continuous selector from the wide coordinate arithmetic.
 // This cache settles one50MHz clock after the generator, well before the
 // next5ms control transaction; no measurement or physical STOP is delayed.
 reg signed [63:0] selected_target;
 reg signed [31:0] selected_velocity,selected_acceleration;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin selected_target<=0;selected_velocity<=0;selected_acceleration<=0;end
  else begin
   selected_target<=VARIANT==2 && shape_mode ? shaped_target:nominal_target_wide;
   selected_velocity<=VARIANT==2 && shape_mode ? shaped_velocity_q:velocity_q;
   selected_acceleration<=VARIANT==2 && shape_mode ? shaped_acceleration_q:acceleration_q;
  end
 end
 assign target_wide=selected_target;
 assign controller_velocity_q=selected_velocity;
 assign controller_acceleration_q=selected_acceleration;
 assign fault=coordinate_fault || planner_fault || (shape_mode && shape_fault);
 // Exact modulo32 scaling, split before the sign-preserving shift. These
 // numerical caches settle within40ns; the planner consumes them5ms apart.
 // Keep the wide planner control enables off a DSP input-register path.
 reg signed [31:0] selected_scale192,selected_scale8;
 reg signed [31:0] nominal_scale192,nominal_scale8;
 reg signed [31:0] velocity_cps_now;
 wire signed [31:0] selected_scale_sum=selected_scale192+selected_scale8;
 wire signed [31:0] nominal_scale_sum=nominal_scale192+nominal_scale8;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   selected_scale192<=0;selected_scale8<=0;nominal_scale192<=0;nominal_scale8<=0;
   reference_velocity_cps<=0;velocity_cps_now<=0;
  end else begin
   selected_scale192<=($signed(controller_velocity_q)<<<7)+($signed(controller_velocity_q)<<<6);
   selected_scale8<=$signed(controller_velocity_q)<<<3;
   nominal_scale192<=($signed(velocity_q)<<<7)+($signed(velocity_q)<<<6);
   nominal_scale8<=$signed(velocity_q)<<<3;
   reference_velocity_cps<=selected_scale_sum>>>16;
   velocity_cps_now<=nominal_scale_sum>>>16;
  end
 end
 assign trajectory_done=nominal_done && (VARIANT!=2 ||
   (target_wide==nominal_target_wide && controller_velocity_q==0));
 generate if(VARIANT==2)begin:reference_preconditioning
  forest_motion_reference_shape shape(.clk(clk),.rst_n(rst_n),.tick(tick_5ms),
   .rebase(numerical_rebase),
   .stationary(velocity_q==0 && acceleration_q==0),
   .reset_target(numerical_seed_nominal ? nominal_target_wide:numerical_reset_target),.nominal_target(nominal_target_wide),
   .target(shaped_target),.velocity_q(shaped_velocity_q),
   .acceleration_q(shaped_acceleration_q),.fault(shape_fault));
 end else begin:no_preconditioning
  assign shape_fault=0;
 end endgenerate
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
 reg [10:0] start_guard_ms;
 reg [8:0] start_guard_ticks,start_guard_remaining;
 assign motion_guard_active=VARIANT==2 && start_guard_remaining!=0;
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
   10:cfg_read_value={21'd0,start_guard_ms};
   default:cfg_read_value=32'hffffffff;
  endcase
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   finite_speed<=VARIANT==2 ? 520:260;acceleration_limit_q<=524;jerk_limit_q<=13;
   amplitude<=87;period_ms<=16000;period_ticks<=3200;cycles<=2;positive<=1;
   cfg_ok<=0;cfg_error<=0;cfg_revision<=0;
   start_guard_ms<=1000;start_guard_ticks<=201;
  end else begin
   cfg_ok<=0;cfg_error<=0;
   if(cfg_write)begin
    cfg_error<=1;
    if(stopped_for_config)begin
     case(cfg_id)
      0:if(cfg_value>=60 && cfg_value<=520)begin finite_speed<=cfg_value[9:0];cfg_ok<=1;cfg_error<=0;end
      1:case(cfg_value)
       80,160,320,640,1200:begin
        acceleration_limit_q<=cfg_value==80 ? 131:cfg_value==160 ? 262:cfg_value==320 ? 524:cfg_value==640 ? 1049:1966;
        cfg_ok<=1;cfg_error<=0;end
       default:;
      endcase
      2:case(cfg_value)
       400,800,1600,3200,12000:begin
        jerk_limit_q<=cfg_value==400 ? 3:cfg_value==800 ? 7:cfg_value==1600 ? 13:cfg_value==3200 ? 26:98;
        cfg_ok<=1;cfg_error<=0;end
       default:;
      endcase
      6:if(cfg_value<2)begin positive<=cfg_value[0];cfg_ok<=1;cfg_error<=0;end
      7:if(cfg_value>=10 && cfg_value<=520)begin amplitude<=cfg_value[9:0];cfg_ok<=1;cfg_error<=0;end
      // Exact millisecond choices avoid a wide combinational divider in the
      // 50MHz control domain. These are configuration values, not new clocks.
      8:case(cfg_value)
       4000,5000,6000,8000,10000,12000,16000,20000,24000,30000,40000,48000,60000:begin
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
      9:if(cfg_value>=1 && cfg_value<=8)begin cycles<=cfg_value[3:0];cfg_ok<=1;cfg_error<=0;end
      10:case(cfg_value)
       0,200,300,500,750,1000,1500,2000:begin
        start_guard_ms<=cfg_value[10:0];cfg_ok<=1;cfg_error<=0;
        case(cfg_value)
         0:start_guard_ticks<=0;200:start_guard_ticks<=41;300:start_guard_ticks<=61;
         500:start_guard_ticks<=101;750:start_guard_ticks<=151;1000:start_guard_ticks<=201;
         1500:start_guard_ticks<=301;default:start_guard_ticks<=401;
        endcase
       end
       default:;
      endcase
      default:;
     endcase
    end
   end
   if(cfg_ok)cfg_revision<=cfg_revision+1'b1;
  end
 end

 reg [15:0] fraction;
 reg [11:0] settling_age;
 reg [2:0] wave_shape;
 reg [13:0] wave_tick,wave_remainder;
 reg [7:0] wave_phase;
 reg [3:0] wave_cycle;
 reg signed [63:0] wave_goal;
 wire [14:0] phase_sum={1'b0,wave_remainder}+15'd256;
 wire signed [15:0] wave_q14;
 wire signed [26:0] wave_product=$signed({1'b0,amplitude})*$signed(wave_q14);
 wire signed [63:0] wave_delta={{37{wave_product[26]}},wave_product}>>>14;

 reg [31:0] calculation_epoch;
 reg signed [64:0] reference_error_ext,actual_error_ext;
 reg signed [31:0] error_q,desired_velocity,desired_acceleration,next_acceleration,next_velocity;
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
  $signed({current_goal[63],current_goal})-$signed({nominal_target_wide[63],nominal_target_wide});
 wire signed [64:0] current_actual_error=
  $signed({current_goal[63],current_goal})-$signed({encoder_wide[63],encoder_wide});
 wire [31:0] measured_magnitude=measured_velocity_cps[31] ? -measured_velocity_cps:measured_velocity_cps;
 wire [31:0] reference_magnitude=velocity_cps_now[31] ? -velocity_cps_now:velocity_cps_now;
 wire [31:0] larger_velocity=measured_magnitude>reference_magnitude ? measured_magnitude:reference_magnitude;
 wire [31:0] desired_magnitude=desired_velocity[31] ? -desired_velocity:desired_velocity;
 wire signed [32:0] velocity_error=$signed({desired_velocity[31],desired_velocity})-
   $signed({velocity_q[31],velocity_q});
 wire signed [31:0] proportional_acceleration=velocity_error[32] ?
   -$signed(((-velocity_error)+33'sd63)>>>6):$signed((velocity_error+33'sd63)>>>6);
 wire [15:0] used_acceleration_limit=state==BRAKING ? 16'd1966:acceleration_limit_q;
 wire [15:0] used_jerk_limit=state==BRAKING ? 16'd98:jerk_limit_q;
 wire signed [31:0] terminal_negative_velocity=-velocity_q;
 wire signed [31:0] terminal_upper_bound=acceleration_q+$signed({16'd0,used_jerk_limit});
 wire signed [31:0] terminal_lower_bound=acceleration_q-$signed({16'd0,used_jerk_limit});
 wire terminal_at_most_upper,terminal_at_least_lower;
 forest_motion_signed_le32 terminal_upper(.left(terminal_negative_velocity),
  .right(terminal_upper_bound),.result(terminal_at_most_upper));
 forest_motion_signed_le32 terminal_lower(.left(terminal_lower_bound),
  .right(terminal_negative_velocity),.result(terminal_at_least_lower));
 wire signed [33:0] position_sum=$signed({18'd0,fraction})+
   $signed({{2{next_velocity[31]}},next_velocity});
 wire signed [64:0] integrated_reference=$signed({nominal_target_wide[63],nominal_target_wide})+
   $signed({{47{position_sum[33]}},position_sum[33:16]});
 wire reference_overflow=integrated_reference[64]!=integrated_reference[63];
 wire settled_now=velocity_valid &&
   (VARIANT==2 ? ($signed(encoder_local)>=16'sd9995 && $signed(encoder_local)<=16'sd10005):
    (tracking_error>=-64'sd5 && tracking_error<=64'sd5)) &&
   measured_velocity_cps>=-32'sd10 && measured_velocity_cps<=32'sd10;
 wire capture_arrived=velocity_valid && current_actual_error>=-65'sd5 && current_actual_error<=65'sd5 &&
   measured_velocity_cps>=-32'sd10 && measured_velocity_cps<=32'sd10;

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
 wire extended_action=(action_kind==1 && (VARIANT==4 || VARIANT==5)) || (action_kind==2 && VARIANT==5);
 wire legacy_action=action_kind==0 && VARIANT==2 && command>=1 && command<=5;
 wire cancel_action=action_kind==3 && (VARIANT==4 || VARIANT==5);
 wire legal_action=cancel_action || (extended_action && action_index>=1 && action_index<=6) || legacy_action;
 // Tracking quality is reported independently. A physical error or a noisy
 // velocity estimate must never hold the next task hostage after reference end.
 wire ready_legacy=state==HOLD || (state==SETTLING && nominal_done && velocity_q==0 &&
  acceleration_q==0);
 wire legacy_start=legacy_action && command<5 && ready_legacy;
 wire start_error_bad=VARIANT==2 ?
  ($signed(encoder_local)<16'sd9800 || $signed(encoder_local)>16'sd10200):
  (tracking_error< -64'sd200 || tracking_error>64'sd200);
 // Prepare both constant sums before command selection, keeping that
 // late control bit outside the wide carry chain. No new clock boundary.
 wire signed [64:0] legacy_forward_goal /* synthesis syn_keep=1 */;
 wire signed [64:0] legacy_reverse_goal /* synthesis syn_keep=1 */;
 assign legacy_forward_goal=$signed({nominal_target_wide[63],nominal_target_wide})+65'sd1040;
 assign legacy_reverse_goal=$signed({nominal_target_wide[63],nominal_target_wide})-65'sd1040;
 wire signed [64:0] proposed_goal=legacy_start && command<=2 ?
  (requested_direction[1] ? legacy_reverse_goal:legacy_forward_goal):
  $signed({encoder_wide[63],encoder_wide})+$signed({relative_delta[63],relative_delta});
 wire goal_overflow=proposed_goal[64]!=proposed_goal[63];
 wire busy_legacy_reject=legacy_action && !ready_legacy && command<5 &&
  !((command==3 || command==4) && requested_direction!=direction);
 reg queued_valid;
 reg [2:0] queued_command;
 reg signed [1:0] queued_direction;
 reg [31:0] queued_request_id;
 wire idle_cancel_reject=(cancel_action || (legacy_action && (command==5 || !ready_legacy))) &&
  (state==BRAKING || ready_legacy) && !queued_valid;
 wire queue_needed=VARIANT==2 && legacy_action && command<5 &&
  (queued_valid || state==FINITE || state==BRAKING || (state==SETTLING && !ready_legacy));
 wire start_guard_reject=VARIANT==2 && start_guard_remaining!=0 && legacy_action && command<5;
 // After the explicitly requested command interval, the newest next-task
 // intent replaces an older pending intent instead of an indefinite busy gate.
 wire queue_submit=command_valid && !start_guard_reject && queue_needed;
 wire admitted_regular=command_valid && !start_guard_reject && !queue_needed && legal_action && !(legacy_start && start_error_bad) &&
  !((extended_action || (legacy_start && command<=2)) && goal_overflow) && !busy_legacy_reject && !idle_cancel_reject;
 wire admitted_request=queue_submit || admitted_regular;
 wire dispatch_pending=VARIANT==2 && queued_valid && ready_legacy && !command_valid &&
  balance_active && !fault && !cancel && !reanchor && !capture_hold;
 wire signed [64:0] queued_goal=queued_direction[1] ? legacy_reverse_goal:legacy_forward_goal;
 wire queued_goal_bad=queued_command<=2 && queued_goal[64]!=queued_goal[63];
 reg finite_reference;
 reg signed [63:0] finite_origin;
 wire finite_start=VARIANT==2 && ((admitted_regular && legacy_start && command<=2) ||
  (dispatch_pending && queued_command<=2 && !queued_goal_bad && !start_error_bad));
 // Only an explicit motion STOP can interrupt an active finite plan. Other
 // movement requests are rejected by the start guard or stored, never cancel.
 wire finite_cancel=reanchor || cancel || capture_hold || fault || !balance_active ||
  (command_valid && legacy_action && command==5);
 wire finite_active,finite_ready,finite_sample,finite_done,finite_fault;
 wire [31:0] finite_offset,finite_velocity;
 wire signed [31:0] finite_acceleration;
 wire [17:0] finite_requested_speed=finite_speed==520 ? 18'd170394:finite_speed_q;
 forest_motion_finite_scurve one_turn(.clk(clk),.rst_n(rst_n),.start(numerical_finite_start),
  .cancel(numerical_cancel || (command_valid && legacy_action && command==5)),.tick(tick_5ms && finite_reference),.speed_q16(finite_requested_speed),
  .acceleration_limit_q16(acceleration_limit_q),.jerk_limit_q16(jerk_limit_q),
  .active(finite_active),.ready(finite_ready),.sample_valid(finite_sample),.finished(finite_done),.fault(finite_fault),
  .offset_q16(finite_offset),.velocity_q16(finite_velocity),.acceleration_q16(finite_acceleration));
 wire [16:0] finite_integer_magnitude={1'b0,finite_offset[31:16]}+
  ((direction[1] && finite_offset[15:0]!=0) ? 17'd1:17'd0);
 wire signed [64:0] finite_reference_position=direction[1] ?
  $signed({finite_origin[63],finite_origin})-$signed({48'd0,finite_integer_magnitude}):
  $signed({finite_origin[63],finite_origin})+$signed({48'd0,finite_integer_magnitude});
 wire signed [31:0] finite_signed_velocity=direction[1] ? -$signed(finite_velocity):$signed(finite_velocity);
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin numerical_cancel<=1;numerical_rebase<=1;numerical_seed_nominal<=0;numerical_finite_start<=0;numerical_reset_target<=0;end
  else begin
   numerical_cancel<=reanchor || cancel || capture_hold || fault || !balance_active;
   numerical_rebase<=reanchor || cancel || capture_hold || !balance_active || !shape_mode;
   numerical_seed_nominal<=!shape_mode && !(reanchor || cancel || capture_hold || !balance_active);
   numerical_finite_start<=finite_start;
   numerical_reset_target<=reanchor ? 64'sd0:encoder_wide;
  end
 end
 // Continuous motion and opposite-swipe brake deliberately retain the
 // qualified original reference path. The single-turn preconditioner is not
 // inserted into an otherwise-good continuous stop.
 // Exactly one interval per accepted command. Internal queue dispatch must
 // not silently add a second interval. Rejected repeats never renew it.
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)start_guard_remaining<=0;
  else if(reanchor || cancel || capture_hold || fault || !balance_active ||
          (command_valid && legacy_action && command==5))start_guard_remaining<=0;
  else if(queue_submit || (admitted_regular && legacy_action && command<5))
   start_guard_remaining<=start_guard_ticks;
  else if(tick_5ms && start_guard_remaining!=0)start_guard_remaining<=start_guard_remaining-1'b1;
 end

 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   nominal_target_wide<=0;origin_wide<=0;final_wide<=0;state<=HOLD;direction<=0;
   accepted<=0;rejected<=0;reason<=0;stop_reason<=0;event_count<=0;last_request_id<=0;last_action<=0;
   planner_fault<=0;fraction<=0;velocity_q<=0;acceleration_q<=0;nominal_reference_velocity_cps<=0;
   nominal_done<=0;tracking_settled<=0;settle_ticks<=0;settling_age<=0;
   wave_shape<=0;wave_tick<=0;wave_remainder<=0;wave_phase<=0;wave_cycle<=0;wave_goal<=0;
   calculation_epoch<=0;reference_error_ext<=0;actual_error_ext<=0;error_q<=0;error_magnitude<=0;
   speed_magnitude<=0;distance_index<=0;speed_index<=0;prepared_cap<=0;prepared_stop_distance<=0;
   desired_velocity<=0;desired_acceleration<=0;next_acceleration<=0;next_velocity<=0;planner_phase<=0;
   queued_valid<=0;queued_command<=0;queued_direction<=0;queued_request_id<=0;
   finite_reference<=0;finite_origin<=0;shape_mode<=0;
  end else begin
   accepted<=0;rejected<=0;
   if(reanchor || cancel || capture_hold)begin
    queued_valid<=0;finite_reference<=0;shape_mode<=0;
    nominal_target_wide<=reanchor ? 64'sd0:encoder_wide;
    origin_wide<=reanchor ? 64'sd0:encoder_wide;final_wide<=reanchor ? 64'sd0:encoder_wide;
    state<=HOLD;direction<=0;planner_fault<=0;fraction<=0;velocity_q<=0;acceleration_q<=0;
    planner_phase<=0;nominal_reference_velocity_cps<=0;nominal_done<=0;tracking_settled<=0;
    settle_ticks<=0;settling_age<=0;wave_shape<=0;wave_tick<=0;wave_remainder<=0;wave_phase<=0;
    wave_cycle<=0;stop_reason<=reanchor || capture_hold ? 7:6;
    if(command_valid)begin rejected<=1;reason<=6;end
   end else if(fault || !balance_active)begin
    queued_valid<=0;finite_reference<=0;shape_mode<=0;
    state<=HOLD;direction<=0;velocity_q<=0;acceleration_q<=0;fraction<=0;planner_phase<=0;
    nominal_reference_velocity_cps<=0;nominal_done<=0;tracking_settled<=0;settle_ticks<=0;
    if(command_valid)begin rejected<=1;reason<=fault ? 8:1;end
   end else begin
    if(command_valid)begin
     if(start_guard_reject)begin rejected<=1;reason<=17;end
     else if(queue_needed)begin
       queued_valid<=1;queued_command<=command;queued_direction<=requested_direction;queued_request_id<=request_id;
       accepted<=1;reason<=16;event_count<=event_count+1'b1;
       last_request_id<=request_id;last_action<={5'd0,command};
     end else if(!legal_action)begin rejected<=1;reason<=4;end
     else if(legacy_start && start_error_bad)begin rejected<=1;reason<=12;end
     else if((extended_action || (legacy_start && command<=2)) && goal_overflow)begin rejected<=1;reason<=5;end
     else if(legacy_action && !ready_legacy && command<5 &&
             !((command==3 || command==4) && requested_direction!=direction))begin
      rejected<=1;reason<=requested_direction==direction ? 3:2;
     end else if((cancel_action || (legacy_action && (command==5 || !ready_legacy))) &&
                 (state==BRAKING || ready_legacy) && !queued_valid)begin rejected<=1;reason<=3;end
     else begin
      accepted<=1;reason<=0;stop_reason<=0;event_count<=event_count+1'b1;
      last_request_id<=request_id;last_action<=action_kind==0 ? {5'd0,command}:
       action_kind==1 ? (8'h10+{5'd0,action_index}):action_kind==2 ? (8'h20+{5'd0,action_index}):8'h30;
      origin_wide<=encoder_wide;nominal_done<=0;tracking_settled<=0;settle_ticks<=0;settling_age<=0;
      // Retain reference, velocity and acceleration when replacing a plan.
      if(cancel_action || (legacy_action && (command==5 || !ready_legacy)))begin
       queued_valid<=0;finite_reference<=0;
       if(VARIANT==2 && shape_mode)begin
        nominal_target_wide<=target_wide;fraction<=0;
        velocity_q<=controller_velocity_q;acceleration_q<=controller_acceleration_q;
       end
       shape_mode<=0;
       state<=BRAKING;stop_reason<=cancel_action || command==5 ? 14:13;wave_shape<=0;
      end else if(VARIANT==5 && action_kind==2)begin
       state<=WAVE_CAPTURE;wave_shape<=action_index;final_wide<=encoder_wide;wave_goal<=encoder_wide;
       wave_tick<=0;wave_phase<=0;wave_remainder<=0;wave_cycle<=0;direction<=positive ? 2'sd1:-2'sd1;
      end else if(legacy_start && command>=3)begin
       finite_reference<=0;
       if(VARIANT==2 && shape_mode)begin
        nominal_target_wide<=target_wide;fraction<=0;
        velocity_q<=controller_velocity_q;acceleration_q<=controller_acceleration_q;
       end
       shape_mode<=0;
       state<=CONTINUOUS;direction<=requested_direction;wave_shape<=0;
      end else begin
       state<=FINITE;direction<=action_kind==1 ? (positive ? 2'sd1:-2'sd1):requested_direction;
       final_wide<=proposed_goal[63:0];wave_shape<=0;
       if(VARIANT==2 && legacy_start && command<=2)begin finite_reference<=1;shape_mode<=1;finite_origin<=nominal_target_wide;fraction<=0;end
      end
      // Recompute a pending tick instead of committing an obsolete goal.
      if(finite_start)planner_phase<=0;
      else if(VARIANT==2 && (cancel_action || (legacy_action && (command==5 || !ready_legacy))))planner_phase<=0;
      else if(planner_phase!=0 || tick_5ms)planner_phase<=1;
     end
    end
    // Acceptance and execution are distinct: the first next command is kept,
    // without changing the current turn or requiring a repeated gesture.
    if(dispatch_pending)begin
     queued_valid<=0;last_request_id<=queued_request_id;last_action<={5'd0,queued_command};
     reason<=0;stop_reason<=0;origin_wide<=encoder_wide;
     nominal_done<=0;tracking_settled<=0;settle_ticks<=0;settling_age<=0;wave_shape<=0;
     // Preserve the original explicit large-error protection, but fail the
     // pending task visibly now rather than waiting for it forever.
     if(start_error_bad)begin reason<=12;stop_reason<=12;nominal_done<=1;end
     else if(queued_goal_bad)begin planner_fault<=1;state<=HOLD;reason<=5;stop_reason<=5;finite_reference<=0;end
     else if(queued_command<=2)begin
      state<=FINITE;direction<=queued_direction;finite_reference<=1;shape_mode<=1;finite_origin<=nominal_target_wide;
      final_wide<=queued_goal[63:0];fraction<=0;planner_phase<=0;
     end else begin
      state<=CONTINUOUS;direction<=queued_direction;finite_reference<=0;planner_phase<=0;
       if(VARIANT==2 && shape_mode)begin
        nominal_target_wide<=target_wide;fraction<=0;
        velocity_q<=controller_velocity_q;acceleration_q<=controller_acceleration_q;
       end
       shape_mode<=0;
     end
    end
    if(finite_reference && !finite_cancel && !dispatch_pending)begin
     if(tick_5ms && finite_ready)planner_phase<=9;
     if(finite_fault)begin planner_fault<=1;state<=HOLD;finite_reference<=0;queued_valid<=0;reason<=8;stop_reason<=8;end
     else if(finite_sample)begin
      planner_phase<=0;
      if(finite_reference_position[64]!=finite_reference_position[63])begin planner_fault<=1;state<=HOLD;finite_reference<=0;queued_valid<=0;reason<=5;stop_reason<=5;end
      else begin
       nominal_target_wide<=finite_reference_position[63:0];
       fraction<=direction[1] ? -finite_offset[15:0]:finite_offset[15:0];
       velocity_q<=finite_signed_velocity;
       acceleration_q<=direction[1] ? -finite_acceleration:finite_acceleration;
       nominal_reference_velocity_cps<=($signed(finite_signed_velocity)*32'sd200)>>>16;
       if(finite_done)begin
        nominal_target_wide<=final_wide;fraction<=0;velocity_q<=0;acceleration_q<=0;nominal_reference_velocity_cps<=0;
        finite_reference<=0;state<=SETTLING;direction<=0;nominal_done<=1;stop_reason<=9;settle_ticks<=0;settling_age<=0;
       end
      end
     end
    end
    if(tick_5ms && !admitted_request && !dispatch_pending && !finite_reference)begin
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
                 nominal_target_wide==final_wide && capture_arrived)begin
      if(settle_ticks<100)settle_ticks<=settle_ticks+1'b1;
      if(settle_ticks>=99)begin state<=WAVE_PLAY;settle_ticks<=0;wave_goal<=origin_wide;end
     end else begin
      if(state==WAVE_CAPTURE)settle_ticks<=0;
      if(planner_phase==0)planner_phase<=1;
     end
     if(VARIANT==5 && state==WAVE_PLAY)begin
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
    if(!admitted_request && !dispatch_pending && !finite_reference)begin
     case(planner_phase)
      1:begin
       calculation_epoch<=event_count;reference_error_ext<=current_reference_error;actual_error_ext<=current_actual_error;
       speed_magnitude<=larger_velocity;planner_phase<=2;
      end
      2:begin
       if(reference_error_ext>4095)error_q<=32'sh0fffffff;
       else if(reference_error_ext< -4095)error_q<=-32'sh0fffffff;
       else error_q<=($signed(reference_error_ext[31:0])<<<16)-$signed({16'd0,fraction});
       distance_index<=(reference_error_ext>2047 || reference_error_ext< -2047) ? 9'd511:
        (reference_error_ext[64] ? (-reference_error_ext)>>2:reference_error_ext>>2);
       speed_index<=speed_magnitude>=4096 ? 8'd255:speed_magnitude[11:4];planner_phase<=3;
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
        if(measured_magnitude>40 && state!=WAVE_PLAY)desired_velocity<=0;
        else if(error_q>32'sd419430)desired_velocity<=32'sd6553;
        else if(error_q< -32'sd419430)desired_velocity<=-32'sd6553;
        else desired_velocity<=error_q[31] ? -$signed((-error_q)>>>6):error_q>>>6;
       end else if(state!=WAVE_PLAY &&
          ((actual_error_ext>=0 && !measured_velocity_cps[31] && measured_velocity_cps>0 && actual_error_ext<=$signed({53'd0,prepared_stop_distance})) ||
           (actual_error_ext<0 && measured_velocity_cps[31] && -actual_error_ext<=$signed({53'd0,prepared_stop_distance}))))begin
        desired_velocity<=0;
       end else desired_velocity<=error_q[31] ? -$signed({14'd0,prepared_cap}):$signed({14'd0,prepared_cap});
       planner_phase<=5;
      end
      5:begin
       if(proportional_acceleration>$signed({16'd0,used_acceleration_limit}))desired_acceleration<=$signed({16'd0,used_acceleration_limit});
       else if(proportional_acceleration< -$signed({16'd0,used_acceleration_limit}))desired_acceleration<=-$signed({16'd0,used_acceleration_limit});
       else desired_acceleration<=proportional_acceleration;
       planner_phase<=6;
      end
      6:begin
       if(desired_acceleration>acceleration_q+$signed({16'd0,used_jerk_limit}))next_acceleration<=acceleration_q+$signed({16'd0,used_jerk_limit});
       else if(desired_acceleration<acceleration_q-$signed({16'd0,used_jerk_limit}))next_acceleration<=acceleration_q-$signed({16'd0,used_jerk_limit});
       else next_acceleration<=desired_acceleration;
       planner_phase<=7;
      end
      7:begin
       if(desired_velocity==0 && velocity_q<=7 && velocity_q>=-7 &&
          terminal_at_most_upper &&
          terminal_at_least_lower)begin
        next_velocity<=0;next_acceleration<=-velocity_q;
       end else next_velocity<=velocity_q+next_acceleration;
       planner_phase<=8;
      end
      8:begin
       if(calculation_epoch!=event_count && !(VARIANT==2 && state==BRAKING))planner_phase<=1;
       else begin
        planner_phase<=0;
        if(reference_overflow)begin planner_fault<=1;reason<=5;stop_reason<=5;state<=HOLD;velocity_q<=0;acceleration_q<=0;end
        else begin
         nominal_target_wide<=integrated_reference[63:0];fraction<=position_sum[15:0];
         velocity_q<=next_velocity;acceleration_q<=next_acceleration;
         nominal_reference_velocity_cps<=($signed(next_velocity)*32'sd200)>>>16;
         if(state==BRAKING && next_velocity==0 && next_acceleration==0)begin
          final_wide<=integrated_reference[63:0];fraction<=0;state<=SETTLING;direction<=0;
          nominal_done<=1;settle_ticks<=0;settling_age<=0;
         end else if(state!=CONTINUOUS && state!=WAVE_PLAY && state!=BRAKING &&
                     next_velocity==0 && next_acceleration==0 && error_magnitude<=64)begin
          nominal_target_wide<=final_wide;fraction<=0;direction<=0;
          if(state!=WAVE_CAPTURE)begin state<=SETTLING;nominal_done<=1;stop_reason<=9;settle_ticks<=0;settling_age<=0;end
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

// Preserve small comparison results to avoid a serial32-bit terminal LUT chain.
module forest_motion_signed_le32(
 input signed [31:0] left,right,output wire result
);
 wire [31:0] ordered_left={~left[31],left[30:0]};
 wire [31:0] ordered_right={~right[31],right[30:0]};
 wire [3:0] byte_less /* synthesis syn_keep=1 */;
 wire [3:0] byte_equal /* synthesis syn_keep=1 */;
 genvar compare_byte;
 generate for(compare_byte=0;compare_byte<4;compare_byte=compare_byte+1)begin:bytes
  assign byte_less[compare_byte]=ordered_left[compare_byte*8+:8]<ordered_right[compare_byte*8+:8];
  assign byte_equal[compare_byte]=ordered_left[compare_byte*8+:8]==ordered_right[compare_byte*8+:8];
 end endgenerate
 assign result=(byte_less[3] || (byte_equal[3] && byte_less[2])) ||
  ((byte_equal[3] && byte_equal[2]) &&
   (byte_less[1] || (byte_equal[1] && (byte_less[0] || byte_equal[0]))));
endmodule
