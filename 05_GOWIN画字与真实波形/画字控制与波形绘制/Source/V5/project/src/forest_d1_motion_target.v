// Position references only. The preserved parallel balance controller owns PWM.
module forest_d1_motion_target(
 input clk,input rst_n,input tick_5ms,input reanchor,input cancel,
 input capture_hold,input balance_active,input signed [31:0] encoder_raw,
 input command_valid,input [2:0] command,input left_positive,input cw_positive,
 output reg signed [63:0] enc_wide_physical,
 output wire signed [63:0] encoder_wide,
 output reg signed [63:0] target_wide,
 output reg signed [15:0] encoder_local,
 output wire signed [15:0] target_local,
 output wire active,output reg [2:0] state,
 output reg signed [1:0] direction,
 output wire fault,output reg accepted,output reg rejected,
 output reg [7:0] reason,output reg [31:0] event_count,
 input kit_ip570,
 output reg trajectory_done,output reg tracking_settled,
 output reg signed [31:0] measured_velocity_cps,
 output wire signed [63:0] tracking_error,
 output reg signed [31:0] reference_velocity_cps,
 output reg [7:0] settle_ticks,output reg [7:0] stop_reason
);
 localparam [2:0] HOLD=0,SINGLE=1,CONTINUOUS=2,DECELERATE=3,SETTLING=4;
 localparam [7:0] OK=0,NOT_BALANCE=1,BUSY=2,DUPLICATE=3,
  UNSUPPORTED=4,RANGE=5,CANCELLED=6,CAPTURED=7,TRACKING_FAULT=8,
  REFERENCE_COMPLETE=9,SETTLED=10,SETTLE_TIMEOUT=11,START_ERROR=12,
  OPPOSITE_BRAKE=13,COMMAND_BRAKE=14,PERMIT_LOST=15;
 reg signed [31:0] previous_raw;
 wire signed [31:0] raw_delta=encoder_raw-previous_raw;
 reg anchored,tracker_fault,local_fault,motion_fault;
 reg signed [63:0] quotient_floor;
 reg [4:0] remainder;
 // Both kits retain raw physical edges. Only the original 1080-edge kit
 // needs the inherited Forest 26/27 map; IP570 has 1040 edges per turn.
 assign encoder_wide=kit_ip570 ? enc_wide_physical : quotient_floor+
   ((enc_wide_physical[63] && remainder!=0) ? 64'sd1 : 64'sd0);
 assign target_local=16'sd10000;
 assign active=state!=HOLD;
 assign fault=tracker_fault || local_fault || motion_fault;
 // Euclidean quotient/remainder update has no division or large multiplier.
 // The physical decoder changes by at most one edge each FPGA clock.
 // Any larger jump is a real fault; it is never silently aliased or clipped.
 always @(posedge clk or negedge rst_n) begin
  if(!rst_n)begin
   previous_raw<=0;anchored<=0;enc_wide_physical<=0;
   quotient_floor<=0;remainder<=0;tracker_fault<=0;
  end else if(reanchor)begin
   previous_raw<=encoder_raw;anchored<=1;enc_wide_physical<=0;
   quotient_floor<=0;remainder<=0;tracker_fault<=0;
  end else begin
   previous_raw<=encoder_raw;
   if(anchored && !tracker_fault)begin
    if(raw_delta==32'sd1)begin
     if(enc_wide_physical==64'sh7fffffffffffffff)tracker_fault<=1;
     else begin
      enc_wide_physical<=enc_wide_physical+64'sd1;
      if(remainder==0)remainder<=26;
      else begin remainder<=remainder-1'b1;quotient_floor<=quotient_floor+64'sd1;end
     end
    end else if(raw_delta== -32'sd1)begin
     if(enc_wide_physical==64'sh8000000000000000)tracker_fault<=1;
     else begin
      enc_wide_physical<=enc_wide_physical-64'sd1;
      if(remainder==26)remainder<=0;
      else begin remainder<=remainder+1'b1;quotient_floor<=quotient_floor-64'sd1;end
     end
    end else if(raw_delta!=0)tracker_fault<=1;
   end
  end
 end
 wire signed [64:0] error_extended=
   $signed({encoder_wide[63],encoder_wide})-$signed({target_wide[63],target_wide});
 assign tracking_error=error_extended[63:0];
 wire error_bad=error_extended < -65'sd42768 || error_extended > 65'sd22767;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin encoder_local<=10000;local_fault<=0;end
  else if(reanchor)begin encoder_local<=10000;local_fault<=0;end
  else if(cancel || capture_hold)begin encoder_local<=10000;local_fault<=0;end
  else if(anchored)begin
   if(error_bad)begin local_fault<=1;encoder_local<=encoder_local;end
   else encoder_local<=16'sd10000+$signed(error_extended[15:0]);
  end
 end
 function [17:0] profile_speed;
  input single_profile,slow_deceleration;input [7:0] phase;
  begin
   if(single_profile && slow_deceleration)begin
    case(phase)
     8'd0:profile_speed=18'd0;
     8'd1:profile_speed=18'd5;
     8'd2:profile_speed=18'd22;
     8'd3:profile_speed=18'd48;
     8'd4:profile_speed=18'd86;
     8'd5:profile_speed=18'd134;
     8'd6:profile_speed=18'd192;
     8'd7:profile_speed=18'd260;
     8'd8:profile_speed=18'd339;
     8'd9:profile_speed=18'd428;
     8'd10:profile_speed=18'd527;
     8'd11:profile_speed=18'd635;
     8'd12:profile_speed=18'd754;
     8'd13:profile_speed=18'd882;
     8'd14:profile_speed=18'd1020;
     8'd15:profile_speed=18'd1168;
     8'd16:profile_speed=18'd1325;
     8'd17:profile_speed=18'd1491;
     8'd18:profile_speed=18'd1667;
     8'd19:profile_speed=18'd1852;
     8'd20:profile_speed=18'd2046;
     8'd21:profile_speed=18'd2249;
     8'd22:profile_speed=18'd2461;
     8'd23:profile_speed=18'd2682;
     8'd24:profile_speed=18'd2912;
     8'd25:profile_speed=18'd3150;
     8'd26:profile_speed=18'd3397;
     8'd27:profile_speed=18'd3653;
     8'd28:profile_speed=18'd3916;
     8'd29:profile_speed=18'd4188;
     8'd30:profile_speed=18'd4469;
     8'd31:profile_speed=18'd4757;
     8'd32:profile_speed=18'd5054;
     8'd33:profile_speed=18'd5358;
     8'd34:profile_speed=18'd5670;
     8'd35:profile_speed=18'd5990;
     8'd36:profile_speed=18'd6318;
     8'd37:profile_speed=18'd6653;
     8'd38:profile_speed=18'd6996;
     8'd39:profile_speed=18'd7346;
     8'd40:profile_speed=18'd7704;
     8'd41:profile_speed=18'd8068;
     8'd42:profile_speed=18'd8440;
     8'd43:profile_speed=18'd8819;
     8'd44:profile_speed=18'd9205;
     8'd45:profile_speed=18'd9598;
     8'd46:profile_speed=18'd9997;
     8'd47:profile_speed=18'd10403;
     8'd48:profile_speed=18'd10816;
     8'd49:profile_speed=18'd11235;
     8'd50:profile_speed=18'd11661;
     8'd51:profile_speed=18'd12093;
     8'd52:profile_speed=18'd12531;
     8'd53:profile_speed=18'd12975;
     8'd54:profile_speed=18'd13426;
     8'd55:profile_speed=18'd13882;
     8'd56:profile_speed=18'd14344;
     8'd57:profile_speed=18'd14812;
     8'd58:profile_speed=18'd15286;
     8'd59:profile_speed=18'd15765;
     8'd60:profile_speed=18'd16250;
     8'd61:profile_speed=18'd16740;
     8'd62:profile_speed=18'd17236;
     8'd63:profile_speed=18'd17736;
     8'd64:profile_speed=18'd18242;
     8'd65:profile_speed=18'd18753;
     8'd66:profile_speed=18'd19269;
     8'd67:profile_speed=18'd19790;
     8'd68:profile_speed=18'd20316;
     8'd69:profile_speed=18'd20846;
     8'd70:profile_speed=18'd21381;
     8'd71:profile_speed=18'd21920;
     8'd72:profile_speed=18'd22464;
     8'd73:profile_speed=18'd23012;
     8'd74:profile_speed=18'd23565;
     8'd75:profile_speed=18'd24121;
     8'd76:profile_speed=18'd24682;
     8'd77:profile_speed=18'd25246;
     8'd78:profile_speed=18'd25815;
     8'd79:profile_speed=18'd26387;
     8'd80:profile_speed=18'd26963;
     8'd81:profile_speed=18'd27543;
     8'd82:profile_speed=18'd28126;
     8'd83:profile_speed=18'd28712;
     8'd84:profile_speed=18'd29302;
     8'd85:profile_speed=18'd29895;
     8'd86:profile_speed=18'd30491;
     8'd87:profile_speed=18'd31091;
     8'd88:profile_speed=18'd31693;
     8'd89:profile_speed=18'd32298;
     8'd90:profile_speed=18'd32906;
     8'd91:profile_speed=18'd33517;
     8'd92:profile_speed=18'd34130;
     8'd93:profile_speed=18'd34746;
     8'd94:profile_speed=18'd35364;
     8'd95:profile_speed=18'd35985;
     8'd96:profile_speed=18'd36608;
     8'd97:profile_speed=18'd37233;
     8'd98:profile_speed=18'd37860;
     8'd99:profile_speed=18'd38489;
     8'd100:profile_speed=18'd39120;
     8'd101:profile_speed=18'd39753;
     8'd102:profile_speed=18'd40388;
     8'd103:profile_speed=18'd41024;
     8'd104:profile_speed=18'd41662;
     8'd105:profile_speed=18'd42301;
     8'd106:profile_speed=18'd42941;
     8'd107:profile_speed=18'd43583;
     8'd108:profile_speed=18'd44226;
     8'd109:profile_speed=18'd44870;
     8'd110:profile_speed=18'd45515;
     8'd111:profile_speed=18'd46161;
     8'd112:profile_speed=18'd46808;
     8'd113:profile_speed=18'd47455;
     8'd114:profile_speed=18'd48103;
     8'd115:profile_speed=18'd48752;
     8'd116:profile_speed=18'd49401;
     8'd117:profile_speed=18'd50050;
     8'd118:profile_speed=18'd50700;
     8'd119:profile_speed=18'd51350;
     8'd120:profile_speed=18'd52000;
     8'd121:profile_speed=18'd52650;
     8'd122:profile_speed=18'd53300;
     8'd123:profile_speed=18'd53950;
     8'd124:profile_speed=18'd54599;
     8'd125:profile_speed=18'd55248;
     8'd126:profile_speed=18'd55897;
     8'd127:profile_speed=18'd56545;
     8'd128:profile_speed=18'd57192;
     8'd129:profile_speed=18'd57839;
     8'd130:profile_speed=18'd58485;
     8'd131:profile_speed=18'd59130;
     8'd132:profile_speed=18'd59774;
     8'd133:profile_speed=18'd60417;
     8'd134:profile_speed=18'd61059;
     8'd135:profile_speed=18'd61699;
     8'd136:profile_speed=18'd62338;
     8'd137:profile_speed=18'd62976;
     8'd138:profile_speed=18'd63612;
     8'd139:profile_speed=18'd64247;
     8'd140:profile_speed=18'd64880;
     8'd141:profile_speed=18'd65511;
     8'd142:profile_speed=18'd66140;
     8'd143:profile_speed=18'd66767;
     8'd144:profile_speed=18'd67392;
     8'd145:profile_speed=18'd68015;
     8'd146:profile_speed=18'd68636;
     8'd147:profile_speed=18'd69254;
     8'd148:profile_speed=18'd69870;
     8'd149:profile_speed=18'd70483;
     8'd150:profile_speed=18'd71094;
     8'd151:profile_speed=18'd71702;
     8'd152:profile_speed=18'd72307;
     8'd153:profile_speed=18'd72909;
     8'd154:profile_speed=18'd73509;
     8'd155:profile_speed=18'd74105;
     8'd156:profile_speed=18'd74698;
     8'd157:profile_speed=18'd75288;
     8'd158:profile_speed=18'd75874;
     8'd159:profile_speed=18'd76457;
     8'd160:profile_speed=18'd77037;
     8'd161:profile_speed=18'd77613;
     8'd162:profile_speed=18'd78185;
     8'd163:profile_speed=18'd78754;
     8'd164:profile_speed=18'd79318;
     8'd165:profile_speed=18'd79879;
     8'd166:profile_speed=18'd80435;
     8'd167:profile_speed=18'd80988;
     8'd168:profile_speed=18'd81536;
     8'd169:profile_speed=18'd82080;
     8'd170:profile_speed=18'd82619;
     8'd171:profile_speed=18'd83154;
     8'd172:profile_speed=18'd83684;
     8'd173:profile_speed=18'd84210;
     8'd174:profile_speed=18'd84731;
     8'd175:profile_speed=18'd85247;
     8'd176:profile_speed=18'd85758;
     8'd177:profile_speed=18'd86264;
     8'd178:profile_speed=18'd86764;
     8'd179:profile_speed=18'd87260;
     8'd180:profile_speed=18'd87750;
     8'd181:profile_speed=18'd88235;
     8'd182:profile_speed=18'd88714;
     8'd183:profile_speed=18'd89188;
     8'd184:profile_speed=18'd89656;
     8'd185:profile_speed=18'd90118;
     8'd186:profile_speed=18'd90574;
     8'd187:profile_speed=18'd91025;
     8'd188:profile_speed=18'd91469;
     8'd189:profile_speed=18'd91907;
     8'd190:profile_speed=18'd92339;
     8'd191:profile_speed=18'd92765;
     8'd192:profile_speed=18'd93184;
     8'd193:profile_speed=18'd93597;
     8'd194:profile_speed=18'd94003;
     8'd195:profile_speed=18'd94402;
     8'd196:profile_speed=18'd94795;
     8'd197:profile_speed=18'd95181;
     8'd198:profile_speed=18'd95560;
     8'd199:profile_speed=18'd95932;
     8'd200:profile_speed=18'd96296;
     8'd201:profile_speed=18'd96654;
     8'd202:profile_speed=18'd97004;
     8'd203:profile_speed=18'd97347;
     8'd204:profile_speed=18'd97682;
     8'd205:profile_speed=18'd98010;
     8'd206:profile_speed=18'd98330;
     8'd207:profile_speed=18'd98642;
     8'd208:profile_speed=18'd98946;
     8'd209:profile_speed=18'd99243;
     8'd210:profile_speed=18'd99531;
     8'd211:profile_speed=18'd99812;
     8'd212:profile_speed=18'd100084;
     8'd213:profile_speed=18'd100347;
     8'd214:profile_speed=18'd100603;
     8'd215:profile_speed=18'd100850;
     8'd216:profile_speed=18'd101088;
     8'd217:profile_speed=18'd101318;
     8'd218:profile_speed=18'd101539;
     8'd219:profile_speed=18'd101751;
     8'd220:profile_speed=18'd101954;
     8'd221:profile_speed=18'd102148;
     8'd222:profile_speed=18'd102333;
     8'd223:profile_speed=18'd102509;
     8'd224:profile_speed=18'd102675;
     8'd225:profile_speed=18'd102832;
     8'd226:profile_speed=18'd102980;
     8'd227:profile_speed=18'd103118;
     8'd228:profile_speed=18'd103246;
     8'd229:profile_speed=18'd103365;
     8'd230:profile_speed=18'd103473;
     8'd231:profile_speed=18'd103572;
     8'd232:profile_speed=18'd103661;
     8'd233:profile_speed=18'd103740;
     8'd234:profile_speed=18'd103808;
     8'd235:profile_speed=18'd103866;
     8'd236:profile_speed=18'd103914;
     8'd237:profile_speed=18'd103952;
     8'd238:profile_speed=18'd103978;
     8'd239:profile_speed=18'd103995;
     8'd240:profile_speed=18'd104000;
     default:profile_speed=18'd104000;
    endcase
   end else begin
    case(phase)
     8'd0:profile_speed=single_profile ? 18'd0 : 18'd0;
     8'd1:profile_speed=single_profile ? 18'd48 : 18'd44;
     8'd2:profile_speed=single_profile ? 18'd192 : 18'd173;
     8'd3:profile_speed=single_profile ? 18'd428 : 18'd385;
     8'd4:profile_speed=single_profile ? 18'd754 : 18'd679;
     8'd5:profile_speed=single_profile ? 18'd1168 : 18'd1051;
     8'd6:profile_speed=single_profile ? 18'd1667 : 18'd1501;
     8'd7:profile_speed=single_profile ? 18'd2249 : 18'd2024;
     8'd8:profile_speed=single_profile ? 18'd2912 : 18'd2621;
     8'd9:profile_speed=single_profile ? 18'd3653 : 18'd3287;
     8'd10:profile_speed=single_profile ? 18'd4469 : 18'd4022;
     8'd11:profile_speed=single_profile ? 18'd5358 : 18'd4822;
     8'd12:profile_speed=single_profile ? 18'd6318 : 18'd5686;
     8'd13:profile_speed=single_profile ? 18'd7346 : 18'd6612;
     8'd14:profile_speed=single_profile ? 18'd8440 : 18'd7596;
     8'd15:profile_speed=single_profile ? 18'd9598 : 18'd8638;
     8'd16:profile_speed=single_profile ? 18'd10816 : 18'd9734;
     8'd17:profile_speed=single_profile ? 18'd12093 : 18'd10884;
     8'd18:profile_speed=single_profile ? 18'd13426 : 18'd12083;
     8'd19:profile_speed=single_profile ? 18'd14812 : 18'd13331;
     8'd20:profile_speed=single_profile ? 18'd16250 : 18'd14625;
     8'd21:profile_speed=single_profile ? 18'd17736 : 18'd15963;
     8'd22:profile_speed=single_profile ? 18'd19269 : 18'd17342;
     8'd23:profile_speed=single_profile ? 18'd20846 : 18'd18761;
     8'd24:profile_speed=single_profile ? 18'd22464 : 18'd20218;
     8'd25:profile_speed=single_profile ? 18'd24121 : 18'd21709;
     8'd26:profile_speed=single_profile ? 18'd25815 : 18'd23233;
     8'd27:profile_speed=single_profile ? 18'd27543 : 18'd24788;
     8'd28:profile_speed=single_profile ? 18'd29302 : 18'd26372;
     8'd29:profile_speed=single_profile ? 18'd31091 : 18'd27982;
     8'd30:profile_speed=single_profile ? 18'd32906 : 18'd29616;
     8'd31:profile_speed=single_profile ? 18'd34746 : 18'd31272;
     8'd32:profile_speed=single_profile ? 18'd36608 : 18'd32947;
     8'd33:profile_speed=single_profile ? 18'd38489 : 18'd34640;
     8'd34:profile_speed=single_profile ? 18'd40388 : 18'd36349;
     8'd35:profile_speed=single_profile ? 18'd42301 : 18'd38071;
     8'd36:profile_speed=single_profile ? 18'd44226 : 18'd39803;
     8'd37:profile_speed=single_profile ? 18'd46161 : 18'd41545;
     8'd38:profile_speed=single_profile ? 18'd48103 : 18'd43293;
     8'd39:profile_speed=single_profile ? 18'd50050 : 18'd45045;
     8'd40:profile_speed=single_profile ? 18'd52000 : 18'd46800;
     8'd41:profile_speed=single_profile ? 18'd53950 : 18'd48555;
     8'd42:profile_speed=single_profile ? 18'd55897 : 18'd50307;
     8'd43:profile_speed=single_profile ? 18'd57839 : 18'd52055;
     8'd44:profile_speed=single_profile ? 18'd59774 : 18'd53797;
     8'd45:profile_speed=single_profile ? 18'd61699 : 18'd55529;
     8'd46:profile_speed=single_profile ? 18'd63612 : 18'd57251;
     8'd47:profile_speed=single_profile ? 18'd65511 : 18'd58960;
     8'd48:profile_speed=single_profile ? 18'd67392 : 18'd60653;
     8'd49:profile_speed=single_profile ? 18'd69254 : 18'd62328;
     8'd50:profile_speed=single_profile ? 18'd71094 : 18'd63984;
     8'd51:profile_speed=single_profile ? 18'd72909 : 18'd65618;
     8'd52:profile_speed=single_profile ? 18'd74698 : 18'd67228;
     8'd53:profile_speed=single_profile ? 18'd76457 : 18'd68812;
     8'd54:profile_speed=single_profile ? 18'd78185 : 18'd70367;
     8'd55:profile_speed=single_profile ? 18'd79879 : 18'd71891;
     8'd56:profile_speed=single_profile ? 18'd81536 : 18'd73382;
     8'd57:profile_speed=single_profile ? 18'd83154 : 18'd74839;
     8'd58:profile_speed=single_profile ? 18'd84731 : 18'd76258;
     8'd59:profile_speed=single_profile ? 18'd86264 : 18'd77637;
     8'd60:profile_speed=single_profile ? 18'd87750 : 18'd78975;
     8'd61:profile_speed=single_profile ? 18'd89188 : 18'd80269;
     8'd62:profile_speed=single_profile ? 18'd90574 : 18'd81517;
     8'd63:profile_speed=single_profile ? 18'd91907 : 18'd82716;
     8'd64:profile_speed=single_profile ? 18'd93184 : 18'd83866;
     8'd65:profile_speed=single_profile ? 18'd94402 : 18'd84962;
     8'd66:profile_speed=single_profile ? 18'd95560 : 18'd86004;
     8'd67:profile_speed=single_profile ? 18'd96654 : 18'd86988;
     8'd68:profile_speed=single_profile ? 18'd97682 : 18'd87914;
     8'd69:profile_speed=single_profile ? 18'd98642 : 18'd88778;
     8'd70:profile_speed=single_profile ? 18'd99531 : 18'd89578;
     8'd71:profile_speed=single_profile ? 18'd100347 : 18'd90313;
     8'd72:profile_speed=single_profile ? 18'd101088 : 18'd90979;
     8'd73:profile_speed=single_profile ? 18'd101751 : 18'd91576;
     8'd74:profile_speed=single_profile ? 18'd102333 : 18'd92099;
     8'd75:profile_speed=single_profile ? 18'd102832 : 18'd92549;
     8'd76:profile_speed=single_profile ? 18'd103246 : 18'd92921;
     8'd77:profile_speed=single_profile ? 18'd103572 : 18'd93215;
     8'd78:profile_speed=single_profile ? 18'd103808 : 18'd93427;
     8'd79:profile_speed=single_profile ? 18'd103952 : 18'd93556;
     8'd80:profile_speed=single_profile ? 18'd104000 : 18'd93600;
     default:profile_speed=single_profile ? 18'd104000 : 18'd93600;
    endcase
   end
  end
 endfunction

 // The speed lookup is prepared one tick ahead. Target arithmetic therefore
 // does not contain the ramp ROM in its register-to-register path.
 reg [9:0] single_tick;
 reg [7:0] curve_phase,brake_phase;
 reg curve_deceleration,brake_deceleration;
 reg single_profile;
 reg [17:0] speed,prepared_speed;
 reg [15:0] fraction;
 reg [10:0] single_distance;
 reg [25:0] single_fraction;
 reg [11:0] settling_age;
 reg [4:0] velocity_tick;
 reg signed [63:0] velocity_position;
 reg velocity_valid;
 wire signed [64:0] velocity_difference=
   $signed({encoder_wide[63],encoder_wide})-
   $signed({velocity_position[63],velocity_position});
 wire signed [68:0] velocity_times10=
   ($signed({{4{velocity_difference[64]}},velocity_difference})<<<3)+
   ($signed({{4{velocity_difference[64]}},velocity_difference})<<<1);
 wire velocity_fits32=velocity_times10[68:31]=={38{velocity_times10[31]}};
 wire settled_now=velocity_valid && tracking_error>=-64'sd5 &&
   tracking_error<=64'sd5 && measured_velocity_cps>=-32'sd10 &&
   measured_velocity_cps<=32'sd10;
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   velocity_tick<=0;velocity_position<=0;velocity_valid<=0;measured_velocity_cps<=0;
  end else if(reanchor || cancel || capture_hold || !balance_active)begin
   velocity_tick<=0;velocity_position<=encoder_wide;
   velocity_valid<=0;measured_velocity_cps<=0;
  end else if(tick_5ms)begin
   if(velocity_tick==19)begin
    velocity_tick<=0;velocity_position<=encoder_wide;
    velocity_valid<=velocity_fits32;
    measured_velocity_cps<=velocity_fits32 ? velocity_times10[31:0] :
      (velocity_times10[68] ? 32'sh80000000 : 32'sh7fffffff);
   end else velocity_tick<=velocity_tick+1'b1;
  end
 end
 function [8:0] single_phase;
  input [9:0] n;
  begin
   if(n<80)single_phase={1'b0,n[7:0]};
   else if(n<=360)single_phase=80;
   else if(n<600)single_phase=10'd856-n;
   else single_phase=9'd256;
  end
 endfunction
 wire [8:0] current_single_phase=single_phase(single_tick+10'd1);
 wire [8:0] next_single_phase=single_phase(single_tick+10'd2);
 wire single_cruise_brake=single_profile && !curve_deceleration && curve_phase==80;
 wire [18:0] fraction_sum={3'd0,fraction}+{1'b0,prepared_speed};
 wire [2:0] continuous_step_value=fraction_sum>=19'd144000 ? 3'd4 :
   fraction_sum>=19'd108000 ? 3'd3 : fraction_sum>=19'd72000 ? 3'd2 :
   fraction_sum>=19'd36000 ? 3'd1 : 3'd0;
 wire [15:0] next_fraction_value=fraction_sum>=19'd144000 ? fraction_sum-19'd144000 :
   fraction_sum>=19'd108000 ? fraction_sum-19'd108000 :
   fraction_sum>=19'd72000 ? fraction_sum-19'd72000 :
   fraction_sum>=19'd36000 ? fraction_sum-19'd36000 : fraction_sum[15:0];
 // Integrate one exact held-reference turn with a common remainder.
 // Tracking error never changes the reference endpoint or its initial phase.
 reg [28:0] single_product;
 wire [28:0] single_sum={3'd0,single_fraction}+single_product;
 wire [2:0] single_step_value=single_sum>=29'd183040000 ? 3'd4 :
   single_sum>=29'd137280000 ? 3'd3 : single_sum>=29'd91520000 ? 3'd2 :
   single_sum>=29'd45760000 ? 3'd1 : 3'd0;
 wire [25:0] next_single_fraction_value=single_sum>=29'd183040000 ? single_sum-29'd183040000 :
   single_sum>=29'd137280000 ? single_sum-29'd137280000 :
   single_sum>=29'd91520000 ? single_sum-29'd91520000 :
   single_sum>=29'd45760000 ? single_sum-29'd45760000 : single_sum[25:0];
 reg [2:0] single_step,continuous_step;
 reg [25:0] next_single_fraction;
 reg [15:0] next_fraction;
 reg [17:0] brake_preview_speed;
 reg [28:0] brake_preview_product;
 reg [2:0] brake_single_step,brake_continuous_step;
 reg [25:0] brake_single_fraction;
 reg [15:0] brake_continuous_fraction;
 reg override_valid;reg [1:0] override_age;
 reg [2:0] override_single_step,override_continuous_step;
 reg [25:0] override_single_fraction;
 reg [15:0] override_continuous_fraction;
 wire use_override=override_valid && override_age<2;
 wire step_negative=direction[1];
 wire [2:0] committed_single_step=use_override ? override_single_step:single_step;
 wire [2:0] committed_continuous_step=use_override ? override_continuous_step:continuous_step;
 wire [25:0] committed_single_fraction=use_override ? override_single_fraction:next_single_fraction;
 wire [15:0] committed_continuous_fraction=use_override ? override_continuous_fraction:next_fraction;
 wire [2:0] step_magnitude=single_profile ? committed_single_step : committed_continuous_step;
 // The signed increment is bounded to -4..4. High-limb neighbours are
 // prepared in parallel, preserving the previous short carry path.
 wire [3:0] target_low_sum={1'b0,target_wide[2:0]}+{1'b0,step_magnitude};
 wire [3:0] target_low_difference={1'b0,target_wide[2:0]}-{1'b0,step_magnitude};
 wire [60:0] target_high_up=target_wide[63:3]+61'd1;
 wire [60:0] target_high_down=target_wide[63:3]-61'd1;
 wire [63:0] target_plus={target_low_sum[3] ? target_high_up : target_wide[63:3],target_low_sum[2:0]};
 wire [63:0] target_minus={target_low_difference[3] ? target_high_down : target_wide[63:3],target_low_difference[2:0]};
 wire [63:0] next_target=step_negative ? target_minus : target_plus;
 wire target_overflow=step_negative ?
   (target_wide[63:3]=={1'b1,60'd0} && target_low_difference[3]) :
   (target_wide[63:3]=={1'b0,{60{1'b1}}} && target_low_sum[3]);
 wire start_error_bad=error_extended < -65'sd200 || error_extended > 65'sd200;
 reg signed [1:0] requested_direction;
 wire [10:0] proposed_single_distance=11'd1040;
 wire signed [64:0] proposed_single_goal=$signed({target_wide[63],target_wide})+
   (requested_direction[1] ? -65'sd1040 : 65'sd1040);
 wire single_goal_overflow=proposed_single_goal[64]!=proposed_single_goal[63];
 // Readiness for another reference is separate from measured settling.
 wire ready_for_motion=state==HOLD || (state==SETTLING && trajectory_done && reference_velocity_cps==0);
 wire accepted_start=command_valid && ready_for_motion && command>=1 && command<=4 &&
   !start_error_bad && !(command<=2 && single_goal_overflow);
 wire accepted_brake=command_valid && (state==SINGLE || state==CONTINUOUS) &&
   (command==5 || ((command==3 || command==4) && requested_direction!=direction));
 wire [17:0] brake_speed_now=single_cruise_brake ? profile_speed(1'b1,1'b1,8'd239) :
   curve_phase!=0 ? profile_speed(single_profile,curve_deceleration,curve_phase-8'd1) : 18'd0;
 function [28:0] normalize_single;
  input [28:0] value;reg [2:0] step;reg [25:0] tail;
  begin
   step=value>=29'd183040000 ? 3'd4 : value>=29'd137280000 ? 3'd3 :
     value>=29'd91520000 ? 3'd2 : value>=29'd45760000 ? 3'd1 : 3'd0;
   tail=value>=29'd183040000 ? value-29'd183040000 :
     value>=29'd137280000 ? value-29'd137280000 :
     value>=29'd91520000 ? value-29'd91520000 :
     value>=29'd45760000 ? value-29'd45760000 : value[25:0];
   normalize_single={step,tail};
  end
 endfunction
 function [18:0] normalize_continuous;
  input [18:0] value;reg [2:0] step;reg [15:0] tail;
  begin
   step=value>=19'd144000 ? 3'd4 : value>=19'd108000 ? 3'd3 :
     value>=19'd72000 ? 3'd2 : value>=19'd36000 ? 3'd1 : 3'd0;
   tail=value>=19'd144000 ? value-19'd144000 : value>=19'd108000 ? value-19'd108000 :
     value>=19'd72000 ? value-19'd72000 : value>=19'd36000 ? value-19'd36000 : value[15:0];
   normalize_continuous={step,tail};
  end
 endfunction
 // Real frame_start ticks are 250000 clocks apart. The portable contract
 // requires at least eight clocks between ticks, with commands on ANY clock.
 // Product and normalization are prepared between commits; a command in the
 // last two clocks uses an already prepared alternative, never a late result.
 // STOP/reanchor/permit gates remain in the original immediate state machine.
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   single_product<=0;single_step<=0;continuous_step<=0;next_single_fraction<=0;next_fraction<=0;
   brake_preview_speed<=0;brake_preview_product<=0;brake_single_step<=0;brake_continuous_step<=0;
   brake_single_fraction<=0;brake_continuous_fraction<=0;
   override_valid<=0;override_age<=3;override_single_step<=0;override_continuous_step<=0;
   override_single_fraction<=0;override_continuous_fraction<=0;
  end else begin
   single_product<=single_distance*prepared_speed;
   single_step<=single_step_value;next_single_fraction<=next_single_fraction_value;
   continuous_step<=continuous_step_value;next_fraction<=next_fraction_value;
   brake_preview_speed<=brake_speed_now;
   brake_preview_product<=single_distance*brake_preview_speed;
   {brake_single_step,brake_single_fraction}<=normalize_single({3'd0,single_fraction}+brake_preview_product);
   {brake_continuous_step,brake_continuous_fraction}<=normalize_continuous({3'd0,fraction}+{1'b0,brake_preview_speed});
   if(accepted_start)begin
    override_valid<=1;override_age<=0;override_single_step<=0;override_continuous_step<=0;
    override_single_fraction<=proposed_single_distance*11'd48;
    override_continuous_fraction<=command<=2 ? 16'd48 : 16'd44;
   end else if(accepted_brake)begin
    override_valid<=1;override_age<=0;override_single_step<=brake_single_step;
    override_continuous_step<=brake_continuous_step;
    override_single_fraction<=brake_single_fraction;override_continuous_fraction<=brake_continuous_fraction;
   end else begin
    if(override_age<3)override_age<=override_age+1'b1;
    if(tick_5ms || reanchor || cancel || capture_hold || fault || !balance_active)override_valid<=0;
   end
  end
 end
 always @* begin
  requested_direction=2'sd0;
  case(command)
   3'd1:requested_direction=cw_positive ? 2'sd1 : -2'sd1;
   3'd2:requested_direction=cw_positive ? -2'sd1 : 2'sd1;
   3'd3:requested_direction=left_positive ? 2'sd1 : -2'sd1;
   3'd4:requested_direction=left_positive ? -2'sd1 : 2'sd1;
   default:requested_direction=2'sd0;
  endcase
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   target_wide<=0;state<=HOLD;direction<=0;accepted<=0;rejected<=0;
   reason<=OK;event_count<=0;single_tick<=0;speed<=0;fraction<=0;motion_fault<=0;
   prepared_speed<=0;curve_phase<=0;brake_phase<=0;single_profile<=0;
   curve_deceleration<=0;brake_deceleration<=0;
   single_distance<=1040;single_fraction<=0;
   trajectory_done<=0;tracking_settled<=0;settle_ticks<=0;settling_age<=0;
   stop_reason<=OK;reference_velocity_cps<=0;
  end else begin
   accepted<=0;rejected<=0;
   if(reanchor)begin
    target_wide<=0;state<=HOLD;direction<=0;single_tick<=0;speed<=0;fraction<=0;
    reason<=CAPTURED;motion_fault<=0;prepared_speed<=0;curve_phase<=0;brake_phase<=0;
    curve_deceleration<=0;brake_deceleration<=0;
    single_distance<=1040;single_fraction<=0;trajectory_done<=0;
    tracking_settled<=0;settle_ticks<=0;settling_age<=0;stop_reason<=CAPTURED;
    reference_velocity_cps<=0;
   end else if(cancel || capture_hold)begin
    // Safety cancellation is immediate and never leaves queued motion.
    target_wide<=encoder_wide;state<=HOLD;direction<=0;single_tick<=0;speed<=0;fraction<=0;
    reason<=cancel ? CANCELLED : CAPTURED;motion_fault<=0;prepared_speed<=0;
    curve_phase<=0;brake_phase<=0;single_distance<=1040;single_fraction<=0;
    curve_deceleration<=0;brake_deceleration<=0;
    trajectory_done<=0;tracking_settled<=0;settle_ticks<=0;settling_age<=0;
    stop_reason<=cancel ? CANCELLED : CAPTURED;reference_velocity_cps<=0;
   end else if(fault || !balance_active)begin
    state<=HOLD;direction<=0;speed<=0;fraction<=0;prepared_speed<=0;
    single_distance<=1040;single_fraction<=0;
    trajectory_done<=0;tracking_settled<=0;settle_ticks<=0;settling_age<=0;
    reference_velocity_cps<=0;stop_reason<=fault ? TRACKING_FAULT : PERMIT_LOST;
    if(fault)reason<=TRACKING_FAULT;
    if(command_valid)begin rejected<=1;reason<=fault ? TRACKING_FAULT : NOT_BALANCE;end
   end else begin
    // A busy/repeated command cannot suppress a scheduled trajectory tick.
    if(command_valid)begin
     if(command==0 || command>5)begin rejected<=1;reason<=UNSUPPORTED;end
     else if(ready_for_motion)begin
      if(command==5)begin rejected<=1;reason<=DUPLICATE;end
      else if(start_error_bad)begin rejected<=1;reason<=START_ERROR;end
      else if(command<=2 && single_goal_overflow)begin rejected<=1;reason<=RANGE;end
      else begin
       // Keep the held target continuous and advance it by one exact turn.
       // A moving or unsettled encoder cannot change the destination phase.
       direction<=requested_direction;state<=command<=2 ? SINGLE : CONTINUOUS;
       single_profile<=command<=2;single_tick<=0;speed<=0;fraction<=0;
       prepared_speed<=profile_speed(command<=2,1'b0,8'd1);curve_phase<=0;brake_phase<=0;
       curve_deceleration<=0;brake_deceleration<=0;
       single_distance<=11'd1040;
       single_fraction<=0;
       accepted<=1;reason<=OK;event_count<=event_count+1'b1;
       trajectory_done<=0;tracking_settled<=0;settle_ticks<=0;settling_age<=0;
       stop_reason<=OK;reference_velocity_cps<=0;
      end
     end else if(command==5 || ((command==3 || command==4) &&
         requested_direction!=direction))begin
      if(state==DECELERATE || state==SETTLING)begin rejected<=1;reason<=DUPLICATE;end
      else begin
       state<=DECELERATE;brake_phase<=single_cruise_brake ? 8'd240 : curve_phase;
       brake_deceleration<=curve_deceleration || single_cruise_brake;
       prepared_speed<=brake_speed_now;
       accepted<=1;reason<=OK;event_count<=event_count+1'b1;
       stop_reason<=command==5 ? COMMAND_BRAKE : OPPOSITE_BRAKE;
      end
     end else begin rejected<=1;reason<=requested_direction==direction ? DUPLICATE : BUSY;end
    end
    if(tick_5ms && !accepted_start && !accepted_brake)begin
     if(state==SETTLING || state==HOLD)begin
      if(trajectory_done)begin
       if(settled_now)begin
        if(settle_ticks<100)settle_ticks<=settle_ticks+1'b1;
        if(settle_ticks>=99)begin
         state<=HOLD;tracking_settled<=1;stop_reason<=SETTLED;
        end
       end else begin
        settle_ticks<=0;tracking_settled<=0;
        if(state==HOLD)state<=SETTLING;
       end
       if(state==SETTLING && settling_age<3000)settling_age<=settling_age+1'b1;
       if(state==SETTLING && settling_age>=2999 && !settled_now)stop_reason<=SETTLE_TIMEOUT;
      end
     end else begin
      if(target_overflow)begin
       state<=HOLD;direction<=0;speed<=0;prepared_speed<=0;
       reason<=RANGE;stop_reason<=RANGE;motion_fault<=1;
       trajectory_done<=0;tracking_settled<=0;reference_velocity_cps<=0;
      end else begin
       target_wide<=next_target;speed<=prepared_speed;fraction<=committed_continuous_fraction;
       // This is the exact discrete reference difference, not an analytic
       // ramp derivative or a measured motor velocity.
       reference_velocity_cps<=direction[1] ?
         -$signed({1'b0,step_magnitude}*32'd200) : $signed({1'b0,step_magnitude}*32'd200);
       if(single_profile)single_fraction<=committed_single_fraction;
       if(state==SINGLE)begin
        curve_phase<=current_single_phase[7:0];curve_deceleration<=current_single_phase[8];
        prepared_speed<=profile_speed(1'b1,next_single_phase[8],next_single_phase[7:0]);
        if(single_tick==599)begin
         state<=SETTLING;direction<=0;speed<=0;fraction<=0;prepared_speed<=0;
         single_fraction<=0;trajectory_done<=1;
         tracking_settled<=0;settle_ticks<=0;settling_age<=0;
         stop_reason<=REFERENCE_COMPLETE;reference_velocity_cps<=0;
        end else single_tick<=single_tick+1'b1;
       end else if(state==CONTINUOUS)begin
        if(curve_phase<80)curve_phase<=curve_phase+1'b1;
        prepared_speed<=profile_speed(1'b0,1'b0,curve_phase<79 ? curve_phase+8'd2 : 8'd80);
       end else if(state==DECELERATE)begin
        if(brake_phase!=0)brake_phase<=brake_phase-1'b1;
        prepared_speed<=brake_phase>1 ? profile_speed(single_profile,brake_deceleration,brake_phase-8'd2) : 18'd0;
        if(prepared_speed==0)begin
         state<=SETTLING;direction<=0;fraction<=0;prepared_speed<=0;
         trajectory_done<=1;tracking_settled<=0;settle_ticks<=0;settling_age<=0;
         reference_velocity_cps<=0;
        end
       end
      end
     end
    end
   end
  end
 end
endmodule
