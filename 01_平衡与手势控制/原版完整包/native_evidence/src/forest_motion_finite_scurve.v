// Exact discrete seven-phase reference for one1040-count turn.
// All planning is on FPGA. A shared bit-serial unit avoids extra DSPs and
// wide combinational division. The integral weights finish exactly at D.
module forest_motion_finite_scurve(
 input clk,rst_n,start,cancel,tick,
 input [17:0] speed_q16,input [15:0] acceleration_limit_q16,jerk_limit_q16,
 output reg active,ready,sample_valid,finished,fault,
 output reg [31:0] offset_q16,velocity_q16,
 output reg signed [31:0] acceleration_q16
);
 localparam [31:0] DISTANCE_Q16=32'd68157440;
 reg [4:0] state;
 reg [17:0] speed;
 reg [15:0] accel_limit,jerk_limit,ramp,middle,accel_ticks,cruise,den_ticks;
 reg [31:0] peak_weight,total_weight,velocity_weight,position_weight;
 reg signed [15:0] acceleration_weight,next_acceleration_weight;
 reg [2:0] segment;
 reg [15:0] segment_tick,segment_length;
 reg signed [32:0] next_velocity_weight;
 reg [32:0] next_position_weight;
 reg [31:0] step_velocity,step_position;
 reg signed [15:0] step_acceleration,prepared_acceleration;
 reg signed [32:0] prepared_velocity;reg [32:0] prepared_position;
 reg last_step;
 reg math_start,math_multiply;
 reg [63:0] math_numerator;
 reg [31:0] math_divisor,math_left,math_right;
 wire math_done,math_busy,math_error;
 wire [63:0] math_result;
 forest_motion_serial_math arithmetic(.clk(clk),.rst_n(rst_n),
  .cancel(cancel||start||fault),.start(math_start),.multiply(math_multiply),
  .numerator(math_numerator),.divisor(math_divisor),.left(math_left),.right(math_right),
  .busy(math_busy),.done(math_done),.error(math_error),.result(math_result));
 function [63:0] scaled_weight;
  input [31:0] value;
  begin scaled_weight=({32'd0,value}<<26)+({32'd0,value}<<20);end
 endfunction
 wire [15:0] step_acceleration_abs=step_acceleration[15] ? -step_acceleration:step_acceleration;
 always @*begin
  next_acceleration_weight=acceleration_weight;
  case(segment)
   0,6:next_acceleration_weight=acceleration_weight+16'sd1;
   2,4:next_acceleration_weight=acceleration_weight-16'sd1;
   3:next_acceleration_weight=0;
   default:;
  endcase
  next_velocity_weight=$signed({1'b0,velocity_weight})+
    $signed({{17{next_acceleration_weight[15]}},next_acceleration_weight});
  next_position_weight={1'b0,position_weight}+{1'b0,next_velocity_weight[31:0]};
  case(segment)
   1,5:segment_length=middle-ramp;
   3:segment_length=cruise;
   default:segment_length=ramp;
  endcase
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=0;active<=0;ready<=0;sample_valid<=0;finished<=0;fault<=0;
   offset_q16<=0;velocity_q16<=0;acceleration_q16<=0;
   speed<=0;accel_limit<=0;jerk_limit<=0;ramp<=0;middle<=0;accel_ticks<=0;
   cruise<=0;den_ticks<=0;peak_weight<=0;total_weight<=0;
   velocity_weight<=0;position_weight<=0;acceleration_weight<=0;
   segment<=0;segment_tick<=0;step_velocity<=0;step_position<=0;step_acceleration<=0;prepared_acceleration<=0;prepared_velocity<=0;prepared_position<=0;last_step<=0;
   math_start<=0;math_multiply<=0;math_numerator<=0;math_divisor<=0;math_left<=0;math_right<=0;
  end else begin
   sample_valid<=0;finished<=0;math_start<=0;
   if(cancel)begin
    state<=0;active<=0;ready<=0;fault<=0;
    offset_q16<=0;velocity_q16<=0;acceleration_q16<=0;
   end else if(start)begin
    state<=1;active<=1;ready<=0;fault<=0;
    offset_q16<=0;velocity_q16<=0;acceleration_q16<=0;
    speed<=speed_q16;accel_limit<=acceleration_limit_q16;jerk_limit<=jerk_limit_q16;
    velocity_weight<=0;position_weight<=0;acceleration_weight<=0;segment<=0;segment_tick<=0;
    if(speed_q16==0 || acceleration_limit_q16==0 || jerk_limit_q16==0)begin fault<=1;active<=0;state<=0;end
   end else if(active)begin
    if(math_error || (tick && state>=12))begin fault<=1;active<=0;ready<=0;state<=0;end
    else case(state)
     // r=ceil(A/J); m=max(r,ceil(V/A)); acceleration duration=r+m.
     1:begin math_multiply<=0;math_numerator<={48'd0,accel_limit}+{48'd0,jerk_limit}-1'b1;math_divisor<={16'd0,jerk_limit};math_start<=1;state<=2;end
     2:if(math_done)begin
      if(math_result==0 || math_result>65535)begin fault<=1;active<=0;state<=0;end
      else begin ramp<=math_result[15:0];state<=3;end
     end
     3:begin math_multiply<=0;math_numerator<={46'd0,speed}+{48'd0,accel_limit}-1'b1;math_divisor<={16'd0,accel_limit};math_start<=1;state<=4;end
     4:if(math_done)begin
      if(math_result>65535 || math_result+{48'd0,ramp}>65535)begin fault<=1;active<=0;state<=0;end
      else begin
       middle<=math_result<ramp ? ramp:math_result[15:0];
       accel_ticks<=math_result<ramp ? {ramp[14:0],1'b0}:math_result[15:0]+ramp;state<=5;
      end
     end
     // Denominator ticks=max(acceleration duration,ceil(D/V)); cruise fills
     // any remaining duration. Peak speed never exceeds the requested cap.
     5:begin math_multiply<=0;math_numerator<={32'd0,DISTANCE_Q16}+{46'd0,speed}-1'b1;math_divisor<={14'd0,speed};math_start<=1;state<=6;end
     6:if(math_done)begin
      if(math_result>65535)begin fault<=1;active<=0;state<=0;end
      else begin
       den_ticks<=math_result>accel_ticks ? math_result[15:0]:accel_ticks;
       cruise<=math_result>accel_ticks ? math_result[15:0]-accel_ticks:16'd0;state<=7;
      end
     end
     7:begin math_multiply<=1;math_left<={16'd0,ramp};math_right<={16'd0,middle};math_start<=1;state<=8;end
     8:if(math_done)begin
      if(math_result==0 || math_result[63:32]!=0)begin fault<=1;active<=0;state<=0;end
      else begin peak_weight<=math_result[31:0];state<=9;end
     end
     9:begin math_multiply<=1;math_left<=peak_weight;math_right<={16'd0,den_ticks};math_start<=1;state<=10;end
     10:if(math_done)begin
      if(math_result==0 || math_result[63:31]!=0)begin fault<=1;active<=0;state<=0;end
      else begin total_weight<=math_result[31:0];ready<=1;state<=11;end
     end
     11:if(tick)begin ready<=0;prepared_acceleration<=next_acceleration_weight;state<=17;end
     17:begin prepared_velocity<=$signed({1'b0,velocity_weight})+
       $signed({{17{prepared_acceleration[15]}},prepared_acceleration});state<=18;end
     18:begin prepared_position<={1'b0,position_weight}+{1'b0,prepared_velocity[31:0]};state<=19;end
     19:begin
      if(prepared_velocity<0 || prepared_velocity>$signed({1'b0,peak_weight}) ||
         prepared_position>{1'b0,total_weight} ||
         (segment==6 && segment_tick+1>=ramp &&
          (prepared_velocity!=0 || prepared_acceleration!=0 || prepared_position!={1'b0,total_weight})))begin
       fault<=1;active<=0;ready<=0;state<=0;
      end else begin
       ready<=0;step_acceleration<=prepared_acceleration;
       step_velocity<=prepared_velocity[31:0];step_position<=prepared_position[31:0];
       acceleration_weight<=prepared_acceleration;velocity_weight<=prepared_velocity[31:0];position_weight<=prepared_position[31:0];
       last_step<=segment==6 && segment_tick+1>=ramp;
       if(segment_tick+1>=segment_length)begin
        segment_tick<=0;
        case(segment)
         0:segment<=middle==ramp ? 2:1;
         2:segment<=cruise==0 ? 4:3;
         4:segment<=middle==ramp ? 6:5;
         default:segment<=segment+1'b1;
        endcase
       end else segment_tick<=segment_tick+1'b1;
       state<=20;
      end
     end
     20:begin math_multiply<=0;math_numerator<=scaled_weight(step_position);math_divisor<=total_weight;math_start<=1;state<=12;end
     12:if(math_done)begin
      if(math_result>DISTANCE_Q16)begin fault<=1;active<=0;state<=0;end
      else begin offset_q16<=math_result[31:0];state<=13;end
     end
     13:begin math_multiply<=0;math_numerator<=scaled_weight(step_velocity);math_divisor<=total_weight;math_start<=1;state<=14;end
     14:if(math_done)begin
      if(math_result>speed)begin fault<=1;active<=0;state<=0;end
      else begin velocity_q16<=math_result[31:0];state<=15;end
     end
     15:begin math_multiply<=0;math_numerator<=scaled_weight({16'd0,step_acceleration_abs});math_divisor<=total_weight;math_start<=1;state<=16;end
     16:if(math_done)begin
      if(math_result>accel_limit)begin fault<=1;active<=0;state<=0;end
      else begin
       acceleration_q16<=step_acceleration[15] ? -$signed(math_result[31:0]):$signed(math_result[31:0]);
       sample_valid<=1;finished<=last_step;ready<=!last_step;
       if(last_step)begin active<=0;state<=0;end else state<=11;
      end
     end
     default:begin fault<=1;active<=0;ready<=0;state<=0;end
    endcase
   end
  end
 end
endmodule

// Bounded-latency unsigned32x32 multiply / unsigned64-by32 division.
// No DSP or combinational divider; each operation uses32 or64 FPGA clocks.
module forest_motion_serial_math(
 input clk,rst_n,cancel,start,multiply,
 input [63:0] numerator,input [31:0] divisor,left,right,
 output reg busy,done,error,output reg [63:0] result
);
 reg operation;
 reg [5:0] iteration;
 reg [63:0] shift_value,accumulator;
 reg [31:0] denominator,multiplier;
 reg [32:0] remainder;
 wire [32:0] trial={remainder[31:0],shift_value[63]};
 wire subtract=trial>={1'b0,denominator};
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin busy<=0;done<=0;error<=0;result<=0;operation<=0;iteration<=0;shift_value<=0;accumulator<=0;denominator<=0;multiplier<=0;remainder<=0;end
  else begin
   done<=0;
   if(cancel)begin busy<=0;done<=0;error<=0;end
   else if(start && !busy)begin
    operation<=multiply;iteration<=0;accumulator<=0;remainder<=0;busy<=1;error<=0;
    shift_value<=multiply ? {32'd0,left}:numerator;denominator<=divisor;multiplier<=right;
    if(!multiply && divisor==0)begin busy<=0;done<=1;error<=1;result<=0;end
   end else if(busy)begin
    if(operation)begin
     accumulator<=accumulator+(multiplier[0] ? shift_value:64'd0);
     shift_value<=shift_value<<1;multiplier<=multiplier>>1;
     if(iteration==31)begin result<=accumulator+(multiplier[0] ? shift_value:64'd0);busy<=0;done<=1;end
    end else begin
     shift_value<=shift_value<<1;
     remainder<=subtract ? trial-{1'b0,denominator}:trial;
     accumulator<={accumulator[62:0],subtract};
     if(iteration==63)begin result<={accumulator[62:0],subtract};busy<=0;done<=1;end
    end
    iteration<=iteration+1'b1;
   end
  end
 end
endmodule
