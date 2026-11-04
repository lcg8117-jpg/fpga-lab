`timescale 1ns/1ps
module live_planner_tb;
 reg clk=0;always #10 clk=~clk;
 reg rst_n=0,tick=0,reanchor=0,cancel=0,capture=0,balance=0,kit=0,valid=0,write=0,follow=1;
 reg signed [31:0] raw=0,value=0;
 reg [1:0] kind=0;reg [31:0] rid=1,job=0,cfg_value=0;reg [7:0] cfg_id=0;
 wire signed [63:0] actual,reference,origin,goal;
 wire signed [31:0] v,a;
 wire active,fault,accepted,rejected,done,settled,cfg_ok,cfg_error;
 wire [2:0] state;wire [7:0] reason,stop_reason;
 wire [31:0] lastjob,point_sequence,elapsed;wire signed [15:0] offset;
 forest_motion_planner #(.VARIANT(5)) dut(
  .clk(clk),.rst_n(rst_n),.tick_5ms(tick),.reanchor(reanchor),.cancel(cancel),.capture_hold(capture),
  .balance_active(balance),.kit_ip570(kit),.encoder_raw(raw),.command_valid(valid),.command(3'd0),
  .action_kind(kind),.action_index(3'd0),.request_id(rid),.left_positive(1'b1),.cw_positive(1'b1),
  .live_job(job),.live_value(value),.live_offset(offset),.live_sequence(point_sequence),.play_time_ms(elapsed),
  .cfg_write(write),.cfg_id(cfg_id),.cfg_value(cfg_value),.cfg_ok(cfg_ok),.cfg_error(cfg_error),
  .cfg_revision(),.cfg_read_value(),.enc_wide_physical(),.encoder_wide(actual),.target_wide(reference),
  .origin_wide(origin),.final_wide(goal),.encoder_local(),.target_local(),.active(active),.fault(fault),
  .state(state),.direction(),.accepted(accepted),.rejected(rejected),.reason(reason),.stop_reason(stop_reason),
  .event_count(),.last_request_id(lastjob),.last_action(),.trajectory_done(done),.tracking_settled(settled),
  .measured_velocity_cps(),.reference_velocity_cps(),.tracking_error(),.settle_ticks(),.planner_phase(),
  .velocity_q(v),.acceleration_q(a));
 integer checks=0,k,d,n,j,timeout_ticks;reg signed [31:0] oldv=0,olda=0;reg signed [63:0] wanted,saved_ref;
 always @*wanted=kit ? reference:(reference*27+(reference>=0 ? 25:-25))/26;
 always @(negedge clk)if(rst_n && balance && follow && !reanchor)begin
  if($signed(raw)<wanted)raw=raw+1;else if($signed(raw)>wanted)raw=raw-1;
 end
 task clocks(input integer count);repeat(count)@(negedge clk);endtask
 task reset_case(input integer whichkit,input integer direction);begin
  rst_n=0;balance=0;tick=0;valid=0;write=0;raw=0;kit=whichkit;follow=1;cancel=0;capture=0;clocks(5);
  rst_n=1;clocks(3);reanchor=1;clocks(1);reanchor=0;clocks(3);
  cfg_id=6;cfg_value=direction;write=1;clocks(1);if(!cfg_ok || cfg_error)$fatal(1,"direction config");
  write=0;clocks(3);balance=1;oldv=0;olda=0;clocks(3);repeat(25)step();
 end endtask
 task step;begin
  tick=1;clocks(1);tick=0;clocks(63);checks=checks+1;
  if(fault)$fatal(1,"unexpected fault kit=%0d dir=%0d n=%0d ref=%0d",k,d,n,reference);
  if(v-oldv!=a)$fatal(1,"acceleration provenance");
  if(a-olda>13 || olda-a>13 || a>524 || a< -524 || v>85196 || v< -85196)$fatal(1,"kinematic bound");
  oldv=v;olda=a;
 end endtask
 task request(input integer action,input integer token,input integer target,input integer success);begin
  rid=rid+1;kind=action;job=token;value=target;valid=1;clocks(1);checks=checks+1;
  if(accepted!==success[0] || rejected===success[0])$fatal(1,"command action=%0d token=%0d target=%0d state=%0d reason=%0d",action,token,target,state,reason);
  valid=0;clocks(3);
 end endtask
 task settle;begin
  j=0;while(state!=0 && j<5000)begin step();j=j+1;end
  if(state!=0 || v || a || !done || !settled)$fatal(1,"smooth stop failed");
 end endtask
 initial begin
  for(k=0;k<2;k=k+1)for(d=0;d<2;d=d+1)begin
   reset_case(k,d);request(1,999,20,0);request(2,999,0,0);request(0,0,0,1);
   if(lastjob!=rid || origin!=0 || elapsed!=0)$fatal(1,"BEGIN origin/id/time");
   for(n=0;n<400;n=n+1)begin
    if(n%4==0)begin
     request(1,lastjob,n<200 ? n/2:100-(n-200)/2,1);
     if(offset!==(d ? value[15:0]:-value[15:0]) || point_sequence!=rid)$fatal(1,"target echo ownership");
    end
    step();
    if(n==150 && reference==0)$fatal(1,"reference waited for end-of-drawing");
   end
   request(0,0,0,1);request(1,lastjob+1,15,0);request(1,lastjob,193,0);request(1,lastjob,-193,0);
   request(2,lastjob+1,0,0);saved_ref=reference;request(2,lastjob,0,1);
   if(reference!=saved_ref || state!=3)$fatal(1,"release jumped reference");
   request(1,lastjob,100,0);settle();
   // A new stroke can start where the previous one stopped; no hidden return-to-zero.
   request(0,0,0,1);request(1,lastjob,30,1);repeat(39)step();
   // Point exactly at the lease boundary is admitted and owns that tick.
   kind=1;job=lastjob;value=-30;rid=rid+1;valid=1;tick=1;clocks(1);
   if(!accepted || state!=6 || dut.live_age!=0)$fatal(1,"valid point lost at lease boundary");
   valid=0;tick=0;clocks(63);oldv=v;olda=a;
   timeout_ticks=0;while(state==6 && timeout_ticks<45)begin step();timeout_ticks=timeout_ticks+1;end
   if(timeout_ticks!=40 || stop_reason!=18)$fatal(1,"200ms stream timeout");
   request(1,lastjob,20,0);settle();request(1,lastjob,20,0);
   // Hand input must remain available while balancing corrections are moving.
   reset_case(k,d);follow=0;
   for(j=0;j<17;j=j+1)begin raw=raw+1;clocks(1);end
   repeat(20)step();
   if(dut.settled_now)$fatal(1,"moving fixture accidentally settled");
   request(0,0,0,1);request(1,lastjob,100,1);
   for(n=0;n<100;n=n+1)begin if(n%4==0)request(1,lastjob,100,1);step();end
   if(v==0)$fatal(1,"moving reference fixture stationary");
   saved_ref=reference;oldv=v;olda=a;request(2,lastjob,0,1);
   request(0,0,0,1);
   if(reference!=saved_ref || v!=oldv || a!=olda || state!=6)$fatal(1,"new stroke must continue from moving reference");
   request(1,lastjob,-100,1);repeat(10)step();
   // A new mouse press at the point lease boundary replaces the old stroke.
   repeat(29)step();kind=0;rid=rid+1;valid=1;tick=1;clocks(1);
   if(!accepted || state!=6 || dut.live_age!=0)$fatal(1,"new BEGIN lost at old lease boundary");
   valid=0;tick=0;clocks(63);oldv=v;olda=a;
   request(1,lastjob,0,1);request(2,lastjob,0,1);
   n=0;while(state==3 && n<1000)begin step();n=n+1;end
   if(state!=4 || dut.settled_now)$fatal(1,"unsettled end fixture");
   request(0,0,0,1);request(1,lastjob,0,1);

  end
  for(k=0;k<2;k=k+1)begin
   reset_case(k,1);kind=0;valid=1;cancel=1;clocks(1);
   if(accepted || !rejected || active)$fatal(1,"STOP lost to BEGIN");valid=0;cancel=0;
   reset_case(k,1);request(0,0,0,1);request(1,lastjob,100,1);repeat(20)step();balance=0;clocks(3);
   if(active || v || a)$fatal(1,"balance loss left stream active");balance=1;clocks(3);if(active)$fatal(1,"auto restart");
   reset_case(k,1);follow=0;for(j=0;j<410;j=j+1)begin raw=raw+1;clocks(1);end
   if(!fault || active)$fatal(1,"position envelope");
   reset_case(k,1);request(0,0,0,1);follow=0;
   for(j=0;j<4400;j=j+1)begin raw=j[0] ? 1:0;clocks(1);end
   if(!fault || active)$fatal(1,"physical travel envelope");
   reset_case(k,1);request(0,0,0,1);
   for(n=0;n<30005;n=n+1)begin
    if(n%20==0 && !fault)request(1,lastjob,0,1);
    tick=1;clocks(1);tick=0;clocks(63);
   end
   if(!fault || active)$fatal(1,"150s stroke limit");checks=checks+1;
  end
  $display("PASS live_planner checks=%0d kits=2 directions=2 live_follow_before_release=1 lease_ms=200 idealFollowerOnly=1",checks);$finish;
 end
endmodule
