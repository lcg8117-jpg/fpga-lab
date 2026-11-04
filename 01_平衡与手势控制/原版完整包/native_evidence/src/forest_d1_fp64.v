// Finite binary64 add/multiply, RNE. Serial 53-bit multiplication, no vendor IP.
// Binary32 operations widen exactly, operate here, then round at C assignment.
module forest_d1_fp64(
 input clk,input rst_n,input start,input multiply,
 input [63:0] a,input [63:0] b,
 output reg done,output reg [63:0] result
);
`include "forest_d1_fp_functions.vh"
 reg [3:0] state;
 reg sa,sb,sign_z;
 integer ea,eb,ez,diff,k,j;
 reg [55:0] ma,mb,aligned_a,aligned_b,norm;
 reg [56:0] sum;
 reg [53:0] rounded;
 reg [52:0] multiplier;
 reg [105:0] multiplicand,product;
 reg [5:0] count;
 reg [52:0] am,bm;
 reg [63:0] saved_a,saved_b;
 reg zero_z;
 reg saved_multiply;
 reg [5:0] shift_a,shift_b;
 integer normalized_exp,rounded_exp;
 always @(posedge clk or negedge rst_n) begin
  if(!rst_n) begin state<=0;done<=0;result<=0; end
  else begin
   done<=0;
   case(state)
    0: if(start) begin
     saved_a<=a; saved_b<=b; sa<=a[63];sb<=b[63];sign_z<=a[63]^b[63];
     ea<=a[62:52]; eb<=b[62:52];
     am<={a[62:52]!=0,a[51:0]}; bm<={b[62:52]!=0,b[51:0]};
     saved_multiply<=multiply;state<=10;
    end
    // Keep leading-index, shifting and exponent arithmetic in distinct stages.
    10: begin
     k=0;for(j=0;j<53;j=j+1) if(am[j]) k=j;
     shift_a<=(saved_a[62:52]==0&&am!=0) ? 52-k : 0;
     k=0;for(j=0;j<53;j=j+1) if(bm[j]) k=j;
     shift_b<=(saved_b[62:52]==0&&bm!=0) ? 52-k : 0;
     state<=11;
    end
    11: begin
     if(saved_a[62:52]==0) begin am<=am<<shift_a;ea<=1-shift_a;end
     if(saved_b[62:52]==0) begin bm<=bm<<shift_b;eb<=1-shift_b;end
     state<=12;
    end
    12: begin
     if(saved_multiply) begin
      ez<=ea+eb-1023; multiplier<=bm; multiplicand<={53'd0,am};
      product<=0;count<=0;state<=4;
     end else begin
      ma<={am,3'b0};mb<={bm,3'b0};state<=1;
     end
    end
    1: begin
     if(ea>=eb) begin aligned_a<=ma;aligned_b<=shift_jam56(mb,ea-eb);ez<=ea;end
     else begin aligned_a<=shift_jam56(ma,eb-ea);aligned_b<=mb;ez<=eb;end
     state<=2;
    end
    2: begin
     if(sa==sb) begin sum<={1'b0,aligned_a}+{1'b0,aligned_b};sign_z<=sa;end
     else if(aligned_a>=aligned_b) begin sum<={1'b0,aligned_a}-{1'b0,aligned_b};sign_z<=sa;end
     else begin sum<={1'b0,aligned_b}-{1'b0,aligned_a};sign_z<=sb;end
     state<=3;
    end
    // Separate normalize, underflow-align, round and pack registers. This
    // preserves all bits while meeting 50 MHz without timing exceptions.
    3: begin
     zero_z<=sum==0;
     if(sum==0) begin norm<=0;normalized_exp<=0;sign_z<=sa&sb;end
     else begin
      if(sum[56]) begin norm<={sum[56:2],sum[1]|sum[0]};normalized_exp<=ez+1;end
      else begin
       k=0;for(j=0;j<56;j=j+1) if(sum[j]) k=j;
       norm<=sum[55:0] << (55-k);normalized_exp<=ez-(55-k);
      end
     end
     state<=6;
    end
    4: begin
     if(multiplier[0]) product<=product+multiplicand;
     multiplier<=multiplier>>1;multiplicand<=multiplicand<<1;
     if(count==52) state<=5; else count<=count+1'b1;
    end
    5: begin
     zero_z<=saved_a[62:0]==0||saved_b[62:0]==0;
     if(product[105]) begin norm<={product[105:53],product[52],product[51],|product[50:0]};normalized_exp<=ez+1;end
     else begin norm<={product[104:52],product[51],product[50],|product[49:0]};normalized_exp<=ez;end
     state<=6;
    end
    6: begin
     if(normalized_exp<=0) begin norm<=shift_jam56(norm,1-normalized_exp);rounded_exp<=0;end
     else rounded_exp<=normalized_exp;
     state<=7;
    end
    7: begin
     rounded<={1'b0,norm[55:3]}+(norm[2]&&(norm[1]||norm[0]||norm[3]));
     state<=8;
    end
    8: begin
     if(zero_z) result<={sign_z,63'd0};
     else if(rounded[53]) begin result<={sign_z,11'd0,rounded[52:1]};result[62:52]<=rounded_exp+1;end
     else begin
      result<={sign_z,11'd0,rounded[51:0]};
      result[62:52]<=(rounded_exp==0&&rounded[52]) ? 1 : rounded_exp;
     end
     done<=1;state<=0;
    end
    default: state<=0;
   endcase
  end
 end
endmodule
