// A reference planner only: the frozen parallel controller always owns PWM.
module forest_motion_planner # (parameter integer VARIANT=2)(
 input clk,rst_n,tick_5ms,reanchor,cancel,capture_hold,balance_active,kit_ip570,
 input signed [31:0] encoder_raw,
 input command_valid,input [2:0] command,input [1:0] action_kind,
 input [2:0] action_index,input [31:0] request_id,input left_positive,cw_positive,
 input [31:0] live_job,input signed [31:0] live_value,
 output reg signed [15:0] live_offset,output reg [31:0] live_sequence,
 output reg [31:0] play_time_ms,

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
 localparam [2:0] HOLD=0,BRAKING=3,SETTLING=4,LIVE=6;
 wire coordinate_fault,velocity_valid;
 reg planner_fault,trajectory_envelope_bad;
 reg [15:0] job_ticks;
 reg [12:0] job_edges;
 reg signed [31:0] observed_raw;
 reg [5:0] live_age;
 reg signed [63:0] live_goal;
 reg [15:0] fraction;
 reg [11:0] settling_age;
 wire trajectory_envelope_outside=balance_active &&
  (encoder_wide>64'sd345 || encoder_wide< -64'sd345 ||
   target_wide>64'sd345 || target_wide< -64'sd345 ||
   (active && (job_ticks>=16'd30000 || job_edges>=(kit_ip570 ? 13'd4160:13'd4320))));
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)trajectory_envelope_bad<=0;
  else if(reanchor)trajectory_envelope_bad<=0;
  else if(trajectory_envelope_outside)trajectory_envelope_bad<=1;
 end
 assign fault=coordinate_fault || planner_fault || trajectory_envelope_bad;
 assign active=state!=HOLD;
 forest_motion_coordinates coordinates(
  .clk(clk),.rst_n(rst_n),.tick_5ms(tick_5ms),.reanchor(reanchor),
  .cancel(cancel),.capture_hold(capture_hold),.balance_active(balance_active),.kit_ip570(kit_ip570),
  .encoder_raw(encoder_raw),.target_wide(target_wide),.enc_wide_physical(enc_wide_physical),
  .encoder_wide(encoder_wide),.encoder_local(encoder_local),.target_local(target_local),
  .fault(coordinate_fault),.tracking_error(tracking_error),
  .measured_velocity_cps(measured_velocity_cps),.velocity_valid(velocity_valid));
 reg [9:0] finite_speed;
 reg [15:0] acceleration_limit_q,jerk_limit_q;
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
   6:cfg_read_value={31'd0,positive};
   default:cfg_read_value=32'hffffffff;
  endcase
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   finite_speed<=260;acceleration_limit_q<=524;jerk_limit_q<=13;
   positive<=1;
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
      default:;
     endcase
    end
   end
   if(cfg_ok)cfg_revision<=cfg_revision+1'b1;
  end
 end

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
 forest_motion_tables tables(.clk(clk),.distance_index(distance_index),.speed_index(speed_index),
  .reference_cap_q(table_cap),
  .stopping_distance(table_stop_distance));

 wire signed [63:0] current_goal=state==LIVE ? live_goal:final_wide;
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

 // Only the current BEGIN request owns a stroke. Late points cannot restart it.
 wire begin_action=action_kind==0,point_action=action_kind==1,end_action=action_kind==2;
 wire cancel_action=action_kind==3;
 // A mouse press takes ownership of the existing reference. Physical settling
 // is a displayed observation, never an admission condition for live input.
 wire ready_live=1'b1;
 wire begin_bad=begin_action && !ready_live;
 wire point_bad=point_action && (state!=LIVE || live_job!=last_request_id || live_value>192 || live_value< -192);
 wire end_bad=end_action && (state!=LIVE || live_job!=last_request_id);
 wire cancel_bad=cancel_action && (state==HOLD || state==BRAKING);
 wire admitted_request=command_valid && !begin_bad && !point_bad && !end_bad && !cancel_bad;
 // A valid point at the deadline wins; malformed, stale or cached requests do not refresh the lease.
 wire stream_timeout=tick_5ms && state==LIVE && live_age>=39 && !(admitted_request && (point_action || begin_action));
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin job_ticks<=0;job_edges<=0;observed_raw<=0;end
  else begin
   observed_raw<=encoder_raw;
   if(reanchor || (admitted_request && begin_action && !fault && balance_active && !cancel && !capture_hold))begin job_ticks<=0;job_edges<=0;end
   else if(active)begin
    if(tick_5ms && job_ticks<16'd30000)job_ticks<=job_ticks+1'b1;
    if(encoder_raw!=observed_raw && job_edges<13'd8191)job_edges<=job_edges+1'b1;
   end
  end
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   target_wide<=0;origin_wide<=0;final_wide<=0;state<=HOLD;direction<=0;
   accepted<=0;rejected<=0;reason<=0;stop_reason<=0;event_count<=0;last_request_id<=0;last_action<=0;
   planner_fault<=0;fraction<=0;velocity_q<=0;acceleration_q<=0;reference_velocity_cps<=0;
   trajectory_done<=0;tracking_settled<=0;settle_ticks<=0;settling_age<=0;
   live_goal<=0;live_offset<=0;live_sequence<=0;live_age<=0;play_time_ms<=0;
   calculation_epoch<=0;reference_error_ext<=0;actual_error_ext<=0;error_q<=0;error_magnitude<=0;
   speed_magnitude<=0;distance_index<=0;speed_index<=0;prepared_cap<=0;prepared_stop_distance<=0;
   desired_velocity<=0;desired_acceleration<=0;next_acceleration<=0;next_velocity<=0;planner_phase<=0;
   jerk_upper<=0;jerk_lower<=0;negative_velocity<=0;
  end else begin
   accepted<=0;rejected<=0;
   if(reanchor || cancel || capture_hold)begin
    target_wide<=reanchor ? 64'sd0:encoder_wide;
    origin_wide<=0;final_wide<=reanchor ? 64'sd0:encoder_wide;
    state<=HOLD;direction<=0;planner_fault<=0;fraction<=0;velocity_q<=0;acceleration_q<=0;
    planner_phase<=0;reference_velocity_cps<=0;trajectory_done<=0;tracking_settled<=0;
    settle_ticks<=0;settling_age<=0;live_age<=0;live_offset<=0;live_goal<=0;
    stop_reason<=reanchor || capture_hold ? 7:6;play_time_ms<=0;
    if(command_valid)begin rejected<=1;reason<=6;end
   end else if(fault || !balance_active)begin
    if(trajectory_envelope_bad)begin planner_fault<=1;reason<=16;stop_reason<=16;end
    state<=HOLD;direction<=0;velocity_q<=0;acceleration_q<=0;fraction<=0;planner_phase<=0;
    reference_velocity_cps<=0;trajectory_done<=0;tracking_settled<=0;settle_ticks<=0;
    if(command_valid)begin rejected<=1;reason<=fault ? 8:1;end
   end else begin
    if(tick_5ms && active)play_time_ms<=play_time_ms+32'd5;
    if(tick_5ms && state==LIVE && live_age<40)live_age<=live_age+1'b1;
    if(command_valid)begin
     if(begin_bad || point_bad || end_bad || cancel_bad)begin rejected<=1;reason<=15;end
     else begin
      accepted<=1;reason<=0;event_count<=event_count+1'b1;
      last_action<=begin_action ? 8'h48:point_action ? 8'h49:end_action ? 8'h4a:8'h47;
      if(begin_action)begin
       state<=LIVE;last_request_id<=request_id;origin_wide<=0;
       live_goal<=target_wide;live_offset<=target_wide[15:0];live_sequence<=0;live_age<=0;
       play_time_ms<=0;direction<=positive ? 2'sd1:-2'sd1;
       stop_reason<=0;trajectory_done<=0;tracking_settled<=0;settle_ticks<=0;settling_age<=0;
      end else if(point_action)begin
       live_goal<=positive ? {{32{live_value[31]}},live_value}:-$signed({{32{live_value[31]}},live_value});
       live_offset<=positive ? live_value[15:0]:-live_value[15:0];live_sequence<=request_id;live_age<=0;
      end else begin state<=BRAKING;stop_reason<=14;trajectory_done<=0;tracking_settled<=0;settle_ticks<=0;settling_age<=0;end
      // Keep velocity/acceleration continuous while switching target ownership.
      if(planner_phase!=0 || tick_5ms)planner_phase<=1;
     end
    end
    if(stream_timeout)begin
     state<=BRAKING;stop_reason<=18;event_count<=event_count+1'b1;
     if(planner_phase!=0 || tick_5ms)planner_phase<=1;
    end
    if(tick_5ms && !admitted_request && !stream_timeout)begin
     if(state==HOLD || state==SETTLING)begin
      if(trajectory_done)begin
       if(settled_now)begin
        if(settle_ticks<100)settle_ticks<=settle_ticks+1'b1;
        if(settle_ticks>=99)begin tracking_settled<=1;state<=HOLD;end
       end else begin settle_ticks<=0;tracking_settled<=0;if(state==HOLD)state<=SETTLING;end
       if(settling_age<3000)settling_age<=settling_age+1'b1;
       if(settling_age>=2999 && !settled_now)stop_reason<=11;
      end
     end else if(planner_phase==0)planner_phase<=1;
    end
    if(!admitted_request && !stream_timeout)begin
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
       if(state==BRAKING)desired_velocity<=0;
       else if(error_magnitude<32'd2097152)begin
        // Terminal creep is small and exponentially approaches the exact goal.
        if(measured_magnitude>40 && state!=LIVE)desired_velocity<=0;
        else if(error_q>32'sd419430)desired_velocity<=32'sd6553;
        else if(error_q< -32'sd419430)desired_velocity<=-32'sd6553;
        else desired_velocity<=error_q[31] ? -$signed((-error_q)>>>6):error_q>>>6;
       end else if(state!=LIVE &&
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
           (integrated_reference>65'sd345 || integrated_reference< -65'sd345 ||
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
         end else if(state!=LIVE && state!=BRAKING &&
                     next_velocity==0 && next_acceleration==0 && error_magnitude<=64)begin
          target_wide<=final_wide;fraction<=0;direction<=0;
          state<=SETTLING;trajectory_done<=1;stop_reason<=9;settle_ticks<=0;settling_age<=0;
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
