// One logical request stream over two physical routes. CRC is checked upstream.
// Cached command/reply bytes use RAM; stale copies never re-execute an action.
module forest_command_router #(
 parameter integer VARIANT=4,WIRE_VERSION=4,PREDECODED_EMERGENCY=0,
 parameter [31:0] FIRMWARE_ID=32'h46443434
)(
 input clk,rst_n,
 input usb_valid,input [383:0] usb_data,input ble_valid,input [383:0] ble_data,
 input [1:0] usb_emergency_decode,ble_emergency_decode,
 input ble_connected,session_expire,
 output reg gateway_valid,output wire [383:0] gateway_data,
 input gateway_reply_valid,input [623:0] gateway_reply,
 output reg usb_reply_valid,ble_reply_valid,output wire [623:0] reply_data,
 input usb_reply_ready,ble_reply_ready,
 output wire urgent_stop,urgent_estop,
 input [2:0] mode,input [7:0] fault,input run_session,motor_permitted,capture_active,
 input [191:0] stored_gains,input [31:0] gain_revision,mutation_highwater,
 input [31:0] control_epoch,active_revision,adaptive_epoch,config_revision,
 output reg [31:0] active_session,last_event,duplicate_count,rejected_count,ingress_overflow,
 output reg [31:0] last_routed_request,output reg [1:0] last_routed_source
);
 localparam [3:0] IDLE=0,LOOKUP=1,FORWARD=2,WAIT_GATEWAY=3,STORE=4,
  CMD_ADDRESS=5,CMD_READ=6,REPLY_ADDRESS=7,REPLY_READ=8,RESPOND=9;
 reg [3:0] phase;
 reg usb_pending,ble_pending,request_ble;
 // Each route admits one pending frame; a consumed owner is separate.
 reg [383:0] usb_pending_frame,ble_pending_frame;
 reg [383:0] request_owned;
 reg usb_layout_pending,ble_layout_pending,layout_ok;
 reg [127:0] usb_key_pending,ble_key_pending,key_owned;
 reg request_held;
 wire [383:0] request=request_held ? request_owned:384'd0;
 reg session_expired;
 reg [31:0] usb_read_highwater,ble_read_highwater;
 reg [7:0] cache_valid;
 reg [31:0] cached_event[0:7];
 reg [2:0] write_slot,read_slot;
 reg [6:0] byte_index;
 reg [623:0] working_reply;
 // These owners remain immutable until the valid/ready transfer finishes.
 // Consumers latch on valid; a second full-frame output copy adds no state.
 assign gateway_data=request;
 assign reply_data=working_reply;
 reg mismatch;
 reg [7:0] command_memory[0:383] /* synthesis syn_ramstyle="block_ram" */;
 reg [7:0] reply_memory[0:639] /* synthesis syn_ramstyle="block_ram" */;
 reg [8:0] command_read_address;
 reg [9:0] reply_read_address;
 reg [7:0] command_read_byte,reply_read_byte;
 wire [8:0] command_write_base=({6'd0,write_slot}<<5)+({6'd0,write_slot}<<4);
 wire [9:0] reply_write_base=({7'd0,write_slot}<<6)+({7'd0,write_slot}<<4);
 wire [8:0] command_read_base=({6'd0,read_slot}<<5)+({6'd0,read_slot}<<4);
 wire [9:0] reply_read_base=({7'd0,read_slot}<<6)+({7'd0,read_slot}<<4);
 // Keep decision metadata in FFs while the complete immutable frame is in
 // RAM. Its capture and consumption edges are identical to the frame's.
 wire [7:0] opcode=key_owned[15:8],group=key_owned[23:16],length=key_owned[31:24];
 wire [31:0] session=key_owned[95:64],event_id=key_owned[127:96],request_id=key_owned[63:32];
 // Static format checks belong to the pending frame. Computing them before
 // consumption keeps the RAM output off the long LOOKUP decision path.
 function frame_layout_ok;
  input [383:0] frame;reg padding_ok;integer n;
  begin
   padding_ok=frame[63:56]<=24;
   for(n=0;n<24;n=n+1)if(n>=frame[63:56] && frame[(12+n)*8+:8]!=0)padding_ok=0;
   frame_layout_ok=frame[31:0]==32'h43324446 && frame[39:32]==WIRE_VERSION &&
    frame[95:64]!=0 && frame[367:360]==0 && frame[359:352]<=2 && padding_ok;
  end
 endfunction
 wire stop_shape=group==0 && length==0 && (opcode==8'h10 || opcode==8'h14);
 wire read_only=opcode==8'h01 || opcode==8'h21 || opcode==8'h32;
 wire separate_reads=VARIANT>=4;
 // Select the comparison result instead of selecting a wide counter first.
 (* syn_keep = 1 *) wire [1:0] read_newer_by_route;
 assign read_newer_by_route={event_id>ble_read_highwater,event_id>usb_read_highwater};
 wire read_event_newer=request_ble ? read_newer_by_route[1]:read_newer_by_route[0];
 function emergency_shape;
  input [383:0] frame;
  begin emergency_shape=frame[31:0]==32'h43324446 && frame[39:32]==WIRE_VERSION &&
   frame[55:48]==0 && frame[63:56]==0 && frame[287:96]==0 && frame[367:360]==0 &&
   (frame[47:40]==8'h10 || frame[47:40]==8'h14);end
 endfunction
 wire usb_emergency_shape=PREDECODED_EMERGENCY ? usb_emergency_decode[0]:emergency_shape(usb_data);
 wire ble_emergency_shape=PREDECODED_EMERGENCY ? ble_emergency_decode[0]:emergency_shape(ble_data);
 assign urgent_stop=(usb_valid && usb_emergency_shape) || (ble_valid && ble_emergency_shape);
 assign urgent_estop=(usb_valid && usb_emergency_shape && (PREDECODED_EMERGENCY ? usb_emergency_decode[1]:usb_data[47:40]==8'h14)) ||
  (ble_valid && ble_emergency_shape && (PREDECODED_EMERGENCY ? ble_emergency_decode[1]:ble_data[47:40]==8'h14));
 // synthesis translate_off
 always @(posedge clk)if(rst_n && PREDECODED_EMERGENCY)begin
  if(usb_valid && usb_emergency_decode!=={emergency_shape(usb_data) && usb_data[47:40]==8'h14,emergency_shape(usb_data)})
   $fatal(1,"USB emergency metadata/version mismatch");
  if(ble_valid && ble_emergency_decode!=={emergency_shape(ble_data) && ble_data[47:40]==8'h14,emergency_shape(ble_data)})
   $fatal(1,"BLE emergency metadata/version mismatch");
 end
 // synthesis translate_on
 wire consume_usb=phase==IDLE && usb_pending;
 wire consume_ble=phase==IDLE && !usb_pending && ble_pending;
 wire capture_usb=usb_valid && (!usb_pending || consume_usb);
 wire capture_ble=ble_valid && (!ble_pending || consume_ble);
 // Nonblocking capture/consume preserves the old pending owner on overlap.
 // Unpublished contents remain masked by request_held after reset.
 always @(posedge clk)begin
  if(rst_n && capture_usb)usb_pending_frame<=usb_data;
  if(rst_n && capture_ble)ble_pending_frame<=ble_data;
  if(consume_usb)request_owned<=usb_pending_frame;
  else if(consume_ble)request_owned<=ble_pending_frame;
 end
 reg hit;reg [2:0] hit_slot;integer k,reset_index;
 always @*begin
  hit=0;hit_slot=0;
  for(k=0;k<8;k=k+1)if(cache_valid[k] && cached_event[k]==event_id)begin hit=1;hit_slot=k;end
 end
 function [623:0] local_reply;
  input [7:0] status;
  reg [623:0] value;
  begin
   value=0;value[31:0]=32'h41324446;value[39:32]=WIRE_VERSION;
   value[47:40]=opcode;value[55:48]=status;value[63:56]=VARIANT;
   value[95:64]=request_id;value[127:96]=gain_revision;value[159:128]=mutation_highwater;
   value[191:160]=control_epoch;value[383:192]=stored_gains;value[447:416]=config_revision;
   value[455:448]=group;value[463:456]=request[103:96];
   value[471:464]={5'd0,mode};value[479:472]=fault;
   value[511:480]={28'd0,motor_permitted,run_session,capture_active,1'b0};
   value[543:512]=active_revision;value[575:544]=adaptive_epoch;value[607:576]=FIRMWARE_ID;
   local_reply=value;
  end
 endfunction
 always @(posedge clk)begin
  command_read_byte<=command_memory[command_read_address];
  reply_read_byte<=reply_memory[reply_read_address];
  if(phase==STORE)begin
   if(byte_index<46)command_memory[command_write_base+{2'd0,byte_index}]<=request[byte_index*8+:8];
   reply_memory[reply_write_base+{3'd0,byte_index}]<=working_reply[byte_index*8+:8];
  end
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   phase<=IDLE;usb_pending<=0;ble_pending<=0;request_ble<=0;request_held<=0;
   usb_layout_pending<=0;ble_layout_pending<=0;layout_ok<=0;
   usb_key_pending<=0;ble_key_pending<=0;key_owned<=0;
   active_session<=0;session_expired<=0;last_event<=0;cache_valid<=0;write_slot<=0;read_slot<=0;
   usb_read_highwater<=0;ble_read_highwater<=0;
   byte_index<=0;working_reply<=0;mismatch<=0;
   command_read_address<=0;reply_read_address<=0;gateway_valid<=0;
   usb_reply_valid<=0;ble_reply_valid<=0;duplicate_count<=0;rejected_count<=0;ingress_overflow<=0;
   last_routed_request<=0;last_routed_source<=0;
   for(reset_index=0;reset_index<8;reset_index=reset_index+1)cached_event[reset_index]<=0;
  end else begin
   gateway_valid<=0;usb_reply_valid<=0;ble_reply_valid<=0;
   if(session_expire)session_expired<=1;
   case(phase)
    IDLE:begin
     if(consume_usb)begin request_held<=1;key_owned<=usb_key_pending;layout_ok<=usb_layout_pending;request_ble<=0;usb_pending<=0;phase<=LOOKUP;end
     else if(consume_ble)begin request_held<=1;key_owned<=ble_key_pending;layout_ok<=ble_layout_pending;request_ble<=1;ble_pending<=0;phase<=LOOKUP;end
    end
    LOOKUP:begin
     if(!layout_ok)begin working_reply<=local_reply(2);rejected_count<=rejected_count+1'b1;phase<=RESPOND;end
     else if(stop_shape)begin
      // Even an expired/stale session may stop. The direct gate above has
      // already inhibited outputs; the gateway supplies the normal ACK.
      phase<=FORWARD;
      if(session==active_session && event_id>last_event && !session_expired)last_event<=event_id;
     end else if(opcode==8'h02 && group==0 && length==0)begin
      if(session==0 || event_id==0 || (session==active_session && session_expired))begin
       working_reply<=local_reply(4);rejected_count<=rejected_count+1'b1;
      end else if(session==active_session && !session_expired)working_reply<=local_reply(0);
      else if(active_session==0 || (!run_session && !motor_permitted && !capture_active))begin
       active_session<=session;session_expired<=0;last_event<=event_id;cache_valid<=0;write_slot<=0;
       usb_read_highwater<=0;ble_read_highwater<=0;
       working_reply<=local_reply(0);
      end else begin working_reply<=local_reply(1);rejected_count<=rejected_count+1'b1;end
      phase<=RESPOND;
     end else if(session==0 || event_id==0 || session!=active_session || session_expired)begin
      working_reply<=local_reply(4);rejected_count<=rejected_count+1'b1;phase<=RESPOND;
     end else if(hit)begin
      if(read_only && separate_reads && read_event_newer)begin
       if(request_ble)ble_read_highwater<=event_id;else usb_read_highwater<=event_id;
      end
      read_slot<=hit_slot;byte_index<=0;mismatch<=0;phase<=CMD_ADDRESS;
     end else if((!read_only || !separate_reads) && event_id<=last_event || (read_only && separate_reads && !read_event_newer))begin
      working_reply<=local_reply(5);rejected_count<=rejected_count+1'b1;phase<=RESPOND;
     end else if(request_ble && !ble_connected && opcode!=8'h01 && opcode!=8'h21 && opcode!=8'h32)begin
      // A rejected action is an outcome too; reconnecting STATE cannot make
      // a delayed copy of that logical event start a new motor action.
      working_reply<=local_reply(4);byte_index<=0;
      last_event<=event_id;rejected_count<=rejected_count+1'b1;phase<=STORE;
     end else begin
      // Fast USB heartbeat reads cannot retire an in-flight BLE key action.
      // Mutation order remains shared, so a newer STOP/goal retires old goals.
      if(read_only && separate_reads)begin
       if(request_ble)ble_read_highwater<=event_id;else usb_read_highwater<=event_id;
      end else last_event<=event_id;
      phase<=FORWARD;
     end
    end
    FORWARD:begin
     gateway_valid<=1;last_routed_request<=request_id;last_routed_source<=request_ble ? 2:1;phase<=WAIT_GATEWAY;
    end
    WAIT_GATEWAY:if(gateway_reply_valid)begin
     working_reply<=gateway_reply;byte_index<=0;
     // STOP from another/stale session must not replace this session's cache.
     if(session==active_session && event_id!=0 && !session_expired)phase<=STORE;
     else phase<=RESPOND;
    end
    STORE:begin
     if(byte_index==77)begin
      cache_valid[write_slot]<=1;cached_event[write_slot]<=event_id;
      write_slot<=write_slot+1'b1;phase<=RESPOND;
     end else byte_index<=byte_index+1'b1;
    end
    CMD_ADDRESS:begin command_read_address<=command_read_base+{2'd0,byte_index};phase<=CMD_READ;end
    CMD_READ:phase<=10;
    10:begin
     if(byte_index!=44 && command_read_byte!=request[byte_index*8+:8])mismatch<=1;
     if(byte_index==45)begin
      byte_index<=0;
      if(mismatch || command_read_byte!=request[byte_index*8+:8])begin
       working_reply<=local_reply(5);rejected_count<=rejected_count+1'b1;phase<=RESPOND;
      end else begin working_reply<=0;duplicate_count<=duplicate_count+1'b1;phase<=REPLY_ADDRESS;end
     end else begin byte_index<=byte_index+1'b1;phase<=CMD_ADDRESS;end
    end
    REPLY_ADDRESS:begin reply_read_address<=reply_read_base+{3'd0,byte_index};phase<=REPLY_READ;end
    REPLY_READ:phase<=11;
    11:begin
     working_reply<={reply_read_byte,working_reply[623:8]};
     if(byte_index==77)phase<=RESPOND;
     else begin byte_index<=byte_index+1'b1;phase<=REPLY_ADDRESS;end
    end
    RESPOND:if((request_ble && ble_reply_ready) || (!request_ble && usb_reply_ready))begin
     if(request_ble)ble_reply_valid<=1;else usb_reply_valid<=1;
     phase<=IDLE;
    end
    default:phase<=IDLE;
   endcase
   // Only immutable complete frames enter these two bounded ingress slots.
   if(usb_valid)begin
    if(usb_pending && !consume_usb)ingress_overflow<=ingress_overflow+1'b1;
    else begin usb_key_pending<={usb_data[351:288],usb_data[95:32]};usb_layout_pending<=frame_layout_ok(usb_data);usb_pending<=1;end
   end
   if(ble_valid)begin
    if(ble_pending && !consume_ble)ingress_overflow<=ingress_overflow+1'b1;
    else begin ble_key_pending<={ble_data[351:288],ble_data[95:32]};ble_layout_pending<=frame_layout_ok(ble_data);ble_pending<=1;end
   end
  end
 end
endmodule
