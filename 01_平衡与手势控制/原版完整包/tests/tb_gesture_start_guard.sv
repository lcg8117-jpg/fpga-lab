`timescale 1ns/1ps
module tb_gesture_start_guard;
 reg clk=0;always #5 clk=~clk;
 reg rst_n=0,flush=0,sample=0,inhibit=0;
 reg [7:0] raw43=0,raw44=0;
 reg [2:0] mapping=0;
 wire valid,armed;wire [3:0] code;wire [31:0] seq,suppressed;
 wire [7:0] reason,state;
 integer checks=0,k,m,before_seq;
 forest_d1_gesture_decode dut(.clk(clk),.rst_n(rst_n),.flush(flush),.sample_valid(sample),
  .motion_inhibit(inhibit),.raw43(raw43),.raw44(raw44),.map_flags(mapping),
  .event_valid(valid),.event_code(code),.event_seq(seq),.suppressed_count(suppressed),
  .decode_reason(reason),.diagnostic_state(state),.armed(armed));
 task check;input condition;input [511:0] message;begin
  checks=checks+1;if(condition!==1'b1)begin $display("FAIL optical %0s seq=%0d code=%0d reason=%0d armed=%0d",message,seq,code,reason,armed);$fatal(1);end
 end endtask
 task feed;input [7:0] a,b;begin
  @(negedge clk);raw43=a;raw44=b;sample=1;
  @(negedge clk);sample=0;
 end endtask
 task release_hand;begin feed(0,0);feed(0,0);check(armed,"two neutral samples rearm");end endtask
 initial begin
  repeat(3)@(negedge clk);rst_n=1;
  // All direction-map combinations and all four motion classifications.
  for(m=0;m<8;m=m+1)begin
   mapping=m;
   for(k=0;k<4;k=k+1)begin
    release_hand();inhibit=1;before_seq=seq;
    case(k)0:feed(1,0);1:feed(2,0);2:feed(64,0);default:feed(128,0);endcase
    check(!valid && seq==before_seq && reason==4 && !armed,"guarded movement never creates an event");
    inhibit=0;
    case(k)0:feed(1,0);1:feed(2,0);2:feed(64,0);default:feed(128,0);endcase
    check(!valid && seq==before_seq,"guard expiry cannot replay held movement");
    inhibit=1;feed(8,0);check(!valid && seq==before_seq && reason==4,"down is inhibited before event creation");
    inhibit=0;feed(8,0);check(!valid && seq==before_seq,"held down is not replayed after expiry");
    release_hand();inhibit=0;
    case(k)0:feed(1,0);1:feed(2,0);2:feed(64,0);default:feed(128,0);endcase
    check(valid && seq==before_seq+1,"fresh released movement recognized normally after expiry");
   end
  end
  // A held movement does not become a new gesture when the guard toggles.
  release_hand();inhibit=0;feed(2,0);before_seq=seq;
  inhibit=1;feed(2,0);check(!valid && seq==before_seq,"inhibit while holding creates no duplicate");
  release_hand();inhibit=0;feed(4,0);check(valid && code==3,"up-slide classification is unchanged");
  feed(0,1);check(valid && code==9,"legacy wave remains observable and unchanged");
  release_hand();inhibit=1;feed(8'hc3,0);check(!valid && reason==4,"guarded mixed movement bits create no guessed event");
  release_hand();before_seq=seq;feed(0,1);check(!valid && seq==before_seq && reason==4,"wave also respects command interval");
  inhibit=0;feed(0,1);check(!valid && seq==before_seq,"held wave not replayed at expiry");
  release_hand();feed(8,0);check(valid && code==4 && seq==before_seq+1,"fresh down STOP works after interval and release");
  $display("PASS OPTICAL_START_GUARD checks=%0d maps=8 no_motion_event_under_guard=1 down_and_wave_guarded=1 no_replay=1",checks);$finish;
 end
 initial begin #1000000;$fatal(1,"optical guard timeout");end
endmodule
