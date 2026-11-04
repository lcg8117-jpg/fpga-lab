// Simulation-only observations; the synthesis project never includes this file.
module system_probe(
 input clk,rst_n,rx,input [3:0] keys,input [9:0] adc,input ea,eb,
 output tx,output [2:0] pins,mode,output [7:0] fault,
 output ready,zero_valid,permit,fast,done,obs,sample,batch,
 output [31:0] ms,angle_epoch,pos_d_epoch,pos_p_epoch,integrals,transport_errors,
 output [191:0] gains,applied,base,output [31:0] revision,
 output [11:0] angle,output [2047:0] packet,output packet_valid,
 output [31:0] play_time,job_id,output [2:0] play_state,output [15:0] live_offset,
 output [63:0] origin,actual,refpos,output adc_clock,output [31:0] bad
);
 wire an1,an2,pwma;tri1 scl,sda;
 forest_d1_system #(.STARTUP_CYCLES(100),.KEY_STABLE_CYCLES(4),.ZERO_SAMPLES(8)) dut(
  .clk_50m(clk),.rst_n(rst_n),.key_sw(keys),.uart_rx(rx),.uart_tx(tx),
  .bt_rx(1'b1),.bt_state(1'b0),.bt_tx(),.adc_data_in(adc),.adc_otr(1'b0),
  .adc_clk(adc_clock),.adc_oe_n(),.enc1_a(ea),.enc1_b(eb),
  .AN1(an1),.AN2(an2),.PWMA(pwma),.BN1(pins[2]),.BN2(pins[1]),.PWMB(pins[0]),.gesture_scl(scl),.gesture_sda(sda));
 assign mode=dut.mode;assign fault=dut.fault;assign ready=dut.source_ready;
 assign zero_valid=dut.zero_valid;assign permit=dut.actual_balance_permission;
 assign fast=dut.control_fast_p;assign done=dut.done;assign obs=dut.observation_start;
 assign sample=dut.sample_en;assign batch=dut.adc_batch_valid;assign ms=dut.uptime_ms;
 assign angle_epoch=dut.control_angle_d_epoch;assign pos_d_epoch=dut.control_position_d_epoch;
 assign pos_p_epoch=dut.control_position_p_epoch;assign integrals=dut.angle_i|dut.position_i;
 assign transport_errors=dut.tlm_dropped|dut.reply_dropped|dut.rx_errors|dut.gateway_errors;
 assign gains=dut.observation_gains;assign applied=dut.core.fast.applied_gains;
 assign base=dut.stored_gains;assign revision=dut.observation_revision;assign angle=dut.observation_angle;
 assign packet=dut.tlm_data;assign packet_valid=dut.tlm_valid;
 assign play_time=dut.play_time_ms;assign job_id=dut.planner_request;
 assign play_state=dut.motion_state;assign live_offset=dut.live_offset;
 assign origin=dut.motion_origin;assign actual=dut.wide_encoder;assign refpos=dut.wide_target;
 assign bad={24'd0,dut.fast_acquisition_fault,dut.compare_deadline_fault,
  dut.acquisition_fault,dut.swing_permission,an1|an2|pwma,(pins[2]&pins[1]),
  (!permit && |pins),dut.adaptation_enabled|dut.gesture_enabled};
endmodule
