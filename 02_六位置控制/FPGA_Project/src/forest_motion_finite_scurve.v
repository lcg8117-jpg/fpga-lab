// Distance-parametric discrete seven-phase reference, starting from rest.
// Units: position Q16 count; velocity Q16 count/5ms tick; a is delta-v/tick.
// ramp/middle are 16 bits, denominator/segment durations 32 bits. Integral
// weights are 64 bits, so the complete D32 * weight64 domain is 96 bits.
// The shared serial unit below performs only 16-bit carry/borrow operations.
module forest_motion_finite_scurve #(parameter integer PROJECT_DOMAIN=0)(
 input clk,rst_n,start,cancel,tick,
 input [31:0] distance_q16,
 input [17:0] speed_q16,input [15:0] acceleration_limit_q16,jerk_limit_q16,
 output reg active,ready,sample_valid,finished,fault,
 output reg [31:0] offset_q16,velocity_q16,
 output reg signed [31:0] acceleration_q16
);
 // Default domain retains the original full-width generator. The project
 // specialization accepts every u32 Q16 distance with unchanged legal caps.
 localparam integer R_BITS=PROJECT_DOMAIN ? 10:16;
 localparam integer M_BITS=PROJECT_DOMAIN ? 11:16;
 localparam integer T_BITS=PROJECT_DOMAIN ? 18:32;
 localparam integer V_BITS=PROJECT_DOMAIN ? 20:32;
 localparam integer P_BITS=PROJECT_DOMAIN ? 40:64;
 localparam integer A_BITS=PROJECT_DOMAIN ? 11:18;
 localparam integer LA_BITS=PROJECT_DOMAIN ? 11:16;
 localparam integer LJ_BITS=PROJECT_DOMAIN ? 7:16;
 localparam integer MATH_N=PROJECT_DOMAIN ? 80:96;
 localparam integer MATH_D=PROJECT_DOMAIN ? 48:64;
 wire project_caps_valid=speed_q16>=18'd19660 && speed_q16<=18'd170393 &&
  (acceleration_limit_q16==16'd131 || acceleration_limit_q16==16'd262 ||
   acceleration_limit_q16==16'd524 || acceleration_limit_q16==16'd1049 || acceleration_limit_q16==16'd1966) &&
  (jerk_limit_q16==16'd3 || jerk_limit_q16==16'd7 || jerk_limit_q16==16'd13 ||
   jerk_limit_q16==16'd26 || jerk_limit_q16==16'd98);
 reg [5:0] state;
 reg [31:0] distance;
 reg [17:0] speed;
 reg [LA_BITS-1:0] accel_limit;reg [LJ_BITS-1:0] jerk_limit;
 reg [R_BITS-1:0] ramp;reg [M_BITS-1:0] middle;
 reg [T_BITS-1:0] accel_ticks,cruise,den_ticks;
 reg [V_BITS-1:0] peak_weight,velocity_weight;
 reg [P_BITS-1:0] total_weight,position_weight;
 reg signed [A_BITS-1:0] acceleration_weight,next_acceleration_weight;
 // P1 commits the private weight stages early and reuses those registers.
 // Public p/v/a are still committed only by state26 after all original checks.
 reg signed [A_BITS-1:0] generic_prepared_acceleration;
 reg [2:0] segment;
 reg [T_BITS-1:0] segment_tick,segment_length;
 reg signed [V_BITS:0] generic_prepared_velocity;
 reg [P_BITS:0] generic_prepared_position;
 reg project_velocity_sign,project_position_carry;
 wire signed [A_BITS-1:0] prepared_acceleration=PROJECT_DOMAIN ? acceleration_weight:generic_prepared_acceleration;
 wire signed [V_BITS:0] prepared_velocity=PROJECT_DOMAIN ? $signed({project_velocity_sign,velocity_weight}):generic_prepared_velocity;
 wire [P_BITS:0] prepared_position=PROJECT_DOMAIN ? {project_position_carry,position_weight}:generic_prepared_position;
 reg last_step;
 reg [P_BITS-1:0] generic_step_position;
 wire [P_BITS-1:0] step_position=PROJECT_DOMAIN ? position_weight:generic_step_position;
 reg [31:0] publish_offset,publish_velocity;
 reg signed [32:0] publish_acceleration;
 reg math_start,math_multiply;
 reg [MATH_N-1:0] math_numerator;
 reg [MATH_D-1:0] math_divisor,math_left;
 reg [31:0] math_right;
 wire math_done,math_busy,math_error;
 wire [MATH_N-1:0] math_result;
 wire [T_BITS:0] segment_step_count={1'b0,segment_tick}+{{T_BITS{1'b0}},1'b1};
 function greater64;
  input [63:0] left_value,right_value;
  reg [3:0] gt,eq;
  integer word;
  begin
   for(word=0;word<4;word=word+1)begin
    gt[word]=left_value[word*16+:16]>right_value[word*16+:16];
    eq[word]=left_value[word*16+:16]==right_value[word*16+:16];
   end
   greater64=(gt[3] || (eq[3] && gt[2])) ||
    ((eq[3] && eq[2]) && (gt[1] || (eq[1] && gt[0])));
  end
 endfunction
 generate if(PROJECT_DOMAIN)begin:project_domain_math
  forest_motion_serial_math_project arithmetic(.clk(clk),.rst_n(rst_n),
   .cancel(cancel||start||fault),.start(math_start),.multiply(math_multiply),
   .numerator(math_numerator),.divisor(math_divisor),.left(math_left),.right(math_right),
   .busy(math_busy),.done(math_done),.error(math_error),.result(math_result));
 end else begin:generic_domain_math
  forest_motion_serial_math arithmetic(.clk(clk),.rst_n(rst_n),
   .cancel(cancel||start||fault),.start(math_start),.multiply(math_multiply),
   .numerator(math_numerator),.divisor(math_divisor),.left(math_left),.right(math_right),
   .busy(math_busy),.done(math_done),.error(math_error),.result(math_result));
 end endgenerate
 always @*begin
  next_acceleration_weight=acceleration_weight;
  case(segment)
   0,6:next_acceleration_weight=acceleration_weight+{{(A_BITS-1){1'b0}},1'b1};
   2,4:next_acceleration_weight=acceleration_weight-{{(A_BITS-1){1'b0}},1'b1};
   3:next_acceleration_weight=0;
   default:;
  endcase
  case(segment)
   1,5:segment_length={{(T_BITS-M_BITS){1'b0}},middle}-{{(T_BITS-R_BITS){1'b0}},ramp};
   3:segment_length=cruise;
   default:segment_length={{(T_BITS-R_BITS){1'b0}},ramp};
  endcase
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=0;active<=0;ready<=0;sample_valid<=0;finished<=0;fault<=0;
   offset_q16<=0;velocity_q16<=0;acceleration_q16<=0;
   distance<=0;speed<=0;accel_limit<=0;jerk_limit<=0;ramp<=0;middle<=0;
   accel_ticks<=0;cruise<=0;den_ticks<=0;peak_weight<=0;total_weight<=0;
   velocity_weight<=0;position_weight<=0;acceleration_weight<=0;
   segment<=0;segment_tick<=0;generic_prepared_acceleration<=0;generic_prepared_velocity<=0;
   generic_prepared_position<=0;generic_step_position<=0;last_step<=0;
   project_velocity_sign<=0;project_position_carry<=0;
   publish_offset<=0;publish_velocity<=0;publish_acceleration<=0;
   math_start<=0;math_multiply<=0;math_numerator<=0;math_divisor<=0;math_left<=0;math_right<=0;
  end else begin
   sample_valid<=0;finished<=0;math_start<=0;
   if(cancel)begin
    state<=0;active<=0;ready<=0;fault<=0;
    offset_q16<=0;velocity_q16<=0;acceleration_q16<=0;
   end else if(start)begin
    state<=1;active<=1;ready<=0;fault<=0;
    offset_q16<=0;velocity_q16<=0;acceleration_q16<=0;
    distance<=distance_q16;speed<=speed_q16;
    accel_limit<=acceleration_limit_q16;jerk_limit<=jerk_limit_q16;
    velocity_weight<=0;position_weight<=0;acceleration_weight<=0;segment<=0;segment_tick<=0;
    if(PROJECT_DOMAIN && !project_caps_valid)begin
     fault<=1;active<=0;state<=0;
    end else if(distance_q16==0)begin
     state<=0;active<=0;sample_valid<=1;finished<=1;
    end else if(speed_q16==0 || acceleration_limit_q16==0 || jerk_limit_q16==0)begin
     fault<=1;active<=0;state<=0;
    end
   end else if(active)begin
    if(math_error || (tick && state>=12))begin
     fault<=1;active<=0;ready<=0;state<=0;
    end else case(state)
     // r=ceil(A/J), m=max(r,ceil(V/A)), T=max(r+m,ceil(D/V)).
     // The normalized peak is r*m; total integral weight is r*m*T.
     1:begin
      math_multiply<=0;math_numerator<={{(MATH_N-LA_BITS){1'b0}},accel_limit}+{{(MATH_N-LJ_BITS){1'b0}},jerk_limit}-1'b1;
      math_divisor<={{(MATH_D-LJ_BITS){1'b0}},jerk_limit};math_start<=1;state<=2;
     end
     2:if(math_done)begin
      if(math_result==0 || math_result[MATH_N-1:R_BITS]!=0)begin fault<=1;active<=0;state<=0;end
      else begin ramp<=math_result[R_BITS-1:0];state<=3;end
     end
     3:begin
      math_multiply<=0;math_numerator<={{(MATH_N-18){1'b0}},speed}+{{(MATH_N-LA_BITS){1'b0}},accel_limit}-1'b1;
      math_divisor<={{(MATH_D-LA_BITS){1'b0}},accel_limit};math_start<=1;state<=4;
     end
     4:if(math_done)begin
      if(math_result[MATH_N-1:M_BITS]!=0)begin fault<=1;active<=0;state<=0;end
      else begin
       middle<=math_result<{{(MATH_N-R_BITS){1'b0}},ramp} ? ramp:math_result[M_BITS-1:0];
       accel_ticks<=math_result<{{(MATH_N-R_BITS){1'b0}},ramp} ? {{(T_BITS-R_BITS-1){1'b0}},ramp,1'b0}:{{(T_BITS-M_BITS){1'b0}},math_result[M_BITS-1:0]}+{{(T_BITS-R_BITS){1'b0}},ramp};
       state<=5;
      end
     end
     5:begin
      math_multiply<=0;math_numerator<={{(MATH_N-32){1'b0}},distance}+{{(MATH_N-18){1'b0}},speed}-1'b1;
      math_divisor<={{(MATH_D-18){1'b0}},speed};math_start<=1;state<=6;
     end
     6:if(math_done)begin
      if(math_result[MATH_N-1:T_BITS]!=0)begin fault<=1;active<=0;state<=0;end
      else begin
       den_ticks<=math_result[T_BITS-1:0]>accel_ticks ? math_result[T_BITS-1:0]:accel_ticks;
       cruise<=math_result[T_BITS-1:0]>accel_ticks ? math_result[T_BITS-1:0]-accel_ticks:{T_BITS{1'b0}};
       state<=7;
      end
     end
     7:begin math_multiply<=1;math_left<={{(MATH_D-R_BITS){1'b0}},ramp};math_right<={{(32-M_BITS){1'b0}},middle};math_start<=1;state<=8;end
     8:if(math_done)begin
      if(math_result==0 || math_result[MATH_N-1:V_BITS]!=0)begin fault<=1;active<=0;state<=0;end
      else begin peak_weight<=math_result[V_BITS-1:0];state<=9;end
     end
     9:begin math_multiply<=1;math_left<={{(MATH_D-V_BITS){1'b0}},peak_weight};math_right<={{(32-T_BITS){1'b0}},den_ticks};math_start<=1;state<=10;end
     10:if(math_done)begin
      if(math_result==0 || math_result[MATH_N-1:P_BITS]!=0)begin fault<=1;active<=0;state<=0;end
      else begin total_weight<=math_result[P_BITS-1:0];ready<=1;state<=11;end
     end
     11:if(tick)begin
      ready<=0;
      if(PROJECT_DOMAIN)acceleration_weight<=next_acceleration_weight;
      else generic_prepared_acceleration<=next_acceleration_weight;
      state<=17;
     end
     17:begin
      if(PROJECT_DOMAIN){project_velocity_sign,velocity_weight}<=$signed({1'b0,velocity_weight})+
        $signed({{(V_BITS+1-A_BITS){prepared_acceleration[A_BITS-1]}},prepared_acceleration});
      else generic_prepared_velocity<=$signed({1'b0,velocity_weight})+
        $signed({{(V_BITS+1-A_BITS){prepared_acceleration[A_BITS-1]}},prepared_acceleration});
      state<=18;
     end
     18:begin
      if(PROJECT_DOMAIN){project_position_carry,position_weight}<={1'b0,position_weight}+{{(P_BITS-V_BITS){1'b0}},prepared_velocity};
      else generic_prepared_position<={1'b0,position_weight}+{{(P_BITS-V_BITS){1'b0}},prepared_velocity};
      state<=19;
     end
     19:begin
      if(prepared_velocity<0 || prepared_velocity>$signed({1'b0,peak_weight}) ||
         prepared_position[P_BITS] || greater64({{(64-P_BITS){1'b0}},prepared_position[P_BITS-1:0]},{{(64-P_BITS){1'b0}},total_weight}) ||
         (segment==6 && segment_step_count>={{(T_BITS+1-R_BITS){1'b0}},ramp} &&
          (prepared_velocity!=0 || prepared_acceleration!=0 || prepared_position!={1'b0,total_weight})))begin
       fault<=1;active<=0;ready<=0;state<=0;
      end else begin
       if(!PROJECT_DOMAIN)begin
        generic_step_position<=prepared_position[P_BITS-1:0];
        acceleration_weight<=prepared_acceleration;velocity_weight<=prepared_velocity[V_BITS-1:0];position_weight<=prepared_position[P_BITS-1:0];
       end
       last_step<=segment==6 && segment_step_count>={{(T_BITS+1-R_BITS){1'b0}},ramp};
       if(segment_step_count>={1'b0,segment_length})begin
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
     20:begin math_multiply<=1;math_left<=step_position;math_right<=distance;math_start<=1;state<=21;end
     21:if(math_done)begin math_numerator<=math_result;state<=22;end
     22:begin math_multiply<=0;math_divisor<=total_weight;math_start<=1;state<=23;end
     23:if(math_done)begin
      if(math_result[MATH_N-1:32]!=0 || math_result[31:0]>distance || math_result[31:0]<offset_q16)begin
       fault<=1;active<=0;ready<=0;state<=0;
      end else begin publish_offset<=math_result[31:0];state<=24;end
     end
     // Keep two discrete derivatives out of a single carry-chain path.
     24:begin publish_velocity<=publish_offset-offset_q16;state<=25;end
     25:begin publish_acceleration<=$signed({1'b0,publish_velocity})-$signed({1'b0,velocity_q16});state<=26;end
     26:begin
      if(publish_velocity>{14'd0,speed} || publish_acceleration[32]!=publish_acceleration[31] ||
         (last_step && (publish_offset!=distance || publish_velocity!=0 || publish_acceleration!=0)))begin
       fault<=1;active<=0;ready<=0;state<=0;
      end else begin
       offset_q16<=publish_offset;velocity_q16<=publish_velocity;
       acceleration_q16<=publish_acceleration[31:0];
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

// Shared unsigned 64x32 multiplication / unsigned96-by64 division.
// Sixteen-bit digit carries are registered: no wide combinational multiplier,
// divider, subtraction or comparison is hidden in the numerical data path.
module forest_motion_serial_math(
 input clk,rst_n,cancel,start,multiply,
 input [95:0] numerator,input [63:0] divisor,left,input [31:0] right,
 output reg busy,done,error,output reg [95:0] result
);
 reg operation;
 reg [1:0] phase;
 reg [6:0] iteration;
 reg [2:0] limb;
 reg [95:0] shift_value,accumulator;
 reg [63:0] denominator,remainder;
 reg [31:0] multiplier;
 reg [79:0] trial,difference;
 reg carry,borrow;
 wire [79:0] denominator_extended={16'd0,denominator};
 wire [16:0] limb_sum={1'b0,accumulator[limb*16+:16]}+
   {1'b0,shift_value[limb*16+:16]}+{16'd0,carry};
 wire [16:0] limb_difference={1'b0,trial[limb*16+:16]}-
   {1'b0,denominator_extended[limb*16+:16]}-{16'd0,borrow};
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   busy<=0;done<=0;error<=0;result<=0;operation<=0;phase<=0;iteration<=0;limb<=0;
   shift_value<=0;accumulator<=0;denominator<=0;remainder<=0;multiplier<=0;
   trial<=0;difference<=0;carry<=0;borrow<=0;
  end else begin
   done<=0;
   if(cancel)begin busy<=0;done<=0;error<=0;phase<=0;end
   else if(start && !busy)begin
    operation<=multiply;phase<=0;iteration<=0;limb<=0;
    accumulator<=0;remainder<=0;busy<=1;error<=0;carry<=0;borrow<=0;
    shift_value<=multiply ? {32'd0,left}:numerator;denominator<=divisor;multiplier<=right;
    if(!multiply && divisor==0)begin busy<=0;done<=1;error<=1;result<=0;end
   end else if(busy)begin
    if(operation)begin
     case(phase)
      0:if(multiplier[0])begin limb<=0;carry<=0;phase<=1;end
        else if(iteration==31)begin result<=accumulator;busy<=0;done<=1;end
        else begin shift_value<=shift_value<<1;multiplier<=multiplier>>1;iteration<=iteration+1'b1;end
      1:begin
       accumulator[limb*16+:16]<=limb_sum[15:0];carry<=limb_sum[16];
       if(limb==5)begin
        if(iteration==31)begin result<={limb_sum[15:0],accumulator[79:0]};busy<=0;done<=1;end
        else begin shift_value<=shift_value<<1;multiplier<=multiplier>>1;iteration<=iteration+1'b1;phase<=0;end
       end else limb<=limb+1'b1;
      end
      default:begin error<=1;busy<=0;done<=1;end
     endcase
    end else begin
     case(phase)
      0:begin trial<={15'd0,remainder,shift_value[95]};difference<=0;limb<=0;borrow<=0;phase<=1;end
      1:begin
       difference[limb*16+:16]<=limb_difference[15:0];borrow<=limb_difference[16];
       if(limb==4)phase<=2;else limb<=limb+1'b1;
      end
      2:begin
       remainder<=borrow ? trial[63:0]:difference[63:0];
       accumulator<={accumulator[94:0],!borrow};shift_value<=shift_value<<1;
       if(iteration==95)begin result<={accumulator[94:0],!borrow};busy<=0;done<=1;end
       else begin iteration<=iteration+1'b1;phase<=0;end
      end
      default:begin error<=1;busy<=0;done<=1;end
     endcase
    end
   end
  end
 end
endmodule

// Project-only unsigned48x32=>80 / unsigned80-by48 core.
module forest_motion_serial_math_project(
 input clk,rst_n,cancel,start,multiply,
 input [79:0] numerator,input [47:0] divisor,left,input [31:0] right,
 output reg busy,done,error,output wire [79:0] result
);
 reg operation;
 reg [1:0] phase;
 reg [6:0] iteration;
 reg [2:0] limb;
 reg [79:0] shift_value,accumulator;
 // The accumulator already holds the final product/quotient on done.
 // Mid-transaction result is deliberately unspecified and is never consumed.
 assign result=accumulator;
 reg [47:0] denominator,remainder;
 reg [31:0] multiplier;
 reg [63:0] trial,difference;
 reg carry,borrow;
 wire [63:0] denominator_extended={16'd0,denominator};
 wire [16:0] limb_sum={1'b0,accumulator[limb*16+:16]}+
   {1'b0,shift_value[limb*16+:16]}+{16'd0,carry};
 wire [16:0] limb_difference={1'b0,trial[limb*16+:16]}-
   {1'b0,denominator_extended[limb*16+:16]}-{16'd0,borrow};
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   busy<=0;done<=0;error<=0;operation<=0;phase<=0;iteration<=0;limb<=0;
   shift_value<=0;accumulator<=0;denominator<=0;remainder<=0;multiplier<=0;
   trial<=0;difference<=0;carry<=0;borrow<=0;
  end else begin
   done<=0;
   if(cancel)begin busy<=0;done<=0;error<=0;phase<=0;end
   else if(start && !busy)begin
    operation<=multiply;phase<=0;iteration<=0;limb<=0;
    accumulator<=0;remainder<=0;busy<=1;error<=0;carry<=0;borrow<=0;
    shift_value<=multiply ? {32'd0,left}:numerator;denominator<=divisor;multiplier<=right;
    if(!multiply && divisor==0)begin busy<=0;done<=1;error<=1;end
   end else if(busy)begin
    if(operation)begin
     case(phase)
      0:if(multiplier[0])begin limb<=0;carry<=0;phase<=1;end
        else if(iteration==31)begin busy<=0;done<=1;end
        else begin shift_value<=shift_value<<1;multiplier<=multiplier>>1;iteration<=iteration+1'b1;end
      1:begin
       accumulator[limb*16+:16]<=limb_sum[15:0];carry<=limb_sum[16];
       if(limb==4)begin
        if(iteration==31)begin busy<=0;done<=1;end
        else begin shift_value<=shift_value<<1;multiplier<=multiplier>>1;iteration<=iteration+1'b1;phase<=0;end
       end else limb<=limb+1'b1;
      end
      default:begin error<=1;busy<=0;done<=1;end
     endcase
    end else begin
     case(phase)
      0:begin trial<={15'd0,remainder,shift_value[79]};difference<=0;limb<=0;borrow<=0;phase<=1;end
      1:begin
       difference[limb*16+:16]<=limb_difference[15:0];borrow<=limb_difference[16];
       if(limb==3)phase<=2;else limb<=limb+1'b1;
      end
      2:begin
       remainder<=borrow ? trial[47:0]:difference[47:0];
       accumulator<={accumulator[78:0],!borrow};shift_value<=shift_value<<1;
       if(iteration==79)begin busy<=0;done<=1;end
       else begin iteration<=iteration+1'b1;phase<=0;end
      end
      default:begin error<=1;busy<=0;done<=1;end
     endcase
    end
   end
  end
 end
endmodule
