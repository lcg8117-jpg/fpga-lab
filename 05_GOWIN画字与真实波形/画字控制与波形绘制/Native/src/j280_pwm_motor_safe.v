module j280_pwm_motor_safe #(
    // 151: ultrasonic carrier, unchanged 100 us command/commutation frame.
    parameter integer PWM_PERIOD = 1250,
    parameter integer COMMAND_PERIODS = 4,
    parameter integer SOFT_COMMAND_RISE = 0,
    parameter integer DIAGNOSTIC_SWITCHES = 0
) (
    input wire clk_50m,
    input wire rst_n,
    input wire motor_enable,
    input wire signed [15:0] duty_q15,
    input wire soft_rise_enable,
    output wire AN1,
    output wire AN2,
    output wire PWMA,
    output wire [31:0] diagnostic_applied
);
    function integer counter_width;
        input integer n;
        integer v;
        begin
            // Legal terminal counters need only ceil(log2(n)). Preserve the
            // parent's 13-bit wrap for unsupported parameter values.
            if(n<1 || n>8192) counter_width=13;
            else begin
                v=n-1;counter_width=0;
                while(v>0) begin counter_width=counter_width+1;v=v>>1;end
                if(counter_width==0)counter_width=1;
            end
        end
    endfunction
    localparam integer CARRIER_BITS=counter_width(PWM_PERIOD);
    localparam integer FRAME_BITS=counter_width(COMMAND_PERIODS);
    reg [CARRIER_BITS-1:0] carrier_count;
    reg [FRAME_BITS-1:0] frame_count;
    wire [12:0] pwm_count=carrier_count;
    wire [12:0] command_period_count=frame_count;
    reg applied_negative;
    reg [15:0] applied_duty_abs;
    reg reversal_blank;
    assign diagnostic_applied = {pwm_count,reversal_blank,applied_negative,motor_enable,applied_duty_abs};

    wire raw_negative = duty_q15[15];
    wire [15:0] requested_duty_raw_abs = raw_negative ?
        (~duty_q15 + 16'd1) : duty_q15;
    wire [15:0] requested_duty_clamped_abs = (DIAGNOSTIC_SWITCHES && requested_duty_raw_abs > 16'd29491) ?
        16'd29491 : requested_duty_raw_abs;
    reg requested_negative_prepared;
    reg [15:0] requested_duty_prepared;
    always @(posedge clk_50m or negedge rst_n) begin
        if (!rst_n) begin requested_negative_prepared <= 1'b0; requested_duty_prepared <= 16'd0; end
        else begin requested_negative_prepared <= raw_negative; requested_duty_prepared <= requested_duty_clamped_abs; end
    end
    wire requested_negative = DIAGNOSTIC_SWITCHES ? requested_negative_prepared : raw_negative;
    wire [15:0] requested_duty_abs = DIAGNOSTIC_SWITCHES ? requested_duty_prepared : requested_duty_clamped_abs;
    // 155: only increases are limited; reductions and the off-frame stay immediate.
    wire [16:0] rise_step = 17'd41 + {5'd0, requested_duty_abs[15:4]};
    wire [16:0] rise_limit = {1'b0, applied_duty_abs} + rise_step;
    wire [15:0] next_duty_abs = (SOFT_COMMAND_RISE != 0 &&
        (!DIAGNOSTIC_SWITCHES || soft_rise_enable) &&
        {1'b0, requested_duty_abs} > rise_limit) ? rise_limit[15:0] : requested_duty_abs;
    wire [28:0] duty_product;
    generate if(PWM_PERIOD == 1250) begin: exact_carrier_product
        // 1250=1024+128+64+32+2. Pure arithmetic rewrite, no new register.
        // Avoid absorbing the enabled duty register into a DSP input cone.
        wire [28:0] magnitude={13'd0,applied_duty_abs};
        wire [28:0] high_part=(magnitude<<10)+(magnitude<<7);
        wire [28:0] low_part=(magnitude<<6)+(magnitude<<5)+(magnitude<<1);
        assign duty_product=high_part+low_part;
    end else begin: other_carrier_product
        assign duty_product=applied_duty_abs * PWM_PERIOD;
    end endgenerate
    wire [13:0] compare_count = duty_product >> 15;
    wire drive_active = motor_enable && (applied_duty_abs != 16'd0);

    // V2.26 J280 actuator shim only: PID mathematics is untouched.
    // Direction and magnitude change only at a PWM-period boundary.
    // A nonzero reversal still inserts a complete 100 us off frame, not 25 us.
    assign AN1 = drive_active ? ~applied_negative : 1'b0;
    assign AN2 = drive_active ?  applied_negative : 1'b0;
    assign PWMA = drive_active && (pwm_count < compare_count);

    always @(posedge clk_50m or negedge rst_n) begin
        if(!rst_n) carrier_count<=0;
        else if(!motor_enable || pwm_count==PWM_PERIOD-1) carrier_count<=0;
        else carrier_count<=carrier_count+1'b1;
    end
    always @(posedge clk_50m or negedge rst_n) begin
        if (!rst_n) begin
            frame_count <= 0;
            applied_negative <= 1'b0;
            applied_duty_abs <= 16'd0;
            reversal_blank <= 1'b0;
        end else if (!motor_enable) begin
            frame_count <= 0;
            applied_negative <= requested_negative;
            applied_duty_abs <= 16'd0;
            reversal_blank <= 1'b0;
        end else if (pwm_count == PWM_PERIOD - 1) begin
            if (command_period_count == COMMAND_PERIODS - 1) begin
                frame_count <= 0;
                if (reversal_blank) begin
                    applied_negative <= requested_negative;
                    applied_duty_abs <= next_duty_abs;
                    reversal_blank <= 1'b0;
                end else if ((applied_duty_abs != 16'd0) &&
                             (requested_duty_abs != 16'd0) &&
                             (requested_negative != applied_negative)) begin
                    applied_duty_abs <= 16'd0;
                    reversal_blank <= 1'b1;
                end else begin
                    applied_negative <= requested_negative;
                    applied_duty_abs <= next_duty_abs;
                    reversal_blank <= 1'b0;
                end
            end else begin
                frame_count <= frame_count + 1'b1;
            end
        end
    end
endmodule
