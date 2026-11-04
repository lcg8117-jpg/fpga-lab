"""Bounded background lossless RX archive; validated frame bytes are stored once."""
from pathlib import Path
import csv
from datetime import datetime
import json
import os
import queue
import shutil
import struct
import threading
import time
import zipfile

RECORD_HEADER=struct.Struct('<QQH')
GIB=1024**3

def choose_log_directory():
    if os.name=='nt':
        candidates=[]
        for letter in 'DEFGHIJKLMNOPQRSTUVWXYZC':
            drive=Path(letter+':/')
            try:
                if drive.is_dir():candidates.append((shutil.disk_usage(drive).free,drive))
            except OSError:pass
        data=[item for item in candidates if str(item[1]).upper()[0]!='C']
        if data or candidates:return max(data or candidates,key=lambda item:item[0])[1]/'ForestD1_Logs'
    return Path.home()/'ForestD1_Logs'

class SessionLogger:
    def __init__(self,directory,variant,*,max_shared_bytes=2*GIB,min_free_bytes=10*GIB,reserve_bytes=8*1024**2,max_queue_bytes=2*1024**2,disk_usage=shutil.disk_usage):
        if min(max_shared_bytes,reserve_bytes,max_queue_bytes)<=0 or min_free_bytes<0:raise ValueError('日志限额必须为正，最小余量不可为负')
        self.directory=Path(directory);self.variant=variant
        self.max_shared_bytes=int(max_shared_bytes);self.min_free_bytes=int(min_free_bytes);self.reserve_bytes=int(reserve_bytes);self.max_queue_bytes=int(max_queue_bytes)
        self.disk_usage=disk_usage;self.queue=queue.Queue();self.lock=threading.Lock();self.queued_bytes=0;self.peak_queue_bytes=0
        self.error='';self.received=0;self.written=0;self.rejected=0;self.logical_bytes=0;self.finished=threading.Event();self.closing=False
        self.thread=None;self.archive_path=None;self.partial_path=None;self.started_ns=time.time_ns()
    def start(self):
        self.directory.mkdir(parents=True,exist_ok=True)
        self._check_space()
        stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        self.partial_path=self.directory/f'ForestD1_V{self.variant}_{stamp}.partial.zip'
        self.archive_path=self.directory/f'ForestD1_V{self.variant}_{stamp}.zip'
        self.thread=threading.Thread(target=self._run,name='ForestLog',daemon=True);self.thread.start();return self
    def _check_space(self):
        used=sum(p.stat().st_size for p in self.directory.rglob('*') if p.is_file())
        if used>=self.max_shared_bytes:raise OSError('日志目录已达到共享额度；历史记录不会自动删除')
        if self.disk_usage(self.directory).free<self.min_free_bytes+self.reserve_bytes:raise OSError('日志磁盘余量不足：最小余量加关键预留未满足')
    def submit(self,raw,item,*,wall_ns=None,mono_ns=None):
        raw=bytes(raw);size=len(raw)+RECORD_HEADER.size
        with self.lock:
            self.received+=1
            if self.error or self.closing or self.queued_bytes+size>self.max_queue_bytes:
                self.rejected+=1
                if not self.error:self.error='日志队列达到内存上限，已阻止控制变更；存在未保存记录'
                return False
            self.queued_bytes+=size;self.peak_queue_bytes=max(self.peak_queue_bytes,self.queued_bytes)
        # Metadata contains no second copy of the raw payload.
        metadata=(item['kind'],item.get('sequence',''),item.get('control_epoch',''),int(item.get('coherent',False)),item.get('board_ms',''))
        self.queue.put((time.time_ns() if wall_ns is None else wall_ns,time.monotonic_ns() if mono_ns is None else mono_ns,raw,metadata));return True
    def close(self,wait=True):
        with self.lock:
            if not self.closing:self.closing=True;self.queue.put(None)
        if wait and self.thread:self.thread.join(timeout=30)
        if wait and self.thread and self.thread.is_alive():
            with self.lock:self.error='后台归档尚未完成，保留partial文件，不能确认为完整日志'
        return self.archive_path if self.finished.is_set() and not self.error else self.partial_path
    def _run(self):
        index_path=self.partial_path.with_suffix('.index.csv')
        try:
            offset=0
            with zipfile.ZipFile(self.partial_path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True) as archive,index_path.open('x',encoding='utf8',newline='') as index_file:
                index=csv.writer(index_file);index.writerow(('record','offset','bytes','kind','sequence','control_epoch','coherent','board_ms','host_unix_ns','host_monotonic_ns'))
                with archive.open('records.bin','w',force_zip64=True) as records:
                    while True:
                        entry=self.queue.get()
                        if entry is None:break
                        wall,mono,raw,metadata=entry;size=len(raw)+RECORD_HEADER.size
                        if self.written%200==0:self._check_space()
                        if self.logical_bytes+size+self.reserve_bytes>self.max_shared_bytes:raise OSError('本会话达到日志额度，已保留现有数据')
                        records.write(RECORD_HEADER.pack(wall,mono,len(raw)));records.write(raw)
                        index.writerow((self.written,offset,len(raw),*metadata,wall,mono));offset+=size
                        self.written+=1;self.logical_bytes+=size
                        with self.lock:self.queued_bytes-=size
                index_file.flush()
                archive.writestr('session.json',json.dumps({'format':'ForestD1.Raw.v2','variant':self.variant,'received':self.received,'written':self.written,'rejected':self.rejected,'error':self.error,'started_unix_ns':self.started_ns,'max_shared_bytes':self.max_shared_bytes,'min_free_bytes':self.min_free_bytes,'reserve_bytes':self.reserve_bytes,'max_queue_bytes':self.max_queue_bytes,'peak_queue_bytes':self.peak_queue_bytes,'record_header':'little endian unix_ns:u64,monotonic_ns:u64,length:u16; validated frame follows once','components':'FD2T coherent200Hz transactions preserve launch gains and25ms position provenance; FD2D diagnostic and FD2A ACK records are separate kinds/sequences.','schema':'0x46443203','gain_provenance':'T bit5 adaptive enabled at launch; bit6 actual used bank source at launch; permission/mode/fault observed at completion. Control freshness is not refreshed by diagnostic/status frames.'},ensure_ascii=False,indent=2))
                archive.write(index_path,'index.csv')
            # Metadata sidecar has no raw bytes; remove only our own completed
            # transient index after its lossless copy entered the final ZIP.
            if not self.error:
                self.partial_path.rename(self.archive_path);index_path.unlink()
        except Exception as exc:
            with self.lock:self.error=str(exc)
        finally:self.finished.set()

def iter_records(archive_path):
    with zipfile.ZipFile(archive_path) as archive,archive.open('records.bin') as file:
        while True:
            header=file.read(RECORD_HEADER.size)
            if not header:break
            if len(header)!=RECORD_HEADER.size:raise ValueError('日志记录头截断')
            wall,mono,length=RECORD_HEADER.unpack(header);raw=file.read(length)
            if len(raw)!=length:raise ValueError('日志帧截断')
            yield wall,mono,raw
