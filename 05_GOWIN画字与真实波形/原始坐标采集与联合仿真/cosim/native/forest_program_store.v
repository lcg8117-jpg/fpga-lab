// Immutable synchronous preset ROM and independent user staging RAM.
module forest_program_store(
 input clk,rst_n,tick_5ms,upload_permitted,abort,
 input cfg_write,input[7:0] cfg_id,input[31:0] cfg_value,
 output wire cfg_ok,cfg_error,output wire[31:0] cfg_revision,
 output reg[31:0] cfg_read_value,
 output wire committed,output wire[9:0] length,output wire tracking_mode,
 output wire[1:0] preset_bank_selected,
 input[8:0] read_address,output wire signed[15:0] sample
);
 reg[1:0] selected;
 // Decode on the original selection edge, outside the live fault path.
 reg custom_bank,first_builtin_bank;
 assign preset_bank_selected=selected;
 wire ram_ok,ram_error,ram_committed,ram_tracking;
 wire[9:0] ram_length;wire signed[15:0] ram_sample;
 wire[31:0] ram_read,ram_revision;
 reg select_ok,select_error;reg[31:0] select_revision;
 wire custom_field=cfg_id==0 || cfg_id==1 || cfg_id==2 || cfg_id==3 || cfg_id==7;
 wire custom_write=cfg_write && custom_field && selected==0;
 forest_draw_buffer custom_buffer(.clk(clk),.rst_n(rst_n),.tick_5ms(tick_5ms),
  .upload_permitted(upload_permitted),.abort(abort),.cfg_write(custom_write),.cfg_id(cfg_id),.cfg_value(cfg_value),
  .cfg_ok(ram_ok),.cfg_error(ram_error),.cfg_revision(ram_revision),.cfg_read_value(ram_read),
  .committed(ram_committed),.length(ram_length),.tracking_mode(ram_tracking),.read_address(read_address),.sample(ram_sample));
 reg scanning;reg[9:0] scan_address;reg scan_valid;
 reg[9:0] delayed_address;reg[31:0] crc;
 reg[1:0] integrity;
 wire[9:0] rom_address=scanning ? scan_address:{selected==2,read_address};
 wire signed[15:0] rom_sample;
 forest_preset_rom presets(.clk(clk),.address(rom_address),.sample(rom_sample));
 wire[31:0] scan_word={7'd0,delayed_address[8:0],rom_sample};
 wire[31:0] next_crc={crc[30:0],crc[31]}^scan_word;
 // Both banks are read through the same registered port before admission.
 // Metadata alone cannot qualify a corrupt initialization image.
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin scanning<=1;scan_address<=0;scan_valid<=0;delayed_address<=0;
   crc<=32'h6a09e667^32'd438^32'd1;integrity<=0;end
  else if(scanning)begin
   scan_valid<=1;delayed_address<=scan_address;
   if(scan_address<1023)scan_address<=scan_address+1'b1;
   if(scan_valid)begin
    if(!delayed_address[9] && delayed_address[8:0]<438)crc<=next_crc;
    if(delayed_address==437)integrity[0]<=next_crc==32'hde0719e9;
    if(delayed_address==511)crc<=32'h6a09e667^32'd508^32'd1;
    if(delayed_address[9] && delayed_address[8:0]<508)crc<=next_crc;
    if(delayed_address==1019)integrity[1]<=next_crc==32'hb4aec1b2;
    if(delayed_address==1023)scanning<=0;
   end
  end
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin selected<=0;custom_bank<=1;first_builtin_bank<=0;
   select_ok<=0;select_error<=0;select_revision<=0;end
  else begin
   select_ok<=0;select_error<=0;
   if(cfg_write && cfg_id==8)begin
    if(upload_permitted && !abort && !scanning && cfg_value<=2 &&
       (cfg_value==0 || (cfg_value==1 && integrity[0]) || (cfg_value==2 && integrity[1])))begin
     selected<=cfg_value[1:0];custom_bank<=cfg_value==0;first_builtin_bank<=cfg_value==1;
     select_ok<=1;select_revision<=select_revision+1'b1;
    end else select_error<=1;
   end else if(cfg_write && (selected!=0 || !custom_field))select_error<=1;
  end
 end
 assign cfg_ok=ram_ok || select_ok;
 assign cfg_error=ram_error || select_error;
 assign cfg_revision=ram_revision+select_revision;
 assign committed=(custom_bank && ram_committed) ||
  (!scanning && ((first_builtin_bank && integrity[0]) || (selected[1] && integrity[1])));
 assign length=selected==0 ? ram_length:selected==1 ? 10'd438:10'd508;
 assign tracking_mode=selected==0 ? ram_tracking:1'b1;
 assign sample=selected==0 ? ram_sample:rom_sample;
 always @*begin
  cfg_read_value=ram_read;
  if(selected!=0)case(cfg_id)
   0,1:cfg_read_value={22'd0,length};
   2:cfg_read_value=selected==1 ? 32'hde0719e9:32'hb4aec1b2;
   3:cfg_read_value={31'd0,committed};
   7:cfg_read_value=1;
   default:;
  endcase
  case(cfg_id)
   8:cfg_read_value={30'd0,selected};
   9:cfg_read_value={30'd0,integrity};
   10:cfg_read_value=32'h43364634;
   11:cfg_read_value=selected==2 ? 32'd2:32'd1;
   default:;
  endcase
 end
endmodule
