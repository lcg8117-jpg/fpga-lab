// Absolute P/D response search with before/trial/after comparison; no global-optimum claim.
module forest_d1_adaptive(
 input clk,rst_n,sample_valid,commit_tick,run_balance,stop,
 input manual_write,enable_write,enable_value,
 input [191:0] gains_base,
 input [31:0] manual_revision,sample_epoch,
 input signed [15:0] angle_error,angle_diff,
 input signed [31:0] position_error,requested_moto,
 input signed [15:0] actual_q15,
 input saturating,fault,input evidence_pause,
 output wire [191:0] target_gains,
 output reg enabled,
 output wire active,
 output reg [7:0] freeze_reason,
 output wire signed [15:0] center_p_q8,center_d_q8,fast_p_q8,fast_d_q8,
 output reg [31:0] learn_revision,adapt_epoch,candidate_epoch,candidate_revision,
 output reg diagnostic_pulse,
 output reg [31:0] diagnostic_seq,diagnostic_epoch,
 output reg [7:0] diagnostic_code,diagnostic_reason,
 output reg signed [7:0] diagnostic_center_before,diagnostic_center_after,
 output reg [31:0] diagnostic_reference_energy,diagnostic_window_energy,
 output reg [15:0] diagnostic_reference_peak,diagnostic_window_peak,
 output reg [6:0] diagnostic_event_samples,
 output wire [1:0] diagnostic_phase,
 output wire [5:0] diagnostic_window_samples,
 output wire [3:0] diagnostic_cooldown
);

 // Absolute, bidirectional coordinate search. A manual value is the starting
 // point, never the centre of a percentage-limited search interval.
 // P32..80, D96..448; I and both position gains retain manual values.
 localparam [7:0] OFF=0,STOPPED=1,FAULT=2,INTEGRAL=3,GAIN=4,
  SATURATION=5,ENVELOPE=6,NOISE=7,WARMUP=8,TRIAL=9,COOLDOWN=10,QUIET=13;
 localparam [13:0] P_MIN=512,P_MAX=1280,D_MIN=1536,D_MAX=7168;
 reg ready;
 reg [191:0] manual_bank;
 reg [13:0] best_p,best_d,wanted_p,wanted_d,applied_p,applied_d,probe_p,probe_d;
 reg axis,negative,tried_reverse;
 reg [9:0] step_p,step_d;
 reg [1:0] phase;
 reg [7:0] warmup;
 reg [5:0] samples;
 reg half_window;
 reg [31:0] cost,half_cost,baseline_cost,probe_cost;
 reg [15:0] peak,baseline_peak,probe_peak,half_peak;
 reg [31:0] last_epoch;
 reg [3:0] evaluate_pipe;
 reg [31:0] evaluate_cost;reg [15:0] evaluate_peak;
 reg [32:0] evaluate_pair;reg [39:0] evaluate_weight;
 reg [16:0] evaluate_peak_limit;reg evaluate_peak_ok,evaluate_better;
 reg [3:0] feature_pipe;
 reg paused;
 reg signed [15:0] raw_error,raw_difference,raw_actual;
 reg signed [31:0] raw_position,raw_request;
 reg raw_saturating;reg [31:0] raw_epoch;
 reg [15:0] magnitude_e,magnitude_d,magnitude_u;
 reg [8:0] magnitude_x;
 reg invalid_one,invalid_two,invalid_three,saturated_one;
 reg [7:0] invalid_reason_one,invalid_reason_two,invalid_reason_three;
 reg [15:0] square_e,square_d;
 reg [17:0] square_x;
 reg [31:0] sample_cost;
 reg [15:0] peak_one,peak_two,peak_three;
 reg [31:0] epoch_one,epoch_two,epoch_three;
 wire [15:0] abs_e=raw_error[15] ? -raw_error:raw_error;
 wire [15:0] abs_d=raw_difference[15] ? -raw_difference:raw_difference;
 wire [31:0] abs_x=raw_position[31] ? -raw_position:raw_position;
 wire [31:0] abs_request=raw_request[31] ? -raw_request:raw_request;
 wire [15:0] abs_u=raw_actual[15] ? -raw_actual:raw_actual;
 function [13:0] from_float;
  input [31:0] value;reg [23:0] mantissa;integer shift;
  begin
   mantissa={1'b1,value[22:0]};shift=146-value[30:23];
   if(value[31] || value[30:23]<123)from_float=0;
   else if(value[30:23]>136)from_float=14'h3fff;
   else from_float=mantissa>>shift;
  end
 endfunction
 function [31:0] to_float;
  input [13:0] value;integer i,leading;reg [7:0] exponent;reg [23:0] normalized;
  begin
   leading=0;for(i=0;i<14;i=i+1)if(value[i])leading=i;
   exponent=123+leading;normalized={10'd0,value}<<(23-leading);
   to_float=value==0 ? 32'd0:{1'b0,exponent,normalized[22:0]};
  end
 endfunction
 wire [13:0] manual_p=from_float(gains_base[31:0]);
 wire [13:0] manual_d=from_float(gains_base[95:64]);
 wire integral_nonzero=(|gains_base[62:32]) || (|gains_base[158:128]);
 wire eligible=!gains_base[31] && !gains_base[95] &&
  gains_base[30:23]!=255 && gains_base[94:87]!=255 &&
  manual_p>=P_MIN && manual_p<=P_MAX && manual_d>=D_MIN && manual_d<=D_MAX;
 reg allowed;
 wire allowed_now=enabled && ready && run_balance && !stop && !fault && !integral_nonzero && eligible &&
  candidate_revision==manual_revision && !manual_write;
 // Numerical ownership is registered; the independent physical/serial STOP
 // still gates motor pins immediately. Never route raw gesture STOP through
 // the wide gain bank and the FP core in the same20ns cycle.
 assign active=allowed && ready && enabled && candidate_revision==manual_revision &&
  !manual_write && !(enable_write && !enable_value);
 // I and position gains are never adapted. Send the stored manual wires
 // directly, removing128 redundant FF/mux routes from numerical ownership.
 assign target_gains={gains_base[191:96],(active ? to_float(applied_d):gains_base[95:64]),
  gains_base[63:32],(active ? to_float(applied_p):gains_base[31:0])};
 // Existing16-bit telemetry slots now explicitly carry absolute Q4 centres.
 assign center_p_q8=$signed({2'd0,best_p});assign center_d_q8=$signed({2'd0,best_d});
 assign fast_p_q8=0;assign fast_d_q8=0;
 assign diagnostic_phase=phase;assign diagnostic_window_samples=samples;
 assign diagnostic_cooldown=warmup==0 ? 4'd0:4'd2;
 wire [31:0] next_cost=cost+sample_cost;
 wire [15:0] next_peak=peak>peak_three ? peak:peak_three;
 wire [31:0] block_cost=half_cost+next_cost;
 wire [15:0] block_peak=half_peak>next_peak ? half_peak:next_peak;
 wire [32:0] baseline_pair={1'b0,baseline_cost}+{1'b0,block_cost};
 wire [39:0] trial_weight={8'd0,probe_cost}<<7;
 wire [39:0] pair_weight=({7'd0,baseline_pair}<<6)-({7'd0,baseline_pair}<<1);
 wire trial_better=trial_weight<pair_weight &&
  {1'b0,probe_peak}<={1'b0,(baseline_peak>block_peak ? baseline_peak:block_peak)}+17'd8;
 wire [13:0] current_step=axis ? {4'd0,step_p}:{4'd0,step_d};
 wire [13:0] best_axis=axis ? best_p:best_d;
 wire [13:0] minimum_axis=axis ? P_MIN:D_MIN;
 wire [13:0] maximum_axis=axis ? P_MAX:D_MAX;
 wire signed [14:0] proposed_axis=negative ? $signed({1'b0,best_axis})-$signed({1'b0,current_step}):
  $signed({1'b0,best_axis})+$signed({1'b0,current_step});
 wire proposed_ok=proposed_axis>=$signed({1'b0,minimum_axis}) && proposed_axis<=$signed({1'b0,maximum_axis});
 function [13:0] ramp;
  input [13:0] current,target;input [4:0] amount;
  begin
   if({1'b0,target}>{1'b0,current}+amount)ramp=current+amount;
   else if({1'b0,current}>{1'b0,target}+amount)ramp=current-amount;
   else ramp=target;
  end
 endfunction
 task clear_window;
  begin samples<=0;cost<=0;peak<=0;half_window<=0;half_cost<=0;half_peak<=0;evaluate_pipe<=0;end
 endtask
 task decision;
  input [7:0] code,why;input [31:0] energy_value;input [15:0] peak_value;
  begin
   diagnostic_pulse<=1;diagnostic_seq<=diagnostic_seq+1'b1;diagnostic_epoch<=epoch_three;
   diagnostic_code<=code;diagnostic_reason<=why;
   // Diagnostic8-bit centres are D/4; full absolute P/D are in16-bit Q4 slots.
   diagnostic_center_before<=$signed({1'b0,best_d[12:6]});
   diagnostic_center_after<=$signed({1'b0,probe_d[12:6]});
   diagnostic_reference_energy<=baseline_cost;diagnostic_window_energy<=energy_value;
   diagnostic_reference_peak<=baseline_peak;diagnostic_window_peak<=peak_value;diagnostic_event_samples<=64;
  end
 endtask
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   paused<=1;raw_error<=0;raw_difference<=0;raw_position<=0;raw_request<=0;raw_actual<=0;raw_saturating<=0;raw_epoch<=0;
   allowed<=0;enabled<=0;ready<=0;manual_bank<=0;freeze_reason<=OFF;best_p<=784;best_d<=4160;
   wanted_p<=784;wanted_d<=4160;applied_p<=784;applied_d<=4160;probe_p<=784;probe_d<=4160;
   axis<=0;negative<=0;tried_reverse<=0;step_p<=16;step_d<=256;phase<=0;warmup<=128;
   samples<=0;half_window<=0;cost<=0;half_cost<=0;peak<=0;half_peak<=0;
   baseline_cost<=0;probe_cost<=0;baseline_peak<=0;probe_peak<=0;last_epoch<=0;
   evaluate_pipe<=0;evaluate_cost<=0;evaluate_peak<=0;evaluate_pair<=0;evaluate_weight<=0;
   evaluate_peak_limit<=0;evaluate_peak_ok<=0;evaluate_better<=0;
   feature_pipe<=0;invalid_one<=0;invalid_two<=0;invalid_three<=0;saturated_one<=0;
   magnitude_e<=0;magnitude_d<=0;magnitude_x<=0;magnitude_u<=0;
   square_e<=0;square_d<=0;square_x<=0;sample_cost<=0;peak_one<=0;peak_two<=0;peak_three<=0;
   epoch_one<=0;epoch_two<=0;epoch_three<=0;
   invalid_reason_one<=0;invalid_reason_two<=0;invalid_reason_three<=0;
   learn_revision<=0;adapt_epoch<=0;candidate_epoch<=0;candidate_revision<=0;
   diagnostic_pulse<=0;diagnostic_seq<=0;diagnostic_epoch<=0;diagnostic_code<=0;diagnostic_reason<=0;
   diagnostic_center_before<=0;diagnostic_center_after<=0;diagnostic_reference_energy<=0;
   diagnostic_window_energy<=0;diagnostic_reference_peak<=0;diagnostic_window_peak<=0;diagnostic_event_samples<=0;
  end else begin
   allowed<=allowed_now;paused<=evidence_pause;
   diagnostic_pulse<=0;feature_pipe<={feature_pipe[2:0],1'b0};evaluate_pipe<={evaluate_pipe[2:0],1'b0};
   if(manual_write)begin enabled<=0;ready<=0;feature_pipe<=0;clear_window;phase<=0;freeze_reason<=OFF;end
   else if(enable_write)begin
    enabled<=enable_value;ready<=enable_value && eligible && !integral_nonzero;
    manual_bank<=gains_base;candidate_revision<=manual_revision;candidate_epoch<=sample_epoch;
    best_p<=manual_p;best_d<=manual_d;wanted_p<=manual_p;wanted_d<=manual_d;
    applied_p<=manual_p;applied_d<=manual_d;probe_p<=manual_p;probe_d<=manual_d;
    axis<=0;negative<=0;tried_reverse<=0;step_p<=16;step_d<=256;phase<=0;warmup<=128;
    feature_pipe<=0;clear_window;freeze_reason<=enable_value ? WARMUP:OFF;
   end else if(!allowed)begin
    feature_pipe<=0;clear_window;phase<=0;warmup<=128;wanted_p<=best_p;wanted_d<=best_d;applied_p<=best_p;applied_d<=best_d;
    if(!enabled)freeze_reason<=OFF;
    else if(fault)freeze_reason<=FAULT;
    else if(stop || !run_balance)freeze_reason<=STOPPED;
    else if(integral_nonzero)freeze_reason<=INTEGRAL;
    else freeze_reason<=GAIN;
   end else begin
    if(commit_tick)begin
     applied_p<=ramp(applied_p,wanted_p,5'd2);applied_d<=ramp(applied_d,wanted_d,5'd16);
     candidate_epoch<=last_epoch;adapt_epoch<=adapt_epoch+1'b1;
    end
    if(paused)begin
     // Learning pauses without throwing away the learned gain bank. A trial
     // interrupted by motion is rolled back; motor commands are never gated.
     wanted_p<=best_p;wanted_d<=best_d;phase<=0;warmup<=128;feature_pipe<=0;clear_window;
     freeze_reason<=WARMUP;
    end else begin
     if(sample_valid)begin
      feature_pipe[0]<=1;raw_error<=angle_error;raw_difference<=angle_diff;
      raw_position<=position_error;raw_request<=requested_moto;raw_actual<=actual_q15;
      raw_saturating<=saturating;raw_epoch<=sample_epoch;
     end
     if(feature_pipe[0])begin
      magnitude_e<=abs_e;magnitude_d<=abs_d>127 ? 16'd127:abs_d;
      magnitude_x<=abs_x[8:0];magnitude_u<=abs_u;
      invalid_one<=raw_saturating || abs_request>=6000 || abs_u>=30000 || abs_e>=256 || abs_x>=300;
      invalid_reason_one<=(raw_saturating || abs_request>=6000 || abs_u>=30000) ? SATURATION:ENVELOPE;
      peak_one<=abs_e;epoch_one<=raw_epoch;
     end
     if(feature_pipe[1])begin
      square_e<=magnitude_e[7:0]*magnitude_e[7:0];square_d<=magnitude_d[6:0]*magnitude_d[6:0];
      square_x<=magnitude_x*magnitude_x;invalid_two<=invalid_one;invalid_reason_two<=invalid_reason_one;
      peak_two<=peak_one;epoch_two<=epoch_one;
     end
     if(feature_pipe[2])begin
      sample_cost<=({16'd0,square_e}<<4)+({16'd0,square_d}<<2)+{14'd0,square_x}+{20'd0,magnitude_u[15:4]};
      invalid_three<=invalid_two;invalid_reason_three<=invalid_reason_two;peak_three<=peak_two;epoch_three<=epoch_two;
     end
     if(feature_pipe[3])begin
      last_epoch<=epoch_three;
      if(invalid_three)begin
       wanted_p<=best_p;wanted_d<=best_d;phase<=0;warmup<=128;clear_window;
       freeze_reason<=invalid_reason_three;decision(5,invalid_reason_three,0,peak_three);
      end else if(applied_p!=wanted_p || applied_d!=wanted_d)begin clear_window;warmup<=128;freeze_reason<=WARMUP;end
      else if(warmup!=0)begin warmup<=warmup-1'b1;clear_window;freeze_reason<=WARMUP;end
      else begin
       freeze_reason<=phase==0 ? 8'd0:TRIAL;
       cost<=next_cost;peak<=next_peak;samples<=samples+1'b1;
       if(samples==63)begin
        samples<=0;cost<=0;peak<=0;
        if(!half_window)begin half_window<=1;half_cost<=next_cost;half_peak<=next_peak;end
        else begin
         half_window<=0;half_cost<=0;half_peak<=0;
         evaluate_cost<=block_cost;evaluate_peak<=block_peak;evaluate_pipe[0]<=1;
        end
       end
      end
     end
     if(evaluate_pipe[0])begin
      evaluate_pair<={1'b0,baseline_cost}+{1'b0,evaluate_cost};
      evaluate_peak_limit<={1'b0,(baseline_peak>evaluate_peak ? baseline_peak:evaluate_peak)}+17'd8;
     end
     if(evaluate_pipe[1])begin
      evaluate_weight<=({7'd0,evaluate_pair}<<6)-({7'd0,evaluate_pair}<<1);
      evaluate_peak_ok<={1'b0,probe_peak}<=evaluate_peak_limit;
     end
     if(evaluate_pipe[2])evaluate_better<=({8'd0,probe_cost}<<7)<evaluate_weight && evaluate_peak_ok;
     if(evaluate_pipe[3] && !(feature_pipe[3] && invalid_three))begin
         case(phase)
          0:begin
           baseline_cost<=evaluate_cost;baseline_peak<=evaluate_peak;
           if(evaluate_peak<5)begin freeze_reason<=QUIET;end
           else if(!proposed_ok)begin
            if(!tried_reverse)begin negative<=!negative;tried_reverse<=1;end
            else begin axis<=!axis;negative<=0;tried_reverse<=0;end
           end else begin
            probe_p<=axis ? proposed_axis[13:0]:best_p;probe_d<=axis ? best_d:proposed_axis[13:0];
            wanted_p<=axis ? proposed_axis[13:0]:best_p;wanted_d<=axis ? best_d:proposed_axis[13:0];
            phase<=1;warmup<=128;decision(negative ? 7:6,TRIAL,evaluate_cost,evaluate_peak);
           end
          end
          1:begin
           probe_cost<=evaluate_cost;probe_peak<=evaluate_peak;wanted_p<=best_p;wanted_d<=best_d;
           phase<=2;warmup<=128;decision(11,TRIAL,evaluate_cost,evaluate_peak);
          end
          2:begin
           phase<=0;warmup<=128;
           if(evaluate_better)begin
            best_p<=probe_p;best_d<=probe_d;wanted_p<=probe_p;wanted_d<=probe_d;
            learn_revision<=learn_revision+1'b1;tried_reverse<=0;
            if(axis)begin if(step_p<32)step_p<=step_p<<1;end
            else begin if(step_d<512)step_d<=step_d<<1;end
            decision(8,0,probe_cost,probe_peak);
           end else begin
            if(!tried_reverse)begin negative<=!negative;tried_reverse<=1;end
            else begin
             if(axis)begin if(step_p>4)step_p<=step_p>>1;end
             else begin if(step_d>32)step_d<=step_d>>1;end
             axis<=!axis;negative<=0;tried_reverse<=0;
            end
            decision(9,COOLDOWN,probe_cost,probe_peak);
           end
          end
          default:begin phase<=0;warmup<=128;wanted_p<=best_p;wanted_d<=best_d;end
         endcase
     end
    end
   end
  end
 end
endmodule

// Exact binary32 RNE of base*(256+code)/256.
// Nine MSB-first shift/add clocks replace each24x9 DSP product. Candidate
// completion is nine50MHz clocks later; the5ms feature/25ms adoption cadence
// and all PID/ADC/driver timing remain unchanged. A new start cancels old work.
module forest_d1_adaptive_scale(
 input clk,rst_n,start,input [31:0] base_bits,input signed [7:0] code,
 output reg valid,output reg [31:0] result_bits
);
 reg one,two,busy;
 reg [3:0] step;
 reg [23:0] mantissa;
 reg [8:0] coefficient;
 reg [32:0] product;
 reg [8:0] exp_one,exp_two;
 reg [31:0] pass_one,pass_two;
 reg bypass_one,bypass_two;
 reg [24:0] rounded;
 wire signed [9:0] factor=10'sd256+{{2{code[7]}},code};
 wire [32:0] next_product={product[31:0],1'b0}+
                                  (coefficient[8] ? {9'd0,mantissa}:33'd0);
 wire [31:0] raw_result=rounded[24] ? {1'b0,(exp_two[7:0]+8'd1),rounded[23:1]}:
                                                   {1'b0,exp_two[7:0],rounded[22:0]};
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   one<=0;two<=0;busy<=0;step<=0;mantissa<=0;coefficient<=0;
   valid<=0;product<=0;exp_one<=0;exp_two<=0;pass_one<=0;pass_two<=0;
   bypass_one<=1;bypass_two<=1;rounded<=0;result_bits<=0;
  end else if(start)begin
   one<=0;two<=0;valid<=0;busy<=1;step<=0;product<=0;
   mantissa<={1'b1,base_bits[22:0]};coefficient<=factor[8:0];
   exp_one<={1'b0,base_bits[30:23]};pass_one<=base_bits;
   bypass_one<=code==0 || base_bits==0 || base_bits[31] || base_bits[30:23]<2 ||
     base_bits[30:23]>253 || code < -8'sd64 || code > 8'sd112;
  end else begin
   one<=0;two<=one;valid<=two;
   if(busy)begin
    product<=next_product;coefficient<={coefficient[7:0],1'b0};
    if(step==8)begin busy<=0;one<=1;end
    else step<=step+1'b1;
   end
   if(one)begin
    pass_two<=pass_one;bypass_two<=bypass_one;
    if(product[32])begin rounded<={1'b0,product[32:9]}+(product[8] && (|product[7:0] || product[9]));exp_two<=exp_one+1'b1;end
    else if(product[31])begin rounded<={1'b0,product[31:8]}+(product[7] && (|product[6:0] || product[8]));exp_two<=exp_one;end
    else begin rounded<={1'b0,product[30:7]}+(product[6] && (|product[5:0] || product[7]));exp_two<=exp_one-1'b1;end
   end
   if(two)result_bits<=bypass_two ? pass_two:raw_result>32'h461c4000 ? 32'h461c4000:raw_result;
  end
 end
endmodule
