// Fastest monotone velocity step inside the latched existing planner A/J limits.
// Future jerk-release distance is summed with bounded serialized jerk-release sums. A small
// binary search finds the strongest feasible deceleration; no divider/DSP.
module forest_motion_brake_step(
 input clk,rst_n,start,cancel,
 input signed [31:0] velocity,acceleration,
 input [15:0] limit_a,limit_j,
 output reg busy,done,fault,
 output reg signed [31:0] next_velocity,next_acceleration
);
 reg signed [15:0] LIMIT_A,LIMIT_J;
 reg [3:0] state;
 reg negative;
 reg [31:0] speed,cost;
 reg signed [15:0] initial_a,lower,upper,candidate;
 reg [15:0] ramp;
 reg [1:0] search;
 reg [14:0] clocks;
 wire signed [32:0] after_step=$signed({1'b0,speed})+$signed({{17{candidate[15]}},candidate});
 wire feasible=after_step>=0 && $unsigned(after_step)>={1'b0,cost};
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   busy<=0;done<=0;fault<=0;state<=0;negative<=0;speed<=0;cost<=0;
   initial_a<=0;lower<=0;upper<=0;candidate<=0;ramp<=0;search<=0;clocks<=0;
   next_velocity<=0;next_acceleration<=0;LIMIT_A<=0;LIMIT_J<=0;
  end else if(cancel)begin busy<=0;done<=0;fault<=0;state<=0;clocks<=0;end
  else if(start)begin
   busy<=1;done<=0;fault<=0;state<=1;clocks<=0;
   LIMIT_A<=$signed(limit_a);LIMIT_J<=$signed(limit_j);
   negative<=velocity==0 ? acceleration[31]:velocity[31];
   speed<=velocity[31] ? -velocity:velocity;
   initial_a<=(velocity==0 ? acceleration[31]:velocity[31]) ? -acceleration[15:0]:acceleration[15:0];
   if(limit_a==0 || limit_j==0 || limit_a>16'd1966 || limit_j>16'd1966 ||
      acceleration>$signed({16'd0,limit_a}) || acceleration< -$signed({16'd0,limit_a}))begin
    busy<=0;done<=1;fault<=1;state<=0;
   end
  end else if(busy)begin
   clocks<=clocks+1'b1;
   if(clocks==20000)begin busy<=0;done<=1;fault<=1;state<=0;end
   else case(state)
    1:begin
     if(speed==0 && initial_a>=-LIMIT_J && initial_a<=LIMIT_J)begin
      next_velocity<=0;next_acceleration<=0;busy<=0;done<=1;state<=0;
     end else begin
      lower<=initial_a-LIMIT_J < -LIMIT_A ? -LIMIT_A:initial_a-LIMIT_J;
      upper<=initial_a+LIMIT_J > LIMIT_A ? LIMIT_A:initial_a+LIMIT_J;
      state<=2;
     end
    end
    2:begin candidate<=lower;search<=0;state<=3;end
    3:begin ramp<=candidate[15] ? -candidate:16'd0;cost<=0;state<=4;end
    4:begin
     if(ramp>LIMIT_J)begin ramp<=ramp-LIMIT_J;cost<=cost+ramp-LIMIT_J;end
     else state<=5;
    end
    5:begin
     if(search==0)begin
      if(feasible)state<=7;
      else begin candidate<=upper;search<=1;state<=3;end
     end else if(search==1)begin
      if(!feasible)begin busy<=0;done<=1;fault<=1;state<=0;end
      else if(upper-lower<=1)begin candidate<=upper;state<=7;end
      else begin
       lower<=lower+1'b1;candidate<=($signed(lower)+16'sd1+$signed(upper))>>>1;
       search<=2;state<=3;
      end
     end else if(feasible)begin
      upper<=candidate;
      if(candidate==lower)state<=7;
      else begin candidate<=($signed(lower)+$signed(candidate))>>>1;state<=3;end
     end else begin
      lower<=candidate+1'b1;
      if(candidate+1'b1==upper)begin candidate<=upper;state<=7;end
      else begin candidate<=($signed(candidate)+16'sd1+$signed(upper))>>>1;state<=3;end
     end
    end
    7:begin
     next_velocity<=negative ? -$signed(after_step[31:0]):$signed(after_step[31:0]);
     next_acceleration<=negative ? -$signed({{16{candidate[15]}},candidate}):$signed({{16{candidate[15]}},candidate});
     busy<=0;done<=1;state<=0;
    end
    default:begin busy<=0;done<=1;fault<=1;state<=0;end
   endcase
  end
 end
endmodule
