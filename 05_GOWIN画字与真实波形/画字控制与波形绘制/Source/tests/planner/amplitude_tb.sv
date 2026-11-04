`timescale 1ns/1ps
module amplitude_tb;
 parameter integer GUARD_TEST=0;
 parameter integer DRAW_TEST=0;
 reg clk=0; always #10 clk=~clk;
 reg rst_n=0,tick=0,reanchor=0,cancel=0,capture=0,balance=0,kit=0;
 reg signed [31:0] raw=0;
 reg command_valid=0,cfg_write=0; reg [1:0] kind=2;
 reg [2:0] shape=3; reg [7:0] cfg_id=0; reg [31:0] cfg_value=0;
 wire signed [63:0] actual,reference,origin,goal;
 wire signed [31:0] v,a;
 wire active,fault,accepted,rejected,done,settled,cfg_ok,cfg_error;
 wire [2:0] state; wire [7:0] reason;
 reg buf_write=0;wire buf_ok,buf_error,buf_valid;wire [9:0] buf_length;
 wire [8:0] buf_address;wire signed [15:0] buf_sample;
 forest_draw_buffer buffer(.clk(clk),.rst_n(rst_n),.tick_5ms(tick),.upload_permitted(!balance),.abort(cancel),
  .cfg_write(buf_write),.cfg_id(cfg_id),.cfg_value(cfg_value),.cfg_ok(buf_ok),.cfg_error(buf_error),
  .cfg_revision(),.cfg_read_value(),.committed(buf_valid),.length(buf_length),.read_address(buf_address),.sample(buf_sample));
 forest_motion_planner #(.VARIANT(5)) dut(
  .clk(clk),.rst_n(rst_n),.tick_5ms(tick),.reanchor(reanchor),.cancel(cancel),
   .draw_tracking(1'b0),.draw_valid(buf_valid),.draw_length(buf_length),.draw_sample(buf_sample),.draw_address(buf_address),.upload_hold_ready(),
  .cfg_revision(),.cfg_read_value(),.enc_wide_physical(),.encoder_local(),.target_local(),.direction(),
  .stop_reason(),.event_count(),.last_request_id(),.last_action(),.measured_velocity_cps(),
  .reference_velocity_cps(),.tracking_error(),.settle_ticks(),.planner_phase(),
  .capture_hold(capture),.balance_active(balance),.kit_ip570(kit),.encoder_raw(raw),
  .command_valid(command_valid),.command(3'd0),.action_kind(kind),.action_index(shape),
  .request_id(32'd1),.left_positive(1'b1),.cw_positive(1'b1),
  .cfg_write(cfg_write),.cfg_id(cfg_id),.cfg_value(cfg_value),.cfg_ok(cfg_ok),.cfg_error(cfg_error),
  .encoder_wide(actual),.target_wide(reference),.origin_wide(origin),.final_wide(goal),
  .active(active),.fault(fault),.state(state),.accepted(accepted),.rejected(rejected),.reason(reason),
  .trajectory_done(done),.tracking_settled(settled),.velocity_q(v),.acceleration_q(a));
 integer k,d,s,n,file,summary,checks=0;
 reg follow=1; reg signed [63:0] wanted;
 reg signed [63:0] minref,maxref,mingoal,maxgoal;
 reg signed [31:0] oldv,olda,maxv,maxa,maxj;
 always @* wanted=kit ? reference:(reference*27+(reference>=0 ? 25:-25))/26;
 always @(negedge clk) if(rst_n && balance && follow && !reanchor)begin
  if($signed(raw)<wanted)raw=raw+1;else if($signed(raw)>wanted)raw=raw-1;
 end
 task clocks(input integer count);repeat(count)@(negedge clk);endtask
 task reset_case(input integer whichkit,input integer direction);begin
  rst_n=0;balance=0;tick=0;command_valid=0;cfg_write=0;raw=0;kit=whichkit;clocks(5);
  rst_n=1;clocks(3);reanchor=1;clocks(1);reanchor=0;clocks(3);
  cfg_id=6;cfg_value=direction;cfg_write=1;clocks(1);
  if(!cfg_ok || cfg_error)$fatal(1,"direction rejected");cfg_write=0;clocks(3);
  balance=1;clocks(3);oldv=0;olda=0;minref=0;maxref=0;mingoal=0;maxgoal=0;maxv=0;maxa=0;maxj=0;
 end endtask
 task step;begin
  tick=1;clocks(1);tick=0;clocks(63);checks=checks+1;
  if(fault)$fatal(1,"unexpected fault kit=%0d dir=%0d shape=%0d tick=%0d ref=%0d",k,d,s,n,reference);
  if(v-oldv!=a)$fatal(1,"acceleration provenance");
  if(a-olda>13 || olda-a>13)$fatal(1,"jerk bound");
  if(a>524 || a< -524)$fatal(1,"acceleration bound");
  if(reference<minref)minref=reference;if(reference>maxref)maxref=reference;
  if(dut.wave_goal<mingoal)mingoal=dut.wave_goal;if(dut.wave_goal>maxgoal)maxgoal=dut.wave_goal;
  if(v>maxv)maxv=v;if(-v>maxv)maxv=-v;
  if(a>maxa)maxa=a;if(-a>maxa)maxa=-a;
  if(a-olda>maxj)maxj=a-olda;if(olda-a>maxj)maxj=olda-a;
  oldv=v;olda=a;
  $fdisplay(file,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",k,d,shape,n,reference,actual,dut.wave_goal,v,a,state);
 end endtask
 task setting(input integer field,input integer value,input integer expected);begin
  cfg_id=field;cfg_value=value;cfg_write=1;clocks(1);
  if(cfg_ok!==expected[0] || cfg_error===expected[0])$fatal(1,"configuration guard field=%0d value=%0d",field,value);
  cfg_write=0;clocks(3);checks=checks+1;
 end endtask
 task start_wave;begin
  kind=2;shape=3;command_valid=1;clocks(1);
  if(!accepted)$fatal(1,"guard fixture start rejected reason=%0d",reason);
  command_valid=0;clocks(3);
 end endtask
 function integer drawn_point(input integer i);
  if(i<=1)drawn_point=0;else if(i<=41)drawn_point=(i-1)*2;
  else if(i==42)drawn_point=80;else if(i<=82)drawn_point=80-(i-42)*2;else drawn_point=0;
 endfunction
 task buf_setting(input integer field,input reg [31:0] value);begin
  cfg_id=field;cfg_value=value;buf_write=1;clocks(1);
  if(!buf_ok || buf_error)$fatal(1,"custom buffer setup rejected");buf_write=0;clocks(2);
 end endtask
 task custom;integer i;reg [31:0] checksum,word_value;reg signed [63:0] expected;begin
  for(k=0;k<2;k=k+1)for(d=0;d<2;d=d+1)begin
   reset_case(k,d);balance=0;clocks(3);buf_setting(0,85);checksum=32'h6a09e667^32'd85;
   for(i=0;i<85;i=i+1)begin
    word_value=(i<<16)|(drawn_point(i)&16'hffff);buf_setting(1,word_value);
    checksum={checksum[30:0],checksum[31]}^word_value;
   end
   if(checksum!==32'h82472974)$fatal(1,"Host/RTL checksum vector mismatch");
   buf_setting(2,32'h82472974);balance=1;n=0;repeat(25)step();shape=7;kind=2;command_valid=1;clocks(1);
   if(!accepted)$fatal(1,"committed custom program not admitted");command_valid=0;clocks(3);
   n=0;while(!done && n<20000)begin
    step();n=n+1;
    if(state==6 && dut.wave_shape==7 && dut.draw_tick==10)begin
     expected=d ? drawn_point(dut.draw_index):-drawn_point(dut.draw_index);
     if(dut.wave_goal!==expected)$fatal(1,"FPGA did not play uploaded goal at sample cadence");checks=checks+1;
    end
   end
   if(!done || reference!=0)$fatal(1,"custom program did not return to origin");
   repeat(120)step();if(!settled)$fatal(1,"custom ideal follower failed settling");
  end
  $display("PASS draw_playback cases=4 checks=%0d kits=2 directions=2 idealFollowerOnly=1",checks);
 end endtask
 task guards;integer j;reg signed [31:0] saved_v,saved_a;reg signed [63:0] saved_ref;begin
  for(k=0;k<2;k=k+1)begin
   follow=1;reset_case(k,1);kind=2;shape=3;command_valid=1;clocks(1);
   if(!rejected || accepted)$fatal(1,"startup without velocity qualification accepted");command_valid=0;
   balance=0;clocks(3);
   setting(0,260,1);setting(0,261,0);setting(0,59,0);
   setting(1,320,1);setting(1,640,0);setting(2,1600,1);setting(2,3200,0);
   setting(7,192,1);setting(7,193,0);setting(7,0,0);
   setting(8,16000,1);setting(8,4000,0);setting(9,2,1);setting(9,3,0);setting(255,1,0);
   balance=1;repeat(25)step();setting(7,192,0);
   kind=1;shape=6;command_valid=1;clocks(1);
   if(!rejected || accepted)$fatal(1,"direct position accepted");command_valid=0;clocks(2);
   start_wave();repeat(1800)step();saved_v=v;saved_a=a;saved_ref=reference;
   command_valid=1;clocks(1);
   if(!rejected || accepted || v!==saved_v || a!==saved_a || reference!==saved_ref)$fatal(1,"active replacement changed current job");
   command_valid=0;clocks(2);kind=3;command_valid=1;clocks(1);
   if(!accepted || v!==saved_v || a!==saved_a || reference!==saved_ref)$fatal(1,"cancel changed reference instantaneously");
   command_valid=0;clocks(2);j=0;while(!done && j<5000)begin step();j=j+1;end
   if(!done || v || a)$fatal(1,"smooth cancel never stopped");checks=checks+1;
   reset_case(k,1);repeat(25)step();start_wave();repeat(500)step();balance=0;clocks(3);
   if(active || v || a)$fatal(1,"lost balance left a moving planner");balance=1;clocks(3);
   if(active)$fatal(1,"lost balance replayed automatically");checks=checks+1;
   reset_case(k,1);repeat(25)step();kind=2;command_valid=1;cancel=1;clocks(1);
   if(accepted || !rejected || active)$fatal(1,"STOP lost to a coincident start");command_valid=0;cancel=0;clocks(3);checks=checks+1;
   reset_case(k,1);repeat(25)step();follow=0;
   for(j=0;j<410;j=j+1)begin raw=raw+1;clocks(1);end
   if(!fault || active)$fatal(1,"actual-position envelope did not latch");clocks(5);
   if(!fault)$fatal(1,"position fault disappeared without deliberate recovery");checks=checks+1;
   follow=1;reset_case(k,1);repeat(25)step();start_wave();follow=0;
   for(j=0;j<4400;j=j+1)begin raw=j[0] ? 1:0;clocks(1);end
   if(!fault || active)$fatal(1,"total physical-edge budget did not stop oscillatory travel");checks=checks+1;
   reset_case(k,1);repeat(25)step();start_wave();follow=0;
   repeat(10)begin raw=raw+1;clocks(1);end
   for(j=0;j<30010;j=j+1)begin tick=1;clocks(1);tick=0;clocks(63);end
   if(!fault || active)$fatal(1,"finite duration watchdog failed");checks=checks+1;
   follow=1;
  end
  $display("PASS trajectory_guards kits=2 checks=%0d",checks);
 end endtask
 initial begin
  file=$fopen(GUARD_TEST ? "guards.csv":"amplitude.csv","w");summary=$fopen(GUARD_TEST ? "guards_summary.csv":"summary.csv","w");
  $fdisplay(file,"kit,direction,shape,tick,reference,ideal_actual,rom_goal,velocity_q,acceleration_q,state");
  $fdisplay(summary,"kit,direction,shape,min_reference,max_reference,min_goal,max_goal,max_velocity_q,max_acceleration_q,max_jerk_q,ticks");
  if(DRAW_TEST)begin custom();$fclose(file);$fclose(summary);$finish;end
  if(GUARD_TEST)begin guards();$fclose(file);$fclose(summary);$finish;end
  for(k=0;k<2;k=k+1)for(d=0;d<2;d=d+1)for(s=3;s<=4;s=s+1)begin
   reset_case(k,d);n=0;repeat(25)step();shape=s;command_valid=1;clocks(1);
   if(!accepted)$fatal(1,"trajectory rejected %0d",reason);command_valid=0;clocks(3);
   n=0;while(!done && n<32000)begin step();n=n+1;end
   if(!done || reference!=0)$fatal(1,"finite return failed");
   repeat(120)step();if(!settled)$fatal(1,"ideal-follower settle failed");
   $fdisplay(summary,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",k,d,s,minref,maxref,mingoal,maxgoal,maxv,maxa,maxj,n);
  end
  $display("PASS amplitude cases=8 checks=%0d idealFollowerOnly=1",checks);
  $fclose(file);$fclose(summary);$finish;
 end
 initial begin #2000000000;$fatal(1,"timeout");end
endmodule

