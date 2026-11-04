// SWM3 shares one supervisor, observer and output across seven session-latched modes.
// Percentages and duration are latched once per explicit RUN session.
module j280_swing_source_swm4 #(
    parameter integer MODE = 0,
    parameter integer DYNAMIC_CONFIG = 0,
    parameter integer HANDOFF_HOLD_MS = 20,
    parameter integer REVERSE_MS = 220,
    parameter integer ALLOW_FULL_ROTATION = 0,
    parameter integer CLOCKS_PER_MS = 50000,
    parameter integer BRAKE_ON_ENCODER = 1,
    parameter integer DRIVE_NEGATIVE = 1,
    parameter integer PWM_ENCODER_SIGN = 1,
    parameter integer DRIVE_TRAVEL_COUNTS = 180,
    parameter integer WRONG_DIRECTION_COUNTS = 2,
    parameter integer QUALIFY_MS = 60,
    parameter integer WAIT_TIMEOUT_MS = 3000,
    parameter integer BRAKE_MAX_MS = 220,
    parameter integer FIXED_BRAKE_MS = 100,
    parameter integer COAST_MS = 1800,
    parameter integer MAX_ATTEMPTS = 3,
    parameter integer PREP_MS = 40,
    parameter integer PREP_WAIT_MS = 1200,
    parameter integer PREP_DISPLACE_Q4 = 256,
    parameter integer GLIDE_MS = 40,
    parameter integer SEED_MS = 30,
    parameter integer PUMP_MAX_MS = 3000,
    parameter integer RAMP_TICK_MS = 20,
    parameter integer RAMP_STEP_Q15 = 512,
    parameter integer SLEW_STEP_Q15 = 512,
    parameter integer PUMP_TRAVEL_COUNTS = 270,
    parameter integer PUMP_RETURN_COUNTS = 90,
    parameter integer PUMP_HARD_COUNTS = 450,
    parameter integer PHASE_PWM_SIGN = 1,
    parameter integer RAW_STEP_MIN_Q4 = 8,
    parameter integer FLIP_BRAKE_ANGLE_Q4 = 4096,
    parameter integer FLIP_RATE_OUTER_Q4 = 112,
    parameter integer FLIP_RATE_MID_Q4 = 80,
    parameter integer FLIP_RATE_INNER_Q4 = 48,
    parameter integer BRAKE_QUIET_MS = 12,
    parameter integer MOTION_FRESH_MS = 40,
    parameter integer LAP_REVERSE_Q4 = 48
) (
    input wire clk_50m, rst_n, run_request, zero_valid, estop_active, adc_otr,
    input wire [7:0] pump_percent, final_pump_percent, final_trigger_deg, pulse_ms_cfg,
    input wire [511:0] swing_config,
    input wire [31:0] configuration_revision,
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
    output reg [7:0] phase_end_reason,session_mode,
    output reg [31:0] session_revision
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
    localparam PUMP_PRECALC=(CLOCKS_PER_MS>=4);
    localparam integer ENCODER_AGE_BITS=DYNAMIC_CONFIG ? 8 : counter_width(BRAKE_QUIET_MS+1);
    localparam integer MOTION_AGE_BITS=DYNAMIC_CONFIG ? 8 : counter_width(MOTION_FRESH_MS+1);
    // These compile-time ranges match the finite counters below. Synthesis
    // removes this checker; any parameter change must also pass simulation.
    // synthesis translate_off
    initial begin
        if(HANDOFF_HOLD_MS<1 || HANDOFF_HOLD_MS>63 || CLOCKS_PER_MS<1 || (BRAKE_ON_ENCODER!=0 && BRAKE_ON_ENCODER!=1) ||
           (DRIVE_NEGATIVE!=0 && DRIVE_NEGATIVE!=1) ||
           (PWM_ENCODER_SIGN!=1 && PWM_ENCODER_SIGN!=-1) ||
           DRIVE_TRAVEL_COUNTS<0 || WRONG_DIRECTION_COUNTS<1 ||
           QUALIFY_MS<1 || QUALIFY_MS>65535 ||
           WAIT_TIMEOUT_MS<QUALIFY_MS || WAIT_TIMEOUT_MS>65535 ||
           BRAKE_MAX_MS<1 || BRAKE_MAX_MS>65535 ||
           FIXED_BRAKE_MS<1 || FIXED_BRAKE_MS>BRAKE_MAX_MS ||
           COAST_MS<20 || COAST_MS>65535 || MAX_ATTEMPTS<1 || MAX_ATTEMPTS>255 ||
           PREP_MS<1 || PREP_MS>65535 || PREP_WAIT_MS<1 || PREP_WAIT_MS>65535 ||
           PREP_DISPLACE_Q4<1 || PREP_DISPLACE_Q4>4096 || GLIDE_MS<1 || GLIDE_MS>65535 ||
           SEED_MS<1 || SEED_MS>65535 || PUMP_MAX_MS<SEED_MS || PUMP_MAX_MS>65535 ||
           RAMP_TICK_MS<1 || RAMP_TICK_MS>65535 || RAMP_STEP_Q15<1 || RAMP_STEP_Q15>31816 ||
           SLEW_STEP_Q15<1 || SLEW_STEP_Q15>31816 ||
           PUMP_RETURN_COUNTS<0 || PUMP_TRAVEL_COUNTS<=PUMP_RETURN_COUNTS ||
           PUMP_HARD_COUNTS<=PUMP_TRAVEL_COUNTS ||
           (PHASE_PWM_SIGN!=1 && PHASE_PWM_SIGN!=-1) ||
           RAW_STEP_MIN_Q4<1 || RAW_STEP_MIN_Q4>256 ||
           FLIP_BRAKE_ANGLE_Q4<684 || FLIP_BRAKE_ANGLE_Q4>6144 ||
           FLIP_RATE_INNER_Q4<1 || FLIP_RATE_MID_Q4<FLIP_RATE_INNER_Q4 ||
           FLIP_RATE_OUTER_Q4<FLIP_RATE_MID_Q4 || FLIP_RATE_OUTER_Q4>8191 ||
           BRAKE_QUIET_MS<4 || BRAKE_QUIET_MS>255 ||
           MOTION_FRESH_MS<1 || MOTION_FRESH_MS>255 ||
           LAP_REVERSE_Q4<32 || LAP_REVERSE_Q4>8191)
            $fatal(1,"Invalid swing-up compile-time parameter range");
    end
    // synthesis translate_on
    localparam [6:0] IDLE=0, QUALIFY=10, DRIVE=11, BRAKE=13,
        PREP=20, PREP_BRAKE=21, PREP_WAIT=22, GLIDE=30, SECOND=31,
        COAST=40, SEED=48, PUMP=50, PAIR_FORWARD=60, PAIR_REVERSE=61,
        PAIR_SETTLE=62, PEAK_WAIT=63, RETRY_WAIT=70, FAILED=90;
    localparam integer EXPECT_NEGATIVE = (DRIVE_NEGATIVE != 0) ^ (PWM_ENCODER_SIGN < 0);
    localparam signed [31:0] HANDOFF_ALPHA=32'sd274517;
    localparam signed [31:0] HANDOFF_AD=32'sd3660218;
    localparam signed [31:0] HANDOFF_THD=32'sd2196131;
    localparam signed [31:0] QUIET_RATE=32'sd366022;
    localparam signed [31:0] BOTTOM_ANGLE=32'sd2470649;
    localparam signed [31:0] HALF_PI=32'sd1647099;
    // Static MODE retains the old five-mode test mapping; production uses the bank.
    localparam CONFIG_VALID=(MODE>=0 && MODE<=4 &&
        (ALLOW_FULL_ROTATION==0 || ALLOW_FULL_ROTATION==1));
    reg [511:0] session_config;
    wire [7:0] configured_mode=DYNAMIC_CONFIG?swing_config[119:112]:MODE+1;
    wire [7:0] configured_drive=DYNAMIC_CONFIG?swing_config[7:0]:pump_percent;
    wire [7:0] configured_reverse=DYNAMIC_CONFIG?swing_config[15:8]:final_pump_percent;
    wire [15:0] configured_ms=DYNAMIC_CONFIG?swing_config[31:16]:{8'd0,pulse_ms_cfg};
    wire [15:0] configured_reverse_ms=DYNAMIC_CONFIG?swing_config[47:32]:BRAKE_MAX_MS;
    wire [15:0] qualify_limit=DYNAMIC_CONFIG?session_config[63:48]:QUALIFY_MS;
    wire [15:0] wait_limit=DYNAMIC_CONFIG?session_config[79:64]:WAIT_TIMEOUT_MS;
    wire [15:0] reverse_limit=DYNAMIC_CONFIG?session_config[47:32]:BRAKE_MAX_MS;
    wire [15:0] coast_limit=DYNAMIC_CONFIG?session_config[95:80]:COAST_MS;
    wire [7:0] attempt_limit=DYNAMIC_CONFIG?session_config[103:96]:MAX_ATTEMPTS;
    wire [7:0] retry_step=DYNAMIC_CONFIG?session_config[111:104]:8'd10;
    wire [7:0] mode_l=DYNAMIC_CONFIG?session_mode:MODE+1;
    wire [15:0] prep_limit=DYNAMIC_CONFIG?session_config[159:144]:PREP_MS;
    wire [15:0] prep_wait_limit=DYNAMIC_CONFIG?session_config[175:160]:PREP_WAIT_MS;
    wire [15:0] prep_displace_limit=DYNAMIC_CONFIG?session_config[191:176]:PREP_DISPLACE_Q4;
    wire [15:0] glide_limit=DYNAMIC_CONFIG?session_config[207:192]:GLIDE_MS;
    wire [15:0] seed_limit=DYNAMIC_CONFIG?session_config[223:208]:SEED_MS;
    wire [15:0] pump_limit=DYNAMIC_CONFIG?session_config[239:224]:PUMP_MAX_MS;
    wire [15:0] ramp_tick_limit=DYNAMIC_CONFIG?session_config[255:240]:RAMP_TICK_MS;
    wire [15:0] ramp_step_limit=DYNAMIC_CONFIG?session_config[271:256]:RAMP_STEP_Q15;
    wire [15:0] slew_step_limit=DYNAMIC_CONFIG?session_config[287:272]:SLEW_STEP_Q15;
    wire [15:0] travel_limit=DYNAMIC_CONFIG?session_config[303:288]:DRIVE_TRAVEL_COUNTS;
    wire [15:0] pump_travel_limit=DYNAMIC_CONFIG?session_config[319:304]:PUMP_TRAVEL_COUNTS;
    wire [15:0] pump_return_limit=DYNAMIC_CONFIG?session_config[335:320]:PUMP_RETURN_COUNTS;
    wire [15:0] pump_hard_limit=DYNAMIC_CONFIG?session_config[351:336]:PUMP_HARD_COUNTS;
    wire phase_pwm_negative=DYNAMIC_CONFIG?session_config[352]:(PHASE_PWM_SIGN<0);
    wire [7:0] brake_quiet_limit=DYNAMIC_CONFIG?session_config[375:368]:BRAKE_QUIET_MS;
    wire [7:0] motion_fresh_limit=DYNAMIC_CONFIG?session_config[391:384]:MOTION_FRESH_MS;
    wire [15:0] lap_reverse_limit=DYNAMIC_CONFIG?session_config[415:400]:LAP_REVERSE_Q4;
    wire [15:0] pair_limit=DYNAMIC_CONFIG?session_config[431:416]:16'd3;
    wire [15:0] pair_settle_limit=DYNAMIC_CONFIG?session_config[447:432]:16'd60;
    wire [15:0] peak_wait_limit=DYNAMIC_CONFIG?session_config[463:448]:16'd2000;
    wire [15:0] peak_excursion_limit=DYNAMIC_CONFIG?session_config[479:464]:16'd256;
    wire [15:0] raw_step_limit=DYNAMIC_CONFIG?session_config[495:480]:RAW_STEP_MIN_Q4;
    wire [15:0] flip_brake_angle_limit=DYNAMIC_CONFIG?session_config[511:496]:FLIP_BRAKE_ANGLE_Q4;
    wire signed [31:0] travel_signed=$signed({16'd0,travel_limit});
    wire signed [31:0] prep_travel_signed=$signed({15'd0,1'b0,travel_limit}+32'd1)>>>1;
    wire signed [16:0] raw_step_signed=$signed({1'b0,raw_step_limit});
    wire signed [16:0] slew_step_signed=$signed({1'b0,slew_step_limit});

    // The gateway range-checks every stored field. Only active-mode relationships
    // participate in RUN acceptance, so unrelated experimental settings cannot block mode0.
    wire configured_invalid=DYNAMIC_CONFIG ?
        (configured_mode>6 || configured_drive<1 || configured_drive>90 ||
         configured_reverse<1 || configured_reverse>90 ||
         configured_ms<1 || configured_ms>1000 ||
         configured_reverse_ms<1 || configured_reverse_ms>1000 ||
         swing_config[63:48]<1 || swing_config[63:48]>swing_config[79:64] ||
         swing_config[95:80]<20 || swing_config[103:96]<1 ||
         (configured_mode==3 && configured_ms<2) ||
         ((configured_mode==4 || configured_mode==5) &&
          (swing_config[223:208]>swing_config[239:224] ||
           swing_config[335:320]>=swing_config[319:304] ||
           swing_config[319:304]>=swing_config[351:336])) ||
         (configured_mode==5 && swing_config[127:120]!=1)) :
        (!CONFIG_VALID || (MODE==4 && ALLOW_FULL_ROTATION!=1) ||
         pulse_ms_cfg==0 || (MODE==2 && pulse_ms_cfg<2) ||
         pump_percent==0 || final_pump_percent==0);

    reg [DIVIDER_BITS-1:0] divider;
    wire tick_1ms = divider == CLOCKS_PER_MS-1;
    reg request_previous, session_started;
    reg [15:0] elapsed_ms, quiet_ms;
    reg [15:0] drive_ms;
    wire [16:0] retry_sum={1'b0,drive_ms}+{9'd0,retry_step};
    wire [15:0] minimum_drive=(mode_l==3)?16'd2:16'd1;
    wire [16:0] retry_floor_sum={1'b0,minimum_drive}+{9'd0,retry_step};
    reg [7:0] attempt_number;
    reg signed [15:0] drive_command, brake_command;
    reg signed [31:0] attempt_origin;
    reg forward_seen, speed_forward_seen, crossed_top;
    reg previous_alpha_sign;
    reg signed [13:0] previous_raw_q4;
    reg [15:0] previous_alpha_abs;
    reg signed [15:0] prep_command;
    reg [15:0] prep_start_abs, prep_min_abs;
    reg prep_displaced;
    reg prep_side_negative;
    reg [15:0] powered_ms;
    reg powered_below_limit_q,powered_at_end_q;
    wire powered_below_limit=PUMP_PRECALC ? powered_below_limit_q : powered_ms<drive_ms;
    wire powered_at_end=PUMP_PRECALC ? powered_at_end_q : powered_ms>=drive_ms-1;
    reg [15:0] pump_ms, ramp_ms, ramp_magnitude;
    reg arm_returning;
    reg [1:0] lap_stage;
    reg lap_negative;
    reg raw_motion_known, raw_motion_negative;
    reg signed [15:0] pend_window_origin;
    reg signed [16:0] pend_window_step;
    reg [1:0] pend_window_ms;
    reg signed [31:0] previous_encoder;
    reg [ENCODER_AGE_BITS-1:0] encoder_still_ms;
    reg [MOTION_AGE_BITS-1:0] motion_age_ms;
    reg [15:0] rise_anchor_abs;
    reg [16:0] lap_reverse_excursion;
    reg lap_left_top, lap_left_bottom;
    reg [4:0] pair_count;
    reg pair_negative,peak_rise_seen,peak_side_negative;

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
    // Signed ordering is unsigned ordering after the sign-bit bias. Three
    // parallel small comparisons avoid a carry chain on the live ADC sample.
    // Return {equal,less} so the opposite-point tests share one comparison.
    function [1:0] compare_raw14;
        input [13:0] left,right;
        reg [13:0] left_order,right_order;
        reg [2:0] less_chunk,equal_chunk;
        begin
            left_order={!left[13],left[12:0]};
            right_order={!right[13],right[12:0]};
            less_chunk={left_order[13:10]<right_order[13:10],
                        left_order[9:5]<right_order[9:5],
                        left_order[4:0]<right_order[4:0]};
            equal_chunk={left_order[13:10]==right_order[13:10],
                         left_order[9:5]==right_order[9:5],
                         left_order[4:0]==right_order[4:0]};
            compare_raw14={&equal_chunk,less_chunk[2] ||
                (equal_chunk[2] && less_chunk[1]) ||
                (equal_chunk[2] && equal_chunk[1] && less_chunk[0])};
            // Relational operators propagate any unknown input. Preserve that
            // simulation contract without synthesizing an X-detection circuit.
            // synthesis translate_off
            if ((^{left,right})===1'bx)
                compare_raw14={left==right,$signed(left)<$signed(right)};
            // synthesis translate_on
        end
    endfunction
    // Limits are unsigned16. Compute both signed branches in parallel and
    // select with the late displacement sign; never negate the live value.
    // Return {magnitude<=limit,magnitude>=limit}, preserving abs(INT_MIN)<0.
    function [1:0] compare_arm_magnitude;
        input signed [31:0] value;
        input [15:0] limit;
        reg [16:0] negative_sum;
        reg positive_far,negative_far,minimum_value;
        reg signed [31:0] reference_magnitude;
        begin
            // For the negative near-origin word, abs(value)=65536-low16.
            // One widened sum supplies both inequalities, including limit zero.
            negative_sum={1'b0,value[15:0]}+{1'b0,limit};
            positive_far=|value[30:16];negative_far=!(&value[30:16]);
            minimum_value=value==32'h80000000;
            compare_arm_magnitude[0]=!minimum_value && (value[31] ?
                (negative_far || !negative_sum[16] || negative_sum[15:0]==0) :
                (positive_far || value[15:0]>=limit));
            compare_arm_magnitude[1]=minimum_value || (value[31] ?
                (!negative_far && negative_sum[16]) :
                (!positive_far && value[15:0]<=limit));
            // synthesis translate_off
            if((^{value,limit})===1'bx) begin
                reference_magnitude=value<0 ? -value : value;
                compare_arm_magnitude={reference_magnitude<=$signed({16'd0,limit}),
                                       reference_magnitude>=$signed({16'd0,limit})};
            end
            // synthesis translate_on
        end
    endfunction
    reg signed [14:0] peak_lower_q,peak_upper_q,flip_lower_q,flip_upper_q;
    reg signed [14:0] rise_lower_q,rise_upper_q,anchor_lower_q,anchor_upper_q;
    reg signed [14:0] prep_displace_lower_q,prep_displace_upper_q;
    reg signed [14:0] prep_return_lower_q,prep_return_upper_q,prep_min_lower_q,prep_min_upper_q;
    reg rise_bound_valid_q,prep_displace_valid_q;
    // Modular subtraction handles signed32 wrap for these short trajectories.
    wire signed [31:0] displacement = encoder_count-attempt_origin;
    wire forward_displacement = EXPECT_NEGATIVE ? displacement < 0 : displacement > 0;
    wire wrong_displacement = EXPECT_NEGATIVE ? displacement >= WRONG_DIRECTION_COUNTS :
                                                           displacement <= -WRONG_DIRECTION_COUNTS;
    wire travel_reached = travel_limit > 0 &&
        (EXPECT_NEGATIVE ? displacement <= -travel_signed : displacement >= travel_signed);
    wire signed [31:0] encoder_step = encoder_count-previous_encoder;
    // Sign of exact32-bit modular subtraction: bit31 XOR low31 borrow.
    // Four parallel small comparisons avoid a full subtract-then-zero/sign chain.
    wire encoder_same=encoder_count==previous_encoder;
    wire [3:0] encoder_less_chunk={encoder_count[30:24]<previous_encoder[30:24],
        encoder_count[23:16]<previous_encoder[23:16],encoder_count[15:8]<previous_encoder[15:8],
        encoder_count[7:0]<previous_encoder[7:0]};
    wire [3:0] encoder_equal_chunk={encoder_count[30:24]==previous_encoder[30:24],
        encoder_count[23:16]==previous_encoder[23:16],encoder_count[15:8]==previous_encoder[15:8],
        encoder_count[7:0]==previous_encoder[7:0]};
    wire encoder_lower_borrow=encoder_less_chunk[3] ||
        (encoder_equal_chunk[3] && encoder_less_chunk[2]) ||
        (encoder_equal_chunk[3] && encoder_equal_chunk[2] && encoder_less_chunk[1]) ||
        (encoder_equal_chunk[3] && encoder_equal_chunk[2] && encoder_equal_chunk[1] && encoder_less_chunk[0]);
    wire encoder_step_negative=encoder_count[31]^previous_encoder[31]^encoder_lower_borrow;
    wire encoder_step_positive=!encoder_same && !encoder_step_negative;
    wire actual_reverse_step = EXPECT_NEGATIVE ? encoder_step_positive : encoder_step_negative;
    wire actual_prep_reverse_step = EXPECT_NEGATIVE ? encoder_step_negative : encoder_step_positive;
    wire encoder_quiet = encoder_same && encoder_still_ms>={1'b0,brake_quiet_limit}-1 &&
        theta_dot_q20>=-QUIET_RATE && theta_dot_q20<=QUIET_RATE;
    wire speed_forward = EXPECT_NEGATIVE ? theta_dot_q20 < 0 : theta_dot_q20 > 0;
    wire speed_stopped_or_reversed = speed_forward_seen && !speed_forward;
    wire near_top = alpha_q20 >= -HANDOFF_ALPHA && alpha_q20 <= HANDOFF_ALPHA;
    wire capture_window = near_top && alpha_dot_q20 >= -HANDOFF_AD &&
        alpha_dot_q20 <= HANDOFF_AD && theta_dot_q20 >= -HANDOFF_THD && theta_dot_q20 <= HANDOFF_THD;
    wire quiet_bottom = ((adc_raw <= 128)||(adc_raw >= 896)) &&
        ((alpha_q20 <= -BOTTOM_ANGLE)||(alpha_q20 >= BOTTOM_ANGLE)) &&
        alpha_dot_q20 >= -QUIET_RATE && alpha_dot_q20 <= QUIET_RATE &&
        theta_dot_q20 >= -QUIET_RATE && theta_dot_q20 <= QUIET_RATE;
    wire signed [14:0] raw_position={alpha_raw_delta_q4[13],alpha_raw_delta_q4[13:0]};
    wire [13:0] alpha_abs_narrow=alpha_raw_delta_q4[13] ? -alpha_raw_delta_q4[13:0] : alpha_raw_delta_q4[13:0];
    wire [15:0] alpha_abs={2'b0,alpha_abs_narrow};
    wire true_top_cross = previous_alpha_sign != alpha_raw_delta_q4[15] &&
        ((raw_position>=-15'sd2048 && raw_position<=15'sd2048)||(previous_alpha_abs <= 16'd2048));
    wire moving_phase = swing_state==DRIVE || swing_state==BRAKE || swing_state==COAST ||
        swing_state==PREP || swing_state==PREP_BRAKE || swing_state==PREP_WAIT ||
        swing_state==GLIDE || swing_state==SECOND || swing_state==SEED || swing_state==PUMP ||
        swing_state==PAIR_FORWARD || swing_state==PAIR_REVERSE || swing_state==PAIR_SETTLE || swing_state==PEAK_WAIT;
    wire prep_forward = EXPECT_NEGATIVE ? displacement>0 : displacement<0;
    wire prep_wrong = EXPECT_NEGATIVE ? displacement<=-WRONG_DIRECTION_COUNTS :
                                                       displacement>=WRONG_DIRECTION_COUNTS;
    wire prep_speed_forward = EXPECT_NEGATIVE ? theta_dot_q20>0 : theta_dot_q20<0;
    wire prep_stopped = speed_forward_seen && !prep_speed_forward;
    wire prep_travel = travel_limit>0 &&
        (EXPECT_NEGATIVE ? displacement>=prep_travel_signed : displacement<=-prep_travel_signed);
    wire [15:0] first_stage_ms = (drive_ms>>1)==0 ? 16'd1 : (drive_ms>>1);
    wire signed [31:0] alpha_magnitude = alpha_q20<0 ? -alpha_q20 : alpha_q20;
    wire [1:0] arm_travel_order=compare_arm_magnitude(displacement,pump_travel_limit);
    wire [1:0] arm_return_order=compare_arm_magnitude(displacement,pump_return_limit);
    wire [1:0] arm_hard_order=compare_arm_magnitude(displacement,pump_hard_limit);
    // The protected core's wrap_q4 output is signed14: -8192 through8191.
    // Subtraction modulo16384 needs only fourteen bits. The exact positive
    // half-turn keeps +8192; the negative half-turn keeps -8192 as before.
    wire [13:0] raw_step_mod=alpha_raw_delta_q4[13:0]-previous_raw_q4;
    wire positive_half_turn=(raw_step_mod==14'h2000) &&
        !alpha_raw_delta_q4[13] && previous_raw_q4[13];
    wire signed [16:0] raw_step={{3{raw_step_mod[13] && !positive_half_turn}},raw_step_mod};
    // Prepare the deadband boundaries from the previous sample. Current raw
    // position then selects direction without a subtraction followed by a compare.
    // The opposite point is a sign-bit toggle, so it needs no arithmetic/register.
    reg signed [14:0] raw_positive_min_q,raw_negative_max_q;
    wire signed [14:0] previous_raw_position={previous_raw_q4[13],previous_raw_q4};
    wire signed [14:0] raw_threshold_position=$signed({6'b0,raw_step_limit[8:0]});
    wire signed [14:0] raw_opposite_position=
        {!previous_raw_q4[13],!previous_raw_q4[13],previous_raw_q4[12:0]};
    wire signed [14:0] raw_positive_wrapped=
        {raw_positive_min_q[13],raw_positive_min_q[13:0]};
    wire signed [14:0] raw_negative_wrapped=
        {raw_negative_max_q[13],raw_negative_max_q[13:0]};
    // A deadband crossing the bottom wrap also excludes its wrapped tail.
    wire positive_boundary_wrapped=raw_positive_min_q[14:13]==2'b01;
    wire negative_boundary_wrapped=raw_negative_max_q[14:13]==2'b10;
    wire [1:0] raw_positive_order=compare_raw14(raw_position[13:0],raw_positive_wrapped[13:0]);
    wire [1:0] raw_opposite_order=compare_raw14(raw_position[13:0],raw_opposite_position[13:0]);
    wire [1:0] raw_negative_order=compare_raw14(raw_position[13:0],raw_negative_wrapped[13:0]);
    wire positive_start_ok=!raw_positive_order[0];
    wire positive_end_ok=previous_raw_q4[13] ? (raw_opposite_order[0] || raw_opposite_order[1]) :
                                                                 raw_opposite_order[0];
    wire negative_start_ok=previous_raw_q4[13] ? (!raw_opposite_order[0] && !raw_opposite_order[1]) :
                                                                   !raw_opposite_order[0];
    wire negative_end_ok=raw_negative_order[0] || raw_negative_order[1];
    wire raw_step_negative=PUMP_PRECALC ?
        ((!previous_raw_q4[13] || negative_boundary_wrapped) ?
         (negative_start_ok && negative_end_ok) : (negative_start_ok || negative_end_ok)) :
        raw_step<=-raw_step_signed;
    wire raw_step_positive=PUMP_PRECALC ?
        ((previous_raw_q4[13] || positive_boundary_wrapped) ?
         (positive_start_ok && positive_end_ok) : (positive_start_ok || positive_end_ok)) :
        raw_step>=raw_step_signed;
    wire raw_motion_now = raw_step_negative || raw_step_positive;
    wire pendulum_negative = raw_motion_now ? raw_step_negative : raw_motion_negative;
    wire motion_fresh = raw_motion_now || (raw_motion_known && motion_age_ms<motion_fresh_limit);
    wire measured_positive=raw_step_positive && !raw_step_negative;
    wire remembered_motion_fresh=!raw_motion_now && raw_motion_known && motion_age_ms<motion_fresh_limit;
    reg motion_rising,motion_returning;
    always @* begin
        // Select the required direction directly instead of selecting a sign
        // and then comparing it again after the live circular comparisons.
        motion_returning=(alpha_raw_delta_q4[15] ? raw_step_negative : measured_positive) ||
            (remembered_motion_fresh && alpha_raw_delta_q4[15]==raw_motion_negative);
        motion_rising=(alpha_raw_delta_q4[15] ? measured_positive : raw_step_negative) ||
            (remembered_motion_fresh && alpha_raw_delta_q4[15]!=raw_motion_negative);
        // synthesis translate_off
        if((^{alpha_raw_delta_q4[15],raw_step_negative,raw_step_positive,
               raw_motion_negative,raw_motion_known,motion_age_ms,motion_fresh_limit})===1'bx) begin
            motion_returning=motion_fresh && (alpha_raw_delta_q4[15]==pendulum_negative);
            motion_rising=motion_fresh && (alpha_raw_delta_q4[15]!=pendulum_negative);
        end
        // synthesis translate_on
    end
    // Local circular motion avoids the unchanged observer's wrap-rate transient.
    // Excursion and freshness prevent a remembered sign alone from launching a pulse.
    wire pendulum_rising = motion_rising &&
        (PUMP_PRECALC ? (rise_bound_valid_q && raw_position>=rise_lower_q && raw_position<=rise_upper_q) :
         {1'b0,rise_anchor_abs}>={1'b0,alpha_abs}+17'd32);
    wire pendulum_returning = motion_returning;
    wire [13:0] pend_window_step_mod=alpha_raw_delta_q4[13:0]-pend_window_origin[13:0];
    wire window_positive_half_turn=(pend_window_step_mod==14'h2000) &&
        !alpha_raw_delta_q4[13] && pend_window_origin[13];
    wire signed [16:0] pend_window_wrapped=
        {{3{pend_window_step_mod[13] && !window_positive_half_turn}},pend_window_step_mod};
    wire upper_half = raw_position>=-15'sd4096 && raw_position<=15'sd4096;
    // PHASE_PWM_SIGN is an explicit experimental actuator/pendulum sign assumption.
    // This is a quadrant phase law; it is not a complete energy-model controller.
    wire phase_negative = ((!pendulum_negative)==upper_half) ^ phase_pwm_negative;
    wire return_negative = (displacement>0) ^ (PWM_ENCODER_SIGN<0);
    wire arm_needs_return = arm_returning || arm_travel_order[0];
    wire flip_approach_region = mode_l==5 && lap_stage==2 && (PUMP_PRECALC ?
        (raw_position>=flip_lower_q && raw_position<=flip_upper_q) : alpha_abs<=flip_brake_angle_limit);
    wire [13:0] flip_speed_limit = (raw_position>=15'sd2048 || raw_position<=-15'sd2048) ? FLIP_RATE_OUTER_Q4 :
        ((raw_position>=15'sd1365 || raw_position<=-15'sd1365) ? FLIP_RATE_MID_Q4 : FLIP_RATE_INNER_Q4);
    wire flip_energy_brake = flip_approach_region && !arm_needs_return &&
        (pend_window_step>$signed({1'b0,flip_speed_limit}) ||
         pend_window_step< -$signed({1'b0,flip_speed_limit}));
    wire pump_negative = swing_state==SEED ? (DRIVE_NEGATIVE!=0) :
        arm_needs_return ?
        return_negative : (flip_approach_region ? !phase_negative :
        (!(raw_motion_known || raw_motion_now) ? (DRIVE_NEGATIVE!=0) : phase_negative));
    wire [15:0] drive_magnitude = drive_command[15] ? -drive_command : drive_command;
    wire [15:0] brake_magnitude = brake_command[15] ? -brake_command : brake_command;
    wire [15:0] flip_brake_magnitude = ramp_magnitude<brake_magnitude ? ramp_magnitude : brake_magnitude;
    // The approach envelope is empirical: remove energy above the local speed
    // threshold, coast below it, and never command another boost in this region.
    wire [15:0] pump_magnitude = swing_state==SEED || arm_needs_return ? ramp_magnitude :
        !motion_fresh ? 16'd0 : flip_approach_region ?
        (flip_energy_brake ? flip_brake_magnitude : 16'd0) : ramp_magnitude;
    wire signed [15:0] pump_target = pump_negative ? -$signed(pump_magnitude) :
                                                                  $signed(pump_magnitude);
    wire signed [16:0] pump_error = {pump_target[15],pump_target}-
                                                   {swing_duty_q15[15],swing_duty_q15};
    wire signed [16:0] slewed_pump = pump_error>slew_step_signed ?
        $signed({swing_duty_q15[15],swing_duty_q15})+slew_step_signed :
        (pump_error< -slew_step_signed ?
         $signed({swing_duty_q15[15],swing_duty_q15})-slew_step_signed :
         $signed({pump_target[15],pump_target}));
    // Only the slow state (updated on1ms ticks) enters this two-clock preparation.
    // Current raw measurements remain combinational selection inputs at the original
    // tick. Four-clock simulations leave two settled clocks; the real design leaves
    // 49998. Very small simulation divider values use the original combinational law.
    reg signed [16:0] slew_upper_q,slew_lower_q;
    reg signed [15:0] ramp_positive_q,ramp_negative_q,brake_positive_q,brake_negative_q;
    reg signed [15:0] slew_ramp_positive_q,slew_ramp_negative_q;
    reg signed [15:0] slew_brake_positive_q,slew_brake_negative_q,slew_zero_q;
    wire [15:0] peak_room=16'd8192-peak_excursion_limit;
    wire [14:0] peak_bound=peak_room>16'd7281 ? 15'd7281 : peak_room[14:0];
    wire [15:0] rise_bound=rise_anchor_abs-16'd32;
    wire [15:0] prep_displace_bound=prep_start_abs-prep_displace_limit;
    wire [15:0] prep_return_bound=prep_min_abs+16'd32;
    reg [16:0] lap_reverse_room_q;
    reg signed [16:0] lap_reverse_negative_room_q;
    function signed [15:0] slew_candidate;
        input signed [15:0] target;
        input signed [16:0] lower_bound,upper_bound;
        reg signed [16:0] extended;
        begin
            extended={target[15],target};
            if(extended>upper_bound) slew_candidate=upper_bound[15:0];
            else if(extended<lower_bound) slew_candidate=lower_bound[15:0];
            else slew_candidate=target;
        end
    endfunction
    always @(posedge clk_50m or negedge rst_n) begin
        if(!rst_n) begin
            powered_below_limit_q<=0;powered_at_end_q<=0;
            slew_upper_q<=0;slew_lower_q<=0;
            ramp_positive_q<=0;ramp_negative_q<=0;brake_positive_q<=0;brake_negative_q<=0;
            slew_ramp_positive_q<=0;slew_ramp_negative_q<=0;
            slew_brake_positive_q<=0;slew_brake_negative_q<=0;slew_zero_q<=0;
            lap_reverse_room_q<=0;lap_reverse_negative_room_q<=0;
            raw_positive_min_q<=15'sd1;raw_negative_max_q<=-15'sd1;
            peak_lower_q<=0;peak_upper_q<=0;flip_lower_q<=0;flip_upper_q<=0;
            rise_lower_q<=0;rise_upper_q<=0;anchor_lower_q<=0;anchor_upper_q<=0;
            prep_displace_lower_q<=0;prep_displace_upper_q<=0;
            prep_return_lower_q<=0;prep_return_upper_q<=0;prep_min_lower_q<=0;prep_min_upper_q<=0;
            rise_bound_valid_q<=0;prep_displace_valid_q<=0;
        end else begin
            // Both counters change only on control ticks. Prepare these two
            // shared booleans between ticks so late raw motion never enters
            // their arithmetic comparator. The original comparison widths stay.
            powered_below_limit_q<=powered_ms<drive_ms;
            powered_at_end_q<=powered_ms>=drive_ms-1;
            raw_positive_min_q<=previous_raw_position+raw_threshold_position;
            raw_negative_max_q<=previous_raw_position-raw_threshold_position;
            peak_upper_q<=peak_bound;peak_lower_q<=-$signed(peak_bound);
            flip_upper_q<=flip_brake_angle_limit[14:0];flip_lower_q<=-$signed(flip_brake_angle_limit[14:0]);
            rise_bound_valid_q<=rise_anchor_abs>=16'd32;
            rise_upper_q<=rise_bound[14:0];rise_lower_q<=-$signed(rise_bound[14:0]);
            anchor_upper_q<=rise_anchor_abs[14:0];anchor_lower_q<=-$signed(rise_anchor_abs[14:0]);
            prep_displace_valid_q<=prep_start_abs>=prep_displace_limit;
            prep_displace_upper_q<=prep_displace_bound[14:0];prep_displace_lower_q<=-$signed(prep_displace_bound[14:0]);
            prep_return_upper_q<=prep_return_bound[14:0];prep_return_lower_q<=-$signed(prep_return_bound[14:0]);
            prep_min_upper_q<=prep_min_abs[14:0];prep_min_lower_q<=-$signed(prep_min_abs[14:0]);
            slew_upper_q<=$signed({swing_duty_q15[15],swing_duty_q15})+slew_step_signed;
            slew_lower_q<=$signed({swing_duty_q15[15],swing_duty_q15})-slew_step_signed;
            ramp_positive_q<=ramp_magnitude;ramp_negative_q<=-$signed(ramp_magnitude);
            brake_positive_q<=flip_brake_magnitude;brake_negative_q<=-$signed(flip_brake_magnitude);
            slew_ramp_positive_q<=slew_candidate(ramp_positive_q,slew_lower_q,slew_upper_q);
            slew_ramp_negative_q<=slew_candidate(ramp_negative_q,slew_lower_q,slew_upper_q);
            slew_brake_positive_q<=slew_candidate(brake_positive_q,slew_lower_q,slew_upper_q);
            slew_brake_negative_q<=slew_candidate(brake_negative_q,slew_lower_q,slew_upper_q);
            slew_zero_q<=slew_candidate(16'sd0,slew_lower_q,slew_upper_q);
            if(lap_reverse_excursion>={1'b0,lap_reverse_limit}) begin
                lap_reverse_room_q<=0;lap_reverse_negative_room_q<=0;
            end else begin
                lap_reverse_room_q<={1'b0,lap_reverse_limit}-lap_reverse_excursion;
                lap_reverse_negative_room_q<=-$signed({1'b0,lap_reverse_limit}-lap_reverse_excursion);
            end
        end
    end
    wire select_ramp=(swing_state==SEED) || arm_needs_return ||
        (motion_fresh && !flip_approach_region);
    wire select_brake=motion_fresh && flip_approach_region && flip_energy_brake;
    wire signed [15:0] prepared_pump=select_ramp ?
        (pump_negative?slew_ramp_negative_q:slew_ramp_positive_q) :
        select_brake ? (pump_negative?slew_brake_negative_q:slew_brake_positive_q) : slew_zero_q;
    wire signed [15:0] next_pump_duty=PUMP_PRECALC?prepared_pump:slewed_pump[15:0];
    wire valid_top_event = true_top_cross && raw_position>=-15'sd2048 && raw_position<=15'sd2048 && previous_alpha_abs<=2048 &&
        alpha_magnitude<=HALF_PI &&
        (alpha_raw_delta_q4[15] ? raw_step_negative : raw_step_positive);
    wire valid_bottom_event = previous_alpha_sign!=alpha_raw_delta_q4[15] &&
        (raw_position>=15'sd6144 || raw_position<=-15'sd6144) && previous_alpha_abs>=6144 &&
        alpha_magnitude>=BOTTOM_ANGLE;
    wire lap_rate_forward = lap_negative ? raw_step_negative : raw_step_positive;
    wire lap_rate_reverse = lap_negative ? raw_step_positive : raw_step_negative;
    wire [16:0] raw_step_magnitude = raw_step[16] ? -raw_step : raw_step;
    wire [17:0] next_lap_reverse = {1'b0,lap_reverse_excursion}+{1'b0,raw_step_magnitude};
    wire lap_reverse_confirmed = lap_rate_reverse && (PUMP_PRECALC ?
        (lap_reverse_room_q==0 || raw_step>=$signed(lap_reverse_room_q) || raw_step<=lap_reverse_negative_room_q) :
        next_lap_reverse>={2'd0,lap_reverse_limit});
    wire lap_bottom_forward = lap_negative ? (!alpha_raw_delta_q4[15] && previous_alpha_sign) :
                                                 (alpha_raw_delta_q4[15] && !previous_alpha_sign);
    // Raw landmarks may lead the unchanged Q20 observer by one tick. They only
    // authorize zero-output coasting; the original Q20 capture gate still applies.
    wire lap_approach = raw_position>=-15'sd683 && raw_position<=15'sd683 && lap_rate_forward &&
        (lap_negative ? !alpha_raw_delta_q4[15] : alpha_raw_delta_q4[15]);
    wire flip_lap_invalid = mode_l==5 && (lap_stage==1 || lap_stage==2) &&
        // Values beyond8192 are unreachable under the protected core's signed14 contract.
        (lap_reverse_confirmed ||
         (valid_bottom_event && lap_stage==2 && lap_left_bottom) ||
         (valid_top_event && lap_stage==1 && lap_left_top));
    wire capture_allowed = mode_l!=5 || lap_stage==3;
    // A real same-side turning point is required after a qualifying rise.
    // The 160-degree exclusion prevents bottom wrap and low-amplitude jitter from kicking.
    wire peak_region=PUMP_PRECALC ? (raw_position>=peak_lower_q && raw_position<=peak_upper_q) :
        (alpha_abs<16'd7282 && {1'b0,alpha_abs}+{1'b0,peak_excursion_limit}<=17'd8192);
    wire peak_rising_now=raw_motion_now && previous_alpha_sign==alpha_raw_delta_q4[15] &&
        (alpha_raw_delta_q4[15]!=raw_step_negative) &&
        (PUMP_PRECALC ? (rise_bound_valid_q && raw_position>=rise_lower_q && raw_position<=rise_upper_q) :
         {1'b0,rise_anchor_abs}>={1'b0,alpha_abs}+17'd32);
    wire peak_returning_now=raw_motion_now && (alpha_raw_delta_q4[15]==raw_step_negative);
    assign phase_elapsed_ms=elapsed_ms;
    assign effective_forward_ms=drive_ms;
    assign session_reverse_ms=reverse_limit;
    assign swing_adc12 = {adc_raw,2'b00};
    assign top_center12 = adc_zero_q4[13:2];

    // No additional OTR policy: R10 deliberately ignores it at bottom wrap.
    // final_trigger_deg is reserved for the old resonant method and unused here.
    always @(posedge clk_50m or negedge rst_n) begin
        if(!rst_n) divider<=0;
        else if(tick_1ms) divider<=0;
        else divider<=divider+1'b1;
    end

    // These observer registers update before the main capture/failure decision.
    // Keep their enables independent of that late decision: moving states and
    // qualification are mutually exclusive, so no output supervisor is needed.
    // STOP/fault retain their old values; only reset and explicit session or
    // attempt initialization clear them, matching the original priority.
    always @(posedge clk_50m or negedge rst_n) begin
        if(!rst_n) begin
            motion_age_ms<=MOTION_FRESH_MS;
            lap_left_top<=0;lap_left_bottom<=0;
        end else if(!run_request) begin
        end else if(!zero_valid || estop_active) begin
        end else if(!request_previous && !session_started) begin
            motion_age_ms<=motion_fresh_limit;
            lap_left_top<=0;lap_left_bottom<=0;
        end else if(tick_1ms && swing_active && !final_fail) begin
            if(moving_phase) begin
                if(raw_motion_now) motion_age_ms<=0;
                else if(motion_age_ms<motion_fresh_limit) motion_age_ms<=motion_age_ms+1'b1;
                if(mode_l==5) begin
                    if(lap_stage==0 && valid_top_event) begin
                        lap_left_top<=0;lap_left_bottom<=0;
                    end
                    if(lap_stage==1 && (raw_position>15'sd2048 || raw_position<-15'sd2048)) lap_left_top<=1;
                    if(lap_stage==2 && raw_position>-15'sd6144 && raw_position<15'sd6144) lap_left_bottom<=1;
                end
            end else if(swing_state==QUALIFY || swing_state==RETRY_WAIT) begin
                if(quiet_bottom && quiet_ms>=qualify_limit-1) begin
                    motion_age_ms<=motion_fresh_limit;
                    lap_left_top<=0;lap_left_bottom<=0;
                end
            end
        end
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

    task begin_main;
        begin
            attempt_origin<=encoder_count;
            elapsed_ms<=0; forward_seen<=0; speed_forward_seen<=0;
            powered_ms<=0; swing_state<=DRIVE; swing_duty_q15<=drive_command;
        end
    endtask

    task end_main_drive;
        begin
            elapsed_ms<=0;phase_end_reason<=21;
            if(speed_stopped_or_reversed || actual_reverse_step ||
               (encoder_quiet && (forward_seen || forward_displacement))) begin enter_coast;phase_end_reason<=13;end
            else if(forward_seen || forward_displacement) begin
                swing_state<=BRAKE; swing_duty_q15<=brake_command;
            end else fail_session(15);
        end
    endtask

    task end_preparation;
        begin
            elapsed_ms<=0;phase_end_reason<=21;
            if(prep_stopped || actual_prep_reverse_step ||
               (encoder_quiet && (forward_seen || prep_forward))) begin
                swing_state<=PREP_WAIT; swing_duty_q15<=0;
            end else if(forward_seen || prep_forward) begin
                swing_state<=PREP_BRAKE; swing_duty_q15<=-brake_command;
            end else fail_session(15);
        end
    endtask

    always @(posedge clk_50m or negedge rst_n) begin
        if(!rst_n) begin
            session_config<=0;session_mode<=0;session_revision<=0;phase_end_reason<=0;
            pair_count<=0;pair_negative<=0;peak_rise_seen<=0;peak_side_negative<=0;
            request_previous<=1; session_started<=0;
            swing_active<=0; handoff_request<=0; swing_duty_q15<=0; swing_state<=IDLE;
            handoff_hold_ms<=0; final_armed<=0; final_shot_done<=0; final_fail<=0;
            final_work_percent<=0; adaptive_retry_count<=0; adaptive_action<=0;
            elapsed_ms<=0; quiet_ms<=0; drive_ms<=0; attempt_number<=0;
            drive_command<=0; brake_command<=0; attempt_origin<=0;
            forward_seen<=0; speed_forward_seen<=0; crossed_top<=0;
            previous_alpha_sign<=0; previous_alpha_abs<=0;previous_raw_q4<=0;
            prep_command<=0; prep_start_abs<=0;
            prep_min_abs<=0; prep_displaced<=0; prep_side_negative<=0; powered_ms<=0;
            pump_ms<=0; ramp_ms<=0; ramp_magnitude<=0; arm_returning<=0;
            lap_stage<=0; lap_negative<=0; raw_motion_known<=0; raw_motion_negative<=0;
            pend_window_origin<=0; pend_window_step<=0; pend_window_ms<=0;
            previous_encoder<=0; encoder_still_ms<=0;
            rise_anchor_abs<=0; lap_reverse_excursion<=0;
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
                fail_session(estop_active?8'd7:8'd6);
            end else if(!request_previous && !session_started) begin
                session_config<=swing_config;session_mode<=configured_mode;
                session_revision<=configuration_revision;phase_end_reason<=0;
                session_started<=1; swing_active<=1; handoff_request<=0;
                swing_duty_q15<=0; swing_state<=QUALIFY; handoff_hold_ms<=0;
                final_armed<=0; final_shot_done<=0; final_fail<=0;
                adaptive_retry_count<=0; adaptive_action<=0; attempt_number<=0;
                elapsed_ms<=0; quiet_ms<=0; drive_ms<=configured_ms;
                drive_command <= DRIVE_NEGATIVE ? -percent_q15(configured_drive) : percent_q15(configured_drive);
                brake_command <= DRIVE_NEGATIVE ? percent_q15(configured_reverse) : -percent_q15(configured_reverse);
                prep_command <= DYNAMIC_CONFIG ?
                    (DRIVE_NEGATIVE ? percent_q15(swing_config[135:128]) : -percent_q15(swing_config[135:128])) :
                    (DRIVE_NEGATIVE ? (percent_q15(configured_drive)>>>1) : -(percent_q15(configured_drive)>>>1));
                final_work_percent<=safe_percent(configured_reverse);
                previous_alpha_sign<=alpha_raw_delta_q4[15]; previous_alpha_abs<=alpha_abs;
                previous_raw_q4<=alpha_raw_delta_q4[13:0];
                crossed_top<=0; forward_seen<=0; speed_forward_seen<=0;
                lap_stage<=0; lap_negative<=0; raw_motion_known<=0; raw_motion_negative<=0;
                pend_window_origin<=alpha_raw_delta_q4; pend_window_step<=0; pend_window_ms<=0;
                previous_encoder<=encoder_count; encoder_still_ms<=0;
                rise_anchor_abs<=alpha_abs;
                lap_reverse_excursion<=0;
                if(configured_invalid) fail_session(12);
            end else if(tick_1ms && swing_active && !final_fail) begin
                if(moving_phase) begin
                    previous_alpha_sign<=alpha_raw_delta_q4[15]; previous_alpha_abs<=alpha_abs;
                previous_raw_q4<=alpha_raw_delta_q4[13:0];
                    previous_encoder<=encoder_count;
                    if(!encoder_same) encoder_still_ms<=0;
                    else if(encoder_still_ms<brake_quiet_limit) encoder_still_ms<=encoder_still_ms+1'b1;
                    if(previous_alpha_sign!=alpha_raw_delta_q4[15] || (PUMP_PRECALC ?
                        (raw_position<anchor_lower_q || raw_position>anchor_upper_q) : alpha_abs>rise_anchor_abs))
                        rise_anchor_abs<=alpha_abs;
                    if(raw_motion_now) begin
                        raw_motion_known<=1; raw_motion_negative<=raw_step_negative;
                    end
                    pend_window_ms<=pend_window_ms+1'b1;
                    if(pend_window_ms==3) begin
                        pend_window_step<=pend_window_wrapped;
                        pend_window_origin<=alpha_raw_delta_q4;
                    end
                    if(true_top_cross) crossed_top<=1;
                    if(mode_l==5) begin
                        if(lap_stage==0 && valid_top_event) begin
                            lap_stage<=1; lap_negative<=raw_step_negative;
                            lap_reverse_excursion<=0;
                        end else if(lap_stage==1 && valid_bottom_event &&
                                    lap_bottom_forward && lap_rate_forward) lap_stage<=2;
                        else if(lap_stage==2 && lap_approach) lap_stage<=3;
                        if(lap_stage==1 || lap_stage==2) begin
                            if(lap_rate_forward) begin
                                if(raw_step_magnitude>=lap_reverse_excursion) lap_reverse_excursion<=0;
                                else lap_reverse_excursion<=lap_reverse_excursion-raw_step_magnitude;
                            end
                            else if(lap_rate_reverse) lap_reverse_excursion<=next_lap_reverse[16:0];
                        end
                    end
                    if(mode_l==2 && (swing_state==PREP || swing_state==PREP_BRAKE || swing_state==PREP_WAIT)) begin
                        if(PUMP_PRECALC ? (raw_position>prep_min_lower_q && raw_position<prep_min_upper_q) :
                            alpha_abs<prep_min_abs) prep_min_abs<=alpha_abs;
                        if(PUMP_PRECALC ? (prep_displace_valid_q && raw_position>=prep_displace_lower_q && raw_position<=prep_displace_upper_q) :
                            {1'b0,alpha_abs}+{1'b0,prep_displace_limit}<={1'b0,prep_start_abs}) begin
                            prep_displaced<=1;
                            if(!prep_displaced) prep_side_negative<=alpha_raw_delta_q4[15];
                        end
                    end
                end
                if(flip_lap_invalid && moving_phase) fail_session(18);
                else if(moving_phase && capture_allowed && capture_window) begin
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
                                forward_seen<=0; speed_forward_seen<=0; crossed_top<=0;
                                previous_alpha_sign<=alpha_raw_delta_q4[15]; previous_alpha_abs<=alpha_abs;
                previous_raw_q4<=alpha_raw_delta_q4[13:0];
                                final_armed<=1; final_shot_done<=0;
                                prep_start_abs<=alpha_abs; prep_min_abs<=alpha_abs; prep_displaced<=0;
                                prep_side_negative<=alpha_raw_delta_q4[15];
                                powered_ms<=0; pump_ms<=0; ramp_ms<=0; arm_returning<=0;
                                lap_stage<=0; lap_negative<=0; raw_motion_known<=0; raw_motion_negative<=0;
                                pend_window_origin<=alpha_raw_delta_q4; pend_window_step<=0; pend_window_ms<=0;
                                previous_encoder<=encoder_count; encoder_still_ms<=0;
                                rise_anchor_abs<=alpha_abs;
                                lap_reverse_excursion<=0;
                                phase_end_reason<=0;pair_count<=0;peak_rise_seen<=0;
                                if(mode_l==6) begin
                                    pair_count<=1;pair_negative<=DRIVE_NEGATIVE!=0;
                                    swing_state<=PAIR_FORWARD;swing_duty_q15<=drive_command;
                                end else if(mode_l==2) begin
                                    swing_state<=PREP; swing_duty_q15<=prep_command;
                                end else if((mode_l==4 || mode_l==5)) begin
                                    swing_state<=SEED; swing_duty_q15<=0;
                                    ramp_magnitude<=drive_magnitude>>2;
                                end else begin
                                    swing_state<=DRIVE; swing_duty_q15<=drive_command;
                                end
                            end else if(elapsed_ms>=wait_limit-1) fail_session(10);
                        end
                        DRIVE: begin
                            elapsed_ms<=elapsed_ms+1'b1;
                            if(mode_l==3) powered_ms<=powered_ms+1'b1;
                            if(forward_displacement) forward_seen<=1;
                            if(speed_forward) speed_forward_seen<=1;
                            if(wrong_displacement) fail_session(8);
                            else if(mode_l==0) begin
                                if(elapsed_ms>=drive_ms-16'd1) begin
                                    elapsed_ms<=0;swing_state<=BRAKE;
                                    swing_duty_q15<=brake_command;phase_end_reason<=1;
                                end
                            end else if(near_top) end_main_drive;
                            else if(mode_l==3 && !travel_reached && elapsed_ms>=first_stage_ms-1) begin
                                swing_state<=GLIDE; swing_duty_q15<=0; elapsed_ms<=0;
                            end else if(travel_reached || elapsed_ms>=drive_ms-1) begin
                                elapsed_ms<=0;
                                if(speed_stopped_or_reversed || actual_reverse_step ||
                                   (encoder_quiet && (forward_seen || forward_displacement))) begin
                                    // A delayed cutoff can occur after the arm has already
                                    // stopped. Reverse PWM would then accelerate the return.
                                    swing_state<=COAST; swing_duty_q15<=0; final_shot_done<=1;
                                end else if(forward_seen || forward_displacement) begin
                                    swing_state<=BRAKE; swing_duty_q15<=brake_command;
                                end else fail_session(15);
                            end
                        end
                        BRAKE: begin
                            elapsed_ms<=elapsed_ms+1'b1;
                            if(mode_l==0) begin
                                if(elapsed_ms>=reverse_limit-16'd1) begin
                                    enter_coast;phase_end_reason<=2;
                                end
                            end else if(speed_stopped_or_reversed || actual_reverse_step ||
                               (BRAKE_ON_ENCODER!=0 && encoder_quiet) ||
                               (BRAKE_ON_ENCODER==0 && elapsed_ms>=FIXED_BRAKE_MS-1)) begin
                                swing_duty_q15<=0; swing_state<=COAST; elapsed_ms<=0;
                                final_shot_done<=1;phase_end_reason<=13;
                            end else if(elapsed_ms>=reverse_limit-1) fail_session(14);
                        end
                        PREP: begin
                            elapsed_ms<=elapsed_ms+1'b1;
                            if(prep_forward) forward_seen<=1;
                            if(prep_speed_forward) speed_forward_seen<=1;
                            if(prep_wrong) fail_session(8);
                            else if(near_top || prep_travel || elapsed_ms>=prep_limit-1) end_preparation;
                        end
                        PREP_BRAKE: begin
                            elapsed_ms<=elapsed_ms+1'b1;
                            if(prep_stopped || actual_prep_reverse_step || encoder_quiet) begin
                                swing_state<=PREP_WAIT; swing_duty_q15<=0; elapsed_ms<=0;
                            end else if(elapsed_ms>=reverse_limit-1) fail_session(14);
                        end
                        PREP_WAIT: begin
                            swing_duty_q15<=0; elapsed_ms<=elapsed_ms+1'b1;
                            if(prep_displaced && pendulum_returning &&
                               alpha_raw_delta_q4[15]==prep_side_negative &&
                               (PUMP_PRECALC ? (raw_position<=prep_return_lower_q || raw_position>=prep_return_upper_q) :
                                {1'b0,alpha_abs}>={1'b0,prep_min_abs}+17'd32)) begin_main;
                            else if(elapsed_ms>=prep_wait_limit-1) fail_session(16);
                        end
                        GLIDE: begin
                            swing_duty_q15<=0; elapsed_ms<=elapsed_ms+1'b1;
                            if(speed_forward) speed_forward_seen<=1;
                            if(near_top) end_main_drive;
                            else if(wrong_displacement) fail_session(8);
                            else if(travel_reached || speed_stopped_or_reversed || actual_reverse_step)
                                end_main_drive;
                            else if(elapsed_ms>=glide_limit-1) begin
                                elapsed_ms<=0;
                                if(pendulum_rising && powered_below_limit) begin
                                    swing_state<=SECOND; swing_duty_q15<=drive_command;
                                end else end_main_drive;
                            end
                        end
                        SECOND: begin
                            elapsed_ms<=elapsed_ms+1'b1; powered_ms<=powered_ms+1'b1;
                            if(forward_displacement) forward_seen<=1;
                            if(speed_forward) speed_forward_seen<=1;
                            if(wrong_displacement) fail_session(8);
                            else if(near_top) end_main_drive;
                            else if(travel_reached || speed_stopped_or_reversed || actual_reverse_step ||
                                    !pendulum_rising || powered_at_end) end_main_drive;
                        end
                        SEED: begin
                            elapsed_ms<=elapsed_ms+1'b1; pump_ms<=pump_ms+1'b1;
                            swing_duty_q15<=(mode_l==4 && near_top) ? 16'sd0 : next_pump_duty;
                            if(forward_displacement) forward_seen<=1;
                            if(speed_forward) speed_forward_seen<=1;
                            if(wrong_displacement) fail_session(8);
                            else if(arm_hard_order[0]) fail_session(17);
                            else if(elapsed_ms>=seed_limit-1) begin
                                if(forward_seen || forward_displacement) begin
                                    // Seed is part of the same total pumping budget.
                                    if(pump_ms>=pump_limit-1) begin enter_coast;phase_end_reason<=22;end
                                    else begin swing_state<=PUMP; elapsed_ms<=0;end
                                end else fail_session(15);
                            end
                        end
                        PUMP: begin
                            elapsed_ms<=elapsed_ms+1'b1;
                            pump_ms<=pump_ms+1'b1; ramp_ms<=ramp_ms+1'b1;
                            if(ramp_ms>=ramp_tick_limit-1) begin
                                ramp_ms<=0;
                                if({1'b0,ramp_magnitude}+{1'b0,ramp_step_limit}>={1'b0,drive_magnitude})
                                    ramp_magnitude<=drive_magnitude;
                                else ramp_magnitude<=ramp_magnitude+{1'b0,ramp_step_limit};
                            end
                            if(arm_travel_order[0]) arm_returning<=1;
                            else if(arm_return_order[1]) arm_returning<=0;
                            // A fast top passage pauses this attempt without committing
                            // to a long coast. Only the unchanged capture gate hands over.
                            swing_duty_q15<=(mode_l==4 && near_top) ? 16'sd0 : next_pump_duty;
                            if(arm_hard_order[0]) fail_session(17);
                            else if(mode_l==5 && (lap_stage==3 || (lap_stage==2 && lap_approach))) enter_coast;
                            else if(pump_ms>=pump_limit-1) begin enter_coast;phase_end_reason<=22;end
                        end
                        PAIR_FORWARD: begin
                            elapsed_ms<=elapsed_ms+1'b1;
                            swing_duty_q15<=near_top?16'sd0:
                                (pair_negative?-$signed(drive_magnitude):$signed(drive_magnitude));
                            // The preceding half-pulse may leave legitimate opposing inertia.
                            if(elapsed_ms>=drive_ms-16'd1) begin
                                swing_state<=PAIR_REVERSE;elapsed_ms<=0;phase_end_reason<=1;
                                swing_duty_q15<=near_top?16'sd0:
                                    (pair_negative?$signed(brake_magnitude):-$signed(brake_magnitude));
                            end
                        end
                        PAIR_REVERSE: begin
                            elapsed_ms<=elapsed_ms+1'b1;
                            swing_duty_q15<=near_top?16'sd0:
                                (pair_negative?$signed(brake_magnitude):-$signed(brake_magnitude));
                            if(elapsed_ms>=reverse_limit-16'd1) begin
                                swing_state<=PAIR_SETTLE;elapsed_ms<=0;phase_end_reason<=2;
                                swing_duty_q15<=0;peak_rise_seen<=0;
                            end
                        end
                        PAIR_SETTLE: begin
                            swing_duty_q15<=0;elapsed_ms<=elapsed_ms+1'b1;
                            if(elapsed_ms>=pair_settle_limit-16'd1) begin
                                elapsed_ms<=0;peak_rise_seen<=0;
                                if({11'd0,pair_count}>=pair_limit) begin
                                    enter_coast;phase_end_reason<=20;
                                end else begin swing_state<=PEAK_WAIT;phase_end_reason<=21;end
                            end
                        end
                        PEAK_WAIT: begin
                            swing_duty_q15<=0;elapsed_ms<=elapsed_ms+1'b1;
                            if(!peak_region || !motion_fresh ||
                               alpha_raw_delta_q4[15]!=peak_side_negative) peak_rise_seen<=0;
                            if(peak_region && peak_rising_now) begin
                                peak_rise_seen<=1;peak_side_negative<=alpha_raw_delta_q4[15];
                            end
                            if(peak_region && peak_rise_seen && peak_returning_now &&
                               alpha_raw_delta_q4[15]==peak_side_negative) begin
                                pair_negative<=!alpha_raw_delta_q4[15];pair_count<=pair_count+1'b1;
                                attempt_origin<=encoder_count;elapsed_ms<=0;peak_rise_seen<=0;
                                swing_state<=PAIR_FORWARD;phase_end_reason<=0;
                                // Suppression applies on the transition edge as well as inside the pair.
                                swing_duty_q15<=near_top ? 16'sd0 :
                                    (alpha_raw_delta_q4[15] ? $signed(drive_magnitude) : -$signed(drive_magnitude));
                            end else if(elapsed_ms>=peak_wait_limit-16'd1) fail_session(19);
                        end
                        COAST: begin
                            swing_duty_q15<=0; elapsed_ms<=elapsed_ms+1'b1;
                            if(elapsed_ms>=coast_limit-1) begin
                                if(attempt_number>=attempt_limit) fail_session(11);
                                else begin
                                    if(mode_l==4 || mode_l==5) adaptive_action<=0;
                                    else if(crossed_top || true_top_cross) begin
                                        drive_ms<={1'b0,drive_ms}>retry_floor_sum ?
                                            drive_ms-{8'd0,retry_step} : minimum_drive;
                                        adaptive_action<=1;
                                    end else begin
                                        // Legacy static regressions retain their original255ms ceiling.
                                        if(DYNAMIC_CONFIG)
                                            drive_ms<=retry_sum>17'd1000 ? 16'd1000 : retry_sum[15:0];
                                        else drive_ms<=retry_sum>17'd255 ? 16'd255 : retry_sum[15:0];
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
