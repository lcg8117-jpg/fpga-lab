"""Single serial-owner thread, independent of Tk rendering and disk compression."""
from collections import deque
from contextlib import nullcontext
from pathlib import Path
import queue
import struct
import sys
import threading
import time
import secrets

from protocol import *
from session_log import SessionLogger
import gain_average

STALE_RX_ERROR='超过500ms未收到有效新帧，控制已锁定'

class HostState:
    def __init__(self,variant):
        self.variant=variant;self.lock=threading.RLock();self.connected=False;self.authenticated=False
        self.message='未连接';self.error='';self.latest=None;self.last_ack=None;self.stored=None;self.independent=None
        self.stored_revision=None;self.independent_revision=None;self.config_reads={};self.config_saved={}
        self.history=deque(maxlen=12000 if variant==5 else 4000 if variant in (2,4) else 2000);self.crc_errors=0;self.sequence_gaps=0;self.last_sequence=None
        self.sent=0;self.received=0;self.timeouts=0;self.last_rx=0.0;self.last_heartbeat=0.0;self.max_heartbeat_gap=0.0
        self.pending_mutations=0;self.unmatched_acks=0;self.manual_drafts=[format(v,'.9g') for v in DEFAULT_GAINS]
        self.log_status='未开始';self.log_error='';self.read_config_requests={}
        self.last_control=None;self.last_control_rx=0.0;self.latest_rx=0.0;self.last_ack_rx=0.0
        self.latest_diagnostic=None;self.last_diagnostic_rx=0.0;self.last_diagnostic_sequence=None
        self.diagnostic_sequence_gaps=0;self.learning_event_gaps=0;self.adoption_event_gaps=0
        self.last_learning_event=None;self.last_adoption_event=None
        self.learning_events=deque(maxlen=60);self.adoption_events=deque(maxlen=60)
        self.control_gain_events=deque(maxlen=80)
        self.gesture_events=deque(maxlen=60);self.last_gesture_event_seq=None;self.gesture_event_gaps=0
        self.motion_reference_start=None;self.motion_reference_start_epoch=None
        self.gain_average=gain_average.new_state()
        self.last_compact=None;self.last_compact_rx=0.0;self.compact_history=deque(maxlen=4000)
        self.command_events=deque(maxlen=100);self.duplicate_controls=0;self.board_resets=0
        self.goal_history=deque(maxlen=4000 if variant==5 else 1200)
    def snapshot(self):
        with self.lock:
            result={key:(list(value) if isinstance(value,deque) else dict(value) if isinstance(value,dict) else value)
                    for key,value in vars(self).items() if key not in ('lock','gain_average')}
            result['control_age_seconds']=time.monotonic()-self.last_control_rx if self.last_control_rx else None
            result['control_fresh']=bool(self.connected and self.authenticated and self.last_control is not None and
                                         result['control_age_seconds'] is not None and result['control_age_seconds']<=0.5)
            result['actual_gain_averages']=gain_average.summarize(self.gain_average,time.monotonic(),result['control_fresh'])
            return result

def open_serial(port,baud):
    # The frozen pyserial dependency is bundled locally. No pip/network step.
    sys.path.insert(0,str(Path(__file__).parent/'vendor'))
    import serial
    return serial.Serial(port=port,baudrate=baud,timeout=0.005,write_timeout=0.02,exclusive=True)

