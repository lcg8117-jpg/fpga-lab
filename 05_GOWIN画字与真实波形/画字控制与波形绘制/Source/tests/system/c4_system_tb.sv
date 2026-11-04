`timescale 1ns/1ps
module c4_system_tb;
 parameter integer VARIANT=5,TRACKING_UART=0;
 localparam SWING_CAPABLE=VARIANT==1 || VARIANT==2 || VARIANT==4;
 reg clk=0;always #10 clk=~clk;
 reg rst_n=0;reg [3:0] keys=15;reg serial_rx=1;
 reg [9:0] adc_data=512;reg adc_otr=0,enc_a=0,enc_b=0;
 wire serial_tx,adc_clk,adc_oe,an1,an2,pwma,bn1,bn2,pwmb;
 tri1 scl,sda;
 forest_d1_system #(.VARIANT(VARIANT),.STARTUP_CYCLES(100),.KEY_STABLE_CYCLES(4),.ZERO_SAMPLES(1),
  .FIRMWARE_ID((VARIANT==5 ? 32'h56354336:32'h46443430+VARIANT))) dut(
  .bt_rx(1'b1),.bt_state(1'b1),.bt_tx(),.clk_50m(clk),.rst_n(rst_n),.key_sw(keys),.uart_rx(serial_rx),.uart_tx(serial_tx),
  .adc_clk(adc_clk),.adc_oe_n(adc_oe),.adc_data_in(adc_data),.adc_otr(adc_otr),.enc1_a(enc_a),.enc1_b(enc_b),
  .AN1(an1),.AN2(an2),.PWMA(pwma),.BN1(bn1),.BN2(bn2),.PWMB(pwmb),.gesture_scl(scl),.gesture_sda(sda));
 localparam [191:0] DEFAULT={32'h44188000,32'd0,32'h41c80000,32'h437b0000,32'd0,32'h42440000};
 localparam [191:0] ALTERNATE={32'h4418c000,32'd0,32'h41d00000,32'h437c0000,32'd0,32'h42480000};
 function integer drawn_point(input integer i);
  case(i)4,5:drawn_point=4;default:drawn_point=0;endcase
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
 integer parked_fault=0;integer upload_balance_checks=0,upload_index=0;reg [31:0] draw_checksum,draw_word;
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
     if(latest_ack[63:56]!==VARIANT || latest_ack[607:576]!==(VARIANT==5 ? 32'h56354336:32'h46443430+VARIANT))$fatal(1,"wire ACK variant/FW");checks++;
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

 task send_program;input integer count;
 begin
  request(8'h33,252,5,(TRACKING_UART<<8)|7,0);
  request(8'h33,252,5,count<<8,0);draw_checksum=32'h6a09e667^count^TRACKING_UART;
  for(upload_index=0;upload_index<count;upload_index++)begin
   draw_word=(upload_index<<16)|(drawn_point(upload_index)&16'hffff);
   request(8'h33,252,5,({224'd0,draw_word}<<8)|256'd1,0);
   draw_checksum={draw_checksum[30:0],draw_checksum[31]}^draw_word;
   if(dut.run_session && (dut.mode!=3 || !dut.actual_balance_permission || dut.wide_target!==old_wide_target))
    $fatal(1,"data upload changed live balance permission/reference");
   if(dut.draw_committed)$fatal(1,"partial data playable");checks++;
  end
  request(8'h33,252,5,({224'd0,draw_checksum}<<8)|256'd2,0);
  request(8'h32,252,1,3,0);if(latest_ack[415:384]!==1)$fatal(1,"commit readback");checks++;
 end endtask
 task encoder_step;begin
  @(negedge clk);
  case({enc_a,enc_b})2'b00:{enc_a,enc_b}=2'b01;2'b01:{enc_a,enc_b}=2'b11;
    2'b11:{enc_a,enc_b}=2'b10;default:{enc_a,enc_b}=2'b00;endcase
  repeat(1000)cycle();
 end endtask
 integer progress_file;
 always begin
  #10000000;
  progress_file=$fopen("system_progress.txt","w");
  $fdisplay(progress_file,"time=%0t mode=%0d fault=%h ready=%0b zero=%0b capture=%0b status=%0d samples=%0d acks=%0d motion=%0d committed=%0b",$time,dut.mode,dut.fault,dut.source_ready,dut.zero_valid,dut.capture_active,status_count,samples,ack_count,dut.motion_state,dut.draw_committed);
  $fclose(progress_file);
 end
 initial begin
  repeat(4)cycle();rst_n=1;wait(dut.control_rst_n);repeat(2000)cycle();
  request(8'h02,0,0,0,0);
  wait(dut.filtered==14'd8192 && dut.raw==10'd512);
  request(8'h11,0,0,0,0);wait(dut.zero_valid);wait(dut.source_ready);
  adc_data=200;wait(dut.fault!=0);parked_fault=dut.fault;repeat(20)cycle();
  if(dut.run_session || bn1 || bn2 || pwmb)$fatal(1,"downward fault enabled motor");
  // Real UART staging while the angle-protection fault remains latched.
  send_program(10);
  if(dut.fault!=parked_fault || dut.run_session || bn1 || bn2 || pwmb || !dut.draw_committed)
   $fatal(1,"data upload cleared fault or enabled motor");checks++;
  if(TRACKING_UART)begin
   request(8'h32,252,1,7,0);if(latest_ack[415:384]!==1)$fatal(1,"UART tracking mode readback");checks++;
   request(8'h33,252,5,({224'd0,(draw_checksum^32'd1024)}<<8)|256'd2,3);
   if(dut.draw_committed)$fatal(1,"mode0 checksum committed mode1 via UART");checks++;
   send_program(10);request(8'h10,0,0,0,0);
   if(!dut.draw_committed || dut.run_session || bn1 || bn2 || pwmb)$fatal(1,"tracked data STOP ownership");checks++;
   request(8'h32,252,1,7,0);if(latest_ack[415:384]!==1)$fatal(1,"STOP erased committed tracking flag");checks++;
   $display("PASS C6_TRACKING_UART checks=%0d ACK=%0d modeBoundCRC=1 poseFaultUpload=1 readback=1 STOPpreserves=1 noAutoMotion=1",checks,ack_count);$finish;
  end
  request(8'h10,0,0,0,0);
  if(!dut.draw_committed)$fatal(1,"STOP erased inert committed data");checks++;
  adc_data=512;wait(dut.filtered==14'd8192);request(8'h11,0,0,0,0);wait(dut.zero_valid);wait(dut.source_ready);repeat(20)cycle();
  if(!dut.draw_committed || dut.fault || dut.run_session)$fatal(1,"ZERO erased data or started motor");checks++;
  request(8'h12,0,0,0,0);
  // Create a real decoded encoder offset beyond the former64-count gate.
  repeat(96)encoder_step();repeat(20)cycle();
  old_wide_target=dut.wide_target;old_completions=completions;
  if(dut.wide_encoder<=64 && dut.wide_encoder>=-64)$fatal(1,"offset fixture not outside old gate");
  send_program(10);
  if(!dut.run_session || dut.mode!=3 || dut.fault || completions<=old_completions)
   $fatal(1,"live staging interrupted200Hz controller");checks++;
  request(8'h10,0,0,0,0);if(!dut.draw_committed)$fatal(1,"second STOP erased data");checks++;
  request(8'h12,0,0,0,0);old_wide_target=dut.wide_encoder;
  request(8'h46,0,1,7,0);
  if(dut.motion_origin!==old_wide_target)$fatal(1,"accepted current-origin mismatch");checks++;
  // Held-reference origin is aligned on RUN. No physical-speed/500ms wait.
  repeat(300000)cycle();
  if(dut.motion_state!=6 || dut.fault || !dut.draw_committed)
   $fatal(1,"current-origin play failed state=%0d fault=%h",dut.motion_state,dut.fault);checks++;
  request(8'h33,252,5,256'd10<<8,1);
  if(!dut.draw_committed)$fatal(1,"playing RAM overwritten");checks++;
  // Let the short uploaded reference really finish, keeping only the
  // ordinary UART heartbeat alive. No state override or accelerated clock.
  while(dut.motion_state!=4)begin
   request(8'h01,0,0,0,0);repeat(500000)cycle();
  end
  repeat(10)encoder_step();repeat(20)cycle();
  if(dut.tracking_error<=5 && dut.tracking_error>=-5)$fatal(1,"field offset fixture too small");
  if(!dut.trajectory_done || dut.tracking_settled || !dut.draw_upload_permitted)
   $fatal(1,"finished reference still requires physical500ms rest before staging");checks++;
  old_wide_target=dut.wide_target;old_completions=completions;send_program(10);
  if(!dut.run_session || dut.mode!=3 || dut.fault || completions<=old_completions)
   $fatal(1,"rest-offset staging interrupted PID");checks++;
  request(8'h46,0,1,3,0);
  if(dut.motion_state!=5 || dut.wide_target!==old_wide_target)
   $fatal(1,"fixed profile after ended reference rejected or jumped");checks++;
  request(8'h10,0,0,0,0);
  if(dut.motion_active || dut.run_session || bn1 || bn2 || pwmb || !dut.draw_committed)
   $fatal(1,"STOP failed motion inhibit or erased data");checks++;
  adc_data=200;wait(dut.fault!=0);
  if(!dut.draw_committed)$fatal(1,"angle fault erased inert program");checks++;
  if(dut.stored_gains!==DEFAULT)$fatal(1,"PID changed");checks++;
  $display("PASS C5_SYSTEM checks=%0d ACK=%0d samples=%0d completions=%0d pinChecks=%0d poseFaultUpload=1 liveMovingUpload=1 stopZeroPreserve=1 currentOrigin=1 noAutoMotion=1",checks,ack_count,samples,completions,pin_checks);
  $finish;
 end
 initial begin #(64'd1500000000);$fatal(1,"W1 system timeout mode=%0d fault=%h",dut.mode,dut.fault);end
endmodule

