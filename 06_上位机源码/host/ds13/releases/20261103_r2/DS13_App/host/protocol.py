"""DS13 balance + custom draw only. No I/O at import."""
import math
import struct
from binascii import crc_hqx

VERSION=5
FIRMWARE_ID=0x44533133
SCHEMA=0x44535002
DEFAULT_GAINS=(53.78,0.0,210.14,17.67,0.0,616.61)
S1_BITS=(0x42571eb8,0,0x435223d7,0x418d5c29,0,0x441a270a)
STATUS,HELLO,STOP,ZERO,RUN,CLEAR,ESTOP=1,2,0x10,0x11,0x12,0x13,0x14
SET_GAINS,READ_GAINS,READ_CONFIG,WRITE_CONFIG,CANCEL_HOLD=0x20,0x21,0x32,0x33,0x47
LIVE_BEGIN,LIVE_POINT,LIVE_END=0x48,0x49,0x4a
LIVE_COMMANDS={LIVE_BEGIN,LIVE_POINT,LIVE_END,CANCEL_HOLD}
READS={STATUS,HELLO,READ_GAINS,READ_CONFIG}
SAFE=READS|{STOP,ESTOP}
ALLOWED=SAFE|{ZERO,RUN,CLEAR,SET_GAINS,WRITE_CONFIG,LIVE_BEGIN,LIVE_POINT,LIVE_END,CANCEL_HOLD}
STATUS_TEXT={0:'已接受',1:'状态不允许',2:'格式错误',3:'参数超界',4:'未就绪',5:'旧或重复请求',6:'本版本不支持'}

def crc16(data):
    return crc_hqx(data,0xffff)

def gains_payload(values):
    values=tuple(float(v) for v in values)
    if len(values)!=6 or any(not math.isfinite(v) or not 0<=v<=10000 for v in values):raise ValueError('PID参数不合法')
    if values[1]!=0 or values[4]!=0:raise ValueError('本版本两个积分I固定为0')
    return struct.pack('<6f',*(0.0 if v==0 else v for v in values))

def request(opcode,request_id,group=0,payload=b'',*,session=0,event=0):
    if opcode not in ALLOWED:raise ValueError('仅支持平衡与自定义轨迹指令')
    if not 0<request_id<=0xffffffff or not 0<session<=0xffffffff or not 0<event<=0xffffffff:raise ValueError('会话或请求编号非法')
    payload=bytes(payload)
    expected=24 if opcode==SET_GAINS else 8 if opcode==LIVE_POINT else 4 if opcode==LIVE_END else 1 if opcode==READ_CONFIG else 5 if opcode==WRITE_CONFIG else 0
    if len(payload)!=expected:raise ValueError('请求长度错误')
    if opcode in (SET_GAINS,READ_GAINS):
        if group!=255:raise ValueError('PID请求组错误')
        if opcode==SET_GAINS:gains_payload(struct.unpack('<6f',payload))
    elif opcode in (READ_CONFIG,WRITE_CONFIG):
        if group not in (253,254):raise ValueError('配置组错误')
        field=payload[0]
        if group==253 and field not in ((0,1,2,6) if opcode==WRITE_CONFIG else range(7)):raise ValueError('轨迹设置字段错误')
        if group==254 and field not in ((5,) if opcode==WRITE_CONFIG else (5,6,7,8)):raise ValueError('套件字段错误')
    elif group!=0:raise ValueError('控制请求组错误')
    if opcode in (LIVE_POINT,LIVE_END) and not struct.unpack_from('<I',payload)[0]:raise ValueError('无效笔画编号')
    if opcode==LIVE_POINT and abs(struct.unpack_from('<i',payload,4)[0])>192:raise ValueError('笔尖目标超出范围')
    frame=bytearray(48);frame[:4]=b'FD2C';frame[4:8]=bytes((5,opcode,group,len(payload)))
    struct.pack_into('<I',frame,8,request_id);frame[12:12+len(payload)]=payload
    struct.pack_into('<II',frame,36,session,event)
    struct.pack_into('<H',frame,46,crc16(frame[:46]));return bytes(frame)

