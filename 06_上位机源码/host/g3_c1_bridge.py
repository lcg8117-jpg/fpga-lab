"""G3 C1 bridge. Hardware access stays exclusively with the delivered SerialOwner."""
from collections import deque
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import builtins, importlib, json, re, sys, threading, time
from host.fd2_bridge import Bridge, json_safe

ROOT = Path(__file__).resolve().parent / 'g3_c1' / 'upstream' / 'host'
_MODULES = None

def load_upstream():
    global _MODULES
    if _MODULES is not None: return _MODULES
    names=('protocol','session_log','gain_average','trajectory_settings','upload_readback','draw_trajectory','client','measured_sequence','sequential_glyph','display_origin','gowin','trace_log','hardware_mutex')
    saved={n:sys.modules.pop(n,None) for n in names}
    sys.path.insert(0,str(ROOT))
    try:
        _MODULES={n:importlib.import_module(n) for n in names}
        original_import=builtins.__import__
        def isolated_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level==0 and name in _MODULES: return _MODULES[name]
            return original_import(name,globals,locals,fromlist,level)
        for module in _MODULES.values():
            module.__dict__['__builtins__']=dict(vars(builtins),__import__=isolated_import)
    finally:
        # hardware_mutex is lazily imported by the owner.
        for n in names:
            if saved[n] is None: sys.modules.pop(n,None)
            else: sys.modules[n]=saved[n]
    return _MODULES

