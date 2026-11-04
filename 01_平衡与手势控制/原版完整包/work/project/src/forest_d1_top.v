// J280 variant 1; all physical pins retain the frozen board contract.
module forest_d1_top(
 input clk_50m,rst_n,input [3:0] key_sw,input uart_rx,output uart_tx,
 output adc_clk,adc_oe_n,input [9:0] adc_data_in,input adc_otr,enc1_a,enc1_b,
 output AN1,AN2,PWMA,BN1,BN2,PWMB,inout gesture_scl,gesture_sda
);
 // Trial admission only: 15deg, rod200deg/s, arm140deg/s. Balance guards and
 // the ordinary RUN entry window retain their existing independent limits.
 // floor(abs(delta_q4)*6921/16) gives 634Q4 inside15deg; 635Q4 is outside.
 forest_d1_system #(.VARIANT(1),.FIRMWARE_ID(32'h41444231),.SCHEMA(32'h41445001),
  .PROTOCOL_VERSION(3),.PROTOCOL_VARIANT(16),.CAPABILITIES(32'h0000000f),
  .FEATURE_SWING(1),.FEATURE_THREE_PROFILES(0),.FEATURE_GESTURE(1),.FEATURE_MOTION(1),.FEATURE_ADAPTIVE(1),.FEATURE_AUTORETURN(0),
  .SWING_CAPTURE_ALPHA_Q20(32'sd274517),.SWING_CAPTURE_AD_Q20(32'sd3660218),
  .SWING_CAPTURE_THD_Q20(32'sd2562155),.SWING_HANDOFF_ENTER_Q4(16'd634)) system(
  .clk_50m(clk_50m),.rst_n(rst_n),.key_sw(key_sw),.uart_rx(uart_rx),.uart_tx(uart_tx),
  .adc_clk(adc_clk),.adc_oe_n(adc_oe_n),.adc_data_in(adc_data_in),.adc_otr(adc_otr),
  .enc1_a(enc1_a),.enc1_b(enc1_b),.AN1(AN1),.AN2(AN2),.PWMA(PWMA),
  .BN1(BN1),.BN2(BN2),.PWMB(PWMB),.gesture_scl(gesture_scl),.gesture_sda(gesture_sda));
endmodule
