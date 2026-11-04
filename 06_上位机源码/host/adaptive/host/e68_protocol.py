"""E68 only. Pure command/frame codec; no device or GUI imports."""
import struct

MAGIC=b'P2M4'
VERSION=15
WORD_COUNT=32
FRAME_BYTES=140
USER_CODE=0x50324568
STOP_PACKET=bytes((0xa5,0x5a,0x20,0,0,0xdf))
LIVE_OPS=frozenset((0x01,0x41,0x02,0x03,0x0e,0x04,0x24,0x49))
RANGES={0x01:(0,65535),0x41:(0,65535),0x02:(0,65535),
        0x03:(0,65535),0x0e:(0,65535),0x04:(0,65535),
        0x24:(1,4096),0x49:(0,7),0x47:(64,4096),0x3f:(1,8),
        0x30:(0,1),0x55:(0,16383),0x20:(0,0)}

RANGES.update({0x70:(0,2),0x71:(0,1),0x72:(0,0),0x73:(0xe001,0xe002)})
RANGES.update({op:(0,65535) for op in range(0x80,0x94)})

def crc16(data):
    value=0xffff
    for byte in data:
        value^=byte<<8
        for _ in range(8):value=((value<<1)^(0x1021 if value&0x8000 else 0))&0xffff
    return value

def signed(v,bits):
    v&=(1<<bits)-1
    return v-(1<<bits) if v&(1<<(bits-1)) else v

def signed_words(words,first,count):
    return signed(sum(words[first+i]<<(32*i) for i in range(count)),32*count)

def encode_status(words,sequence):
    if type(sequence)is not int or not 0<=sequence<=0xffffffff or len(words)!=32 or any(type(v)is not int or not 0<=v<=0xffffffff for v in words):raise ValueError('32 uint32 words and uint32 sequence required')
    body=MAGIC+bytes((VERSION,WORD_COUNT))+struct.pack('<I32I',sequence,*words)
    return body+struct.pack('<H',crc16(body))

def decode_status(raw):
    if len(raw)!=FRAME_BYTES or raw[:6]!=MAGIC+bytes((VERSION,WORD_COUNT)):raise ValueError('Only E68 P2M4/v15/32word/140byte; no diagnostics')
    if crc16(raw[:-2])!=int.from_bytes(raw[-2:],'little'):raise ValueError('E68 CRC mismatch')
    sequence,*words=struct.unpack('<I32I',raw[6:-2])
    if words[23]!=USER_CODE:raise ValueError('E68 UID word23 mismatch')
    flags=words[1]
    if ((flags>>22)&3)!=1 or not 1<=((flags>>24)&15)<=8 or not flags&(1<<31):raise ValueError('E68 requires fixed bank1/window capability')
    if (words[27]>>29)&1!=1:raise ValueError('E68 configured bank must be1')
    return dict(identity='P2M4',version=VERSION,sequence=sequence,words=words)

def scalar_packet(opcode,value):
    if type(opcode)is not int or type(value)is not int or opcode not in RANGES or not RANGES[opcode][0]<=value<=RANGES[opcode][1]:raise ValueError('E68 documented command/range only; RUN/PT/mode/ARM/REF/extraI/capture absent')
    lo=value&255;hi=value>>8
    return bytes((0xa5,0x5a,opcode,lo,hi,opcode^lo^hi^0xff))

def decode_command(raw):
    if len(raw)!=6 or raw[:2]!=b'\xa5\x5a':raise ValueError('Sixbyte scalar packet required')
    opcode=raw[2];value=raw[3]|raw[4]<<8
    if raw!=scalar_packet(opcode,value):raise ValueError('E68 command checksum mismatch')
    return dict(opcode=opcode,value=value,is_STOP=raw==STOP_PACKET)

class StatusStream:
    """Resync strict normal frames. Never interpret P2D4 or legacy frames."""
    def __init__(self):self.buffer=bytearray()
    def feed(self,chunk):
        self.buffer.extend(chunk);out=[]
        while True:
            start=self.buffer.find(MAGIC)
            if start<0:
                if len(self.buffer)>3:del self.buffer[:-3]
                break
            if start:del self.buffer[:start]
            if len(self.buffer)<FRAME_BYTES:break
            raw=bytes(self.buffer[:FRAME_BYTES])
            try:decoded=decode_status(raw)
            except ValueError as exc:
                out.append(dict(raw=raw,decoded=None,error=str(exc)));del self.buffer[0]
            else:
                out.append(dict(raw=raw,decoded=decoded,error=None));del self.buffer[:FRAME_BYTES]
        return out