class G3Bridge(Bridge):
    def __init__(self, *, capacity=64000):
        super().__init__(5,capacity=capacity)
        self.state=load_upstream()['client'].HostState(5)
        self.controller=None; self.points=deque(maxlen=capacity); self.point_cursor=0
        self.play_request=None; self._polling=False

    def attach_owner(self, owner):
        with self.dispatch_lock:
            if self.controller: self.controller.close()
            self.attach(owner)
            with self.lock:
                self.controller=load_upstream()['gowin'].GowinController(owner)
                self.points.clear(); self.play_request=None
            if not self._polling:
                self._polling=True
                threading.Thread(target=self._poll,name='G3Display',daemon=True).start()

    def detach(self):
        with self.dispatch_lock:
            if self.controller: self.controller.close()
            self.new_session()
            with self.lock: self.owner=None; self.controller=None; self.points.clear()

    def _poll(self):
        while self._polling:
            time.sleep(.05)
            with self.dispatch_lock:
                controller=self.controller
                if not controller: continue
                try:
                    for point in controller.poll(): self.add_mapping(point)
                    with self.lock:
                        rec=self.requests.get(self.play_request)
                        if rec and rec['state'] in ('queued','pending'):
                            if controller.ack:
                                rec.update(state='accepted',message='PLAY 设备 ACK；实际到位另看状态')
                            elif not controller.busy:
                                rec.update(state='indeterminate',message=controller.status+'；结果未知，不自动重发')
                except Exception as exc:
                    controller.close()
                    with self.lock:
                        rec=self.requests.get(self.play_request)
                        if rec and rec['state'] in ('queued','pending'): rec.update(state='indeterminate',message=str(exc))

    def add_mapping(self,point):
        with self.lock:
            self.point_cursor+=1
            self.points.append(dict(cursor=self.point_cursor,point=json_safe(point)))

    def gowin(self,after=0,stream=None,limit=512):
        if after<0 or not 1<=limit<=512: raise ValueError('invalid cursor or limit')
        with self.lock:
            head=self.points[0]['cursor'] if self.points else self.point_cursor
            overrun=bool((stream and stream!=self.session) or after<head-1 or after>self.point_cursor)
            selected=[p for p in self.points if overrun or p['cursor']>after][:limit]
            return dict(stream=self.session,cursor=selected[-1]['cursor'] if selected else self.point_cursor,head=head,tail=self.point_cursor,overrun=overrun,samples=selected,meaning='GOWIN 映射展示，非二维实物轨迹')

    def status(self):
        now=time.monotonic(); snapshot=self.state.snapshot()
        for key in ('history','word_history','compact_history','goal_history','config_reads','config_saved','read_config_requests','command_results'): snapshot.pop(key,None)
        with self.lock:
            self._expire_requests_locked(time.time())
            connected=bool(snapshot.get('connected'))
            if not connected:
                for rec in self.requests.values():
                    if rec['state'] in ('queued','pending'): rec.update(state='indeterminate',message='连接中断，结果未知；不重发')
            def age(key):
                t=snapshot.get(key,0); return max(0,now-t) if t else None
            c=self.controller
            snapshot['gowin']=dict(busy=bool(c and c.busy),status=c.status if c else '未连接',ack=c.ack if c else None,relative_ms=c.latest_relative_ms if c else 0,manifest=c.pipeline.trace.manifest if c and c.pipeline else None,mapping=c.pipeline.mapper.metadata if c and c.pipeline else None,meaning='GOWIN 映射展示，非二维实物轨迹')
            return json_safe(dict(protocol='FD2',profile='G3_C1',variant=5,user_code='56354733',schema='46443405',wire_version=5,session=self.session,connected=connected,synced=bool(connected and snapshot.get('authenticated') and not snapshot.get('error')),status_age_s=age('latest_rx'),control_age_s=age('last_control_rx'),diagnostic_age_s=age('last_diagnostic_rx'),host=snapshot,requests=self.requests,transport=dict(usb_connected=connected,ble_connected=False),replay=dict(active=False,finished=False,label='')))

    def command(self,body):
        if not isinstance(body,dict) or set(body)!={'request_id','action'}: raise ValueError('无效命令字段')
        rid=body['request_id']; action=body['action']
        if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',rid): raise ValueError('无效请求编号')
        actions={'stop':0x10,'zero':0x11,'balance':0x12,'clear-fault':0x13,'estop':0x14,'cancel-c1':0x47}
        if action not in (*actions,'play-c1'): raise ValueError('不支持的命令')
        with self.dispatch_lock:
            with self.lock:
                prior=self.requests.get(rid)
                if prior:
                    if prior['action']!=action: raise ValueError('请求编号冲突')
                    return dict(prior)
                owner=self.owner; controller=self.controller
                if not owner or not controller: raise ValueError('Host 未连接')
                rec=dict(request_id=rid,action=action,state='queued',session=self.session,created_at=time.time(),message='等待处理')
                self.requests[rid]=rec
            try:
                if action=='play-c1':
                    if not controller.play(): rec.update(state='rejected',message=controller.status)
                    else:
                        with self.lock: self.points.clear()
                        self.play_request=rid
                        rec.update(state='pending',message='正在校验、必要时上传并播放')
                else:
                    if action in ('stop','estop','cancel-c1'): controller.cancel.set()
                    owner.command(actions[action],source='WEB/'+rid)
            except Exception as exc: rec.update(state='rejected',message=str(exc))
            return dict(rec)

    def trace(self,event,fields):
        super().trace(event,fields)
        with self.lock:
            rid=self.wire_requests.get(fields.get('request_id')) or self.queue_requests.get(fields.get('queue_id'))
            rec=self.requests.get(rid)
            if rec and event=='command_unknown':
                rec.update(state='indeterminate',message='设备应答超时，结果未知；不重发')
            elif rec and event in ('queue_cancelled','host_rejected'):
                rec.update(state='rejected',message=fields.get('reason','发送前取消'))

    def serve(self,port=8371):
        bridge=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def reply(self,code,data):
                raw=json.dumps(json_safe(data),ensure_ascii=False).encode('utf8'); self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
            def local(self): return self.headers.get('Origin') is None and self.headers.get('X-J280-Local')=='1' and self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}'
            def do_GET(self):
                if not self.local(): return self.reply(403,{'error':'local bridge clients only'})
                try:
                    url=urlparse(self.path); q=parse_qs(url.query)
                    if url.path=='/status': return self.reply(200,bridge.status())
                    if url.path in ('/waveform','/gowin'):
                        fn=bridge.waveform if url.path=='/waveform' else bridge.gowin
                        return self.reply(200,fn(int(q.get('after',['0'])[0]),q.get('stream',[None])[0],int(q.get('limit',['512'])[0])))
                    if url.path.startswith('/request/'):
                        with bridge.lock: item=bridge.requests.get(url.path.split('/')[-1]); item=dict(item) if item else None
                        return self.reply(200 if item else 404,item or {'error':'request not found'})
                    return self.reply(404,{'error':'not found'})
                except (ValueError,TypeError) as exc: return self.reply(400,{'error':str(exc)})
            def do_POST(self):
                if not self.local(): return self.reply(403,{'error':'local bridge clients only'})
                try:
                    if self.path!='/command': return self.reply(404,{'error':'not found'})
                    length=int(self.headers.get('Content-Length','0'))
                    if not 1<=length<=8192: raise ValueError('invalid Content-Length')
                    return self.reply(200,bridge.command(json.loads(self.rfile.read(length))))
                except (ValueError,TypeError,KeyError) as exc: return self.reply(400,{'error':str(exc)})
        self.server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
        threading.Thread(target=self.server.serve_forever,name='G3HTTP',daemon=True).start()
        return self.server

