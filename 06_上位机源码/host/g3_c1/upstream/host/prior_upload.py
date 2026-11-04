"""Resolve the preserved previous POINT uncertainty using reads only."""
import json,time,threading
from upload_readback import probe
GUARD='正在独立核对上次未确认的上传；暂不接受新的运动或写入'

def check_prior(owner,source,output):
 cancel=threading.Event()
 def work():
  record=dict(status='UNRESOLVED',source=str(source),read_only=True,old_write_retried=False)
  try:
   raw=source.read_bytes()
   if len(raw)>65536:raise RuntimeError('历史上传证据过大')
   old=json.loads(raw);record['original_request']=old['request'];record['original_unknown']=old['unknown']
   deadline=time.monotonic()+5
   while time.monotonic()<deadline:
    s=owner.state.snapshot()
    if s['connected'] and s['authenticated']:break
    if owner.stop_event.is_set():raise RuntimeError('窗口已关闭')
    time.sleep(.01)
   else:raise TimeoutError('没有新设备身份回传')
   expected=dict(revision=old['expected_revision_after'],fields={0:old['expected_total'],1:old['expected_received_after'],
    2:old['expected_prefix_checksum'],3:0,4:0x44524334,7:old['expected_tracking'],8:old['expected_bank']})
   reads=probe(owner,expected,cancel,time.monotonic()+2)
   record.update(status='APPLIED_BY_METADATA_READBACK_STAGING_EXPIRED',expected=expected,reads=reads,
    original_ack_missing=True,limitation='Protocol rolling checksum and metadata, not bytewise RAM readback',
    next_action='No automatic writes. Next explicit PLAY prepares a new upload because the old staging transaction expired.')
   output.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
   owner.trace('prior_upload_independently_resolved',original_request_id=old['request']['request_id'],evidence=str(output),write_retried=False)
   with owner.state.lock:
    if owner.state.error==GUARD:owner.state.error=''
  except Exception as exc:
   record['error']=str(exc);output.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
   with owner.state.lock:
    if owner.state.error==GUARD:owner.state.error='上次上传尚未独立核实：'+str(exc)
   owner.trace('prior_upload_still_unknown',reason=str(exc))
 worker=threading.Thread(target=work,name='PriorUploadReadOnly',daemon=True);worker.start();return worker
