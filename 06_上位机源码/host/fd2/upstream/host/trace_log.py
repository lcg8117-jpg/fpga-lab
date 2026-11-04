"""Lossless physical byte chunks once, with separate command and frame metadata."""
from pathlib import Path
from datetime import datetime
import json,queue,shutil,struct,threading,time,zipfile,zlib
from session_log import GIB
CHUNK=struct.Struct('<QQBBII')
class TraceLogger:
    def __init__(self,directory,variant,*,max_shared_bytes=2*GIB,min_free_bytes=10*GIB,reserve_bytes=8*1024**2,max_queue_bytes=2*1024**2,disk_usage=shutil.disk_usage,shared_roots=()):
        self.directory=Path(directory);self.variant=variant;self.max_shared_bytes=min(int(max_shared_bytes),2*GIB)
        self.min_free_bytes=max(int(min_free_bytes),10*GIB);self.reserve_bytes=max(int(reserve_bytes),8*1024**2)
        self.max_queue_bytes=int(max_queue_bytes);self.disk_usage=disk_usage
        self.roots=[self.directory,*map(Path,shared_roots)];self.queue=queue.Queue();self.lock=threading.Lock()
        self.queued_bytes=0;self.written=0;self.logical_bytes=0;self.error='';self.closing=False;self.thread=None
        self.archive_path=None;self.partial_path=None;self.raw_path=None;self.events_path=None;self.finished=threading.Event()
    def _check(self):
        files={}
        for root in self.roots:
            if root.exists():
                for p in root.rglob('*'):
                    if p.is_file():files[str(p.resolve()).casefold()]=p.stat().st_size
        own=sum(p.stat().st_size for p in (self.raw_path,self.events_path) if p is not None and p.exists())
        # Keep room for the worst-case lossless archive while its source
        # sidecars still exist. No history is removed to make room.
        if sum(files.values())+own+2*self.queued_bytes+self.reserve_bytes>=self.max_shared_bytes:raise OSError('全部登记日志或归档预留已到共享额度，保留历史并锁定控制')
        if self.disk_usage(self.directory).free<self.min_free_bytes+self.reserve_bytes:raise OSError('日志盘不足10GiB加8MiB预留，控制锁定')
    def start(self):
        self.directory.mkdir(parents=True,exist_ok=True);self._check()
        stem=f'ForestFive_V{self.variant}_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        self.partial_path=self.directory/(stem+'.partial.zip');self.archive_path=self.directory/(stem+'.zip')
        self.thread=threading.Thread(target=self._run,name='ForestPhysicalTrace',daemon=True);self.thread.start();return self
    def _put(self,kind,data):
        size=len(data)+64
        with self.lock:
            if self.closing or self.error:return False
            if self.queued_bytes+size>self.max_queue_bytes:self.error='日志队列溢出，存在未保存数据；控制锁定';return False
            self.queued_bytes+=size
        self.queue.put((kind,data,size));return True
    def io(self,route,direction,raw):
        raw=bytes(raw)
        return self._put('raw',CHUNK.pack(time.time_ns(),time.monotonic_ns(),route,direction,len(raw),zlib.crc32(raw))+raw)
    def event(self,event,**fields):
        return self._put('event',(json.dumps(dict(event=event,wall_ns=time.time_ns(),mono_ns=time.monotonic_ns(),**fields),ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8'))
    def submit(self,raw,item,**unused):
        # Physical RX was already recorded before parsing, including corruption.
        return self.event('frame',kind=item['kind'],sequence=item.get('sequence'),epoch=item.get('control_epoch'),request_id=item.get('request_id'),board_ms=item.get('board_ms'),coherent=item.get('coherent',False),
            **{name:item[name] for name in ('gesture_event_seq','action_reason','motion_request_id','origin','final_target','decode_reason','gesture_state','motion_action','planner_phase') if name in item})
    def _run(self):
        try:
            raw_path=self.partial_path.with_suffix('.chunks');events_path=self.partial_path.with_suffix('.events')
            self.raw_path=raw_path;self.events_path=events_path
            with raw_path.open('xb') as raw,events_path.open('xb') as events:
                while True:
                    entry=self.queue.get()
                    if entry is None:break
                    kind,data,size=entry
                    if self.written%200==0:self._check()
                    if self.logical_bytes+len(data)+self.reserve_bytes>=self.max_shared_bytes:raise OSError('本会话达到共享日志额度')
                    (raw if kind=='raw' else events).write(data);self.logical_bytes+=len(data);self.written+=1
                    with self.lock:self.queued_bytes-=size
            self._check()
            with zipfile.ZipFile(self.partial_path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as z:
                z.write(raw_path,'physical_chunks.bin');z.write(events_path,'events.jsonl')
                z.writestr('session.json',json.dumps(dict(format='ForestFive.Physical.v1',variant=self.variant,chunk_header=CHUNK.format,
                    route='0 USB;1 BLE',direction='0 RX;1 TX',raw_copy_count=1,log_roots=[str(p) for p in self.roots],
                    written=self.written,error=self.error,max_shared_bytes=self.max_shared_bytes,min_free_bytes=self.min_free_bytes,reserve_bytes=self.reserve_bytes),indent=2))
            if not self.error:self.partial_path.rename(self.archive_path);raw_path.unlink();events_path.unlink()
        except Exception as exc:self.error=str(exc)
        finally:self.finished.set()
    def close(self,wait=True):
        with self.lock:
            if not self.closing:self.closing=True;self.queue.put(None)
        if wait and self.thread:self.thread.join(timeout=30)
        if self.thread and self.thread.is_alive():self.error='归档未结束，保留partial及原始侧文件'
        return self.partial_path if self.error else self.archive_path
def iter_chunks(path):
    with zipfile.ZipFile(path) as z,z.open('physical_chunks.bin') as f:
        while header:=f.read(CHUNK.size):
            if len(header)!=CHUNK.size:raise ValueError('chunk header truncated')
            wall,mono,route,direction,length,crc=CHUNK.unpack(header);raw=f.read(length)
            if len(raw)!=length or zlib.crc32(raw)!=crc:raise ValueError('chunk CRC/length mismatch')
            yield wall,mono,route,direction,raw
