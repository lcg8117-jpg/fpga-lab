"""Lossless physical byte chunks once, with separate command and frame metadata."""
from pathlib import Path
from datetime import datetime
import json,queue,struct,threading,time,zipfile,zlib
CHUNK=struct.Struct('<QQBBII')
JOURNAL=struct.Struct('<cI')
class TraceLogger:
    def __init__(self,directory,variant,*,max_queue_bytes=2*1024**2,**legacy_options):
        # Disk quotas were explicitly removed by the owner. Legacy options are ignored.
        self.directory=Path(directory);self.variant=variant;self.max_queue_bytes=int(max_queue_bytes)
        self.queue=queue.Queue()
        self.queued_bytes=0;self.written=0;self.logical_bytes=0;self.error='';self.closing=False;self.thread=None
        self.archive_path=None;self.partial_path=None;self.raw_path=None;self.events_path=None;self.finished=threading.Event()
        self.critical_queue=queue.Queue();self.critical_bytes=0;self.memory_critical=[];self.memory_critical_bytes=0
        self.safety_path=None;self.safety_error='';self.first_fault=None;self.raw_received=0;self.raw_written=0;self.raw_missing=0;self.first_missing=None;self.last_missing=None;self.missing_ranges=[];self.missing_ranges_truncated=False
        self.lock=threading.RLock();self._tail_initial=None
    def start(self):
        self.directory.mkdir(parents=True,exist_ok=True)
        stem=f'ForestFive_V{self.variant}_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        self.partial_path=self.directory/(stem+'.partial.zip');self.archive_path=self.directory/(stem+'.zip')
        self.safety_path=self.directory/(stem+'.failure')
        self.thread=threading.Thread(target=self._run,name='ForestPhysicalTrace',daemon=True);self.thread.start();return self
    def _remember(self,data):
        if self.memory_critical_bytes+len(data)<=256*1024:
            self.memory_critical.append(data);self.memory_critical_bytes+=len(data)
    def _missing(self,sequence):
        self.raw_missing+=1
        self.first_missing=sequence if self.first_missing is None else min(self.first_missing,sequence)
        self.last_missing=sequence if self.last_missing is None else max(self.last_missing,sequence)
        ranges=sorted([*self.missing_ranges,[sequence,sequence]])
        merged=[]
        for start,end in ranges:
            if merged and start<=merged[-1][1]+1:merged[-1][1]=max(merged[-1][1],end)
            else:merged.append([start,end])
        self.missing_ranges=merged[:128]
        if len(merged)>128:self.missing_ranges_truncated=True
    def _put(self,kind,data,sequence=None):
        size=len(data)+64
        with self.lock:
            if self.closing or self.thread is None:
                if sequence is not None:self._missing(sequence)
                elif kind=='event':self._remember(data)
                return False
            if kind=='event':
                if self.critical_bytes+size>256*1024:
                    self._remember(data);self.error=self.error or '关键事件队列溢出，落盘不完整';return False
                self.critical_bytes+=size;self.critical_queue.put((kind,data,size,sequence));return True
            if self.queued_bytes+size>self.max_queue_bytes:
                self._missing(sequence);self.error=self.error or '日志队列溢出，存在未保存数据；控制锁定';return False
            self.queued_bytes+=size
            self.queue.put((kind,data,size,sequence));return True
    def io(self,route,direction,raw):
        raw=bytes(raw)
        with self.lock:self.raw_received+=1;sequence=self.raw_received
        return self._put('raw',CHUNK.pack(time.time_ns(),time.monotonic_ns(),route,direction,len(raw),zlib.crc32(raw))+raw,sequence)
    def event(self,event,**fields):
        return self._put('event',(json.dumps(dict(event=event,wall_ns=time.time_ns(),mono_ns=time.monotonic_ns(),**fields),ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8'))
    def submit(self,raw,item,**unused):
        # Per-frame JSON can be reconstructed from physical bytes.
        if item.get('fault') and self.first_fault is None:
            self.first_fault={name:item.get(name) for name in ('kind','fault','sequence','control_epoch','board_ms','mode','motion_request_id')}
            self.event('first_fault',**self.first_fault)
        return not bool(self.error)
    def _next(self):
        while True:
            try:return self.critical_queue.get_nowait()
            except queue.Empty:pass
            try:return self.queue.get(timeout=.02)
            except queue.Empty:
                if self.closing and self.critical_queue.empty() and self.queue.empty():return None
    def _done(self,kind,size):
        with self.lock:
            if kind=='event':self.critical_bytes-=size
            else:self.queued_bytes-=size
    def _write_primary(self,file,data):
        if file.write(data)!=len(data):raise OSError('日志部分写入；当前记录完整性未知')
    def _failure_tail(self):
        journal=None
        def save(kind,data,sequence=None):
            nonlocal journal
            try:
                if self.safety_error:raise OSError(self.safety_error)
                if journal is None:journal=self.safety_path.open('xb',buffering=0)
                record=JOURNAL.pack(b'R' if kind=='raw' else b'E',len(data))+data
                if journal.write(record)!=len(record):raise OSError('故障证据部分写入')
                if sequence is not None:self.raw_written+=1
            except Exception as exc:
                self.safety_error=str(exc)
                if sequence is not None:self._missing(sequence)
                else:self._remember(data)
        save('event',(json.dumps(dict(event='log_failure',wall_ns=time.time_ns(),mono_ns=time.monotonic_ns(),reason=self.error))+'\n').encode('utf-8'))
        entry=self._tail_initial
        while True:
            if entry is None:entry=self._next()
            if entry is None:break
            kind,data,size,sequence=entry;save(kind,data,sequence);self._done(kind,size);entry=None
        save('event',(json.dumps(dict(event='log_closed',wall_ns=time.time_ns(),mono_ns=time.monotonic_ns(),raw_received=self.raw_received,raw_written=self.raw_written,raw_missing=self.raw_missing,
            first_missing=self.first_missing,last_missing=self.last_missing,missing_ranges=self.missing_ranges,missing_ranges_truncated=self.missing_ranges_truncated,error=self.error,safety_error=self.safety_error),ensure_ascii=False)+'\n').encode('utf-8'))
        if journal:journal.close()
    def _run(self):
        try:
            raw_path=self.partial_path.with_suffix('.chunks');events_path=self.partial_path.with_suffix('.events')
            self.raw_path=raw_path;self.events_path=events_path
            with raw_path.open('xb',buffering=0) as raw,events_path.open('xb',buffering=0) as events:
                while True:
                    entry=self._next()
                    if entry is None:break
                    kind,data,size,sequence=entry
                    if self.error:
                        self._tail_initial=entry;raise OSError(self.error)
                    try:self._write_primary(raw if kind=='raw' else events,data)
                    except Exception:
                        if sequence is not None:self._missing(sequence)
                        else:self.critical_queue.put(entry)
                        if sequence is not None:self._done(kind,size)
                        raise
                    self.logical_bytes+=len(data);self.written+=1
                    if sequence is not None:self.raw_written+=1
                    self._done(kind,size)
            with zipfile.ZipFile(self.partial_path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as z:
                z.write(raw_path,'physical_chunks.bin');z.write(events_path,'events.jsonl')
                z.writestr('session.json',json.dumps(dict(format='ForestFive.Physical.v2',variant=self.variant,chunk_header=CHUNK.format,
                    route='0 USB;1 BLE',direction='0 RX;1 TX',raw_copy_count=1,disk_quotas_enabled=False,
                    written=self.written,error=self.error,raw_received=self.raw_received,raw_written=self.raw_written,max_queue_bytes=self.max_queue_bytes),indent=2))
            if not self.error:self.partial_path.rename(self.archive_path);raw_path.unlink();events_path.unlink()
        except Exception as exc:
            self.error=str(exc);self._failure_tail()
        finally:self.finished.set()
    def close(self,wait=True):
        with self.lock:
            if not self.closing:self.closing=True
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


def iter_journal(path):
    """Read failure-tail records; R carries a single CHUNK+payload, E carries JSON."""
    with Path(path).open('rb') as f:
        while header:=f.read(JOURNAL.size):
            if len(header)!=JOURNAL.size:raise ValueError('journal header truncated')
            kind,length=JOURNAL.unpack(header)
            if kind not in (b'R',b'E') or length>2*1024**2:raise ValueError('journal header invalid')
            data=f.read(length)
            if len(data)!=length:raise ValueError('journal record truncated')
            yield kind,data

def iter_recovered_chunks(primary_path,failure_path=None,*,report=None):
    """Recover a valid sidecar prefix and journal tail without inventing missing bytes.

    report is populated with exact source offsets, CRC/truncation failures and the
    logger's missing chunk ordinal ranges. A failed/absent summary is incomplete.
    """
    report={} if report is None else report
    report.update(complete=False,gaps=[],summary=None,primary_valid_bytes=0)
    primary=Path(primary_path)
    if primary.suffix=='.zip':
        try:
            yield from iter_chunks(primary)
            report['complete']=failure_path is None
        except (OSError,ValueError,zipfile.BadZipFile) as exc:
            report['gaps'].append(dict(source=str(primary),reason=str(exc)))
    else:
        try:
            with primary.open('rb') as file:
                while header:=file.read(CHUNK.size):
                    offset=file.tell()-len(header)
                    if len(header)!=CHUNK.size:
                        report['gaps'].append(dict(source=str(primary),offset=offset,reason='truncated chunk header'));break
                    wall,mono,route,direction,length,crc=CHUNK.unpack(header)
                    if length>2*1024**2:
                        report['gaps'].append(dict(source=str(primary),offset=offset,reason='invalid chunk length'));break
                    data=file.read(length)
                    if len(data)!=length or zlib.crc32(data)!=crc:
                        report['gaps'].append(dict(source=str(primary),offset=offset,reason='truncated payload or CRC mismatch'));break
                    report['primary_valid_bytes']=file.tell()
                    yield wall,mono,route,direction,data
        except OSError as exc:report['gaps'].append(dict(source=str(primary),reason=str(exc)))
    if failure_path is not None:
        try:
            for kind,data in iter_journal(failure_path):
                if kind==b'E':
                    event=json.loads(data)
                    if event.get('event')=='log_closed':report['summary']=event
                    continue
                if len(data)<CHUNK.size:raise ValueError('journal chunk header truncated')
                wall,mono,route,direction,length,crc=CHUNK.unpack(data[:CHUNK.size]);raw=data[CHUNK.size:]
                if len(raw)!=length or zlib.crc32(raw)!=crc:raise ValueError('journal chunk CRC/length mismatch')
                yield wall,mono,route,direction,raw
        except (OSError,ValueError) as exc:report['gaps'].append(dict(source=str(failure_path),reason=str(exc)))
        # A failure summary never upgrades unknown physical records to complete.
        report['complete']=False
