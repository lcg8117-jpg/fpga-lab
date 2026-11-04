`timescale 1ns/1ps
module tb;
reg clk=0;always #10 clk=~clk;reg rst=0,start=0;reg[31:0] base=0,expected;reg signed[7:0] code=0;
wire valid;wire[31:0] result;integer fd,count=0,c,rc;
forest_d1_adaptive_scale d(clk,rst,start,base,code,valid,result);
initial begin repeat(4)@(negedge clk);rst=1;fd=$fopen("vectors.txt","r");
 while(!$feof(fd))begin
  rc=$fscanf(fd,"%h %d %h\n",base,c,expected);if(rc!=3)$fatal(1,"input vector");code=c;
  @(negedge clk);start=1;@(negedge clk);start=0;wait(valid);#1;
  if(result!==expected)$fatal(1,"scale mismatch base%h code%0d got%h expected%h",base,c,result,expected);
  count=count+1;@(negedge clk);
 end
 $display("PASS widened scaler vectors=%0d",count);$finish;
end
endmodule
