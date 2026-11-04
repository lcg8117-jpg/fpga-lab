"""Bounded pre-parser evidence. Never opens a device or writes on import."""
from collections import Counter, deque
from datetime import datetime
from pathlib import Path
import json
import struct
import threading
import time
import zipfile

# route, direction: 0=RX notification, 1=TX fragment. Preserve callback boundaries.
IO_HEADER = struct.Struct('<QQBBI')

class LinkDiagnostics:
    def __init__(self, directory, *, max_bytes=4*1024*1024):
        self.directory=Path(directory);self.max_bytes=max_bytes
        self.lock=threading.RLock();self.write_lock=threading.Lock()
        self.records=deque();self.initial=[];self.initial_bytes=0;self.bytes=0
        self.dropped_records=0;self.dropped_bytes=0;self.counts=Counter()
        self.events=deque(maxlen=1024);self.error='';self.archive_path=''
        self.first_export=None;self.thread=None
        self.token=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    def io(self,route,direction,data):
        try:
            raw=bytes(data);block=IO_HEADER.pack(time.time_ns(),time.monotonic_ns(),route,direction,len(raw))+raw
            with self.lock:
                self.counts['rx_notifications' if direction==0 else 'tx_fragments']+=1
                self.counts['rx_bytes' if direction==0 else 'tx_bytes']+=len(raw)
                # Preserve connection startup plus a bounded rolling recent tail.
                if self.initial_bytes < self.max_bytes//2:
                    self.initial.append(block);self.initial_bytes+=len(block)
                else:
                    self.records.append(block);self.bytes+=len(block)
                    while self.bytes+self.initial_bytes>self.max_bytes and self.records:
                        old=self.records.popleft();self.bytes-=len(old)
                        self.dropped_records+=1;self.dropped_bytes+=len(old)
        except Exception as exc:self.error='原始接收证据记录失败：'+repr(exc)
    def event(self,event,**data):
        try:
            with self.lock:self.events.append(dict(wall_ns=time.time_ns(),mono_ns=time.monotonic_ns(),event=event,**data))
        except Exception as exc:self.error='连接事件记录失败：'+repr(exc)
    def _write(self,state):
        try:
            with self.write_lock:
                with self.lock:
                    chunks=tuple(self.initial)+tuple(self.records)
                    events=list(self.events);counts=dict(self.counts)
                    dropped=dict(records=self.dropped_records,bytes=self.dropped_bytes)
                self.directory.mkdir(parents=True,exist_ok=True)
                path=self.directory/f'LinkDiagnostics_{self.token}.zip'
                temporary=path.with_suffix('.writing')
                metadata=dict(format='J280.LinkDiagnostics.v1',io_header='<QQBBI: wall_ns,mono_ns,route,direction,length',
                    direction={'0':'RX pre-parser BLE notification','1':'TX physical BLE fragment'},
                    counts=counts,capture_budget_bytes=self.max_bytes,dropped_due_to_capture_budget=dropped,
                    snapshot=state,events=events,error=self.error,
                    scope='Connection evidence only; no change to firmware, PID, identity, timeout or motor protections.')
                with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
                    with archive.open('io.bin','w') as stream:
                        for block in chunks:stream.write(block)
                    archive.writestr('diagnostics.json',json.dumps(metadata,ensure_ascii=False,indent=2,default=str))
                temporary.replace(path);self.archive_path=str(path)
        except Exception as exc:self.error='诊断归档失败：'+repr(exc)
    def export_on_first_error(self,state):
        if self.first_export is not None:return
        self.first_export=True
        # Compress outside the BLE/control threads; no connection operation here.
        self.thread=threading.Thread(target=self._write,args=(state,),name='BLEEvidenceWriter',daemon=True)
        self.thread.start()
    def close(self,state):
        if self.thread:self.thread.join(timeout=2)
        self._write(state)
