// Asynchronous assertion and two-clock synchronous reset release.
module forest_d1_reset(input wire clk, input wire rst_n_in, output wire rst_n);
    reg [1:0] release_pipe;
    always @(posedge clk or negedge rst_n_in)
        if (!rst_n_in) release_pipe <= 2'b00;
        else release_pipe <= {release_pipe[0],1'b1};
    assign rst_n = release_pipe[1];
endmodule
