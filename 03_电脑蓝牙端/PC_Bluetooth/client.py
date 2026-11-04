"""One BLE connection owner with the qualified session/STOP/stale semantics.

Importing creates no connection, device import, thread, or log file.
"""
from collections import deque
from pathlib import Path
import queue
import secrets
import threading
import time

from protocol import *
from session_log import SessionLogger

STALE_RX_ERROR='超过500ms未收到有效新帧，控制已锁定'

class HostState:
    def __init__(self):
        self.lock=threading.RLock();self.connected=False;self.authenticated=False
        self.message='未连接';self.error='';self.latest=None;self.last_ack=None;self.diagnostic=None
        self.latest_rx=0.;self.last_ack_rx=0.;self.diagnostic_rx=0.;self.last_rx=0.
        self.stored=None;self.independent=None;self.stored_revision=None;self.independent_revision=None
        self.history=deque(maxlen=2400);self.events=deque(maxlen=12)
        self.sent=0;self.received=0;self.timeouts=0;self.crc_errors=0;self.format_errors=0;self.sequence_gaps=0
        self.log_status='未开始';self.log_error='';self.unbound_compact=0
    def snapshot(self):
        with self.lock:
            result={k:(list(v) if isinstance(v,deque) else v) for k,v in vars(self).items() if k!='lock'}
        now=time.monotonic()
        result['control_fresh']=bool(result['connected'] and result['authenticated'] and result['latest'] and
            result['latest']['coherent'] and result['latest']['range_valid'] and now-result['latest_rx']<=.5)
        return result

class BLEConnection:
    """Pure BLE adapter over the existing BT10 transport; no serial fallback."""
    records_physical_io=True
    async_write=True
    def __init__(self,address):
        from bt10_windows_select import install
        install(address)
        from ble_transport import BLEPort
        self.port=BLEPort(address)
    def read(self,n):return self.port.read(n)
    def write(self,raw):return self.port.write(raw)
    def close(self):self.port.close()
    def __enter__(self):return self
    def __exit__(self,*unused):self.close()

