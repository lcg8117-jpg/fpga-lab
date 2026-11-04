// Small sequential format converters. No long leading-zero/barrel-shift path.
module forest_d1_serial_format(
 input clk,rst_n,start,input signed [47:0] value,input [5:0] fractional,
 output reg done,output reg busy,output reg [31:0] result
);
 reg sign,guard,sticky;reg [47:0] magnitude;reg [24:0] significand;
 reg [7:0] exponent;reg [1:0] phase;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin done<=0;busy<=0;result<=0;sign<=0;guard<=0;sticky<=0;magnitude<=0;significand<=0;exponent<=0;phase<=0;end
  else begin
   done<=0;
   if(!busy)begin
    if(start)begin
     sign<=value[47];magnitude<=value[47] ? -value:value;
     guard<=0;sticky<=0;exponent<=8'd150-fractional;phase<=0;busy<=1;
    end
   end else case(phase)
    0:if(magnitude==0)begin result<=0;done<=1;busy<=0;end
      else if(|magnitude[47:24])begin sticky<=sticky||guard;guard<=magnitude[0];magnitude<=magnitude>>1;exponent<=exponent+1'b1;end
      else if(!magnitude[23])begin magnitude<=magnitude<<1;exponent<=exponent-1'b1;end
      else phase<=1;
    1:begin significand<={1'b0,magnitude[23:0]}+(guard && (sticky || magnitude[0]));phase<=2;end
    2:begin
     result<=significand[24] ? {sign,(exponent+8'd1),significand[23:1]}:{sign,exponent,significand[22:0]};
     done<=1;busy<=0;
    end
   endcase
  end
 end
endmodule

module forest_d1_serial_q(
 input clk,rst_n,start,input [31:0] value,input [5:0] fractional,
 output reg done,output reg busy,output reg signed [63:0] result
);
 reg sign;reg [63:0] magnitude;reg signed [9:0] shift;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin done<=0;busy<=0;result<=0;sign<=0;magnitude<=0;shift<=0;end
  else begin
   done<=0;
   if(!busy)begin
    if(start)begin
     sign<=value[31];magnitude<=value[30:23]==0 ? 0:{40'd0,1'b1,value[22:0]};
     shift<=$signed({2'd0,value[30:23]})-10'sd150+$signed({4'd0,fractional});busy<=1;
    end
   end else if(shift==0 || magnitude==0)begin result<=sign ? -$signed(magnitude):$signed(magnitude);done<=1;busy<=0;end
   else if(shift>0)begin magnitude<=magnitude<<1;shift<=shift-1'b1;end
   else begin magnitude<=magnitude>>1;shift<=shift+1'b1;end
  end
 end
endmodule
