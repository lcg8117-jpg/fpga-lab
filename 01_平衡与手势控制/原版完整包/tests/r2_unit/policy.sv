`timescale 1ns/1ps
module tb;
reg clk=0;always #10 clk=~clk;
reg rst=0,sample=0,commit=0,running=1,stop=0,manual=0,enwrite=0,envalue=0,fault=0,sat=0;
reg[191:0] gains=192'h441b00000000000041c80000438200000000000042440000;
reg[31:0] rev=0,epoch=0;reg signed[15:0] error=0,diff=0,actual=0;reg signed[31:0] pos=0,moto=0;
wire enabled,active;wire[191:0] target;wire[7:0] freeze;wire signed[15:0] cp,cd,fp,fd;
wire[31:0] learn,adopt,ce,cr;integer n=0,wave_triangle=0,value=0,previous=0,learned=0,above64=0,maximumD=0,noise=0;
reg signed[7:0] lastp=0,lastd=0;
forest_d1_adaptive d(.clk(clk),.rst_n(rst),.sample_valid(sample),.commit_tick(commit),.run_balance(running),.stop(stop),
 .manual_write(manual),.enable_write(enwrite),.enable_value(envalue),.gains_base(gains),.manual_revision(rev),.sample_epoch(epoch),
 .angle_error(error),.angle_diff(diff),.position_error(pos),.requested_moto(moto),.actual_q15(actual),.saturating(sat),.fault(fault),
 .target_gains(target),.enabled(enabled),.active(active),.freeze_reason(freeze),.center_p_q8(cp),.center_d_q8(cd),.fast_p_q8(fp),.fast_d_q8(fd),
 .learn_revision(learn),.adapt_epoch(adopt),.candidate_epoch(ce),.candidate_revision(cr));
task command;input value;begin @(negedge clk);enwrite=1;envalue=value;@(negedge clk);enwrite=0;end endtask
task tick;input integer e,de;begin
 @(negedge clk);sample=1;epoch=epoch+5;error=e;diff=de;commit=n%5==0;@(negedge clk);sample=0;commit=0;
 repeat(22)@(negedge clk);n=n+1;
 if(cd<0||cd>96||fd<0||cd+fd>112||fp < -16||fp>16)$fatal(1,"policy range");
 if(active)begin
  if(target[191:96]!==gains[191:96]||target[63:32]!==gains[63:32])$fatal(1,"position/I changed");
  if(target[31:0]<32'h4237c000||target[31:0]>32'h42504000||target[95:64]<32'h43820000||target[95:64]>32'h43bae000)$fatal(1,"actual gain bounds");
  if(cr!=rev)$fatal(1,"stale revision");
 end
 if(cd>64)above64=above64+1;if(cd+fd>maximumD)maximumD=cd+fd;
end endtask
always @(posedge clk)if(rst)begin
 if(commit&&active)begin
  if($signed(d.candidate_p)-$signed(d.applied_p)>2||$signed(d.applied_p)-$signed(d.candidate_p)>2||
     $signed(d.candidate_d)-$signed(d.applied_d)>2||$signed(d.applied_d)-$signed(d.candidate_d)>2)$fatal(1,"slew limit");
 end
end
initial begin
 repeat(4)@(negedge clk);rst=1;repeat(4)@(negedge clk);
 if(enabled||target!==gains)$fatal(1,"default off");command(1);
 repeat(256)begin tick(n%3-1,0);if(cd!=0||fp!=0||fd!=0||target!==gains)$fatal(1,"quiet changed baseline");end
 for(integer i=0;i<14000;i=i+1)begin
  wave_triangle=(i%128<64 ? i%128:(127-i%128));wave_triangle=2*wave_triangle-63;
  value=wave_triangle*(256-d.center_d)/256;tick(value,value-previous);previous=value;
 end
 learned=learn;if(learned<1||above64<1||maximumD<=64)$fatal(1,"extended learning range not exercised learn%0d above%0d maxD%0d",learned,above64,maximumD);
 // A live manual write immediately takes ownership and invalidates the trial.
 @(negedge clk);manual=1;rev=rev+1;@(negedge clk);manual=0;repeat(15)@(negedge clk);
 if(enabled||active||target!==gains||d.scale_armed)$fatal(1,"manual ownership");
 // Cancel scaler publication with unchanged manual revision.
 command(1);tick(30,1);@(negedge clk);sample=1;epoch=epoch+5;@(negedge clk);sample=0;
 wait(d.scale_start);@(negedge clk);enwrite=1;envalue=0;@(negedge clk);enwrite=0;
 command(1);repeat(15)@(negedge clk);
 if(d.candidate_valid||d.scale_armed)$fatal(1,"canceled scaler published stale bank");
 // Guards must preserve manual bank and never enable a motor.
 gains[63:32]=32'h3dcccccd;tick(20,1);if(active||freeze!=3||target!==gains)$fatal(1,"integral guard");
 gains[63:32]=0;fault=1;tick(20,1);if(active||freeze!=2)$fatal(1,"fault guard");fault=0;
 stop=1;tick(20,1);if(active||freeze!=1)$fatal(1,"stop guard");stop=0;
 command(1);sat=1;tick(20,1);if(freeze!=5)$fatal(1,"saturation guard");sat=0;
 command(1);tick(256,1);if(freeze!=6)$fatal(1,"envelope guard");
 command(1);repeat(20)begin tick(n%2 ? 8:-8,n%2 ? 4:-4);if(freeze==7)noise=noise+1;end
 if(noise==0)$fatal(1,"noise guard not reached");command(0);if(active||target!==gains)$fatal(1,"disabled baseline");
 $display("PASS V3 wider policy: accepted_centers=%0d samples_above_old64=%0d maxTotalD=%0d; quiet exact, manual/cancel/I/fault/STOP/noise/saturation/envelope guards",learned,above64,maximumD);$finish;
end
initial begin #200000000;$fatal(1,"timeout");end
endmodule
