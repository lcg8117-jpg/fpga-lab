module j280_adc_capture_trace #(
    parameter integer DIAGNOSTIC_SWITCHES = 0,
    parameter integer QUIET_CACHE_WORDS = 1,
    parameter integer QUIET_GUARD_CYCLES = 160,
    parameter integer QUIET_MAX_AGE_CYCLES = 1500
) (
    input wire clk_50m,
    input wire rst_n,
    input wire [9:0] adc_data_in,
    input wire average_enable,
    input wire trace_adc_otr,
    input wire [31:0] trace_actuator_diag,
    input wire [2:0] trace_motor_pins, // {BN1,BN2,PWMB}, actual logical outputs
    output wire adc_clk,
    output wire adc_oe_n,
    output reg [9:0] adc_raw,
    output wire [9:0] adc_clean_stage,
    output reg [13:0] adc_filtered_q4,
    output reg trace_valid,
    output reg [239:0] trace_record,
    output wire [31:0] trace_time_counter
);
    // OFFLINE candidate: 3PA1030 clock3.125MHz; same16samples/1ms processing.
    // Official RevA.1 Figure22 shows datachanges afterADC falling edge.
    // Capture atgenerated risingedge160ns afterlastfall, nominal135ns after
    // tOD25ns TYP. No guaranteed max/board delays => no certifiedI/Oclosure.
    //
    // The old filter averaged 256 consecutive 50 MHz fabric samples, which
    // covered only 5.12 us and therefore did almost nothing to the low-rate
    // angle jitter that contaminated AlphaDot.  This version takes 16 samples
    // over 1 ms (one sample every 62.5 us).
    //
    // The WDD35D/ADC value legitimately wraps 1023 -> 0.  A normal arithmetic
    // average would produce a false value near 512 when a 1 ms batch straddles
    // that wrap.  Every sample is therefore unwrapped relative to the first
    // sample of the batch before averaging, then the Q4 result is wrapped back
    // modulo 1024.
    reg [3:0] adc_clock_phase;
    reg [11:0] sample_timer;
    reg [4:0] sample_count;
    reg [9:0] batch_ref_raw;
    reg signed [15:0] sample_sum_unwrapped;
    reg [9:0] sample_previous, sample_older;
    reg [1:0] median_filled;

    assign adc_clk = adc_clock_phase[3];
    assign adc_oe_n = 1'b0;
    // Raw capture/clock are unchanged. Selected conversion feeds the existing
    // medoid/Q4 pipeline at its original candidate and publication deadlines.

    function signed [11:0] wrap_raw_delta;
        input [9:0] now_raw;
        input [9:0] ref_raw;
        reg signed [11:0] diff;
        begin
            diff = $signed({1'b0, now_raw}) - $signed({1'b0, ref_raw});
            if (diff > 12'sd511)
                wrap_raw_delta = diff - 12'sd1024;
            else if (diff < -12'sd512)
                wrap_raw_delta = diff + 12'sd1024;
            else
                wrap_raw_delta = diff;
        end
    endfunction

    // Circular three-sample medoid: reject an isolated sample without clipping
    // a real ramp, and keep 1023/0 adjacent. Ties prefer the middle sample.
    function [10:0] circular_distance;
        input [9:0] a, b;
        reg [10:0] distance;
        begin
            distance = a >= b ? {1'b0,a}-{1'b0,b} : {1'b0,b}-{1'b0,a};
            circular_distance = distance > 11'd512 ? 11'd1024-distance : distance;
        end
    endfunction
    reg [9:0] candidate_raw, clean_sample;
    assign adc_clean_stage = clean_sample;
    reg [10:0] d_current_previous, d_previous_older, d_current_older;
    reg [11:0] cost_current, cost_previous, cost_older;
    reg signed [11:0] current_unwrapped;
    wire signed [15:0] final_sum_unwrapped =
        sample_sum_unwrapped + {{4{current_unwrapped[11]}}, current_unwrapped};

    // Selection is based only on motor output timing, never ADC amplitude.
    // This cache never drives the original adc_raw/clock/raw-guard path.
    reg [31:0] trace_cycle;
    reg [9:0] early5, early6;
    reg [2:0] pins_previous;
    reg [31:0] quiet_pin_age;
    reg [31:0] main_word_cycle, quiet_cache_cycle;
    reg quiet_cache_valid;
    reg [9:0] quiet_cache_word, quiet_cache_p5, quiet_cache_p6, quiet_cache_p7;
    reg quiet_cache_otr;
    wire pins_quiet = trace_motor_pins == pins_previous &&
        quiet_pin_age >= QUIET_GUARD_CYCLES;
    wire [31:0] quiet_cache_age = trace_cycle - quiet_cache_cycle;
    wire quiet_cache_fresh = quiet_cache_valid &&
        quiet_cache_age <= QUIET_MAX_AGE_CYCLES;
    wire use_quiet_cache = QUIET_CACHE_WORDS != 0 &&
        quiet_cache_valid && quiet_cache_fresh;
    wire [9:0] selected_candidate = use_quiet_cache ? quiet_cache_word : adc_raw;
    reg [31:0] selected_IO_cycle;
    reg [9:0] baseline_uniform_raw;
    reg [5:0] selection_flags;

    // All values on the RHS are pre-edge values. At a phase7/3120 collision
    // the query intentionally consumes the OLD cache and OLD main word.
    always @(posedge clk_50m or negedge rst_n) begin
        if (!rst_n) begin
            pins_previous <= 3'd0; quiet_pin_age <= 32'd0;
            main_word_cycle <= 32'd0; quiet_cache_cycle <= 32'd0;
            quiet_cache_valid <= 1'b0;
            quiet_cache_word <= 10'd0; quiet_cache_p5 <= 10'd0;
            quiet_cache_p6 <= 10'd0; quiet_cache_p7 <= 10'd0;
            quiet_cache_otr <= 1'b0;
        end else begin
            pins_previous <= trace_motor_pins;
            if (trace_motor_pins != pins_previous)
                quiet_pin_age <= 32'd0;
            else if (quiet_pin_age < QUIET_GUARD_CYCLES)
                quiet_pin_age <= quiet_pin_age + 32'd1;
            if (adc_clock_phase == 4'd7) begin
                main_word_cycle <= trace_cycle;
                if (pins_quiet) begin
                    quiet_cache_word <= adc_data_in;
                    quiet_cache_p5 <= early5;
                    quiet_cache_p6 <= early6;
                    quiet_cache_p7 <= adc_data_in;
                    quiet_cache_otr <= trace_adc_otr;
                    quiet_cache_cycle <= trace_cycle;
                    quiet_cache_valid <= 1'b1;
                end
            end
        end
    end

    always @(posedge clk_50m or negedge rst_n) begin
        if (!rst_n) begin
            adc_clock_phase <= 4'd0;
            adc_raw <= 10'd0;
            adc_filtered_q4 <= 14'd0;
            sample_timer <= 12'd0;
            sample_count <= 5'd0;
            batch_ref_raw <= 10'd0;
            sample_sum_unwrapped <= 16'sd0;
            sample_previous <= 10'd0;
            sample_older <= 10'd0;
            median_filled <= 2'd0;
            candidate_raw <= 10'd0; clean_sample <= 10'd0;
            d_current_previous <= 11'd0; d_previous_older <= 11'd0; d_current_older <= 11'd0;
            cost_current <= 12'd0; cost_previous <= 12'd0; cost_older <= 12'd0;
            current_unwrapped <= 12'sd0;
        end else begin
            adc_clock_phase <= adc_clock_phase + 1'b1;
            // Launch positive edge after this capture edge (phase7->8).
            // Previous falling edge was160ns earlier. No controltickchange.
            if (adc_clock_phase == 4'd7)
                adc_raw <= adc_data_in;

            // Finish the sensor pipeline before the ORIGINAL batch commit edge.
            // Sampling is shifted only80ns earlier; no extra1ms frame latency.
            if (sample_timer == 12'd3120) begin
                candidate_raw <= selected_candidate;
                d_current_previous <= circular_distance(selected_candidate,sample_previous);
                d_previous_older <= circular_distance(sample_previous,sample_older);
                d_current_older <= circular_distance(selected_candidate,sample_older);
            end
            if (sample_timer == 12'd3121) begin
                cost_current <= {1'b0,d_current_previous}+{1'b0,d_current_older};
                cost_previous <= {1'b0,d_current_previous}+{1'b0,d_previous_older};
                cost_older <= {1'b0,d_current_older}+{1'b0,d_previous_older};
            end
            if (sample_timer == 12'd3122)
                clean_sample <= median_filled < 2 ? candidate_raw :
                    (cost_previous <= cost_current && cost_previous <= cost_older) ? sample_previous :
                    cost_current <= cost_older ? candidate_raw : sample_older;
            if (sample_timer == 12'd3123)
                current_unwrapped <= $signed({1'b0,batch_ref_raw}) + wrap_raw_delta(clean_sample,batch_ref_raw);

            // 3125 fabric clocks = 62.5 us.  Sixteen samples = 1 ms.
            if (sample_timer == 12'd3124) begin
                sample_timer <= 12'd0;
                sample_older <= sample_previous;
                sample_previous <= candidate_raw;
                if (median_filled < 2) median_filled <= median_filled + 1'b1;

                if (sample_count == 5'd0) begin
                    batch_ref_raw <= clean_sample;
                    sample_sum_unwrapped <= $signed({6'd0, clean_sample});
                    sample_count <= 5'd1;
                end else if (sample_count == 5'd15) begin
                    // Sum of 16 raw samples numerically equals average*16,
                    // exactly the required Q4 representation.  Keeping the
                    // low 14 bits performs the modulo-1024 wrap in Q4 units.
                    adc_filtered_q4 <= (DIAGNOSTIC_SWITCHES && !average_enable) ?
                        {adc_raw,4'b0} : final_sum_unwrapped[13:0];
                    sample_sum_unwrapped <= 16'sd0;
                    sample_count <= 5'd0;
                end else begin
                    sample_sum_unwrapped <= final_sum_unwrapped;
                    sample_count <= sample_count + 5'd1;
                end
            end else begin
                sample_timer <= sample_timer + 12'd1;
            end
        end
    end

    // Selected-word observation and the unchanged global fabric cycle.
    assign trace_time_counter=trace_cycle;
    reg [15:0] trace_batch;
    reg [9:0] pair5,pair6,pair7;
    reg [9:0] cand5,cand6,cand7;
    reg [3:0] cand_phase;
    reg main_otr,pair_otr,cand_otr;
    wire [13:0] trace_q4_result=sample_count==15 ?
        final_sum_unwrapped[13:0] : adc_filtered_q4;
    always @(posedge clk_50m or negedge rst_n) begin
      if(!rst_n) begin
        trace_cycle<=0;trace_batch<=0;early5<=0;early6<=0;
        pair5<=0;pair6<=0;pair7<=0;cand5<=0;cand6<=0;cand7<=0;
        cand_phase<=0;main_otr<=0;pair_otr<=0;cand_otr<=0;trace_valid<=0;trace_record<=0;
        selected_IO_cycle<=0;baseline_uniform_raw<=0;selection_flags<=0;
      end else begin
        trace_cycle<=trace_cycle+1'b1;trace_valid<=0;
        if(adc_clock_phase==5)early5<=adc_data_in;
        if(adc_clock_phase==6)early6<=adc_data_in;
        if(adc_clock_phase==7)main_otr<=trace_adc_otr;
        // phase8 sees the phase7 main register of this same conversion.
        if(adc_clock_phase==8)begin pair5<=early5;pair6<=early6;pair7<=adc_raw;pair_otr<=main_otr;end
        if(sample_timer==3120)begin
          // At phase8 the current pair commits on this very edge. Select its
          // pre-edge components explicitly instead of taking the old pair.
          cand5<=use_quiet_cache ? quiet_cache_p5 : (adc_clock_phase==8 ? early5:pair5);
          cand6<=use_quiet_cache ? quiet_cache_p6 : (adc_clock_phase==8 ? early6:pair6);
          cand7<=use_quiet_cache ? quiet_cache_p7 : (adc_clock_phase==8 ? adc_raw:pair7);
          cand_otr<=use_quiet_cache ? quiet_cache_otr : (adc_clock_phase==8 ? main_otr:pair_otr);
          cand_phase<=adc_clock_phase;
          selected_IO_cycle<=use_quiet_cache ? quiet_cache_cycle:main_word_cycle;
          baseline_uniform_raw<=adc_raw;
          // bit3 describes pins at QUERY time, not the cached word's time.
          // Cache validity already certifies its own past capture condition.
          selection_flags<={1'b0,!use_quiet_cache,pins_quiet,
            quiet_cache_fresh,quiet_cache_valid,use_quiet_cache};
        end
        if(sample_timer==3124)begin
          trace_valid<=1;
          // The pre-edge applied driver state is captured on exactly the
          // same 3124 edge as the selected 160-bit pipeline record.
          trace_record<={selection_flags,baseline_uniform_raw,selected_IO_cycle,
            trace_actuator_diag,1'b0,cand_otr,current_unwrapped,trace_q4_result,
            sample_sum_unwrapped,batch_ref_raw,cand_phase,sample_count[3:0],
            cand7,cand6,cand5,clean_sample,candidate_raw,trace_batch,trace_cycle};
          if(sample_count==15)trace_batch<=trace_batch+1'b1;
        end
      end
    end
endmodule
