`timescale 1ns/1ps
module session_replica_tb;
 reg clk=0;always #10 clk=~clk;
 reg rst_n=0,rx=1;reg [3:0] keys=15;reg [9:0] adc=512;reg [1:0] enc_pins=0;
 wire tx,adc_clk,adc_oe,an1,an2,pwma,bn1,bn2,pwmb;tri1 scl,sda;
 forest_d1_system #(.VARIANT(1),.STARTUP_CYCLES(0),.KEY_STABLE_CYCLES(2),.ZERO_SAMPLES(4),
  .FEATURE_SWING(1),.FEATURE_THREE_PROFILES(1),.FEATURE_GESTURE(1),.FEATURE_MOTION(1),
  .FEATURE_ADAPTIVE(1),.FEATURE_AUTORETURN(1),.PROTOCOL_VERSION(3),.PROTOCOL_VARIANT(16),
  .FIRMWARE_ID(32'h4a325531),.SCHEMA(32'h4a325503),.CAPABILITIES(31),
  .SWING_CAPTURE_ALPHA_Q20(32'sd274517),.SWING_CAPTURE_AD_Q20(32'sd3660218),
  .SWING_CAPTURE_THD_Q20(32'sd2562155),.SWING_HANDOFF_ENTER_Q4(634)) dut(
  .clk_50m(clk),.rst_n(rst_n),.key_sw(keys),.uart_rx(rx),.uart_tx(tx),
  .adc_clk(adc_clk),.adc_oe_n(adc_oe),.adc_data_in(adc),.adc_otr(1'b0),
  .enc1_a(enc_pins[1]),.enc1_b(enc_pins[0]),.AN1(an1),.AN2(an2),.PWMA(pwma),
  .BN1(bn1),.BN2(bn2),.PWMB(pwmb),.gesture_scl(scl),.gesture_sda(sda));
 wire ref_tx,ref_adc_clk,ref_adc_oe,ref_an1,ref_an2,ref_pwma,ref_bn1,ref_bn2,ref_pwmb;
 tri1 ref_scl,ref_sda;
 REF_forest_d1_system #(.VARIANT(1),.STARTUP_CYCLES(0),.KEY_STABLE_CYCLES(2),.ZERO_SAMPLES(4),
  .FEATURE_SWING(1),.FEATURE_THREE_PROFILES(1),.FEATURE_GESTURE(1),.FEATURE_MOTION(1),
  .FEATURE_ADAPTIVE(1),.FEATURE_AUTORETURN(1),.PROTOCOL_VERSION(3),.PROTOCOL_VARIANT(16),
  .FIRMWARE_ID(32'h4a325531),.SCHEMA(32'h4a325503),.CAPABILITIES(31),
  .SWING_CAPTURE_ALPHA_Q20(32'sd274517),.SWING_CAPTURE_AD_Q20(32'sd3660218),
  .SWING_CAPTURE_THD_Q20(32'sd2562155),.SWING_HANDOFF_ENTER_Q4(634)) base(
  .clk_50m(clk),.rst_n(rst_n),.key_sw(keys),.uart_rx(rx),.uart_tx(ref_tx),
  .adc_clk(ref_adc_clk),.adc_oe_n(ref_adc_oe),.adc_data_in(adc),.adc_otr(1'b0),
  .enc1_a(enc_pins[1]),.enc1_b(enc_pins[0]),.AN1(ref_an1),.AN2(ref_an2),.PWMA(ref_pwma),
  .BN1(ref_bn1),.BN2(ref_bn2),.PWMB(ref_pwmb),.gesture_scl(ref_scl),.gesture_sda(ref_sda));
 integer clocks=0,reply_count=0,control_packets=0,diagnostic_packets=0,extension_packets=0,received_bytes=0;
 integer packet_index=0,packet_length=0,wire_errors=0,commands=0,source_accepts=0,motion_accepts=0;
 integer checked_banks=0,moving_adoptions=0,moving_changed_adoptions=0,watchdog_events=0;
 integer log_file,adoption_log,feature_log,physical_count=0,follow_div=0,excitation_div=0,excitation_tick=0;
 reg uart_owner=0;reg follow_motion=0,angle_excitation=0;reg signed [63:0] desired_physical;
 reg [7:0] packet[0:255];reg [31:0] header=0;
 reg [623:0] last_reply=0;reg [31:0] next_id=1;
 reg [191:0] expected_bank;reg [31:0] expected_feature,expected_adoption,expected_revision;
 reg expected_adaptive,expected_commit;reg [7:0] received;
 function [15:0] crc_byte(input [15:0] crc,input [7:0] value);
  reg [15:0] t;integer b;begin t=crc^{value,8'd0};for(b=0;b<8;b=b+1)t=t[15]?(t<<1)^16'h1021:t<<1;crc_byte=t;end
 endfunction
 task accept_byte(input [7:0] value);
  integer j;reg [15:0] crc;reg [623:0] response;
  begin
   received_bytes=received_bytes+1;
   if(packet_length==0)begin
    header={value,header[31:8]};
    if(header==32'h41324446 || header==32'h54324446 || header==32'h44324446 || header==32'h45324446)begin
     packet[0]=header[7:0];packet[1]=header[15:8];packet[2]=header[23:16];packet[3]=header[31:24];
     packet_length=header==32'h41324446 ? 80:256;packet_index=4;
    end
   end else begin
    packet[packet_index]=value;packet_index=packet_index+1;
    if(packet_index==packet_length)begin
     crc=16'hffff;for(j=0;j<packet_length-2;j=j+1)crc=crc_byte(crc,packet[j]);
     if(crc!=={packet[packet_length-1],packet[packet_length-2]})$fatal(1,"Pin-level UART CRC mismatch packet=%h",header);
     if(packet[4]!==3 || (header==32'h41324446 ? packet[7]!==16:packet[5]!==16))
      $fatal(1,"Pin-level UART identity/version mismatch magic=%h version=%h variant=%h/%h",header,packet[4],packet[5],packet[7]);
     if(header==32'h41324446)begin
      response=0;for(j=0;j<80;j=j+1)response[j*8+:8]=packet[j];last_reply=response;reply_count=reply_count+1;
     end else if(header==32'h54324446)control_packets=control_packets+1;
     else if(header==32'h44324446)diagnostic_packets=diagnostic_packets+1;
     else extension_packets=extension_packets+1;
     packet_length=0;packet_index=0;header=0;
    end
   end
  end
 endtask
 // Independent pin receiver: actual2Mbaud,8N1; no observation of internal TX RAM.
 initial forever begin : receive_pin_byte
  @(negedge tx);
  begin : byte_active
   if(!rst_n)disable byte_active;
   repeat(12)begin @(posedge clk);if(!rst_n)disable byte_active;end #1;
   if(tx!==0)$fatal(1,"UART start sample invalid");
   for(integer bit_index=0;bit_index<8;bit_index=bit_index+1)begin
    repeat(25)begin @(posedge clk);if(!rst_n)disable byte_active;end #1;received[bit_index]=tx;
   end
   repeat(25)begin @(posedge clk);if(!rst_n)disable byte_active;end #1;
   if(tx!==1)$fatal(1,"UART stop sample invalid");accept_byte(received);
  end
 end
 always @(negedge rst_n)begin packet_length=0;packet_index=0;header=0;last_reply=0;end
 task send_byte(input [7:0] value);
  begin
   rx=0;repeat(25)@(negedge clk);
   for(integer b=0;b<8;b=b+1)begin rx=value[b];repeat(25)@(negedge clk);end
   rx=1;repeat(25)@(negedge clk);
  end
 endtask
 task automatic command(input [7:0] opcode,input [7:0] group,input [7:0] length,
              input [255:0] payload,input [7:0] expected_status);
  reg [383:0] frame;reg [15:0] crc;reg [31:0] id;integer j,wait_cycles;
  begin
   wait(!uart_owner);uart_owner=1;id=next_id;next_id=next_id+1;frame=0;
   frame[31:0]=32'h43324446;frame[39:32]=3;frame[47:40]=opcode;frame[55:48]=group;
   frame[63:56]=length;frame[95:64]=id;frame[351:96]=payload;
   crc=16'hffff;for(j=0;j<46;j=j+1)crc=crc_byte(crc,frame[j*8+:8]);frame[383:368]=crc;
   @(negedge clk);for(j=0;j<48;j=j+1)send_byte(frame[j*8+:8]);
   wait_cycles=0;while(last_reply[95:64]!=id && wait_cycles<1000000)begin @(negedge clk);wait_cycles=wait_cycles+1;end
   if(last_reply[95:64]!=id)$fatal(1,"UART reply timeout opcode=%h request=%0d errors=%0d",opcode,id,dut.rx_errors);
   if(last_reply[55:48]!==expected_status || last_reply[47:40]!==opcode || last_reply[607:576]!==32'h4a325531)
    $fatal(1,"UART command rejected opcode=%h group=%0d request=%0d status=%0d expected=%0d FW=%h mode=%0d fault=%h",
     opcode,group,id,last_reply[55:48],expected_status,last_reply[607:576],dut.mode,dut.fault);
   commands=commands+1;$fdisplay(log_file,"%0d,%0d,%h,%0d,%0d,%0d,%h",clocks,id,opcode,group,last_reply[55:48],dut.mode,last_reply[415:384]);uart_owner=0;$fflush(log_file);
  end
 endtask
 task read_config(input [7:0] group,input [7:0] field,input [31:0] expected);
  begin command(8'h32,group,1,{248'd0,field},0);if(last_reply[415:384]!==expected)$fatal(1,"Config read mismatch group=%0d field=%0d value=%h expected=%h",group,field,last_reply[415:384],expected);end
 endtask
 task write_config(input [7:0] group,input [7:0] field,input [31:0] value);
  begin command(8'h33,group,5,{216'd0,value,field},0);end
 endtask
 task milliseconds(input integer count);begin repeat(count*50000)@(negedge clk);end endtask
 localparam [191:0] DEFAULT_GAINS={32'h44188000,32'd0,32'h41c80000,32'h437b0000,32'd0,32'h42440000};
 integer checks=0,cycle_checks=0,done_checks=0,sample_checks=0,telemetry_checks=0,reply_checks=0;
 integer kit_pulses=0,pwm_high=0,valid_writes=0,rejected_writes=0,i;
 task check(input bit condition,input string message);begin
  checks=checks+1;if(!condition)$fatal(1,"Calibration replica %s clock=%0d mode=%0d/%0d fault=%h/%h",message,clocks,dut.mode,base.mode,dut.fault,base.fault);
 end endtask
 task press(input integer index);begin
  @(negedge clk);keys[index]=0;repeat(12)@(negedge clk);keys[index]=1;repeat(12)@(negedge clk);
 end endtask
 task edge_step(input bit positive);begin
  @(negedge clk);
  if(positive)case(enc_pins)0:enc_pins=2;2:enc_pins=3;3:enc_pins=1;1:enc_pins=0;endcase
  else case(enc_pins)0:enc_pins=1;1:enc_pins=3;3:enc_pins=2;2:enc_pins=0;endcase
 end endtask
 task kit_write(input [31:0] value,input [7:0] status);begin
  command(8'h33,254,5,{216'd0,value,8'd5},status);
  if(status==0)valid_writes=valid_writes+1;else rejected_writes=rejected_writes+1;
 end endtask
 task fresh_zero;begin
  adc=512;command(8'h10,0,0,0,0);milliseconds(8);press(0);
  wait(dut.zero_valid && !dut.capture_active && base.zero_valid && !base.capture_active);
  wait(dut.source_ready && base.source_ready);
 end endtask
 task external_reset;begin
  @(negedge clk);rst_n=0;rx=1;keys=15;adc=512;enc_pins=0;repeat(12)@(negedge clk);rst_n=1;milliseconds(12);
 end endtask
 // Compare after NBA updates on every actual clock, including reset cycles.
 always @(posedge clk)begin
  clocks=clocks+1;#1;
  if(dut.run_session_controls !== dut.run_session)$fatal(1,"SESSION_REPLICA_EDGE_MISMATCH clock=%0d",clocks);
  if(dut.source_window_bad !== ((dut.raw_guard_mean<2285 || dut.raw_guard_mean>3885) || dut.angle<12'd2285 || dut.angle>12'd3885))
   $fatal(1,"SYSTEM_GUARD_COHORT_MISMATCH clock=%0d",clocks);
  if(dut.source_mean_combined_bad !== base.source_mean_combined_bad)
   $fatal(1,"SYSTEM_GUARD_REFERENCE_MISMATCH clock=%0d",clocks);
  if(dut.kit_changed_motor!==dut.kit_changed || dut.kit_changed!==base.kit_changed)
   $fatal(1,"REPLICA_EDGE_MISMATCH clock=%0d original=%b replica=%b ref=%b",clocks,dut.kit_changed,dut.kit_changed_motor,base.kit_changed);
  if(dut.kit_changed)kit_pulses=kit_pulses+1;
  if(rst_n && dut.control_rst_n && base.control_rst_n)begin
   cycle_checks=cycle_checks+1;
   if({tx,adc_clk,adc_oe,an1,an2,pwma,bn1,bn2,pwmb,scl,sda} !== {ref_tx,ref_adc_clk,ref_adc_oe,ref_an1,ref_an2,ref_pwma,ref_bn1,ref_bn2,ref_pwmb,ref_scl,ref_sda})
    $fatal(1,"SESSION_REPLICA_EXTERNAL_PIN_MISMATCH clock=%0d",clocks);
   if({dut.raw,dut.clean,dut.filtered,dut.enc,dut.invalid,dut.kit_ip570,dut.mode,dut.fault,dut.zero_valid,dut.capture_active,dut.run_session,dut.zero_q4,dut.encoder_zero,
       dut.sample_en,dut.done,dut.busy,dut.position_div,dut.target,dut.balance_permission,dut.swing_permission,dut.source_ready,dut.prime_pending,dut.actual_balance_permission,
       dut.moto,dut.balance_pwm,dut.position_pwm,dut.actuator_diag,dut.core_gains,dut.stored_gains,dut.active_revision} !==
      {base.raw,base.clean,base.filtered,base.enc,base.invalid,base.kit_ip570,base.mode,base.fault,base.zero_valid,base.capture_active,base.run_session,base.zero_q4,base.encoder_zero,
       base.sample_en,base.done,base.busy,base.position_div,base.target,base.balance_permission,base.swing_permission,base.source_ready,base.prime_pending,base.actual_balance_permission,
       base.moto,base.balance_pwm,base.position_pwm,base.actuator_diag,base.core_gains,base.stored_gains,base.active_revision})
    $fatal(1,"SESSION_REPLICA_CONTROL_MISMATCH clock=%0d mode=%0d/%0d",clocks,dut.mode,base.mode);
   if({dut.tlm_valid,dut.tlm_data,dut.reply_valid,dut.reply} !== {base.tlm_valid,base.tlm_data,base.reply_valid,base.reply})
    $fatal(1,"SESSION_REPLICA_PACKET_MISMATCH clock=%0d",clocks);
   if(pwmb)pwm_high=pwm_high+1;
   if(dut.tlm_valid)telemetry_checks=telemetry_checks+1;
   if(dut.reply_valid)reply_checks=reply_checks+1;
   if(dut.sample_en)begin
    sample_checks=sample_checks+1;
    if({dut.angle,dut.encoder_sample,dut.raw_guard_mean,dut.frames,dut.commit_bank,dut.prime_pending} !== {base.angle,base.encoder_sample,base.raw_guard_mean,base.frames,base.commit_bank,base.prime_pending})
     $fatal(1,"SESSION_REPLICA_SAMPLE_MISMATCH frame=%0d",dut.frames);
   end
   if(dut.done)begin
    done_checks=done_checks+1;
    if({dut.bias_bits,dut.position_bias,dut.position_diff,dut.angle_difference,dut.angle_p,dut.angle_i,dut.angle_d,dut.position_p,dut.position_i,dut.position_d,
        dut.captured_gains,dut.captured_epoch,dut.captured_revision,dut.captured_position_update} !==
       {base.bias_bits,base.position_bias,base.position_diff,base.angle_difference,base.angle_p,base.angle_i,base.angle_d,base.position_p,base.position_i,base.position_d,
        base.captured_gains,base.captured_epoch,base.captured_revision,base.captured_position_update})$fatal(1,"SESSION_REPLICA_DONE_MISMATCH");
   end
  end
 end
 initial begin
  log_file=$fopen("calibration_commands.csv","w");$fdisplay(log_file,"clock,id,opcode,group,status,mode,readback");
  repeat(20)@(negedge clk);rst_n=1;milliseconds(12);
  read_config(254,5,0);
  kit_write(2,3);check(kit_pulses==0,"invalid stopped kit value is rejected");
  kit_write(1,0);check(kit_pulses==1 && dut.kit_ip570,"valid stopped kit change produces one same-edge pulse");
  kit_write(1,0);check(kit_pulses==1,"same-value write creates no pulse");
  // Physical ZERO guarantees the rejected packet arrives inside4ms capture.
  press(0);check(dut.capture_active,"natural physical ZERO is capturing");kit_write(0,1);
  wait(dut.zero_valid && !dut.capture_active);wait(dut.source_ready);
  command(8'h12,0,0,0,0);wait(dut.mode==3);
  for(i=0;i<18;i=i+1)begin
   @(negedge dut.done);@(negedge clk);adc=510+(i%5);edge_step(i%8<4);
  end
  check(pwm_high>0,"real PID/PWM response is nonzero in equivalence window");
  kit_write(0,1);kit_write(2,1);check(dut.kit_ip570 && kit_pulses==1,"running kit mutations reject without inhibition pulse");
  command(8'h20,255,24,{64'd0,DEFAULT_GAINS[191:32],32'h424c0000},0);milliseconds(30);
  check(dut.stored_revision==1,"manual angle P changes through real UART");
  command(8'h22,0,0,0,0);command(8'h40,0,0,0,0);milliseconds(40);
  command(8'h44,0,0,0,0);command(8'h23,0,0,0,0);
  command(8'h10,0,0,0,0);kit_write(0,0);check(!dut.zero_valid && kit_pulses==2,"stopped kit change invalidates calibration identically");
  command(8'h12,0,0,0,1);fresh_zero();command(8'h12,0,0,0,0);milliseconds(25);
  adc=513;milliseconds(12);check(dut.actual_q15!=0 || bn1 || bn2,"second real actuator response before emergency stop");
  @(negedge clk);keys[3]=0;#1;check({bn1,bn2,pwmb,ref_bn1,ref_bn2,ref_pwmb}==0,"raw ESTOP immediately inhibits both systems");
  repeat(10)@(negedge clk);kit_write(1,1);command(8'h12,0,0,0,1);
  check(dut.fault==3 && base.fault==3,"ESTOP state equivalence");keys[3]=1;command(8'h13,0,0,0,0);
  external_reset();read_config(254,5,0);check(dut.kit_changed==0 && dut.kit_changed_motor==0,"reset clears both register copies");
  fresh_zero();command(8'h12,0,0,0,0);adc=514;milliseconds(12);external_reset();
  check(dut.mode==0 && !dut.run_session,"reset during running actuator is identical");
  kit_write(1,0);kit_write(0,0);check(kit_pulses==4,"post-reset valid writes preserve exact pulse count");
  check(valid_writes==5 && rejected_writes==5 && done_checks>=25 && sample_checks>=25 && pwm_high>0 && telemetry_checks>30 && reply_checks>=15,"meaningful calibration, transaction and protection coverage");
  fresh_zero();command(8'h12,0,0,0,0);adc=514;milliseconds(12);
  press(1);check(!dut.run_session && !base.run_session,"physical RUN toggles an active session to STOP");
  wait(dut.source_ready && base.source_ready);press(1);wait(dut.mode==3 && base.mode==3);milliseconds(12);
  press(1);check(!dut.run_session && !base.run_session,"a released and re-pressed RUN is a new STOP action");milliseconds(3);
  $display("CALIBRATION_STREAM_COUNTERS invalid=%0d rx=%0d telemetry_drop=%0d reply_drop=%0d",dut.invalid,dut.rx_errors,dut.tlm_dropped,dut.reply_dropped);
  check(dut.invalid==0 && dut.rx_errors==0 && dut.tlm_dropped==0 && dut.reply_dropped==0,"natural UART/AB path remains clean");
  $fclose(log_file);
  $display("PASS SESSION_REPLICA_SYSTEM_MITER clocks=%0d cycle_checks=%0d checks=%0d samples=%0d done=%0d telemetry=%0d replies=%0d kit_pulses=%0d valid_writes=%0d rejected_writes=%0d pwm_high=%0d no_internal_assignment=1",clocks,cycle_checks,checks,sample_checks,done_checks,telemetry_checks,reply_checks,kit_pulses,valid_writes,rejected_writes,pwm_high);$finish;
 end
 initial begin #(64'd700000000);$fatal(1,"Calibration miter timeout mode=%0d fault=%h",dut.mode,dut.fault);end

endmodule
