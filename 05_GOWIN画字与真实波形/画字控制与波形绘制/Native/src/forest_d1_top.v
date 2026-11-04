// J280 variant 1; all physical pins retain the frozen board contract.
module forest_d1_top(
 input clk_50m,rst_n,input [3:0] key_sw,input uart_rx,output uart_tx,
 input bt_rx,bt_state,output bt_tx,
 output adc_clk,adc_oe_n,input [9:0] adc_data_in,input adc_otr,enc1_a,enc1_b,
 output AN1,AN2,PWMA,BN1,BN2,PWMB,inout gesture_scl,gesture_sda
);
 forest_d1_system #(.VARIANT(5),.FIRMWARE_ID(32'h56354336)) system(
  .bt_rx(bt_rx),.bt_tx(bt_tx),.bt_state(bt_state),.clk_50m(clk_50m),.rst_n(rst_n),.key_sw(key_sw),.uart_rx(uart_rx),.uart_tx(uart_tx),
  .adc_clk(adc_clk),.adc_oe_n(adc_oe_n),.adc_data_in(adc_data_in),.adc_otr(adc_otr),
  .enc1_a(enc1_a),.enc1_b(enc1_b),.AN1(AN1),.AN2(AN2),.PWMA(PWMA),
  .BN1(BN1),.BN2(BN2),.PWMB(PWMB),.gesture_scl(gesture_scl),.gesture_sda(gesture_sda));
endmodule