def install(bridge,modules=None):
    m=modules or load_upstream(); cls=m['client'].SerialOwner
    original_receive=cls._receive; original_trace=cls.trace
    def receive(owner,raw,item):
        original_receive(owner,raw,item)
        with owner.state.lock:
            accepted=owner.state.authenticated and ((item.get('kind')=='telemetry' and owner.state.last_control is item) or (item.get('kind')=='diagnostic' and owner.state.latest_diagnostic is item))
            resets=owner.state.board_resets
        with bridge.dispatch_lock:
            if owner is not bridge.owner: return
            if resets!=bridge._reset_count:
                if bridge.controller: bridge.controller.close()
                bridge.new_session(); bridge._reset_count=resets
                with bridge.lock: bridge.points.clear()
            if accepted: bridge.capture(item)
    def trace(owner,event,**fields):
        original_trace(owner,event,**fields)
        if owner is bridge.owner: bridge.trace(event,fields)
    cls._receive=receive; cls.trace=trace
    return m


def start_window(bridge):
    """Start an initially disconnected local window with explicit COM controls."""
    import tkinter as tk
    from tkinter import ttk, messagebox
    modules=install(bridge)
    root=tk.Tk(); root.title('G3 C1 串口连接'); root.geometry('480x180')
    port=tk.StringVar(); status=tk.StringVar(value='未连接；请输入串口并点击连接')
    frame=ttk.Frame(root,padding=12); frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='串口（例如 COM3）').pack(anchor='w')
    ttk.Entry(frame,textvariable=port).pack(fill='x',pady=4)
    controls=ttk.Frame(frame); controls.pack(fill='x')
    def disconnect():
        owner=bridge.owner
        if owner:
            bridge.detach()
            owner.stop()
        status.set('已断开')
    def connect():
        if bridge.owner:
            messagebox.showerror('连接','请先断开当前串口'); return
        name=port.get().strip()
        if not name:
            messagebox.showerror('连接','请输入串口'); return
        try:
            owner=modules['client'].SerialOwner(5,name,ROOT.parent.parent/'logs',logger_factory=modules['trace_log'].TraceLogger)
            bridge.attach_owner(owner)
            owner.start()
            status.set('已请求连接，等待 G3 身份验证')
        except Exception as exc:
            bridge.detach(); messagebox.showerror('连接失败',str(exc))
    ttk.Button(controls,text='连接',command=connect).pack(side='left',padx=(0,8))
    ttk.Button(controls,text='断开',command=disconnect).pack(side='left')
    ttk.Label(frame,textvariable=status,wraplength=450).pack(anchor='w',pady=8)
    def refresh():
        if bridge.owner:
            snapshot=bridge.state.snapshot()
            status.set(snapshot.get('error') or snapshot.get('message') or ('身份已验证' if snapshot.get('authenticated') else '等待连接'))
        root.after(500,refresh)
    def close():
        disconnect(); root.destroy()
    root.protocol('WM_DELETE_WINDOW',close); root.after(500,refresh); root.mainloop()
