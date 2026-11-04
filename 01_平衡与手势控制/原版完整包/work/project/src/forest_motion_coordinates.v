// Wide physical/control coordinates and100ms observation; no controller filter.
module forest_motion_coordinates(
 input clk,rst_n,tick_5ms,reanchor,cancel,capture_hold,balance_active,kit_ip570,
 input signed [31:0] encoder_raw,input signed [63:0] target_wide,
 output reg signed [63:0] enc_wide_physical,output wire signed [63:0] encoder_wide,
 output reg signed [15:0] encoder_local,output wire signed [15:0] target_local,
 output wire fault,output wire signed [63:0] tracking_error,
 output reg signed [31:0] measured_velocity_cps,output reg velocity_valid
);
 reg signed [31:0] previous_raw;
 wire signed [31:0] raw_delta=encoder_raw-previous_raw;
 reg anchored,tracker_fault,local_fault;
 reg signed [63:0] quotient_floor;
 reg [4:0] remainder;
 // Both kits retain raw physical edges. Only the original 1080-edge kit
 // needs the inherited Forest 26/27 map; IP570 has 1040 edges per turn.
 assign encoder_wide=kit_ip570 ? enc_wide_physical : quotient_floor+
   ((enc_wide_physical[63] && remainder!=0) ? 64'sd1 : 64'sd0);
 assign target_local=16'sd10000;
 assign fault=tracker_fault || local_fault;
 // Euclidean quotient/remainder update has no division or large multiplier.
 // The physical decoder changes by at most one edge each FPGA clock.
 // Any larger jump is a real fault; it is never silently aliased or clipped.
 always @(posedge clk or negedge rst_n) begin
  if(!rst_n)begin
   previous_raw<=0;anchored<=0;enc_wide_physical<=0;
   quotient_floor<=0;remainder<=0;tracker_fault<=0;
  end else if(reanchor)begin
   previous_raw<=encoder_raw;anchored<=1;enc_wide_physical<=0;
   quotient_floor<=0;remainder<=0;tracker_fault<=0;
  end else begin
   previous_raw<=encoder_raw;
   if(anchored && !tracker_fault)begin
    if(raw_delta==32'sd1)begin
     if(enc_wide_physical==64'sh7fffffffffffffff)tracker_fault<=1;
     else begin
      enc_wide_physical<=enc_wide_physical+64'sd1;
      if(remainder==0)remainder<=26;
      else begin remainder<=remainder-1'b1;quotient_floor<=quotient_floor+64'sd1;end
     end
    end else if(raw_delta== -32'sd1)begin
     if(enc_wide_physical==64'sh8000000000000000)tracker_fault<=1;
     else begin
      enc_wide_physical<=enc_wide_physical-64'sd1;
      if(remainder==26)remainder<=0;
      else begin remainder<=remainder+1'b1;quotient_floor<=quotient_floor-64'sd1;end
     end
    end else if(raw_delta!=0)tracker_fault<=1;
   end
  end
 end
 wire signed [64:0] error_extended=
   $signed({encoder_wide[63],encoder_wide})-$signed({target_wide[63],target_wide});
 assign tracking_error=error_extended[63:0];
 wire error_bad=error_extended < -65'sd42768 || error_extended > 65'sd22767;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin encoder_local<=10000;local_fault<=0;end
  else if(reanchor)begin encoder_local<=10000;local_fault<=0;end
  else if(cancel || capture_hold)begin encoder_local<=10000;local_fault<=0;end
  else if(anchored)begin
   if(error_bad)begin local_fault<=1;encoder_local<=encoder_local;end
   else encoder_local<=16'sd10000+$signed(error_extended[15:0]);
  end
 end
 reg [4:0] velocity_tick;
 reg signed [63:0] velocity_position;

 wire signed [64:0] velocity_difference=
   $signed({encoder_wide[63],encoder_wide})-
   $signed({velocity_position[63],velocity_position});
 wire signed [68:0] velocity_times10=
   ($signed({{4{velocity_difference[64]}},velocity_difference})<<<3)+
   ($signed({{4{velocity_difference[64]}},velocity_difference})<<<1);
 wire velocity_fits32=velocity_times10[68:31]=={38{velocity_times10[31]}};
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   velocity_tick<=0;velocity_position<=0;velocity_valid<=0;measured_velocity_cps<=0;
  end else if(reanchor || cancel || capture_hold || !balance_active)begin
   velocity_tick<=0;velocity_position<=reanchor ? 64'sd0 : encoder_wide;
   velocity_valid<=0;measured_velocity_cps<=0;
  end else if(tick_5ms)begin
   if(velocity_tick==19)begin
    velocity_tick<=0;velocity_position<=encoder_wide;
    velocity_valid<=velocity_fits32;
    measured_velocity_cps<=velocity_fits32 ? velocity_times10[31:0] :
      (velocity_times10[68] ? 32'sh80000000 : 32'sh7fffffff);
   end else velocity_tick<=velocity_tick+1'b1;
  end
 end
endmodule
