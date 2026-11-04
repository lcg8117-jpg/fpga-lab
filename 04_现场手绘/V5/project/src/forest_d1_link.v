// FD2 full-duplex transport. Producers pulse valid once per immutable snapshot.
// Byte-write rings preserve block-RAM inference and never overwrite TX slots.
module forest_d1_link #(
 parameter integer CLKS_PER_BIT=25,
 parameter integer INTERBYTE_CYCLES=2500000,EMERGENCY_WIRE_VERSION=4
)(
 input clk,input rst_n,input uart_rx,output uart_tx,
 output reg command_valid,output wire [383:0] command_data,
 output reg [1:0] command_emergency,
 input reply_valid,input [623:0] reply_data,output reply_ready,
 input tlm_valid,input [2047:0] tlm_data,input [8:0] tlm_length,output tlm_ready,
 output reg [31:0] rx_error_count,tlm_dropped,reply_dropped,
 output wire busy
);
 function [15:0] crc_byte;
  input [15:0] crc;input [7:0] value;reg [15:0] t;integer i;
  begin t=crc^{value,8'd0};for(i=0;i<8;i=i+1)t=t[15] ? (t<<1)^16'h1021 : t<<1;crc_byte=t;end
 endfunction
 wire [7:0] received_byte;wire received_valid,framing_error;
 forest_d1_window_uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) receiver(
  .clk(clk),.rst_n(rst_n),.serial_in(uart_rx),.data(received_byte),
  .valid(received_valid),.framing_error(framing_error));
 reg [383:0] receive_window;
 // CRC ownership finishes before valid. The next UART byte arrives only
 // after the consumer has latched this immutable valid-cycle snapshot.
 assign command_data=receive_window;
 reg [5:0] receive_count;reg [6:0] crc_index;
 reg [31:0] interbyte_count;
 reg pending_header,crc_active;
 reg [15:0] receive_crc;
 reg [1:0] pending_emergency;
 wire emergency_shape=receive_window[31:0]==32'h43324446 &&
  receive_window[39:32]==EMERGENCY_WIRE_VERSION &&
  receive_window[55:48]==0 && receive_window[63:56]==0 &&
  receive_window[287:96]==0 && receive_window[367:360]==0 &&
  (receive_window[47:40]==8'h10 || receive_window[47:40]==8'h14);
 wire [383:0] window_next={received_byte,receive_window[383:8]};
 wire [31:0] newest_header=window_next[383:352];
 wire candidate_ready=received_valid && receive_count>=47 && window_next[31:0]==32'h43324446;

 function [15:0] crc_nibble;
  input [15:0] crc;input [3:0] value;reg [15:0] t;integer bit_index;
  begin t=crc^{value,12'd0};for(bit_index=0;bit_index<4;bit_index=bit_index+1)t=t[15] ? (t<<1)^16'h1021:t<<1;crc_nibble=t;end
 endfunction
 wire [7:0] crc_input_byte=receive_window[crc_index[6:1]*8+:8];
 wire [3:0] crc_input=crc_index[0] ? crc_input_byte[3:0]:crc_input_byte[7:4];
 // synthesis translate_off
 initial if(CLKS_PER_BIT<10)$fatal(1,"RX CRC/window ownership requires divisor >=10");
 always @(posedge clk)if(rst_n && crc_active && received_valid)$fatal(1,"RX window changed during CRC ownership");
 // synthesis translate_on

 // Sliding windows recover after arbitrary junk/truncation, including headers
 // embedded in other payloads. Only a complete CRC-valid48-byte window emits.
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   receive_window<=0;
   receive_count<=0;crc_index<=0;interbyte_count<=0;pending_header<=0;
   crc_active<=0;receive_crc<=16'hffff;command_valid<=0;rx_error_count<=0;
   command_emergency<=0;pending_emergency<=0;
  end else begin
   command_valid<=0;command_emergency<=0;
   if(framing_error)begin
    receive_count<=0;receive_window<=0;pending_header<=0;crc_active<=0;interbyte_count<=0;
    if(rx_error_count!=32'hffffffff)rx_error_count<=rx_error_count+1'b1;
   end else begin
    if(received_valid)begin
     receive_window<=window_next;interbyte_count<=0;
     if(receive_count<48)receive_count<=receive_count+1'b1;
     if(newest_header==32'h43324446)pending_header<=1;
     if(candidate_ready)begin
      
      receive_crc<=16'hffff;crc_index<=0;crc_active<=1;
     end
    end else if(receive_count!=0)begin
     if(interbyte_count>=INTERBYTE_CYCLES-1)begin
      receive_count<=0;receive_window<=0;interbyte_count<=0;pending_header<=0;
      if(pending_header && rx_error_count!=32'hffffffff)rx_error_count<=rx_error_count+1'b1;
     end else interbyte_count<=interbyte_count+1'b1;
    end
    if(crc_active)begin
     // The immutable frame is available before its92-clock CRC scan ends.
     if(crc_index==0)pending_emergency<={emergency_shape && receive_window[47:40]==8'h14,emergency_shape};
     receive_crc<=crc_nibble(receive_crc,crc_input);
     if(crc_index==91)begin
      crc_active<=0;pending_header<=0;
      if(crc_nibble(receive_crc,crc_input)==receive_window[383:368])begin
       command_valid<=1;
       command_emergency<=pending_emergency;
      end else if(rx_error_count!=32'hffffffff)rx_error_count<=rx_error_count+1'b1;
     end else crc_index<=crc_index+1'b1;
    end
   end
  end
 end
 reg [7:0] telemetry_memory[0:2047] /* synthesis syn_ramstyle="block_ram" */;
 reg [7:0] reply_memory[0:639] /* synthesis syn_ramstyle="block_ram" */;
 reg [2:0] telemetry_write_slot,telemetry_read_slot,reply_write_slot,reply_read_slot;
 reg [3:0] telemetry_count,reply_count;
 reg telemetry_writing,reply_writing;
 reg [7:0] telemetry_write_index;
 reg [8:0] write_length;reg [8:0] packet_lengths[0:7];reg [8:0] transmit_length;
 reg [6:0] reply_write_index;
 // Producer holds tlm_data stable while telemetry_writing.
 reg [623:0] reply_owned;
 reg [7:0] reply_byte;
 reg [15:0] telemetry_crc,reply_crc;
 assign tlm_ready=!telemetry_writing && telemetry_count<8;
 assign reply_ready=!reply_writing && reply_count<8;
 wire telemetry_push=telemetry_writing && {1'b0,telemetry_write_index}==write_length-1;
 wire reply_push=reply_writing && reply_write_index==79;
 reg telemetry_pop,reply_pop;
 wire [10:0] telemetry_write_address={telemetry_write_slot,telemetry_write_index};
 wire [9:0] reply_write_base=({7'd0,reply_write_slot}<<6)+({7'd0,reply_write_slot}<<4);
 wire [9:0] reply_write_address=reply_write_base+{3'd0,reply_write_index};
 // RAMs have no bulk reset. Slots become visible only after their final byte.
 always @(posedge clk)begin
  if(telemetry_writing)begin
   if({1'b0,telemetry_write_index}<write_length-2)telemetry_memory[telemetry_write_address]<=tlm_data[telemetry_write_index*8+:8];
   else if({1'b0,telemetry_write_index}==write_length-2)telemetry_memory[telemetry_write_address]<=telemetry_crc[7:0];
   else telemetry_memory[telemetry_write_address]<=telemetry_crc[15:8];
  end
  if(reply_writing)begin
   if(reply_write_index<78)reply_memory[reply_write_address]<=reply_byte;
   else if(reply_write_index==78)reply_memory[reply_write_address]<=reply_crc[7:0];
   else reply_memory[reply_write_address]<=reply_crc[15:8];
  end
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   telemetry_write_slot<=0;telemetry_read_slot<=0;reply_write_slot<=0;reply_read_slot<=0;
   telemetry_count<=0;reply_count<=0;telemetry_writing<=0;reply_writing<=0;
   write_length<=256;telemetry_write_index<=0;reply_write_index<=0;reply_owned<=0;reply_byte<=0;
   telemetry_crc<=16'hffff;reply_crc<=16'hffff;tlm_dropped<=0;reply_dropped<=0;
  end else begin
   if(tlm_valid)begin
    if(tlm_ready)begin
     write_length<=tlm_length;packet_lengths[telemetry_write_slot]<=tlm_length;telemetry_write_index<=0;telemetry_writing<=1;telemetry_crc<=16'hffff;
    end else if(tlm_dropped!=32'hffffffff)tlm_dropped<=tlm_dropped+1'b1;
   end
   if(reply_valid)begin
    if(reply_ready)begin reply_owned<=reply_data;reply_byte<=reply_data[7:0];reply_write_index<=0;reply_writing<=1;reply_crc<=16'hffff;end
    else if(reply_dropped!=32'hffffffff)reply_dropped<=reply_dropped+1'b1;
   end
   if(telemetry_writing)begin
    if({1'b0,telemetry_write_index}<write_length-2)begin telemetry_crc<=crc_byte(telemetry_crc,tlm_data[telemetry_write_index*8+:8]);end
    if(telemetry_push)begin telemetry_writing<=0;telemetry_write_slot<=telemetry_write_slot+1'b1;end
    else telemetry_write_index<=telemetry_write_index+1'b1;
   end
   if(reply_writing)begin
    if(reply_write_index<78)reply_crc<=crc_byte(reply_crc,reply_byte);
    // Prefetch within the existing write cadence; input ownership remains
    // local even when the producer starts preparing its next response.
    if(reply_write_index<77)reply_byte<=reply_owned[(reply_write_index+1'b1)*8+:8];
    if(reply_push)begin reply_writing<=0;reply_write_slot<=reply_write_slot+1'b1;end
    else reply_write_index<=reply_write_index+1'b1;
   end
   if(telemetry_pop)telemetry_read_slot<=telemetry_read_slot+1'b1;
   if(reply_pop)reply_read_slot<=reply_read_slot+1'b1;
   case({telemetry_push,telemetry_pop})2'b10:telemetry_count<=telemetry_count+1'b1;
    2'b01:telemetry_count<=telemetry_count-1'b1;default:;endcase
   case({reply_push,reply_pop})2'b10:reply_count<=reply_count+1'b1;
    2'b01:reply_count<=reply_count-1'b1;default:;endcase
  end
 end
 localparam [2:0] TX_IDLE=0,TX_PREFETCH=1,TX_READY=2,TX_WAIT=3,TX_RELEASE=4;
 reg [2:0] tx_state;
 reg tx_reply;
 reg [7:0] transmit_index;
 reg [3:0] ack_burst;
 reg [10:0] telemetry_read_address;
 reg [9:0] reply_read_address;
 reg [7:0] telemetry_read_byte,reply_read_byte;
 reg uart_launch;reg [7:0] uart_data;wire uart_busy;
 wire [9:0] reply_read_base=({7'd0,reply_read_slot}<<6)+({7'd0,reply_read_slot}<<4);
 always @(posedge clk)begin
  telemetry_read_byte<=telemetry_memory[telemetry_read_address];
  reply_read_byte<=reply_memory[reply_read_address];
 end
 forest_d1_window_uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) transmitter(
  .clk(clk),.rst_n(rst_n),.data(uart_data),.launch(uart_launch),.serial_out(uart_tx),.busy(uart_busy));
 assign busy=tx_state!=TX_IDLE || uart_busy || telemetry_writing || reply_writing ||
  telemetry_count!=0 || reply_count!=0;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   transmit_length<=256;tx_state<=TX_IDLE;tx_reply<=0;transmit_index<=0;ack_burst<=0;
   telemetry_read_address<=0;reply_read_address<=0;
   uart_launch<=0;uart_data<=0;telemetry_pop<=0;reply_pop<=0;
  end else begin
   uart_launch<=0;telemetry_pop<=0;reply_pop<=0;
   case(tx_state)
    TX_IDLE:if(reply_count!=0 && (ack_burst<8 || telemetry_count==0))begin
     tx_reply<=1;transmit_index<=0;reply_read_address<=reply_read_base;tx_state<=TX_PREFETCH;
     if(ack_burst<8)ack_burst<=ack_burst+1'b1;
    end else if(telemetry_count!=0)begin
     transmit_length<=packet_lengths[telemetry_read_slot];tx_reply<=0;transmit_index<=0;telemetry_read_address<={telemetry_read_slot,8'd0};
     tx_state<=TX_PREFETCH;ack_burst<=0;
    end
    TX_PREFETCH:tx_state<=TX_READY;
    TX_READY:if(!uart_busy && !uart_launch)begin
     uart_data<=tx_reply ? reply_read_byte : telemetry_read_byte;uart_launch<=1;tx_state<=TX_WAIT;
    end
    TX_WAIT:if(!uart_busy && !uart_launch)begin
     if((tx_reply && transmit_index==79) || (!tx_reply && {1'b0,transmit_index}==transmit_length-1))begin
      if(tx_reply)reply_pop<=1;else telemetry_pop<=1;
      tx_state<=TX_RELEASE;
     end else begin
      transmit_index<=transmit_index+1'b1;
      if(tx_reply)reply_read_address<=reply_read_address+1'b1;
      else telemetry_read_address<=telemetry_read_address+1'b1;
      tx_state<=TX_PREFETCH;
     end
    end
    TX_RELEASE:tx_state<=TX_IDLE;
    default:tx_state<=TX_IDLE;
   endcase
  end
 end
endmodule

module forest_d1_window_uart_rx #(parameter integer CLKS_PER_BIT=25)(
    input wire clk,input wire rst_n,input wire serial_in,
    output reg [7:0] data,output reg valid,output reg framing_error
);
    reg [1:0] synchronizer;
    reg [1:0] state;
    reg [7:0] timer;
    reg [2:0] bit_index;
    reg [7:0] shift;
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            synchronizer<=3;state<=0;timer<=0;bit_index<=0;shift<=0;
            data<=0;valid<=0;framing_error<=0;
        end else begin
            synchronizer<={synchronizer[0],serial_in};valid<=0;framing_error<=0;
            case(state)
                0:if(!synchronizer[1]) begin timer<=CLKS_PER_BIT/2-1;state<=1;end
                1:if(timer!=0) timer<=timer-1'b1;
                  else if(synchronizer[1]) state<=0;
                  else begin timer<=CLKS_PER_BIT-1;bit_index<=0;state<=2;end
                2:if(timer!=0) timer<=timer-1'b1;
                  else begin
                      shift[bit_index]<=synchronizer[1];timer<=CLKS_PER_BIT-1;
                      if(bit_index==7) state<=3;else bit_index<=bit_index+1'b1;
                  end
                3:if(timer!=0) timer<=timer-1'b1;
                  else begin
                      if(synchronizer[1]) begin data<=shift;valid<=1;end
                      else framing_error<=1;
                      state<=0;
                  end
                default:state<=0;
            endcase
        end
    end
endmodule

module forest_d1_window_uart_tx #(parameter integer CLKS_PER_BIT=25)(
    input wire clk,input wire rst_n,input wire [7:0] data,input wire launch,
    output reg serial_out,output reg busy
);
    reg [7:0] timer;
    reg [3:0] bit_index;
    reg [7:0] shift;
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin serial_out<=1;busy<=0;timer<=0;bit_index<=0;shift<=0;end
        else if(!busy) begin
            serial_out<=1;
            if(launch) begin
                serial_out<=0;busy<=1;timer<=CLKS_PER_BIT-1;bit_index<=0;shift<=data;
            end
        end else if(timer!=0) timer<=timer-1'b1;
        else begin
            timer<=CLKS_PER_BIT-1;
            if(bit_index<8) begin serial_out<=shift[0];shift<=shift>>1;bit_index<=bit_index+1'b1;end
            else if(bit_index==8) begin serial_out<=1;bit_index<=9;end
            else begin busy<=0;serial_out<=1;end
        end
    end
endmodule
