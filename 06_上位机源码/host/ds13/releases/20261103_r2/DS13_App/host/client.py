"""Explicit-connect USB owner; read-only handshake, separate heartbeat and logging."""
from collections import deque
from pathlib import Path
from contextlib import nullcontext
import json,queue,secrets,struct,sys,threading,time
from protocol import *

class HostState:
    def __init__(self):
        self.lock=threading.RLock();self.connected=False;self.authenticated=False
        self.error='';self.message='未连接';self.command_results={};self.config_reads={}
        self.last_control=None;self.last_control_rx=0.;self.latest=None;self.diagnostic=None;self.last_ack=None
        self.independent=None;self.independent_bits=None;self.history=deque(maxlen=40000)
        self.control_generation=0;self.received=0;self.crc_errors=0;self.log_directory='';self.sequence_gaps=0
    def snapshot(self,*,include_history=True,history_after=None):
        with self.lock:
            result={k:(dict(v) if isinstance(v,dict) else v) for k,v in vars(self).items() if k not in ('lock','history')}
            result['history']=[]
            if include_history:
                if history_after is None:result['history']=list(self.history)
                else:
                    newer=[]
                    for row in reversed(self.history):
                        if not 0<((row['control_epoch']-history_after)&0xffffffff)<0x80000000:break
                        newer.append(row)
                    result['history']=list(reversed(newer))
            result['control_age_seconds']=time.monotonic()-self.last_control_rx if self.last_control_rx else None
            result['control_fresh']=bool(self.connected and self.authenticated and self.last_control and result['control_age_seconds']<=.5 and not self.error)
            return result

class Logger:
    def __init__(self,directory):
        self.directory=Path(directory);self.queue=queue.Queue(maxsize=12000);self.error='';self.thread=None;self.written=0
    def start(self):
        self.directory.mkdir(parents=True,exist_ok=False)
        self.raw=(self.directory/'frames.bin').open('wb');self.events=(self.directory/'events.jsonl').open('w',encoding='utf-8')
        self.thread=threading.Thread(target=self._run,name='DrawLog',daemon=True);self.thread.start()
    def submit(self,item,raw=b''):
        if self.error:return
        try:self.queue.put_nowait((dict(item,host_monotonic=time.monotonic()),raw))
        except queue.Full:self.error='记录队列已满'
    def _run(self):
        try:
            while True:
                item=self.queue.get()
                if item is None:break
                event,raw=item;self.events.write(json.dumps(event,ensure_ascii=False)+'\n')
                if raw:self.raw.write(raw)
                self.written+=1
                if self.written%100==0:self.raw.flush();self.events.flush()
        except Exception as exc:self.error=str(exc)
        finally:self.raw.close();self.events.close()
    def close(self):
        if self.thread and self.thread.is_alive():
            try:self.queue.put(None,timeout=1)
            except queue.Full:self.error='关闭记录时队列阻塞'
            self.thread.join(timeout=3)

def open_serial(port,baud):
    sys.path.insert(0,str(Path(__file__).parent/'vendor'))
    import serial
    device=serial.Serial(port=None,baudrate=baud,timeout=.005,write_timeout=.03,exclusive=True)
    device.dtr=False;device.rts=False;device.port=port;device.open()
    return device

