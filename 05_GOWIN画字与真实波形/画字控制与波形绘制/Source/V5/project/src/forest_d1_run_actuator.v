// Forest numerical output -> existing S5C3 B-channel actuation contract.
// core_moto keeps its source PWM7200 units and existing +/-6900 bound.
// Exactly one board MOTOR_SIGN=-1 is applied after the unit conversion.
module forest_d1_run_actuator(
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
    reg signed [15:0] motor_duty_prepared_q15;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) motor_duty_prepared_q15 <= 16'sd0;
        else motor_duty_prepared_q15 <= duty_actual_q15;
    end
    // Byte-identical S5C3 driver, including its 4*25us command frame,
    // complete 100us nonzero-reversal blank and nominal +/-90% physical cap.
    // SOFT_COMMAND_RISE exists but is disabled by the actual input, as S5C3.
    j280_pwm_motor_safe #(.PWM_PERIOD(1250),.COMMAND_PERIODS(4),
        .SOFT_COMMAND_RISE(1),.DIAGNOSTIC_SWITCHES(1)) motor_driver(
        .clk_50m(clk),.rst_n(rst_n),.motor_enable(driver_enable),
        .duty_q15(motor_duty_prepared_q15),.soft_rise_enable(1'b0),
        .AN1(BN1),.AN2(BN2),.PWMA(PWMB),.diagnostic_applied(diag));
endmodule
