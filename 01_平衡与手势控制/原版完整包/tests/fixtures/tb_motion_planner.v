`timescale 1ns/1ps
module tb_motion_planner;
 reg clk=0;always #5 clk=~clk;
 reg rst_n=0,tick=0,reanchor=0,cancel=0,capture_hold=0,balance=0,kit=1;
 reg signed [31:0] raw=0;
 reg command_valid=0;reg [2:0] command=0;
 reg left_positive=1,cw_positive=1,cfg_write=0;
 reg [7:0] cfg_id=0;reg [31:0] cfg_value=0,request_id=0;
 wire cfg_ok,cfg_error,active,fault,accepted,rejected,trajectory_done,settled,velocity_valid;
 wire [31:0] cfg_revision,cfg_read,events,last_request;
 wire signed [63:0] physical,actual,target,origin,final_goal,error;
 wire signed [15:0] local_encoder,local_target;
 wire signed [31:0] measured_speed,reference_speed,velocity_q,acceleration_q;
 wire [2:0] state;wire signed [1:0] direction;
 wire [7:0] reason,stop_reason,last_action,settle_ticks;wire [3:0] phase;
 reg follow=1;reg signed [63:0] aligned_actual,aligned_target;
 integer checks=0,i,ticks,old_speed,old_accel,old_velocity,max_speed,delta;
 reg signed [63:0] old_target,old_final,start_target;
 forest_motion_planner #(.VARIANT(2)) dut(.clk(clk),.rst_n(rst_n),.tick_5ms(tick),
  .reanchor(reanchor),.cancel(cancel),.capture_hold(capture_hold),.balance_active(balance),.kit_ip570(kit),
  .encoder_raw(raw),.command_valid(command_valid),.command(command),.action_kind(2'd0),.action_index(3'd0),
  .request_id(request_id),.left_positive(left_positive),.cw_positive(cw_positive),.cfg_write(cfg_write),
  .cfg_id(cfg_id),.cfg_value(cfg_value),.cfg_ok(cfg_ok),.cfg_error(cfg_error),.cfg_revision(cfg_revision),
  .cfg_read_value(cfg_read),.enc_wide_physical(physical),.encoder_wide(actual),.target_wide(target),
  .origin_wide(origin),.final_wide(final_goal),.encoder_local(local_encoder),.target_local(local_target),
  .active(active),.fault(fault),.state(state),.direction(direction),.accepted(accepted),.rejected(rejected),
  .reason(reason),.stop_reason(stop_reason),.event_count(events),.last_request_id(last_request),
  .last_action(last_action),.trajectory_done(trajectory_done),.tracking_settled(settled),
  .measured_velocity_cps(measured_speed),.velocity_valid(velocity_valid),.reference_velocity_cps(reference_speed),
  .tracking_error(error),.settle_ticks(settle_ticks),.planner_phase(phase),.velocity_q(velocity_q),.acceleration_q(acceleration_q));
 always @(posedge clk)begin aligned_actual<=actual;aligned_target<=target;end
 always @(negedge clk)begin
  if(rst_n && follow)begin
   if(kit)begin
    if($signed(raw)<target)raw=raw+1;
    else if($signed(raw)>target)raw=raw-1;
   end else begin
    if(actual<target)raw=raw+1;else if(actual>target)raw=raw-1;
   end
  end
 end
 task check;input condition;input [511:0] message;begin
  checks=checks+1;if(condition!==1'b1)begin
   $display("FAIL planner %0s state=%0d target=%0d actual=%0d vel=%0d accel=%0d reason=%0d phase=%0d",message,state,target,actual,velocity_q,acceleration_q,reason,phase);$fatal(1);end
 end endtask
 task pulse_tick;begin
  @(negedge clk);tick=1;
  // This is the exact pre-edge snapshot the root sampler takes.
  check($signed(local_encoder)-16'sd10000==$signed(aligned_actual)-$signed(aligned_target),"aligned frame snapshot equals PID local tracking error");
  @(negedge clk);tick=0;repeat(32)@(negedge clk);
  check(!fault && phase==0,"planner commits within one5ms slot");
  check(reference_speed==($signed(velocity_q)*32'sd200)>>>16,"reference velocity units");
 end endtask
 task send_command;input [2:0] value;input ok;input [7:0] expected_reason;begin
  @(negedge clk);command=value;command_valid=1;request_id=request_id+1;
  @(negedge clk);check(accepted==ok && rejected==!ok && reason==expected_reason,"command disposition");
  command_valid=0;repeat(12)@(negedge clk);
 end endtask
 task reset_session;begin
  @(negedge clk);follow=0;rst_n=0;balance=0;raw=0;cancel=0;capture_hold=0;command_valid=0;
  repeat(3)@(negedge clk);rst_n=1;reanchor=1;
  @(negedge clk);reanchor=0;repeat(5)@(negedge clk);balance=1;follow=1;
 end endtask
 task finish_finite;input integer sign;begin
  old_target=target;max_speed=0;ticks=0;
  while(!trajectory_done && ticks<5000)begin
   old_velocity=velocity_q;old_accel=acceleration_q;pulse_tick();ticks=ticks+1;
   delta=acceleration_q-old_accel;
   check(delta<=13 && delta>=-13,"finite jerk bounded");
   check(acceleration_q<=524 && acceleration_q>=-524,"finite acceleration bounded");
   check(target-old_target<5 && target-old_target> -5,"finite reference continuous");
   if(reference_speed*sign>max_speed)max_speed=reference_speed*sign;
   old_target=target;
  end
  check(trajectory_done && state==4 && target==final_goal && velocity_q==0 && acceleration_q==0,"finite target and velocity stop exactly");
  check(max_speed>200 && reference_speed==0,"finite profile accelerates then decelerates");
  for(i=0;i<125;i=i+1)pulse_tick();
  check(settled && state==0 && settle_ticks==100,"500ms tracking settlement");
 end endtask
 initial begin
  repeat(3)@(negedge clk);reset_session();
  balance=0;send_command(1,0,1);balance=1;
  send_command(0,0,4);
  send_command(1,1,0);check(final_goal==1040 && direction==1,"CW one1040 turn");
  send_command(1,0,3);finish_finite(1);
  send_command(2,1,0);check(final_goal==0 && direction==-1,"CCW one1040 turn");finish_finite(-1);
  send_command(5,0,3);
  send_command(3,1,0);check(state==2 && direction==1,"left continuous direction mapping");
  ticks=0;while((reference_speed<519 || acceleration_q!=0 || ticks<100) && ticks<2000)begin pulse_tick();ticks=ticks+1;end
  check(state==2 && reference_speed>=519 && acceleration_q==0,"continuous steady speed permits adaptive qualification");
  send_command(3,0,3);old_target=target;old_velocity=velocity_q;old_accel=acceleration_q;
  send_command(4,1,0);check(state==3 && velocity_q==old_velocity && acceleration_q==old_accel && target==old_target,"opposite swipe brakes without resetting reference");
  ticks=0;while(!trajectory_done && ticks<2000)begin
   old_target=target;pulse_tick();ticks=ticks+1;
   check(target>=old_target && target-old_target<5,"positive braking stays continuous and never reverses");
  end
  check(trajectory_done && state==4 && velocity_q==0 && acceleration_q==0,"controlled brake reacheszero");
  for(i=0;i<125;i=i+1)pulse_tick();
  send_command(4,1,0);ticks=0;
  while((reference_speed> -519 || acceleration_q!=0 || ticks<100) && ticks<2000)begin pulse_tick();ticks=ticks+1;end
  check(state==2 && reference_speed<=-519 && acceleration_q==0,"right continuous reaches negative steady speed");
  send_command(3,1,0);ticks=0;while(!trajectory_done && ticks<2000)begin
   old_target=target;pulse_tick();ticks=ticks+1;
   check(target<=old_target && target-old_target> -5,"negative braking stays continuous and never reverses");
  end
  check(trajectory_done && velocity_q==0,"negative brake reacheszero");
  for(i=0;i<125;i=i+1)pulse_tick();
  cw_positive=0;send_command(1,1,0);check(direction==-1,"CW direction is configurable");
  @(negedge clk);cancel=1;command_valid=1;command=3;
  @(negedge clk);check(rejected && reason==6 && state==0 && velocity_q==0 && acceleration_q==0,"STOP cancel wins over simultaneous motion request");
  cancel=0;command_valid=0;repeat(5)@(negedge clk);
  follow=0;for(i=0;i<37;i=i+1)begin @(negedge clk);raw=raw+1;end
  repeat(4)@(negedge clk);old_target=actual;capture_hold=1;
  @(negedge clk);capture_hold=0;repeat(4)@(negedge clk);
  check(target==old_target && origin==old_target && final_goal==old_target && local_encoder==10000,"late capture holds current actual with no stale-target jump");
  cfg_id=0;cfg_value=300;cfg_write=1;@(negedge clk);check(cfg_error && !cfg_ok,"configuration rejected while balancing");
  cfg_write=0;balance=0;repeat(3)@(negedge clk);cfg_write=1;
  @(negedge clk);check(cfg_ok && !cfg_error,"stopped configuration accepted");cfg_write=0;
  repeat(3)@(negedge clk);check(cfg_read==300 && cfg_revision==1,"configuration independent readback and revision");
  cfg_write=1;cfg_value=521;@(negedge clk);check(cfg_error && !cfg_ok,"invalid finite speed rejected");cfg_write=0;
  reset_session();left_positive=0;send_command(3,1,0);check(direction==-1,"left direction configurable");
  // External-target lag beyond200 units must reject a fresh request.
  cancel=1;@(negedge clk);cancel=0;follow=0;
  for(i=0;i<202;i=i+1)begin @(negedge clk);raw=raw+1;end
  repeat(5)@(negedge clk);send_command(1,0,12);
  // The other physical kit must use1080 raw edges for the same1040 goal.
  kit=0;reset_session();cw_positive=1;left_positive=1;
  send_command(1,1,0);finish_finite(1);
  check(actual==1040 && physical==1080,"original kit finite turn retains1080 physical versus1040 control units");
  send_command(2,1,0);finish_finite(-1);
  check(actual==0 && physical>=-1 && physical<=1,"original kit opposite turn returns within physical quantizer residual");
  // A command on the tick and a braking command mid-pipeline invalidate the
  // obsolete calculation without adding an extra reference step.
  kit=1;reset_session();
  @(negedge clk);tick=1;command_valid=1;command=3;request_id=request_id+1;
  @(negedge clk);check(accepted && phase==1 && state==2,"tick-coincident command restarts one planner transaction");
  tick=0;command_valid=0;repeat(20)@(negedge clk);
  check(phase==0 && events==1,"tick-coincident command commits once");
  for(i=0;i<30;i=i+1)pulse_tick();
  @(negedge clk);tick=1;@(negedge clk);tick=0;
  while(phase!=4)@(negedge clk);
  command_valid=1;command=4;request_id=request_id+1;
  @(negedge clk);check(accepted && state==3 && phase==1,"opposite swipe invalidates in-flight pre-brake plan");
  command_valid=0;repeat(20)@(negedge clk);
  check(phase==0 && events==2,"recomputed braking transaction commits once");
  ticks=0;while(!trajectory_done && ticks<2000)begin pulse_tick();ticks=ticks+1;end
  check(trajectory_done && velocity_q==0,"mid-pipeline reversal request finishes controlled brake");
  // Completion of the reference alone does not claim a settled physical arm.
  reset_session();follow=0;send_command(1,1,0);ticks=0;
  while(!trajectory_done && ticks<5000)begin pulse_tick();ticks=ticks+1;end
  check(trajectory_done && !settled && actual==0 && target==1040,"stationary plant cannot masquerade as physical arrival");
  for(i=0;i<3005;i=i+1)pulse_tick();
  check(!settled && state==4 && stop_reason==11,"missing physical arrival exposes settlement timeout");
  $display("PASS planner checks=%0d",checks);$finish;
 end
 initial begin #20000000;$fatal(1,"planner timeout");end
endmodule
