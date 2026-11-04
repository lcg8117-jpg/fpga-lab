// Two-degree-of-freedom command path; the measured feedback/PID is untouched.
// Forest position Kp=25,Kd=620 at25ms has a setpoint derivative zero with
// time constant620/25*25ms=620ms. At5ms, alpha1/128 gives635ms and removes
// the large command-derivative lead. This is NOT an ADC/encoder filter.
// Integer output and fractional state always approach the commanded goal
// monotonically for a monotone input. No return-to-old-target stage exists.
module forest_motion_reference_shape(
 input clk,rst_n,tick,rebase,stationary,
 input signed [63:0] reset_target,nominal_target,
 output reg signed [63:0] target,
 output reg signed [31:0] velocity_q,acceleration_q,
 output reg fault
);
 reg [2:0] phase;
 reg [15:0] fraction;
 reg signed [64:0] difference;
 reg signed [31:0] step_q;
 reg signed [63:0] held_goal;
 reg snap;
 wire signed [32:0] error_q=
   ($signed({difference[31],difference[31:0]})<<<16)-$signed({17'd0,fraction});
 wire signed [33:0] sum_q=$signed({18'd0,fraction})+$signed({{2{step_q[31]}},step_q});
 wire signed [64:0] next_target=$signed({target[63],target})+
   $signed({{47{sum_q[33]}},sum_q[33:16]});
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin target<=0;fraction<=0;phase<=0;difference<=0;step_q<=0;
   velocity_q<=0;acceleration_q<=0;fault<=0;held_goal<=0;snap<=0;end
  else if(rebase)begin target<=reset_target;fraction<=0;phase<=0;
   velocity_q<=0;acceleration_q<=0;fault<=0;end
  else if(!fault)begin
   case(phase)
    0:if(tick)begin difference<=$signed({nominal_target[63],nominal_target})-
       $signed({target[63],target});held_goal<=nominal_target;phase<=1;end
    1:begin
     if(difference < -65'sd16384 || difference >65'sd16384)begin fault<=1;phase<=0;end
     else begin
      snap<=stationary && error_q>=-33'sd65536 && error_q<=33'sd65536;
      step_q<=error_q>>>7;phase<=2;
     end
    end
    2:begin
     if(next_target[64]!=next_target[63])fault<=1;
     else begin
      target<=snap ? held_goal:next_target[63:0];fraction<=snap ? 16'd0:sum_q[15:0];
      velocity_q<=snap ? 32'sd0:step_q;
      acceleration_q<=(snap ? 32'sd0:step_q)-velocity_q;
     end
     phase<=0;
    end
    default:begin phase<=0;fault<=1;end
   endcase
  end
 end
endmodule
