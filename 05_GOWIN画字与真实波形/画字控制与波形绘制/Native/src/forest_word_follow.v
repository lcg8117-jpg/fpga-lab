// Optional model-based INPUT lead compensation for the explicit slow word mode.
// Original PID gains and motor ownership remain unchanged. This is not a
// physical plant identification. Coefficients are tied to the supplied model.
module forest_word_follow(
 input clk,rst_n,start,abort,tick_5ms,enabled,valid,
 input signed [31:0] velocity_q16,acceleration_q16,
 output reg signed [15:0] correction,
 output reg fault
);
 reg [2:0] phase;
 reg signed [27:0] velocity_product;
 reg signed [29:0] acceleration_product;
 reg signed [31:0] request_q16,filtered_q16;
 wire signed [32:0] difference=$signed({request_q16[31],request_q16})-
  $signed({filtered_q16[31],filtered_q16});
 wire signed [32:0] next_filtered=$signed({filtered_q16[31],filtered_q16})+(difference>>>4);
 wire signed [32:0] magnitude=next_filtered[32] ? -next_filtered:next_filtered;
 wire signed [32:0] rounded=(magnitude+33'sd32768)>>>16;
 // 5.35 s velocity lead and 3.3238125 s^2 acceleration lead; a 1/16
 // input filter has75ms DC group delay. All products have bounded inputs.
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin phase<=0;velocity_product<=0;acceleration_product<=0;
   request_q16<=0;filtered_q16<=0;correction<=0;fault<=0;end
  else if(abort || start)begin phase<=0;request_q16<=0;filtered_q16<=0;correction<=0;fault<=0;end
  else begin
   if(valid && enabled && phase==0 && !fault)begin
    if(velocity_q16>19663 || velocity_q16< -19663 ||
       acceleration_q16>524 || acceleration_q16< -524)fault<=1;
    else begin
     velocity_product<=$signed(velocity_q16[15:0])*12'sd1070;
     acceleration_product<=$signed(acceleration_q16[10:0])*19'sd132953;
     phase<=1;
    end
   end else if(tick_5ms && !enabled && phase==0)begin request_q16<=0;phase<=2;end
   case(phase)
    1:begin request_q16<=$signed({{4{velocity_product[27]}},velocity_product})+
      $signed({{2{acceleration_product[29]}},acceleration_product});phase<=2;end
    2:begin
     if(next_filtered>33'sd16777216 || next_filtered< -33'sd16777216)begin fault<=1;phase<=0;end
     else begin
      filtered_q16<=next_filtered[31:0];
      correction<=next_filtered[32] ? -$signed(rounded[15:0]):$signed(rounded[15:0]);phase<=0;
     end
    end
    default:;
   endcase
  end
 end
endmodule
