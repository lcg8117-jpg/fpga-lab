// Stateless optical classification plus release-based event ownership.
// Wave preempts normal arming and never waits for a gesture quit delay.
module forest_d1_gesture_decode(
 input clk,input rst_n,input flush,input sample_valid,
 input [7:0] raw43,raw44,input [2:0] map_flags,
 output reg event_valid,output reg [3:0] event_code,
 output reg [31:0] event_seq,suppressed_count,
 output reg [7:0] decode_reason,
 output wire [7:0] diagnostic_state,output reg armed
);
 reg [1:0] zero_count;
 reg normal_held,wave_held;
 assign diagnostic_state={3'd0,wave_held,zero_count,normal_held,armed};
 reg [3:0] decoded,mapped;
 always @* begin
  case(raw43)
   8'h01:decoded=1;8'h02:decoded=2;8'h04:decoded=3;8'h08:decoded=4;
   8'h10:decoded=5;8'h20:decoded=6;8'h40:decoded=7;8'h80:decoded=8;
   default:decoded=0;
  endcase
  mapped=decoded;
  if(map_flags[0] && (decoded==1 || decoded==2))mapped=decoded==1 ? 2 : 1;
  if(map_flags[1] && (decoded==7 || decoded==8))mapped=decoded==7 ? 8 : 7;
  if(map_flags[2] && (decoded==5 || decoded==6))mapped=decoded==5 ? 6 : 5;
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   event_valid<=0;event_code<=0;event_seq<=0;suppressed_count<=0;
   decode_reason<=1;zero_count<=0;normal_held<=0;wave_held<=0;armed<=0;
  end else begin
   event_valid<=0;
   if(flush)begin armed<=0;normal_held<=0;zero_count<=0;decode_reason<=1;end
   if(sample_valid)begin
    // Even a mixed or otherwise invalid frame may request the safe STOP.
    if(raw44[0])begin
     zero_count<=0;normal_held<=1;armed<=0;
     if(!wave_held)begin
      event_valid<=1;event_code<=9;event_seq<=event_seq+1'b1;decode_reason<=0;
     end else begin
      if(suppressed_count!=32'hffffffff)suppressed_count<=suppressed_count+1'b1;
      decode_reason<=2;
     end
     wave_held<=1;
    end else if(raw43==0 && raw44==0)begin
     if(!flush)begin
      if(zero_count==1)begin
       zero_count<=2;normal_held<=0;wave_held<=0;armed<=1;decode_reason<=0;
      end else if(zero_count==0)zero_count<=1;
     end
    end else begin
     zero_count<=0;
     if(raw44!==8'd0 || decoded==0)begin
      // No guessed action for combination or reserved bits.
      decode_reason<=3;
      if(suppressed_count!=32'hffffffff)suppressed_count<=suppressed_count+1'b1;
      normal_held<=1;armed<=0;
     end else if(!flush && armed && !normal_held)begin
      event_valid<=1;event_code<=mapped;event_seq<=event_seq+1'b1;
      normal_held<=1;armed<=0;decode_reason<=0;
     end else begin
      decode_reason<=2;
      if(suppressed_count!=32'hffffffff)suppressed_count<=suppressed_count+1'b1;
     end
    end
   end
  end
 end
endmodule
