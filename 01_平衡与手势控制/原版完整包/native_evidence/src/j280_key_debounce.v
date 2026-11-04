module j280_key_debounce #(
    parameter integer STABLE_CYCLES = 500000
) (
    input wire clk_50m,
    input wire rst_n,
    input wire key_n,
    output reg pressed,
    output reg press_pulse
);
    reg sync_0;
    reg sync_1;
    reg [19:0] stable_count;
    reg candidate_pressed;

    always @(posedge clk_50m or negedge rst_n) begin
        if (!rst_n) begin
            sync_0 <= 1'b1;
            sync_1 <= 1'b1;
            stable_count <= 20'd0;
            candidate_pressed <= 1'b0;
            pressed <= 1'b0;
            press_pulse <= 1'b0;
        end else begin
            sync_0 <= key_n;
            sync_1 <= sync_0;
            press_pulse <= 1'b0;

            if ((~sync_1) != candidate_pressed) begin
                candidate_pressed <= ~sync_1;
                stable_count <= 20'd0;
            end else if (stable_count < STABLE_CYCLES) begin
                stable_count <= stable_count + 20'd1;
            end else if (pressed != candidate_pressed) begin
                pressed <= candidate_pressed;
                if (candidate_pressed)
                    press_pulse <= 1'b1;
            end
        end
    end
endmodule
