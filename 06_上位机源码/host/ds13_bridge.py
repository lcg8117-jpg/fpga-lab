"""DS13 adapter around the unmodified delivered USB owner and LiveStroke."""
from pathlib import Path
import importlib
import re
import struct
import sys
import threading
import time
import uuid

from host.fd2_bridge import Bridge, json_safe

ROOT = Path(__file__).resolve().parent / 'ds13' / 'releases' / '20261103_r2' / 'DS13_App' / 'host'
_MODULES = None

class StrokeRejected(ValueError):
    def __init__(self,message,code):
        super().__init__(message);self.code=code

def load_upstream():
    global _MODULES
    if _MODULES is not None: return _MODULES
    names = ('protocol','client','live_control','hardware_mutex','timeline','draw_editor','app')
    saved = {n:sys.modules.pop(n, None) for n in names}
    sys.path.insert(0,str(ROOT))
    try:
        _MODULES = {n:importlib.import_module(n) for n in names}
    finally:
        # The delivered owner imports hardware_mutex lazily in its worker.
        # Keep its directory available after loading.
        for n in names:
            if saved[n] is None: sys.modules.pop(n,None)
            else: sys.modules[n]=saved[n]
    return _MODULES

class DS13Bridge(Bridge):
    LEASE = .2
    def __init__(self, *, clock=time.monotonic, wall_clock=time.time):
        super().__init__(5)
        self.clock=clock
        self.wall_clock=wall_clock
        self.stroke=None
        self.active_id=None
        self.input_source=None
        self.native_is_held=None
        self.seq=-1
        self.deadline=0
        self.used=set()
        self.last_diagnostic_at=0
        self._watchdog_started=False
        self._beginning=False; self._ending=False
        self._begin_tickets={};self._job_bindings={}

    def attach_owner(self,owner):
        self.cancel_stroke()
        with self.dispatch_lock, self.lock:
            self.new_session(); self.owner=owner; self.used=set(); self.last_diagnostic_at=0
            self._begin_tickets.clear();self._job_bindings.clear()
            self.stroke=load_upstream()['live_control'].LiveStroke(lambda: self.owner, self.is_held)
            owner._bridge_enqueue=threading.local()
            queue=owner.queue
            original_put=queue.put_nowait
            def put(entry):
                rid=getattr(owner._bridge_enqueue,'rid',None)
                if rid and entry[5]=='WEB':
                    with self.lock:
                        if owner is self.owner: self.queue_requests[entry[1]]=rid
                if entry[5]=='MOUSE_PRESS':
                    with self.lock:
                        if owner is self.owner and self.active_id:
                            self._begin_tickets[entry[1]]=(self.session,self.active_id)
                try: return original_put(entry)
                except Exception:
                    with self.lock:
                        if owner is self.owner: self.queue_requests.pop(entry[1],None)
                    raise
            queue.put_nowait=put
    def _watchdog(self):
        while True:
            time.sleep(.02)
            with self.lock:
                if not self.active_id and not self._beginning:
                    self._watchdog_started=False
                    return
                expired=bool(self.active_id and not self._held_locked())
            if expired: self.cancel_stroke(expired_only=True)

    def is_held(self):
        with self.lock:
            return self._held_locked()

    def _held_locked(self):
        if not self.active_id: return False
        if not self._beginning and self.stroke:
            worker=getattr(self.stroke,'thread',None)
            if not self.stroke.pressed or (worker is not None and not worker.is_alive()): return False
        if self.input_source=='native':
            try: return bool(self.native_is_held and self.native_is_held())
            except Exception: return False
        return self.clock()<self.deadline

    def cancel_stroke(self,expired_only=False):
        with self.lock:
            if expired_only and (not self.active_id or self._held_locked()): return
            stroke=self.stroke
            needs_end=bool(self.active_id or self._beginning or (stroke and stroke.pressed))
            self.active_id=None
            self.input_source=None
            self._begin_tickets.clear();self._job_bindings.clear()
            if not needs_end or not stroke or self._ending: return
            self._ending=True
        # An in-flight begin can hold the delivered stroke lock. Invalidate the
        # lease immediately, and do not hold up independent STOP/ESTOP.
        # A replacement stroke must wait until this old asynchronous end returns.
        def finish():
            try: stroke.end('输入结束或租约过期')
            finally:
                with self.lock: self._ending=False
        threading.Thread(target=finish,daemon=True).start()

    def command(self,body):
        if not isinstance(body,dict): raise ValueError('无效命令')
        action=body.get('action'); rid=body.get('request_id')
        if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',rid): raise ValueError('无效请求编号')
        if action in ('stroke-begin','stroke-move','stroke-hold','stroke-end'):
            return self._stroke_command(body)
        actions={'stop':0x10,'estop':0x14,'zero':0x11,'balance':0x12,'clear-fault':0x13}
        if set(body)!={'action','request_id'} or action not in actions: raise ValueError('不支持的命令')
        with self.dispatch_lock:
            if action in ('stop','estop','zero'): self.cancel_stroke()
            with self.lock:
                prior=self.requests.get(rid)
                if prior:
                    if prior['action']!=action: raise ValueError('请求编号冲突')
                    return dict(prior)
                owner=self.owner
                if owner is None: raise ValueError('Host 未连接')
                record=dict(request_id=rid,action=action,state='queued',session=self.session,opcode=actions[action],message='等待发送',created_at=time.time())
                self.requests[rid]=record
            try:
                # Register the ticket when the owner's queue inserts it; no
                # owner state lock is held while calling the delivered owner.
                if not hasattr(owner,'_bridge_enqueue'): owner._bridge_enqueue=threading.local()
                owner._bridge_enqueue.rid=rid
                try: owner.command(actions[action],source='WEB')
                finally: owner._bridge_enqueue.rid=None
            except Exception as exc:
                with self.lock: record.update(state='rejected',message=str(exc))
            return dict(record)

    def _begin_stroke(self, sid, seq, target, session, source, deadline):
        with self.dispatch_lock, self.lock:
            if session != self.session: raise ValueError('会话已变更')
            if self.active_id or self._beginning or self._ending or sid in self.used or len(self.used)>=10000:
                raise ValueError('已有手绘正在进行或尚未结束')
            if self.owner is None or self.stroke is None: raise ValueError('Host 未连接')
            self.used.add(sid); self.active_id=sid; self.input_source=source
            self.seq=seq; self.deadline=deadline
            self._beginning=True; stroke=self.stroke
            if not self._watchdog_started:
                self._watchdog_started=True
                threading.Thread(target=self._watchdog,daemon=True,name='DS13Lease').start()
        try:
            if not self.is_held(): raise ValueError('输入已松开或续租过期')
            stroke.begin(target)
            with self.lock:
                valid=(self.active_id==sid and self.session==session and self._held_locked())
            if not valid:
                stroke.end('笔画已取消或续租过期')
                raise ValueError('笔画已取消或续租过期')
        except Exception as exc:
            with self.lock:
                if self.active_id==sid: self.active_id=None; self.input_source=None
            raise ValueError(str(exc)) from exc
        finally:
            with self.lock: self._beginning=False

    def native_begin(self, target):
        with self.lock: session=self.session
        self._begin_stroke('native-'+uuid.uuid4().hex, 1, target, session, 'native', 0)

    def native_move(self, target):
        with self.dispatch_lock:
            with self.lock:
                stroke=self.stroke if self.input_source=='native' and self._held_locked() else None
            if stroke: stroke.move(target)

    def native_end(self, reason):
        with self.dispatch_lock:
            with self.lock:
                if self.input_source!='native': return
                stroke=self.stroke; self.active_id=None; self.input_source=None
                self._begin_tickets.clear(); self._job_bindings.clear()
            if stroke: stroke.end(reason)

    def _stroke_command(self,body):
        action=body['action']; required={'action','request_id','session','stroke_id','seq'}
        if action in ('stroke-begin','stroke-move'): required.add('target')
        if 'issued_ms' in body: required.add('issued_ms')
        if set(body)!=required: raise ValueError('笔画字段无效')
        sid=body['stroke_id']; seq=body['seq']
        if not isinstance(sid,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',sid) or type(seq)!=int or seq<0: raise ValueError('笔画编号无效')
        if 'target' in body and (type(body['target'])!=int or abs(body['target'])>192): raise ValueError('目标须在 ±192 计数内')
        deadline=self.clock()+self.LEASE
        if 'issued_ms' in body:
            stamp=body['issued_ms']
            if type(stamp)!=int or not 0<stamp<10**16: raise ValueError('输入时间无效')
            age=self.wall_clock()-stamp/1000
            if action!='stroke-end':
                if age < -.5 or age>=self.LEASE: raise StrokeRejected('手绘输入已过期','stroke_input_expired')
                deadline=self.clock()+min(self.LEASE,self.LEASE-max(0,age))
        if action == 'stroke-begin':
            self._begin_stroke(sid,seq,body['target'],body['session'],'web',deadline)
            return dict(request_id=body['request_id'],action=action,state='accepted',session=body['session'],stroke_id=sid,message='输入已接收；设备动作状态见遥测')
        with self.dispatch_lock:
            with self.lock:
                if body['session']!=self.session: raise ValueError('会话已变更')
                # Release can overtake a slow begin. Record it so that the old
                # begin can never start motion after the pointer was released.
                if action=='stroke-end' and self.active_id!=sid and sid not in self.used:
                    if len(self.used)<10000: self.used.add(sid)
                    return dict(request_id=body['request_id'],action=action,state='accepted',session=self.session,stroke_id=sid,message='笔画已结束')
                if self.active_id!=sid:
                    if sid not in self.used: raise StrokeRejected('笔画尚未开始','stroke_not_started')
                    raise StrokeRejected('笔画已结束或续租过期','stroke_ended')
                if not self._held_locked():
                    raise StrokeRejected('笔画已结束或续租过期','stroke_ended')
                if self.input_source!='web': raise ValueError('当前由 Host 手绘控制')
                if seq<=self.seq: raise StrokeRejected('旧笔画序号','stroke_outdated')
                self.seq=seq; self.deadline=deadline; stroke=self.stroke
                if action=='stroke-end':
                    self.active_id=None;self.input_source=None;self._begin_tickets.clear();self._job_bindings.clear()
            if action=='stroke-move': stroke.move(body['target'])
            elif action=='stroke-end': stroke.end('浏览器结束笔画')
            return dict(request_id=body['request_id'],action=action,state='accepted',session=self.session,stroke_id=sid,message='输入已接收；设备动作状态见遥测')

    def status(self):
        while True:
            with self.lock: owner=self.owner; stroke=self.stroke
            s=owner.state.snapshot(include_history=False) if owner else {}
            with self.lock:
                if owner is not self.owner or stroke is not self.stroke: continue
                s.pop('history',None); reads=s.pop('config_reads',{})
                now=self.clock(); connected=bool(s.get('connected'))
                self._expire_requests_locked(time.time())
                if not connected:
                    for rec in self.requests.values():
                        if rec['state'] in ('queued','pending'): rec.update(state='indeterminate',message='连接中断，结果未知；不重发')
                s['direction_readback']=reads.get((253,6))
                s['stroke']=dict(active=self._held_locked(),source=self.input_source,stroke_id=self.active_id,target=getattr(stroke,'target',None),sent_points=getattr(stroke,'sent_points',0),message=getattr(stroke,'message',''),error=getattr(stroke,'error',''))
                return json_safe(dict(protocol='FD2',variant=5,user_code='44533133',schema='44535002',wire_version=5,session=self.session,connected=connected,synced=bool(connected and s.get('authenticated') and not s.get('error')),status_age_s=max(0,time.monotonic()-owner.last_rx) if owner and owner.last_rx else None,control_age_s=s.get('control_age_seconds'),diagnostic_age_s=max(0,time.monotonic()-self.last_diagnostic_at) if self.last_diagnostic_at else None,host=s,requests=self.requests,transport=dict(usb_connected=connected,ble_connected=False),replay=dict(active=False,finished=False,label=''),read_only=False))

class NativeStrokeProxy:
    """Give the delivered window access to the same mutually exclusive stroke."""
    def __init__(self,bridge,is_held):
        self.bridge=bridge
        with bridge.lock: bridge.native_is_held=is_held
    def _native_stroke(self):
        with self.bridge.lock:
            return self.bridge.stroke if self.bridge.input_source=='native' else None
    @property
    def pressed(self): return bool(getattr(self._native_stroke(),'pressed',False))
    @property
    def job(self): return getattr(self._native_stroke(),'job',None)
    @property
    def error(self): return getattr(self._native_stroke(),'error','')
    def begin(self,target): return self.bridge.native_begin(target)
    def move(self,target): return self.bridge.native_move(target)
    def end(self,reason='松开鼠标，平滑停止并保持平衡'): return self.bridge.native_end(reason)

def install(bridge,modules=None):
    m=modules or load_upstream(); cls=m['client'].SerialOwner; app=m['app'].App
    if not hasattr(cls,'_ds13_bridge_originals'):
        cls._ds13_bridge_originals=(cls._receive,cls._send)
    if not hasattr(app,'_ds13_bridge_originals'):
        app._ds13_bridge_originals=(app.connect,app.disconnect,app.close,app.__init__)
    original_receive,original_send=cls._ds13_bridge_originals
    original_connect,original_disconnect,original_close,original_init=app._ds13_bridge_originals
    def init(window,*args,**kwargs):
        original_init(window,*args,**kwargs)
        window.editor.controller=NativeStrokeProxy(bridge,window.editor.controller.is_held)
    def receive(owner,raw,item):
        pending=owner.pending.get(item.get('request_id')) if item.get('kind')=='ack' else None
        matched=bool(pending and pending[0]==item.get('opcode') and item.get('identity_ok'))
        original_receive(owner,raw,item)
        with bridge.lock:
            if owner is not bridge.owner: return
            if matched and item.get('opcode')==m['protocol'].LIVE_BEGIN and pending[3] is not None:
                binding=bridge._begin_tickets.pop(pending[3],None)
                if binding and item.get('status')==0:
                    bridge._job_bindings[item['request_id']]=binding
            if matched and pending[3] is not None:
                rid=bridge.queue_requests.pop(pending[3],None)
                rec=bridge.requests.get(rid)
                if rec: rec.update(state='accepted' if item['status']==0 else 'rejected',ack_status=item['status'],message='设备 ACK；动作完成情况另看状态')
        with owner.state.lock:
            accepted=owner.state.authenticated and ((item.get('kind')=='telemetry' and owner.state.last_control is item) or (item.get('kind')=='diagnostic' and owner.state.diagnostic is item))
        if accepted and len(raw)>4 and raw[4]==5:
            with bridge.dispatch_lock:
                if owner is not bridge.owner: return
                if item['kind']=='diagnostic': bridge.last_diagnostic_at=time.monotonic()
                frame=dict(item,wire_version=raw[4])
                if item['kind']=='telemetry' and len(raw)==256 and m['protocol'].crc16(raw[:-2])==struct.unpack_from('<H',raw,254)[0]:
                    # DS13 T transmits captured Q4 at byte 52; expose it only
                    # from the same CRC-checked coherent control transaction.
                    frame['quiet_q4']=struct.unpack_from('<H',raw,52)[0]
                bridge.capture(frame)
    def send(owner,opcode,group=0,payload=b'',ticket=None,source='heartbeat'):
        with bridge.lock:
            if source in ('MOUSE_PRESS', 'LIVE_POINTER') and (
                    owner is not bridge.owner or not bridge._held_locked() or
                    (source=='MOUSE_PRESS' and bridge._begin_tickets.get(ticket)!=(bridge.session,bridge.active_id)) or
                    (source=='LIVE_POINTER' and (len(payload)!=8 or
                     bridge._job_bindings.get(struct.unpack_from('<I',payload)[0])!=(bridge.session,bridge.active_id)))):
                if ticket is not None:
                    with owner.state.lock: owner.state.command_results[ticket]=dict(status=1,cancelled=True)
                return None
            rec=bridge.requests.get(bridge.queue_requests.get(ticket))
            if rec and owner is bridge.owner: rec.update(state='pending',wire_request_id=owner.next_id,written_at=time.time(),message='已发送，等待设备 ACK')
            return original_send(owner,opcode,group,payload,ticket,source)
    def connect(window):
        previous=window.owner
        original_connect(window)
        if window.owner is not None and window.owner is not previous and not window.owner.stop_event.is_set():
            bridge.attach_owner(window.owner)
    def detach():
        bridge.cancel_stroke()
        with bridge.dispatch_lock, bridge.lock:
            bridge.new_session(); bridge.owner=None
    def disconnect(window):
        detach(); return original_disconnect(window)
    def close(window):
        detach(); return original_close(window)
    cls._receive=receive; cls._send=send; app.__init__=init; app.connect=connect; app.disconnect=disconnect; app.close=close
    return m
