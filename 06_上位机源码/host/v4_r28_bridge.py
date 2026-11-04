"""R28 BLE adapter: preserve the delivered owner and its command protections."""
from pathlib import Path
import importlib
import json
import re
import sys
import threading
import time

from host.fd2_bridge import Bridge, json_safe

ROOT = Path(__file__).resolve().parent / 'v4_r28' / 'PC_Bluetooth'
_modules = None

def load_upstream():
    global _modules
    if _modules is None:
        names = ('protocol', 'session_log', 'client')
        saved = {name: sys.modules.pop(name, None) for name in names}
        sys.path.insert(0, str(ROOT))
        try:
            p = importlib.import_module('protocol')
            c = importlib.import_module('client')
            _modules = (c, p)
        finally:
            sys.path.remove(str(ROOT))
            for name in names:
                sys.modules.pop(name, None)
                if saved[name] is not None: sys.modules[name] = saved[name]
    return _modules

class V4R28Bridge(Bridge):
    def __init__(self, *, capacity=64000):
        super().__init__(4, capacity=capacity)
        self.session = ''
        self._queue_ids = {}
        self._active = None

    def upstream(self):
        return load_upstream()

    def attach_owner(self, owner):
        with self.dispatch_lock, self.lock:
            if self.owner is owner: return
            self.new_session()
            self.owner = owner
            self.session = str(owner.session)
            self._queue_ids.clear()
            self._active = None
            queue = owner.queue
            original_get = queue.get_nowait
            original_put = queue.put_nowait
            owner._bridge_enqueue = threading.local()
            def put(entry):
                rid = getattr(owner._bridge_enqueue, 'rid', None)
                if rid and entry[1] == owner.counter:
                    with self.lock:
                        if owner is self.owner: self._queue_ids[entry[1]] = rid
                try: original_put(entry)
                except Exception:
                    with self.lock:
                        if owner is self.owner: self._queue_ids.pop(entry[1], None)
                    raise
            queue.put_nowait = put
            def get():
                entry = original_get()
                with self.lock:
                    if owner is not self.owner: return entry
                    # STOP's drain requeues SAFE entries; retain their identity.
                    retained = threading.current_thread() is not owner.thread and entry[2] in self.upstream()[0].SAFE
                    rid = self._queue_ids.get(entry[1]) if retained else self._queue_ids.pop(entry[1], None)
                    if threading.current_thread() is owner.thread:
                        if self._active and self._active[0] in self.requests:
                            self.requests[self._active[0]].update(state='rejected', message='发送前资格检查未通过')
                        self._active = (rid, entry[2], entry[3], entry[4]) if rid else None
                    elif not retained and rid in self.requests:
                        self.requests[rid].update(state='rejected', message='STOP 在发送前取消请求')
                return entry
            queue.get_nowait = get

    def capture_owner(self, owner, item):
        with self.dispatch_lock, self.lock, owner.state.lock:
            if owner is not self.owner: return
            if item['kind'] == 'compact':
                if not owner.state.authenticated or owner.state.latest is not item or not item['coherent'] or not item['range_valid']: return
            elif item['kind'] == 'diagnostic':
                if not owner.state.authenticated or owner.state.diagnostic is not item or item['session'] != owner.session: return
            else: return
            previous = self._last.get(item['kind'])
            if previous and item['sequence'] == previous['sequence']: return
            gap = bool(previous and ((item['sequence']-previous['sequence']) & 0xffffffff) != 1)
            self.cursor += 1
            self.rows.append(dict(cursor=self.cursor, session=self.session, received_at=time.time(), frame=json_safe(item), gap_before=gap))
            self._last[item['kind']] = dict(item)

    def status(self):
        while True:
            owner = self.owner
            state = owner.state.snapshot() if owner else {}
            with self.lock:
                if owner is self.owner:
                    return self._status_locked(owner, state)

    def _status_locked(self, owner, state):
        now = time.monotonic()
        def age(field):
            v=state.get(field,0)
            return max(0,now-v) if v else None
        state.pop('history',None)
        # Observe the original parser and BLE buffer without reading, draining,
        # or sending anything. Valid-frame counters alone cannot distinguish
        # silence from non-FD2 bytes discarded during framing.
        parser = getattr(owner, 'parser', None)
        diagnostics = dict(parser_discarded_bytes=getattr(parser, 'discarded_bytes', None),
                           parser_buffer_bytes=len(parser.buffer) if parser is not None else None,
                           startup_rx_bytes=None, ble_buffer_bytes=None,
                           pending_write_frames=None, transport_error=None)
        connection = getattr(owner, 'port', None)
        transport = getattr(connection, 'port', None)
        lock = getattr(transport, 'lock', None)
        if lock is not None:
            with lock:
                diagnostics.update(startup_rx_bytes=getattr(transport, 'startup_bytes', None),
                    ble_buffer_bytes=len(transport.buffer) if hasattr(transport, 'buffer') else None,
                    pending_write_frames=getattr(transport, 'pending_writes', None),
                    transport_error=getattr(transport, 'error', None))
        state['link_diagnostics'] = diagnostics
        self._expire_requests_locked(time.time())
        if owner and not state.get('connected'):
            for item in self.requests.values():
                if item['state'] in ('queued','pending'): item.update(state='indeterminate',message='连接中断，结果未知；不重发')
        return json_safe(dict(protocol='FD2',profile='V4_R28',variant=4,user_code='56345239',schema='56345208',wire_version=4,layout=5,session=self.session,connected=bool(state.get('connected')),synced=bool(state.get('connected') and state.get('authenticated') and not state.get('error')),status_age_s=age('last_rx'),control_age_s=age('latest_rx'),diagnostic_age_s=age('diagnostic_rx'),host=state,requests=self.requests,transport=dict(usb_connected=False,ble_connected=bool(state.get('connected'))),replay=dict(active=False,finished=False,label='')))

    def command(self, body):
        _,p=self.upstream()
        actions={'stop':p.STOP,'estop':p.ESTOP,'zero':p.ZERO,'balance':p.RUN,'clear-fault':p.CLEAR,'position':p.POSITION,'hold':p.CANCEL_HOLD,'read-gains':p.READ_GAINS,'set-gains':p.SET_GAINS}
        if not isinstance(body,dict) or set(body)-{'request_id','action','value'}: raise ValueError('命令字段无效')
        rid,action=body.get('request_id'),body.get('action')
        if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',rid) or action not in actions: raise ValueError('请求 ID 或操作无效')
        value=body.get('value')
        if action=='position':
            if type(value) is not int or value not in range(1,7): raise ValueError('位置编号必须为1～6')
            group,payload=0,bytes([value])
        elif action=='set-gains':
            if not isinstance(value,list): raise ValueError('需要六个PID参数')
            group,payload=255,p.gains_payload(value)
        elif action=='read-gains':
            if 'value' in body: raise ValueError('不接受参数')
            group,payload=255,b''
        else:
            if 'value' in body: raise ValueError('不接受参数')
            group,payload=0,b''
        signature=dict(action=action,value=value)
        with self.dispatch_lock:
            with self.lock:
                prior=self.requests.get(rid)
                if prior:
                    if self.fingerprints[rid]!=signature: raise ValueError('请求 ID 内容冲突')
                    return dict(prior)
                owner=self.owner
                if owner is None: raise ValueError('Host 未连接')
                record=dict(request_id=rid,action=action,state='queued',session=self.session,created_at=time.time(),message='已排队，等待设备 ACK')
                self.requests[rid]=record;self.fingerprints[rid]=signature
            try:
                # Original command lock protects STOP's queue drain and insertion.
                with owner.command_lock:
                    owner._bridge_enqueue.rid=rid
                    try: owner.command(actions[action],group,payload)
                    finally: owner._bridge_enqueue.rid=None
            except Exception as exc:
                with self.lock: record.update(state='rejected',message=str(exc))
            return dict(record)

