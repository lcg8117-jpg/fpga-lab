// Independent measurement option, never an output/control limiter.
// Legacy M: 8 x 1ms count difference, 762546 rad/s Q20 per count,
// then v[k]=v[k-1]+((raw-v[k-1]) >>> 2). 50ms without an edge sets
// exact zero (the old signed shift can otherwise retain a few Q20 LSB).
// T: 50MHz period of successive legal same-direction x4 count edges.
// T uses max(last_period,elapsed) to decay. After min(4 periods,200ms)
// its period is discarded. First/reverse/expired pulses cannot reuse an old
// T period; the existing M estimate supplies the next1ms sample until a valid
// period exists. Thus sparse known count edges are not suppressed to zero.
// Legacy M remains 50ms and still supplies the independent speed hard guard.
module j280_encoder_velocity #(
 parameter integer CLOCK_HZ = 50000000,
 parameter integer TIMEOUT_CYCLES = CLOCK_HZ/20,
 parameter integer FAST_PERIOD_CYCLES = CLOCK_HZ/1000,
 parameter integer PERIOD_TIMEOUT_CYCLES = TIMEOUT_CYCLES*4
) (
 input wire clk, rst_n, tick_1ms, clear,
 input wire signed [31:0] encoder_count,
 input wire [15:0] invalid_count,
 input wire hybrid_mode,
 output reg signed [31:0] velocity_q20,
 output reg velocity_valid, using_period,
 output reg signed [31:0] legacy_velocity_q20,
 output reg signed [31:0] legacy_raw_q20,
 output reg measurement_fault,
 input wire kit_ip570
);
 // Physical raw x4 count remains unchanged; only radian units depend on kit.
 wire [47:0] period_numerator = kit_ip570 ? 48'd6335 * CLOCK_HZ : 48'd6100 * CLOCK_HZ;
 wire signed [20:0] rate_scale = kit_ip570 ? 21'sd791875 : 21'sd762546;
 reg signed [31:0] count_previous;
 reg [15:0] invalid_previous;
 reg [31:0] age_cycles, period_cycles;
 reg have_edge, direction_negative, period_valid;
 reg signed [31:0] history[0:7];
 reg [3:0] filled;
 reg signed [31:0] edge_delta;
 wire has_edge=edge_delta!=0;
 wire legal_edge=edge_delta==32'sd1 || edge_delta== -32'sd1;
 wire reversal=has_edge && have_edge && edge_delta[31]!=direction_negative;
 reg [2:0] m_state;
 reg signed [31:0] m_delta;
 reg signed [63:0] m_product;
 reg signed [32:0] m_error;
 reg signed [33:0] m_result;
 reg m_warm, m_stopped, m_fallback;
 wire signed [33:0] m_next=$signed({{2{legacy_velocity_q20[31]}},legacy_velocity_q20})+(m_error>>>2);
 wire m_overflow=(m_product[63:31]!={33{m_product[31]}});
 wire [33:0] four_periods={2'd0,period_cycles}<<2;
 wire [31:0] expire_cycles=four_periods>PERIOD_TIMEOUT_CYCLES ? PERIOD_TIMEOUT_CYCLES : four_periods[31:0];
 wire expired=age_cycles>=PERIOD_TIMEOUT_CYCLES || (period_valid && age_cycles>=expire_cycles);
 wire [31:0] effective_period=age_cycles>period_cycles ? age_cycles : period_cycles;
 wire select_t=hybrid_mode && period_valid && !expired &&
               period_cycles>FAST_PERIOD_CYCLES;
 // Unsigned restoring divider: fixed48clocks, independent of1ms sample.
 reg divider_busy, divide_negative;
 reg [5:0] bits_left;
 reg [47:0] quotient_shift;
 reg [32:0] remainder;
 reg [31:0] denominator;
 wire [32:0] remainder_shift={remainder[31:0],quotient_shift[47]};
 wire quotient_bit=remainder_shift>={1'b0,denominator};
 wire [32:0] next_remainder=quotient_bit ? remainder_shift-{1'b0,denominator} : remainder_shift;
 wire [47:0] next_quotient={quotient_shift[46:0],quotient_bit};
 integer i;
 // Timestamp all edges after the same20ns decode pipeline. Consecutive-edge
 // intervals are unchanged; avoid32bit subtract plus divider-enable gating.
 always @(posedge clk or negedge rst_n) begin
  if(!rst_n) edge_delta<=0;
  else if(clear) edge_delta<=0;
  else edge_delta<=encoder_count-count_previous;
 end
 always @(posedge clk or negedge rst_n) begin
  if(!rst_n) begin
   count_previous<=0; invalid_previous<=0; age_cycles<=0; period_cycles<=0;
   have_edge<=0; direction_negative<=0; period_valid<=0; filled<=0;
   velocity_q20<=0; velocity_valid<=0; using_period<=0;
   legacy_velocity_q20<=0; legacy_raw_q20<=0; measurement_fault<=0;
   m_state<=0; m_delta<=0; m_product<=0; m_error<=0; m_result<=0;
   m_warm<=1; m_stopped<=1; m_fallback<=1;
   divider_busy<=0; divide_negative<=0; bits_left<=0; quotient_shift<=0; remainder<=0; denominator<=1;
   for(i=0;i<8;i=i+1) history[i]<=0;
  end else if(clear) begin
   count_previous<=encoder_count; invalid_previous<=invalid_count;
   age_cycles<=0; period_cycles<=0; have_edge<=0; direction_negative<=0; period_valid<=0; filled<=0;
   velocity_q20<=0; velocity_valid<=0; using_period<=0; legacy_velocity_q20<=0; legacy_raw_q20<=0;
   measurement_fault<=0; divider_busy<=0;
   m_state<=0; m_delta<=0; m_product<=0; m_error<=0; m_result<=0;
   m_warm<=1; m_stopped<=1; m_fallback<=1;
   for(i=0;i<8;i=i+1) history[i]<=encoder_count;
  end else if(measurement_fault || invalid_count!=invalid_previous || (has_edge && !legal_edge)) begin
   // A changed illegal-transition counter or impossible multi-count fabric
   // jump is an estimator failure, not an invented/clipped speed.
   count_previous<=encoder_count; invalid_previous<=invalid_count;
   measurement_fault<=1; velocity_q20<=0; velocity_valid<=0; using_period<=0;
   legacy_velocity_q20<=0; legacy_raw_q20<=0; divider_busy<=0;
   m_state<=0;
   have_edge<=0; period_valid<=0; filled<=0;
  end else begin
   count_previous<=encoder_count;
   // Commit the unchanged M arithmetic80ns after its exact1ms snapshot.
   // Angle measurement prepares PID later in this same sample epoch.
   case(m_state)
    1: begin m_product<=m_delta*rate_scale; m_state<=2; end
    2: begin
     m_error<=$signed({m_product[31],m_product[31:0]})-$signed({legacy_velocity_q20[31],legacy_velocity_q20});
     m_state<=3;
    end
    3: begin m_result<=m_next; m_state<=4; end
    4: begin
     m_state<=0;
     legacy_raw_q20<=m_warm ? 32'sd0 : m_product[31:0];
     legacy_velocity_q20<=m_warm || m_stopped ? 32'sd0 : m_result[31:0];
     if(m_fallback) begin
      velocity_q20<=m_warm || m_stopped ? 32'sd0 : m_result[31:0];
      velocity_valid<=!m_warm;
     end
    end
    default: m_state<=0;
   endcase
   if(age_cycles!=32'hFFFFFFFF) age_cycles<=age_cycles+1'b1;
   if(expired) begin
    period_valid<=0; have_edge<=0; divider_busy<=0;
    if(hybrid_mode && using_period) begin velocity_q20<=0; velocity_valid<=1; end
   end
   if(has_edge) begin
    age_cycles<=0; have_edge<=1; direction_negative<=edge_delta[31];
    if(!have_edge || reversal || expired || age_cycles==32'hFFFFFFFF) begin
     period_valid<=0; divider_busy<=0;
     // Reverse instantly invalidates stale T before the next 1ms update.
     if(hybrid_mode && (using_period || !period_valid)) begin velocity_q20<=0; velocity_valid<=0; end
    end else begin period_cycles<=age_cycles+1'b1; period_valid<=1; end
   end
   if(divider_busy && !reversal && !expired && hybrid_mode) begin
    quotient_shift<=next_quotient; remainder<=next_remainder; bits_left<=bits_left-1'b1;
    if(bits_left==1) begin
     divider_busy<=0;
     if(next_quotient[47:31]!=0) begin
      measurement_fault<=1; velocity_q20<=0; velocity_valid<=0;
     end else begin velocity_q20<=divide_negative ? -$signed(next_quotient[31:0]) : $signed(next_quotient[31:0]); velocity_valid<=1; end
    end
   end
   if(tick_1ms) begin
    for(i=7;i>0;i=i-1) history[i]<=history[i-1];
    history[0]<=encoder_count;
    m_delta<=encoder_count-history[7]; m_state<=1;
    m_warm<=filled<8; m_stopped<=age_cycles>=TIMEOUT_CYCLES; m_fallback<=!select_t;
    if(filled<8) filled<=filled+1'b1;
    using_period<=select_t;
    if(!select_t) begin
     divider_busy<=0;
    end else if(!period_valid || expired || effective_period==0 || reversal) begin
     divider_busy<=0; velocity_q20<=0; velocity_valid<=expired || !have_edge;
    end else begin
     divider_busy<=1; bits_left<=48; quotient_shift<=period_numerator;
     remainder<=0; denominator<=effective_period; divide_negative<=direction_negative; velocity_valid<=0;
    end
   end
   if(m_state==2 && !m_warm && m_overflow) begin
    measurement_fault<=1; m_state<=0; using_period<=0; divider_busy<=0;
    legacy_raw_q20<=0; legacy_velocity_q20<=0; velocity_q20<=0; velocity_valid<=0;
   end
  end
 end
endmodule
