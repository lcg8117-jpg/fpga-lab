// Sequential upload while holding balance or disarmed. RAM is staged only.
module forest_draw_buffer(
 input clk,rst_n,tick_5ms,upload_permitted,abort,
 input cfg_write,input [7:0] cfg_id,input [31:0] cfg_value,
 output reg cfg_ok,cfg_error,output reg [31:0] cfg_revision,cfg_read_value,
 output reg committed,output reg [9:0] length,
 input [8:0] read_address,output reg signed [15:0] sample
);
 reg signed [15:0] memory[0:511] /* synthesis syn_ramstyle="block_ram" */;
 reg uploading;
 reg [9:0] count;
 reg [6:0] idle_ticks;
 reg [31:0] checksum;
 reg signed [15:0] previous_point;
 reg signed [16:0] previous_delta;
 reg [14:0] travel;
 reg signed [17:0] previous_second_delta;
 reg [2:0] zero_run;
 wire signed [15:0] point=cfg_value[15:0];
 wire signed [16:0] delta=$signed({point[15],point})-$signed({previous_point[15],previous_point});
 wire signed [17:0] second_delta=$signed({delta[16],delta})-$signed({previous_delta[16],previous_delta});
 wire signed [18:0] third_delta=$signed({second_delta[17],second_delta})-$signed({previous_second_delta[17],previous_second_delta});
 wire [16:0] step_size=delta[16] ? -delta:delta;
 wire [17:0] next_travel={3'd0,travel}+{1'b0,step_size};
 wire point_ok=cfg_value[31:25]==0 && uploading && count<length &&
  cfg_value[24:16]==count[8:0] && point>=-16'sd3072 && point<=16'sd3072 &&
  (count>=4 || point==0) && delta>=-17'sd96 && delta<=17'sd96 &&
  second_delta>=-18'sd32 && second_delta<=18'sd32 && third_delta>=-19'sd16 && third_delta<=19'sd16 && next_travel<=18'd16640;
 wire store=cfg_write && upload_permitted && !abort && cfg_id==1 && point_ok;
 // Memory has no reset/clear loop, preserving synchronous block-RAM inference.
 always @(posedge clk)begin
  sample<=memory[read_address];
  if(store)memory[count[8:0]]<=point;
 end
 always @*begin
  case(cfg_id)
   0:cfg_read_value={22'd0,length};
   1:cfg_read_value={22'd0,count};
   2:cfg_read_value=checksum;
   3:cfg_read_value={30'd0,uploading,committed};
   4:cfg_read_value=32'h44524334;
   5:cfg_read_value=32'd512;
   6:cfg_read_value={31'd0,upload_permitted};
   default:cfg_read_value=32'hffffffff;
  endcase
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   committed<=0;uploading<=0;length<=0;count<=0;idle_ticks<=0;
   checksum<=0;previous_point<=0;previous_delta<=0;previous_second_delta<=0;zero_run<=0;travel<=0;
   cfg_ok<=0;cfg_error<=0;cfg_revision<=0;
  end else begin
   cfg_ok<=0;cfg_error<=0;
   if(abort)begin committed<=0;uploading<=0;idle_ticks<=0;end
   else if(cfg_write)begin
    cfg_error<=1;
    // Unqualified writes cannot mutate the program. A quiet BALANCE upload
    // updates only this staging RAM and never changes the live reference.
    if(upload_permitted)begin
     committed<=0;uploading<=0;idle_ticks<=0;
     case(cfg_id)
      0:if(cfg_value>=8 && cfg_value<=512)begin
       length<=cfg_value[9:0];count<=0;uploading<=1;
       checksum<=32'h6a09e667^cfg_value;previous_point<=0;previous_delta<=0;previous_second_delta<=0;zero_run<=0;travel<=0;
       cfg_ok<=1;cfg_error<=0;cfg_revision<=cfg_revision+1'b1;
      end
      1:if(point_ok)begin
       count<=count+1'b1;uploading<=1;previous_point<=point;previous_delta<=delta;previous_second_delta<=second_delta;travel<=next_travel[14:0];
       if(point==0)begin if(zero_run<4)zero_run<=zero_run+1'b1;end else zero_run<=0;
       checksum<={checksum[30:0],checksum[31]}^cfg_value;
       cfg_ok<=1;cfg_error<=0;cfg_revision<=cfg_revision+1'b1;
      end
      2:if(uploading && count==length && previous_point==0 && previous_delta==0 && zero_run>=4 && cfg_value==checksum)begin
       committed<=1;cfg_ok<=1;cfg_error<=0;cfg_revision<=cfg_revision+1'b1;
      end
      3:if(cfg_value==0)begin cfg_ok<=1;cfg_error<=0;cfg_revision<=cfg_revision+1'b1;end
      default:;
     endcase
    end
   end else if(uploading && tick_5ms)begin
    if(idle_ticks>=99)begin uploading<=0;committed<=0;idle_ticks<=0;end
    else idle_ticks<=idle_ticks+1'b1;
   end
  end
 end
endmodule
