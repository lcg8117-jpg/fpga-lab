// Signed40 x unsigned32, exact72-bit product,32 clocks. One carry chain.
module forest_d1_serial_mul(
 input clk,rst_n,start,input signed [39:0] a,input [31:0] b,
 output reg done,output reg busy,output reg signed [71:0] result
);
 reg signed [71:0] multiplicand,total;
 reg [31:0] multiplier;reg [5:0] count;
 wire signed [71:0] next_total=multiplier[0] ? total+multiplicand:total;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin done<=0;busy<=0;result<=0;multiplicand<=0;total<=0;multiplier<=0;count<=0;end
  else begin
   done<=0;
   if(!busy)begin
    if(start)begin multiplicand<={{32{a[39]}},a};multiplier<=b;total<=0;count<=0;busy<=1;end
   end else begin
    total<=next_total;multiplicand<=multiplicand<<<1;multiplier<=multiplier>>1;
    if(count==31)begin result<=next_total;done<=1;busy<=0;end
    else count<=count+1'b1;
   end
  end
 end
endmodule
