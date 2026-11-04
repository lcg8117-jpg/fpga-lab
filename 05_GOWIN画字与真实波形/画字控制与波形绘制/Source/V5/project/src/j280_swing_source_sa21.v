// SWM2: independently timed forward and reverse active drive commands.
// Percentages and duration are latched once per explicit RUN session.
module j280_swing_source_sa21 #(
    parameter integer MODE = 0,
    parameter integer DYNAMIC_CONFIG = 0,
    parameter integer HANDOFF_HOLD_MS = 20,
    parameter integer ALLOW_FULL_ROTATION = 0,
    parameter integer CLOCKS_PER_MS = 50000,
    parameter integer DRIVE_NEGATIVE = 1,
    parameter integer PWM_ENCODER_SIGN = 1,
    parameter integer WRONG_DIRECTION_COUNTS = 2,
    parameter integer QUALIFY_MS = 60,
    parameter integer WAIT_TIMEOUT_MS = 3000,
    parameter integer REVERSE_MS = 220,
    parameter integer COAST_MS = 1800,
    parameter integer MAX_ATTEMPTS = 3
) (
    input wire clk_50m, rst_n, run_request, zero_valid, estop_active, adc_otr,
    input wire [7:0] pump_percent, final_pump_percent, final_trigger_deg, pulse_ms_cfg,
    input wire [127:0] swing_config,
    input wire [9:0] adc_raw,
    input wire [13:0] adc_zero_q4,
    input wire signed [15:0] alpha_raw_delta_q4,
    input wire signed [31:0] alpha_q20, alpha_dot_q20, theta_dot_q20, encoder_count,
    output reg swing_active, handoff_request,
    output reg signed [15:0] swing_duty_q15,
    output reg [6:0] swing_state,
    output wire [11:0] swing_adc12, top_center12,
    output reg [5:0] handoff_hold_ms,
    output reg final_armed, final_shot_done, final_fail,
    output reg [7:0] final_work_percent, adaptive_retry_count,
    output reg [1:0] adaptive_action,
    output wire [15:0] phase_elapsed_ms,effective_forward_ms,session_reverse_ms,
    output reg [7:0] phase_end_reason
);
    function integer counter_width;
        input integer maximum;
        integer remaining;
        begin
            remaining=maximum-1; counter_width=0;
            while(remaining>0) begin counter_width=counter_width+1; remaining=remaining>>1; end
            if(counter_width==0) counter_width=1;
        end
    endfunction
    localparam integer DIVIDER_BITS=counter_width(CLOCKS_PER_MS);
    // synthesis translate_off
    initial begin
        if(MODE!=0 || HANDOFF_HOLD_MS<1 || HANDOFF_HOLD_MS>63 || CLOCKS_PER_MS<1 ||
           QUALIFY_MS<1 || WAIT_TIMEOUT_MS<QUALIFY_MS || COAST_MS<20 ||
           MAX_ATTEMPTS<1 || REVERSE_MS<1 || REVERSE_MS>1000)
            $fatal(1,"Invalid SWM2 configuration");
    end
    // synthesis translate_on
    localparam [6:0] IDLE=0, QUALIFY=10, DRIVE=11, REVERSE=13,
        COAST=40, RETRY_WAIT=70, FAILED=90;
    localparam integer EXPECT_NEGATIVE = (DRIVE_NEGATIVE != 0) ^ (PWM_ENCODER_SIGN < 0);
    localparam signed [31:0] HANDOFF_ALPHA=32'sd274517;
    localparam signed [31:0] HANDOFF_AD=32'sd3660218;
    localparam signed [31:0] HANDOFF_THD=32'sd2196131;
    localparam signed [31:0] QUIET_RATE=32'sd366022;
    localparam signed [31:0] BOTTOM_ANGLE=32'sd2470649;
    localparam CONFIG_VALID=(MODE==0 &&
        (ALLOW_FULL_ROTATION==0 || ALLOW_FULL_ROTATION==1));

    // Freeze all phase and retry settings once per explicit RUN session.
    reg [127:0] session_config;
    wire [7:0] configured_drive=DYNAMIC_CONFIG?swing_config[7:0]:pump_percent;
    wire [7:0] configured_reverse=DYNAMIC_CONFIG?swing_config[15:8]:final_pump_percent;
    wire [15:0] configured_ms=DYNAMIC_CONFIG?swing_config[31:16]:{8'd0,pulse_ms_cfg};
    wire [15:0] configured_reverse_ms=DYNAMIC_CONFIG?swing_config[47:32]:REVERSE_MS;
    wire [15:0] qualify_limit=DYNAMIC_CONFIG?session_config[63:48]:QUALIFY_MS;
    wire [15:0] wait_limit=DYNAMIC_CONFIG?session_config[79:64]:WAIT_TIMEOUT_MS;
    wire [15:0] reverse_limit=DYNAMIC_CONFIG?session_config[47:32]:REVERSE_MS;
    wire [15:0] coast_limit=DYNAMIC_CONFIG?session_config[95:80]:COAST_MS;
    wire [7:0] attempt_limit=DYNAMIC_CONFIG?session_config[103:96]:MAX_ATTEMPTS;
    wire [7:0] retry_step=DYNAMIC_CONFIG?session_config[111:104]:8'd10;
    reg [DIVIDER_BITS-1:0] divider;
    wire tick_1ms = divider == CLOCKS_PER_MS-1;
    reg request_previous, session_started;
    reg [15:0] elapsed_ms, quiet_ms;
    reg [15:0] drive_ms;
    wire [16:0] retry_sum={1'b0,drive_ms}+{9'd0,retry_step};
    reg [7:0] attempt_number;
    reg signed [15:0] drive_command, reverse_command;
    reg signed [31:0] attempt_origin;
    reg crossed_top;
    reg previous_alpha_sign;
    reg [15:0] previous_alpha_abs;
    function [7:0] safe_percent;
        input [7:0] value;
        begin safe_percent = value > 97 ? 97 : value; end
    endfunction
    function signed [15:0] percent_q15;
        input [7:0] value;
        reg [15:0] widened;
        begin
            widened = {8'd0,safe_percent(value)};
            percent_q15 = (widened<<8)+(widened<<6)+(widened<<3);
        end
    endfunction
    // Modular subtraction handles signed32 wrap for these short trajectories.
    wire signed [31:0] displacement = encoder_count-attempt_origin;
    wire wrong_displacement = EXPECT_NEGATIVE ? displacement >= WRONG_DIRECTION_COUNTS :
                                                           displacement <= -WRONG_DIRECTION_COUNTS;
    wire near_top = alpha_q20 >= -HANDOFF_ALPHA && alpha_q20 <= HANDOFF_ALPHA;
    wire capture_window = near_top && alpha_dot_q20 >= -HANDOFF_AD &&
        alpha_dot_q20 <= HANDOFF_AD && theta_dot_q20 >= -HANDOFF_THD && theta_dot_q20 <= HANDOFF_THD;
    wire quiet_bottom = ((adc_raw <= 128)||(adc_raw >= 896)) &&
        ((alpha_q20 <= -BOTTOM_ANGLE)||(alpha_q20 >= BOTTOM_ANGLE)) &&
        alpha_dot_q20 >= -QUIET_RATE && alpha_dot_q20 <= QUIET_RATE &&
        theta_dot_q20 >= -QUIET_RATE && theta_dot_q20 <= QUIET_RATE;
    wire [15:0] alpha_abs = alpha_raw_delta_q4[15] ? -alpha_raw_delta_q4 : alpha_raw_delta_q4;
    wire true_top_cross = previous_alpha_sign != alpha_raw_delta_q4[15] &&
        ((alpha_abs <= 16'd2048)||(previous_alpha_abs <= 16'd2048));
    wire moving_phase = swing_state==DRIVE || swing_state==REVERSE || swing_state==COAST;
    assign swing_adc12 = {adc_raw,2'b00};
    assign top_center12 = adc_zero_q4[13:2];
    assign phase_elapsed_ms=elapsed_ms;
    assign effective_forward_ms=drive_ms;
    assign session_reverse_ms=reverse_limit;

    // No additional OTR policy: R10 deliberately ignores it at bottom wrap.
    // final_trigger_deg is reserved for the old resonant method and unused here.
    always @(posedge clk_50m or negedge rst_n) begin
        if(!rst_n) divider<=0;
        else if(tick_1ms) divider<=0;
        else divider<=divider+1'b1;
    end

    task fail_session;
        input [7:0] reason;
        begin
            phase_end_reason<=reason;
            // Retain ownership until STOP. Never fall into a different motor mux owner.
            swing_active<=1; swing_duty_q15<=0; handoff_request<=0;
            final_fail<=1; swing_state<=FAILED; handoff_hold_ms<=0;
        end
    endtask

    task enter_coast;
        begin
            swing_state<=COAST; swing_duty_q15<=0; elapsed_ms<=0;
            final_shot_done<=1;
        end
    endtask

    always @(posedge clk_50m or negedge rst_n) begin
        if(!rst_n) begin
            session_config<=0;phase_end_reason<=0;
            request_previous<=1; session_started<=0;
            swing_active<=0; handoff_request<=0; swing_duty_q15<=0; swing_state<=IDLE;
            handoff_hold_ms<=0; final_armed<=0; final_shot_done<=0; final_fail<=0;
            final_work_percent<=0; adaptive_retry_count<=0; adaptive_action<=0;
            elapsed_ms<=0; quiet_ms<=0; drive_ms<=0; attempt_number<=0;
            drive_command<=0; reverse_command<=0; attempt_origin<=0;
              crossed_top<=0;
            previous_alpha_sign<=0; previous_alpha_abs<=0;

        end else begin
            request_previous<=run_request;
            if(!run_request) begin
                phase_end_reason<=5;
                session_started<=0; swing_active<=0; handoff_request<=0;
                swing_duty_q15<=0; swing_state<=IDLE; handoff_hold_ms<=0;
                final_armed<=0; final_shot_done<=0; final_fail<=0;
                adaptive_retry_count<=0; adaptive_action<=0; elapsed_ms<=0; quiet_ms<=0;
            end else if(!zero_valid || estop_active) begin
                session_started<=1;
                fail_session(estop_active ? 8'd7 : 8'd6);
            end else if(!request_previous && !session_started) begin
                session_config<=swing_config;phase_end_reason<=0;
                session_started<=1; swing_active<=1; handoff_request<=0;
                swing_duty_q15<=0; swing_state<=QUALIFY; handoff_hold_ms<=0;
                final_armed<=0; final_shot_done<=0; final_fail<=0;
                adaptive_retry_count<=0; adaptive_action<=0; attempt_number<=0;
                elapsed_ms<=0; quiet_ms<=0; drive_ms<=configured_ms;
                drive_command <= DRIVE_NEGATIVE ? -percent_q15(configured_drive) : percent_q15(configured_drive);
                reverse_command <= DRIVE_NEGATIVE ? percent_q15(configured_reverse) : -percent_q15(configured_reverse);

                final_work_percent<=safe_percent(configured_reverse);
                previous_alpha_sign<=alpha_raw_delta_q4[15]; previous_alpha_abs<=alpha_abs;
                crossed_top<=0;  

                if(!CONFIG_VALID || configured_ms<1 || configured_ms>1000 ||
                   configured_reverse_ms<1 || configured_reverse_ms>1000 ||
                   configured_drive<1 || configured_drive>90 ||
                   configured_reverse<1 || configured_reverse>90)
                    fail_session(12);
            end else if(tick_1ms && swing_active && !final_fail) begin
                if(moving_phase) begin
                    previous_alpha_sign<=alpha_raw_delta_q4[15]; previous_alpha_abs<=alpha_abs;

                    if(true_top_cross) crossed_top<=1;
                end
                if(moving_phase && capture_window) begin
                    swing_duty_q15<=0; swing_state<=COAST;phase_end_reason<=3;
                    if(handoff_hold_ms==HANDOFF_HOLD_MS-1) begin
                        handoff_hold_ms<=HANDOFF_HOLD_MS; handoff_request<=1; swing_active<=0;phase_end_reason<=4;
                        swing_duty_q15<=0; swing_state<=IDLE;
                    end else handoff_hold_ms<=handoff_hold_ms+1'b1;
                    // Keep the coast deadline finite while candidate capture is interrupted.
                    if(swing_state!=COAST) elapsed_ms<=0;
                    else if(elapsed_ms<coast_limit) elapsed_ms<=elapsed_ms+1'b1;
                end else begin
                    handoff_hold_ms<=0;
                    case(swing_state)
                        QUALIFY, RETRY_WAIT: begin
                            swing_duty_q15<=0;
                            elapsed_ms<=elapsed_ms+1'b1;
                            if(quiet_bottom) quiet_ms<=quiet_ms+1'b1;
                            else quiet_ms<=0;
                            if(quiet_bottom && quiet_ms>=qualify_limit-1) begin
                                attempt_number<=attempt_number+1'b1;
                                if(attempt_number!=0) adaptive_retry_count<=adaptive_retry_count+1'b1;
                                attempt_origin<=encoder_count;
                                elapsed_ms<=0; quiet_ms<=0;
                                  crossed_top<=0;
                                previous_alpha_sign<=alpha_raw_delta_q4[15]; previous_alpha_abs<=alpha_abs;
                                final_armed<=1; final_shot_done<=0;

                                swing_state<=DRIVE; swing_duty_q15<=drive_command;phase_end_reason<=0;
                            end else if(elapsed_ms>=wait_limit-1) fail_session(10);
                        end
                        DRIVE: begin
                            elapsed_ms<=elapsed_ms+1'b1;
                            // Only this stage retains the archived wrong-direction check.
                            if(wrong_displacement) fail_session(8);
                            else if(elapsed_ms>=drive_ms-16'd1) begin
                                elapsed_ms<=0;swing_state<=REVERSE;
                                swing_duty_q15<=reverse_command;phase_end_reason<=1;
                            end
                        end
                        REVERSE: begin
                            // Timing starts with the reverse command, including deceleration.
                            // Zero speed, reverse encoder edges and quiet windows are not exits.
                            elapsed_ms<=elapsed_ms+1'b1;
                            if(elapsed_ms>=reverse_limit-16'd1) begin
                                enter_coast;phase_end_reason<=2;
                            end
                        end
                        COAST: begin
                            swing_duty_q15<=0; elapsed_ms<=elapsed_ms+1'b1;
                            if(elapsed_ms>=coast_limit-1) begin
                                if(attempt_number>=attempt_limit) fail_session(11);
                                else begin
                                    if(crossed_top || true_top_cross) begin
                                        drive_ms<=drive_ms>{8'd0,retry_step} ? drive_ms-{8'd0,retry_step} : 16'd1;
                                        adaptive_action<=1;
                                    end else begin
                                        drive_ms<=retry_sum>17'd1000 ? 16'd1000 : retry_sum[15:0];
                                        adaptive_action<=2;
                                    end
                                    elapsed_ms<=0; quiet_ms<=0; swing_state<=RETRY_WAIT;phase_end_reason<=9;
                                end
                            end
                        end
                        default: fail_session(12);
                    endcase
                end
            end
        end
    end
endmodule
