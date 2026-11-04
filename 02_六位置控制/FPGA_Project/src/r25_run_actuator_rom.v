// Forest numerical output -> existing S5C3 B-channel actuation contract.
// core_moto keeps its source PWM7200 units and existing +/-6900 bound.
// Exactly one board MOTOR_SIGN=-1 is applied after the unit conversion.
module r25_run_actuator_rom(
    input wire clk, input wire rst_n,
    input wire motor_permitted,
    input wire signed [31:0] core_moto,
    output wire BN1, output wire BN2, output wire PWMB,
    // Matches S5C3 duty_actual_q15: signed physical REQUEST, before the
    // driver's +/-29491 clamp/adoption/blank. Applied state is in diag.
    output wire signed [15:0] duty_actual_q15,
    output wire [31:0] diag
);
    // The core contract is +/-6900. Invalid wide inputs independently inhibit
    // the board; they cannot wrap through a sliced magnitude into new drive.
    wire source_in_range = core_moto >= -32'sd6900 && core_moto <= 32'sd6900;
    wire [31:0] source_magnitude = core_moto[31] ?
        (~core_moto + 32'd1) : core_moto;
    // floor(m*32768/7200) = floor(m*1024/225).
    // ceil((1024/225)*2^21)=9544372. For 0<=m<=8191 the positive
    // approximation error is <=8191*13/117964800 < 1/225, the least
    // nonzero fractional spacing. Thus this 13x24-bit multiply/shift is
    // EXACT over the declared source range, with no division logic.
    wire [36:0] reciprocal_product = source_magnitude[12:0] * 24'd9544372;
    wire signed [15:0] magnitude_q15 = reciprocal_product[36:21];
    wire signed [15:0] source_q15 = core_moto[31] ? -magnitude_q15 : magnitude_q15;
    assign duty_actual_q15 = source_in_range ? -source_q15 : 16'sd0;
    // J280_MOTOR_SIGN=-1 is applied once above, after the TZ unit conversion.

    // All real board protections, including the original Forest angle window,
    // feed motor_permitted. The source MCU AIN result is not a board admission:
    // its unavailable battery channel remains a reference-platform condition.
    wire driver_enable = motor_permitted && source_in_range;
    // A magnitude-only synchronous ROM uses the same prepared edge.
    // Full signed32 range remains the immediate independent inhibit above.
    // Sign and range are sampled with the address; there is no address FF.
    wire [12:0] magnitude_address = core_moto[31] ?
        (~core_moto[12:0] + 13'd1) : core_moto[12:0];
    // Exact floor(m*1024/225) on all unsigned13 addresses.
    // The original synchronous magnitude stage is replaced, not extended.
    // Product and address validity capture unconditionally, including reset.
    // The unused historical ROM table is omitted in the minimal project.
    wire [34:0] prepared_product_live /* synthesis syn_dspstyle = "dsp" */;
    assign prepared_product_live = magnitude_address * 22'd2386093;
    reg [34:0] product_q;
    reg address_ok_q;
    always @(posedge clk) begin
        product_q <= prepared_product_live;
        address_ok_q <= magnitude_address <= 13'd6900;
    end
    wire [14:0] ROM_q = address_ok_q ? product_q[33:19] : 15'd0;
    reg prepared_range_valid;
    reg prepared_negative;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            prepared_range_valid <= 1'b0;
            prepared_negative <= 1'b0;
        end else begin
            prepared_range_valid <= source_in_range;
            prepared_negative <= core_moto[31];
        end
    end
    wire signed [15:0] motor_duty_prepared_q15 = prepared_range_valid ?
        (prepared_negative ? $signed({1'b0,ROM_q}) : -$signed({1'b0,ROM_q})) : 16'sd0;
    // Byte-identical S5C3 driver, including its 4*25us command frame,
    // complete 100us nonzero-reversal blank and nominal +/-90% physical cap.
    // SOFT_COMMAND_RISE exists but is disabled by the actual input, as S5C3.
    j280_pwm_motor_safe #(.PWM_PERIOD(1250),.COMMAND_PERIODS(4),
        .SOFT_COMMAND_RISE(1),.DIAGNOSTIC_SWITCHES(1)) motor_driver(
        .clk_50m(clk),.rst_n(rst_n),.motor_enable(driver_enable),
        .duty_q15(motor_duty_prepared_q15),.soft_rise_enable(1'b0),
        .AN1(BN1),.AN2(BN2),.PWMA(PWMB),.diagnostic_applied(diag));
endmodule
