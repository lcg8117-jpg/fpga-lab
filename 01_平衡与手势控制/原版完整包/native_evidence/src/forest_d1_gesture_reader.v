// Atomic 0x43/0x44 pairs; no vendor800/1000ms blocking event delays.
// The219-entry table and100kHz STOP-before-read transport are preserved.
module forest_d1_gesture_reader #(
 parameter integer CLK_HZ=50000000,I2C_HZ=100000,
 parameter integer STARTUP_CYCLES=CLK_HZ*2,
 parameter integer WAKE_CYCLES=CLK_HZ/1000,
 parameter integer WRITE_GAP_CYCLES=1,
 parameter integer STABLE_CYCLES=CLK_HZ/1000,
 parameter integer POLL_CYCLES=CLK_HZ/1000,
 parameter integer RETRY_CYCLES=CLK_HZ/2,
 parameter integer I2C_TIMEOUT_CYCLES=CLK_HZ/100
)(
 input clk,input rst_n,input restart,input flush,input motion_inhibit,input [2:0] map_flags,
 inout scl,inout sda,
 output reg ready,output wire busy,
 output reg [7:0] error_code,output reg [15:0] error_count,
 output reg [7:0] id0,id1,init_index,raw43,raw44,
 output reg [31:0] poll_seq,output wire [31:0] event_seq,
 output wire event_valid,output wire [3:0] event_code,
 output wire [31:0] suppressed_count,output wire [7:0] decode_reason,output wire [7:0] diagnostic_state
);
 localparam [3:0] POWER_WAIT=0,BANK0=1,WAKE_WAIT=2,ID_READ=3,
  CHECK_ID=4,INIT_ROM=5,WRITE_GAP=6,RETURN_BANK0=7,
  STABLE=8,POLL_READ=9,POLL_WAIT=10,RETRY=11;
 reg [3:0] state;
 reg [31:0] delay_count;
 reg active,start_transfer,read_transfer,two_transfer;
 reg [7:0] address_transfer,write_transfer;
 reg pair_valid,decode_flush;
 wire transport_busy,transport_done;
 wire [7:0] transport_error,low_byte,high_byte,rom_addr,rom_data;
 wire [1:0] read_count;
 wire local_rst_n=rst_n && !restart;
 assign busy=!ready || transport_busy;
 dd1_init_rom rom(.index(init_index),.reg_addr(rom_addr),.reg_data(rom_data));
 dd1_i2c_byte #(.CLK_HZ(CLK_HZ),.I2C_HZ(I2C_HZ),
  .TIMEOUT_CYCLES(I2C_TIMEOUT_CYCLES)) transport(
  .clk(clk),.rst_n(local_rst_n),.start(start_transfer),
  .read_not_write(read_transfer),.read_two(two_transfer),
  .reg_addr(address_transfer),.write_data(write_transfer),
  .device_address(7'h73),.address_only(1'b0),.scl(scl),.sda(sda),
  .busy(transport_busy),.done(transport_done),.error_code(transport_error),
  .read_data(low_byte),.read_high(high_byte),.read_count(read_count));
 forest_d1_gesture_decode decoder(.clk(clk),.rst_n(local_rst_n),
  .flush(flush || decode_flush),.sample_valid(pair_valid),.motion_inhibit(motion_inhibit),
  .raw43(raw43),.raw44(raw44),.map_flags(map_flags),
  .event_valid(event_valid),.event_code(event_code),.event_seq(event_seq),
  .suppressed_count(suppressed_count),.decode_reason(decode_reason),.armed(),.diagnostic_state(diagnostic_state));
 always @(posedge clk or negedge local_rst_n)begin
  if(!local_rst_n)begin
   state<=POWER_WAIT;ready<=0;error_code<=0;error_count<=0;
   id0<=0;id1<=0;init_index<=0;raw43<=0;raw44<=0;poll_seq<=0;
   delay_count<=0;active<=0;start_transfer<=0;read_transfer<=0;
   two_transfer<=0;address_transfer<=0;write_transfer<=0;pair_valid<=0;decode_flush<=1;
  end else begin
   start_transfer<=0;pair_valid<=0;decode_flush<=0;
   if(transport_done && (transport_error!=0 ||
     (read_transfer && read_count!=(two_transfer ? 2 : 1))))begin
    ready<=0;state<=RETRY;active<=0;delay_count<=0;decode_flush<=1;
    error_code<=transport_error!=0 ? transport_error : 8'h08;
    if(error_count!=16'hffff)error_count<=error_count+1'b1;
   end else case(state)
    POWER_WAIT:if(delay_count>=STARTUP_CYCLES-1)begin delay_count<=0;state<=BANK0;end
     else delay_count<=delay_count+1'b1;
    BANK0,ID_READ,INIT_ROM,RETURN_BANK0,POLL_READ:begin
     if(!active)begin
      read_transfer<=state==ID_READ || state==POLL_READ;
      two_transfer<=state==ID_READ || state==POLL_READ;
      case(state)
       ID_READ:address_transfer<=0;
       INIT_ROM:address_transfer<=rom_addr;
       POLL_READ:address_transfer<=8'h43;
       default:address_transfer<=8'hef;
      endcase
      write_transfer<=state==INIT_ROM ? rom_data : 0;
      start_transfer<=1;active<=1;
     end else if(transport_done)begin
      active<=0;delay_count<=0;
      case(state)
       BANK0:state<=WAKE_WAIT;
       ID_READ:begin id0<=low_byte;id1<=high_byte;state<=CHECK_ID;end
       INIT_ROM:state<=WRITE_GAP;
       RETURN_BANK0:state<=STABLE;
       POLL_READ:begin
        raw43<=low_byte;raw44<=high_byte;poll_seq<=poll_seq+1'b1;
        pair_valid<=1;state<=POLL_WAIT;
       end
       default:begin ready<=0;decode_flush<=1;state<=RETRY;end
      endcase
     end
    end
    WAKE_WAIT:if(delay_count>=WAKE_CYCLES-1)begin delay_count<=0;state<=ID_READ;end
     else delay_count<=delay_count+1'b1;
    CHECK_ID:if(id0==8'h20 && id1==8'h76)begin init_index<=0;state<=INIT_ROM;end
     else begin
      ready<=0;error_code<=8'hff;state<=RETRY;delay_count<=0;decode_flush<=1;
      if(error_count!=16'hffff)error_count<=error_count+1'b1;
     end
    WRITE_GAP:if(delay_count>=WRITE_GAP_CYCLES-1)begin
     delay_count<=0;
     if(init_index==218)state<=RETURN_BANK0;
     else begin init_index<=init_index+1'b1;state<=INIT_ROM;end
    end else delay_count<=delay_count+1'b1;
    STABLE:if(delay_count>=STABLE_CYCLES-1)begin
     ready<=1;error_code<=0;delay_count<=0;state<=POLL_READ;
    end else delay_count<=delay_count+1'b1;
    POLL_WAIT:if(delay_count>=POLL_CYCLES-1)begin delay_count<=0;state<=POLL_READ;end
     else delay_count<=delay_count+1'b1;
    RETRY:if(delay_count>=RETRY_CYCLES-1)begin
     delay_count<=0;active<=0;state<=BANK0;init_index<=0;decode_flush<=1;
    end else delay_count<=delay_count+1'b1;
    default:begin
     ready<=0;state<=RETRY;active<=0;delay_count<=0;error_code<=7;decode_flush<=1;
    end
   endcase
  end
 end
endmodule
