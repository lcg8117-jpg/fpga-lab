`timescale 1ns/1ps
module tb;
reg clk=0;always #10 clk=~clk;
reg rst=0,start=0;reg[31:0] base=0;reg signed[7:0] code=0;
wire valid;wire[31:0] result;integer seen;
forest_d1_adaptive_scale d(clk,rst,start,base,code,valid,result);
task launch;input[31:0] b;input signed[7:0] c;begin
 @(negedge clk);base=b;code=c;start=1;@(negedge clk);start=0;
end endtask
initial begin
 repeat(4)@(negedge clk);rst=1;
 for(integer offset=0;offset<13;offset=offset+1)begin
  launch(32'h43820000,112);repeat(offset)@(negedge clk);
  launch(32'h42440000,16);
  seen=0;
  repeat(18)begin @(negedge clk);if(valid)begin
   seen=seen+1;if(result!==32'h42504000)$fatal(1,"old restart product offset%0d got%h",offset,result);
  end end
  if(seen!=1)$fatal(1,"restart publication count%0d offset%0d",seen,offset);
 end
 launch(32'h43820000,112);repeat(4)@(negedge clk);rst=0;
 repeat(2)@(negedge clk);rst=1;
 repeat(18)begin @(negedge clk);if(valid)$fatal(1,"reset leaked old work");end
 $display("PASS serial restart13phase offsets and mid-job reset");$finish;
end
endmodule
