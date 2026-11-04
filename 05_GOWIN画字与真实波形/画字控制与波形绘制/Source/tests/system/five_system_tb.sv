`timescale 1ns/1ps
module five_system_tb;
 parameter integer VARIANT=2;
 localparam SWING_CAPABLE=VARIANT==1 || VARIANT==2 || VARIANT==4;
 reg clk=0;always #10 clk=~clk;
 reg rst_n=0;reg [3:0] keys=15;reg serial_rx=1;
 reg [9:0] adc_data=512;reg adc_otr=0,enc_a=0,enc_b=0;
 wire serial_tx,adc_clk,adc_oe,an1,an2,pwma,bn1,bn2,pwmb;
 tri1 scl,sda;
 forest_d1_system #(.VARIANT(VARIANT),.STARTUP_CYCLES(100),.KEY_STABLE_CYCLES(4),.ZERO_SAMPLES(8),
  .FIRMWARE_ID((VARIANT==5 ? 32'h56354431:32'h46443430+VARIANT))) dut(
  .bt_rx(1'b1),.bt_state(1'b1),.bt_tx(),.clk_50m(clk),.rst_n(rst_n),.key_sw(keys),.uart_rx(serial_rx),.uart_tx(serial_tx),
  .adc_clk(adc_clk),.adc_oe_n(adc_oe),.adc_data_in(adc_data),.adc_otr(adc_otr),.enc1_a(enc_a),.enc1_b(enc_b),
  .AN1(an1),.AN2(an2),.PWMA(pwma),.BN1(bn1),.BN2(bn2),.PWMB(pwmb),.gesture_scl(scl),.gesture_sda(sda));
 localparam [191:0] DEFAULT={32'h44188000,32'd0,32'h41c80000,32'h437b0000,32'd0,32'h42440000};
 localparam [191:0] ALTERNATE={32'h4418c000,32'd0,32'h41d00000,32'h437c0000,32'd0,32'h42480000};
 function integer drawn_point(input integer i);
  if(i<=1)drawn_point=0;else if(i<=41)drawn_point=(i-1)*2;
  else if(i==42)drawn_point=80;else if(i<=82)drawn_point=80-(i-42)*2;else drawn_point=0;
 endfunction
 function [15:0] crc_byte;
  input [15:0] c;input [7:0] byte_value;reg [15:0] v;integer k;
  begin v=c^{byte_value,8'd0};for(k=0;k<8;k++)v=v[15] ? (v<<1)^16'h1021 : v<<1;crc_byte=v;end
 endfunction
 integer diag_count=0;integer checks=0,ack_count=0,tlm_count=0,coherent_count=0,status_count=0,expected_write=0,expected_read=0;
 integer samples=0,completions=0,pwm_edges=0,adc_edges=0,max_latency=0,clock_count=0;
 reg [31:0] logical_session=91;
 integer frame_bytes=0,frame_length=0,j,k,i,next_id=1,last_ack_id=0;
 integer sample_clock=0,previous_sample=-1,previous_ms=-1,watchdog_events=0;
 integer source_starts=0,holds=0,handoffs=0,primes=0,gesture_seams=0,finished_seams=0;
 integer removed_swing_rejections=0;
 integer upload_balance_checks=0,upload_index=0;reg [31:0] draw_checksum,draw_word;
 reg removed_swing_window=0;
 integer pin_checks=0,pin_swing_cycles=0,pin_balance_cycles=0,pin_stop_cycles=0,pin_fault_cycles=0,pin_handoffs=0;
 generate if(VARIANT>=4)begin:emergency_timing_proof
  wire old_usb=dut.usb_command_valid && dut.router.emergency_shape(dut.usb_command);
  wire old_ble=dut.ble_command_valid && dut.router.emergency_shape(dut.ble_command);
  always @(posedge clk)if(dut.control_rst_n)begin
   #2;
   if(dut.urgent_stop!==(old_usb || old_ble) ||
     dut.urgent_estop!==((old_usb && dut.usb_command[47:40]==8'h14) || (old_ble && dut.ble_command[47:40]==8'h14)))
    $fatal(1,"predecoded emergency changed its direct inhibit cycle");
  end
 end endgenerate
 integer gain_select_checks=0,gain_active_cycles=0,gain_manual_cycles=0,gain_disable_edges=0;
 reg previous_adapt_enabled=0;
 wire [191:0] old_desired_gains=(VARIANT==3 && dut.adaptation_active &&
  dut.candidate_revision==dut.stored_revision && !dut.manual_write) ? dut.adaptive_target:dut.stored_gains;
 wire [2:0] old_priority_pins=dut.swing_permission ? {dut.swing_bn1,dut.swing_bn2,dut.swing_pwm}:
  dut.actual_balance_permission ? {dut.balance_bn1,dut.balance_bn2,dut.balance_pwm_pin}:3'b0;
 wire [2:0] independent_or_pins={dut.swing_bn1|dut.balance_bn1,
  dut.swing_bn2|dut.balance_bn2,dut.swing_pwm|dut.balance_pwm_pin};
 reg [2047:0] expected_tlm[0:1023];reg [7:0] frame[0:255],serial_byte;
 reg [639:0] latest_ack;reg [15:0] crc;
 reg [383:0] request_packet;reg [255:0] payload;
 reg [191:0] sampled_base,sampled_gains;reg [31:0] sampled_stored_revision;
 reg [31:0] sampled_epoch;reg [15:0] sampled_mean;
 reg [63:0] old_wide_target;integer old_samples,old_completions,old_source_starts,old_holds,old_primes;
 time previous_adc_edge=0;
 task cycle;begin @(posedge clk);#1;end endtask
 task send_byte;input [7:0] value;integer b;
 begin @(negedge clk);serial_rx=0;repeat(25)@(negedge clk);
  for(b=0;b<8;b++)begin serial_rx=value[b];repeat(25)@(negedge clk);end
  serial_rx=1;repeat(25)@(negedge clk);
 end endtask
 task build_request;input [7:0] op,group,len;input [255:0] data;
 integer b;reg [15:0] c;
 begin
  request_packet=0;request_packet[31:0]=32'h43324446;request_packet[39:32]=VARIANT==2 ? 3:VARIANT;
  request_packet[47:40]=op;request_packet[55:48]=group;request_packet[63:56]=len;
  request_packet[95:64]=next_id;next_id++;request_packet[351:96]=data;request_packet[319:288]=logical_session;request_packet[351:320]=request_packet[95:64];
  c=16'hffff;for(b=0;b<46;b++)c=crc_byte(c,request_packet[b*8+:8]);request_packet[383:368]=c;
 end endtask
 task await_ack;input integer id;input [7:0] status;
 integer limit;
 begin
  limit=0;while(last_ack_id!=id && limit<400000)begin cycle();limit++;end
  if(last_ack_id!=id || (status==8'hff ?
      (latest_ack[55:48]!==8'd0 && latest_ack[55:48]!==8'd1):latest_ack[55:48]!==status))
   $fatal(1,"UART ACK mismatch id=%0d last=%0d status=%0d expected=%0d mode=%0d fault=%h op=%h group=%0d payload=%h ready=%b motion=%0d ref=%0d",id,last_ack_id,latest_ack[55:48],status,dut.mode,dut.fault,request_packet[47:40],request_packet[55:48],request_packet[135:96],dut.draw_upload_permitted,dut.motion_state,dut.wide_target);
  checks++;
 end endtask
 task request;input [7:0] op,group,len;input [255:0] data;input [7:0] status;
 integer b,id;
 begin build_request(op,group,len,data);id=request_packet[95:64];
  for(b=0;b<48;b++)send_byte(request_packet[b*8+:8]);await_ack(id,status);
 end endtask
 task removed_swing_request;input [7:0] op,group,len;input [255:0] data;
 begin
  request(op,group,len,data,8'd6);repeat(4)cycle();
  if(dut.run_session || dut.swing_start_pending || dut.swing_active || dut.source_start ||
     dut.swing_permission || dut.actual_balance_permission || bn1 || bn2 || pwmb)
   $fatal(1,"removed swing command changed stopped ownership/output op=%h group=%0d",op,group);
  removed_swing_rejections++;checks++;
 end endtask
 function [31:0] relative_position;input integer index;
 begin
  case(index)
   1:relative_position=173;2:relative_position=347;3:relative_position=520;
   4:relative_position=693;5:relative_position=867;6:relative_position=1040;
   default:relative_position=0;
  endcase
 end endfunction
 task uart_stop;integer settle;begin request(8'h10,0,0,0,0);repeat(4)cycle();
  if(dut.run_session || bn1 || bn2 || pwmb)$fatal(1,"UART STOP did not inhibit hardware path");checks++;
  settle=0;while((dut.swing_active || dut.swing_start_pending) && settle<250000)begin cycle();settle++;end
  if(dut.mode!==(dut.fault!=0 ? 3'd4:dut.zero_valid ? 3'd1:3'd0) || dut.swing_active || dut.swing_start_pending)
   $fatal(1,"UART STOP did not retire swing state mode=%0d fault=%h",dut.mode,dut.fault);checks++;
 end endtask
 task gesture;input [3:0] code;
 begin
  if(VARIANT>=4)begin
   // The keyboard-only build starts and stops through the real UART path.
   case(code)
    // Busy admission can return idempotent success or STATE depending on
    // the session edge. The caller separately checks no new start pulse.
    3:request(8'h31,0,0,0,(dut.swing_active || dut.swing_start_pending) ? 8'hff:8'd0);
    9:uart_stop();
    default:$fatal(1,"keyboard builds have no optical gesture seam");
   endcase
  end else begin
  // Explicit integration seam: reader transport/optical decoding tested separately.
  @(negedge clk);force dut.gesture_ready=1;force dut.gesture_event=1;
  case(code)
   1:force dut.gesture_code=1;2:force dut.gesture_code=2;3:force dut.gesture_code=3;
   7:force dut.gesture_code=7;8:force dut.gesture_code=8;9:force dut.gesture_code=9;
   default:$fatal(1,"unsupported seam");
  endcase
  gesture_seams++;cycle();@(negedge clk);release dut.gesture_event;release dut.gesture_code;
  repeat(4)cycle();release dut.gesture_ready;
  end
 end endtask
 // Independent UART wire decoder, full payload and CRC, no DUT TX access.
 initial forever begin
  @(negedge serial_tx);
  if(rst_n)begin
   #750;for(j=0;j<8;j++)begin serial_byte[j]=serial_tx;#500;end
   if(serial_tx!==1)$fatal(1,"wire TX stop bit");
   frame[frame_bytes]=serial_byte;frame_bytes++;
   if(frame_bytes==4)begin
    if(frame[0]!=8'h46 || frame[1]!=8'h44 || frame[2]!=8'h32)$fatal(1,"wire magic");
    if(frame[3]==8'h41)frame_length=80;else if(frame[3]==8'h54 || frame[3]==8'h44)frame_length=256;else $fatal(1,"wire type");
   end
   if(frame_length!=0 && frame_bytes==frame_length)begin
    crc=16'hffff;for(k=0;k<frame_length-2;k++)crc=crc_byte(crc,frame[k]);
    if({frame[frame_length-1],frame[frame_length-2]}!==crc)$fatal(1,"wire CRC");checks++;
    if(frame_length==80)begin
     for(k=0;k<80;k++)latest_ack[k*8+:8]=frame[k];
     last_ack_id=latest_ack[95:64];ack_count++;
     if(latest_ack[63:56]!==VARIANT || latest_ack[607:576]!==(VARIANT==5 ? 32'h56354431:32'h46443430+VARIANT))$fatal(1,"wire ACK variant/FW");checks++;
    end else begin
     if(expected_read>=expected_write)$fatal(1,"unowned/duplicate telemetry");
     for(k=0;k<254;k++)if(frame[k]!==expected_tlm[expected_read][k*8+:8])$fatal(1,"wire telemetry snapshot mismatch seq=%0d byte=%0d",expected_read,k);
     if(frame[4]!=(VARIANT==2 ? 3:VARIANT) || frame[5]!=VARIANT)$fatal(1,"wire telemetry identity");
     expected_read++;tlm_count++;
     if(frame[3]==8'h44)diag_count++;else if(frame[6]&1)coherent_count++;else status_count++;
    end
    frame_bytes=0;frame_length=0;
   end
  end
 end
 always @(posedge adc_clk)if(rst_n)begin
  if(previous_adc_edge!=0 && $time-previous_adc_edge!=320)$fatal(1,"ADC clock is not3.125MHz");
  previous_adc_edge=$time;adc_edges++;
 end
 always @(posedge pwmb)if(rst_n)pwm_edges++;
 // Check the actual protected actuator outputs, not just mode encodings.
 // Sample after NBA/combinational settling, while counting one-clock requests
 // before the edge so STOP and handoff coverage cannot disappear in the NBA.
 always @(posedge clk)if(dut.control_rst_n)begin
  if(dut.stopping)pin_stop_cycles++;
  if(dut.handoff_pulse)pin_handoffs++;
  #2;
  if(dut.swing_permission && dut.actual_balance_permission)$fatal(1,"pin route permission overlap");
  if(VARIANT==5 && (dut.mode==5 || dut.swing_permission || dut.swing_active ||
     dut.swing_start_pending || dut.source_start || dut.remote_swing || dut.handoff_pulse))
   $fatal(1,"manual-only V5 exposed an automatic swing owner");
  if(removed_swing_window && (dut.remote_run || dut.run_session || dut.swing_permission ||
     dut.actual_balance_permission || bn1 || bn2 || pwmb))
   $fatal(1,"removed swing command produced a transient start/output");
  if(!dut.swing_permission && {dut.swing_bn1,dut.swing_bn2,dut.swing_pwm}!==3'b0)
   $fatal(1,"unpermitted swing pins are nonzero");
  if(!dut.actual_balance_permission && {dut.balance_bn1,dut.balance_bn2,dut.balance_pwm_pin}!==3'b0)
   $fatal(1,"unpermitted balance pins are nonzero");
  if(old_priority_pins!==independent_or_pins || old_priority_pins!=={bn1,bn2,pwmb})
   $fatal(1,"priority mux and protected OR differ");
  pin_checks++;
  if(dut.swing_permission)pin_swing_cycles++;
  if(dut.actual_balance_permission)pin_balance_cycles++;
  if(dut.fault!=0)pin_fault_cycles++;
  if(dut.desired_gains!==old_desired_gains)$fatal(1,"desired gains differ from preserved selection");
  gain_select_checks++;
  if(dut.adaptation_active)gain_active_cycles++;
  if(dut.manual_write)gain_manual_cycles++;
  if(previous_adapt_enabled && !dut.adaptation_enabled)gain_disable_edges++;
  previous_adapt_enabled=dut.adaptation_enabled;
 end
 always @(posedge clk)begin
  clock_count++;
  if(dut.control_rst_n)begin
   if(dut.source_start)source_starts++;
   if(dut.hold_pulse)holds++;
   if(dut.handoff_pulse)handoffs++;
   if(dut.prime_pending && dut.sample_en)primes++;
   if(dut.watchdog_stop)watchdog_events++;
   if(dut.tlm_valid)begin
    if(!dut.tlm_ready)$fatal(1,"200Hz producer overflow");
    expected_tlm[expected_write]=dut.tlm_data;expected_write++;
    if(dut.tlm_data[31:0]==32'h54324446 && dut.tlm_data[48])begin
     if(dut.tlm_data[223:192]!==sampled_stored_revision || dut.tlm_data[1215:1024]!==sampled_base)
      $fatal(1,"manual bank/revision not tied to transaction");
     if(dut.tlm_data[1407:1216]!==sampled_gains || dut.tlm_data[159:128]!==sampled_epoch || dut.tlm_data[447:432]!==sampled_mean)
      $fatal(1,"coherent input/gain/epoch mismatch");
     if(dut.tlm_data[927:912]!==5000)$fatal(1,"control period provenance");checks+=3;
    end
   end
   if(!dut.source_rst_n)begin previous_sample=-1;previous_ms=-1;end
   if(dut.sample_en)begin
    if(previous_sample>=0 && clock_count-previous_sample!=250000)$fatal(1,"quiet control sample cadence changed");
    if(previous_ms>=0 && dut.uptime_ms-previous_ms!=5)$fatal(1,"200Hz board timestamp cadence");
    previous_sample=clock_count;previous_ms=dut.uptime_ms;sample_clock=clock_count;
    sampled_base=dut.stored_gains;sampled_stored_revision=dut.stored_revision;
    sampled_gains=dut.core_gains;sampled_epoch=dut.frames;sampled_mean={4'd0,dut.angle};samples++;
    if(dut.busy)$fatal(1,"overlapping numerical transaction");checks++;
   end
   if(dut.done)begin
    completions++;if(clock_count-sample_clock>max_latency)max_latency=clock_count-sample_clock;
    if(clock_count-sample_clock>=100000)$fatal(1,"numerical transaction exceeded2ms budget");
    if(dut.angle_i!==0 || dut.position_i!==0)$fatal(1,"I=0 emitted integral component");checks++;
    if({dut.core.pkd,dut.core.pki,dut.core.pkp,dut.core.bkd,dut.core.bki,dut.core.bkp}!==sampled_gains)
     $fatal(1,"telemetry bank is not the six gains used by numerical core");checks++;
   end
   #2;
   if(an1 || an2 || pwma || (bn1 && bn2))$fatal(1,"physical owner conflict");
   if(!dut.swing_permission && !dut.actual_balance_permission && (bn1 || bn2 || pwmb))$fatal(1,"unpermitted physical output");
   if(dut.swing_permission && dut.actual_balance_permission)$fatal(1,"swing and balance both own PWM");
  end
 end
 initial begin
  repeat(4)cycle();rst_n=1;wait(dut.control_rst_n);repeat(2000)cycle();
  if(dut.stored_gains!==DEFAULT || dut.run_session || bn1 || bn2 || pwmb)$fatal(1,"system reset defaults/safe pins");checks++;
  request(8'h02,0,0,0,0);
  request(8'h21,255,0,0,0);
  if(latest_ack[383:192]!==DEFAULT)$fatal(1,"actualUART default gain read");checks++;
  // STOP-only kit selection is atomic, independently readable and never ZERO/RUN.
  request(8'h33,254,5,256'h105,0);
  if(!dut.kit_ip570 || dut.zero_valid || dut.capture_active || dut.run_session || bn1 || bn2 || pwmb)$fatal(1,"kit change unexpectedly calibrated or started");checks++;
  request(8'h32,254,1,256'd6,0);if(latest_ack[415:384]!==1040)$fatal(1,"IP570 resolution readback");checks++;
  request(8'h33,254,5,256'h5,0);
  request(8'h32,254,1,256'd6,0);if(latest_ack[415:384]!==1080)$fatal(1,"original kit resolution readback");checks++;
  // One actual100ms stopped sensor frame, not fake repeated control samples.
  wait(status_count>=1);if(samples!=0 || dut.source_rst_n)$fatal(1,"preZERO control sample fabricated");checks++;
  request(8'h11,0,0,0,0);wait(dut.zero_valid);wait(dut.source_ready);repeat(20)cycle();
  if(dut.zero_q4!==8192 || dut.fault || dut.acquisition_fault)$fatal(1,"realADC calibration/quiet sampling");checks++;
  if(VARIANT==5)begin
   // Exercise the removed interface over the actual wire while calibrated
   // and stopped; no pulse may transiently escape between reply checks.
   removed_swing_window=1;
   removed_swing_request(8'h30,0,1,0);
   removed_swing_request(8'h31,0,0,0);
   for(i=0;i<3;i++)begin
    removed_swing_request(8'h32,i,1,0);
    removed_swing_request(8'h33,i,5,256'd70<<8);
   end
   removed_swing_request(8'h32,254,1,0);
   removed_swing_request(8'h33,254,5,0);
   if(source_starts || holds || handoffs || finished_seams || dut.selected_profile!=0)
    $fatal(1,"removed swing interface changed a source/selection");checks++;
   removed_swing_window=0;
   request(8'h32,253,1,0,0);
   if(latest_ack[415:384]!==260)$fatal(1,"manual V5 motion configuration read missing");checks++;
   request(8'h33,253,5,256'd260<<8,0);
   request(8'h32,253,1,0,0);
   if(latest_ack[415:384]!==260 || dut.run_session || bn1 || bn2 || pwmb)
    $fatal(1,"manual V5 motion configuration write/read or stopped ownership changed");checks++;
  end
  request(8'h12,0,0,0,0);if(!dut.run_session || dut.mode!=3)$fatal(1,"realUART RUN");checks++;
  request(8'h33,254,5,256'h105,1);if(dut.kit_ip570)$fatal(1,"live kit change was accepted");checks++;
  adc_data=513;old_samples=samples;old_completions=completions;
  wait(samples>=old_samples+12);wait(completions>=old_completions+12);repeat(10000)cycle();
  if(!pwm_edges || dut.fault || dut.tlm_dropped || dut.reply_dropped || dut.rx_errors)$fatal(1,"real control/PWM/logging path failed");checks++;
  // SET completes during a position transaction: old captured bank/revision must
  // remain coherent, and the new bank adopts only at a later25ms boundary.
  wait(dut.position_div==4);wait(dut.sampler.cycle==130000);
  build_request(8'h20,255,24,{64'd0,ALTERNATE});
  for(i=0;i<47;i++)send_byte(request_packet[i*8+:8]);
  wait(dut.sample_en);send_byte(request_packet[383:376]);await_ack(request_packet[95:64],0);
  if(dut.stored_gains!==ALTERNATE)$fatal(1,"UART atomic SET not stored");checks++;
  wait(dut.active_revision==dut.stored_revision);request(8'h21,255,0,0,0);
  if(latest_ack[383:192]!==ALTERNATE)$fatal(1,"independent realUART READ after SET");checks++;
  if(VARIANT==3)begin
   // Exercise the actual UART -> gateway -> pipelined learner ->25ms adoption
   // path, including manual ownership revocation while balance remains live.
   request(8'h22,0,0,0,0);if(!dut.adaptation_enabled)$fatal(1,"actualUART adaptive enable");checks++;
   old_samples=samples;wait(samples>=old_samples+6);wait(dut.adaptation_active);
   if(dut.adaptive_epoch==0 || !dut.run_session || dut.fault)$fatal(1,"pipelined candidate never adopted at25ms");checks++;
   request(8'h20,255,24,{64'd0,ALTERNATE},0);
   if(dut.adaptation_enabled || dut.adaptation_active)$fatal(1,"manual SET failed adaptive ownership revocation");checks++;
   request(8'h22,0,0,0,0);old_samples=samples;wait(samples>=old_samples+2);
   request(8'h23,0,0,0,0);
   if(dut.adaptation_enabled || dut.adaptation_active || !dut.run_session)$fatal(1,"live adaptive disable changed balance");checks++;
  end
  uart_stop();
  if(SWING_CAPABLE)begin
   old_source_starts=source_starts;gesture(3);wait(dut.mode==5);wait(dut.swing_active);repeat(20)cycle();
   if(source_starts!=old_source_starts+1 || dut.swing_start_pending)$fatal(1,"Up/pending start handshake lost or repeated");checks++;
   gesture(3);if(source_starts!=old_source_starts+1)$fatal(1,"repeatedUp restarted source");checks++;
   gesture(9);if(dut.run_session || dut.swing_start_pending || dut.motion_active || bn1 || bn2 || pwmb)$fatal(1,"Wave did not cancel all owners");checks++;
   // Fresh Up, then a documented handoff seam. Real swing waveform/capture
   // qualification is separately tested; only its finished wire is injected.
   gesture(3);wait(dut.mode==5);wait(dut.swing_active);old_holds=holds;old_primes=primes;
   @(negedge clk);force dut.swing_finished=1;finished_seams++;cycle();
   @(negedge clk);release dut.swing_finished;
   wait(dut.mode==3);repeat(5)cycle();
   if(holds!=old_holds+1 || handoffs==0 || primes<=old_primes || !dut.source_ready || dut.acquisition_fault || dut.fault)
    $fatal(1,"newanchor/quietprime/handoff failed holds=%0d primes=%0d mode=%0d fault=%h",holds,primes,dut.mode,dut.fault);checks++;
   if(VARIANT==2)begin
    gesture(2);if(!dut.motion_active || dut.motion_state!=2 || dut.motion_direction!=1)$fatal(1,"left gesture did not route to continuous");checks++;
    repeat(600000)cycle();gesture(1);wait(dut.trajectory_done);
    old_wide_target=dut.wide_target;repeat(300000)cycle();
    if(dut.wide_target!==old_wide_target || dut.mode!=3)$fatal(1,"opposite gesture failed stop/hold");checks++;
    gesture(7);if(!dut.motion_active || dut.motion_state!=1)$fatal(1,"CW single gesture route");checks++;
    gesture(9);if(dut.run_session || dut.motion_active || dut.gesture_motion_valid || bn1 || bn2 || pwmb)$fatal(1,"Wave left a residual motion command");checks++;
   end else begin
    old_holds=holds;old_primes=primes;
    request(8'h45,0,1,256'd6,0);
    if(dut.motion_final-dut.motion_origin!=1040 || dut.motion_state!=1)$fatal(1,"relative360 target not full circle");checks++;
    request(8'h45,0,1,256'd1,0);
    if(dut.motion_final-dut.motion_origin!=173 || dut.motion_state!=1)$fatal(1,"latest position did not preempt");checks++;
    if(holds!=old_holds || primes!=old_primes || dut.mode!=3)$fatal(1,"movement reset the balance owner");checks++;
    uart_stop();
   end
  end
  if(VARIANT==5)begin
   // V5 reaches balance only through the normal real-UART manual RUN.
   request(8'h12,0,0,0,0);
   if(!dut.run_session || dut.mode!=3 || !dut.actual_balance_permission)
    $fatal(1,"manual-only V5 did not enter balance through RUN");checks++;
   request(8'h31,0,0,0,6);
   request(8'h30,0,1,0,6);
   if(!dut.run_session || dut.mode!=3 || source_starts || dut.swing_start_pending)
    $fatal(1,"removed swing command disrupted manual V5 balance");checks++;
   removed_swing_rejections+=2;
   old_holds=holds;old_primes=primes;
   repeat(3)begin repeat(2000000)cycle();request(8'h01,0,0,0,0);end
   // Quiet upright observations establish the100ms start-velocity qualification.
   for(i=1;i<=6;i++)request(8'h45,0,1,i,6);
   for(i=3;i<=4;i++)begin
    request(8'h46,0,1,i,0);
    if(dut.motion_state!=5 || dut.motion_final!=dut.motion_origin || dut.planner_action!=(8'h20+i))
     $fatal(1,"manual V5 finite trajectory dispatch index=%0d",i);checks++;
    request(8'h46,0,1,i,1);request(8'h45,0,1,256'd3,6);
    request(8'h47,0,0,0,0);
    repeat(15)begin repeat(2000000)cycle();request(8'h01,0,0,0,0);end
    if(!dut.run_session || dut.mode!=3 || !dut.actual_balance_permission)
     $fatal(1,"normal fixed cancellation dropped balance");checks++;
   end
   request(8'h46,0,1,256'd1,3);request(8'h46,0,1,256'd2,3);
   request(8'h46,0,1,256'd5,3);request(8'h46,0,1,256'd6,3);
   request(8'h46,0,1,256'd7,1);
   request(8'h33,252,5,256'd10<<8,0);
   request(8'h33,252,5,256'h101,3);
   if(dut.draw_committed || !dut.run_session || dut.mode!=3 || !dut.actual_balance_permission)
    $fatal(1,"invalid upload changed balance or became playable");checks++;
   old_completions=completions;old_wide_target=dut.wide_target;
   request(8'h33,252,5,256'd85<<8,0);draw_checksum=32'h6a09e667^32'd85;
   for(upload_index=0;upload_index<85;upload_index++)begin
    draw_word=(upload_index<<16)|(drawn_point(upload_index)&16'hffff);
    request(8'h33,252,5,({224'd0,draw_word}<<8)|256'd1,0);
    draw_checksum={draw_checksum[30:0],draw_checksum[31]}^draw_word;
    if(!dut.run_session || dut.mode!=3 || !dut.actual_balance_permission ||
       dut.wide_target!==old_wide_target || dut.motion_active || dut.draw_committed)
     $fatal(1,"staging upload interrupted PID/held reference or exposed partial RAM");upload_balance_checks++;
   end
   request(8'h33,252,5,({224'd0,draw_checksum}<<8)|256'd2,0);
   request(8'h32,252,1,256'd1,0);if(latest_ack[415:384]!==85)$fatal(1,"uploaded count readback");checks++;
   request(8'h32,252,1,256'd2,0);if(latest_ack[415:384]!==draw_checksum)$fatal(1,"uploaded checksum readback");checks++;
   request(8'h32,252,1,256'd3,0);if(latest_ack[415:384]!==1)$fatal(1,"uploaded committed flag readback");checks++;
   if(completions<=old_completions || upload_balance_checks!=85 || !dut.run_session || dut.mode!=3)
    $fatal(1,"PID control epochs did not continue throughout upload");checks++;
   request(8'h46,0,1,256'd7,0);
   request(8'h33,252,5,256'd10<<8,1);
   if(!dut.draw_committed)$fatal(1,"moving write modified committed RAM");checks++;
   repeat(20)begin repeat(2000000)cycle();request(8'h01,0,0,0,0);end
   if(dut.motion_state!=6 || !dut.run_session || dut.mode!=3 || !dut.actual_balance_permission)
    $fatal(1,"custom playback lost the balance owner");checks++;
   request(8'h47,0,0,0,0);
   repeat(5)begin repeat(2000000)cycle();request(8'h01,0,0,0,0);end
   if(!dut.run_session || dut.mode!=3 || !dut.actual_balance_permission)
    $fatal(1,"normal custom cancellation dropped balance");checks++;
   if(holds!=old_holds || primes!=old_primes || dut.mode!=3 || !dut.run_session)
    $fatal(1,"manual V5 motion reset the balance owner");checks++;
   uart_stop();
  end
  // Remote-owned physical chain uses the actual500ms watchdog default.
  adc_data=512;request(8'h12,0,0,0,0);old_samples=samples;old_completions=completions;
  wait(watchdog_events>=1);repeat(20)cycle();
  if(dut.run_session || bn1 || bn2 || pwmb || samples<old_samples+98 || completions<old_completions+98)
   $fatal(1,"actual500ms watchdog/200Hz path failed");checks++;
  logical_session=logical_session+1;request(8'h02,0,0,0,0);
  // Real asynchronous physical ESTOP while balance is permitted must suppress
  // both protected pin sets immediately, then latch the normal safety fault.
  request(8'h12,0,0,0,0);
  if(!dut.actual_balance_permission)$fatal(1,"fault-route coverage did not enter balance");checks++;
  @(negedge clk);keys[3]=0;#1;
  if(dut.swing_permission || dut.actual_balance_permission || bn1 || bn2 || pwmb)
   $fatal(1,"physical ESTOP did not immediately inhibit pin routes");checks++;
  repeat(6)cycle();
  if(dut.fault!==8'h03 || dut.run_session)$fatal(1,"physical ESTOP fault did not latch");checks++;
  @(negedge clk);keys[3]=1;repeat(12)cycle();
  // Drain the last snapshot; no dropped or fabricated coherent records.
  wait(!dut.link_busy);repeat(1000)cycle();
  if(expected_read!=expected_write || dut.tlm_dropped || dut.reply_dropped || dut.rx_errors || dut.gateway_errors)$fatal(1,"integrated log transport loss");checks++;
  if(!pin_checks || !pin_balance_cycles || !pin_stop_cycles || !pin_fault_cycles ||
     (SWING_CAPABLE && (!pin_swing_cycles || !pin_handoffs)))$fatal(1,"pin route lifecycle coverage missing");checks++;
  if(VARIANT==5)begin
   if(pin_swing_cycles || finished_seams || source_starts || pin_handoffs ||
      removed_swing_rejections!=12)$fatal(1,"manual V5 removal/lifecycle coverage missing");checks++;
  end
  if(gain_select_checks!=pin_checks || (VARIANT==3 && (!gain_active_cycles || !gain_manual_cycles || gain_disable_edges<2)))
   $fatal(1,"gain selection lifecycle coverage missing");checks++;
  $display("PASS system variant=%0d checks=%0d ACK=%0d telemetry=%0d coherent=%0d status=%0d samples=%0d completions=%0d maxCoreClocks=%0d pwmEdges=%0d adcEdges=%0d watchdog500ms=%0d gestureSeams=%0d finishedSeams=%0d holds=%0d primes=%0d pinChecks=%0d pinSwing=%0d pinBalance=%0d pinStops=%0d pinFaults=%0d pinHandoffs=%0d gainSelectChecks=%0d gainActive=%0d gainManual=%0d gainDisableEdges=%0d removedSwingRejected=%0d uploadBalanceChecks=%0d",VARIANT,checks,ack_count,tlm_count,coherent_count,status_count,samples,completions,max_latency,pwm_edges,adc_edges,watchdog_events,gesture_seams,finished_seams,holds,primes,pin_checks,pin_swing_cycles,pin_balance_cycles,pin_stop_cycles,pin_fault_cycles,pin_handoffs,gain_select_checks,gain_active_cycles,gain_manual_cycles,gain_disable_edges,removed_swing_rejections,upload_balance_checks);
  $finish;
 end
 initial begin #(64'd6000000000);$fatal(1,"system timeout variant=%0d mode=%0d fault=%h samples=%0d",VARIANT,dut.mode,dut.fault,samples);end
endmodule
