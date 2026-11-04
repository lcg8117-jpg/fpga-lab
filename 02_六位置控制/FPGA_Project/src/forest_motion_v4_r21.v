// V4 R21: actual-origin tasks, continuous inherited-state transitions and
// preconditioned finite command reference. Controller/measurement are unchanged.
module forest_motion_v4_r21(
 input clk,rst_n,tick_5ms,reanchor,cancel,capture_hold,balance_active,kit_ip570,
 input signed [31:0] encoder_raw,
 input command_valid,input [2:0] command,input [1:0] action_kind,
 input [2:0] action_index,input [31:0] request_id,input left_positive,cw_positive,
 input cfg_write,input [7:0] cfg_id,input [31:0] cfg_value,
 output reg cfg_ok,cfg_error,output reg [31:0] cfg_revision,
 output reg [31:0] cfg_read_value,
 output wire signed [63:0] enc_wide_physical,encoder_wide,target_wide,
 output reg signed [63:0] origin_wide,final_wide,
 output wire signed [15:0] encoder_local,target_local,
 output wire active,fault,output reg [2:0] state,
 output reg signed [1:0] direction,output reg accepted,rejected,
 output reg [7:0] reason,stop_reason,output reg [31:0] event_count,last_request_id,
 output reg [7:0] last_action,output wire trajectory_done,output reg tracking_settled,
 output wire signed [31:0] measured_velocity_cps,
 output reg signed [31:0] reference_velocity_cps,
 output wire signed [63:0] tracking_error,output reg [7:0] settle_ticks,
 output reg [3:0] planner_phase,
 output wire signed [31:0] velocity_q,acceleration_q,
 output reg signed [63:0] nominal_target_wide,cancelled_final_wide,
 output reg [15:0] nominal_fraction,parking_fraction,
 output wire [15:0] control_fraction,
 output reg signed [31:0] nominal_velocity_q,nominal_acceleration_q,
 output reg nominal_done,output wire control_reference_done,transition_active,
 output reg cancelled_goal_valid
);
 localparam [2:0] HOLD=0,FINITE=1,BRAKING=3,SETTLING=4;
 localparam [2:0] IDLE=0,TRANSITION=1,PLAN=2,PROFILE=3,DIRECT_BRAKE=4,DIRECT_TO_PLAN=5,REBASE_WAIT=6,CANCEL_PENDING=7;
 reg [2:0] mode;
 reg direct_mode,planner_fault,reference_scale_fault;
 wire coordinate_fault,velocity_valid;
 wire shape_fault,shape_sample,shape_done,finite_ready,admitted_request;
 wire signed [63:0] shaped_target;
 wire [15:0] shaped_fraction;
 wire signed [31:0] shaped_velocity,shaped_acceleration;
 reg signed [63:0] selected_target;
 reg [15:0] selected_fraction;
 reg signed [31:0] selected_velocity,selected_acceleration;
 wire signed [63:0] selection_target=direct_mode ? nominal_target_wide:shaped_target;
 wire [15:0] selection_fraction=direct_mode ? nominal_fraction:shaped_fraction;
 wire signed [31:0] selection_velocity=direct_mode ? nominal_velocity_q:shaped_velocity;
 wire signed [31:0] selection_acceleration=direct_mode ? nominal_acceleration_q:shaped_acceleration;
 reg shape_rebase,abort_jobs,abort_brake,shape_pending,profile_tick_pending;
 reg profile_transition_pending,pending_replan,position_add_for_finite;
 reg [11:0] settling_age;
 assign target_wide=selected_target;
 assign control_fraction=selected_fraction;
 assign velocity_q=selected_velocity;
 assign acceleration_q=selected_acceleration;
 assign active=state!=HOLD;
 wire numerical_job_fault;
 assign fault=coordinate_fault || planner_fault || reference_scale_fault || numerical_job_fault || (!direct_mode && shape_fault);
 assign transition_active=mode==TRANSITION || mode==DIRECT_TO_PLAN || mode==REBASE_WAIT || mode==CANCEL_PENDING;
 assign control_reference_done=nominal_done && selected_target==final_wide &&
  selected_fraction==parking_fraction && selected_velocity==0 && selected_acceleration==0;
 assign trajectory_done=nominal_done && control_reference_done;
 wire hard_abort=reanchor || cancel || capture_hold;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin shape_pending<=0;profile_tick_pending<=0;end
  else begin
   if(hard_abort || !balance_active || shape_rebase)shape_pending<=0;
   else begin
    if(shape_sample)shape_pending<=0;
    if(tick_5ms && !direct_mode)shape_pending<=1;
   end
   if(hard_abort || !balance_active || abort_jobs)profile_tick_pending<=0;
   else begin
    if(tick_5ms && mode==PROFILE && finite_ready)profile_tick_pending<=1;
    if(mode==PROFILE && planner_phase==14 && position_add_for_finite && !admitted_request)profile_tick_pending<=0;
   end
  end
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin selected_target<=0;selected_fraction<=0;selected_velocity<=0;selected_acceleration<=0;end
  else if(hard_abort)begin
   // Reanchor/STOP must not leave an old wide coordinate in the selector
   // cache for one extra clock after the unchanged coordinate guard resets.
   selected_target<=reanchor ? 64'sd0:encoder_wide;selected_fraction<=0;
   selected_velocity<=0;selected_acceleration<=0;
  end
  else begin
   selected_target<=selection_target;
   selected_fraction<=selection_fraction;
   selected_velocity<=selection_velocity;
   selected_acceleration<=selection_acceleration;
  end
 end
 // Registered shift/add stages retain the R20 timing separation. No DSP and
 // no combinational target-selector -> wide-coordinate -> velocity path.
 reg signed [63:0] scale192,scale8;
 wire signed [63:0] selected_velocity_wide={{32{selected_velocity[31]}},selected_velocity};
 wire signed [63:0] scale_sum=scale192+scale8;
 wire signed [63:0] scaled_velocity_cps=scale_sum>>>16;
 wire velocity_cps_fits32=scaled_velocity_cps[63:31]=={33{scaled_velocity_cps[31]}};
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin scale192<=0;scale8<=0;reference_velocity_cps<=0;reference_scale_fault<=0;end
  else begin
   scale192<=(selected_velocity_wide<<<7)+(selected_velocity_wide<<<6);
   scale8<=selected_velocity_wide<<<3;
   if(velocity_cps_fits32)reference_velocity_cps<=scaled_velocity_cps[31:0];
   else reference_scale_fault<=1;
   if(hard_abort)reference_scale_fault<=0;
  end
 end
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
   amplitude<=87;period_ms<=16000;period_ticks<=3200;cycles<=2;positive<=1;
   cfg_ok<=0;cfg_error<=0;cfg_revision<=0;
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
      default:;
     endcase
    end
   end
   if(cfg_ok)cfg_revision<=cfg_revision+1'b1;
  end
 end


 wire signed [63:0] shape_reset_target=reanchor ? 64'sd0:
  (cancel || capture_hold) ? encoder_wide:nominal_target_wide;
 wire [15:0] shape_reset_fraction=hard_abort ? 16'd0:nominal_fraction;
 forest_motion_reference_shape reference_preconditioning(
  .clk(clk),.rst_n(rst_n),.tick(tick_5ms && !direct_mode && !hard_abort && balance_active),
  .rebase(shape_rebase || hard_abort || !balance_active),
  .stationary(nominal_velocity_q==0 && nominal_acceleration_q==0),
  .reset_target(shape_reset_target),.reset_fraction(shape_reset_fraction),
  .reset_velocity_q(32'sd0),.reset_acceleration_q(32'sd0),
  .nominal_target(nominal_target_wide),.nominal_fraction(nominal_fraction),
  .target(shaped_target),.fraction(shaped_fraction),.velocity_q(shaped_velocity),
  .acceleration_q(shaped_acceleration),.sample_valid(shape_sample),.done(shape_done),.fault(shape_fault));
 reg finite_start,finite_negative;
 reg [31:0] finite_distance,staged_offset;
 reg signed [63:0] finite_origin;
 reg [15:0] finite_origin_fraction;
 wire finite_active,finite_sample,finite_finished,finite_fault;
 wire [31:0] finite_offset,finite_velocity;
 wire signed [31:0] finite_acceleration;
 reg staged_last;
 reg signed [31:0] staged_velocity,staged_acceleration;
 forest_motion_finite_scurve #(.PROJECT_DOMAIN(1)) finite_profile(
  .clk(clk),.rst_n(rst_n),.start(finite_start),
  .cancel(hard_abort || abort_jobs || fault || !balance_active),
  .tick(tick_5ms && mode==PROFILE),.distance_q16(finite_distance),
  .speed_q16(finite_speed_q),.acceleration_limit_q16(acceleration_limit_q),.jerk_limit_q16(jerk_limit_q),
  .active(finite_active),.ready(finite_ready),.sample_valid(finite_sample),.finished(finite_finished),
  .fault(finite_fault),.offset_q16(finite_offset),.velocity_q16(finite_velocity),
  .acceleration_q16(finite_acceleration));
 reg brake_start;
 wire brake_busy,brake_done,brake_fault;
 wire signed [31:0] brake_velocity,brake_acceleration;
 wire [15:0] brake_limit_a=direct_mode ? 16'd1966:acceleration_limit_q;
 wire [15:0] brake_limit_j=direct_mode ? 16'd98:jerk_limit_q;
 forest_motion_brake_step braking_step(
  .clk(clk),.rst_n(rst_n),.start(brake_start),
  .cancel(hard_abort || abort_brake || fault || !balance_active),
  .velocity(nominal_velocity_q),.acceleration(nominal_acceleration_q),
  .limit_a(brake_limit_a),.limit_j(brake_limit_j),
  .busy(brake_busy),.done(brake_done),.fault(brake_fault),
  .next_velocity(brake_velocity),.next_acceleration(brake_acceleration));
 wire brake_position_pending=(planner_phase==12 || planner_phase==13 || planner_phase==14) && !position_add_for_finite;
 assign numerical_job_fault=(mode==PROFILE && finite_fault) ||
  ((planner_phase==1 || planner_phase==2 || planner_phase==11 || brake_position_pending) && brake_fault);
 reg signed [64:0] goal_error;
 // Phase5 already validates the full signed65 integer error in +/-65536.
 // Prepare its exact Q16 magnitude as short limbs before phase6 admission;
 // nominal_fraction must not drive a subtract/negate/range-check cascade.
 wire nominal_fraction_nonzero=|nominal_fraction;
 wire goal_integer_positive=!goal_error[64] && (|goal_error[16:0]);
 wire [16:0] goal_positive_integer=goal_error[16:0]-{16'd0,nominal_fraction_nonzero};
 wire [16:0] goal_negative_integer=-goal_error[16:0];
 wire [15:0] goal_positive_fraction=-nominal_fraction;
 reg [16:0] prepared_goal_integer;
 reg [15:0] prepared_goal_fraction;
 reg prepared_goal_negative;
 wire signed [33:0] brake_fraction_sum=$signed({18'd0,nominal_fraction})+
  $signed({{2{staged_velocity[31]}},staged_velocity});
 reg signed [33:0] registered_brake_fraction;
 wire signed [17:0] finite_fraction_sum=finite_negative ?
  $signed({2'd0,finite_origin_fraction})-$signed({2'd0,staged_offset[15:0]}):
  $signed({2'd0,finite_origin_fraction})+$signed({2'd0,staged_offset[15:0]});
 wire signed [64:0] finite_integer_step=finite_negative ?
  -$signed({49'd0,staged_offset[31:16]}):$signed({49'd0,staged_offset[31:16]});
 // Add only a short signed displacement to the full64-bit coordinate. The
 // four16-bit carry stages end before a separate validation/commit boundary.
 reg signed [17:0] registered_finite_fraction;
 reg signed [16:0] registered_finite_integer;
 reg position_add_start;
 reg signed [63:0] position_add_base;
 reg signed [17:0] position_add_delta;
 wire position_add_busy,position_add_done;
 wire signed [64:0] position_add_result;
 reg position_overflow,position_terminal_mismatch;
 wire position_add_cancel=hard_abort || fault || !balance_active ||
  (position_add_for_finite ? abort_jobs:abort_brake);
 forest_motion_position_add16 position_addition(
  .clk(clk),.rst_n(rst_n),.cancel(position_add_cancel),.start(position_add_start),
  .base(position_add_base),.delta(position_add_delta),.busy(position_add_busy),
  .done(position_add_done),.result(position_add_result));
 wire signed [64:0] finite_position_sum=position_add_result;
 wire signed [64:0] brake_position_sum=position_add_result;
 // Signed small-range predicates use sign-fill reductions instead of two
 // full-width ordered comparators. The public observation edge is unchanged.
 wire settled_error_range=(!( |tracking_error[63:3]) && tracking_error[2:0]<=3'd5) ||
  ((&tracking_error[63:3]) && tracking_error[2:0]>=3'd3);
 wire settled_velocity_range=(!( |measured_velocity_cps[31:4]) && measured_velocity_cps[3:0]<=4'd10) ||
  ((&measured_velocity_cps[31:4]) && measured_velocity_cps[3:0]>=4'd6);
 wire settled_now=velocity_valid && settled_error_range && settled_velocity_range;
 reg signed [63:0] relative_delta;
 reg [10:0] relative_magnitude;
 always @*begin
  case(action_index)
   1:relative_magnitude=173;2:relative_magnitude=347;3:relative_magnitude=520;
   4:relative_magnitude=693;5:relative_magnitude=867;6:relative_magnitude=1040;
   default:relative_magnitude=0;
  endcase
  relative_delta=positive ? $signed({53'd0,relative_magnitude}):
   -$signed({53'd0,relative_magnitude});
 end
 wire position_action=action_kind==1 && action_index>=1 && action_index<=6;
 wire cancel_action=action_kind==3;
 wire legal_action=position_action || cancel_action;
 wire signed [64:0] proposed_goal=$signed({encoder_wide[63],encoder_wide})+
  $signed({relative_delta[63],relative_delta});
 // An increment/decrement below2048 can cross a signed64 boundary only
 // from its adjacent2048-count block. This removes a second64-bit carry chain
 // from same-clock command admission without delaying origin capture/ACK.
 wire [11:0] goal_low_positive={1'b0,encoder_wide[10:0]}+{1'b0,relative_magnitude};
 wire goal_negative_borrow=encoder_wide[10:0]<relative_magnitude;
 wire goal_overflow=positive ?
  (!encoder_wide[63] && (&encoder_wide[62:11]) && goal_low_positive[11]):
  (encoder_wide[63] && !(|encoder_wide[62:11]) && goal_negative_borrow);
 wire idle_cancel_reject=cancel_action && (state==BRAKING || state==HOLD ||
  (state==SETTLING && trajectory_done && nominal_velocity_q==0));
 assign admitted_request=command_valid && legal_action && !(position_action && goal_overflow) && !idle_cancel_reject;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   nominal_target_wide<=0;nominal_fraction<=0;nominal_velocity_q<=0;nominal_acceleration_q<=0;
   origin_wide<=0;final_wide<=0;cancelled_final_wide<=0;cancelled_goal_valid<=0;parking_fraction<=0;
   state<=HOLD;mode<=IDLE;direct_mode<=1;direction<=0;planner_fault<=0;
   accepted<=0;rejected<=0;reason<=0;stop_reason<=0;event_count<=0;last_request_id<=0;last_action<=0;
   nominal_done<=0;tracking_settled<=0;settle_ticks<=0;settling_age<=0;planner_phase<=0;
   finite_start<=0;finite_negative<=0;finite_distance<=0;finite_origin<=0;finite_origin_fraction<=0;
   staged_offset<=0;staged_last<=0;staged_velocity<=0;staged_acceleration<=0;
   registered_brake_fraction<=0;registered_finite_fraction<=0;registered_finite_integer<=0;
   position_add_start<=0;position_add_for_finite<=0;position_add_base<=0;position_add_delta<=0;
   position_overflow<=0;position_terminal_mismatch<=0;
   brake_start<=0;abort_jobs<=0;abort_brake<=0;shape_rebase<=0;goal_error<=0;
   profile_transition_pending<=0;pending_replan<=0;
   prepared_goal_integer<=0;prepared_goal_fraction<=0;prepared_goal_negative<=0;
  end else begin
   accepted<=0;rejected<=0;finite_start<=0;brake_start<=0;position_add_start<=0;abort_jobs<=0;abort_brake<=0;shape_rebase<=0;
   if(hard_abort)begin
    nominal_target_wide<=reanchor ? 64'sd0:encoder_wide;
    origin_wide<=reanchor ? 64'sd0:encoder_wide;final_wide<=reanchor ? 64'sd0:encoder_wide;
    nominal_fraction<=0;nominal_velocity_q<=0;nominal_acceleration_q<=0;parking_fraction<=0;
    state<=HOLD;mode<=IDLE;direct_mode<=1;direction<=0;planner_fault<=0;
    nominal_done<=0;tracking_settled<=0;settle_ticks<=0;settling_age<=0;planner_phase<=0;
    cancelled_goal_valid<=0;stop_reason<=reanchor || capture_hold ? 7:6;
    profile_transition_pending<=0;pending_replan<=0;
    if(command_valid)begin rejected<=1;reason<=6;end
   end else if(fault || !balance_active)begin
    if(fault)planner_fault<=1;
    if(numerical_job_fault || reference_scale_fault || (!direct_mode && shape_fault))begin reason<=8;stop_reason<=8;end
    state<=HOLD;mode<=IDLE;direct_mode<=1;direction<=0;
    nominal_velocity_q<=0;nominal_acceleration_q<=0;nominal_fraction<=0;planner_phase<=0;
    nominal_done<=0;tracking_settled<=0;settle_ticks<=0;
    profile_transition_pending<=0;pending_replan<=0;
    if(command_valid)begin rejected<=1;reason<=fault ? 8:1;end
   end else begin
    if(command_valid)begin
     if(!legal_action)begin rejected<=1;reason<=4;end
     else if(position_action && goal_overflow)begin rejected<=1;reason<=5;end
     else if(idle_cancel_reject)begin rejected<=1;reason<=3;end
    end
    if(admitted_request)begin
     accepted<=1;reason<=0;stop_reason<=0;event_count<=event_count+1'b1;
     last_request_id<=request_id;last_action<=position_action ? (8'h10+{5'd0,action_index}):8'h30;
     origin_wide<=encoder_wide;nominal_done<=0;tracking_settled<=0;settle_ticks<=0;settling_age<=0;
     if(cancel_action)begin
      // Seed from the SAME coherent mux source that this clock publishes
      // into selected_*. Copying the previous selector cache would rewind
      // one generator sample if cancellation follows shape_sample by 1clk.
      cancelled_final_wide<=final_wide;cancelled_goal_valid<=1;
      abort_jobs<=1;profile_transition_pending<=0;pending_replan<=0;
      state<=BRAKING;stop_reason<=14;
      if(direct_mode && (mode==DIRECT_TO_PLAN || mode==DIRECT_BRAKE))begin
       // A direct brake already in flight is the exact same A/J-bounded
       // operation. Finish its current sample rather than dropping a tick.
       mode<=DIRECT_BRAKE;
       if(planner_phase==1 && brake_done && !brake_start)begin
        staged_velocity<=brake_velocity;staged_acceleration<=brake_acceleration;planner_phase<=2;
       end else if(planner_phase==0 && tick_5ms)begin brake_start<=1;planner_phase<=1;end
      end else if(!direct_mode && (shape_pending || tick_5ms) && !shape_sample)begin
       // Soft cancel may arrive in the 17-clock shape transaction. The
       // already-started sample is published first; hard STOP never waits.
       mode<=CANCEL_PENDING;planner_phase<=0;abort_brake<=1;
      end else begin
       nominal_target_wide<=selection_target;nominal_fraction<=selection_fraction;
       nominal_velocity_q<=selection_velocity;nominal_acceleration_q<=selection_acceleration;
       direct_mode<=1;mode<=DIRECT_BRAKE;planner_phase<=0;abort_brake<=1;
      end
     end else begin
      final_wide<=proposed_goal[63:0];parking_fraction<=0;cancelled_goal_valid<=0;
      state<=FINITE;direction<=positive ? 2'sd1:-2'sd1;
      if(mode==CANCEL_PENDING)begin
       pending_replan<=1;
       if(shape_sample)begin
        // Accept the new final AND consume this one-cycle completion.
        // Waiting for another shape sample would advance an extra old tick.
        nominal_target_wide<=selection_target;nominal_fraction<=selection_fraction;
        nominal_velocity_q<=selection_velocity;nominal_acceleration_q<=selection_acceleration;
        direct_mode<=1;mode<=DIRECT_TO_PLAN;planner_phase<=0;pending_replan<=0;
       end
      end
      else if(mode==PROFILE && (profile_tick_pending || finite_sample ||
         planner_phase==9 || planner_phase==10 || planner_phase==12 || planner_phase==13 || planner_phase==14 || (tick_5ms && finite_ready)))begin
       // Retarget final immediately, while allowing only this one already
       // launched old-profile sample to commit. It cannot restore old final.
       profile_transition_pending<=1;
       if(finite_sample)begin
        staged_offset<=finite_offset;staged_last<=finite_finished;
        staged_velocity<=finite_negative ? -$signed(finite_velocity):$signed(finite_velocity);
        staged_acceleration<=finite_negative ? -finite_acceleration:finite_acceleration;
        planner_phase<=9;
       end
      end else if(mode==TRANSITION || mode==DIRECT_TO_PLAN || mode==DIRECT_BRAKE)begin
       mode<=direct_mode ? DIRECT_TO_PLAN:TRANSITION;
       if(planner_phase==1 && brake_done && !brake_start)begin
        staged_velocity<=brake_velocity;staged_acceleration<=brake_acceleration;planner_phase<=2;
       end else if(planner_phase==0 && tick_5ms)begin brake_start<=1;planner_phase<=1;end
      end else if(mode==REBASE_WAIT)begin
       // Rest seeding is independent of the destination, which is already
       // replaced above. Retain the in-flight coherent mux handover.
      end else begin
       planner_phase<=0;abort_jobs<=1;abort_brake<=1;profile_transition_pending<=0;
       if(direct_mode)mode<=DIRECT_TO_PLAN;
       else if(nominal_velocity_q==0 && nominal_acceleration_q==0)mode<=PLAN;
       else begin
        mode<=TRANSITION;
        if(tick_5ms)begin brake_start<=1;abort_brake<=0;planner_phase<=1;end
       end
      end
     end
    end else begin
     if(tick_5ms && (state==HOLD || state==SETTLING) && trajectory_done)begin
      if(settled_now)begin
       if(settle_ticks<100)settle_ticks<=settle_ticks+1'b1;
       if(settle_ticks>=99)begin tracking_settled<=1;state<=HOLD;stop_reason<=10;end
      end else begin settle_ticks<=0;tracking_settled<=0;if(state==HOLD)state<=SETTLING;end
      if(settling_age<3000)settling_age<=settling_age+1'b1;
      if(settling_age>=2999 && !settled_now)stop_reason<=11;
     end
     if(mode==PROFILE && finite_fault)begin
      planner_fault<=1;reason<=8;stop_reason<=8;state<=HOLD;planner_phase<=0;
     end else if((planner_phase==1 || planner_phase==2 || planner_phase==11 || brake_position_pending) && brake_fault)begin
      planner_fault<=1;reason<=8;stop_reason<=8;state<=HOLD;planner_phase<=0;
     end else case(planner_phase)
      0:begin
       if(mode==CANCEL_PENDING && shape_sample)begin
        nominal_target_wide<=selection_target;nominal_fraction<=selection_fraction;
        nominal_velocity_q<=selection_velocity;nominal_acceleration_q<=selection_acceleration;
        direct_mode<=1;mode<=pending_replan ? DIRECT_TO_PLAN:DIRECT_BRAKE;
       end else if(mode==PLAN)begin
        goal_error<=$signed({final_wide[63],final_wide})-$signed({nominal_target_wide[63],nominal_target_wide});
        planner_phase<=5;
       end else if(tick_5ms && (mode==TRANSITION || mode==DIRECT_BRAKE || mode==DIRECT_TO_PLAN))begin
        brake_start<=1;planner_phase<=1;
       end
      end
      1:begin
       if(brake_done && !brake_start)begin staged_velocity<=brake_velocity;staged_acceleration<=brake_acceleration;planner_phase<=2;end
       else if(tick_5ms)begin planner_fault<=1;reason<=8;stop_reason<=8;planner_phase<=0;end
      end
      2:begin
       registered_brake_fraction<=brake_fraction_sum;position_add_for_finite<=0;planner_phase<=11;
      end
      11:begin
       position_add_base<=nominal_target_wide;
       position_add_delta<=$signed(registered_brake_fraction[33:16]);
       position_add_start<=1;planner_phase<=12;
      end
      // The shape is fully seeded before the selected-reference mux changes.
      // Both sides are then the same rest position INCLUDING its Q16 fraction.
      3:planner_phase<=4;
      4:begin direct_mode<=0;mode<=PLAN;planner_phase<=0;end
      5:begin
       if(goal_error>65'sd65536 || goal_error< -65'sd65536)begin
        planner_fault<=1;reason<=5;stop_reason<=5;state<=HOLD;planner_phase<=0;
       end else begin
        prepared_goal_integer<=goal_integer_positive ? goal_positive_integer:goal_negative_integer;
        prepared_goal_fraction<=goal_integer_positive ? goal_positive_fraction:nominal_fraction;
        prepared_goal_negative<=goal_error[64] || (!goal_integer_positive && nominal_fraction_nonzero);
        planner_phase<=6;
       end
      end
      6:begin
       if(prepared_goal_integer[16])begin
        planner_fault<=1;reason<=5;stop_reason<=5;state<=HOLD;planner_phase<=0;
       end else if(prepared_goal_integer==0 && prepared_goal_fraction==0)begin
        nominal_done<=1;state<=SETTLING;mode<=IDLE;direction<=0;stop_reason<=9;planner_phase<=0;
       end else begin
        finite_distance<={prepared_goal_integer[15:0],prepared_goal_fraction};finite_negative<=prepared_goal_negative;
        finite_origin<=nominal_target_wide;finite_origin_fraction<=nominal_fraction;planner_phase<=7;
       end
      end
      7:begin finite_start<=1;mode<=PROFILE;planner_phase<=8;end
      8:if(finite_sample)begin
       staged_offset<=finite_offset;staged_last<=finite_finished;
       staged_velocity<=finite_negative ? -$signed(finite_velocity):$signed(finite_velocity);
       staged_acceleration<=finite_negative ? -finite_acceleration:finite_acceleration;
       planner_phase<=9;
      end
      9:begin
       registered_finite_fraction<=finite_fraction_sum;
       registered_finite_integer<=finite_integer_step[16:0];
       position_add_for_finite<=1;planner_phase<=10;
      end
      10:begin
       position_add_base<=finite_origin;
       position_add_delta<=$signed({registered_finite_integer[16],registered_finite_integer})+
        $signed({{16{registered_finite_fraction[17]}},registered_finite_fraction[17:16]});
       position_add_start<=1;planner_phase<=12;
      end
      12:begin
       if(position_add_done && !position_add_start)planner_phase<=13;
       else if(tick_5ms)begin planner_fault<=1;reason<=8;stop_reason<=8;state<=HOLD;planner_phase<=0;end
      end
      13:begin
       position_overflow<=position_add_result[64]!=position_add_result[63];
       position_terminal_mismatch<=position_add_result[63:0]!=final_wide || registered_finite_fraction[15:0]!=0;
       planner_phase<=14;
      end
      14:begin
       if(position_overflow || (position_add_for_finite && staged_last &&
          !profile_transition_pending && position_terminal_mismatch))begin
        planner_fault<=1;reason<=5;stop_reason<=5;state<=HOLD;planner_phase<=0;
       end else if(position_add_for_finite)begin
        nominal_target_wide<=finite_position_sum[63:0];nominal_fraction<=registered_finite_fraction[15:0];
        nominal_velocity_q<=staged_velocity;nominal_acceleration_q<=staged_acceleration;planner_phase<=8;
        if(profile_transition_pending)begin
         profile_transition_pending<=0;abort_jobs<=1;planner_phase<=0;
         mode<=staged_velocity==0 && staged_acceleration==0 ? PLAN:TRANSITION;
        end else if(staged_last)begin
         nominal_velocity_q<=0;nominal_acceleration_q<=0;nominal_done<=1;
         mode<=IDLE;planner_phase<=0;state<=SETTLING;direction<=0;stop_reason<=9;
         settle_ticks<=0;settling_age<=0;
        end
       end else begin
        nominal_target_wide<=brake_position_sum[63:0];nominal_fraction<=registered_brake_fraction[15:0];
        nominal_velocity_q<=staged_velocity;nominal_acceleration_q<=staged_acceleration;planner_phase<=0;
        if(staged_velocity==0 && staged_acceleration==0)begin
         if(mode==DIRECT_BRAKE)begin
          final_wide<=brake_position_sum[63:0];parking_fraction<=registered_brake_fraction[15:0];
          nominal_done<=1;state<=SETTLING;direction<=0;mode<=IDLE;
         end else if(mode==DIRECT_TO_PLAN)begin
          shape_rebase<=1;mode<=REBASE_WAIT;planner_phase<=3;
         end else mode<=PLAN;
        end
       end
      end
      default:begin planner_fault<=1;reason<=8;stop_reason<=8;planner_phase<=0;end
     endcase
    end
   end
  end
 end
endmodule

// Signed64 base plus signed18 displacement, exact signed65 result.
// Four registered16-bit digits prevent a wide carry->overflow->enable cone.
module forest_motion_position_add16(
 input clk,rst_n,cancel,start,input signed [63:0] base,input signed [17:0] delta,
 output reg busy,done,output reg signed [64:0] result
);
 reg [63:0] remaining;reg signed [17:0] step;
 reg [1:0] limb;reg carry,base_sign;
 wire [15:0] step_word=limb==0 ? step[15:0]:
  limb==1 ? {{14{step[17]}},step[17:16]}:{16{step[17]}};
 wire [16:0] digit_sum={1'b0,remaining[15:0]}+{1'b0,step_word}+{16'd0,carry};
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin busy<=0;done<=0;result<=0;remaining<=0;step<=0;limb<=0;carry<=0;base_sign<=0;end
  else begin
   done<=0;
   if(cancel)begin busy<=0;done<=0;end
   else if(start && !busy)begin
    busy<=1;remaining<=base;step<=delta;limb<=0;carry<=0;base_sign<=base[63];result<=0;
   end else if(busy)begin
    result[limb*16+:16]<=digit_sum[15:0];
    remaining<=remaining>>16;carry<=digit_sum[16];
    if(limb==3)begin
     result[64]<=base_sign ^ step[17] ^ digit_sum[16];busy<=0;done<=1;
    end else limb<=limb+1'b1;
   end
  end
 end
endmodule
