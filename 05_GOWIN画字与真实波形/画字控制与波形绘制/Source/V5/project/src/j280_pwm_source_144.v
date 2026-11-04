module j280_pwm_source_144 #(
    // WHEELTEC source carrier: TIM3 ARR=7199 at 72 MHz -> 10 kHz.
    parameter integer PWM_PERIOD = 5000
) (
    input wire clk_50m,
    input wire rst_n,
    input wire motor_enable,
    input wire signed [15:0] duty_q15,
    output wire AN1,
    output wire AN2,
    output wire PWMA,
    output wire [31:0] diagnostic_applied
);
    reg [12:0] pwm_count;
    reg applied_negative;
    reg [15:0] applied_duty_abs;
    reg reversal_blank;
    assign diagnostic_applied={pwm_count,reversal_blank,applied_negative,motor_enable,applied_duty_abs};

    wire requested_negative = duty_q15[15];
    wire [15:0] requested_duty_abs = requested_negative ?
        (~duty_q15 + 16'd1) : duty_q15;
    wire [28:0] duty_product = applied_duty_abs * PWM_PERIOD;
    wire [13:0] compare_count = duty_product >> 15;
    wire drive_active = motor_enable && (applied_duty_abs != 16'd0);

    // V2.26 J280 actuator shim only: PID mathematics is untouched.
    // Direction and magnitude change only at a PWM-period boundary.
    // A direct nonzero sign reversal inserts one complete 10 kHz off period.
    assign AN1 = drive_active ? ~applied_negative : 1'b0;
    assign AN2 = drive_active ?  applied_negative : 1'b0;
    assign PWMA = drive_active && (pwm_count < compare_count);

    always @(posedge clk_50m or negedge rst_n) begin
        if (!rst_n) begin
            pwm_count <= 13'd0;
            applied_negative <= 1'b0;
            applied_duty_abs <= 16'd0;
            reversal_blank <= 1'b0;
        end else if (!motor_enable) begin
            pwm_count <= 13'd0;
            applied_negative <= requested_negative;
            applied_duty_abs <= 16'd0;
            reversal_blank <= 1'b0;
        end else if (pwm_count == PWM_PERIOD - 1) begin
            pwm_count <= 13'd0;
            if (reversal_blank) begin
                applied_negative <= requested_negative;
                applied_duty_abs <= requested_duty_abs;
                reversal_blank <= 1'b0;
            end else if ((applied_duty_abs != 16'd0) &&
                         (requested_duty_abs != 16'd0) &&
                         (requested_negative != applied_negative)) begin
                // Finish the present direction, then remain disabled for one full period.
                applied_duty_abs <= 16'd0;
                reversal_blank <= 1'b1;
            end else begin
                applied_negative <= requested_negative;
                applied_duty_abs <= requested_duty_abs;
                reversal_blank <= 1'b0;
            end
        end else begin
            pwm_count <= pwm_count + 13'd1;
        end
    end
endmodule
