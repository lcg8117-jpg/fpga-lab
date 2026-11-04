// Verification-only event-step bridge. Production safety RTL is unchanged.
// The M velocity arithmetic/4-clock commit is source-identical. Physical
// edge age is an explicit sideband: compressed core clocks are not50MHz time.
module source_m_guard(
 input clk,rst_n,clear,tick_1ms,kit_ip570,
 input signed[31:0] encoder_raw,input[15:0] invalid_count,
 input[31:0] physical_edge_age_cycles,input illegal_edge_seen,
 output reg signed[31:0] legacy_velocity_q20,legacy_raw_q20,
 output reg measurement_fault
);
 reg signed[31:0] history[0:7];reg[3:0] filled;
 reg[15:0] invalid_previous;reg[2:0] state;
 reg signed[31:0] delta;reg signed[63:0] product;
 reg signed[32:0] error;reg signed[33:0] result;
 reg warm,stopped;integer i;
 wire signed[20:0] scale=kit_ip570?21'sd791875:21'sd762546;
 wire signed[33:0] next_value=$signed({{2{legacy_velocity_q20[31]}},legacy_velocity_q20})+(error>>>2);
 always @(posedge clk or negedge rst_n)
  if(!rst_n)begin
   filled<=0;invalid_previous<=0;state<=0;delta<=0;product<=0;error<=0;result<=0;
   warm<=1;stopped<=1;legacy_velocity_q20<=0;legacy_raw_q20<=0;measurement_fault<=0;
   for(i=0;i<8;i++)history[i]<=0;
  end else if(clear)begin
   filled<=0;invalid_previous<=invalid_count;state<=0;delta<=0;product<=0;error<=0;result<=0;
   warm<=1;stopped<=1;legacy_velocity_q20<=0;legacy_raw_q20<=0;measurement_fault<=0;
   for(i=0;i<8;i++)history[i]<=encoder_raw;
  end else if(measurement_fault || illegal_edge_seen || invalid_count!=invalid_previous)begin
   invalid_previous<=invalid_count;measurement_fault<=1;legacy_velocity_q20<=0;legacy_raw_q20<=0;state<=0;
  end else begin
   case(state)
    1:begin product<=delta*scale;state<=2;end
    2:begin error<=$signed({product[31],product[31:0]})-$signed({legacy_velocity_q20[31],legacy_velocity_q20});state<=3;end
    3:begin result<=next_value;state<=4;end
    4:begin state<=0;legacy_raw_q20<=warm?0:product[31:0];legacy_velocity_q20<=warm||stopped?0:result[31:0];end
    default:state<=0;
   endcase
   if(tick_1ms)begin
    for(i=7;i>0;i--)history[i]<=history[i-1];history[0]<=encoder_raw;
    delta<=encoder_raw-history[7];state<=1;warm<=filled<8;stopped<=physical_edge_age_cycles>=2500000;
    if(filled<8)filled<=filled+1'b1;
   end
   if(state==2 && !warm && product[63:31]!={33{product[31]}})begin
    measurement_fault<=1;state<=0;legacy_raw_q20<=0;legacy_velocity_q20<=0;
   end
  end
endmodule

