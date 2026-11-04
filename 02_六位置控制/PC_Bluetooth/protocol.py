"""R27 minimal V4 codec; importing never opens a device.

FD2C requests and FD2B/FD2A fields retain the qualified R22 wire4 format.
R27 diagnostics use layout5, FW56345239/schema56345208. Compact frames have
no identity: only the connection owner may bind them to authenticated D/A.
"""
import binascii
import math
import struct

FW=0x56345239
SCHEMA=0x56345208
VERSION=4
VARIANT=4
LAYOUT=5
DEFAULT_GAINS=(53.78,0.0,210.14,17.67,0.0,616.61)
STATUS,STOP,ZERO,RUN,CLEAR,ESTOP=0x01,0x10,0x11,0x12,0x13,0x14
SET_GAINS,READ_GAINS=0x20,0x21
HELLO,POSITION,CANCEL_HOLD=0x02,0x45,0x47
READS={HELLO,STATUS,READ_GAINS}
SAFE=READS|{STOP,ESTOP}
ALLOWED=SAFE|{ZERO,RUN,CLEAR,SET_GAINS,POSITION,CANCEL_HOLD}
STATUS_TEXT={0:'已接受',1:'状态拒绝',2:'格式错误',3:'参数范围错误',4:'未就绪',5:'旧或重复请求',6:'本版本不支持'}

def crc16(data):return binascii.crc_hqx(data,0xffff)

def seal(frame):
    b=bytearray(frame)
    struct.pack_into('<H',b,len(b)-2,crc16(b[:-2]))
    return bytes(b)

def gains_payload(values):
    if len(values)!=6:raise ValueError('需要六个PID参数')
    numbers=tuple(float(x) for x in values)
    if any(not math.isfinite(x) or x<0 or x>10000 for x in numbers):
        raise ValueError('PID范围为有限非负数0～10000')
    if numbers[1]!=0 or numbers[4]!=0:raise ValueError('本版两环积分固定为0')
    return struct.pack('<6f',*(0.0 if x==0 else x for x in numbers))

def request(opcode,request_id,group=0,payload=b'',*,version=4,session=0,event=0,route=1):
    # Same wire4 command packing and CRC as the qualified R22 Host.
    if opcode not in ALLOWED:raise ValueError('本版仅支持平衡和六位置命令')
    if not 0<request_id<=0xffffffff:raise ValueError('请求ID非法或耗尽，禁止回绕重放')
    if version!=4 or route!=1:raise ValueError('本版仅支持wire4蓝牙通道')
    payload=bytes(payload)
    expected=24 if opcode==SET_GAINS else 1 if opcode==POSITION else 0
    if len(payload)!=expected:raise ValueError('请求长度错误')
    if opcode in (SET_GAINS,READ_GAINS):
        if group!=255:raise ValueError('PID请求组必须为255')
    elif group!=0:raise ValueError('控制请求组必须为0')
    if opcode==SET_GAINS:gains_payload(struct.unpack('<6f',payload))
    if opcode==POSITION and payload[0] not in range(1,7):raise ValueError('位置编号必须为1～6')
    frame=bytearray(48);frame[:4]=b'FD2C';frame[4:8]=bytes((version,opcode,group,len(payload)))
    struct.pack_into('<I',frame,8,request_id);frame[12:12+len(payload)]=payload
    struct.pack_into('<II',frame,36,session,event);frame[44]=route
    return seal(frame)

def _get(frame,offset,fmt):return struct.unpack_from('<'+fmt,frame,offset)[0]

