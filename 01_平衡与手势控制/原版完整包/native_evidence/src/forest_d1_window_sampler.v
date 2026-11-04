// Original Forest15 capture offsets and ordinary integer mean at5ms.
// Preserve the board mapper's four-clock request/response qualification.
// Mean ready150875 (3.0175ms); cadence250000 (5ms).
module forest_d1_window_sampler #(parameter integer SEPARATE_RAW_GUARD=0)(
 input clk,input rst_n,input [11:0] angle_in,input angle_valid,
 input [11:0] raw_angle_in,input raw_angle_valid,
 input signed [15:0] encoder_in,output wire angle_capture,output wire frame_start,
 output reg sample_en,output reg [11:0] angle_adc12,
 output reg signed [15:0] encoder_sample,output reg [31:0] frame_number,
 output reg acquisition_fault,
 // Independent original raw15 mean remains a guard, even when the
 // control mean uses the ADC medoid/1ms Q4 front end.
 output reg [11:0] raw_guard_mean,
 output reg source_window_bad /* synthesis syn_preserve=1 syn_keep=1 */
);
 reg [17:0] cycle,next_capture;reg [3:0] sample_index;
 reg [4:0] committed;reg [15:0] adc_sum,raw_adc_sum;
 // All15 captures finish nearly200us before publication. Precompute the
 // EXACT unsigned /15 during that idle interval, keeping the original edge.
 // floor(n/15)==floor(n*69906/2^20) for every16-bit n.
 reg [32:0] adc_high,adc_low,adc_product,raw_high,raw_low,raw_product;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin adc_high<=0;adc_low<=0;adc_product<=0;raw_high<=0;raw_low<=0;raw_product<=0;end
  else begin
   adc_high<=({17'd0,adc_sum}<<16)+({17'd0,adc_sum}<<12);
   adc_low<=({17'd0,adc_sum}<<8)+({17'd0,adc_sum}<<4)+({17'd0,adc_sum}<<1);
   adc_product<=adc_high+adc_low;
   raw_high<=({17'd0,raw_adc_sum}<<16)+({17'd0,raw_adc_sum}<<12);
   raw_low<=({17'd0,raw_adc_sum}<<8)+({17'd0,raw_adc_sum}<<4)+({17'd0,raw_adc_sum}<<1);
   raw_product<=raw_high+raw_low;
  end
 end

 wire guard_valid=SEPARATE_RAW_GUARD ? raw_angle_valid:angle_valid;
 wire [11:0] guard_angle=SEPARATE_RAW_GUARD ? raw_angle_in:angle_in;
 reg [3:0] expected_valid_pipe;
 reg frame_invalid;
 function [17:0] capture_cycle;
  input [3:0] index;
  // Exact original: ceil((index+1)*175/3) + index*10000.
  // No runtime multiply/divide chain on the capture scheduling path.
  begin case(index)
   0:capture_cycle=18'd59;1:capture_cycle=18'd10117;
   2:capture_cycle=18'd20175;3:capture_cycle=18'd30234;
   4:capture_cycle=18'd40292;5:capture_cycle=18'd50350;
   6:capture_cycle=18'd60409;7:capture_cycle=18'd70467;
   8:capture_cycle=18'd80525;9:capture_cycle=18'd90584;
   10:capture_cycle=18'd100642;11:capture_cycle=18'd110700;
   12:capture_cycle=18'd120759;13:capture_cycle=18'd130817;
   14:capture_cycle=18'd140875;15:capture_cycle=18'd150934;
   default:capture_cycle=18'd59;
  endcase end
 endfunction
 assign angle_capture=rst_n && sample_index<15 && cycle==next_capture;
 assign frame_start=rst_n && cycle==0;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin cycle<=0;next_capture<=59;sample_index<=0;committed<=0;adc_sum<=0;raw_adc_sum<=0;
   expected_valid_pipe<=0;frame_invalid<=0;
   sample_en<=0;angle_adc12<=3085;raw_guard_mean<=3085;source_window_bad<=0;
   encoder_sample<=10000;frame_number<=0;acquisition_fault<=0;end
  else begin
   sample_en<=0;
   // Each request has exactly the mapper's four-clock valid latency. Missing
   // or repeated responses poison the frame instead of accidentally averaging
   // fifteen wrong captures. Acquisition fault remains latched until ZERO.
   expected_valid_pipe<={expected_valid_pipe[2:0],angle_capture};
   cycle<=cycle==249999 ? 0 : cycle+1'b1;
   if(cycle==0)begin encoder_sample<=encoder_in;adc_sum<=0;raw_adc_sum<=0;sample_index<=0;committed<=0;
    frame_invalid<=0;
    next_capture<=59;frame_number<=frame_number+1'b1;end
   if(angle_capture)begin sample_index<=sample_index+1'b1;
    next_capture<=capture_cycle(sample_index+1'b1);end
   if(angle_valid!=expected_valid_pipe[3] || guard_valid!=expected_valid_pipe[3])begin frame_invalid<=1;acquisition_fault<=1;end
   if(angle_valid && guard_valid && expected_valid_pipe[3])begin
    if(committed<15)begin
     adc_sum<=adc_sum+{4'b0,angle_in};raw_adc_sum<=raw_adc_sum+{4'b0,guard_angle};
     committed<=committed+1'b1;
    end else begin frame_invalid<=1;acquisition_fault<=1;end
   end
   // The last accepted capture precedes this edge by almost200us.
   // 15*4095<=65535. Independent sums use the same15 capture times.
   if(cycle==150875)begin
    if(committed==15 && !frame_invalid && angle_valid==expected_valid_pipe[3] && guard_valid==expected_valid_pipe[3])begin
     raw_guard_mean<=raw_product[31:20];
     angle_adc12<=adc_product[31:20];
     // Use the same D values and edge as the two published means.
     source_window_bad<=raw_product[31:20]<12'd2285 || raw_product[31:20]>12'd3885 ||
                        adc_product[31:20]<12'd2285 || adc_product[31:20]>12'd3885;
     sample_en<=1;
    end
    else acquisition_fault<=1;
   end
  end
 end
endmodule
