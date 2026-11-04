module j280_swing_source_144 #(parameter integer CLOCKS_PER_MS=50000) (
    input  wire clk_50m,
    input  wire rst_n,
    input  wire run_request,
    input  wire zero_valid,
    input  wire estop_active,
    input  wire adc_otr,
    input  wire [7:0] pump_percent,
    input  wire [7:0] final_pump_percent,
    input  wire [7:0] final_trigger_deg,
    input  wire [7:0] pulse_ms_cfg,
    input  wire [9:0] adc_raw,
    input  wire [13:0] adc_zero_q4,
    input  wire signed [15:0] alpha_raw_delta_q4,
    input  wire signed [31:0] alpha_q20,
    input  wire signed [31:0] alpha_dot_q20,
    input  wire signed [31:0] theta_dot_q20,
    input  wire signed [31:0] encoder_count,
    output reg swing_active,
    output reg handoff_request,
    output reg signed [15:0] swing_duty_q15,
    output reg [6:0] swing_state,
    output wire [11:0] swing_adc12,
    output wire [11:0] top_center12,
    output reg [5:0] handoff_hold_ms,
    output reg final_armed,
    output reg final_shot_done,
    output reg final_fail,
    output reg [7:0] final_work_percent,
    output reg [7:0] adaptive_retry_count,
    output reg [1:0] adaptive_action,
    output wire [1:0] observe_pair,
    output wire observe_tick
);
    wire [7:0] pulse_ms_safe = (pulse_ms_cfg < 8'd1) ? 8'd1 : pulse_ms_cfg;
    localparam [6:0] START_WAIT_MS = 7'd60;
    localparam [9:0] START_MIN_EXCURSION_RAW = 10'd16; // ~5.6 deg from bottom wrap
    localparam [5:0] SAMPLE_TERM_MS = 6'd39; // 40 ms legacy fallback
    localparam [15:0] NORMAL_PEAK_WRAP_Q4 = 16'd7282; // 160deg: reject +/-180deg bottom-wrap false turns
    localparam [11:0] EXTREMA_RANGE12 = 12'd500;

    // V5.6 automatic handoff gate:
    // angle <=15deg, pendulum speed <=200deg/s, arm speed <=120deg/s,
    // continuously for 20ms.
    localparam [31:0] HANDOFF_ALPHA_Q20 = 32'd274517;
    localparam [31:0] HANDOFF_AD_Q20    = 32'd3660218;
    localparam [31:0] HANDOFF_THD_Q20   = 32'd2196131;
    localparam [5:0] HANDOFF_HOLD_MS    = 6'd20;
    localparam [31:0] FINAL_PEAK_AD_Q20   = 32'd2196132; // 120 deg/s, final-fail turning check
    localparam [31:0] FINAL_PREPEAK_AD_Q20= 32'd1830110; // legacy 100 deg/s, retained for reference
    // 131 FAST ADAPTIVE: stage-dependent pre-peak timing limits.
    localparam [31:0] FAST_AD_300_Q20 = 32'd5490330;
    localparam [31:0] FAST_AD_260_Q20 = 32'd4758286;
    localparam [31:0] FAST_AD_220_Q20 = 32'd4026242;
    localparam [31:0] FAST_AD_170_Q20 = 32'd3111187;
    localparam [31:0] FAST_AD_120_Q20 = 32'd2196132;
    localparam [15:0] FAST_A60_Q4 = 16'd2731;
    localparam [15:0] FAST_A45_Q4 = 16'd2048;
    localparam [15:0] FAST_A30_Q4 = 16'd1365;
    localparam [15:0] FAST_A20_Q4 = 16'd910;
    localparam [31:0] FINAL_MOVING_AD_Q20 = 32'd1098065; // 60 deg/s, adaptive turning-point detector only
    localparam [31:0] FINAL_TOP_NEAR_Q20  = 32'd274517;  // 15 deg
    localparam [31:0] FINAL_TOP_EXIT_Q20  = 32'd366022;  // 20 deg hysteresis
    localparam [7:0] ADAPT_STEP_PERCENT = 8'd2;
    localparam [7:0] ADAPT_FINE_STEP_PERCENT = 8'd1;
    localparam [7:0] ADAPT_OVERSHOOT_STEP_PERCENT = 8'd4;
    localparam [7:0] ADAPT_MIN_PERCENT  = 8'd5;
    localparam [15:0] ADAPT_PEAK_MARGIN_Q4 = 16'd64; // ~1.4deg: ignore tiny peak noise
    localparam [15:0] ADAPT_TOP_CROSS_Q4 = 16'd2048; // 45deg: sign flip counts only near upright, never at +/-180 bottom wrap
    localparam [15:0] ADAPT_LOST_SWING_Q4 = 16'd5461; // 120deg: if turning point falls this low, rebuild with NORMAL/START_KICK
    localparam [15:0] ADAPT_PAIR_SOFT_Q4 = 16'd2048; // 45deg: once both sides enter here, prevent 42->44 overshoot jump
    localparam [15:0] ADAPT_PAIR_FINE_Q4 = 16'd1365; // 30deg: legacy fine threshold retained
    localparam [15:0] ADAPT_PAIR_HOLD_Q4 = 16'd683; // 15deg: both sides inside handoff angle -> hold/coast
    localparam [11:0] ROT_QUIET_RECOVER_MS = 12'd800; // no true top crossing for 0.8s + bottom_ready -> NORMAL recovery

    reg [15:0] div_1k;
    reg tick_1k;
    reg [5:0] sample_ms;
    reg [1:0] hist_valid;
    reg [1:0] final_hist_valid;
    reg [11:0] angle0;
    reg [11:0] angle1;
    reg [15:0] final_abs0;
    reg [15:0] final_abs1;
    reg final_qual0;
    reg final_qual1;
    reg final_sign0;
    reg final_sign1;
    reg [11:0] final_adc0;
    reg [11:0] final_adc1;
    reg [7:0] pulse_ms;
    // 140: pair index for controlled 92/92/91 ms experiment.
    // 0=start pair, 1=first NORMAL pair, 2=second NORMAL pair (third pair overall).
    reg [1:0] swing_pair_index;
    assign observe_pair=swing_pair_index;
    assign observe_tick=tick_1k;
    wire [7:0] normal_pulse_ms_929291 =
        ((swing_pair_index == 2'd2) && (pulse_ms_safe > 8'd1)) ?
        (pulse_ms_safe - 8'd1) : pulse_ms_safe;
    reg [7:0] final_pulse_ms_work;
    reg [6:0] start_wait_ms;
    reg [9:0] startup_peak_raw;
    reg final_left_peak;
    reg final_top_seen;
    reg [7:0] final_coast_ms;
    reg [15:0] prev_peak_abs_q4;
    reg attempt_start_sign;
    reg attempt_crossed_top;
    reg [1:0] lost_swing_count;
    reg final_pair_first_valid;
    reg [15:0] final_pair_first_abs_q4;
    reg adapt_prev_alpha_sign;
    reg [15:0] adapt_prev_alpha_abs_q4;
    reg rotation_coast_active;
    reg [11:0] rotation_quiet_ms;
    // 132: 1ms phase history for NORMAL peak lock. START_KICK is untouched.
    reg normal_prev_valid;
    reg normal_prev_alpha_sign;
    reg normal_prev_ad_sign;
    reg started;
    reg signed [31:0] swing_home_encoder;
    reg [2:0] swing_fric_zone;
    wire signed [31:0] swing_rel_encoder = encoder_count - swing_home_encoder;

    // Timing-clean input pipe: all adaptive swing decisions use the previous
    // 50 MHz sample of the wrapped pendulum angle. 20 ns is negligible versus
    // the 1 ms swing state-machine period, while it breaks the ADC->decision
    // critical path without altering the 1 kHz algorithm.
    reg signed [15:0] alpha_raw_delta_q4_pipe;
    reg alpha_sign_q4_pipe2;
    reg [15:0] alpha_abs_q4_pipe2;
    wire [15:0] alpha_abs_q4_pipe_comb = alpha_raw_delta_q4_pipe[15] ?
        (~alpha_raw_delta_q4_pipe + 16'd1) : alpha_raw_delta_q4_pipe;
    always @(posedge clk_50m or negedge rst_n) begin
        if (!rst_n) begin
            alpha_raw_delta_q4_pipe <= 16'sd0;
            alpha_sign_q4_pipe2 <= 1'b0;
            alpha_abs_q4_pipe2 <= 16'd0;
        end else begin
            alpha_raw_delta_q4_pipe <= alpha_raw_delta_q4;
            alpha_sign_q4_pipe2 <= alpha_raw_delta_q4_pipe[15];
            alpha_abs_q4_pipe2 <= alpha_abs_q4_pipe_comb;
        end
    end

    assign swing_adc12 = {adc_raw,2'b00};
    assign top_center12 = adc_zero_q4[13:2];

    // Same percent -> Q15 conversion used by the earlier working resonant branch.
    // 60 -> 19680/32768 = 60.06%, 45 -> 14760/32768 = 45.04%.
    wire [7:0] pump_percent_safe = (pump_percent > 8'd97) ? 8'd97 : pump_percent;
    wire [15:0] pump_q15_u =
        ({8'd0,pump_percent_safe} << 8) +
        ({8'd0,pump_percent_safe} << 6) +
        ({8'd0,pump_percent_safe} << 3);
    wire signed [15:0] pump_q15 = $signed(pump_q15_u);

    wire [7:0] final_pump_percent_safe = (final_pump_percent > 8'd97) ? 8'd97 : final_pump_percent;
    wire [7:0] final_runtime_percent_safe =
        (final_work_percent > 8'd97) ? 8'd97 : final_work_percent;
    wire [15:0] final_pump_q15_u =
        ({8'd0,final_runtime_percent_safe} << 8) +
        ({8'd0,final_runtime_percent_safe} << 6) +
        ({8'd0,final_runtime_percent_safe} << 3);
    wire signed [15:0] final_pump_q15 = $signed(final_pump_q15_u);
    wire [7:0] adaptive_max_percent =
        (pump_percent_safe > 8'd1) ? (pump_percent_safe - 8'd1) : pump_percent_safe;

    // FINAL keeps the learned 36/38/40/... full-cycle staircase.
    // Only compensate measured local static-friction deviation from the
    // six-point one-lap mean (~7.34%); signed correction avoids globally
    // increasing swing energy.
    reg signed [3:0] final_pos_fric_corr_pct;
    reg signed [3:0] final_neg_fric_corr_pct;
    reg signed [3:0] final_pair_side_corr_pct;
    always @* begin
        final_pos_fric_corr_pct = 4'sd0;
        final_neg_fric_corr_pct = 4'sd0;
        // 129 half-cycle balance trim, reduced from 128 after real-hardware tests:
        // state61..64 occurs on the NEGATIVE pendulum-side peak and drives
        // the following POSITIVE peak, so strengthen that whole pair +1%.
        // state51..54 occurs on the POSITIVE pendulum-side peak and drives
        // the following NEGATIVE peak, so soften that whole pair -1%.
        // Average commanded energy is therefore not globally increased.
        case (swing_state)
            7'd51,7'd52,7'd53,7'd54: final_pair_side_corr_pct = -4'sd1;
            7'd61,7'd62,7'd63,7'd64: final_pair_side_corr_pct =  4'sd1;
            default:                 final_pair_side_corr_pct =  4'sd0;
        endcase
        case (swing_fric_zone)
            3'd0: begin final_pos_fric_corr_pct= 4'sd1; final_neg_fric_corr_pct=-4'sd1; end // P1 8.10/6.00
            3'd1: begin final_pos_fric_corr_pct= 4'sd0; final_neg_fric_corr_pct= 4'sd1; end // P2 7.10/8.40
            3'd2: begin final_pos_fric_corr_pct=-4'sd2; final_neg_fric_corr_pct= 4'sd2; end // P3 5.40/9.43
            3'd3: begin final_pos_fric_corr_pct=-4'sd1; final_neg_fric_corr_pct= 4'sd0; end // P4 6.77/7.43
            3'd4: begin final_pos_fric_corr_pct= 4'sd1; final_neg_fric_corr_pct=-4'sd1; end // P5 8.10/6.43
            3'd5: begin final_pos_fric_corr_pct= 4'sd2; final_neg_fric_corr_pct=-4'sd2; end // P6 9.77/5.10
            default: begin final_pos_fric_corr_pct=4'sd0; final_neg_fric_corr_pct=4'sd0; end
        endcase
    end
    wire signed [8:0] final_pos_percent_adj =
        $signed({1'b0,final_runtime_percent_safe}) +
        {{5{final_pos_fric_corr_pct[3]}},final_pos_fric_corr_pct} +
        {{5{final_pair_side_corr_pct[3]}},final_pair_side_corr_pct};
    wire signed [8:0] final_neg_percent_adj =
        $signed({1'b0,final_runtime_percent_safe}) +
        {{5{final_neg_fric_corr_pct[3]}},final_neg_fric_corr_pct} +
        {{5{final_pair_side_corr_pct[3]}},final_pair_side_corr_pct};
    wire [7:0] final_pos_percent_eff =
        (final_pos_percent_adj < $signed({1'b0,ADAPT_MIN_PERCENT})) ? ADAPT_MIN_PERCENT :
        ((final_pos_percent_adj > $signed({1'b0,pump_percent_safe})) ? pump_percent_safe : final_pos_percent_adj[7:0]);
    wire [7:0] final_neg_percent_eff =
        (final_neg_percent_adj < $signed({1'b0,ADAPT_MIN_PERCENT})) ? ADAPT_MIN_PERCENT :
        ((final_neg_percent_adj > $signed({1'b0,pump_percent_safe})) ? pump_percent_safe : final_neg_percent_adj[7:0]);
    wire [15:0] final_pos_q15_u=({8'd0,final_pos_percent_eff}<<8)+({8'd0,final_pos_percent_eff}<<6)+({8'd0,final_pos_percent_eff}<<3);
    wire [15:0] final_neg_q15_u=({8'd0,final_neg_percent_eff}<<8)+({8'd0,final_neg_percent_eff}<<6)+({8'd0,final_neg_percent_eff}<<3);
    wire signed [15:0] final_pos_q15=$signed(final_pos_q15_u);
    wire signed [15:0] final_neg_q15=$signed(final_neg_q15_u);

    // 131 FAST ADAPTIVE timing-clean planner.
    // Use a private angle copy and registered plan so the new planner cannot
    // increase fanout on the already-critical real-time alpha path.
    reg [15:0] fast_plan_alpha_q4;
    reg [7:0] fast_stage_percent_reg;
    reg [7:0] fast_stage_pulse_ms_reg;
    reg [31:0] fast_stage_ad_limit_q20_reg;
    always @(posedge clk_50m or negedge rst_n) begin
        if (!rst_n) begin
            fast_plan_alpha_q4 <= 16'd0;
            fast_stage_percent_reg <= 8'd48;
            fast_stage_pulse_ms_reg <= 8'd110;
            fast_stage_ad_limit_q20_reg <= FAST_AD_300_Q20;
        end else begin
            fast_plan_alpha_q4 <= alpha_abs_q4_pipe2;
            if (tick_1k) begin
                if (fast_plan_alpha_q4 > FAST_A60_Q4) begin
                    fast_stage_percent_reg <= 8'd48;
                    fast_stage_pulse_ms_reg <= 8'd110;
                    fast_stage_ad_limit_q20_reg <= FAST_AD_300_Q20;
                end else if (fast_plan_alpha_q4 > FAST_A45_Q4) begin
                    fast_stage_percent_reg <= 8'd47;
                    fast_stage_pulse_ms_reg <= 8'd95;
                    fast_stage_ad_limit_q20_reg <= FAST_AD_260_Q20;
                end else if (fast_plan_alpha_q4 > FAST_A30_Q4) begin
                    fast_stage_percent_reg <= 8'd44;
                    fast_stage_pulse_ms_reg <= 8'd75;
                    fast_stage_ad_limit_q20_reg <= FAST_AD_220_Q20;
                end else if (fast_plan_alpha_q4 > FAST_A20_Q4) begin
                    fast_stage_percent_reg <= 8'd41;
                    fast_stage_pulse_ms_reg <= 8'd55;
                    fast_stage_ad_limit_q20_reg <= FAST_AD_170_Q20;
                end else begin
                    fast_stage_percent_reg <= 8'd36;
                    fast_stage_pulse_ms_reg <= 8'd40;
                    fast_stage_ad_limit_q20_reg <= FAST_AD_120_Q20;
                end
            end
        end
    end

    // FINAL trigger is expressed as swing amplitude from natural-down.
    // alpha_raw_delta_q4_pipe uses 16384 counts/revolution, so:
    //   |alpha|deg <= (180-TRIG)
    // is equivalent to:
    //   |alpha_q4| * 45 <= (180-TRIG) * 2048
    // This keeps the real-time path small (16/20-bit shift-add only).
    wire [7:0] final_trigger_deg_safe =
        (final_trigger_deg < 8'd60) ? 8'd60 :
        ((final_trigger_deg > 8'd150) ? 8'd150 : final_trigger_deg);
    wire [8:0] final_top_window_deg = 9'd180 - {1'b0,final_trigger_deg_safe};
    wire [15:0] alpha_abs_q4_final = alpha_abs_q4_pipe2;
    wire [19:0] alpha_abs_q4_w = {4'd0,alpha_abs_q4_final};
    wire [19:0] alpha_abs_q4_x45 =
        (alpha_abs_q4_w << 5) + (alpha_abs_q4_w << 3) +
        (alpha_abs_q4_w << 2) + alpha_abs_q4_w;
    wire [19:0] final_top_window_scaled =
        ({11'd0,final_top_window_deg} << 11);
    wire final_current_qual = 1'b0; // PULSE_TUNE: disable FINAL planner; calibrate source-faithful constant paired kicks only

    // 132 NORMAL true-peak detector, evaluated on the 1kHz control tick.
    // Previous sample: A and A_dot opposite signs => still moving toward upright.
    // Current sample: A and A_dot same sign => just started moving away.
    // Same-side + <160deg guard prevents the +/-180deg natural-down wrap from
    // being mistaken for a mechanical turning point. FINAL keeps priority.
    wire normal_peak_turn_now =
        normal_prev_valid &&
        (normal_prev_alpha_sign == alpha_sign_q4_pipe2) &&
        (normal_prev_ad_sign != normal_prev_alpha_sign) &&
        (alpha_dot_q20[31] == alpha_sign_q4_pipe2) &&
        (alpha_abs_q4_final < NORMAL_PEAK_WRAP_Q4) &&
        !final_current_qual;

    // Distance from the natural-down 0/1023 wrap in raw 10-bit codes.
    wire [10:0] bottom_excursion_raw11 =
        (adc_raw <= 10'd512) ? {1'b0,adc_raw} : (11'd1024 - {1'b0,adc_raw});
    wire [9:0] bottom_excursion_raw = bottom_excursion_raw11[9:0];

    wire [12:0] low13 = ({1'b0,top_center12} > {1'b0,EXTREMA_RANGE12}) ?
        ({1'b0,top_center12}-{1'b0,EXTREMA_RANGE12}) : 13'd0;
    wire [12:0] high13 = {1'b0,top_center12}+{1'b0,EXTREMA_RANGE12};
    wire [11:0] low12 = low13[11:0];
    wire [11:0] high12 = (high13 > 13'd4092) ? 12'd4092 : high13[11:0];

    wire bottom_ready = (adc_raw <= 10'd128) || (adc_raw >= 10'd896);

    wire [31:0] alpha_abs_q20 = alpha_q20[31] ? (~alpha_q20 + 32'd1) : alpha_q20;
    wire [31:0] ad_abs_q20 = alpha_dot_q20[31] ? (~alpha_dot_q20 + 32'd1) : alpha_dot_q20;
    wire [31:0] thd_abs_q20 = theta_dot_q20[31] ? (~theta_dot_q20 + 32'd1) : theta_dot_q20;

    wire near_top = (alpha_abs_q20 <= HANDOFF_ALPHA_Q20);
    wire handoff_window = near_top &&
        (ad_abs_q20 <= HANDOFF_AD_Q20) &&
        (thd_abs_q20 <= HANDOFF_THD_Q20);

    // 133: pre-handoff coast guard learned from 132 real hardware.
    // If we are already within 20deg, both speeds are inside the handoff limits,
    // and A/A_dot still show motion toward upright, do NOT fire another full FINAL.
    // Keep coasting so the existing 15deg/20ms handoff window can capture it.
    wire pre_handoff_coast =
        (alpha_abs_q20 <= FINAL_TOP_EXIT_Q20) &&
        (ad_abs_q20 <= HANDOFF_AD_Q20) &&
        (thd_abs_q20 <= HANDOFF_THD_Q20) &&
        (alpha_q20[31] != alpha_dot_q20[31]);

    wire true_top_cross_now =
        (alpha_sign_q4_pipe2 != adapt_prev_alpha_sign) &&
        ((alpha_abs_q4_final <= ADAPT_TOP_CROSS_Q4) ||
         (adapt_prev_alpha_abs_q4 <= ADAPT_TOP_CROSS_Q4));

    always @(posedge clk_50m or negedge rst_n) begin
        if (!rst_n) begin
            div_1k <= 16'd0;
            tick_1k <= 1'b0;
        end else if (div_1k == CLOCKS_PER_MS-1) begin
            div_1k <= 16'd0;
            tick_1k <= 1'b1;
        end else begin
            div_1k <= div_1k + 16'd1;
            tick_1k <= 1'b0;
        end
    end

    always @(posedge clk_50m or negedge rst_n) begin
        if (!rst_n) begin
            normal_prev_valid <= 1'b0;
            normal_prev_alpha_sign <= 1'b0;
            normal_prev_ad_sign <= 1'b0;
        end else if (tick_1k) begin
            // Keep history only while coasting in state1. Any pulse/START_KICK
            // clears it so a stale sign transition cannot create a false peak.
            if (!swing_active || (swing_state != 7'd1)) begin
                normal_prev_valid <= 1'b0;
                normal_prev_alpha_sign <= alpha_sign_q4_pipe2;
                normal_prev_ad_sign <= alpha_dot_q20[31];
            end else begin
                normal_prev_valid <= 1'b1;
                normal_prev_alpha_sign <= alpha_sign_q4_pipe2;
                normal_prev_ad_sign <= alpha_dot_q20[31];
            end
        end
    end

    always @(posedge clk_50m or negedge rst_n) begin
        if (!rst_n) swing_fric_zone <= 3'd7;
        else if (tick_1k) begin
            if ((swing_rel_encoder < -32'sd540) || (swing_rel_encoder > 32'sd540))
                swing_fric_zone <= 3'd7;
            else if (swing_rel_encoder <= -32'sd271) swing_fric_zone <= 3'd0; // nearest P1=-363
            else if (swing_rel_encoder <= -32'sd90)  swing_fric_zone <= 3'd1; // nearest P2=-179
            else if (swing_rel_encoder <=  32'sd87)  swing_fric_zone <= 3'd2; // nearest P3=0
            else if (swing_rel_encoder <=  32'sd264) swing_fric_zone <= 3'd3; // nearest P4=+175
            else if (swing_rel_encoder <=  32'sd444) swing_fric_zone <= 3'd4; // nearest P5=+353
            else                                      swing_fric_zone <= 3'd5; // nearest P6=+535
        end
    end

    always @(posedge clk_50m or negedge rst_n) begin
        if (!rst_n) begin
            swing_active <= 1'b0;
            handoff_request <= 1'b0;
            swing_duty_q15 <= 16'sd0;
            swing_state <= 6'd0;
            handoff_hold_ms <= 6'd0;
            sample_ms <= 6'd0;
            hist_valid <= 2'd0;
            final_hist_valid <= 2'd0;
            angle0 <= 12'd0;
            angle1 <= 12'd0;
            final_abs0 <= 16'hFFFF;
            final_abs1 <= 16'hFFFF;
            final_qual0 <= 1'b0;
            final_qual1 <= 1'b0;
            final_sign0 <= 1'b0;
            final_sign1 <= 1'b0;
            final_adc0 <= 12'd0;
            final_adc1 <= 12'd0;
            pulse_ms <= 8'd0;
            final_pulse_ms_work <= 8'd92; // async reset constant; runtime PULSE loads after reset
            start_wait_ms <= 7'd0;
            startup_peak_raw <= 10'd0;
            final_armed <= 1'b0;
            final_shot_done <= 1'b0;
            final_fail <= 1'b0;
            final_left_peak <= 1'b0;
            final_top_seen <= 1'b0;
            final_coast_ms <= 8'd0;
            prev_peak_abs_q4 <= 16'hFFFF;
            attempt_start_sign <= 1'b0;
            attempt_crossed_top <= 1'b0;
            lost_swing_count <= 2'd0;
            final_pair_first_valid <= 1'b0;
            final_pair_first_abs_q4 <= 16'hFFFF;
            adapt_prev_alpha_sign <= 1'b0;
            adapt_prev_alpha_abs_q4 <= 16'hFFFF;
            rotation_coast_active <= 1'b0;
            rotation_quiet_ms <= 12'd0;
            final_work_percent <= 8'd0;
            adaptive_retry_count <= 8'd0;
            adaptive_action <= 2'd0;
            started <= 1'b0;
            swing_home_encoder <= 32'sd0;
        // IMPORTANT: adc_otr is intentionally ignored while SWING is active.
        // Natural-down is mechanically aligned at the ADC 0/1023 wrap, where OTR can pulse.
        // The original V2.59 balance core still enforces its own OTR protection after handoff.
        end else if (!run_request || !zero_valid || estop_active) begin
            swing_active <= 1'b0;
            handoff_request <= 1'b0;
            swing_duty_q15 <= 16'sd0;
            swing_state <= 6'd0;
            handoff_hold_ms <= 6'd0;
            sample_ms <= 6'd0;
            hist_valid <= 2'd0;
            final_hist_valid <= 2'd0;
            angle0 <= 12'd0;
            angle1 <= 12'd0;
            final_abs0 <= 16'hFFFF;
            final_abs1 <= 16'hFFFF;
            final_qual0 <= 1'b0;
            final_qual1 <= 1'b0;
            final_sign0 <= 1'b0;
            final_sign1 <= 1'b0;
            final_adc0 <= 12'd0;
            final_adc1 <= 12'd0;
            pulse_ms <= 8'd0;
            swing_pair_index <= 2'd0;
            final_pulse_ms_work <= pulse_ms_safe;
            start_wait_ms <= 7'd0;
            startup_peak_raw <= 10'd0;
            final_armed <= 1'b0;
            final_shot_done <= 1'b0;
            final_fail <= 1'b0;
            final_left_peak <= 1'b0;
            final_top_seen <= 1'b0;
            final_coast_ms <= 8'd0;
            prev_peak_abs_q4 <= 16'hFFFF;
            attempt_start_sign <= 1'b0;
            attempt_crossed_top <= 1'b0;
            lost_swing_count <= 2'd0;
            final_pair_first_valid <= 1'b0;
            final_pair_first_abs_q4 <= 16'hFFFF;
            adapt_prev_alpha_sign <= 1'b0;
            adapt_prev_alpha_abs_q4 <= 16'hFFFF;
            rotation_coast_active <= 1'b0;
            rotation_quiet_ms <= 12'd0;
            final_work_percent <= 8'd0;
            adaptive_retry_count <= 8'd0;
            adaptive_action <= 2'd0;
            started <= 1'b0;
            swing_home_encoder <= 32'sd0;
        end else if (!started && bottom_ready) begin
            // Explicit START_KICK phase. Do not begin 3-point sampling until
            // the startup pair has produced a measurable excursion from bottom.
            swing_active <= 1'b1;
            handoff_request <= 1'b0;
            swing_duty_q15 <= 16'sd0;
            swing_state <= 6'd10;
            handoff_hold_ms <= 6'd0;
            sample_ms <= 6'd0;
            hist_valid <= 2'd0;
            final_hist_valid <= 2'd0;
            final_abs0 <= 16'hFFFF;
            final_abs1 <= 16'hFFFF;
            final_qual0 <= 1'b0;
            final_qual1 <= 1'b0;
            final_sign0 <= 1'b0;
            final_sign1 <= 1'b0;
            final_adc0 <= swing_adc12;
            final_adc1 <= swing_adc12;
            pulse_ms <= 8'd0;
            swing_pair_index <= 2'd0;
            final_pulse_ms_work <= pulse_ms_safe;
            start_wait_ms <= 7'd0;
            startup_peak_raw <= 10'd0;
            final_armed <= 1'b0;
            final_shot_done <= 1'b0;
            final_fail <= 1'b0;
            final_left_peak <= 1'b0;
            final_top_seen <= 1'b0;
            final_coast_ms <= 8'd0;
            prev_peak_abs_q4 <= 16'hFFFF;
            attempt_start_sign <= 1'b0;
            attempt_crossed_top <= 1'b0;
            lost_swing_count <= 2'd0;
            final_pair_first_valid <= 1'b0;
            final_pair_first_abs_q4 <= 16'hFFFF;
            adapt_prev_alpha_sign <= alpha_sign_q4_pipe2;
            adapt_prev_alpha_abs_q4 <= alpha_abs_q4_final;
            rotation_coast_active <= 1'b0;
            rotation_quiet_ms <= 12'd0;
            final_work_percent <= (final_pump_percent_safe > adaptive_max_percent) ?
                                  adaptive_max_percent : final_pump_percent_safe;
            adaptive_retry_count <= 8'd0;
            adaptive_action <= 2'd0;
            swing_home_encoder <= encoder_count;
            started <= 1'b1;
        end else if (tick_1k && swing_active) begin
            if ((swing_state >= 6'd10) && (swing_state <= 6'd14) &&
                (bottom_excursion_raw > startup_peak_raw))
                startup_peak_raw <= bottom_excursion_raw;

            // Adaptive FINAL phase:
            // after each FINAL pair, coast freely until the next real turning point.
            // Handoff to V2.59 BALANCE always has first priority.
            if (final_shot_done) begin
                swing_duty_q15 <= 16'sd0;
                swing_state <= rotation_coast_active ? 7'd80 : 7'd40; // ROT_COAST or ADAPT_COAST
                if (final_coast_ms != 8'hFF)
                    final_coast_ms <= final_coast_ms + 8'd1;

                // TRUE over-top detection. Alpha also wraps sign at natural bottom (+/-180deg),
                // so sign change alone is invalid. Count crossing only when consecutive 1ms
                // samples change sign with at least one sample inside +/-45deg of upright.
                if (true_top_cross_now) begin
                    attempt_crossed_top <= 1'b1;
                    rotation_quiet_ms <= 12'd0;
                    // First real top pass that misses handoff enters ROT_COAST.
                    // Reduce the NEXT FINAL once, then stop injecting energy.
                    if (!rotation_coast_active) begin
                        rotation_coast_active <= 1'b1;
                        adaptive_action <= 2'd3; // ROT_COAST
                        final_pair_first_valid <= 1'b0;
                        final_pair_first_abs_q4 <= 16'hFFFF;
                        lost_swing_count <= 2'd0;
                        if (final_work_percent > (ADAPT_MIN_PERCENT + ADAPT_OVERSHOOT_STEP_PERCENT))
                            final_work_percent <= final_work_percent - ADAPT_OVERSHOOT_STEP_PERCENT;
                        else
                            final_work_percent <= ADAPT_MIN_PERCENT;
                    end
                end else if (rotation_coast_active && (rotation_quiet_ms != 12'hFFF)) begin
                    rotation_quiet_ms <= rotation_quiet_ms + 12'd1;
                end
                adapt_prev_alpha_sign <= alpha_sign_q4_pipe2;
                adapt_prev_alpha_abs_q4 <= alpha_abs_q4_final;

                if (handoff_window) begin
                    if (handoff_hold_ms >= HANDOFF_HOLD_MS-1) begin
                        handoff_hold_ms <= HANDOFF_HOLD_MS;
                        swing_active <= 1'b0;
                        handoff_request <= 1'b1;
                        rotation_coast_active <= 1'b0;
                        rotation_quiet_ms <= 12'd0;
                        swing_state <= 7'd0;
                    end else handoff_hold_ms <= handoff_hold_ms + 6'd1;
                end else begin
                    handoff_hold_ms <= 6'd0;

                    if (ad_abs_q20 >= FINAL_MOVING_AD_Q20)
                        final_left_peak <= 1'b1;
                    if (alpha_abs_q20 <= FINAL_TOP_NEAR_Q20)
                        final_top_seen <= 1'b1;

                    // ROT_COAST has priority after any true over-top miss.
                    // Do NOT interpret the +/-180deg bottom-wrap derivative artifact as
                    // a turning point. Resume FINAL only after a genuine UPPER-HEMISPHERE
                    // turn: |A|<120deg and |AD|<=120deg/s.
                    if (rotation_coast_active || true_top_cross_now) begin
                        adaptive_action <= 2'd3; // ROT_COAST
                        if (rotation_coast_active && final_left_peak &&
                            (ad_abs_q20 <= FINAL_PEAK_AD_Q20) &&
                            (alpha_abs_q4_final < ADAPT_LOST_SWING_Q4) &&
                            (final_coast_ms >= 8'd120)) begin
                            rotation_coast_active <= 1'b0;
                            rotation_quiet_ms <= 12'd0;
                            adaptive_action <= 2'd0;
                            prev_peak_abs_q4 <= alpha_abs_q4_final;
                            attempt_start_sign <= alpha_sign_q4_pipe2;
                            attempt_crossed_top <= 1'b0;
                            adapt_prev_alpha_sign <= alpha_sign_q4_pipe2;
                            adapt_prev_alpha_abs_q4 <= alpha_abs_q4_final;
                            final_left_peak <= 1'b0;
                            final_top_seen <= 1'b0;
                            final_coast_ms <= 8'd0;
                            final_shot_done <= 1'b0;
                            final_pair_first_valid <= 1'b0;
                            final_pair_first_abs_q4 <= 16'hFFFF;
                            lost_swing_count <= 2'd0;
                            if (adaptive_retry_count != 8'hFF)
                                adaptive_retry_count <= adaptive_retry_count + 8'd1;
                            if (swing_adc12 >= top_center12)
                                swing_state <= 7'd51;
                            else
                                swing_state <= 7'd61;
                        end else if (rotation_coast_active &&
                                     (rotation_quiet_ms >= ROT_QUIET_RECOVER_MS) &&
                                     bottom_ready) begin
                            // Rotation has stopped producing true top crossings and has
                            // collapsed back to natural-down: rebuild with NORMAL.
                            rotation_coast_active <= 1'b0;
                            rotation_quiet_ms <= 12'd0;
                            adaptive_action <= 2'd2;
                            lost_swing_count <= 2'd0;
                            final_pair_first_valid <= 1'b0;
                            final_pair_first_abs_q4 <= 16'hFFFF;
                            final_armed <= 1'b0;
                            final_shot_done <= 1'b0;
                            hist_valid <= 2'd0;
                            final_hist_valid <= 2'd0;
                            final_abs0 <= 16'hFFFF;
                            final_abs1 <= 16'hFFFF;
                            final_qual0 <= 1'b0;
                            final_qual1 <= 1'b0;
                            sample_ms <= 6'd0;
                            pulse_ms <= 8'd0;
                            start_wait_ms <= 7'd0;
                            startup_peak_raw <= 10'd0;
                            swing_state <= 7'd70;
                        end
                    end
                    // Normal adaptive FINAL: only when not in ROT_COAST.
                    else if (final_left_peak && (ad_abs_q20 <= FINAL_PEAK_AD_Q20) &&
                        (final_coast_ms >= 8'd120) && !pre_handoff_coast) begin
                        prev_peak_abs_q4 <= alpha_abs_q4_final;
                        attempt_start_sign <= alpha_sign_q4_pipe2;
                        attempt_crossed_top <= 1'b0;
                        adapt_prev_alpha_sign <= alpha_sign_q4_pipe2;
                        adapt_prev_alpha_abs_q4 <= alpha_abs_q4_final;
                        final_left_peak <= 1'b0;
                        final_top_seen <= 1'b0;
                        final_coast_ms <= 8'd0;
                        final_shot_done <= 1'b0;
                        if (adaptive_retry_count != 8'hFF)
                            adaptive_retry_count <= adaptive_retry_count + 8'd1;

                        // A single low turning point is NOT enough to declare that swing energy
                        // is lost. After a true over-top pass, the very next turning point can
                        // naturally be low. Require TWO CONSECUTIVE real turning points with
                        // |A|>120deg before returning to NORMAL/START_KICK.
                        if ((alpha_abs_q4_final >= ADAPT_LOST_SWING_Q4) &&
                            (lost_swing_count >= 2'd1)) begin
                            adaptive_action <= 2'd2; // UP + RECOVER after second consecutive low peak
                            lost_swing_count <= 2'd0;
                            final_pair_first_valid <= 1'b0;
                            final_pair_first_abs_q4 <= 16'hFFFF;
                            if ((final_work_percent + ADAPT_STEP_PERCENT) < adaptive_max_percent)
                                final_work_percent <= final_work_percent + ADAPT_STEP_PERCENT;
                            else
                                final_work_percent <= adaptive_max_percent;
                            final_armed <= 1'b0;
                            final_shot_done <= 1'b0;
                            hist_valid <= 2'd0;
                            final_hist_valid <= 2'd0;
                            final_abs0 <= 16'hFFFF;
                            final_abs1 <= 16'hFFFF;
                            final_qual0 <= 1'b0;
                            final_qual1 <= 1'b0;
                            sample_ms <= 6'd0;
                            pulse_ms <= 8'd0;
                            start_wait_ms <= 7'd0;
                            startup_peak_raw <= 10'd0;
                            swing_state <= 7'd70; // WAIT_BOTTOM_FOR_NORMAL_RECOVERY
                        end else begin
                            // First low peak only arms the lost-swing confirmation.
                            // Any healthier peak immediately clears it.
                            if (alpha_abs_q4_final >= ADAPT_LOST_SWING_Q4)
                                lost_swing_count <= 2'd1;
                            else
                                lost_swing_count <= 2'd0;

                            // V5.11 explicit full-cycle staircase learned from the
                            // V5.10 golden success segment:
                            //   38,38,40,40,42,42,44,44,46 -> coast -> BALANCE.
                            // One FINAL level is therefore used for BOTH sides before
                            // we decide to climb. This avoids reacting to the known
                            // half-cycle friction / drive asymmetry.
                            if (attempt_crossed_top) begin
                                adaptive_action <= 2'd1; // DOWN FAST after a true top crossing
                                final_pair_first_valid <= 1'b0;
                                final_pair_first_abs_q4 <= 16'hFFFF;
                                lost_swing_count <= 2'd0;
                                if (final_work_percent > (ADAPT_MIN_PERCENT + ADAPT_OVERSHOOT_STEP_PERCENT))
                                    final_work_percent <= final_work_percent - ADAPT_OVERSHOOT_STEP_PERCENT;
                                else
                                    final_work_percent <= ADAPT_MIN_PERCENT;
                            end else if (alpha_abs_q4_final >= ADAPT_LOST_SWING_Q4) begin
                                // First confirmed low peak: try one stronger FINAL before
                                // the second-low-peak branch above is allowed to recover NORMAL.
                                adaptive_action <= 2'd2; // UP
                                final_pair_first_valid <= 1'b0;
                                final_pair_first_abs_q4 <= 16'hFFFF;
                                if ((final_work_percent + ADAPT_STEP_PERCENT) < adaptive_max_percent)
                                    final_work_percent <= final_work_percent + ADAPT_STEP_PERCENT;
                                else
                                    final_work_percent <= adaptive_max_percent;
                            end else if (!final_pair_first_valid) begin
                                // First healthy side at this FINAL level: HOLD and wait for
                                // the opposite side before changing force.
                                adaptive_action <= 2'd0; // HOLD
                                final_pair_first_valid <= 1'b1;
                                final_pair_first_abs_q4 <= alpha_abs_q4_final;
                                final_work_percent <= final_work_percent;
                            end else begin
                                // Second healthy side completes one full oscillation.
                                // Coarse-to-fine staircase:
                                //   worst side >30deg  -> +2%
                                //   worst side 15..30 -> +1%
                                //   both sides <=15deg -> HOLD/COAST
                                // This is faster than V5.10 far from top, but becomes
                                // deliberately gentle near the actual handoff window.
                                final_pair_first_valid <= 1'b0;
                                // 127 reliable 3-to-5-cycle energy taper:
                                // If BOTH sides are already inside 45deg, do not make the
                                // old 42->44 jump that produced a ~595deg/s top crossing.
                                // Allow exactly one fine rise 42->43, then HOLD 43 while
                                // both sides stay inside the soft zone. If either side falls
                                // back outside 45deg, the original coarse +2 recovery remains.
                                if ((final_pair_first_abs_q4 <= ADAPT_PAIR_SOFT_Q4) &&
                                    (alpha_abs_q4_final <= ADAPT_PAIR_SOFT_Q4) &&
                                    (final_work_percent >= 8'd43)) begin
                                    adaptive_action <= 2'd0; // SOFT HOLD at 43% or above
                                    final_work_percent <= final_work_percent;
                                end else if ((final_pair_first_abs_q4 <= ADAPT_PAIR_SOFT_Q4) &&
                                             (alpha_abs_q4_final <= ADAPT_PAIR_SOFT_Q4) &&
                                             (final_work_percent >= 8'd42)) begin
                                    adaptive_action <= 2'd2; // one fine 42->43 step only
                                    if ((final_work_percent + ADAPT_FINE_STEP_PERCENT) < adaptive_max_percent)
                                        final_work_percent <= final_work_percent + ADAPT_FINE_STEP_PERCENT;
                                    else
                                        final_work_percent <= adaptive_max_percent;
                                end else if ((final_pair_first_abs_q4 > ADAPT_PAIR_FINE_Q4) ||
                                             (alpha_abs_q4_final > ADAPT_PAIR_FINE_Q4)) begin
                                    adaptive_action <= 2'd2; // original coarse UP once per full cycle
                                    if ((final_work_percent + ADAPT_STEP_PERCENT) < adaptive_max_percent)
                                        final_work_percent <= final_work_percent + ADAPT_STEP_PERCENT;
                                    else
                                        final_work_percent <= adaptive_max_percent;
                                end else if ((final_pair_first_abs_q4 > ADAPT_PAIR_HOLD_Q4) ||
                                             (alpha_abs_q4_final > ADAPT_PAIR_HOLD_Q4)) begin
                                    adaptive_action <= 2'd2; // original fine UP +1% once per full cycle
                                    if ((final_work_percent + ADAPT_FINE_STEP_PERCENT) < adaptive_max_percent)
                                        final_work_percent <= final_work_percent + ADAPT_FINE_STEP_PERCENT;
                                    else
                                        final_work_percent <= adaptive_max_percent;
                                end else begin
                                    adaptive_action <= 2'd0; // BOTH SIDES INSIDE 15deg: HOLD
                                    final_work_percent <= final_work_percent;
                                end
                                final_pair_first_abs_q4 <= 16'hFFFF;
                            end

                            if (swing_adc12 >= top_center12)
                                swing_state <= 7'd51;
                            else
                                swing_state <= 7'd61;
                        end
                    end
                end

            // Never keep pumping through the strict top zone.
            end else if (near_top) begin
                swing_duty_q15 <= 16'sd0;
                pulse_ms <= 8'd0;
                sample_ms <= 6'd0;
                if (final_armed) begin
                    swing_state <= 7'd40;
                    final_shot_done <= 1'b1;
                    final_left_peak <= 1'b0;
                    final_top_seen <= 1'b1;
                    final_coast_ms <= 8'd0;
                end else begin
                    swing_state <= 7'd1;
                end
                if (handoff_window) begin
                    if (handoff_hold_ms >= HANDOFF_HOLD_MS-1) begin
                        handoff_hold_ms <= HANDOFF_HOLD_MS;
                        swing_active <= 1'b0;
                        handoff_request <= 1'b1;
                        swing_state <= 6'd0;
                    end else begin
                        handoff_hold_ms <= handoff_hold_ms + 6'd1;
                    end
                end else begin
                    handoff_hold_ms <= 6'd0;
                end
            end else begin
                handoff_hold_ms <= 6'd0;
                case (swing_state)
                    // Recovery after FINAL loses amplitude: wait until true natural-down,
                    // then restart the proven NORMAL kick/3-point resonant sequence.
                    7'd70: begin
                        swing_duty_q15 <= 16'sd0;
                        if (bottom_ready) begin
                            startup_peak_raw <= 10'd0;
                            start_wait_ms <= 7'd0;
                            pulse_ms <= 8'd0;
                            swing_pair_index <= 2'd0;
                            sample_ms <= 6'd0;
                            hist_valid <= 2'd0;
                            final_hist_valid <= 2'd0;
                            swing_state <= 7'd10;
                        end
                    end
                    // START_KICK: guaranteed one paired kick before any 3-point sampling.
                    6'd10: begin
                        swing_duty_q15 <= -pump_q15;
                        pulse_ms <= pulse_ms_safe;
                        swing_state <= 6'd11;
                    end
                    6'd11: begin
                        if (pulse_ms <= 8'd1) begin
                            pulse_ms <= 8'd0;
                            swing_state <= 6'd12;
                        end else pulse_ms <= pulse_ms - 8'd1;
                    end
                    6'd12: begin
                        swing_duty_q15 <= pump_q15;
                        pulse_ms <= pulse_ms_safe;
                        swing_state <= 6'd13;
                    end
                    6'd13: begin
                        if (pulse_ms <= 8'd1) begin
                            pulse_ms <= 8'd0;
                            swing_duty_q15 <= 16'sd0;
                            start_wait_ms <= START_WAIT_MS;
                            swing_pair_index <= 2'd1;
                            swing_state <= 6'd14;
                        end else pulse_ms <= pulse_ms - 8'd1;
                    end
                    6'd14: begin
                        swing_duty_q15 <= 16'sd0;
                        if (start_wait_ms <= 7'd1) begin
                            start_wait_ms <= 7'd0;
                            sample_ms <= 6'd0;
                            hist_valid <= 2'd0;
                            angle0 <= swing_adc12;
                            angle1 <= swing_adc12;
                            if ((startup_peak_raw >= START_MIN_EXCURSION_RAW) ||
                                (bottom_excursion_raw >= START_MIN_EXCURSION_RAW)) begin
                                swing_state <= 6'd1;
                            end else begin
                                // Too little motion: repeat START_KICK rather than
                                // entering a dead 3-point detector with flat samples.
                                startup_peak_raw <= 10'd0;
                                swing_state <= 6'd10;
                            end
                        end else start_wait_ms <= start_wait_ms - 7'd1;
                    end
                    6'd1: begin
                        swing_duty_q15 <= 16'sd0;
                        if (!final_armed &&
                            (final_hist_valid >= 2'd1) &&
                            final_current_qual &&
                            (final_sign0 == alpha_sign_q4_pipe2) &&
                            (alpha_abs_q4_final <= final_abs0) &&
                            // Explicitly require motion TOWARD upright.  This is the
                            // missing phase test in V1: A and A_dot must have opposite signs.
                            (alpha_sign_q4_pipe2 != alpha_dot_q20[31]) &&
                            (ad_abs_q20 <= fast_stage_ad_limit_q20_reg)) begin
                            final_armed <= 1'b1;
                            // Re-plan every HALF swing from current angle/speed.
                            final_work_percent <= (fast_stage_percent_reg > adaptive_max_percent) ?
                                                  adaptive_max_percent : fast_stage_percent_reg;
                            final_pulse_ms_work <= fast_stage_pulse_ms_reg;
                            handoff_hold_ms <= 6'd0;
                            prev_peak_abs_q4 <= alpha_abs_q4_final;
                            attempt_start_sign <= alpha_sign_q4_pipe2;
                            attempt_crossed_top <= 1'b0;
                            lost_swing_count <= 2'd0;
                            final_pair_first_valid <= 1'b0;
                            final_pair_first_abs_q4 <= 16'hFFFF;
                            rotation_coast_active <= 1'b0; rotation_quiet_ms <= 12'd0;
                            adapt_prev_alpha_sign <= alpha_sign_q4_pipe2;
                            adapt_prev_alpha_abs_q4 <= alpha_abs_q4_final;
                            adaptive_retry_count <= 8'd0; adaptive_action <= 2'd0;
                            sample_ms <= 6'd0;
                            if (swing_adc12 >= top_center12) swing_state <= 7'd51;
                            else swing_state <= 7'd61;
                        end else if (!final_armed && normal_peak_turn_now) begin
                            // 132: lock the NORMAL pulse to the real 1ms mechanical
                            // turning point instead of waiting for the 40ms 3-point
                            // sampler. This removes the random "miss P1, wait one more
                            // half swing" behavior seen on 131.
                            sample_ms <= 6'd0;
                            hist_valid <= 2'd0;
                            final_hist_valid <= 2'd0;
                            final_abs0 <= 16'hFFFF;
                            final_abs1 <= 16'hFFFF;
                            final_qual0 <= 1'b0;
                            final_qual1 <= 1'b0;
                            if (swing_adc12 >= top_center12)
                                swing_state <= 6'd21;
                            else
                                swing_state <= 6'd31;
                        end else if (sample_ms == SAMPLE_TERM_MS) begin
                            sample_ms <= 6'd0;

                            // Shift the exact same 40ms sample stream used by the
                            // source-faithful 3-point extremum detector.
                            angle1 <= angle0;
                            angle0 <= swing_adc12;
                            final_abs1 <= final_abs0;
                            final_abs0 <= alpha_abs_q4_final;
                            final_qual1 <= final_qual0;
                            final_qual0 <= final_current_qual;
                            final_sign1 <= final_sign0;
                            final_sign0 <= alpha_sign_q4_pipe2;
                            final_adc1 <= final_adc0;
                            final_adc0 <= swing_adc12;

                            if (hist_valid < 2'd2)
                                hist_valid <= hist_valid + 2'd1;
                            if (final_hist_valid < 2'd2)
                                final_hist_valid <= final_hist_valid + 2'd1;

                            if (final_hist_valid >= 2'd2 &&
                                // FINAL qualification:
                                // 1) ALL THREE 40ms samples exceed the configured
                                //    swing amplitude from bottom;
                                // 2) all three remain on the SAME side of upright,
                                //    so a high-speed top crossing is not mistaken for a peak;
                                // 3) the MIDDLE sample is the true local highest point
                                //    (minimum |A| of the three).
                                !final_armed &&
                                final_qual1 && final_qual0 && final_current_qual &&
                                (final_sign1 == final_sign0) &&
                                (final_sign0 == alpha_sign_q4_pipe2) &&
                                (final_abs0 <= final_abs1) &&
                                (final_abs0 <= alpha_abs_q4_final)) begin
                                final_armed <= 1'b1;
                                final_work_percent <= (fast_stage_percent_reg > adaptive_max_percent) ?
                                                      adaptive_max_percent : fast_stage_percent_reg;
                                final_pulse_ms_work <= fast_stage_pulse_ms_reg;
                                handoff_hold_ms <= 6'd0;
                                prev_peak_abs_q4 <= final_abs0;
                                attempt_start_sign <= final_sign0;
                                attempt_crossed_top <= 1'b0;
                                lost_swing_count <= 2'd0;
                                final_pair_first_valid <= 1'b0;
                                final_pair_first_abs_q4 <= 16'hFFFF;
                                rotation_coast_active <= 1'b0;
                                rotation_quiet_ms <= 12'd0;
                                adapt_prev_alpha_sign <= alpha_sign_q4_pipe2;
                                adapt_prev_alpha_abs_q4 <= alpha_abs_q4_final;
                                adaptive_retry_count <= 8'd0;
                                adaptive_action <= 2'd0;
                                if (final_adc0 >= top_center12)
                                    swing_state <= 7'd51;
                                else
                                    swing_state <= 7'd61;
                            end
                            // Otherwise preserve the original normal 3-point
                            // resonant extremum scheduling unchanged.
                            else if (!final_armed && (hist_valid >= 2'd2)) begin
                                if ((swing_adc12 > high12) && (angle0 > high12) && (angle1 > high12) &&
                                    (angle0 < swing_adc12) && (angle0 < angle1))
                                    swing_state <= 6'd21;
                                else if ((swing_adc12 < low12) && (angle0 < low12) && (angle1 < low12) &&
                                         (angle0 > swing_adc12) && (angle0 > angle1))
                                    swing_state <= 6'd31;
                            end
                        end else begin
                            sample_ms <= sample_ms + 6'd1;
                        end
                    end
                    6'd21: begin
                        swing_duty_q15 <= -pump_q15;
                        pulse_ms <= normal_pulse_ms_929291;
                        swing_state <= 6'd22;
                    end
                    6'd22: begin
                        if (pulse_ms <= 8'd1) begin
                            pulse_ms <= 8'd0;
                            swing_state <= 6'd23;
                        end else pulse_ms <= pulse_ms - 8'd1;
                    end
                    6'd23: begin
                        swing_duty_q15 <= pump_q15;
                        pulse_ms <= normal_pulse_ms_929291;
                        swing_state <= 6'd24;
                    end
                    6'd24: begin
                        if (pulse_ms <= 8'd1) begin
                            pulse_ms <= 8'd0;
                            swing_duty_q15 <= 16'sd0;
                            swing_state <= 6'd1;
                            if (swing_pair_index != 2'd3)
                                swing_pair_index <= swing_pair_index + 2'd1;
                            sample_ms <= 6'd0;
                            final_hist_valid <= 2'd0;
                            final_abs0 <= 16'hFFFF;
                            final_abs1 <= 16'hFFFF;
                            final_qual0 <= 1'b0;
                            final_qual1 <= 1'b0;
                        end else pulse_ms <= pulse_ms - 8'd1;
                    end
                    6'd31: begin
                        swing_duty_q15 <= pump_q15;
                        pulse_ms <= normal_pulse_ms_929291;
                        swing_state <= 6'd32;
                    end
                    6'd32: begin
                        if (pulse_ms <= 8'd1) begin
                            pulse_ms <= 8'd0;
                            swing_state <= 6'd33;
                        end else pulse_ms <= pulse_ms - 8'd1;
                    end
                    6'd33: begin
                        swing_duty_q15 <= -pump_q15;
                        pulse_ms <= normal_pulse_ms_929291;
                        swing_state <= 6'd34;
                    end
                    6'd34: begin
                        if (pulse_ms <= 8'd1) begin
                            pulse_ms <= 8'd0;
                            swing_duty_q15 <= 16'sd0;
                            swing_state <= 6'd1;
                            if (swing_pair_index != 2'd3)
                                swing_pair_index <= swing_pair_index + 2'd1;
                            sample_ms <= 6'd0;
                            final_hist_valid <= 2'd0;
                            final_abs0 <= 16'hFFFF;
                            final_abs1 <= 16'hFFFF;
                            final_qual0 <= 1'b0;
                            final_qual1 <= 1'b0;
                        end else pulse_ms <= pulse_ms - 8'd1;
                    end

                    // FINAL pair: same physical direction as the normal extremum pair,
                    // but uses independent FINAL_PUMP and can execute exactly once.
                    6'd51: begin
                        swing_duty_q15 <= -final_neg_q15;
                        pulse_ms <= final_pulse_ms_work;
                        swing_state <= 6'd52;
                    end
                    6'd52: begin
                        if (pulse_ms <= 8'd1) begin pulse_ms <= 8'd0; swing_state <= 6'd53; end
                        else pulse_ms <= pulse_ms - 8'd1;
                    end
                    6'd53: begin
                        swing_duty_q15 <= final_pos_q15;
                        pulse_ms <= final_pulse_ms_work;
                        swing_state <= 6'd54;
                    end
                    6'd54: begin
                        if (pulse_ms <= 8'd1) begin
                            pulse_ms <= 8'd0;
                            swing_duty_q15 <= 16'sd0;
                            final_shot_done <= 1'b1;
                            final_left_peak <= 1'b0;
                            final_top_seen <= 1'b0;
                            attempt_crossed_top <= 1'b0;
                            adapt_prev_alpha_sign <= alpha_sign_q4_pipe2;
                            adapt_prev_alpha_abs_q4 <= alpha_abs_q4_final;
                            final_coast_ms <= 8'd0;
                            swing_state <= 7'd40;
                        end else pulse_ms <= pulse_ms - 8'd1;
                    end
                    6'd61: begin
                        swing_duty_q15 <= final_pos_q15;
                        pulse_ms <= final_pulse_ms_work;
                        swing_state <= 6'd62;
                    end
                    6'd62: begin
                        if (pulse_ms <= 8'd1) begin pulse_ms <= 8'd0; swing_state <= 6'd63; end
                        else pulse_ms <= pulse_ms - 8'd1;
                    end
                    6'd63: begin
                        swing_duty_q15 <= -final_neg_q15;
                        pulse_ms <= final_pulse_ms_work;
                        swing_state <= 7'd64;
                    end
                    7'd64: begin
                        if (pulse_ms <= 8'd1) begin
                            pulse_ms <= 8'd0;
                            swing_duty_q15 <= 16'sd0;
                            final_shot_done <= 1'b1;
                            final_left_peak <= 1'b0;
                            final_top_seen <= 1'b0;
                            attempt_crossed_top <= 1'b0;
                            adapt_prev_alpha_sign <= alpha_sign_q4_pipe2;
                            adapt_prev_alpha_abs_q4 <= alpha_abs_q4_final;
                            final_coast_ms <= 8'd0;
                            swing_state <= 7'd40;
                        end else pulse_ms <= pulse_ms - 8'd1;
                    end
                    default: begin
                        swing_duty_q15 <= 16'sd0;
                        swing_state <= 6'd1;
                        sample_ms <= 6'd0;
                    end
                endcase
            end
        end
    end
endmodule

