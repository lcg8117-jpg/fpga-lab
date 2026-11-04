`timescale 1ns/1ps
module tb;
reg clk=0;always #10 clk=~clk;reg rst=0;reg signed[15:0] encoder_value=10000;
wire an1,an2,pa,bn1,bn2,pb,adcclk,oe,tx;tri1 scl,sda;
forest_d1_system #(.VARIANT(1),.STARTUP_CYCLES(0),.KEY_STABLE_CYCLES(1),.ZERO_SAMPLES(4),
 .FEATURE_SWING(1),.FEATURE_THREE_PROFILES(0),.FEATURE_GESTURE(1),.FEATURE_MOTION(1),.FEATURE_ADAPTIVE(1),.FEATURE_AUTORETURN(0),
 .FIRMWARE_ID(32'h41444231),.SCHEMA(32'h41445001),.PROTOCOL_VERSION(3),.PROTOCOL_VARIANT(16),.CAPABILITIES(15)) d(
 .clk_50m(clk),.rst_n(rst),.key_sw(4'hf),.uart_rx(1'b1),.uart_tx(tx),.adc_clk(adcclk),.adc_oe_n(oe),
 .adc_data_in(10'd512),.adc_otr(1'b0),.enc1_a(1'b0),.enc1_b(1'b0),
 .AN1(an1),.AN2(an2),.PWMA(pa),.BN1(bn1),.BN2(bn2),.PWMB(pb),.gesture_scl(scl),.gesture_sda(sda));
integer cycle=0,fast_n=0,slow_n=0,p_n=0,d_n=0,pub_n=0,pfd=0;
integer last_fast=-1,last_slow=-1,last_p=-1,last_d=-1,last_pub=-1,last_fast_launch=-1,last_slow_launch=-1;
reg[31:0] expected_ad,expected_pd,expected_ae,expected_pe,previous_p=0,previous_d=0;
reg signed[15:0] expected_encoder;integer packets;
always @(posedge clk)begin
 cycle=cycle+1;
 if(d.core.fast_launch)begin
  if(last_fast_launch>=0 && cycle-last_fast_launch!=50000)$fatal(1,"Fast sample cadence not1ms");
  last_fast_launch=cycle;
  expected_ad<=d.core.completed_angle_d_bits;expected_pd<=d.core.completed_position_d_bits;
  expected_ae<=d.core.completed_angle_epoch;expected_pe<=d.core.completed_position_epoch;
  expected_encoder<=d.fast_control_encoder;
 end
 if(d.sample_en)begin
  if(last_slow_launch>=0 && cycle-last_slow_launch!=250000)$fatal(1,"Original D sampling cadence changed");
  last_slow_launch=cycle;
 end
 if(d.core.bd)begin
  last_slow=cycle;slow_n=slow_n+1;
 end
 if(d.core.fd)begin
  if(d.core.fast.angle_d_bits!==expected_ad || d.core.fast.position_d_bits!==expected_pd ||
     d.core.fast.angle_d_epoch!==expected_ae || d.core.fast.position_d_epoch!==expected_pe)
    $fatal(1,"D was recalculated/mixed instead of completed-D snapshot");
  if(d.core.fast.position_error!==($signed(expected_encoder)-17'sd10000))$fatal(1,"Fast P lost motion-relative encoder");
  last_fast=cycle;fast_n=fast_n+1;
  if(d.core.fast.position_p_epoch!=previous_p)begin
   if(last_p>=0 && d.core.fast.position_p_epoch-previous_p!=5)$fatal(1,"Position P not5ms");
   previous_p=d.core.fast.position_p_epoch;last_p=cycle;p_n=p_n+1;
  end
  if(d.core.fast.position_d_epoch!=previous_d)begin
   if(last_d>=0 && d.core.fast.position_d_epoch-previous_d!=25)$fatal(1,"Position D not25ms");
   previous_d=d.core.fast.position_d_epoch;last_d=cycle;d_n=d_n+1;
  end
 end
 if(d.publish_control && d.captured_control_fast)begin
  if(last_pub>=0 && d.captured_epoch-last_pub!=5)$fatal(1,"Telemetry not200Hz");
  last_pub=d.captured_epoch;pub_n=pub_n+1;
 end
 if(d.tlm_valid)begin $fdisplay(pfd,"%h",d.tlm_data);packets=packets+1;end
end
initial begin
 pfd=$fopen("packets.hex","w");packets=0;
 repeat(5)@(negedge clk);rst=1;repeat(10)@(negedge clk);
 force d.zero_valid=1;force d.zero_q4=14'd8192;force d.encoder_zero=0;
 force d.mode=3;force d.balance_permission=1;force d.run_session=1;
 force d.motion_session=1;force d.control_encoder=encoder_value;
 repeat(800000)@(negedge clk);encoder_value=10030;
 repeat(800000)@(negedge clk);encoder_value=9980;
 repeat(1800000)@(negedge clk);
 if(!d.control_fast_p || !d.compare_ready)$fatal(1,"Dedicated fast P never actually selected");
 if(d.compare_deadline_fault || d.fast_acquisition_fault)$fatal(1,"Acquisition/compute deadline fault");
 if(fast_n<60 || slow_n<12 || p_n<12 || d_n<2 || pub_n<12)$fatal(1,"Insufficient real transactions");
 $fclose(pfd);
 $display("PASS fast1ms=%0d originalD5ms=%0d positionP5ms=%0d positionD25ms=%0d telemetry200Hz=%0d packets=%0d",fast_n,slow_n,p_n,d_n,pub_n,packets);
 $finish;
end
endmodule
