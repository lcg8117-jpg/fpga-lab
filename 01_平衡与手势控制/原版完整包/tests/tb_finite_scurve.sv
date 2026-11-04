`timescale 1ns/1ps
module tb_finite_scurve;
 reg clk=0;always #5 clk=~clk;
 reg rst_n=0,start=0,cancel=0,tick=0;
 reg [17:0] speed=170394;
 reg [15:0] alim=524,jlim=13;
 wire active,ready,valid,finished,fault;
 wire [31:0] pos,vel;wire signed [31:0] acc;
 forest_motion_finite_scurve dut(clk,rst_n,start,cancel,tick,speed,alim,jlim,
  active,ready,valid,finished,fault,pos,vel,acc);
 integer checks=0,ticks=0,waited=0,i,oldpos,oldacc,peak;
 reg opstart=0,opcancel=0,multiply=0;
 reg [63:0] num=0;reg [31:0] den=1,l=0,r=0;
 wire busy,done,error;wire [63:0] result;
 reg [63:0] expected;
 forest_motion_serial_math math(clk,rst_n,opcancel,opstart,multiply,num,den,l,r,busy,done,error,result);
 task check(input bit ok,input string message);begin
  checks=checks+1;if(!ok)$fatal(1,"FINITE %s state=%0d phase=%0d step=%0d p=%0d v=%0d a=%0d",message,dut.state,dut.segment,ticks,pos,vel,acc);
 end endtask
 task operation(input bit mul,input [63:0] n,input [31:0] d,input [31:0] a,input [31:0] b);begin
  @(negedge clk);multiply=mul;num=n;den=d;l=a;r=b;opstart=1;
  @(negedge clk);opstart=0;waited=0;
  while(!done && waited<70)begin @(negedge clk);waited=waited+1;end
  check(done&&!error,"serial arithmetic bounded latency");
  expected=mul ? ({32'd0,a}*{32'd0,b}):n/d;
  check(result==expected,"serial arithmetic equals independent integer operation");
 end endtask
 task run_profile(input [17:0] v,input [15:0] a,input [15:0] j);begin
  @(negedge clk);speed=v;alim=a;jlim=j;start=1;
  @(negedge clk);start=0;waited=0;
  while(!ready && waited<400)begin @(negedge clk);waited=waited+1;end
  check(ready&&!fault,"parameter preparation bounded within8us at50MHz");
  oldpos=0;oldacc=0;ticks=0;peak=0;
  while(active && ticks<6000)begin
   @(negedge clk);tick=1;@(negedge clk);tick=0;waited=0;
   while(!valid && !fault && waited<240)begin @(negedge clk);waited=waited+1;end
   check(valid&&!fault,"one reference transaction per tick, below5us at50MHz");
   check(pos>=oldpos && pos<=68157440,"monotone reference never crosses exact one-turn endpoint");
   check(vel<=v && acc<=$signed({16'd0,a}) && acc>=-$signed({16'd0,a}),"speed and acceleration caps");
   check(acc-oldacc<=$signed({16'd0,j}) && acc-oldacc>=-$signed({16'd0,j}),"jerk cap includes terminal step");
   oldpos=pos;oldacc=acc;if(vel>peak)peak=vel;ticks=ticks+1;
   if(finished)begin check(pos==68157440 && vel==0 && acc==0,"exact endpoint withzero speed andacceleration");end
   repeat(3)@(negedge clk);
  end
  check(!active&&!fault && pos==68157440,"finite profile completes");
  if(v==170394 && a==524 && j==13)begin
   check(peak>=170390 && ticks==767,"default peak matches520cps continuous motion,3835ms turn");
  end
 end endtask
 initial begin
  repeat(3)@(negedge clk);rst_n=1;
  operation(0,64'hffffffffffffffff,32'hffffffff,0,0);
  operation(0,64'hffffffffffffffff,1,0,0);
  operation(1,0,1,32'hffffffff,32'hffffffff);
  for(i=0;i<200;i=i+1)begin
   operation(0,{$random,$random},($random|32'd1),0,0);
   operation(1,0,1,$random,$random);
  end
  run_profile(170394,524,13);
  run_profile(85196,524,13);
  run_profile(19660,131,3);
  run_profile(170394,1966,98);
  run_profile(19660,1966,3);
  run_profile(170394,131,98);
  run_profile(170394,524,3);
  run_profile(51199,1049,7);
  @(negedge clk);start=1;@(negedge clk);start=0;repeat(20)@(negedge clk);cancel=1;
  @(negedge clk);cancel=0;repeat(3)@(negedge clk);
  check(!active&&!ready&&!valid&&!fault,"cancel during parameter preparation never resumes");
  @(negedge clk);start=1;@(negedge clk);start=0;wait(ready);
  @(negedge clk);tick=1;@(negedge clk);tick=0;repeat(10)@(negedge clk);cancel=1;
  @(negedge clk);cancel=0;repeat(80)@(negedge clk);
  check(!active&&!valid&&!fault,"cancel during division never publishes obsolete position");
  $display("PASS FINITE_SCURVE checks=%0d profiles=8 default_turn_ms=3835 no_reference_overshoot=1",checks);$finish;
 end
 initial begin #100000000;$fatal(1,"finite test timeout");end
endmodule
