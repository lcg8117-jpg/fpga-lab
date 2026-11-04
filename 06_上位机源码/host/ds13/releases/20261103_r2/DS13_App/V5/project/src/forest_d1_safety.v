// Runtime board adapter. No PID or angle estimator is instantiated.
// Lifecycle and real board protection predicates follow audited S5C3.
// The STM32 battery channel is absent on this board; there is no voltage
// admission input and no fabricated voltage measurement in this adapter.
module forest_d1_safety #(
    parameter integer ALLOW_SWING=0,
    parameter integer ZERO_SAMPLES=1024,
    parameter [15:0] HANDOFF_ENTER_Q4=16'd272,
    parameter integer USE_SPLIT_MOTOR_INVALIDATE=0
)(
    input wire clk,rst_n,tick_1ms,kit_ip570,invalidate_calibration,
    input wire zero_pulse,run_pulse,stop_pulse,clear_pulse,estop_raw,
    input wire start_swing_pulse,hold_pulse,handoff_pulse,swing_failed,
    input wire [9:0] adc_raw,
    input wire [13:0] adc_q4,
    input wire adc_otr_qualified,
    input wire signed [31:0] encoder_count,
    input wire [15:0] invalid_count,
    input wire arithmetic_fault,
    input wire source_sample_en,
    input wire [11:0] source_angle_adc12,
    output reg [2:0] mode /* synthesis syn_maxfan=32 */,
    output reg [7:0] fault /* synthesis syn_maxfan=32 */,
    output reg zero_valid /* synthesis syn_maxfan=32 */,capture_active /* synthesis syn_maxfan=32 */,
    output reg [13:0] zero_q4,
    output reg signed [31:0] encoder_zero,
    output reg run_session /* synthesis syn_preserve=1 syn_maxfan=32 */,
    output reg run_session_controls /* synthesis syn_preserve=1 syn_keep=1 syn_maxfan=32 */,
    output wire motor_permitted,swing_permitted,
    input wire motor_invalidate_calibration
);
    localparam [2:0] SAFE_IDLE=0,ZERO_CAPTURED=1,ARMED=2,BALANCE=3,FAULT_STOP=4,SWING=5;
    reg [10:0] capture_count;
    reg [24:0] capture_sum;
    wire [24:0] capture_final_sum=capture_sum+{11'd0,adc_q4};
    reg [15:0] invalid_at_zero;
    reg [4:0] speed_fault_count;
    wire signed [15:0] delta_unwrapped=$signed({1'b0,adc_q4})-$signed({1'b0,zero_q4});
    wire signed [15:0] delta=delta_unwrapped>16'sd8191 ? delta_unwrapped-16'sd16384:
        delta_unwrapped< -16'sd8192 ? delta_unwrapped+16'sd16384:delta_unwrapped;
    wire [15:0] abs_delta=delta[15] ? -delta:delta;
    wire inside_enter=abs_delta<=16'd272;
    // Swing handoff admission is separate from ordinary RUN/ARMED entry.
    // The top passes the same calibrated Q4 limit used by its prime window.
    wire inside_handoff=abs_delta<=HANDOFF_ENTER_Q4;
    wire angle_bad=mode==BALANCE && abs_delta>16'd3808;
    // Inspect exactly the 15-point mean presented to the immutable Forest
    // controller, on the same sample edge. The original +/-800-count window
    // is inclusive. Do not wait for the later floating-point core completion.
    wire source_mean_outside=source_angle_adc12<12'd2285 || source_angle_adc12>12'd3885;
    reg latest_source_mean_bad;
    // The latest accepted mean remains a real hazard until another accepted
    // mean proves it safe. User CLEAR changes the fault state, never this
    // independent measurement state; a coincident CLEAR cannot swallow a
    // one-clock unsafe sample. ZERO invalidation begins a new sample epoch.
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n)latest_source_mean_bad<=1'b0;
        else if(!zero_valid)latest_source_mean_bad<=1'b0;
        else if(source_sample_en)latest_source_mean_bad<=source_mean_outside;
    end
    wire source_angle_bad=zero_valid && (!ALLOW_SWING || mode==BALANCE || mode==ARMED) &&
        ((source_sample_en && source_mean_outside) || latest_source_mean_bad);
    wire adc_bad=zero_valid && adc_otr_qualified && adc_raw>10'd8 && adc_raw<10'd1015;
    wire signed [31:0] measured_velocity,legacy_velocity,legacy_raw;
    wire velocity_valid,using_period,measurement_fault;
    wire clear_velocity=!zero_valid || capture_active || invalidate_calibration || (clear_pulse && !run_session);
    // Exact byte-frozen S5C3 measurement module. The legacy M path supplies
    // the independent speed guard; M/T measurement failures remain faults.
    j280_encoder_velocity velocity_guard(
        .clk(clk),.rst_n(rst_n),.tick_1ms(tick_1ms),.clear(clear_velocity),.kit_ip570(kit_ip570),
        .encoder_count(encoder_count),.invalid_count(invalid_count),.hybrid_mode(1'b1),
        .velocity_q20(measured_velocity),.velocity_valid(velocity_valid),.using_period(using_period),
        .legacy_velocity_q20(legacy_velocity),.legacy_raw_q20(legacy_raw),.measurement_fault(measurement_fault));
    wire encoder_bad=zero_valid && (invalid_count!=invalid_at_zero || measurement_fault);
    wire speed_excess=legacy_velocity>32'sd10485760 || legacy_velocity< -32'sd10485760;
    wire speed_bad=(mode==BALANCE || mode==SWING) && speed_fault_count>=20;
    wire automatic_fault=angle_bad || source_angle_bad || adc_bad || encoder_bad || speed_bad || arithmetic_fault || (ALLOW_SWING && mode==SWING && swing_failed);
    // Direct inhibit is independent of the 1-ms state update. A RUN event
    // while already running is a STOP request, never an automatic re-enable.
    // The selected input is an equal, same-edge register replica. Keep the
    // motor cone separate from the high-fanout velocity-clear qualification.
    wire motor_calibration_invalid=USE_SPLIT_MOTOR_INVALIDATE ? motor_invalidate_calibration:invalidate_calibration;
    assign motor_permitted=mode==BALANCE && run_session && zero_valid && !capture_active && !motor_calibration_invalid &&
        fault==0 && !estop_raw && !stop_pulse && !zero_pulse &&
        !(run_pulse && run_session) && !angle_bad && !adc_bad && !encoder_bad &&
        !speed_bad && !arithmetic_fault && !source_angle_bad;
    // Only the explicit swing mode has an unrestricted angular travel envelope.
    // ADC, encoder, sustained-speed and arithmetic/STOP inhibits remain live.
    assign swing_permitted=ALLOW_SWING && mode==SWING && run_session && zero_valid && !capture_active && !motor_calibration_invalid &&
        fault==0 && !estop_raw && !stop_pulse && !zero_pulse && !(run_pulse && run_session) && !adc_bad && !encoder_bad &&
        !speed_bad && !arithmetic_fault && !swing_failed;
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n)begin
            mode<=SAFE_IDLE;fault<=0;zero_valid<=0;capture_active<=0;
            zero_q4<=0;encoder_zero<=0;run_session<=0;run_session_controls<=0;
            capture_count<=0;capture_sum<=0;invalid_at_zero<=0;speed_fault_count<=0;
        end else begin
        if(estop_raw)begin
            mode<=FAULT_STOP;fault<=8'h03;capture_active<=0;run_session<=0;run_session_controls<=0;speed_fault_count<=0;
        end else if(stop_pulse)begin
            run_session<=0;run_session_controls<=0;capture_active<=0;speed_fault_count<=0;
            mode<=fault!=0 ? FAULT_STOP:zero_valid ? ZERO_CAPTURED:SAFE_IDLE;
        end else if(zero_pulse)begin
            run_session<=0;run_session_controls<=0;speed_fault_count<=0;
            if(run_session || mode==BALANCE || mode==ARMED)begin
                // First ZERO during an active RUN is STOP only. A subsequent
                // stopped ZERO request is required before taking any samples.
                capture_active<=0;
                mode<=fault!=0 ? FAULT_STOP:zero_valid ? ZERO_CAPTURED:SAFE_IDLE;
            end else begin
                // A stopped ZERO is a deliberate new measurement epoch,
                // including recovery from a mapping-range fault. Clearing
                // zero_valid also tells the external mapper to discard its
                // old epoch; persistent real hazards still fault afterwards.
                fault<=0;mode<=SAFE_IDLE;zero_valid<=0;capture_active<=1;capture_count<=0;capture_sum<=0;
            end
        end else if(clear_pulse && !run_session && !capture_active)begin
            fault<=0;mode<=zero_valid ? ZERO_CAPTURED:SAFE_IDLE;
            invalid_at_zero<=invalid_count;speed_fault_count<=0;
        end else if(automatic_fault)begin
            mode<=FAULT_STOP;capture_active<=0;run_session<=0;run_session_controls<=0;speed_fault_count<=0;
            fault<=angle_bad || source_angle_bad ? 8'h01:adc_bad ? 8'h04:encoder_bad ? 8'h05:
                speed_bad ? 8'h07:(ALLOW_SWING && mode==SWING && swing_failed) ? 8'h09:8'h08;
        end else if(invalidate_calibration && !run_session && !capture_active)begin
            // A stopped kit change invalidates scale-dependent calibration;
            // it neither clears a fault nor starts ZERO or RUN.
            zero_valid<=0;mode<=fault!=0 ? FAULT_STOP:SAFE_IDLE;
            speed_fault_count<=0;capture_count<=0;capture_sum<=0;
        end else if(ALLOW_SWING && start_swing_pulse)begin
            if(!run_session && zero_valid && !capture_active && fault==0)begin mode<=SWING;run_session<=1;run_session_controls<=1;speed_fault_count<=0;end
        end else if(ALLOW_SWING && hold_pulse && mode==SWING)begin
            encoder_zero<=encoder_count;
        end else if(ALLOW_SWING && handoff_pulse && mode==SWING && run_session && inside_handoff && !latest_source_mean_bad && !(source_sample_en && source_mean_outside))begin
            mode<=BALANCE;speed_fault_count<=0;
        end else if(run_pulse)begin
            if(run_session)begin
                run_session<=0;run_session_controls<=0;mode<=zero_valid ? ZERO_CAPTURED:SAFE_IDLE;speed_fault_count<=0;
            end else if(zero_valid && !capture_active && fault==0)begin
                run_session<=1;run_session_controls<=1;mode<=inside_enter ? BALANCE:ARMED;speed_fault_count<=0;
            end
        end else if(tick_1ms)begin
            if(capture_active)begin
                if(capture_count==ZERO_SAMPLES-1)begin
                    zero_q4<=capture_final_sum/ZERO_SAMPLES;
                    encoder_zero<=encoder_count;invalid_at_zero<=invalid_count;
                    zero_valid<=1;capture_active<=0;capture_count<=0;capture_sum<=0;mode<=ZERO_CAPTURED;
                end else begin
                    capture_sum<=capture_final_sum;capture_count<=capture_count+1'b1;
                end
            end else begin
                if((mode==BALANCE || mode==SWING) && speed_excess)begin
                    if(speed_fault_count<20)speed_fault_count<=speed_fault_count+1'b1;
                end else speed_fault_count<=0;
                if(mode==ARMED && run_session && inside_enter)mode<=BALANCE;
            end
        end
        if(invalidate_calibration && !run_session && !capture_active)zero_valid<=0;
        end
    end
endmodule