class SerialOwner:
    def __init__(self,variant,port,log_directory,*,serial_factory=open_serial,logger_factory=SessionLogger,logger_options=None,lease_factory=None):
        if variant not in FIRMWARE:raise ValueError('版本必须为1/2/3')
        self.state=HostState(variant);self.port=port;self.log_directory=log_directory;self.serial_factory=serial_factory
        self.logger_factory=logger_factory;self.logger_options=logger_options or {}
        self.lease_factory=lease_factory;self.parser=FrameParser();self.queue=queue.PriorityQueue(maxsize=128)
        self.stop_event=threading.Event();self.thread=None;self.pending={};self.next_id=1;self.counter=0;self.logger=None
        self._log_stop_sent=False;self._serial=None;self.closed=threading.Event()
        self._identity_failed=False
        self._input_tail=b''
        self.rx_stale=False
        self.logical_session=secrets.randbits(32) or 1;self._sending_source='internal';self.queued_sources={}
        self.completed_requests=deque(maxlen=128)
        self.command_lock=threading.RLock()
        self._route_stop_sent=False
    def trace(self,event,**fields):
        entry=dict(event=event,mono_ns=time.monotonic_ns(),**fields)
        with self.state.lock:self.state.command_events.append(entry)
        if self.logger and hasattr(self.logger,'event'):self.logger.event(event,**fields)
    def start(self):
        if self.thread:raise RuntimeError('串口所有者已经启动')
        self.thread=threading.Thread(target=self._run,name='ForestSerialOwner',daemon=True);self.thread.start();return self
    def command(self,opcode,group=0,payload=b'',*,source='GUI'):
        self.trace('intent',source=source,opcode=opcode,group=group)
        try:return self._command(opcode,group,payload,source=source)
        except Exception as exc:
            self.trace('host_rejected',source=source,opcode=opcode,reason=str(exc));raise
    def _command(self,opcode,group=0,payload=b'',*,source='GUI'):
        with self.state.lock:
            if not self.state.connected:raise RuntimeError('未连接串口')
            if not self.state.authenticated and opcode not in (STATUS,STOP,ESTOP):raise RuntimeError('固件身份尚未核验，禁止控制')
            if self.state.log_error and opcode not in SAFE:raise RuntimeError('日志不可用，已阻止非STOP控制变更')
            if self.state.error and opcode not in SAFE:raise RuntimeError('未解决错误已锁定控制：'+self.state.error)
            if opcode in (ADAPT_ENABLE,ADAPT_DISABLE) and self.state.variant!=3:raise ValueError('本版本没有自适应功能')
            if opcode in range(MOTION_CW,MOTION_STOP+1) and self.state.variant!=2:raise ValueError('本版本没有旋转功能')
            if opcode in (SELECT_SWING,START_SWING) and self.state.variant==3:raise ValueError('本版本没有起摆功能')
            if opcode in (SELECT_SWING,START_SWING) and self.state.variant==5:raise ValueError('V5已移除起摆；请手动扶直、ZERO后RUN')
            if self.state.variant==5 and opcode in (READ_CONFIG,WRITE_CONFIG) and (group in (0,1,2) or (group==254 and payload[:1]==b'\x00')):raise ValueError('V5已移除起摆参数与方案选择')
            if opcode in (POSITION,CANCEL_HOLD) and self.state.variant<4:raise ValueError('本版本没有六位置功能')
            if opcode==TRAJECTORY and self.state.variant!=5:raise ValueError('轨迹功能仅V5支持')
            if opcode in (MOTION_CW,MOTION_CCW,MOTION_LEFT,MOTION_RIGHT,POSITION,TRAJECTORY):
                full=self.state.snapshot()['control_fresh']
                compact=self.state.last_compact
                brief=bool(compact and compact['coherent'] and compact['range_valid'] and time.monotonic()-self.state.last_compact_rx<=.5)
                if not full and not brief:raise RuntimeError('没有500ms内的新控制事务，禁止新的运动计划；STOP仍可用')
        # Validate packet shape before placing user work in the owner queue.
        request(opcode,1,group,payload)
        with self.command_lock:
            self.counter+=1
            if opcode in (STOP,ESTOP):
                retained=[]
                while True:
                    try:entry=self.queue.get_nowait()
                    except queue.Empty:break
                    if entry[2] in SAFE:retained.append(entry)
                    else:
                        self.queued_sources.pop(entry[1],None);self.trace('queue_cancelled_by_stop',queue_id=entry[1],opcode=entry[2])
                for entry in retained:self.queue.put_nowait(entry)
            self.queued_sources[self.counter]=source
            self.queue.put_nowait((0 if opcode in (STOP,ESTOP) else 1,self.counter,opcode,group,bytes(payload)))
            self.trace('queued',source=source,queue_id=self.counter,opcode=opcode,group=group)
    def set_gains(self,values):
        payload=gains_payload(values)
        with self.state.lock:
            if self.state.latest and self.state.latest['flags']&2 and self.state.stored:
                new=struct.unpack('<6f',payload)
                if (new[1],new[4])!=(self.state.stored[1],self.state.stored[4]):raise ValueError('积分I只能在STOP状态修改')
        self.command(SET_GAINS,255,payload)
    def set_kit(self,value):
        if value not in (0,1):raise ValueError('套件只能选择原套件0或IP570 1')
        with self.state.lock:
            candidates=[(self.state.latest_rx,self.state.latest),(self.state.last_ack_rx,self.state.last_ack)]
            stamp,item=max(candidates,key=lambda pair:pair[0])
            if not item or time.monotonic()-stamp>0.5:raise RuntimeError('需要500ms内的新鲜板端停机状态')
            if item['mode'] not in (0,1) or item['fault'] or item['flags']&6:
                raise RuntimeError('切换套件要求STOP、故障清除且电机无输出许可')
        self.command(WRITE_CONFIG,254,b'\x05'+struct.pack('<I',value))
    def stop(self,wait=True):
        self.stop_event.set()
        if wait and self.thread:self.thread.join(timeout=5)
    def _send(self,opcode,group=0,payload=b'',*,internal=False):
        if self.next_id>0xffffffff:raise RuntimeError('请求ID已耗尽；禁止回绕或自动复位FPGA')
        request_id=self.next_id;self.next_id+=1
        version=WIRE_VERSIONS[self.state.variant]
        source=('heartbeat' if opcode==STATUS else 'internal') if internal else self._sending_source
        route=self._serial.route_for_source(source) if hasattr(self._serial,'route_for_source') else 'usb'
        route_code=1 if route=='ble' else 2 if route=='mirror' else 0
        raw=request(opcode,request_id,group,payload,version=version,session=self.logical_session,event=request_id,route=route_code)
        values=list(struct.unpack('<6f',payload)) if opcode==SET_GAINS else list(payload) if len(payload)<=1 else dict(field=payload[0],value=struct.unpack_from('<I',payload,1)[0])
        self.trace('write_requested',source=source,session=self.logical_session if version!=2 else None,event_id=request_id,request_id=request_id,opcode=opcode,group=group,values=values,expected_bytes=len(raw))
        try:sent=self._serial.write(raw,route=route) if hasattr(self._serial,'route_for_source') else self._serial.write(raw)
        except Exception as exc:
            self.trace('write_unknown',source=source,request_id=request_id,reason=str(exc));raise
        if self.logger and hasattr(self.logger,'io') and not getattr(self._serial,'records_physical_io',False):self.logger.io(0,1,raw[:sent] if sent is not None else raw)
        self.trace('write_returned',request_id=request_id,reported_bytes=sent,transport_queued=bool(getattr(self._serial,'async_write',False)))
        if sent is not None and sent!=len(raw):raise OSError('串口仅写入部分请求；禁止自动重发')
        now=time.monotonic();self.pending[request_id]=(opcode,group,payload,now)
        with self.state.lock:
            self.state.sent+=1
            if opcode not in READS:self.state.pending_mutations+=1
            if opcode==STATUS:
                if self.state.last_heartbeat:self.state.max_heartbeat_gap=max(self.state.max_heartbeat_gap,now-self.state.last_heartbeat)
                self.state.last_heartbeat=now
        return request_id
    def _receive(self,raw,item):
        now=time.monotonic()
        with self.state.lock:self.state.received+=1;self.state.last_rx=now
        if self.logger and not self.logger.submit(raw,item):
            with self.state.lock:self.state.log_error=self.logger.error;self.state.message='日志异常，控制已锁定'
        if item['kind']=='compact':
            if item['variant']!=self.state.variant or item['wire_version']!=WIRE_VERSIONS[self.state.variant]:return
            if not self.state.authenticated:return
            with self.state.lock:
                previous=self.state.last_compact
                if previous:
                    distance=(item['control_epoch']-previous['control_epoch'])&0xffffffff
                    reset=item['board_ms']<previous['board_ms'] and previous['board_ms']-item['board_ms']<0x80000000
                    if reset:
                        self.state.compact_history.clear();self.state.goal_history.clear();self.state.authenticated=False;self.state.board_resets+=1
                        if not self.state.error or self.state.error==STALE_RX_ERROR:self.state.error='检测到板端重置，请明确断开再连接；没有重放动作'
                        self.trace('board_reset_observed',route=1,board_ms=item['board_ms']);return
                    if item['coherent'] and previous['coherent'] and not 0<distance<0x80000000:
                        if distance and (item['mode']!=3 or previous['mode']!=3):self.state.compact_history.clear()
                        else:self.state.duplicate_controls+=1;return
                self.state.last_compact=item;self.state.last_compact_rx=now
                if item['coherent'] and item['range_valid']:
                    self.state.compact_history.append(dict(item))
            return
        if item['variant']!=self.state.variant or item['firmware_id']!=FIRMWARE[self.state.variant] or item.get('wire_version',2)!=WIRE_VERSIONS[self.state.variant] or (item['kind'] in ('telemetry','diagnostic') and item['schema']!=SCHEMAS[self.state.variant]):
            self._identity_failed=True
            with self.state.lock:self.state.authenticated=False;self.state.error='固件/版本/schema不匹配，禁止控制';self.state.message=self.state.error
            return
        if item['kind']=='ack':
            pending=self.pending.get(item['request_id'])
            self.trace('ack',request_id=item['request_id'],opcode=item['opcode'],status=item['status'],matched=bool(pending),mode=item['mode'],fault=item['fault'],control_epoch=item['control_epoch'])
            if not pending or pending[0]!=item['opcode']:
                if item['request_id'] in self.completed_requests:
                    self.trace('mirrored_ack_duplicate',request_id=item['request_id']);return
                with self.state.lock:self.state.unmatched_acks+=1
                return
            self.pending.pop(item['request_id'])
            self.completed_requests.append(item['request_id'])
            opcode,group,payload,_=pending
            if WIRE_VERSIONS[self.state.variant]==2:self.next_id=max(self.next_id,item['mutation_highwater']+1)
            with self.state.lock:
                self.state.last_ack=item
                self.state.last_ack_rx=now
                if opcode not in READS:self.state.pending_mutations=max(0,self.state.pending_mutations-1)
                self.state.message=f"{STATUS_TEXT.get(item['status'],'未知状态')} · 操作0x{opcode:02X} · ID{item['request_id']}"
                if opcode==STATUS and item['status']==0 and not self._identity_failed:
                    self.state.authenticated=True;self.rx_stale=False
                    if self.state.error==STALE_RX_ERROR:self.state.error=''
                if item['status']==0:
                    self.state.stored=item['stored_gains'];self.state.stored_revision=item['stored_revision']
                    if opcode==READ_GAINS:self.state.independent=item['stored_gains'];self.state.independent_revision=item['stored_revision']
                    if opcode==READ_CONFIG:
                        self.state.config_reads[(item['config_group'],item['config_id'])]=(item['config_read_value'],item['config_revision'])
                    if opcode==WRITE_CONFIG:self.state.config_saved[(group,payload[0])]=item['config_revision']
            if item['status']==0 and opcode==SET_GAINS and self.state.authenticated:self.command(READ_GAINS,255)
            if item['status']==0 and opcode==WRITE_CONFIG and self.state.authenticated:self.command(READ_CONFIG,group,payload[:1])
            if item['status']==0 and opcode==STATUS and self.state.authenticated and self.state.independent is None:
                # At most one outstanding independent initial bank read.
                if not any(entry[0]==READ_GAINS for entry in self.pending.values()):self.command(READ_GAINS,255)
            if item['status']==0 and opcode==STATUS and self.state.authenticated and self.state.variant>=4 and (254,8) not in self.state.config_reads:
                if not any(entry[0]==READ_CONFIG and entry[1]==254 and entry[2]==b'\x08' for entry in self.pending.values()):self.command(READ_CONFIG,254,b'\x08')
        elif item['kind']=='diagnostic':
            with self.state.lock:
                previous=self.state.last_diagnostic_sequence
                if previous is not None:
                    distance=(item['sequence']-previous)&0xffffffff
                    if 1<distance<0x80000000:self.state.diagnostic_sequence_gaps+=distance-1
                self.state.last_diagnostic_sequence=item['sequence'];self.state.latest_diagnostic=item
                self.state.last_diagnostic_rx=now
                if 'final_target' in item:self.state.goal_history.append((item['board_ms'],item['final_target'],item['origin'],item['motion_request_id'],item.get('final_target_valid',True)))
                for kind in ('learning','adoption'):
                    counter=item[kind+'_event_seq'];prior=getattr(self.state,'last_'+kind+'_event')
                    if prior is not None:
                        distance=(counter-prior)&0xffffffff
                        if 1<distance<0x80000000:
                            setattr(self.state,kind+'_event_gaps',getattr(self.state,kind+'_event_gaps')+distance-1)
                    if counter and counter!=prior:
                        getattr(self.state,kind+'_events').append(dict(item))
                    setattr(self.state,'last_'+kind+'_event',counter)
        else:
            with self.state.lock:
                previous=self.state.last_sequence
                if previous is not None:
                    distance=(item['sequence']-previous)&0xffffffff
                    if 1<distance<0x80000000:self.state.sequence_gaps+=distance-1
                self.state.last_sequence=item['sequence'];self.state.latest=item;self.state.latest_rx=now
                if self.state.variant==3:gain_average.observe(self.state.gain_average,item,now)
                gesture_seq=item['gesture_event_seq'];prior_gesture=self.state.last_gesture_event_seq
                if prior_gesture is not None:
                    distance=(gesture_seq-prior_gesture)&0xffffffff
                    if 1<distance<0x80000000:self.state.gesture_event_gaps+=distance-1
                if gesture_seq and gesture_seq!=prior_gesture:
                    # This is the first observed frame for the decoded event,
                    # not an invented optical-event timestamp or TX cause.
                    self.state.gesture_events.append(dict(item))
                self.state.last_gesture_event_seq=gesture_seq
                if item['coherent']:
                    previous_control=self.state.last_control
                    if previous_control:
                        distance=(item['control_epoch']-previous_control['control_epoch'])&0xffffffff
                        reset=(item['board_ms']<previous_control['board_ms'] and previous_control['board_ms']-item['board_ms']<0x80000000)
                        if reset:
                            self.state.board_resets+=1;self.state.history.clear();self.state.compact_history.clear()
                            self.state.authenticated=False;self.state.message='检测到板时刻重置，等待同会话状态确认；不重放动作'
                            if not self.state.error or self.state.error==STALE_RX_ERROR:self.state.error='检测到板端重置，请明确断开再连接；没有重放动作'
                            gain_average.clear(self.state.gain_average,'board_reset')
                        elif not 0<distance<0x80000000:
                            if distance and (item['mode']!=3 or previous_control['mode']!=3):
                                self.state.history.clear();self.state.motion_reference_start=None;self.state.motion_reference_start_epoch=None
                            else:self.state.duplicate_controls+=1;return
                    if self.state.variant==2:
                        if item['mode']!=3 or (previous_control and item['control_epoch']<previous_control['control_epoch']):
                            self.state.motion_reference_start=None;self.state.motion_reference_start_epoch=None
                        elif previous_control and previous_control['mode']==3 and item['motion_state'] in (1,2) and previous_control['motion_state'] in (0,4):
                            self.state.motion_reference_start=previous_control['wide_target']
                            self.state.motion_reference_start_epoch=previous_control['control_epoch']
                    if previous_control and item['active_gains']!=previous_control['active_gains']:
                        self.state.control_gain_events.append(dict(before=previous_control['active_gains'],after=item['active_gains'],
                            control_epoch=item['control_epoch'],board_ms=item['board_ms'],freeze=item['freeze_reason'],
                            adaptive_used=item['adaptive_used_at_launch'],motor_permitted=item['motor_permitted_at_completion']))
                    self.state.last_control=item;self.state.last_control_rx=now
                    angle=(item['mean_adc12']-3085)*345.0/4096
                    target=(item['target_angle_adc12']-3085)*345.0/4096
                    self.state.history.append((item['control_epoch'],angle,target,item['position_error'],item['wide_encoder'],item['wide_target'],item['board_ms']))
    def _run(self):
        if self.lease_factory is None:
            from hardware_mutex import SharedMutex
            lease=SharedMutex()
        else:lease=self.lease_factory()
        try:
            with lease:
                self.logger=self.logger_factory(self.log_directory,self.state.variant,**self.logger_options)
                try:self.logger.start()
                except Exception as exc:
                    self.logger.error=str(exc)
                    with self.state.lock:self.state.log_error=str(exc);self.state.log_status='日志启动失败，控制已锁定'
                with self.serial_factory(self.port,2000000) as serial:
                    self._serial=serial
                    with self.state.lock:self.state.connected=True;self.state.message='等待新版固件身份确认'
                    if WIRE_VERSIONS[self.state.variant]!=2:self._send(HELLO,internal=True)
                    heartbeat_due=0.0
                    route_parsers={0:self.parser,1:FrameParser()}
                    while not self.stop_event.is_set():
                        now=time.monotonic()
                        if now>=heartbeat_due:
                            self._send(STATUS,internal=True);heartbeat_due=now+0.1
                        # Safety traffic remains independent of log compression.
                        if self.logger and self.logger.error:
                            with self.state.lock:self.state.log_error=self.logger.error
                        if self.state.log_error and self.state.authenticated and not self._log_stop_sent:
                            self._send(STOP,internal=True);self._log_stop_sent=True
                        for _ in range(4):
                            try:_,queue_id,opcode,group,payload=self.queue.get_nowait()
                            except queue.Empty:break
                            self._sending_source=self.queued_sources.pop(queue_id,'GUI')
                            if not self.state.authenticated and opcode not in (STATUS,STOP,ESTOP):
                                with self.state.lock:self.state.message='身份未核验，已取消未发送的控制请求'
                                continue
                            if self.state.log_error and opcode not in SAFE:
                                with self.state.lock:self.state.message='日志异常，已取消未发送的控制变更'
                                continue
                            self._send(opcode,group,payload)
                        chunks=serial.read_routes(4096) if hasattr(serial,'read_routes') else [(0,serial.read(4096))]
                        data=bool(any(raw for _,raw in chunks))
                        for route,chunk in chunks:
                            if not chunk:continue
                            if self.logger and hasattr(self.logger,'io') and not getattr(serial,'records_physical_io',False):self.logger.io(route,0,chunk)
                            for raw,item in route_parsers[route].feed(chunk):
                                self.trace('parsed',route=route,kind=item['kind'],request_id=item.get('request_id'),sequence=item.get('sequence'))
                                self._receive(raw,item)
                        if getattr(serial,'route_errors',None):
                            with self.state.lock:self.state.error='通信通道故障/写入结果可能未知，未重连未重发：'+str(serial.route_errors)
                            if not self._route_stop_sent:self._route_stop_sent=True;self._send(STOP,internal=True)
                        with self.state.lock:
                            self.state.crc_errors=sum(parser.crc_errors for parser in route_parsers.values())
                            if self.logger:self.state.log_status=f'已保存{self.logger.written}帧，队列{self.logger.queued_bytes}字节'
                        for request_id,entry in list(self.pending.items()):
                            if now-entry[3]>1.0:
                                self.pending.pop(request_id)
                                with self.state.lock:
                                    self.state.timeouts+=1
                                    if entry[0] not in READS:
                                        self.state.pending_mutations=max(0,self.state.pending_mutations-1);self.state.error='命令结果未知：不会自动重发，请独立读回确认'
                        if self.state.last_rx and now-self.state.last_rx>0.5:
                            with self.state.lock:
                                self.state.authenticated=False;self.rx_stale=True
                                if self.state.error in ('',STALE_RX_ERROR):self.state.error=STALE_RX_ERROR
                        if not data:time.sleep(0.001)
                    if self.state.authenticated:self._send(STOP,internal=True)
        except Exception as exc:
            with self.state.lock:self.state.error=str(exc);self.state.message='串口已停止：'+str(exc)
        finally:
            with self.state.lock:
                self.state.connected=False;self.state.authenticated=False
                gain_average.clear(self.state.gain_average,'disconnected')
            if self.logger:
                self.logger.close()
                with self.state.lock:
                    self.state.log_error=self.logger.error
                    self.state.log_status=('已完成：' if not self.logger.error else '保留异常记录：')+str(self.logger.archive_path if not self.logger.error else self.logger.partial_path)
            self.closed.set()
