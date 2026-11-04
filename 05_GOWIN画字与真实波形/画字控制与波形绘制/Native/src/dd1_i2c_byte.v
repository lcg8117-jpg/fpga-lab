// M5 U127 register transport: STOP-before-read, one/two byte receive.
// PixArt v1.5 Figure13 and supplied paj7620ReadReg use this two-part framing.
module dd1_i2c_byte #(
    parameter integer CLK_HZ=50000000,
    parameter integer I2C_HZ=100000,
    parameter integer QUARTER_CYCLES=CLK_HZ/(I2C_HZ*4),
    parameter integer TIMEOUT_CYCLES=CLK_HZ/100
)(
    input wire clk, input wire rst_n,
    input wire start, input wire read_not_write, input wire read_two,
    input wire [7:0] reg_addr, input wire [7:0] write_data,
    input wire [6:0] device_address, input wire address_only,
    inout wire scl, inout wire sda,
    output reg busy, output reg done,
    output reg [7:0] error_code, output reg [7:0] read_data,read_high,
    output reg [1:0] read_count
);
    localparam [4:0] IDLE=0, START_A=1, START_B=2, TX_LOW=3,
        TX_HIGH=4, TX_HOLD=5, ACK_LOW=6, ACK_HIGH=7, ACK_HOLD=8,
        STOP_LOW=9, STOP_HIGH=10, STOP_RELEASE=11, STOP_HOLD=12,
        READ_START_A=13, RX_LOW=15, RX_HIGH=16,
        RX_HOLD=17, NACK_LOW=18, NACK_HIGH=19, NACK_HOLD=20,
        TX_FALL=21, ACK_FALL=22, RX_FALL=23, NACK_FALL=24,
        START_HOLD=25, STOP_SETUP=26;
    reg [4:0] state;
    reg scl_low, sda_low;
    reg [31:0] quarter, timeout_count;
    reg [7:0] tx_byte, addr_latched, data_latched;
    reg [2:0] bit_index;
    reg [1:0] byte_index;
    reg read_latched, read_phase, address_only_latched;
    reg pending_read_start;
    reg two_latched,read_second;
    reg [6:0] device_latched;
    reg scl_meta, scl_sync, sda_meta, sda_sync;
    wire tick = (quarter == QUARTER_CYCLES-1);
    assign scl = scl_low ? 1'b0 : 1'bz;
    assign sda = sda_low ? 1'b0 : 1'bz;

    // External open-drain lines cross into clk; sample only the second stage.
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            scl_meta<=1; scl_sync<=1; sda_meta<=1; sda_sync<=1;
        end else begin
            scl_meta<=scl; scl_sync<=scl_meta;
            sda_meta<=sda; sda_sync<=sda_meta;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state<=IDLE; scl_low<=0; sda_low<=0; quarter<=0;
            timeout_count<=0; tx_byte<=0; addr_latched<=0;
            data_latched<=0; bit_index<=7; byte_index<=0;
            read_latched<=0; read_phase<=0;
            address_only_latched<=0; device_latched<=0;
            pending_read_start<=0;two_latched<=0;read_second<=0;read_high<=0;read_count<=0;
            busy<=0; done<=0; error_code<=0; read_data<=0;
        end else begin
            done<=0;
            if (state==IDLE) begin
                quarter<=0; timeout_count<=0; scl_low<=0; sda_low<=0; pending_read_start<=0;
                if (start) begin
                    busy<=1; error_code<=0; read_phase<=0;
                    two_latched<=read_two;read_second<=0;read_count<=0;read_data<=0;read_high<=0;
                    // A probe always sends W, even if the caller changes other modes.
                    read_latched<=read_not_write && !address_only;
                    address_only_latched<=address_only; device_latched<=device_address;
                    addr_latched<=reg_addr; data_latched<=write_data;
                    tx_byte<={device_address,1'b0}; bit_index<=7; byte_index<=0;
                    state<=START_A;
                end
            end else if (!scl_low && scl_sync!==1'b1) begin
                // Also catches a disconnected/stuck or indefinitely stretched bus.
                if (timeout_count==TIMEOUT_CYCLES-1) begin
                    error_code<=8'h05; busy<=0; done<=1;
                    scl_low<=0; sda_low<=0; state<=IDLE; quarter<=0;
                    pending_read_start<=0;
                end else timeout_count<=timeout_count+1'b1;
            end else begin
                timeout_count<=0;
                if (tick) begin
                    quarter<=0;
                    case(state)
                        START_A,READ_START_A: if (sda_sync!==1'b1) begin
                            error_code<=8'h06; done<=1; busy<=0;
                            state<=IDLE; scl_low<=0; sda_low<=0;
                            pending_read_start<=0;
                        end else begin sda_low<=1; state<=START_HOLD; end
                        // Two quarters meet the standard-mode 4 us START hold.
                        START_HOLD: state<=START_B;
                        START_B: begin scl_low<=1; state<=TX_LOW; end
                        TX_LOW: begin
                            sda_low<=!tx_byte[bit_index]; state<=TX_HIGH;
                        end
                        TX_HIGH: begin scl_low<=0; state<=TX_HOLD; end
                        TX_HOLD: state<=TX_FALL;
                        TX_FALL: begin
                            scl_low<=1;
                            if (bit_index==0) state<=ACK_LOW;
                            else begin bit_index<=bit_index-1'b1; state<=TX_LOW; end
                        end
                        ACK_LOW: begin sda_low<=0; state<=ACK_HIGH; end
                        ACK_HIGH: begin scl_low<=0; state<=ACK_HOLD; end
                        ACK_HOLD: state<=ACK_FALL;
                        ACK_FALL: begin
                            scl_low<=1;
                            if (sda_sync!==1'b0) begin
                                if (read_phase) error_code<=8'h04;
                                else error_code<={6'b0,byte_index}+1'b1;
                                pending_read_start<=0;
                                state<=STOP_LOW;
                            end else if (read_phase) begin
                                bit_index<=7; state<=RX_LOW;
                            end else if (byte_index==0) begin
                                if (address_only_latched) state<=STOP_LOW;
                                else begin
                                    byte_index<=1; tx_byte<=addr_latched;
                                    bit_index<=7; state<=TX_LOW;
                                end
                            end else if (byte_index==1) begin
                                if (read_latched) begin
                                    read_phase<=1; tx_byte<={device_latched,1'b1};
                                    bit_index<=7; pending_read_start<=1; state<=STOP_LOW;
                                end else begin
                                    byte_index<=2; tx_byte<=data_latched;
                                    bit_index<=7; state<=TX_LOW;
                                end
                            end else state<=STOP_LOW;
                        end
                        STOP_LOW: begin
                            scl_low<=1; sda_low<=1; state<=STOP_HIGH;
                        end
                        STOP_HIGH: begin scl_low<=0; state<=STOP_SETUP; end
                        STOP_SETUP: state<=STOP_RELEASE;
                        STOP_RELEASE: begin sda_low<=0; state<=STOP_HOLD; end
                        // STOP_RELEASE -> STOP_HOLD -> READ_START_A gives two
                        // quarters (5 us) of bus-free time, without early done.
                        STOP_HOLD: if (pending_read_start) begin
                            pending_read_start<=0; state<=READ_START_A;
                        end else begin busy<=0; done<=1; state<=IDLE; end
                        RX_LOW: begin sda_low<=0; state<=RX_HIGH; end
                        RX_HIGH: begin scl_low<=0; state<=RX_HOLD; end
                        RX_HOLD: state<=RX_FALL;
                        RX_FALL: begin
                            if(read_second)read_high[bit_index]<=sda_sync;
                            else read_data[bit_index]<=sda_sync; scl_low<=1;
                            if(bit_index==0)read_count<=read_second ? 2 : 1;
                            if (bit_index==0) state<=NACK_LOW;
                            else begin bit_index<=bit_index-1'b1; state<=RX_LOW; end
                        end
                        NACK_LOW: begin sda_low<=two_latched && !read_second; state<=NACK_HIGH; end
                        NACK_HIGH: begin scl_low<=0; state<=NACK_HOLD; end
                        NACK_HOLD: state<=NACK_FALL;
                        NACK_FALL: begin
                            scl_low<=1;
                            if(two_latched && !read_second)begin read_second<=1;bit_index<=7;state<=RX_LOW;end
                            else state<=STOP_LOW;
                        end
                        default: begin
                            state<=IDLE; busy<=0; done<=1; error_code<=8'h07;
                            scl_low<=0; sda_low<=0;
                            pending_read_start<=0;
                        end
                    endcase
                end else quarter<=quarter+1'b1;
            end
        end
    end
endmodule
