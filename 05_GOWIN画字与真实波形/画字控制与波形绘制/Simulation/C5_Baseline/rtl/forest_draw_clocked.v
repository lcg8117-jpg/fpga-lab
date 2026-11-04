// C4 uploaded knots: signed Q4 counts,100ms per segment,20 reference ticks.
// Uniform cubic B-spline by forward differences. A narrow serial CONSTANT
// divider replaces a wide combinational divider. This module never owns PWM.
module forest_draw_clocked(
 input clk,rst_n,start,abort,tick_5ms,committed,
 input [9:0] length,input signed [15:0] sample,
 input [15:0] acceleration_limit_q,jerk_limit_q,
 output reg [8:0] read_address,
 output reg active,valid,done,fault,
 output reg signed [31:0] position_q16,velocity_q16,acceleration_q16
);
 localparam IDLE=0,WAIT_RAM=1,READ_RAM=2,COEFF=3,SCALE=4,DIFFERENCE=5,
  WAIT_TICK=6,DIVIDE=7,PUBLISH=8;
 reg [3:0] state;
 reg [2:0] slot;
 reg [8:0] segment;
 reg [4:0] substep;
 reg [5:0] bit_count;
 reg signed [31:0] p0,p1,p2,p3;
 reg signed [31:0] a,b,c,d,c1,c2;
 reg signed [31:0] numerator,fd1,fd2,fd3;
 reg [47:0] dividend,quotient;
 reg [20:0] remainder;
 reg negative;
 wire signed [31:0] next_numerator=numerator+fd1;
 wire [31:0] magnitude=next_numerator[31] ? -next_numerator:next_numerator;
 wire [20:0] trial={remainder[19:0],dividend[47]};
 wire subtract=trial>=21'd768000;
 wire [20:0] next_remainder=subtract ? trial-21'd768000:trial;
 wire [47:0] next_quotient={quotient[46:0],subtract};
 wire signed [31:0] rounded_position=negative ? -$signed(quotient[31:0]):$signed(quotient[31:0]);
 wire signed [32:0] velocity_ext=$signed({rounded_position[31],rounded_position})-$signed({position_q16[31],position_q16});
 wire signed [32:0] acceleration_ext=velocity_ext-$signed({velocity_q16[31],velocity_q16});
 wire signed [32:0] jerk_ext=acceleration_ext-$signed({acceleration_q16[31],acceleration_q16});
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=IDLE;slot<=0;segment<=0;substep<=0;bit_count<=0;read_address<=0;
   p0<=0;p1<=0;p2<=0;p3<=0;a<=0;b<=0;c<=0;d<=0;c1<=0;c2<=0;
   numerator<=0;fd1<=0;fd2<=0;fd3<=0;dividend<=0;quotient<=0;remainder<=0;negative<=0;
   active<=0;valid<=0;done<=0;fault<=0;position_q16<=0;velocity_q16<=0;acceleration_q16<=0;
  end else begin
   valid<=0;done<=0;
   if(abort)begin state<=IDLE;active<=0;fault<=0;position_q16<=0;velocity_q16<=0;acceleration_q16<=0;end
   else if(start)begin
    if(!committed || length<8 || length>512)begin fault<=1;active<=0;state<=IDLE;end
    else begin
     state<=WAIT_RAM;read_address<=0;slot<=0;segment<=0;substep<=0;
     position_q16<=0;velocity_q16<=0;acceleration_q16<=0;fault<=0;active<=1;
    end
   end else if(active && (!committed || (tick_5ms && state!=WAIT_TICK)))begin
    fault<=1;active<=0;state<=IDLE;
   end else case(state)
    WAIT_RAM:state<=READ_RAM;
    READ_RAM:begin
     case(slot)0:p0<={{16{sample[15]}},sample};1:p1<={{16{sample[15]}},sample};
      2:p2<={{16{sample[15]}},sample};default:p3<={{16{sample[15]}},sample};endcase
     if(slot==3)state<=COEFF;
     else begin slot<=slot+1'b1;read_address<=read_address+1'b1;state<=WAIT_RAM;end
    end
    COEFF:begin
     a<=p0+(p1<<<2)+p2;
     b<=((p2-p0)<<<1)+(p2-p0);
     c<=(p0-(p1<<<1)+p2)+((p0-(p1<<<1)+p2)<<<1);
     d<=-p0+(p1<<<1)+p1-(p2<<<1)-p2+p3;
     state<=SCALE;
    end
    SCALE:begin
     // denominator=6*20^3*16=768000 counts; all products are constants.
     numerator<=(a<<<13)-(a<<<8)+(a<<<6);
     c1<=(b<<<9)-(b<<<7)+(b<<<4);
     c2<=(c<<<4)+(c<<<2);fd3<=(d<<<2)+(d<<<1);state<=DIFFERENCE;
    end
    DIFFERENCE:begin fd1<=c1+c2+d;fd2<=(c2<<<1)+fd3;state<=WAIT_TICK;end
    WAIT_TICK:if(tick_5ms)begin
     numerator<=next_numerator;fd1<=fd1+fd2;fd2<=fd2+fd3;
     negative<=next_numerator[31];dividend<={magnitude,16'd0}+48'd384000;
     quotient<=0;remainder<=0;bit_count<=47;state<=DIVIDE;
    end
    DIVIDE:begin
     dividend<={dividend[46:0],1'b0};quotient<=next_quotient;remainder<=next_remainder;
     if(bit_count==0)state<=PUBLISH;else bit_count<=bit_count-1'b1;
    end
    PUBLISH:begin
     // Limits include the few Q16 rounding units, not additional motion range.
     if(quotient[47:32]!=0 || rounded_position>32'sd12582912 || rounded_position< -32'sd12582912 ||
        velocity_ext>33'sd19663 || velocity_ext< -33'sd19663 ||
        acceleration_ext>$signed({17'd0,acceleration_limit_q}) || acceleration_ext< -$signed({17'd0,acceleration_limit_q}) ||
        jerk_ext>$signed({17'd0,jerk_limit_q}) || jerk_ext< -$signed({17'd0,jerk_limit_q}))begin
      fault<=1;active<=0;state<=IDLE;
     end else begin
      position_q16<=rounded_position;velocity_q16<=velocity_ext[31:0];acceleration_q16<=acceleration_ext[31:0];valid<=1;
      if(substep==19)begin
       substep<=0;
       if({1'b0,segment}+10'd4>=length)begin active<=0;done<=1;state<=IDLE;end
       else begin
        p0<=p1;p1<=p2;p2<=p3;slot<=3;read_address<=segment+9'd4;segment<=segment+1'b1;state<=WAIT_RAM;
       end
      end else begin substep<=substep+1'b1;state<=WAIT_TICK;end
     end
    end
    default:;
   endcase
  end
 end
endmodule
