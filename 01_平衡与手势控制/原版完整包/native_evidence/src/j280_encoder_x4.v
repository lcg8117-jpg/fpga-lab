// IP570 HAL timer contract: TI12 (x4), IC1Filter=IC2Filter=10.
// At TIM4=72MHz, CKD=1, filter code10 samples at 72MHz/16=4.5MHz
// and requires five consecutive samples before a pin transition is accepted.
// A shared 9/100 clock-enable reproduces the average4.5MHz at this50MHz
// clock (11/12-clock intervals). It is NOT a generated/gated clock.
// Existing synchronizers, direction, x4 count and illegal-transition policy
// are preserved. Only sub-microsecond input qualification is added; there
// is no angle/D/velocity lowpass, new output limit or fault suppression.
module j280_encoder_x4 #(
    parameter integer FILTER_PHASE_MODULUS = 100,
    parameter integer FILTER_PHASE_STEP = 9,
    parameter integer FILTER_SAMPLES = 5
) (
    input wire clk_50m,
    input wire rst_n,
    input wire enc_a,
    input wire enc_b,
    output reg signed [31:0] count,
    output reg [15:0] invalid_transition_count
);
    reg [1:0] sync_a;
    reg [1:0] sync_b;
    function integer width_for;
        input integer n;
        integer v;
        begin
            v=n-1; width_for=0;
            while(v>0) begin width_for=width_for+1; v=v>>1; end
            if(width_for<1)width_for=1;
        end
    endfunction
    localparam integer PHASE_BITS=width_for(FILTER_PHASE_MODULUS);
    localparam integer SAMPLE_BITS=width_for(FILTER_SAMPLES);
    reg [PHASE_BITS-1:0] filter_phase;
    reg [SAMPLE_BITS-1:0] a_consecutive,b_consecutive;
    reg qualified_a,qualified_b;
    wire filter_sample=filter_phase>=FILTER_PHASE_MODULUS-FILTER_PHASE_STEP;

    always @(posedge clk_50m or negedge rst_n) begin
        if(!rst_n) begin
            filter_phase<=0; a_consecutive<=0; b_consecutive<=0;
            qualified_a<=0; qualified_b<=0;
        end else begin
            if(filter_sample)filter_phase<=filter_phase-(FILTER_PHASE_MODULUS-FILTER_PHASE_STEP);
            else filter_phase<=filter_phase+FILTER_PHASE_STEP;
            if(filter_sample)begin
                if(sync_a[1]==qualified_a)a_consecutive<=0;
                else if(a_consecutive==FILTER_SAMPLES-1)begin
                    qualified_a<=sync_a[1]; a_consecutive<=0;
                end else a_consecutive<=a_consecutive+1'b1;
                if(sync_b[1]==qualified_b)b_consecutive<=0;
                else if(b_consecutive==FILTER_SAMPLES-1)begin
                    qualified_b<=sync_b[1]; b_consecutive<=0;
                end else b_consecutive<=b_consecutive+1'b1;
            end
        end
    end
    reg [1:0] state_last;
    wire [1:0] state_now = {qualified_a,qualified_b};

    always @(posedge clk_50m or negedge rst_n) begin
        if (!rst_n) begin
            sync_a <= 2'b00;
            sync_b <= 2'b00;
            state_last <= 2'b00;
            count <= 32'sd0;
            invalid_transition_count <= 16'd0;
        end else begin
            sync_a <= {sync_a[0], enc_a};
            sync_b <= {sync_b[0], enc_b};
            state_last <= state_now;
            case ({state_last, state_now})
                4'b00_01, 4'b01_11, 4'b11_10, 4'b10_00:
                    count <= count - 32'sd1;
                4'b00_10, 4'b10_11, 4'b11_01, 4'b01_00:
                    count <= count + 32'sd1;
                4'b00_11, 4'b11_00, 4'b01_10, 4'b10_01:
                    invalid_transition_count <= invalid_transition_count + 16'd1;
                default: begin
                    count <= count;
                    invalid_transition_count <= invalid_transition_count;
                end
            endcase
        end
    end
endmodule
