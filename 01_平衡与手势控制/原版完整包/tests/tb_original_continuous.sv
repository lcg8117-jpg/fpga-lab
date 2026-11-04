`timescale 1ns/1ps
module tb_original_continuous;
 reg clk=0;always #5 clk=~clk;
 reg rst_n=0,tick=0,reanchor=0,balance=0,valid=0,cfg_write=0;
 reg [2:0] command=0;reg [7:0] cfg_id=10;reg [31:0] cfg_value=0;
 wire signed [63:0] target,legacy_target;
 wire signed [31:0] velocity,acceleration,ref_speed,legacy_v,legacy_a,legacy_speed;
 wire [2:0] state,legacy_state;wire done,legacy_done,fault,legacy_fault;
 integer checks=0,k,direction,ticks;
 forest_motion_planner #(.VARIANT(2)) current(.clk(clk),.rst_n(rst_n),.tick_5ms(tick),
  .reanchor(reanchor),.cancel(1'b0),.capture_hold(1'b0),.balance_active(balance),.kit_ip570(1'b1),
  .encoder_raw(32'sd0),.command_valid(valid),.command(command),.action_kind(2'd0),.action_index(3'd0),
  .request_id(32'd1),.left_positive(1'b1),.cw_positive(1'b1),.cfg_write(cfg_write),.cfg_id(cfg_id),.cfg_value(cfg_value),
  .target_wide(target),.velocity_q(velocity),.acceleration_q(acceleration),.reference_velocity_cps(ref_speed),
  .state(state),.trajectory_done(done),.fault(fault));
 legacy_forest_motion_planner #(.VARIANT(2)) original(.clk(clk),.rst_n(rst_n),.tick_5ms(tick),
  .reanchor(reanchor),.cancel(1'b0),.capture_hold(1'b0),.balance_active(balance),.kit_ip570(1'b1),
  .encoder_raw(32'sd0),.command_valid(valid),.command(command),.action_kind(2'd0),.action_index(3'd0),
  .request_id(32'd1),.left_positive(1'b1),.cw_positive(1'b1),.cfg_write(1'b0),.cfg_id(8'd0),.cfg_value(32'd0),
  .target_wide(legacy_target),.velocity_q(legacy_v),.acceleration_q(legacy_a),.reference_velocity_cps(legacy_speed),
  .state(legacy_state),.trajectory_done(legacy_done),.fault(legacy_fault));
 task check(input bit condition,input string message);begin
  checks++;if(!condition)begin $display("FAIL ORIGINAL %s state=%0d/%0d target=%0d/%0d velocity=%0d/%0d acceleration=%0d/%0d",message,state,legacy_state,target,legacy_target,velocity,legacy_v,acceleration,legacy_a);$fatal(1);end
 end endtask
 task step;begin
  @(negedge clk);tick=1;@(negedge clk);tick=0;repeat(512)@(negedge clk);
  check(!fault && !legacy_fault,"no fault in original/current");
  check(target==legacy_target,"actual controller reference matches qualified original each5ms");
  check(velocity==legacy_v && acceleration==legacy_a,"velocity and acceleration match original each5ms");
  check(ref_speed==legacy_speed && state==legacy_state && done==legacy_done,"state and published reference velocity match original");
  check(!current.shape_mode,"single-turn shaping never enters continuous or its braking path");
 end endtask
 task send(input [2:0] value);begin @(negedge clk);command=value;valid=1;@(negedge clk);valid=0;repeat(20)@(negedge clk);end endtask
 initial begin
  for(direction=0;direction<2;direction++)begin
   rst_n=0;balance=0;repeat(4)@(negedge clk);rst_n=1;reanchor=1;@(negedge clk);reanchor=0;
   cfg_write=1;@(negedge clk);cfg_write=0;repeat(8)@(negedge clk);balance=1;repeat(8)@(negedge clk);
   send(direction==0 ? 3:4);
   for(k=0;k<1000;k++)step();
   send(direction==0 ? 4:3);ticks=0;
   while(!done && ticks<1000)begin step();ticks++;end
   check(done && legacy_done && velocity==0 && acceleration==0,"original brake completes without new return target");
   for(k=0;k<150;k++)step();
   $display("ORIGINAL_CONTINUOUS direction=%0d brake_ms=%0d endpoint=%0d",direction,ticks*5,target);
  end
  $display("PASS ORIGINAL_CONTINUOUS checks=%0d both_directions=1 every5ms_reference_velocity_acceleration_exact=1",checks);$finish;
 end
endmodule
