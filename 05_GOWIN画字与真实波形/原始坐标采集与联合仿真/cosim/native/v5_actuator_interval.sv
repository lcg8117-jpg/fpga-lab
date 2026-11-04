`timescale 1ns/1ps
// Simulation adapter only. Production actuator and its parameters are frozen.
module v5_actuator_interval;
 reg clk=0,rst_n=0,enabled=0;
 integer prefix1_q8=0,prefix2_q8=0,steady_q8=0;
 reg signed [31:0] request=0;
 wire n1,n2,pwm;wire signed [15:0] requested_q15;wire [31:0] diag;
 forest_d1_run_actuator actual(.clk(clk),.rst_n(rst_n),
  .motor_permitted(enabled),.core_moto(request),.BN1(n1),.BN2(n2),
  .PWMB(pwm),.duty_actual_q15(requested_q15),.diag(diag));
 task tick;begin #10;clk=1;#10;clk=0;end endtask
 initial begin repeat(4)tick();rst_n=1;repeat(4)tick();end
 // At a stable command-frame boundary, whole identical 100us frames return
 // every driver register to the same state. Check one extra stable frame,
 // then omit only its identical repetitions, retaining exact pin duty.
 task advance(input integer moto,input integer permit,input integer fast,
              output integer signed_high_cycles,output integer average_q15_q8);
  integer k,limit,frame1,frame2,frame3,frame4,term;reg [31:0] stable_diag;integer initial_carrier,initial_frame;
  begin
   initial_carrier=actual.motor_driver.carrier_count;initial_frame=actual.motor_driver.frame_count;request=moto;enabled=permit;frame1=0;frame2=0;frame3=0;frame4=0;signed_high_cycles=0;
   limit=fast ? 20000:50000;
   for(k=0;k<limit;k=k+1)begin
    tick();term=pwm ? (n2 ? -1:1):0;
    signed_high_cycles=signed_high_cycles+term;
    if(k<5000)frame1=frame1+term;
    if(k>=5000 && k<10000)frame2=frame2+term;
    if(k>=10000 && k<15000)frame3=frame3+term;
    if(k==14999)stable_diag=diag;
    if(k>=15000 && k<20000)frame4=frame4+term;
   end
   if(fast)begin
    if(frame3!=frame4 || stable_diag!==diag || diag[18])
     $fatal(1,"Actuator omission prerequisite failed");
    if(permit && moto>=-6900 && moto<=6900 && (actual.motor_driver.carrier_count!=initial_carrier || actual.motor_driver.frame_count!=initial_frame))
     $fatal(1,"Actuator frame alignment failed");
    signed_high_cycles=signed_high_cycles+6*frame4;
   end
   prefix1_q8=$rtoi($itor(frame1)*32768.0*256.0/5000.0);
   prefix2_q8=$rtoi($itor(frame2)*32768.0*256.0/5000.0);
   steady_q8=$rtoi($itor(frame4)*32768.0*256.0/5000.0);
   average_q15_q8=$rtoi($itor(signed_high_cycles)*32768.0*256.0/50000.0);
  end
 endtask
endmodule
