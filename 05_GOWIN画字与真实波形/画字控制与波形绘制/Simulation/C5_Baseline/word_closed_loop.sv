`timescale 1ns/1ps
`include "word_constants.vh"
// Only this adapter schedules transactions. Production RTL remains unchanged.
// A tick is a real20ns clock period; quiescent clock cycles are omitted.
module word_control_lane #(
 parameter integer POINTS=3,
 parameter DATA_FILE="data/GOWIN.hex",
 parameter [31:0] CHECKSUM=0
)(
 input signed [15:0] measured_error,
 input signed [31:0] measured_raw,
 input [31:0] request_index,
 output reg signed [31:0] motor_command=0,
 output wire signed [31:0] target_position,actual_position,position_error,
 output wire [2:0] motion_state,
 output wire fault,trajectory_done,tracking_settled,
 output reg [31:0] completed_transactions=0,accepted_count=0,
 output wire [8:0] draw_index,
 output wire signed [31:0] reference_velocity,measured_velocity
);
 reg clk=0,rst_n=0,tick_5ms=0,reanchor=0,command_valid=0;
 reg signed [31:0] encoder_raw=0;
 reg cfg_write=0;
 reg [7:0] cfg_id=0;
 reg [31:0] cfg_value=0;
 wire cfg_ok,cfg_error,committed;
 wire [9:0] draw_length;
 wire signed [15:0] draw_sample;
 reg [15:0] program_data[0:511];
 wire signed [63:0] physical_wide,encoder_wide,target_wide,origin_wide,final_wide,tracking_error;
 wire signed [15:0] encoder_local,target_local;
 wire accepted,rejected,active;
 wire [7:0] reason;
 wire [3:0] planner_phase;
 wire signed [31:0] velocity_q,acceleration_q;
 forest_draw_buffer buffer(.clk(clk),.rst_n(rst_n),.tick_5ms(tick_5ms),
  .upload_permitted(1'b1),.abort(1'b0),.cfg_write(cfg_write),.cfg_id(cfg_id),.cfg_value(cfg_value),
  .cfg_ok(cfg_ok),.cfg_error(cfg_error),.cfg_revision(),.cfg_read_value(),
  .committed(committed),.length(draw_length),.read_address(draw_index),.sample(draw_sample));
 forest_motion_planner #(.VARIANT(5)) planner(
  .clk(clk),.rst_n(rst_n),.tick_5ms(tick_5ms),.reanchor(reanchor),.cancel(1'b0),.capture_hold(1'b0),
  .balance_active(1'b1),.kit_ip570(1'b0),.encoder_raw(encoder_raw),
  .command_valid(command_valid),.command(3'd0),.action_kind(2'd2),.action_index(3'd7),
  .request_id(32'd1),.left_positive(1'b1),.cw_positive(1'b1),
  .draw_valid(committed),.draw_length(draw_length),.draw_sample(draw_sample),.draw_address(draw_index),
  .upload_hold_ready(),.cfg_write(1'b0),.cfg_id(8'd0),.cfg_value(32'd0),
  .cfg_ok(),.cfg_error(),.cfg_revision(),.cfg_read_value(),
  .enc_wide_physical(physical_wide),.encoder_wide(encoder_wide),
  .target_wide(target_wide),.origin_wide(origin_wide),.final_wide(final_wide),
  .encoder_local(encoder_local),.target_local(target_local),.active(active),.fault(fault),
  .state(motion_state),.direction(),.accepted(accepted),.rejected(rejected),.reason(reason),
  .stop_reason(),.event_count(),.last_request_id(),.last_action(),
  .trajectory_done(trajectory_done),.tracking_settled(tracking_settled),
  .measured_velocity_cps(measured_velocity),.reference_velocity_cps(reference_velocity),
  .tracking_error(tracking_error),.settle_ticks(),.planner_phase(planner_phase),
  .velocity_q(velocity_q),.acceleration_q(acceleration_q));
 reg sample_en=0;
 reg [11:0] angle_adc12=3085;
 wire done,busy;
 wire signed [31:0] moto,balance_pwm,position_pwm;
 forest_d1_pid controller(.clk(clk),.rst_n(rst_n),.sample_en(sample_en),
  .integrator_clear(fault),.prime_history(completed_transactions==0),
  .angle_adc12(angle_adc12),.encoder(encoder_local),.gain_write(1'b0),
  .bkp_bits(32'h42440000),.bki_bits(32'd0),.bkd_bits(32'h437b0000),
  .pkp_bits(32'h41c80000),.pki_bits(32'd0),.pkd_bits(32'h44188000),
  .done(done),.moto(moto),.balance_pwm(balance_pwm),.position_pwm(position_pwm),.target(),.busy(busy));
 assign target_position=target_wide[31:0];
 assign actual_position=encoder_wide[31:0];
 assign position_error=tracking_error[31:0];
 task tick;begin #10;clk=1;#10;clk=0;end endtask
 task store(input [7:0] id,input [31:0] value);begin
  cfg_id=id;cfg_value=value;cfg_write=1;tick();
  if(!cfg_ok || cfg_error)$fatal(1,"Word buffer write rejected id=%0d value=%h",id,value);
  cfg_write=0;tick();
 end endtask
 integer k,clocks,edge_count;
 reg [31:0] previous_request=0;
 initial begin
  $readmemh(DATA_FILE,program_data,0,POINTS-1);
  repeat(4)tick();rst_n=1;repeat(3)tick();
  reanchor=1;tick();reanchor=0;repeat(3)tick();
  store(0,POINTS);
  for(k=0;k<POINTS;k=k+1)store(1,{7'd0,k[8:0],program_data[k]});
  store(2,CHECKSUM);
  if(!committed)$fatal(1,"Word upload did not commit");
  forever begin
   wait(request_index!=previous_request && request_index!=0);
   previous_request=request_index;
   if(measured_error>800 || measured_error< -800)$fatal(1,"Representative small-angle domain exceeded");
   edge_count=0;
   while(encoder_raw!=measured_raw && edge_count<1000)begin
    if(encoder_raw<measured_raw)encoder_raw=encoder_raw+1;else encoder_raw=encoder_raw-1;
    tick();edge_count=edge_count+1;
   end
   if(encoder_raw!=measured_raw)$fatal(1,"Model encoder rate exceeds adapter bound");
   repeat(4)tick();
   if(completed_transactions==100)begin
    command_valid=1;tick();
    if(!accepted || rejected)$fatal(1,"Word command rejected reason=%0d",reason);
    accepted_count=accepted_count+1;command_valid=0;tick();
   end
   tick_5ms=1;tick();tick_5ms=0;repeat(128)tick();
   clocks=0;
   while(planner_phase!=0 && clocks<32)begin tick();clocks=clocks+1;end
   if(planner_phase!=0)$fatal(1,"Planner did not finish a5ms update");
   repeat(3)tick();
   angle_adc12=3085+$signed(measured_error);
   sample_en=1;tick();sample_en=0;clocks=0;
   while(!done && clocks<2000)begin tick();clocks=clocks+1;end
   if(!done)$fatal(1,"Actual PID transaction timeout");
   motor_command=fault ? 0:moto;
   if(fault)$fatal(1,"Actual planner protection fault reason=%0d",reason);
   repeat(16)tick();completed_transactions=completed_transactions+1;
  end
 end
endmodule

module word_closed_loop(
 input signed [15:0] g_error,e_error,
 input signed [31:0] g_raw,e_raw,
 input [31:0] g_request,e_request,
 output wire signed [31:0] g_motor,g_target,g_actual,g_tracking,g_vref,g_vmeas,
 output wire [2:0] g_state,
 output wire g_fault,g_done,g_settled,
 output wire [31:0] g_completed,g_accepted,
 output wire [8:0] g_draw_index,
 output wire signed [31:0] e_motor,e_target,e_actual,e_tracking,e_vref,e_vmeas,
 output wire [2:0] e_state,
 output wire e_fault,e_done,e_settled,
 output wire [31:0] e_completed,e_accepted,
 output wire [8:0] e_draw_index
);
 word_control_lane #(.POINTS(`GOWIN_POINTS),.DATA_FILE("data/GOWIN.hex"),.CHECKSUM(`GOWIN_CHECKSUM)) g_lane(
  .measured_error(g_error),.measured_raw(g_raw),.request_index(g_request),
  .motor_command(g_motor),.target_position(g_target),.actual_position(g_actual),.position_error(g_tracking),
  .motion_state(g_state),.fault(g_fault),.trajectory_done(g_done),.tracking_settled(g_settled),
  .completed_transactions(g_completed),.accepted_count(g_accepted),.draw_index(g_draw_index),
  .reference_velocity(g_vref),.measured_velocity(g_vmeas));
 word_control_lane #(.POINTS(`EASYSTART_POINTS),.DATA_FILE("data/EasyStart.hex"),.CHECKSUM(`EASYSTART_CHECKSUM)) e_lane(
  .measured_error(e_error),.measured_raw(e_raw),.request_index(e_request),
  .motor_command(e_motor),.target_position(e_target),.actual_position(e_actual),.position_error(e_tracking),
  .motion_state(e_state),.fault(e_fault),.trajectory_done(e_done),.tracking_settled(e_settled),
  .completed_transactions(e_completed),.accepted_count(e_accepted),.draw_index(e_draw_index),
  .reference_velocity(e_vref),.measured_velocity(e_vmeas));
endmodule

