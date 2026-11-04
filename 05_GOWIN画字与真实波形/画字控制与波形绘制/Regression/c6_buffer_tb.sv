`timescale 1ns/1ps
module c6_buffer_tb;
 reg clk=0;always #10 clk=~clk;
 reg rst_n=0,tick=0,permitted=1,abort=0,write=0;
 reg[7:0]id=0;reg[31:0]value=0;wire ok,error,committed,tracking;wire[9:0]length;
 forest_draw_buffer dut(.clk(clk),.rst_n(rst_n),.tick_5ms(tick),.upload_permitted(permitted),.abort(abort),
  .cfg_write(write),.cfg_id(id),.cfg_value(value),.cfg_ok(ok),.cfg_error(error),.cfg_revision(),.cfg_read_value(),
  .committed(committed),.tracking_mode(tracking),.length(length),.read_address(9'd0),.sample());
 integer n,checks=0;reg[31:0]sum,normal;
 task store(input[7:0]field,input[31:0]word,input expected);begin
  @(negedge clk);id=field;value=word;write=1;@(negedge clk);
  if(ok!==expected || error!==!expected)$fatal(1,"write ACK field%0d expected%0d",field,expected);
  write=0;@(negedge clk);checks++;
 end endtask
 task points;begin
  for(n=0;n<8;n++)begin store(1,n<<16,1);sum={sum[30:0],sum[31]}^(n<<16);end
 end endtask
 initial begin
  repeat(4)@(negedge clk);rst_n=1;repeat(3)@(negedge clk);
  normal=32'h6a09e667^8;for(n=0;n<8;n++)normal={normal[30:0],normal[31]}^(n<<16);
  store(7,1,1);store(0,8,1);sum=32'h6a09e667^8^1;points();store(2,normal,0);
  if(committed)$fatal(1,"mode0 checksum committed mode1");checks++;
  store(7,1,1);store(0,8,1);sum=32'h6a09e667^8^1;points();store(2,sum,1);
  if(!committed || !tracking)$fatal(1,"tracking commit missing");checks++;
  permitted=0;store(7,0,0);if(!committed || !tracking)$fatal(1,"busy mode write mutated active data");checks++;
  permitted=1;store(0,8,1);sum=32'h6a09e667^8;points();store(2,sum,1);
  if(!committed || tracking)$fatal(1,"one-shot mode was not consumed");checks++;
  store(7,2,0);if(committed)$fatal(1,"invalid mode retained a commit");checks++;
  $display("PASS C6_BUFFER checks=%0d modeBoundCRC=1 oneShot=1 busyNoMutation=1 invalidNoCommit=1",checks);$finish;
 end
 initial begin #1000000;$fatal(1,"timeout");end
endmodule