class BLEOwner:
    def __init__(self,address,log_directory,*,port_factory=BLEConnection,logger_factory=SessionLogger,lease_factory=None):
        self.state=HostState();self.address=address;self.log_directory=Path(log_directory)
        self.port_factory=port_factory;self.logger_factory=logger_factory;self.lease_factory=lease_factory
        self.parser=FrameParser();self.queue=queue.PriorityQueue(maxsize=64);self.command_lock=threading.RLock()
        self.stop_event=threading.Event();self.closed=threading.Event();self.thread=None
        self.pending={};self.completed=deque(maxlen=128);self.next_id=1;self.counter=0
        self.session=secrets.randbits(32) or 1;self.port=None;self.logger=None
        self.identity_ack=False;self.identity_d=False;self.identity_failed=False;self.log_stop_sent=False
    def start(self):
        if self.thread:raise RuntimeError('连接所有者已经启动')
        self.thread=threading.Thread(target=self._run,name='V4_BLE_Owner',daemon=True)
        self.thread.start();return self
    def _event(self,message):
        with self.state.lock:self.state.events.append(message);self.state.message=message
    def _require_stopped(self,*,parameters=False):
        with self.state.lock:
            if not self.state.connected or not self.state.authenticated:raise RuntimeError('请先连接并核验板端身份')
            if self.state.error or self.state.log_error:raise RuntimeError('错误未解决，控制已锁定')
            # Prefer compact on equal Windows timer ticks, so an earlier
            # stopped ACK cannot override a just-arrived running sample.
            stamp,item=max(((self.state.latest_rx,self.state.latest),(self.state.last_ack_rx,self.state.last_ack)),key=lambda x:x[0])
            if not item or time.monotonic()-stamp>.5:raise RuntimeError('需要500ms内的板端停机状态')
            # A bits1/2/3 = capture/run/motor; B bits1/2/3 = run/motor/capture.
            if item['mode'] not in (0,1) or item['fault'] or item['flags']&0x0e:
                raise RuntimeError('请先STOP并确认停机、故障清除和电机输出禁止')
            if any(entry[0]==RUN for entry in self.pending.values()):raise RuntimeError('RUN尚待确认，请先STOP')
            with self.queue.mutex:
                if any(entry[2]==RUN for entry in self.queue.queue):raise RuntimeError('RUN已排队，请先STOP')
    def parameter_write_status(self):
        try:self._require_stopped(parameters=True);return True,''
        except RuntimeError as exc:return False,str(exc)
    def _require_balance(self):
        s=self.state.snapshot()
        if not s['control_fresh'] or s['latest']['mode']!=3 or s['latest']['fault']:
            raise RuntimeError('需要500ms内的新鲜平衡控制事务')
    def command(self,opcode,group=0,payload=b''):
        request(opcode,1,group,payload,session=self.session,event=1)
        with self.state.lock:
            if not self.state.connected:raise RuntimeError('尚未连接蓝牙')
            if not self.state.authenticated and opcode not in (STATUS,STOP,ESTOP):raise RuntimeError('固件身份尚未核验')
            if (self.state.error or self.state.log_error) and opcode not in SAFE:
                raise RuntimeError('当前错误已锁定控制：'+(self.state.error or self.state.log_error))
        if opcode in (SET_GAINS,ZERO,RUN):self._require_stopped(parameters=opcode==SET_GAINS)
        if opcode==RUN:
            with self.state.lock:
                if not self.state.diagnostic or not self.state.diagnostic['zero_valid']:
                    raise RuntimeError('请扶直校零并等待板端校零有效状态')
        if opcode in (POSITION,CANCEL_HOLD):
            self._require_balance()
        with self.command_lock:
            self.counter+=1
            if opcode in (STOP,ESTOP):
                retained=[]
                while True:
                    try:entry=self.queue.get_nowait()
                    except queue.Empty:break
                    if entry[2] in SAFE:retained.append(entry)
                for entry in retained:self.queue.put_nowait(entry)
            self.queue.put_nowait((0 if opcode in (STOP,ESTOP) else 1,self.counter,opcode,group,bytes(payload)))
    def set_gains(self,values):self.command(SET_GAINS,255,gains_payload(values))
    def stop(self,wait=True):
        self.stop_event.set()
        if wait and self.thread:self.thread.join(timeout=5)
    def _send(self,opcode,group=0,payload=b''):
        if opcode in (SET_GAINS,ZERO,RUN):self._require_stopped(parameters=opcode==SET_GAINS)
        if opcode in (POSITION,CANCEL_HOLD):self._require_balance()
        if self.next_id>0xffffffff:raise RuntimeError('请求ID已耗尽，禁止回绕或自动重连')
        rid=self.next_id;self.next_id+=1
        raw=request(opcode,rid,group,payload,session=self.session,event=rid)
        sent=self.port.write(raw)
        if sent is not None and sent!=len(raw):raise OSError('蓝牙请求写入不完整，结果未知且不会重发')
        self.pending[rid]=(opcode,group,payload,time.monotonic())
        with self.state.lock:self.state.sent+=1
        return rid
    def _authenticate(self):
        with self.state.lock:
            if self.identity_ack and self.identity_d and not self.identity_failed and not self.state.error:
                self.state.authenticated=True
    def _receive(self,raw,item):
        now=time.monotonic()
        with self.state.lock:self.state.received+=1;self.state.last_rx=now
        if self.logger and not self.logger.submit(raw,item):
            with self.state.lock:self.state.log_error=self.logger.error
        if item['kind']=='diagnostic':
            if item['session']!=self.session:return
            with self.state.lock:
                old=self.state.diagnostic
                if old and item['board_ms']<old['board_ms'] and old['board_ms']-item['board_ms']<0x80000000:
                    self.state.authenticated=False;self.identity_failed=True;self.state.error='板端已重置，请断开后重新连接'
                self.state.diagnostic=item;self.state.diagnostic_rx=now
            self.identity_d=True;self._authenticate();return
        if item['kind']=='ack':
            prior=self.pending.get(item['request_id'])
            if not prior or prior[0]!=item['opcode']:return
            self.pending.pop(item['request_id']);self.completed.append(item['request_id'])
            opcode,group,payload,_=prior
            with self.state.lock:
                self.state.last_ack=item;self.state.last_ack_rx=now
                self.state.message=f"{STATUS_TEXT.get(item['status'],'未知状态')} · 操作0x{opcode:02X} · ID{item['request_id']}"
                if item['status']==0:
                    self.state.stored=item['stored_gains'];self.state.stored_revision=item['stored_revision']
                    if opcode==READ_GAINS:self.state.independent=item['stored_gains'];self.state.independent_revision=item['stored_revision']
            if opcode in READS and item['status']==0:self.identity_ack=True;self._authenticate()
            if item['status']==0 and opcode==SET_GAINS:self.command(READ_GAINS,255)
            return
        with self.state.lock:
            # A B frame has neither firmware nor schema. It cannot bind a session,
            # unlock an operation, or seed the graph before both fresh A and D.
            if not self.state.authenticated:
                self.state.unbound_compact+=1;return
            old=self.state.latest
            if old:
                distance=(item['sequence']-old['sequence'])&0xffffffff
                if item['board_ms']<old['board_ms'] and old['board_ms']-item['board_ms']<0x80000000:
                    self.state.authenticated=False;self.identity_failed=True
                    self.state.error='板端已重置，请断开后重新连接';return
                if distance==0 or distance>=0x80000000:return
                if distance>1:self.state.sequence_gaps+=distance-1
            self.state.latest=item;self.state.latest_rx=now
            if item['coherent'] and item['range_valid']:self.state.history.append(dict(item))
    def _run(self):
        if self.lease_factory is None:
            from hardware_mutex import SharedMutex
            lease=SharedMutex()
        else:lease=self.lease_factory()
        try:
            with lease:
                self.logger=self.logger_factory(self.log_directory,4);self.logger.start()
                with self.port_factory(self.address) as port:
                    self.port=port
                    with self.state.lock:self.state.connected=True;self.state.message='等待R27固件与schema核验'
                    self._send(HELLO);heartbeat_due=0.;identity_deadline=time.monotonic()+3.
                    while not self.stop_event.is_set():
                        now=time.monotonic()
                        if now>=heartbeat_due:self._send(STATUS);heartbeat_due=now+.1
                        if self.logger.error:
                            with self.state.lock:self.state.log_error=self.logger.error
                            if not self.log_stop_sent:self._send(STOP);self.log_stop_sent=True
                        for _ in range(4):
                            try:_,_,opcode,group,payload=self.queue.get_nowait()
                            except queue.Empty:break
                            with self.state.lock:
                                permitted=(opcode in (STATUS,STOP,ESTOP) or self.state.authenticated) and (
                                    opcode in SAFE or not (self.state.error or self.state.log_error))
                            if not permitted:continue
                            try:self._send(opcode,group,payload)
                            except RuntimeError as exc:self._event('请求未发送：'+str(exc))
                        data=port.read(4096)
                        for raw,item in self.parser.feed(data):self._receive(raw,item)
                        with self.state.lock:
                            self.state.crc_errors=self.parser.crc_errors;self.state.format_errors=self.parser.format_errors
                            self.state.log_status=f'已保存{self.logger.written}帧'
                            if now>identity_deadline and not (self.identity_ack and self.identity_d):
                                self.identity_failed=True;self.state.authenticated=False
                                self.state.error='未收到匹配的R27固件/schema身份，控制已锁定'
                            if self.state.last_rx and now-self.state.last_rx>.5:
                                self.state.authenticated=False;self.state.error=STALE_RX_ERROR
                            if self.identity_d and now-self.state.diagnostic_rx>1.5:
                                self.state.authenticated=False;self.state.error='板端身份诊断已过期，请断开后重新连接'
                        for rid,entry in list(self.pending.items()):
                            if now-entry[3]>1.:
                                self.pending.pop(rid)
                                with self.state.lock:
                                    self.state.timeouts+=1
                                    if entry[0] not in READS:self.state.error='命令结果未知，不会自动重发；请独立确认'
                        if not data:time.sleep(.001)
                    # A disconnect/close asks STOP once. BLE transport never
                    # retries an unknown write and the FPGA watchdog remains.
                    if self.state.connected:
                        try:self._send(STOP)
                        except Exception:pass
                        until=time.monotonic()+.2
                        while time.monotonic()<until:
                            data=port.read(4096)
                            for raw,item in self.parser.feed(data):self._receive(raw,item)
                            time.sleep(.005)
        except Exception as exc:
            with self.state.lock:self.state.error=str(exc);self.state.message='蓝牙已停止：'+str(exc)
        finally:
            with self.state.lock:self.state.connected=False;self.state.authenticated=False
            if self.logger:
                self.logger.close()
                with self.state.lock:
                    self.state.log_error=self.logger.error
                    self.state.log_status=('已保存：' if not self.logger.error else '日志未完成：')+str(self.logger.archive_path if not self.logger.error else self.logger.partial_path)
            self.closed.set()
