`timescale 1ns/1ps
module draw_buffer_tb;
 reg clk=0;always #10 clk=~clk;
 reg rst_n=0,tick=0,permitted=1,abort=0,write=0;
 reg [7:0] id=0;reg [31:0] value=0;reg [8:0] address=0;
 wire ok,error,valid;wire [9:0] length;wire signed [15:0] sample;
 wire [31:0] revision,readvalue;
 forest_draw_buffer dut(.clk(clk),.rst_n(rst_n),.tick_5ms(tick),.upload_permitted(permitted),.abort(abort),
  .cfg_write(write),.cfg_id(id),.cfg_value(value),.cfg_ok(ok),.cfg_error(error),
  .cfg_revision(revision),.cfg_read_value(readvalue),.committed(valid),.length(length),.read_address(address),.sample(sample));
 integer checks=0,j,pos,velocity,direction,total,previous,absolute_step;reg [31:0] checksum,word_value;
 task cycles(input integer n);repeat(n)@(negedge clk);endtask
 task setting(input integer field,input reg [31:0] data,input integer expected);begin
  id=field;value=data;write=1;cycles(1);
  if(ok!==expected[0] || error===expected[0])$fatal(1,"buffer guard field=%0d value=%h",field,data);
  write=0;cycles(2);checks=checks+1;
 end endtask
 function integer point(input integer n);case(n)0,1,8,9:point=0;2,7:point=2;3,6:point=4;default:point=6;endcase endfunction
 task upload;begin
  setting(0,10,1);checksum=32'h6a09e667^32'd10;
  for(j=0;j<10;j=j+1)begin
   word_value=(j<<16)|(point(j)&16'hffff);setting(1,word_value,1);
   checksum={checksum[30:0],checksum[31]}^word_value;
   if(valid)$fatal(1,"partial program became playable");
  end
 end endtask
 initial begin
  cycles(4);rst_n=1;cycles(3);if(valid)$fatal(1,"uninitialized program valid");
  setting(0,2,0);setting(0,513,0);setting(0,10,1);
  setting(1,32'h00010000,0);if(valid || dut.uploading)$fatal(1,"index mismatch retained upload");
  setting(0,10,1);setting(1,32'd1,0);
  setting(0,10,1);setting(1,0,1);setting(1,32'h000100c1,0);
  setting(0,10,1);setting(1,0,1);setting(1,32'h00010007,0);
  setting(0,10,1);setting(1,0,1);setting(1,32'h00010003,0);
  setting(0,10,1);setting(1,0,1);setting(2,0,0);
  upload();setting(2,checksum^1,0);if(valid)$fatal(1,"bad checksum admitted");
  upload();setting(2,checksum,1);if(!valid)$fatal(1,"valid program not committed");
  for(j=0;j<10;j=j+1)begin address=j;cycles(2);if(sample!==point(j))$fatal(1,"RAM read mismatch");checks=checks+1;end
  permitted=0;setting(0,3,0);if(!valid)$fatal(1,"unqualified write changed committed program");permitted=1;
  abort=1;cycles(1);abort=0;cycles(2);if(valid)$fatal(1,"STOP did not invalidate program");checks=checks+1;
  setting(0,10,1);setting(1,0,1);
  repeat(100)begin tick=1;cycles(1);tick=0;cycles(1);end
  if(valid || dut.uploading)$fatal(1,"partial upload inactivity did not abort");checks=checks+1;
  setting(0,10,1);setting(3,0,1);if(valid || dut.uploading)$fatal(1,"explicit upload abort failed");checks=checks+1;
  setting(0,512,1);checksum=32'h6a09e667^32'd512;
  for(j=0;j<512;j=j+1)begin word_value=j<<16;setting(1,word_value,1);checksum={checksum[30:0],checksum[31]}^word_value;end
  setting(2,checksum,1);address=511;cycles(2);
  if(!valid || length!=512 || sample!==0 || dut.count!=512)$fatal(1,"full512 capacity/wrap guard");checks=checks+1;
  setting(0,512,1);setting(1,0,1);pos=0;velocity=0;direction=1;total=0;j=1;
  while(j<512 && total<=1040)begin
   if(pos>=174)direction=-1;else if(pos<=-174)direction=1;
   if(velocity<direction*6)velocity=velocity+2;else if(velocity>direction*6)velocity=velocity-2;
   pos=pos+velocity;absolute_step=velocity<0 ? -velocity:velocity;total=total+absolute_step;
   word_value=(j<<16)|(pos&16'hffff);setting(1,word_value,total<=1040);j=j+1;
  end
  if(total<=1040 || valid || dut.uploading)$fatal(1,"cumulative requested travel guard failed");checks=checks+1;
  $display("PASS draw_buffer checks=%0d uncommittedNeverPlayed=1",checks);$finish;
 end
 initial begin #10000000;$fatal(1,"buffer timeout");end
endmodule
