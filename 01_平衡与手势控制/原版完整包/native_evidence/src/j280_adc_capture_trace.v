module j280_adc_capture_trace #(
    parameter integer DIAGNOSTIC_SWITCHES = 0,
    parameter integer QUIET_CACHE_WORDS = 1,
    parameter integer ADC_WORD_GUARD = 1,
    parameter integer WINDOW_SAMPLES = 16,
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
    output reg control_sample_valid,
    output reg trace_valid,
    output reg [239+2*(WINDOW_SAMPLES==64 ? 2:0):0] trace_record,
    output reg io_sample_valid,
    output reg [31:0] io_sample_word,
    output wire [31:0] trace_time_counter
);
    // Retain the 3.125MHz ADC clock and the existing 1ms publication window.
    // Capture the entire input word at old phase6:140ns after the previous
    // falling launch and20ns BEFORE the next generated rising launch.
    // RevA.1 prose and Figure22 disagree about the ADC output-update edge.
    // tOD25ns is TYP only. This avoids the same-edge capture/launch in RTL;
    // it does not certify external board delays or repair analog noise.
    //
    // The old filter averaged 256 consecutive 50 MHz fabric samples, which
    // covered only 5.12 us and therefore did almost nothing to the low-rate
    // angle jitter that contaminated AlphaDot. The compatibility mode takes
    // 16 samples/ms; the dense mode takes 64 within that same window.
    //
    // The WDD35D/ADC value legitimately wraps 1023 -> 0.  A normal arithmetic
    // average would produce a false value near 512 when a 1 ms batch straddles
    // that wrap.  Every sample is therefore unwrapped relative to the first
    // sample of the batch before averaging, then the Q4 result is wrapped back
    // modulo 1024.
    //64 samples reuse the existing1ms window and exact publication edge.
    // Four interleaved medoids retain62.5us history spacing per lane.
    // The denser uniform mean moves its time center23.44us earlier;
    // there is no extra1ms frame, PID gain or control-cadence change.
    localparam integer EXTRA_BITS=WINDOW_SAMPLES==64 ? 2:0;
    reg [3:0] adc_clock_phase;
    reg [11:0] sample_timer;
    reg [4+EXTRA_BITS:0] sample_count;
    reg [9:0] batch_ref_raw;
    reg signed [15+EXTRA_BITS:0] sample_sum_unwrapped;
    reg [9:0] sample_previous, sample_older;
    reg [1:0] median_filled;
    reg [9:0] lane_previous[0:3],lane_older[0:3];
    reg [1:0] lane_filled[0:3];
    integer reset_lane;
    wire [9:0] history_previous=WINDOW_SAMPLES==64 ? lane_previous[sample_count[1:0]]:sample_previous;
    wire [9:0] history_older=WINDOW_SAMPLES==64 ? lane_older[sample_count[1:0]]:sample_older;
    wire [1:0] history_filled=WINDOW_SAMPLES==64 ? lane_filled[sample_count[1:0]]:median_filled;
    // Four intervals per3125clocks:782,781,781,781. Exactly64/50000clocks.
    wire candidate_query=sample_timer==3120 || (WINDOW_SAMPLES==64 &&
        (sample_timer==777 || sample_timer==1558 || sample_timer==2339));
    wire candidate_cost=sample_timer==3121 || (WINDOW_SAMPLES==64 &&
        (sample_timer==778 || sample_timer==1559 || sample_timer==2340));
    wire candidate_clean=sample_timer==3122 || (WINDOW_SAMPLES==64 &&
        (sample_timer==779 || sample_timer==1560 || sample_timer==2341));
    wire candidate_unwrap=sample_timer==3123 || (WINDOW_SAMPLES==64 &&
        (sample_timer==780 || sample_timer==1561 || sample_timer==2342));
    wire sample_commit=sample_timer==3124 || (WINDOW_SAMPLES==64 &&
        (sample_timer==781 || sample_timer==1562 || sample_timer==2343));

    assign adc_clk = adc_clock_phase[3];
    assign adc_oe_n = 1'b0;
    // Keep the word-guard baseline's phase6 capture and ADC clock. The dense
    // mode adds three candidate slots per62.5us, with the same1ms deadline.

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
    wire signed [15+EXTRA_BITS:0] final_sum_unwrapped =
        sample_sum_unwrapped + {{(4+EXTRA_BITS){current_unwrapped[11]}}, current_unwrapped};
    function [13:0] sum_to_q4;
        input signed [15+EXTRA_BITS:0] value;
        reg signed [15+EXTRA_BITS:0] quotient;
        begin
            if(WINDOW_SAMPLES==64)begin
                // sum64/4 preserves the SAME raw*16 unit. Round nearest,
                // ties even, including negative sums and modulo1024 wrap.
                quotient=value>>>2;
                if(value[1:0]>2 || (value[1:0]==2 && quotient[0]))quotient=quotient+1'b1;
                sum_to_q4=quotient[13:0];
            end else sum_to_q4=value[13:0];
        end
    endfunction
    wire [13:0] normalized_sum_q4=sum_to_q4(final_sum_unwrapped);

    // Selection is based only on motor output timing, never ADC amplitude.
    // This cache never drives the independent raw-guard path.
    reg [31:0] trace_cycle;
    reg [9:0] early5, early6;
    reg [2:0] pins_previous;
    reg [31:0] quiet_pin_age;
    reg [31:0] main_word_cycle, quiet_cache_cycle;
    reg quiet_cache_valid;
    reg main_otr;
    reg [2:0] pins_observed;
    reg motor_observer_valid;
    reg [9:0] quiet_cache_word, quiet_cache_p5, quiet_cache_p6, quiet_cache_p7;
    reg quiet_cache_otr;
    wire pins_quiet = motor_observer_valid && pins_observed == pins_previous &&
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

    // All RHS values are pre-edge values. A phase6/query collision uses
    // the old main word; a phase7/query collision uses the old cache. The
    // cache commits atphase7 with the WHOLE phase6 register and its phase6
    // timestamp. Diagnostic slots remain literal phase5/6/7 observations.
    always @(posedge clk_50m or negedge rst_n) begin
        if (!rst_n) begin
            pins_previous <= 3'd0; quiet_pin_age <= 32'd0;
            main_word_cycle <= 32'd0; quiet_cache_cycle <= 32'd0;
            quiet_cache_valid <= 1'b0;
            pins_observed <= 3'd0; motor_observer_valid <= 1'b0;
            quiet_cache_word <= 10'd0; quiet_cache_p5 <= 10'd0;
            quiet_cache_p6 <= 10'd0; quiet_cache_p7 <= 10'd0;
            quiet_cache_otr <= 1'b0;
        end else begin
            // Observe actual logical motor outputs once, then age those
            // registered observations. At phase7 this qualification belongs
            // to phase6, exactly matching the ADC word being committed.
            // This cuts the live key/mode/motor->age/cache combinational path.
            pins_observed <= trace_motor_pins;
            pins_previous <= pins_observed;
            motor_observer_valid <= 1'b1;
            if (!motor_observer_valid || pins_observed != pins_previous)
                quiet_pin_age <= 32'd0;
            else if (quiet_pin_age < QUIET_GUARD_CYCLES)
                quiet_pin_age <= quiet_pin_age + 32'd1;
            if (adc_clock_phase == 4'd6) begin
                main_word_cycle <= trace_cycle;
            end
            if (adc_clock_phase == 4'd7) begin
                // pins_quiet is the delayed qualification of actual phase6
                // motor pins. Data, OTR, time and qualification agree.
                // Compare two complete registers of the SAME conversion.
                // No ADC-amplitude clipping and no new averaging window.
                // An invalid word never renews the quiet cache; its existing
                //30us age limit and independent raw-guard fallback remain.
                if (pins_quiet && (ADC_WORD_GUARD==0 || early5==adc_raw)) begin
                    quiet_cache_word <= adc_raw;
                    quiet_cache_p5 <= early5;
                    quiet_cache_p6 <= early6;
                    quiet_cache_p7 <= adc_data_in;
                    quiet_cache_otr <= main_otr;
                    quiet_cache_cycle <= main_word_cycle;
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
            control_sample_valid <= 0;
            sample_timer <= 12'd0;
            sample_count <= 5'd0;
            batch_ref_raw <= 10'd0;
            sample_sum_unwrapped <= 16'sd0;
            sample_previous <= 10'd0;
            sample_older <= 10'd0;
            median_filled <= 2'd0;
            for(reset_lane=0;reset_lane<4;reset_lane=reset_lane+1)begin
                lane_previous[reset_lane]<=0;lane_older[reset_lane]<=0;lane_filled[reset_lane]<=0;
            end
            candidate_raw <= 10'd0; clean_sample <= 10'd0;
            d_current_previous <= 11'd0; d_previous_older <= 11'd0; d_current_older <= 11'd0;
            cost_current <= 12'd0; cost_previous <= 12'd0; cost_older <= 12'd0;
            current_unwrapped <= 12'sd0;
        end else begin
            control_sample_valid <= 0;
            adc_clock_phase <= adc_clock_phase + 1'b1;
            sample_timer <= sample_timer==3124 ? 12'd0:sample_timer+12'd1;
            // Whole-word capture one50MHz cycle before phase7->8
            // launches the ADC rising edge. No extra pipeline/filter frame.
            if (adc_clock_phase == 4'd6)
                adc_raw <= adc_data_in;

            // Finish the sensor pipeline before the ORIGINAL batch commit edge.
            // Sampling is shifted only80ns earlier; no extra1ms frame latency.
            if (candidate_query) begin
                candidate_raw <= selected_candidate;
                d_current_previous <= circular_distance(selected_candidate,history_previous);
                d_previous_older <= circular_distance(history_previous,history_older);
                d_current_older <= circular_distance(selected_candidate,history_older);
            end
            if (candidate_cost) begin
                cost_current <= {1'b0,d_current_previous}+{1'b0,d_current_older};
                cost_previous <= {1'b0,d_current_previous}+{1'b0,d_previous_older};
                cost_older <= {1'b0,d_current_older}+{1'b0,d_previous_older};
            end
            if (candidate_clean)
                clean_sample <= history_filled < 2 ? candidate_raw :
                    (cost_previous <= cost_current && cost_previous <= cost_older) ? history_previous :
                    cost_current <= cost_older ? candidate_raw : history_older;
            if (candidate_unwrap)
                current_unwrapped <= $signed({1'b0,batch_ref_raw}) + wrap_raw_delta(clean_sample,batch_ref_raw);

            // Commit16 or64 candidates per1ms; lane history is always62.5us.
            if (sample_commit) begin
                if(WINDOW_SAMPLES==64)begin
                    lane_older[sample_count[1:0]]<=lane_previous[sample_count[1:0]];
                    lane_previous[sample_count[1:0]]<=candidate_raw;
                    if(lane_filled[sample_count[1:0]]<2)
                        lane_filled[sample_count[1:0]]<=lane_filled[sample_count[1:0]]+1'b1;
                end else begin
                    sample_older <= sample_previous;
                    sample_previous <= candidate_raw;
                    if (median_filled < 2) median_filled <= median_filled + 1'b1;
                end

                if (sample_count == 5'd0) begin
                    batch_ref_raw <= clean_sample;
                    sample_sum_unwrapped <= $signed({{(6+EXTRA_BITS){1'b0}},clean_sample});
                    sample_count <= 5'd1;
                end else if (sample_count == WINDOW_SAMPLES-1) begin
                    control_sample_valid <= 1;
                    // Normalize16/64-sample sums to the same average*16 Q4
                    // unit. The low14 bits implement modulo1024 wrapping.
                    adc_filtered_q4 <= (DIAGNOSTIC_SWITCHES && !average_enable) ?
                        {adc_raw,4'b0} : normalized_sum_q4;
                    sample_sum_unwrapped <= 16'sd0;
                    sample_count <= 5'd0;
                end else begin
                    sample_sum_unwrapped <= final_sum_unwrapped;
                    sample_count <= sample_count + 5'd1;
                end
            end
        end
    end

    // Selected-word observation and the unchanged global fabric cycle.
    assign trace_time_counter=trace_cycle;
    reg [15:0] trace_batch;
    reg [9:0] pair5,pair6,pair7;
    reg [9:0] cand5,cand6,cand7;
    reg [3:0] cand_phase;
    reg pair_otr,cand_otr;
    wire [13:0] trace_q4_result=sample_count==WINDOW_SAMPLES-1 ?
        normalized_sum_q4 : adc_filtered_q4;
    always @(posedge clk_50m or negedge rst_n) begin
      if(!rst_n) begin
        trace_cycle<=0;trace_batch<=0;early5<=0;early6<=0;
        pair5<=0;pair6<=0;pair7<=0;cand5<=0;cand6<=0;cand7<=0;
        io_sample_valid<=0;io_sample_word<=0;
        cand_phase<=0;main_otr<=0;pair_otr<=0;cand_otr<=0;trace_valid<=0;trace_record<=0;
        selected_IO_cycle<=0;baseline_uniform_raw<=0;selection_flags<=0;
      end else begin
        trace_cycle<=trace_cycle+1'b1;trace_valid<=0;io_sample_valid<=0;
        if(adc_clock_phase==7)begin
          io_sample_valid<=1;
          io_sample_word<={main_otr,pins_quiet,early5,adc_raw,adc_data_in};
        end
        if(adc_clock_phase==5)early5<=adc_data_in;
        if(adc_clock_phase==6)early6<=adc_data_in;
        if(adc_clock_phase==6)main_otr<=trace_adc_otr;
        // phase7 completes diagnostic shadows for the phase6 main word.
        if(adc_clock_phase==7)begin pair5<=early5;pair6<=early6;pair7<=adc_data_in;pair_otr<=main_otr;end
        if(candidate_query)begin
          // At phase7 the current shadow bundle commits on this edge.
          // Select its components explicitly; control always uses phase6.
          cand5<=use_quiet_cache ? quiet_cache_p5 : (adc_clock_phase==7 ? early5:pair5);
          cand6<=use_quiet_cache ? quiet_cache_p6 : (adc_clock_phase==7 ? early6:pair6);
          cand7<=use_quiet_cache ? quiet_cache_p7 : (adc_clock_phase==7 ? adc_data_in:pair7);
          cand_otr<=use_quiet_cache ? quiet_cache_otr : (adc_clock_phase==7 ? main_otr:pair_otr);
          cand_phase<=adc_clock_phase;
          selected_IO_cycle<=use_quiet_cache ? quiet_cache_cycle:main_word_cycle;
          baseline_uniform_raw<=adc_raw;
          // bit3 describes the QUERY-minus-one-cycle observation, not the cached word.
          // Cache validity already certifies its own past capture condition.
          selection_flags<={1'b0,!use_quiet_cache,pins_quiet,
            quiet_cache_fresh,quiet_cache_valid,use_quiet_cache};
        end
        if(sample_commit)begin
          trace_valid<=1;
          // The pre-edge applied driver state is captured on exactly the
          // same 3124 edge as the selected 160-bit pipeline record.
          trace_record<={selection_flags,baseline_uniform_raw,selected_IO_cycle,
            trace_actuator_diag,1'b0,cand_otr,current_unwrapped,trace_q4_result,
            sample_sum_unwrapped,batch_ref_raw,cand_phase,sample_count[3+EXTRA_BITS:0],
            cand7,cand6,cand5,clean_sample,candidate_raw,trace_batch,trace_cycle};
          if(sample_count==WINDOW_SAMPLES-1)trace_batch<=trace_batch+1'b1;
        end
      end
    end
endmodule

// Observation only. No output reaches a control or motor decision.
// Four atomic21-bit counters use the existing diagnostic bytes252/253.
// Eight existing50ms packets form one bank; no extra UART packet or command.
module j280_adc_io_stats #(parameter integer COUNTER_BITS=21,parameter integer WINDOW_SAMPLES=16)(
 input wire clk,rst_n,io_valid,diag_sent,guard_enabled,
 input wire [31:0] io_word,
 output wire [15:0] diagnostic_word
);
 localparam [COUNTER_BITS-1:0] MAX_COUNT={COUNTER_BITS{1'b1}};
 reg [COUNTER_BITS-1:0] total,bad56,bad67,quiet_bad56;
 reg [COUNTER_BITS-1:0] bank_total,bank_bad56,bank_bad67,bank_quiet_bad56;
 reg overflow,bank_overflow,bank_valid;
 reg [2:0] page;
 wire mismatch56=io_word[29:20]!=io_word[19:10];
 wire mismatch67=io_word[19:10]!=io_word[9:0];
 wire quiet_mismatch=io_word[30] && mismatch56;
 function [COUNTER_BITS-1:0] increment;
  input [COUNTER_BITS-1:0] value;input enabled;
  begin increment=enabled && value!=MAX_COUNT ? value+1'b1:value;end
 endfunction
 wire [COUNTER_BITS-1:0] next_total=increment(total,io_valid);
 wire [COUNTER_BITS-1:0] next_bad56=increment(bad56,io_valid && mismatch56);
 wire [COUNTER_BITS-1:0] next_bad67=increment(bad67,io_valid && mismatch67);
 wire [COUNTER_BITS-1:0] next_quiet_bad56=increment(quiet_bad56,io_valid && quiet_mismatch);
 wire next_overflow=overflow || (io_valid && (total==MAX_COUNT ||
  (mismatch56 && bad56==MAX_COUNT) || (mismatch67 && bad67==MAX_COUNT) ||
  (quiet_mismatch && quiet_bad56==MAX_COUNT)));
 wire [20:0] ext_total={{(21-COUNTER_BITS){1'b0}},bank_total};
 wire [20:0] ext_bad56={{(21-COUNTER_BITS){1'b0}},bank_bad56};
 wire [20:0] ext_bad67={{(21-COUNTER_BITS){1'b0}},bank_bad67};
 wire [20:0] ext_quiet_bad56={{(21-COUNTER_BITS){1'b0}},bank_quiet_bad56};
 reg [12:0] value;
 always @* begin
  case(page)
   0:value=ext_total[12:0];
   1:value={bank_valid,4'd0,ext_total[20:13]};
   2:value=ext_bad56[12:0];
   3:value={bank_overflow,4'd0,ext_bad56[20:13]};
   4:value=ext_bad67[12:0];
   5:value={(WINDOW_SAMPLES==64 ? 5'd1:5'd0),ext_bad67[20:13]};
   6:value=ext_quiet_bad56[12:0];
   7:value={4'hd,guard_enabled,ext_quiet_bad56[20:13]};
   default:value=0;
  endcase
 end
 assign diagnostic_word={page,value};
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   total<=0;bad56<=0;bad67<=0;quiet_bad56<=0;overflow<=0;
   bank_total<=0;bank_bad56<=0;bank_bad67<=0;bank_quiet_bad56<=0;
   bank_overflow<=0;bank_valid<=0;page<=0;
  end else begin
   total<=next_total;bad56<=next_bad56;bad67<=next_bad67;quiet_bad56<=next_quiet_bad56;
   overflow<=next_overflow;
   if(diag_sent)begin
    page<=page+1'b1;
    if(page==7)begin
     // Include a coincident conversion exactly once in the closing bank.
     bank_total<=next_total;bank_bad56<=next_bad56;bank_bad67<=next_bad67;
     bank_quiet_bad56<=next_quiet_bad56;bank_overflow<=next_overflow;bank_valid<=1;
     total<=0;bad56<=0;bad67<=0;quiet_bad56<=0;overflow<=0;
    end
   end
  end
 end
endmodule