def decode(frame):
    if len(frame) not in (80,256) or frame[4]!=5 or struct.unpack_from('<H',frame,len(frame)-2)[0]!=crc16(frame[:-2]):raise ValueError('长度、CRC或版本不匹配')
    def get(o,f='I'):return struct.unpack_from('<'+f,frame,o)[0]
    kind=frame[:4]
    if kind==b'FD2A' and len(frame)==80:
        if frame[76:78]!=bytes(2):raise ValueError('ACK保留字段错误')
        result=dict(kind='ack',variant=frame[7],opcode=frame[5],status=frame[6],request_id=get(8),stored_revision=get(12),
            mutation_highwater=get(16),control_epoch=get(20),stored_gains=struct.unpack_from('<6f',frame,24),stored_bits=struct.unpack_from('<6I',frame,24),
            config_read_value=get(48),config_revision=get(52),config_group=frame[56],config_id=frame[57],mode=frame[58],fault=frame[59],
            flags=get(60),active_revision=get(64),firmware_id=get(72))
    elif kind==b'FD2T' and len(frame)==256:
        result=dict(kind='telemetry',variant=frame[5],flags=frame[6],mode=frame[7],coherent=bool(frame[6]&1),firmware_id=get(32),schema=get(36))
        for name,o,fmt in [('sequence',8,'I'),('board_ms',12,'I'),('control_epoch',16,'I'),('active_revision',20,'I'),('stored_revision',24,'I'),
            ('fault',40,'B'),('raw_adc',48,'H'),('zero_q4',50,'H'),('mean_adc12',54,'H'),('angle_error',58,'h'),('actual_applied_q15',62,'h'),
            ('physical_encoder_raw',64,'i'),('local_encoder',68,'h'),('wide_encoder',72,'q'),('wide_target',80,'q'),('wide_physical_encoder',88,'q'),
            ('control_period_us',114,'H'),('origin',176,'q'),('play_time_ms',184,'I'),('job_id',188,'I'),('live_offset',192,'h'),('point_sequence',28,'I'),('play_state',194,'B'),
            ('reference_velocity',196,'i'),('telemetry_dropped',224,'I'),('rx_errors',228,'I'),('config_revision',232,'I'),
            ('angle_d_epoch',236,'I'),('position_d_epoch',240,'I'),('position_p_epoch',244,'I')]:result[name]=get(o,fmt)
        for name,o,fmt in [('manual_gains',128,'6f'),('active_gains',152,'6f'),('manual_bits',128,'6I'),('active_bits',152,'6I'),('components',200,'6f')]:result[name]=struct.unpack_from('<'+fmt,frame,o)
        result['periods_ms']=tuple(frame[248:252]);result['fast_p']=bool(frame[252]);result['adc_samples']=frame[253]
    elif kind==b'FD2D' and len(frame)==256:
        if frame[6]!=1:raise ValueError('诊断布局错误')
        result=dict(kind='diagnostic',variant=frame[5],firmware_id=get(20),schema=get(16),sequence=get(8),board_ms=get(12),kit=frame[32],
            measured_velocity=get(56,'i'),reference_velocity=get(60,'i'),tracking_error=get(64,'q'),stop_reason=frame[73],
            trajectory_done=bool(frame[74]&1),tracking_settled=bool(frame[74]&2),origin=get(230,'q'),final_target=get(238,'q'),job_id=get(246))
    else:raise ValueError('帧类型不匹配')
    result['identity_ok']=result['variant']==5 and result['firmware_id']==FIRMWARE_ID and result.get('schema',SCHEMA)==SCHEMA
    return result

class FrameParser:
    def __init__(self):self.buffer=bytearray();self.errors=0
    def feed(self,data):
        self.buffer.extend(data);result=[]
        while len(self.buffer)>=4:
            offsets=[p for m in (b'FD2A',b'FD2T',b'FD2D') if (p:=self.buffer.find(m))>=0]
            if not offsets:
                del self.buffer[:max(0,len(self.buffer)-3)];break
            del self.buffer[:min(offsets)]
            length=80 if self.buffer[:4]==b'FD2A' else 256
            if len(self.buffer)<length:break
            raw=bytes(self.buffer[:length])
            try:item=decode(raw)
            except ValueError:self.errors+=1;del self.buffer[0];continue
            del self.buffer[:length];result.append((raw,item))
        return result
