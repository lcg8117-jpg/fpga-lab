`timescale 1ns/1ps
module c4_guard_tb;
 reg clk=0;always #10 clk=~clk;
 reg rst_n=0,tick=0,balance=0,reanchor=0,cancel=0,capture=0;
 reg signed[31:0]raw=0;reg kit=1,dir=1;
 reg write=0;reg[7:0]field=0;reg[31:0]value=0;
 reg command_valid=0;reg[1:0]kind=2;reg[2:0]shape=7;
 wire buf_ok,buf_error,committed;wire[9:0]length;wire[8:0]address;wire signed[15:0]sample;
 wire signed[63:0]reference,actual,origin;wire signed[31:0]v,a;
 wire fault,accepted,done,settled;wire[2:0]state;
 reg cfg_write=0;wire cfg_ok,cfg_error;

 forest_draw_buffer buffer(.clk(clk),.rst_n(rst_n),.tick_5ms(tick),.upload_permitted(!balance),.abort(1'b0),
 .cfg_write(write),.cfg_id(field),.cfg_value(value),.cfg_ok(buf_ok),.cfg_error(buf_error),.cfg_revision(),.cfg_read_value(),
 .committed(committed),.length(length),.read_address(address),.sample(sample));
 forest_motion_planner #(.VARIANT(5)) dut(.clk(clk),.rst_n(rst_n),.tick_5ms(tick),.reanchor(reanchor),.cancel(cancel),
 .capture_hold(capture),.balance_active(balance),.kit_ip570(kit),.encoder_raw(raw),.command_valid(command_valid),.command(3'd0),
 .action_kind(kind),.action_index(shape),.request_id(32'd42),.left_positive(1'b1),.cw_positive(1'b1),
  .draw_tracking(1'b0),.draw_valid(committed),.draw_length(length),.draw_sample(sample),.draw_address(address),.upload_hold_ready(),
 .cfg_write(cfg_write),.cfg_id(field),.cfg_value(value),.cfg_ok(cfg_ok),.cfg_error(cfg_error),.cfg_revision(),.cfg_read_value(),
 .encoder_wide(actual),.target_wide(reference),.origin_wide(origin),.final_wide(),.enc_wide_physical(),.encoder_local(),.target_local(),
 .active(),.fault(fault),.state(state),.direction(),.accepted(accepted),.rejected(),.reason(),.stop_reason(),.event_count(),
 .last_request_id(),.last_action(),.trajectory_done(done),.tracking_settled(settled),.measured_velocity_cps(),
 .reference_velocity_cps(),.tracking_error(),.settle_ticks(),.planner_phase(),.velocity_q(v),.acceleration_q(a));
 reg [15:0]knots[0:511];reg[31:0]oracle[0:10200];
 integer count,steps,i,k,d,offset_case,n,checks=0,cases=0,csv;
 always @(posedge clk)if(rst_n && dut.draw_player_fault)
  $display("PLAYERFAULT tick=%0d p=%0d vnext=%0d anext=%0d jnext=%0d q=%0d numerator=%0d quotient=%0d state=%0d",i,dut.clocked_draw.rounded_position,dut.clocked_draw.velocity_ext,dut.clocked_draw.acceleration_ext,dut.clocked_draw.jerk_ext,dut.clocked_draw.position_q16,dut.clocked_draw.numerator,dut.clocked_draw.quotient,dut.clocked_draw.state);
 reg[31:0]sum,packed_point;reg signed[63:0]captured,expected;
 reg signed[31:0]oldv,olda,expected_q;
 reg follow=1;wire signed[63:0]wanted=kit ? reference:(reference*27+(reference>=0 ? 25:-25))/26;
 always @(negedge clk)if(rst_n && balance && follow)begin
  if(raw<wanted)raw=raw+1;else if(raw>wanted)raw=raw-1;
 end
 task clocks(input integer number);repeat(number)@(negedge clk);endtask
 task setbuf(input integer id,input reg[31:0]word);begin
  field=id;value=word;write=1;clocks(1);if(!buf_ok || buf_error)$fatal(1,"buffer rejected field%0d value%0h",id,word);
  write=0;clocks(2);
 end endtask
 task step;begin tick=1;clocks(1);tick=0;clocks(127);checks++;
  if(fault)$fatal(1,"player fault tick=%0d state=%0d playerstate=%0d q=%0d v=%0d a=%0d",i,state,dut.clocked_draw.state,dut.clocked_draw.position_q16,v,a);
 end endtask
 task reject_word(input integer id,input reg[31:0] word);begin
  field=id;value=word;write=1;clocks(1);if(buf_ok || !buf_error)$fatal(1,"unsafe upload accepted");write=0;clocks(2);checks++;
 end endtask
 task reset_fixture;begin
  rst_n=0;balance=0;follow=0;raw=0;kit=1;command_valid=0;write=0;cfg_write=0;clocks(6);rst_n=1;clocks(3);
  reanchor=1;clocks(1);reanchor=0;clocks(3);
 end endtask
 task upload_glyph;begin
  setbuf(0,count);sum=32'h6a09e667^count;
  for(n=0;n<count;n++)begin packed_point=(n<<16)|knots[n];setbuf(1,packed_point);sum={sum[30:0],sum[31]}^packed_point;end
  setbuf(2,sum);
 end endtask
 task play_glyph;begin
  balance=1;follow=1;clocks(3);shape=7;kind=2;command_valid=1;clocks(1);if(!accepted)$fatal(1,"play");command_valid=0;clocks(3);i=0;step();
 end endtask
 initial begin
  if(!$value$plusargs("COUNT=%d",count))$fatal(1,"COUNT");$readmemh("knots.hex",knots,0,count-1);
  csv=$fopen("guard_unused.csv","w");reset_fixture();
  reject_word(0,7);setbuf(0,8);reject_word(1,1);
  setbuf(0,8);for(n=0;n<4;n++)setbuf(1,n<<16);reject_word(1,(4<<16)|17);
  setbuf(0,512);sum=32'h6a09e667^512;
  for(n=0;n<512;n++)begin packed_point=n<<16;setbuf(1,packed_point);sum={sum[30:0],sum[31]}^packed_point;end
  reject_word(2,sum^1);if(committed)$fatal(1,"bad checksum playable");
  setbuf(0,512);sum=32'h6a09e667^512;
  for(n=0;n<512;n++)begin packed_point=n<<16;setbuf(1,packed_point);sum={sum[30:0],sum[31]}^packed_point;end
  setbuf(2,sum);if(!committed || length!=512)$fatal(1,"full capacity");checks++;
  balance=1;reject_word(0,8);if(!committed)$fatal(1,"unqualified write destroyed program");balance=0;
  upload_glyph();play_glyph();for(i=0;i<600;i++)step();
  captured=reference;oldv=v;olda=a;kind=3;command_valid=1;clocks(1);
  if(!accepted || state!=3 || reference!=captured || v!=oldv || a!=olda)$fatal(1,"cancel discontinuity");command_valid=0;
  n=0;while(!done && n<3000)begin step();n++;end
  if(!done || fault)$fatal(1,"cancel did not brake");checks++;
  cancel=1;clocks(2);cancel=0;clocks(3);if(state!=0 || dut.clocked_draw.active || !committed)$fatal(1,"STOP handoff/data retention");checks++;
  // A live motion must still trip the per-job observed position envelope.
  reset_fixture();upload_glyph();play_glyph();follow=0;repeat(350)begin raw++;clocks(1);end
  clocks(3);if(!fault)$fatal(1,"observed range guard lost");checks++;
  // Lower user acceleration limits are enforced, not silently bypassed.
  reset_fixture();field=1;value=80;cfg_write=1;clocks(1);if(!cfg_ok)$fatal(1,"limit setup");cfg_write=0;clocks(3);
  upload_glyph();play_glyph();n=0;
  while(!fault && n<5000)begin tick=1;clocks(1);tick=0;clocks(127);n++;end
  if(!fault)$fatal(1,"configured acceleration guard not enforced");checks++;
  $display("PASS C4_GUARDS checks=%0d full512=1 malformedRejected=1 cancelBrake=1 stopPreservesRAM=1 localRange=1 configuredLimits=1",checks);$finish;
 end
 initial begin #1000000000;$fatal(1,"guard timeout");end
endmodule

