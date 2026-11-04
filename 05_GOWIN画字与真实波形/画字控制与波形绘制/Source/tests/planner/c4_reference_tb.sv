`timescale 1ns/1ps
module c4_reference_tb;
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
 initial begin
  if(!$value$plusargs("COUNT=%d",count))$fatal(1,"COUNT required");steps=(count-3)*20;
  $readmemh("knots.hex",knots,0,count-1);$readmemh("oracle.hex",oracle,0,steps);
  csv=$fopen("reference.csv","w");$fdisplay(csv,"tick,reference,oracle_q16,velocity_q16,acceleration_q16,state");
  for(k=0;k<2;k++)for(d=0;d<2;d++)for(offset_case=0;offset_case<3;offset_case++)begin
   balance=0;follow=0;rst_n=0;write=0;tick=0;command_valid=0;raw=0;kit=k;clocks(6);rst_n=1;clocks(3);
   reanchor=1;clocks(1);reanchor=0;clocks(3);
   field=6;value=d;cfg_write=1;clocks(1);if(!cfg_ok)$fatal(1,"direction config");cfg_write=0;clocks(2);
   if(offset_case==1)repeat(1300)begin raw++;clocks(1);end
   if(offset_case==2)repeat(1300)begin raw--;clocks(1);end
   capture=1;clocks(1);capture=0;clocks(4);captured=actual;
   setbuf(0,count);sum=32'h6a09e667^count;
   for(n=0;n<count;n++)begin packed_point=(n<<16)|knots[n];setbuf(1,packed_point);sum={sum[30:0],sum[31]}^packed_point;end
   setbuf(2,sum);if(!committed)$fatal(1,"missing commit");balance=1;follow=offset_case!=0;clocks(4);
   shape=7;kind=2;command_valid=1;clocks(1);if(!accepted)$fatal(1,"play rejected");command_valid=0;clocks(3);
   i=0;step();if(state!=6)$fatal(1,"capture start did not enter playback");oldv=0;olda=0;
   for(i=1;i<=steps;i++)begin
    step();expected_q=$signed(oracle[i]);if(!d)expected_q=-expected_q;
    expected=captured+(expected_q>>>16);
    if(reference!==expected)$fatal(1,"reference mismatch kit=%0d dir=%0d offset=%0d tick=%0d actual=%0d expected=%0d q=%0d",k,d,offset_case,i,reference,expected,expected_q);
    if(v-oldv!=a || a>524 || a< -524 || a-olda>13 || olda-a>13)$fatal(1,"v/a/jerk provenance tick%0d",i);
    oldv=v;olda=a;
    if(k==1 && d==1 && offset_case==0)$fdisplay(csv,"%0d,%0d,%0d,%0d,%0d,%0d",i,reference-captured,$signed(oracle[i]),v,a,state);
   end
   if(state!=1 || v!=0 || a!=0 || reference!=captured)$fatal(1,"nonzero handoff to original stopping");
   follow=1;repeat(160)step();if(!done || !settled || state!=0 || reference!=captured)$fatal(1,"original stopping/settling did not finish");cases++;
  end
  $display("PASS C4_REFERENCE cases=%0d checks=%0d exactQ16Oracle=1 kits2Directions2Offsets3=1 stationaryAndIdealEncoder=1 originalStopping=1",cases,checks);$finish;
 end
 initial begin #1000000000;$fatal(1,"test timeout");end
endmodule

