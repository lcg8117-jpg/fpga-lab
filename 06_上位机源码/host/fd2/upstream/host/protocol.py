"""Forest D1 serial-v2 codec. Importing this module never opens a device."""
import math
import struct

VERSION=2
SCHEMA=0x46443203
FIRMWARE={1:0x46443231,2:0x46443432,3:0x46443233,4:0x46443434,5:0x46443435}
WIRE_VERSIONS={1:2,2:3,3:2,4:4,5:5}
SCHEMAS={1:SCHEMA,2:0x46443402,3:SCHEMA,4:0x46443404,5:0x46443405}
DEFAULT_GAINS=(49.0,0.0,251.0,25.0,0.0,610.0)
STATUS,STOP,ZERO,RUN,CLEAR,ESTOP=0x01,0x10,0x11,0x12,0x13,0x14
SET_GAINS,READ_GAINS,ADAPT_ENABLE,ADAPT_DISABLE=0x20,0x21,0x22,0x23
SELECT_SWING,START_SWING,READ_CONFIG,WRITE_CONFIG=0x30,0x31,0x32,0x33
MOTION_CW,MOTION_CCW,MOTION_LEFT,MOTION_RIGHT,MOTION_STOP=range(0x40,0x45)
HELLO,POSITION,TRAJECTORY,CANCEL_HOLD=0x02,0x45,0x46,0x47
READS={HELLO,STATUS,READ_GAINS,READ_CONFIG}
SAFE={HELLO,STATUS,READ_GAINS,READ_CONFIG,STOP,ESTOP}
STATUS_TEXT={0:'已接受',1:'状态拒绝',2:'格式错误',3:'参数范围错误',4:'未就绪',5:'旧或重复请求',6:'本版本不支持'}
GESTURES={0:'无',1:'右滑',2:'左滑',3:'上滑',4:'下滑',5:'靠近',6:'远离',7:'顺时针',8:'逆时针',9:'挥手STOP'}
FREEZE_TEXT={0:'正常观察',1:'停车禁止输出',2:'故障禁止输出',3:'非零积分：固定基准',4:'基准超出自适应资格',5:'饱和冻结',6:'幅度/位置范围冻结',7:'噪声冻结',8:'观察预热',9:'中心试探中',10:'拒绝后冷却'}
LEARNING_TEXT={0:'尚无学习事件',1:'明确开启',2:'明确关闭',3:'手动接管',4:'运行/故障/资格抑制',5:'采样被守卫拒绝',6:'D中心上探',7:'D中心下探',8:'试探中心已保留',9:'试探拒绝并回滚',10:'冷却完成'}
ADOPTION_TEXT={0:'尚无增益变化',1:'自适应来源采用',2:'固定基准采用',3:'资格/守卫后回基准'}
MOTION_TEXT={0:'保持',1:'单圈轨迹',2:'连续旋转',3:'受控减速',4:'参考完成 / 观察实际收敛'}
MOTION_REASON_TEXT={0:'正常',1:'未进入平衡',2:'忙碌拒绝',3:'同向重复',4:'不支持',5:'范围保护',6:'已取消',7:'保持当前位置',8:'跟踪保护',9:'轨迹结束，等待到位',10:'已收敛',11:'收敛超时，仍保持目标',12:'起点误差过大',13:'反向手势减速',14:'明确减速指令',15:'平衡许可撤销'}

def crc16(data):
    value=0xffff
    for byte in data:
        value^=byte<<8
        for _ in range(8):value=((value<<1)^0x1021)&0xffff if value&0x8000 else (value<<1)&0xffff
    return value

def gains_payload(values):
    if len(values)!=6:raise ValueError('需要6个P/I/D参数')
    numbers=tuple(float(x) for x in values)
    if any(not math.isfinite(x) or x<0 or x>10000 for x in numbers):raise ValueError('PID必须为有限非负数，范围0～10000')
    return struct.pack('<6f',*(0.0 if x==0 else x for x in numbers))

def request(opcode,request_id,group=0,payload=b'',*,version=2,session=0,event=0,route=0):
    if not 0<request_id<=0xffffffff:raise ValueError('请求ID耗尽或非法；禁止回绕重放')
    payload=bytes(payload)
    expected=24 if opcode==SET_GAINS else 1 if opcode in (SELECT_SWING,READ_CONFIG,POSITION,TRAJECTORY) else 5 if opcode==WRITE_CONFIG else 0
    if len(payload)!=expected:raise ValueError('请求长度错误')
    if opcode in (SET_GAINS,READ_GAINS) and group!=255:raise ValueError('PID请求组必须为255')
    if opcode in (READ_CONFIG,WRITE_CONFIG) and group not in (0,1,2,253,254):raise ValueError('配置组错误')
    if opcode not in (SET_GAINS,READ_GAINS,READ_CONFIG,WRITE_CONFIG) and group!=0:raise ValueError('控制请求组必须为0')
    if version not in (2,3,4,5) or route not in (0,1,2):raise ValueError('协议版本或通道错误')
    if opcode in (POSITION,TRAJECTORY) and payload[0] not in range(1,7):raise ValueError('位置/轨迹编号必须为1～6')
    frame=bytearray(48);frame[:4]=b'FD2C';frame[4:8]=bytes((version,opcode,group,len(payload)))
    struct.pack_into('<I',frame,8,request_id);frame[12:12+len(payload)]=payload
    if version!=2:
        struct.pack_into('<II',frame,36,session,event);frame[44]=route
    struct.pack_into('<H',frame,46,crc16(frame[:46]));return bytes(frame)

