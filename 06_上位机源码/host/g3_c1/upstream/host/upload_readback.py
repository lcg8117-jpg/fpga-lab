"""Read-only resolution of one staged POINT; never resend a mutation.

The existing FPGA exposes length, received count, rolling checksum, flags,
tracking mode and bank. This is protocol metadata evidence, not per-word RAM
readback. A failed/expired/inconsistent probe never grants continuation.
"""
import time,struct
from protocol import READ_CONFIG,WRITE_CONFIG,FIRMWARE,WIRE_VERSIONS

FIELDS=(0,1,2,3,4,7,8)

def validate_reads(reads,expected,*,minimum_rid=0):
 if len(reads)!=14:raise RuntimeError('独立读回次数不完整')
 last_rid=minimum_rid;last_epoch=None;highwater=None
 for index,ack in enumerate(reads):
  field=FIELDS[index%7]
  if (ack.get('kind'),ack.get('opcode'),ack.get('status'),ack.get('variant'),ack.get('firmware_id'),ack.get('wire_version'),ack.get('config_group'),ack.get('config_id'))!=('ack',READ_CONFIG,0,5,FIRMWARE[5],WIRE_VERSIONS[5],252,field):
   raise RuntimeError('独立读回应答身份或字段不匹配')
  if ack.get('config_revision')!=expected['revision'] or ack.get('config_read_value')!=expected['fields'][field]:
   raise RuntimeError('独立读回的点数、校验值、状态或版本不匹配')
  rid=ack.get('request_id');epoch=ack.get('control_epoch')
  if type(rid) is not int or rid<=last_rid or type(epoch) is not int or (last_epoch is not None and ((epoch-last_epoch)&0xffffffff)>=0x80000000):
   raise RuntimeError('独立读回请求或时序不连续')
  if highwater is not None and ack.get('mutation_highwater')!=highwater:raise RuntimeError('读回期间出现其他写操作')
  highwater=ack.get('mutation_highwater');last_epoch=epoch;last_rid=rid
 return reads[-1]

def probe(owner,expected,cancel,deadline):
 reads=[]
 for field in FIELDS*2:
  if cancel.is_set():raise RuntimeError('已取消核验')
  if time.monotonic()>=deadline:raise TimeoutError('独立读回超过本次时限')
  ticket=owner.command(READ_CONFIG,252,bytes((field,)),source='UPLOAD_READBACK')
  while time.monotonic()<deadline:
   if cancel.is_set():raise RuntimeError('已取消核验')
   with owner.state.lock:
    ack=owner.state.command_results.pop(ticket,None);connected=owner.state.connected
   if ack is not None:reads.append(ack);break
   if not connected:raise RuntimeError('读回时连接中断')
   cancel.wait(.001)
  else:raise TimeoutError('独立读回缺少应答')
 validate_reads(reads,expected)
 return reads

def await_point(owner,ticket,cancel,expected,stamp):
 started=time.monotonic();attempted=False
 while time.monotonic()-started<1.4:
  if cancel.is_set():raise RuntimeError('上传已取消；不重发')
  with owner.state.lock:
   result=owner.state.command_results.pop(ticket,None);error=owner.state.error;connected=owner.state.connected
  if result is not None:
   if result['status']!=0:raise RuntimeError(f"设备拒绝上传请求，状态{result['status']}")
   return result
  if error or not connected:raise RuntimeError(error or '连接中断')
  if not attempted and time.monotonic()-started>=.04:
   attempted=True
   try:
    # Finish early enough for the unchanged 500ms FPGA staging timeout.
    reads=probe(owner,expected,cancel,started+.32)
    receipt=owner.confirm_upload_point(ticket,expected,reads,stamp,cancel)
    if receipt is not None:
     if receipt.get('kind')=='ack' and receipt.get('status')!=0:raise RuntimeError(f"设备拒绝上传请求，状态{receipt['status']}")
     return receipt
   except Exception as exc:
    owner.trace('upload_point_readback_failed',queue_id=ticket,reason=str(exc))
    # Preserve any late genuine ACK. No write/reconnect/reset fallback.
  cancel.wait(.001)
 raise TimeoutError('上传点未得到应答或一致读回；不会重发或播放')
