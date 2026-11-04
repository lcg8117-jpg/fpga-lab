`timescale 1ns/1ps
module tb_brake_step;
 reg clk=0;always #5 clk=~clk;
 reg rst_n=0,start=0,cancel=0;
 reg signed [31:0] v=0,a=0;
 wire busy,done,fault;wire signed [31:0] nv,na;
 integer checks=0,profiles=0,steps,cycles,largest_latency=0,k,seed=42;
 integer random_v,random_a,release_cost,mag;
 forest_motion_brake_step dut(.clk(clk),.rst_n(rst_n),.start(start),.cancel(cancel),
  .velocity(v),.acceleration(a),.busy(busy),.done(done),.fault(fault),
  .next_velocity(nv),.next_acceleration(na));
 task check;input good;input [511:0] msg;begin
  checks=checks+1;if(good!==1'b1)begin $display("FAIL BRAKE %0s v=%0d a=%0d nv=%0d na=%0d",msg,v,a,nv,na);$fatal(1);end
 end endtask
 task one_step;begin
  @(negedge clk);start=1;@(negedge clk);start=0;cycles=0;
  while(!done && cycles<512)begin @(negedge clk);cycles=cycles+1;end
  if(cycles>largest_latency)largest_latency=cycles;
  check(done && !busy && !fault,"valid state produces a bounded completed result");
  check(na>=-1966 && na<=1966,"original brake acceleration bound");
  check(na-a>=-98 && na-a<=98,"original brake jerk bound");
  check(nv-v==na,"discrete velocity/acceleration are consistent");
  if(v>0)check(nv>=0,"positive stop never reverses");
  if(v<0)check(nv<=0,"negative stop never reverses");
  v=nv;a=na;
 end endtask
 task profile;input signed [31:0] iv,ia;input integer bound;begin
  v=iv;a=ia;steps=0;
  while((steps==0 || v!=0 || a!=0) && steps<bound)begin one_step();steps=steps+1;end
  check(v==0 && a==0 && steps<bound,"finite exact stop with no exponential tail");
  profiles=profiles+1;
 end endtask
 initial begin
  repeat(3)@(negedge clk);rst_n=1;
  profile(170394,0,121);profile(-170394,0,121);
  profile(93559,522,150);profile(24565,-522,150);profile(1950,-229,150);
  profile(170394,1966,180);profile(-170394,-1966,180);profile(0,0,4);
  profile(1,-1,4);profile(-1,1,4);profile(0,1966,180);
  for(k=0;k<256;k=k+1)begin
   random_v=$unsigned($random(seed))%180000;
   random_a=($unsigned($random(seed))%3933)-1966;
   release_cost=0;mag=-random_a;
   while(mag>98)begin mag=mag-98;release_cost=release_cost+mag;end
   if(random_a<0 && random_v<release_cost)random_v=release_cost;
   if(k[0])profile(-random_v,-random_a,200);else profile(random_v,random_a,200);
  end
  // Invalid arithmetic state reports failure instead of waiting without end.
  v=1;a=-1966;@(negedge clk);start=1;@(negedge clk);start=0;cycles=0;
  while(!done && cycles<512)begin @(negedge clk);cycles=cycles+1;end
  check(done && fault && !busy,"infeasible internal state has a bounded explicit failure");
  cancel=1;@(negedge clk);cancel=0;
  v=170394;a=-1966;start=1;@(negedge clk);start=0;repeat(5)@(negedge clk);
  cancel=1;@(negedge clk);cancel=0;repeat(20)@(negedge clk);
  check(!busy && !done && !fault,"cancel cannot replay an old brake result");
  $display("PASS BRAKE_STEP checks=%0d profiles=%0d maximum_step_clocks=%0d",checks,profiles,largest_latency);$finish;
 end
 initial begin #100000000;$fatal(1,"brake step timeout");end
endmodule