def valid_frame(frame):
    return len(frame)>=6 and frame[4] in (2,3,4,5) and struct.unpack_from('<H',frame,len(frame)-2)[0]==crc16(frame[:-2])

def decode(frame):
    if not valid_frame(frame):raise ValueError('CRC或协议版本错误')
    if frame[:4]==b'FD2B' and len(frame)==48:
        actual=struct.unpack_from('<q',frame,20)[0];reference,final=struct.unpack_from('<ii',frame,28)
        return dict(kind='compact',wire_version=frame[4],variant=frame[5],flags=frame[6],mode=frame[7],
            sequence=struct.unpack_from('<I',frame,8)[0],board_ms=struct.unpack_from('<I',frame,12)[0],control_epoch=struct.unpack_from('<I',frame,16)[0],
            wide_encoder=actual,wide_target=actual+reference,final_target=actual+final,
            angle_error=struct.unpack_from('<h',frame,36)[0],target_angle_error=struct.unpack_from('<h',frame,38)[0],
            actual_applied_q15=struct.unpack_from('<h',frame,40)[0],measured_velocity=struct.unpack_from('<h',frame,42)[0],
            fault=frame[44],motion_state=frame[45],coherent=bool(frame[6]&1),range_valid=bool(frame[6]&128))
    if frame[:4]==b'FD2A' and len(frame)==80:
        if frame[76:78]!=b'\0\0':raise ValueError('ACK保留字节非零')
        names=('request_id','stored_revision','mutation_highwater','control_epoch')
        result=dict(zip(names,struct.unpack_from('<4I',frame,8)))
        result.update(kind='ack',wire_version=frame[4],opcode=frame[5],status=frame[6],variant=frame[7],stored_gains=struct.unpack_from('<6f',frame,24),
            config_read_value=struct.unpack_from('<I',frame,48)[0],config_revision=struct.unpack_from('<I',frame,52)[0],
            config_group=frame[56],config_id=frame[57],mode=frame[58],fault=frame[59],flags=struct.unpack_from('<I',frame,60)[0],
            active_revision=struct.unpack_from('<I',frame,64)[0],adaptive_adoption_epoch=struct.unpack_from('<I',frame,68)[0],firmware_id=struct.unpack_from('<I',frame,72)[0])
        return result
    if frame[:4]==b'FD2D' and len(frame)==256:
        if frame[6]!=1:raise ValueError('FD2D诊断布局不兼容')
        result={'kind':'diagnostic','wire_version':frame[4],'variant':frame[5],'layout':frame[6],'flags':frame[7],'coherent':False}
        fields=[('sequence',8,'I'),('board_ms',12,'I'),('schema',16,'I'),('firmware_id',20,'I'),
            ('last_completed_epoch',24,'I'),('last_completed_ms',28,'I'),('kit',32,'B'),('zero_valid',33,'B'),
            ('swing_failure_reason',34,'B'),('swing_flags',35,'B'),('handoff_wait_ms',36,'H'),('delta_q4',38,'h'),
            ('physical_encoder_raw',40,'i'),('invalid_transitions',44,'H'),('minimum_edge_clocks',46,'H'),
            ('physical_counts_per_rev',48,'I'),('forest_counts_per_rev',52,'I'),('measured_velocity',56,'i'),
            ('reference_velocity',60,'i'),('tracking_error',64,'q'),('settle_ticks',72,'B'),('stop_reason',73,'B'),
            ('tracking_flags',74,'B'),('source_reason',75,'B'),('gesture_map',76,'B'),('left_positive',77,'B'),
            ('cw_positive',78,'B'),('capture_hold_ms',79,'B'),('adoption_event_seq',80,'I'),('adoption_event_ms',84,'I'),
            ('adoption_control_epoch',88,'I'),('adoption_feature_epoch',92,'I'),('adoption_reason',96,'B'),
            ('last_adaptive_used',97,'B'),('last_freeze',98,'B'),('learning_code',99,'B'),('learning_event_seq',100,'I'),
            ('learning_feature_epoch',104,'I'),('learning_event_ms',108,'I'),('learning_reason',112,'B'),
            ('center_before',113,'b'),('center_after',114,'b'),('learning_phase',115,'B'),('window_samples',116,'B'),
            ('cooldown_windows',117,'B'),('reference_peak',118,'H'),('window_peak',120,'H'),
            ('reference_energy',124,'I'),('window_energy',128,'I'),('candidate_feature_epoch',180,'I'),
            ('candidate_revision',184,'I'),('adopted_feature_epoch',188,'I'),('stored_revision',192,'I'),
            ('active_revision',196,'I'),('adoption_count',200,'I'),('swing_finished_ms',204,'I'),
            ('handoff_ms',208,'I'),('swing_failure_ms',212,'I'),('source_alpha',216,'i'),
            ('source_alpha_rate',220,'i'),('source_arm_rate',224,'i'),('source_capture_flags',228,'B'),
            ('event_samples',229,'B')]
        for name,offset,fmt in fields:result[name]=struct.unpack_from('<'+fmt,frame,offset)[0]
        for name,offset in [('adoption_before_gains',132),('adoption_after_gains',156)]:
            result[name]=struct.unpack_from('<6f',frame,offset)
        if frame[4]!=2:
            for name,offset,fmt in [('origin',230,'q'),('final_target',238,'q'),('motion_request_id',246,'I'),('motion_action',250,'B'),('planner_phase',251,'B'),('gesture_state',252,'B'),('decode_reason',253,'B')]:
                result[name]=struct.unpack_from('<'+fmt,frame,offset)[0]
        result['trajectory_done']=bool(result['tracking_flags']&1)
        result['tracking_settled']=bool(result['tracking_flags']&2)
        if frame[4]!=2:
            action=result['motion_action']
            result['final_target_valid']=not bool(result['flags']&2) or result['trajectory_done'] or action in (0,1,2) or 0x11<=action<=0x16 or 0x21<=action<=0x26
        return result
    if frame[:4]!=b'FD2T' or len(frame)!=256:raise ValueError('帧身份或长度错误：需要匹配FD2，旧FD1W窗口不兼容')
    result={'kind':'telemetry','wire_version':frame[4],'variant':frame[5],'flags':frame[6],'mode':frame[7]}
    for name,offset in [('sequence',8),('board_ms',12),('control_epoch',16),('active_revision',20),('stored_revision',24),('adaptive_adoption_epoch',28),('firmware_id',32),('schema',36),('position_component_epoch',116),('gesture_event_seq',120),('gesture_poll_seq',124),('telemetry_dropped',224),('rx_errors',228),('config_revision',232),('learning_revision',236),('candidate_feature_epoch',240)]:
        result[name]=struct.unpack_from('<I',frame,offset)[0]
    for name,offset in [('fault',40),('swing_profile',41),('swing_stage',42),('motion_state',43),('gesture_code',44),('action_reason',45),('freeze_reason',46),('raw43',246),('raw44',247)]:result[name]=frame[offset]
    for name,offset,fmt in [('direction',47,'b'),('raw_adc',48,'H'),('zero_q4',50,'H'),('quiet_q4',52,'H'),('mean_adc12',54,'H'),('raw_guard_mean',56,'H'),('angle_error',58,'h'),('angle_diff',60,'h'),('actual_applied_q15',62,'h'),('physical_encoder_raw',64,'i'),('local_encoder',68,'h'),('local_target',70,'h'),('wide_encoder',72,'q'),('wide_target',80,'q'),('wide_physical_encoder',88,'q'),('position_error',96,'i'),('requested_moto',100,'i'),('angle_pwm',104,'i'),('position_pwm',108,'i'),('target_angle_adc12',112,'H'),('control_period_us',114,'H'),('gesture_errors',244,'H'),('center_p_offset',248,'h'),('center_d_offset',250,'h'),('fast_p_offset',252,'b'),('fast_d_offset',253,'b')]:
        result[name]=struct.unpack_from('<'+fmt,frame,offset)[0]
    for name,offset in [('manual_gains',128),('active_gains',152),('adaptive_target_gains',176),('components',200)]:result[name]=struct.unpack_from('<6f',frame,offset)
    result['coherent']=bool(result['flags']&1)
    result['adaptive_enabled_at_launch']=bool(result['flags']&32)
    result['adaptive_used_at_launch']=bool(result['flags']&64)
    result['motor_permitted_at_completion']=bool(result['flags']&4)
    result['position_components_held']=result['position_component_epoch']!=result['control_epoch']
    return result

class FrameParser:
    def __init__(self):self.buffer=bytearray();self.crc_errors=0;self.format_errors=0;self.discarded_bytes=0
    def feed(self,data):
        self.buffer.extend(data);result=[]
        while len(self.buffer)>=4:
            positions=[p for m in (b'FD2A',b'FD2T',b'FD2D',b'FD2B') if (p:=self.buffer.find(m))>=0]
            if not positions:
                n=max(0,len(self.buffer)-3);del self.buffer[:n];self.discarded_bytes+=n;break
            p=min(positions)
            if p:self.discarded_bytes+=p;del self.buffer[:p]
            length=80 if self.buffer[:4]==b'FD2A' else 48 if self.buffer[:4]==b'FD2B' else 256
            if len(self.buffer)<length:break
            raw=bytes(self.buffer[:length])
            try:item=decode(raw)
            except ValueError:
                if not valid_frame(raw):self.crc_errors+=1
                else:self.format_errors+=1
                del self.buffer[0];continue
            del self.buffer[:length];result.append((raw,item))
        return result