module outer_guard_macro(
 input clk,rst_n,tick_1ms,kit_ip570,kit_changed,
 input zero_pulse,run_pulse,stop_pulse,clear_pulse,estop,
 input[9:0] adc_raw,input[13:0] adc_q4,input adc_otr_qualified,
 input signed[31:0] encoder_raw,input[15:0] invalid_count,
 input[31:0] physical_edge_age_cycles,input illegal_edge_seen,
 input mapping_valid,mapping_fault,motion_calibration_fault,core_busy,acquisition_fault,
 input source_sample_en,input[11:0] source_guard_mean,
 output[2:0] mode,output[7:0] fault,output zero_valid,capture_active,run_session,
 output[13:0] zero_q4,output signed[31:0] encoder_zero,
 output motor_permitted,clear_blocked,
 output signed[31:0] legacy_velocity_q20,legacy_raw_q20,output measurement_fault
);
 wire mclear=!zero_valid || capture_active || kit_changed;
 source_m_guard mpath(.clk(clk),.rst_n(rst_n),.clear(mclear),.tick_1ms(tick_1ms),.kit_ip570(kit_ip570),
  .encoder_raw(encoder_raw),.invalid_count(invalid_count),.physical_edge_age_cycles(physical_edge_age_cycles),
  .illegal_edge_seen(illegal_edge_seen),.legacy_velocity_q20(legacy_velocity_q20),.legacy_raw_q20(legacy_raw_q20),.measurement_fault(measurement_fault));
 forest_d1_safety #(.ALLOW_SWING(0),.ZERO_SAMPLES(1024)) actual(
  .clk(clk),.rst_n(rst_n),.tick_1ms(tick_1ms),.kit_ip570(kit_ip570),.invalidate_calibration(kit_changed),
  .zero_pulse(zero_pulse),.run_pulse(run_pulse),.stop_pulse(stop_pulse),.clear_pulse(clear_pulse),.estop_raw(estop),
  .start_swing_pulse(1'b0),.hold_pulse(1'b0),.handoff_pulse(1'b0),.swing_failed(1'b0),
  .adc_raw(adc_raw),.adc_q4(adc_q4),.adc_otr_qualified(adc_otr_qualified),.encoder_count(encoder_raw),.invalid_count(invalid_count),
  .arithmetic_fault(zero_valid&&((mapping_valid&&mapping_fault)||(source_sample_en&&core_busy)||acquisition_fault)),
  .clear_arithmetic_fault(zero_valid&&(motion_calibration_fault||(source_sample_en&&core_busy)||acquisition_fault)),
  .clear_blocked(clear_blocked),.source_sample_en(source_sample_en),.source_angle_adc12(source_guard_mean),
  .mode(mode),.fault(fault),.zero_valid(zero_valid),.capture_active(capture_active),.zero_q4(zero_q4),.encoder_zero(encoder_zero),
  .run_session(run_session),.motor_permitted(motor_permitted),.swing_permitted());
 // The original period path cannot see an event-compressed fabric clock.
 // Only outputs used by safety are replaced by the separately checked M path.
 // T cannot overflow when selected: period>50000 implies quotient<6335000.
 // Illegal pin/count transitions must be supplied by the real x4 adapter.
 initial begin
  force actual.velocity_guard.legacy_velocity_q20=legacy_velocity_q20;
  force actual.velocity_guard.legacy_raw_q20=legacy_raw_q20;
  force actual.velocity_guard.measurement_fault=measurement_fault;
 end
 task load_known_RUN(input[13:0] known_zero,input signed[31:0] known_encoder_zero);
  begin
   actual.mode=3;actual.fault=0;actual.zero_valid=1;actual.capture_active=0;
   actual.zero_q4=known_zero;actual.encoder_zero=known_encoder_zero;actual.run_session=1;
   actual.invalid_at_zero=invalid_count;actual.latest_source_mean_bad=0;
   actual.capture_count=0;actual.capture_sum=0;
   // This is a declared conditional initial state, not a reconstructed M history.
   // Caller must causally warm the M path and prove this counter is reset, or
   // retain this uncertainty in the diagnostic/acceptance metadata.
   actual.speed_fault_count=0;
  end
 endtask
endmodule

`timescale 1ns/1ps
module tb_source_m_guard;
 reg clk=0;always #10 clk=~clk;
 reg rst=0,clear=1;reg signed[31:0] count=0;
 integer cycle=0,checks=0,ms=0;wire tick=cycle%50000==49999;
 wire signed[31:0] original_m,original_raw,macro_m,macro_raw;
 wire original_fault,macro_fault;
 j280_encoder_velocity original(.clk(clk),.rst_n(rst),.tick_1ms(tick),.clear(clear),.encoder_count(count),.invalid_count(16'd0),
  .hybrid_mode(1'b1),.kit_ip570(1'b0),.velocity_q20(),.velocity_valid(),.using_period(),
  .legacy_velocity_q20(original_m),.legacy_raw_q20(original_raw),.measurement_fault(original_fault));
 source_m_guard equivalent(.clk(clk),.rst_n(rst),.clear(clear),.tick_1ms(tick),.kit_ip570(1'b0),.encoder_raw(count),.invalid_count(16'd0),
  .physical_edge_age_cycles(original.age_cycles),.illegal_edge_seen(1'b0),.legacy_velocity_q20(macro_m),.legacy_raw_q20(macro_raw),.measurement_fault(macro_fault));
 always @(negedge clk)if(rst)begin
  if(cycle>=500000 && cycle<1500000 && cycle%10000==0)count=count+1;
  if(cycle>=1500000 && cycle<2000000 && cycle%10000==0)count=count-1;
  cycle=cycle+1;
 end
 always @(posedge clk)if(rst)begin
  #1;
  if(cycle%50000==10 && cycle>100)begin
   checks++;if(original_m!==macro_m || original_raw!==macro_raw || original_fault!==macro_fault)
    $fatal(1,"M_GUARD source mismatch cycle%0d original%0d/%0d macro%0d/%0d",cycle,original_m,original_raw,macro_m,macro_raw);
  end
 end
 initial begin
  repeat(10)@(negedge clk);rst=1;repeat(100)@(negedge clk);clear=0;
  repeat(5100000)@(negedge clk);
  if(checks<100 || original_m!==0 || macro_m!==0)$fatal(1,"M guard coverage/stopped exactzero");
  $display("PASS SOURCE_M_GUARD checks=%0d legalForwardReverseStop=1 sourceFourClockCommit=1",checks);$finish;
 end
endmodule