def install(bridge, client=None):
    client=client or load_upstream()[0]
    cls=client.BLEOwner
    if not hasattr(cls,'_bridge_originals'):
        cls._bridge_originals=(cls.__init__,cls._receive,cls._send)
    init0,receive0,send0=cls._bridge_originals
    def init(owner,*args,**kwargs):
        init0(owner,*args,**kwargs); bridge.attach_owner(owner)
    def receive(owner,raw,item):
        pending=owner.pending.get(item.get('request_id')) if item['kind']=='ack' else None
        matched=bool(pending and pending[0]==item.get('opcode'))
        receive0(owner,raw,item)
        if owner is not bridge.owner: return
        if item['kind']=='ack' and matched:
            with bridge.lock:
                if owner is not bridge.owner: return
                rid=bridge.wire_requests.get(item['request_id'])
                if rid in bridge.requests and bridge.wire_opcodes.get(item['request_id'])==item['opcode']:
                    bridge.requests[rid].update(state='accepted' if item['status']==0 else 'rejected',ack_status=item['status'],message='设备 ACK；动作完成情况另看状态')
        else: bridge.capture_owner(owner,item)
    def send(owner,opcode,group=0,payload=b''):
        with bridge.lock:
            active=bridge._active if owner is bridge.owner else None
            if owner is bridge.owner: bridge._active=None
            rid = active[0] if active and active[1:] == (opcode,group,payload) else None
            if active and rid is None and active[0] in bridge.requests:
                bridge.requests[active[0]].update(state='rejected',message='发送前资格检查未通过')
        try:
            wire=send0(owner,opcode,group,payload)
        except Exception:
            if rid:
                with bridge.lock:
                    if owner is bridge.owner: bridge.requests[rid].update(state='indeterminate',message='发送结果未知；不重发')
            raise
        if rid:
            with bridge.lock:
                if owner is bridge.owner:
                    bridge.wire_requests[wire]=rid;bridge.wire_opcodes[wire]=opcode
                    bridge.requests[rid].update(state='pending',wire_request_id=wire,written_at=time.time(),message='已发送，等待设备 ACK')
        return wire
    cls.__init__,cls._receive,cls._send=init,receive,send


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8371)
    args=parser.parse_args()
    # The application and its lazy transport imports require the delivered directory.
    sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'vendor_ble'))
    import client, app
    bridge=V4R28Bridge();install(bridge,client);bridge.serve(args.port)
    app.main()

if __name__=='__main__': main()