def decode(frame):
    if not isinstance(frame,(bytes,bytearray)) or len(frame) not in (48,80,256):
        raise ValueError('帧长度错误')
    if frame[4]!=4 or crc16(frame[:-2])!=_get(frame,len(frame)-2,'H'):
        raise ValueError('CRC或wire版本错误')
    if frame[:4]==b'FD2B' and len(frame)==48:
        if frame[5]!=4:raise ValueError('compact版本错误')
        # Canonical R22 compact field mapping, unchanged.
        actual=_get(frame,20,'q');reference,final=struct.unpack_from('<ii',frame,28)
        return dict(kind='compact',wire_version=frame[4],variant=frame[5],flags=frame[6],mode=frame[7],
            sequence=_get(frame,8,'I'),board_ms=_get(frame,12,'I'),control_epoch=_get(frame,16,'I'),
            wide_encoder=actual,wide_target=actual+reference,final_target=actual+final,
            angle_error=_get(frame,36,'h'),target_angle_error=_get(frame,38,'h'),
            actual_applied_q15=_get(frame,40,'h'),measured_velocity=_get(frame,42,'h'),
            fault=frame[44],motion_state=frame[45],coherent=bool(frame[6]&1),range_valid=bool(frame[6]&128))
    if frame[:4]==b'FD2A' and len(frame)==80:
        if frame[7]!=4 or _get(frame,72,'I')!=FW or frame[76:78]!=b'\0\0':
            raise ValueError('ACK固件身份错误')
        result=dict(zip(('request_id','stored_revision','mutation_highwater','control_epoch'),struct.unpack_from('<4I',frame,8)))
        result.update(kind='ack',wire_version=4,variant=4,opcode=frame[5],status=frame[6],
            stored_gains=struct.unpack_from('<6f',frame,24),mode=frame[58],fault=frame[59],flags=_get(frame,60,'I'),
            active_revision=_get(frame,64,'I'),firmware_id=FW)
        return result
    if frame[:4]==b'FD2D' and len(frame)==256:
        if frame[5]!=4 or frame[6]!=LAYOUT or _get(frame,16,'I')!=SCHEMA or _get(frame,20,'I')!=FW:
            raise ValueError('诊断固件/schema/layout身份错误')
        result=dict(kind='diagnostic',wire_version=4,variant=4,layout=LAYOUT,flags=frame[7],firmware_id=FW,schema=SCHEMA)
        for name,offset,fmt in (
            ('sequence',8,'I'),('board_ms',12,'I'),('last_completed_epoch',24,'I'),('last_completed_ms',28,'I'),
            ('kit',32,'B'),('zero_valid',33,'B'),('physical_encoder_raw',40,'i'),
            ('physical_counts_per_rev',48,'I'),('forest_counts_per_rev',52,'I'),
            ('measured_velocity',56,'i'),('reference_velocity',60,'i'),
            ('active_revision',64,'I'),('stored_revision',68,'I'),
            ('zero_q4',72,'H'),('mean_adc12',74,'H'),('captured_q4',76,'H'),('raw_adc',78,'H'),
            ('motion_snapshot_epoch',80,'I'),('motion_snapshot_ms',84,'I'),('final_target',88,'q'),
            ('nominal_target',96,'q'),('control_target',104,'q'),('wide_encoder',112,'q'),
            ('control_fraction',120,'H'),('motion_flags',122,'B'),('mode',123,'B'),('requested_moto',124,'i'),
            ('rx_errors',152,'I'),('telemetry_dropped',156,'I'),('reply_dropped',160,'I'),
            ('session',164,'I'),('config_revision',168,'I'),('fault',172,'B'),
            ('control_profile',189,'B'),('angle_p_period_us',190,'H'),('position_p_period_us',192,'H'),
            ('angle_d_period_us',194,'H'),('position_d_period_us',196,'H'),('upload_period_us',198,'H'),
            ('adc_batch_sequence',200,'I'),('angle_d_epoch',204,'I'),('position_d_epoch',208,'I'),
            ('position_p_epoch',212,'I'),('completed_sample_epoch',216,'I'),('d_gain_revision',220,'I')):
            result[name]=_get(frame,offset,fmt)
        if result['motion_snapshot_epoch']!=result['last_completed_epoch'] or (
                result['motion_snapshot_ms']!=result['last_completed_ms']) or (
                result['completed_sample_epoch']!=result['last_completed_epoch']) or (
                result['d_gain_revision']!=result['active_revision']):
            raise ValueError('诊断混入不同控制事务')
        result['active_gains']=struct.unpack_from('<6f',frame,128)
        result['tracking_settled']=bool(result['motion_flags']&2)
        result['coherent']=bool(result['flags']&1)
        return result
    raise ValueError('本版不接受该帧格式')

class FrameParser:
    """Original bounded resynchronizing FD2 framing with strict R27 decoding."""
    def __init__(self):
        self.buffer=bytearray();self.crc_errors=0;self.format_errors=0;self.discarded_bytes=0
    def feed(self,data):
        self.buffer.extend(data);result=[]
        while len(self.buffer)>=4:
            positions=[p for m in (b'FD2A',b'FD2D',b'FD2B') if (p:=self.buffer.find(m))>=0]
            if not positions:
                n=max(0,len(self.buffer)-3);del self.buffer[:n];self.discarded_bytes+=n;break
            p=min(positions)
            if p:self.discarded_bytes+=p;del self.buffer[:p]
            length=80 if self.buffer[:4]==b'FD2A' else 48 if self.buffer[:4]==b'FD2B' else 256
            if len(self.buffer)<length:break
            raw=bytes(self.buffer[:length])
            try:item=decode(raw)
            except ValueError:
                if crc16(raw[:-2])!=_get(raw,len(raw)-2,'H'):self.crc_errors+=1
                else:self.format_errors+=1
                del self.buffer[0];continue
            del self.buffer[:length];result.append((raw,item))
        return result
