// R20 two-degree-of-freedom reference path, generalized to full Q16 inputs.
// alpha=1/128 approximates the frozen P25/D610..620 zero at 25ms. This is
// command shaping only; no ADC, encoder, feedback or PID operation changes.
// Full-width subtraction/addition is digit-serial (16-bit registered carry).
module forest_motion_reference_shape(
 input clk,rst_n,tick,rebase,stationary,
 input signed [63:0] reset_target,nominal_target,
 input [15:0] reset_fraction,nominal_fraction,
 input signed [31:0] reset_velocity_q,reset_acceleration_q,
 output reg signed [63:0] target,
 output reg [15:0] fraction,
 output reg signed [31:0] velocity_q,acceleration_q,
 output reg sample_valid,output wire done,output reg fault
);
 reg [3:0] phase;
 reg [2:0] limb;
 reg [95:0] position_q,goal_q,difference_q,next_position_q;
 reg signed [32:0] error_q;
 reg signed [31:0] step_q,step_acceleration_q;
 reg carry,borrow,held_stationary,done_reg;
 wire [16:0] subtract_limb={1'b0,goal_q[limb*16+:16]}-
   {1'b0,position_q[limb*16+:16]}-{16'd0,borrow};
 wire [32:0] error_magnitude=error_q[32] ? -error_q:error_q;
 wire [33:0] rounded_magnitude=({1'b0,error_magnitude}+34'd64)>>7;
 wire signed [31:0] rounded_step=error_q[32] ?
   -$signed(rounded_magnitude[31:0]):$signed(rounded_magnitude[31:0]);
 wire signed [32:0] acceleration_extended=$signed({step_q[31],step_q})-
   $signed({velocity_q[31],velocity_q});
 wire [95:0] step_extended={{64{step_q[31]}},step_q};
 wire [16:0] add_limb={1'b0,position_q[limb*16+:16]}+
   {1'b0,step_extended[limb*16+:16]}+{16'd0,carry};
 // A just-published old goal cannot assert completion after a new nominal
 // sample arrives. Task-origin capture and reference state remain separate.
 assign done=done_reg && stationary && target==nominal_target &&
   fraction==nominal_fraction && velocity_q==0 && acceleration_q==0;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   target<=0;fraction<=0;velocity_q<=0;acceleration_q<=0;sample_valid<=0;fault<=0;
   phase<=0;limb<=0;position_q<=0;goal_q<=0;difference_q<=0;next_position_q<=0;
   error_q<=0;step_q<=0;step_acceleration_q<=0;carry<=0;borrow<=0;held_stationary<=0;done_reg<=0;
  end else begin
   sample_valid<=0;
   if(rebase)begin
    target<=reset_target;fraction<=reset_fraction;
    velocity_q<=reset_velocity_q;acceleration_q<=reset_acceleration_q;
    phase<=0;limb<=0;carry<=0;borrow<=0;fault<=0;done_reg<=0;
   end else if(!fault)begin
    if(tick && phase!=0)begin fault<=1;phase<=0;done_reg<=0;end
    else case(phase)
     0:if(tick)begin
      position_q<={{16{target[63]}},target,fraction};
      goal_q<={{16{nominal_target[63]}},nominal_target,nominal_fraction};
      held_stationary<=stationary;difference_q<=0;limb<=0;borrow<=0;done_reg<=0;phase<=1;
     end
     1:begin
      difference_q[limb*16+:16]<=subtract_limb[15:0];borrow<=subtract_limb[16];
      if(limb==5)phase<=2;else limb<=limb+1'b1;
     end
     2:begin
      // Full signed96 difference must fit symmetric +/-0xffffffff Q16.
      // No narrowing occurs before this test, including +/-2^63 origins.
      if(difference_q[95:32]!={64{difference_q[32]}} ||
         difference_q[32:0]==33'h100000000)begin fault<=1;phase<=0;end
      else begin error_q<=$signed(difference_q[32:0]);phase<=3;end
     end
     3:begin
      // Symmetric rounding removes the donor's negative-only floor bias.
      // Tail quantum completion consumes one Q16 LSB only when nearest
      // rounding would stall. No whole remainder is snapped or hidden.
      if(held_stationary && rounded_step==0 && error_q!=0)
       step_q<=error_q[32] ? -32'sd1:32'sd1;
      else step_q<=rounded_step;
      phase<=4;
     end
     4:begin
      if(acceleration_extended[32]!=acceleration_extended[31])begin fault<=1;phase<=0;end
      else begin step_acceleration_q<=acceleration_extended[31:0];next_position_q<=0;limb<=0;carry<=0;phase<=5;end
     end
     5:begin
      next_position_q[limb*16+:16]<=add_limb[15:0];carry<=add_limb[16];
      if(limb==5)phase<=6;else limb<=limb+1'b1;
     end
     6:begin
      if(next_position_q[95:79]!={17{next_position_q[79]}})begin fault<=1;done_reg<=0;end
      else begin
       target<=next_position_q[79:16];fraction<=next_position_q[15:0];
       velocity_q<=step_q;acceleration_q<=step_acceleration_q;sample_valid<=1;
       done_reg<=held_stationary && next_position_q==goal_q && step_q==0 && step_acceleration_q==0;
      end
      phase<=0;
     end
     default:begin phase<=0;fault<=1;done_reg<=0;end
    endcase
   end
  end
 end
endmodule