class SerialOwner:
    def __init__(self,port,log_directory,*,serial_factory=open_serial,lease_factory=None,logger_factory=Logger):
        self.port=port;self.state=HostState();self.serial_factory=serial_factory;self.lease_factory=lease_factory
        self.logger=logger_factory(log_directory);self.state.log_directory=str(log_directory)
        self.queue=queue.PriorityQueue(maxsize=128);self.stop_event=threading.Event();self.closed=threading.Event()
        self.command_lock=threading.Lock();self.thread=None;self.parser=FrameParser();self.pending={}
        self.serial=None;self.counter=0;self.next_id=1;self.session=secrets.randbits(32) or 1
        self.hello_ok=False;self.schema_ok=False;self.status_ok=False;self.identity_failed=False
        self.last_rx=0.;self.last_board_ms=None;self.last_sequence=None;self.stop_for_error=False
        self.heartbeat_due=0.
    def start(self):
        self.thread=threading.Thread(target=self._run,name='LiveDrawSerialOwner',daemon=True);self.thread.start();return self
    def command(self,opcode,group=0,payload=b'',*,source='GUI',expected_generation=None):
        request(opcode,1,group,payload,session=self.session,event=1)
        with self.state.lock:
            if not self.state.connected:raise RuntimeError('请先明确连接')
            if opcode not in SAFE and (not self.state.authenticated or self.state.error):raise RuntimeError(self.state.error or '固件身份尚未核验')
            if opcode in LIVE_COMMANDS and not self.state.snapshot(include_history=False)['control_fresh']:raise RuntimeError('没有新鲜板端控制数据')
        with self.command_lock:
            if expected_generation is not None:
                with self.state.lock:
                    if self.state.control_generation!=expected_generation:raise RuntimeError('控制状态已改变，本笔结束')
            self.counter+=1;ticket=self.counter
            if opcode in (STOP,ESTOP):
                with self.state.lock:self.state.control_generation+=1
            if opcode in (STOP,ESTOP,LIVE_END,CANCEL_HOLD):
                saved=[]
                while True:
                    try:entry=self.queue.get_nowait()
                    except queue.Empty:break
                    if entry[2] in SAFE or (opcode in (LIVE_END,CANCEL_HOLD) and entry[2] not in LIVE_COMMANDS):saved.append(entry)
                    else:
                        with self.state.lock:self.state.command_results[entry[1]]=dict(status=1,cancelled=True)
                for entry in saved:self.queue.put_nowait(entry)
            self.queue.put_nowait((0 if opcode in (STOP,ESTOP) else .5 if opcode in (LIVE_END,CANCEL_HOLD) else 1,ticket,opcode,group,bytes(payload),source))
        return ticket
    def stop(self,wait=False):
        self.stop_event.set()
        if wait and self.thread:self.thread.join(timeout=5)
    def _send(self,opcode,group=0,payload=b'',ticket=None,source='heartbeat'):
        if self.next_id>0xffffffff:raise RuntimeError('请求编号耗尽；请断开后重连')
        rid=self.next_id;self.next_id+=1
        raw=request(opcode,rid,group,payload,session=self.session,event=rid)
        self.logger.submit(dict(kind='request',opcode=opcode,group=group,payload=payload.hex(),request_id=rid,ticket=ticket,source=source))
        sent=self.serial.write(raw)
        if sent!=len(raw):raise RuntimeError('写入不完整，结果未知，不自动重发')
        self.pending[rid]=(opcode,group,payload,ticket,time.monotonic())
    def _heartbeat(self):
        # Archived ADB1/V5: STATUS has its own 100ms clock, independent of ACKs.
        now=time.monotonic()
        if now>=self.heartbeat_due:
            self._send(STATUS);self.heartbeat_due=now+.1
    def _receive(self,raw,item):
        now=time.monotonic();self.logger.submit(item,raw)
        with self.state.lock:
            self.state.received+=1
            if not item['identity_ok']:
                self.identity_failed=True;self.state.authenticated=False;self.state.error='固件不匹配：需要DS13专用版本';return
            self.last_rx=now
            if 'schema' in item:self.schema_ok=True
            if item['kind']=='ack':
                pending=self.pending.get(item['request_id'])
                if not pending or pending[0]!=item['opcode']:return
                self.pending.pop(item['request_id']);op,group,payload,ticket,_=pending
                if op in (READ_CONFIG,WRITE_CONFIG) and (item['config_group']!=group or item['config_id']!=payload[0]):
                    self.state.error='应答配置字段与请求不匹配';return
                if ticket is not None:
                    self.state.command_results[ticket]=item
                    while len(self.state.command_results)>256:self.state.command_results.pop(next(iter(self.state.command_results)))
                self.state.last_ack=item;self.state.message=f"{STATUS_TEXT.get(item['status'],'未知状态')} · 指令{op:02X}"
                if item['status']==0:
                    if op==HELLO:self.hello_ok=True
                    if op==STATUS:self.status_ok=True
                    if op==READ_GAINS:self.state.independent=item['stored_gains'];self.state.independent_bits=item['stored_bits']
                    if op==READ_CONFIG:self.state.config_reads[(group,payload[0])]=item['config_read_value']
                    # User STOP/ESTOP already invalidated the stroke at enqueue.
                    # Its delayed ACK must not invalidate a later explicit action.
                    if op==ZERO or (op in (STOP,ESTOP) and ticket is None):self.state.control_generation+=1
                elif op==HELLO:self.state.error='板端会话未获准：请先停止已有控制连接'
            elif item['kind']=='diagnostic':self.state.diagnostic=item
            else:
                self.state.latest=item
                if self.last_board_ms is not None:
                    delta=(item['board_ms']-self.last_board_ms)&0xffffffff
                    if delta>=0x80000000:
                        self.state.error='检测到板端重置，请明确断开重连';self.state.authenticated=False;self.state.control_generation+=1;return
                self.last_board_ms=item['board_ms']
                if self.last_sequence is not None:
                    gap=(item['sequence']-self.last_sequence)&0xffffffff
                    if 1<gap<0x80000000:self.state.sequence_gaps+=gap-1
                self.last_sequence=item['sequence']
                if item['coherent']:
                    previous=self.state.last_control
                    if previous and not 0<((item['control_epoch']-previous['control_epoch'])&0xffffffff)<0x80000000:return
                    if previous and previous['mode']==3 and (item['mode']!=3 or item['fault']):self.state.control_generation+=1
                    self.state.last_control=item;self.state.last_control_rx=now
                    self.state.history.append(dict(item,received_at=now))
                if item['telemetry_dropped'] or item['rx_errors']:self.state.message='板端报告丢帧或通信错误，图窗将保留空隙'
            self.state.authenticated=self.hello_ok and self.status_ok and self.schema_ok and not self.identity_failed and not self.state.error
    def _run(self):
        from hardware_mutex import SharedMutex
        lease=self.lease_factory() if self.lease_factory else SharedMutex()
        try:
            with lease:
                self.logger.start()
                with self.serial_factory(self.port,2000000) as serial:
                    self.serial=serial
                    with self.state.lock:self.state.connected=True;self.state.message='核验固件身份中'
                    self._send(HELLO,source='connect');initial_reads=False;initial_queue=deque();connected_at=time.monotonic()
                    while not self.stop_event.is_set():
                        self._heartbeat()
                        if self.state.authenticated and not initial_reads:
                            initial_queue.extend([(READ_GAINS,255,b''),(READ_CONFIG,253,b'\x06'),(READ_CONFIG,254,b'\x05')]);initial_reads=True
                        if initial_queue:
                            op,group,payload=initial_queue.popleft();self._send(op,group,payload,source='independent_read')
                        # Qualified V5 drains up to four commands per iteration.
                        # pending binds replies; it does not gate the send queue.
                        for _ in range(4):
                            with self.command_lock:
                                try:_,ticket,op,group,payload,source=self.queue.get_nowait()
                                except queue.Empty:break
                                if op not in SAFE and (not self.state.authenticated or self.state.error):
                                    with self.state.lock:self.state.command_results[ticket]=dict(status=1,cancelled=True)
                                    continue
                                self._send(op,group,payload,ticket,source)
                        chunk=serial.read(4096)
                        for raw,item in self.parser.feed(chunk):
                            self._heartbeat();self._receive(raw,item)
                        self._heartbeat();now=time.monotonic()
                        with self.state.lock:self.state.crc_errors=self.parser.errors
                        for rid,p in list(self.pending.items()):
                            if now-p[4]>1.0:
                                self.pending.pop(rid)
                                if p[0] not in READS:
                                    with self.state.lock:self.state.error='命令结果未知，已锁定；不会自动重发'
                        if now-(self.last_rx or connected_at)>.5:
                            with self.state.lock:self.state.error='超过500ms未收到新帧，请断开后重连';self.state.authenticated=False
                        if self.logger.error:
                            with self.state.lock:self.state.error='记录失败：'+self.logger.error
                        # A matched identity is required even for automatic fault STOP.
                        if self.state.error and self.hello_ok and self.schema_ok and not self.identity_failed and not self.stop_for_error:
                            self._send(STOP,source='host_fault');self.stop_for_error=True
                        if not chunk:time.sleep(.001)
                    if self.hello_ok and self.schema_ok and not self.identity_failed:
                        stop_id=self.next_id;self._send(STOP,source='explicit_disconnect')
                        deadline=time.monotonic()+.3
                        while stop_id in self.pending and time.monotonic()<deadline:
                            for raw,item in self.parser.feed(serial.read(4096)):self._receive(raw,item)
                        if stop_id in self.pending:self.logger.submit(dict(kind='disconnect_stop_ack_unknown',request_id=stop_id))
        except Exception as exc:
            with self.state.lock:self.state.error=str(exc);self.state.message='连接已结束'
        finally:
            self.logger.close()
            with self.state.lock:self.state.connected=False;self.state.authenticated=False;self.state.control_generation+=1
            self.closed.set()
