// FD2 command semantics. UART, CRC and frame queuing are separate.
module forest_d1_gateway #(
 parameter integer VARIANT=3,WIRE_VERSION=3,BLE_TELEMETRY=1,WATCHDOG_CYCLES=25000000,
 parameter [31:0] FIRMWARE_ID=32'h46443233
)(
 input clk,rst_n,command_valid,urgent_stop,urgent_estop,input [383:0] command_data,
 output reg reply_valid,output reg [623:0] reply_data,
 input [2:0] mode,input [7:0] fault,
 input zero_valid,capture_active,run_session,motor_permitted,physical_estop_active,source_ready,
 input balance_active,adapt_enabled,adapt_active,physical_start_event,
 input [31:0] active_revision,control_epoch,adaptive_epoch,
 output reg stop_pulse,zero_pulse,run_pulse,clear_pulse,swing_start_pulse,
 output reg serial_estop_latched,output reg watchdog_stop,
 output reg [191:0] gains_stored,output reg [31:0] gain_revision,last_mutation_id,
 output reg manual_write,enable_write,enable_value,
 output reg [1:0] selected_profile,output reg [2:0] gesture_map_flags,
 output reg left_positive,cw_positive,gesture_enable,kit_ip570,kit_changed,
 output reg cfg_write,output wire [7:0] cfg_group,cfg_id,output wire [31:0] cfg_value,
 input [31:0] cfg_read_value,external_cfg_revision,input upload_permitted,
 input cfg_result_valid,cfg_result_ok,
 output reg motion_valid,output reg [2:0] motion_command,
 output reg [1:0] action_kind,output reg [2:0] action_index,output wire [31:0] motion_request_id,
 input motion_result_valid,motion_result_ok,
 output wire [31:0] config_revision,output reg [31:0] gateway_errors
);
 localparam [191:0] DEFAULT_GAINS={32'h44188000,32'd0,32'h41c80000,32'h437b0000,32'd0,32'h42440000};
 localparam SWING_ENABLED=(VARIANT==1 || VARIANT==2 || VARIANT==4);
 reg [383:0] request;reg [2:0] phase;
 reg [7:0] result_status;reg [3:0] result_wait;
 reg waiting_motion;reg [31:0] system_revision;
 wire [7:0] opcode=request[47:40],group=request[55:48],length=request[63:56];
 wire [31:0] request_id=request[95:64];
 assign motion_request_id=request_id;
 assign cfg_group=group;assign cfg_id=request[103:96];assign cfg_value=request[135:104];
 assign config_revision=system_revision+external_cfg_revision;
 wire controls=(opcode==8'h01 || (opcode>=8'h10 && opcode<=8'h14) ||
  opcode==8'h22 || opcode==8'h23 || opcode==8'h31 || (opcode>=8'h40 && opcode<=8'h44));
 wire relative_command=(opcode==8'h45 || opcode==8'h46);
 wire cancel_command=opcode==8'h47;
 wire known=relative_command || cancel_command || controls || opcode==8'h20 || opcode==8'h21 || opcode==8'h30 || opcode==8'h32 || opcode==8'h33;
 wire mutation=known && opcode!=8'h01 && opcode!=8'h21 && opcode!=8'h32;
 reg padding_ok;integer i;
 always @*begin
  padding_ok=length<=24;
  for(i=0;i<24;i=i+1)if(i>=length && request[(12+i)*8+:8]!=0)padding_ok=0;
 end
 wire layout_ok=request[31:0]==32'h43324446 && request[39:32]==WIRE_VERSION && request_id!=0 && request[367:360]==0 && request[359:352]<=2 && padding_ok;
 wire shape_ok=((relative_command && group==0 && length==1) || (cancel_command && group==0 && length==0)) || (controls && group==0 && length==0) ||
  (opcode==8'h20 && group==255 && length==24) || (opcode==8'h21 && group==255 && length==0) ||
  (opcode==8'h30 && group==0 && length==1) ||
  (opcode==8'h32 && length==1) || (opcode==8'h33 && length==5);
 function valid_gain;
  input [31:0] value;
  begin valid_gain=!value[31] && value<=32'h461c4000;end
 endfunction
 wire gains_ok=valid_gain(request[127:96]) && valid_gain(request[159:128]) && valid_gain(request[191:160]) &&
  valid_gain(request[223:192]) && valid_gain(request[255:224]) && valid_gain(request[287:256]);
 wire integral_changed=request[159:128]!=gains_stored[63:32] || request[255:224]!=gains_stored[159:128];
 wire external_group_ok=((VARIANT==1 || VARIANT==4) && group<3) || (VARIANT==2 && group==0) || (VARIANT!=3 && group==253) || (VARIANT==5 && group==252);
 reg [31:0] system_read;reg system_id_ok,system_value_ok;
 always @*begin
  system_read=32'hffffffff;system_id_ok=1;system_value_ok=1;
  case(cfg_id)
   0:begin system_read={30'd0,selected_profile};system_value_ok=cfg_value<((VARIANT==1 || VARIANT>=4) ? 3:1);end
   1:begin system_read={29'd0,gesture_map_flags};system_value_ok=cfg_value<8;end
   2:begin system_read={31'd0,left_positive};system_value_ok=cfg_value<2;end
   3:begin system_read={31'd0,cw_positive};system_value_ok=cfg_value<2;end
   4:begin system_read={31'd0,gesture_enable};system_value_ok=cfg_value<2;end
   5:begin system_read={31'd0,kit_ip570};system_value_ok=cfg_value<2;end
   6:begin system_read=kit_ip570 ? 1040:1080;system_value_ok=0;end
   7:begin system_read=1040;system_value_ok=0;end
   8:begin system_read=VARIANT>=4 ? (BLE_TELEMETRY ? 3:1):0;system_value_ok=0;end
   default:begin system_id_ok=0;system_value_ok=0;end
  endcase
 end
 reg remote_owned;reg [31:0] watchdog_count;
 wire keepalive=phase==1 && layout_ok && shape_ok && (opcode==8'h01 || opcode==8'h21 || opcode==8'h32);
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin remote_owned<=0;watchdog_count<=0;watchdog_stop<=0;end
  else begin
   watchdog_stop<=0;
   if(physical_start_event || stop_pulse || serial_estop_latched)begin remote_owned<=0;watchdog_count<=0;end
   else if(run_pulse || swing_start_pulse)begin remote_owned<=1;watchdog_count<=0;end
   else if(!run_session)begin remote_owned<=0;watchdog_count<=0;end
   else if(keepalive)watchdog_count<=0;
   else if(remote_owned)begin
    if(watchdog_count>=WATCHDOG_CYCLES-1)begin watchdog_stop<=1;remote_owned<=0;watchdog_count<=0;end
    else watchdog_count<=watchdog_count+1'b1;
   end
  end
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   request<=0;phase<=0;result_status<=0;result_wait<=0;waiting_motion<=0;
   reply_valid<=0;reply_data<=0;stop_pulse<=0;zero_pulse<=0;run_pulse<=0;clear_pulse<=0;swing_start_pulse<=0;
   serial_estop_latched<=0;gains_stored<=DEFAULT_GAINS;gain_revision<=0;last_mutation_id<=0;
   manual_write<=0;enable_write<=0;enable_value<=0;selected_profile<=0;gesture_map_flags<=0;
   left_positive<=1;cw_positive<=1;gesture_enable<=VARIANT!=3;kit_ip570<=0;kit_changed<=0;cfg_write<=0;motion_valid<=0;motion_command<=0;action_kind<=0;action_index<=0;
   system_revision<=0;gateway_errors<=0;
  end else begin
   reply_valid<=0;stop_pulse<=0;zero_pulse<=0;run_pulse<=0;clear_pulse<=0;swing_start_pulse<=0;
   manual_write<=0;enable_write<=0;cfg_write<=0;motion_valid<=0;kit_changed<=0;
   if(command_valid && phase!=0 && gateway_errors!=32'hffffffff)gateway_errors<=gateway_errors+1'b1;
   case(phase)
    0:if(command_valid)begin request<=command_data;phase<=1;result_status<=0;result_wait<=0;waiting_motion<=0;end
    1:begin
     phase<=3;
     if(!layout_ok || (known && !shape_ok))result_status<=2;
     else if(!known)result_status<=6;
     // Session/event rejection is performed once by the shared router.
     else begin
      if(mutation && request_id>last_mutation_id)last_mutation_id<=request_id;
      case(opcode)
       8'h01,8'h21:begin end
       8'h10:stop_pulse<=1;
       8'h14:begin serial_estop_latched<=1;stop_pulse<=1;end
       8'h11:if(run_session || capture_active || physical_estop_active)result_status<=1;else zero_pulse<=1;
       8'h12:begin
        if(run_session)begin end
        else if(fault!=0 || !zero_valid || capture_active || physical_estop_active || serial_estop_latched)result_status<=1;
        else if(!source_ready)result_status<=4;
        else run_pulse<=1;
       end
       8'h13:if(run_session || capture_active || physical_estop_active)result_status<=1;
        else begin serial_estop_latched<=0;clear_pulse<=1;end
       8'h20:begin
        if(!gains_ok)result_status<=3;
        else if(run_session && integral_changed)result_status<=1;
        else begin gains_stored<=request[287:96];gain_revision<=gain_revision+1'b1;manual_write<=1;end
       end
       8'h22,8'h23:if(VARIANT!=3)result_status<=6;
        else if(opcode==8'h22 && (!zero_valid || capture_active || fault!=0 || physical_estop_active))result_status<=1;
        else begin enable_write<=1;enable_value<=opcode==8'h22;end
       8'h30:if(!SWING_ENABLED)result_status<=6;
        else if(run_session || capture_active)result_status<=1;
        else if(request[103:96]>=((VARIANT==1 || VARIANT>=4) ? 3:1))result_status<=3;
        else begin selected_profile<=request[97:96];system_revision<=system_revision+1'b1;end
       8'h31:if(!SWING_ENABLED)result_status<=6;
        else if(run_session || capture_active || !zero_valid || fault!=0 || physical_estop_active || serial_estop_latched)result_status<=1;
        else swing_start_pulse<=1;
       8'h32:begin
        if(VARIANT==5 && group==254 && cfg_id==0)result_status<=6;
        else if(group==254)begin if(!system_id_ok)result_status<=3;end
        else if(!external_group_ok)result_status<=6;
        else if(cfg_read_value==32'hffffffff && !(VARIANT==5 && group==252 && cfg_id==2))result_status<=3;
       end
       8'h33:begin
        if(VARIANT==5 && (group<3 || (group==254 && cfg_id==0)))result_status<=6;
        else if(VARIANT==3 && !(group==254 && cfg_id==5))result_status<=6;
        else if((run_session && !(group==252 && upload_permitted)) || capture_active)result_status<=1;
        else if(group==254 && cfg_id==5 && (fault!=0 || physical_estop_active || serial_estop_latched))result_status<=1;
        else if(group==254)begin
         if(!system_id_ok || !system_value_ok)result_status<=3;
         else begin
          case(cfg_id)
           0:selected_profile<=cfg_value[1:0];1:gesture_map_flags<=cfg_value[2:0];
           2:left_positive<=cfg_value[0];3:cw_positive<=cfg_value[0];4:gesture_enable<=cfg_value[0];
           5:begin kit_ip570<=cfg_value[0];kit_changed<=kit_ip570!=cfg_value[0];end
          endcase
          system_revision<=system_revision+1'b1;
         end
        end else if(!external_group_ok)result_status<=6;
        else begin cfg_write<=1;phase<=2;waiting_motion<=0;result_wait<=0;end
       end
       8'h40,8'h41,8'h42,8'h43,8'h44:begin
        if(VARIANT!=2)result_status<=6;
        else if(!balance_active || fault!=0 || physical_estop_active)result_status<=1;
        else begin motion_valid<=1;action_kind<=0;action_index<=0;motion_command<=opcode-8'h3f;phase<=2;waiting_motion<=1;result_wait<=0;end
       end
       8'h45,8'h46,8'h47:begin
        if((opcode==8'h45 && VARIANT==5) || (opcode==8'h46 && VARIANT!=5) || (opcode!=8'h46 && VARIANT<4))result_status<=6;
        else if(opcode==8'h46 && VARIANT==5 && request[103:96]!=3 && request[103:96]!=4 && request[103:96]!=7)result_status<=3;
        else if(opcode!=8'h47 && (request[103:96]<1 || request[103:96]>7))result_status<=3;
        else if(!balance_active || fault!=0 || physical_estop_active)result_status<=1;
        else begin motion_valid<=1;motion_command<=0;action_kind<=opcode==8'h45 ? 1:opcode==8'h46 ? 2:3;
         action_index<=request[98:96];phase<=2;waiting_motion<=1;result_wait<=0;end
       end
       default:result_status<=6;
      endcase
     end
    end
    2:begin
     if((waiting_motion && motion_result_valid) || (!waiting_motion && cfg_result_valid))begin
      result_status<=waiting_motion ? (motion_result_ok ? 0:1):(cfg_result_ok ? 0:3);phase<=4;
     end else if(result_wait==12)begin result_status<=4;phase<=4;end
     else result_wait<=result_wait+1'b1;
    end
    3:phase<=4;
    4:begin
     reply_data<=0;
     reply_data[31:0]<=32'h41324446;reply_data[39:32]<=WIRE_VERSION;reply_data[47:40]<=opcode;
     reply_data[55:48]<=result_status;reply_data[63:56]<=VARIANT;
     reply_data[95:64]<=request_id;reply_data[127:96]<=gain_revision;reply_data[159:128]<=last_mutation_id;
     reply_data[191:160]<=control_epoch;reply_data[383:192]<=gains_stored;
     reply_data[415:384]<=group==254 ? system_read:cfg_read_value;
     reply_data[447:416]<=config_revision;reply_data[455:448]<=group;reply_data[463:456]<=cfg_id;
     reply_data[471:464]<={5'd0,mode};reply_data[479:472]<=fault;
     reply_data[511:480]<={24'd0,(mode==5),adapt_active,adapt_enabled,source_ready,motor_permitted,run_session,capture_active,zero_valid};
     reply_data[543:512]<=active_revision;reply_data[575:544]<=adaptive_epoch;reply_data[607:576]<=FIRMWARE_ID;
     reply_valid<=1;phase<=0;
    end
    default:phase<=0;
   endcase
   if(urgent_stop)stop_pulse<=1;
   if(urgent_estop)begin serial_estop_latched<=1;stop_pulse<=1;end
  end
 end
endmodule
