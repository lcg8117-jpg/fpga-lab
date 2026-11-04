"""A held mouse button owns one live stroke; only the newest pointer is sent."""
import struct,threading,time
from protocol import LIVE_BEGIN,LIVE_POINT,LIVE_END,CANCEL_HOLD,STATUS_TEXT

class LiveStroke:
    def __init__(self,get_owner,is_held=None):
        self.get_owner=get_owner;self.is_held=is_held;self.lock=threading.RLock();self.pressed=False
        self.owner=None;self.job=None;self.target=0;self.thread=None;self.sign=1
        self.control_generation=0
        self.message='按住左图上下画，实物实时跟随';self.error='';self.sent_points=0
    def begin(self,target):
        owner=self.get_owner()
        if owner is None:raise RuntimeError('先连接并启动平衡')
        s=owner.state.snapshot(include_history=False);c=s['last_control']
        if not s['control_fresh'] or c['mode']!=3 or c['fault']:raise RuntimeError('请先启动平衡')
        direction=s['config_reads'].get((253,6))
        if direction not in (0,1):raise RuntimeError('等待板端方向设置读回')
        with self.lock:
            if self.pressed or (self.thread and self.thread.is_alive()):raise RuntimeError('上一笔正在结束')
            self.owner=owner;self.job=None;self.target=max(-192,min(192,int(target)));self.sign=1 if direction else -1
            self.control_generation=s['control_generation']
            self.error='';self.sent_points=0;self.pressed=True;self.message='正在开始实时跟随'
            try:ticket=owner.command(LIVE_BEGIN,source='MOUSE_PRESS',expected_generation=self.control_generation)
            except Exception:self.pressed=False;raise
            self.thread=threading.Thread(target=self._run,args=(owner,ticket),daemon=True,name='LivePointer')
            self.thread.start()
    def move(self,target):
        with self.lock:self.target=max(-192,min(192,int(target)))
    def _finish_locked(self,why):
        if not self.pressed:return
        self.pressed=False;self.message=why
        try:
            if self.owner and self.owner.state.control_generation==self.control_generation:
                if self.job:self.owner.command(LIVE_END,payload=struct.pack('<I',self.job),source=why)
                else:self.owner.command(CANCEL_HOLD,source=why)
        except Exception as exc:
            if not self.error:self.error=str(exc)
    def end(self,why='松开鼠标，平滑停止并保持平衡'):
        with self.lock:self._finish_locked(why);worker=self.thread
        # Let an already-cancelled worker exit before the next mouse press.
        if worker and worker is not threading.current_thread():worker.join(timeout=.05)
    def _wait(self,owner,ticket):
        # The serial owner already applies V5's deadline from the actual write.
        # Queue residence and unrelated heartbeat ACKs have no extra deadline.
        while True:
            with self.lock:
                if not self.pressed:return None
            if self.is_held is not None and not self.is_held():self.end();return None
            with owner.state.lock:
                ack=owner.state.command_results.pop(ticket,None)
                connected=owner.state.connected;error=owner.state.error
                generation=owner.state.control_generation
            if error:raise RuntimeError(error)
            if generation!=self.control_generation:raise RuntimeError('控制状态已改变，本笔结束')
            if ack is not None:
                if ack.get('cancelled'):return None
                if ack['status']!=0:
                    raise RuntimeError(STATUS_TEXT.get(ack['status'],'指令未被接受'))
                return ack
            if not connected:raise RuntimeError('连接已断开')
            time.sleep(.003)
    def _run(self,owner,ticket):
        try:
            ack=self._wait(owner,ticket)
            with self.lock:
                if ack is None or not self.pressed:return
                self.job=ack['request_id'];self.message='实时跟随中 · 松开停止'
            outstanding=set();next_point=time.perf_counter()
            while True:
                if self.is_held is not None and not self.is_held():
                    self.end();return
                s=owner.state.snapshot(include_history=False);c=s['last_control']
                if s['error']:raise RuntimeError(s['error'])
                if s['control_generation']!=self.control_generation or not s['control_fresh'] or c['mode']!=3 or c['fault']:raise RuntimeError('跟随已停止')
                with owner.state.lock:
                    replies=[(t,owner.state.command_results.pop(t)) for t in tuple(outstanding) if t in owner.state.command_results]
                for ticket,reply in replies:
                    outstanding.remove(ticket)
                    if reply.get('cancelled'):return
                    if reply['status']!=0:raise RuntimeError(STATUS_TEXT.get(reply['status'],'指令未被接受'))
                    self.sent_points+=1
                with self.lock:
                    if not self.pressed:return
                    now=time.perf_counter()
                    if now>=next_point:
                        ticket=owner.command(LIVE_POINT,payload=struct.pack('<Ii',self.job,self.target*self.sign),source='LIVE_POINTER',expected_generation=self.control_generation)
                        outstanding.add(ticket);next_point=now+.02
                # Every point is a new current pointer sample, never a retry.
                # ACK failure/actual-write timeout remains owned by SerialOwner.
                time.sleep(.003)
        except Exception as exc:
            with self.lock:
                self.error=str(exc);self._finish_locked('结束实时跟随');self.message=self.error
