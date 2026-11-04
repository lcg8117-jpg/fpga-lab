"""ADB1 telemetry and explicit commands through the delivered serial owner."""
import re
import time
import sys
from pathlib import Path

from host.fd2_bridge import Bridge, json_safe
from host.adb1_fastp.host import protocol

def load_upstream():
    vendor = str(Path(__file__).resolve().parent / 'adb1_fastp' / 'vendor')
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    from host.adb1_fastp.host import protocol, client, app
    protocol.DEFAULT_GAINS = (53.78, 0., 210.14, 17.67, 0., 616.61)
    return dict(protocol=protocol, client=client, app=app)


class ADB1FastPBridge(Bridge):
    protocol = protocol
    epoch_stride = 5
    schema = '41445001'
    delivery_profile = None

    def __init__(self):
        super().__init__(1)
        self.simulated = True
        self.last_diagnostic_at = 0
        self.last_extension_at = 0

    def attach_owner(self, owner, *, simulated):
        if hasattr(owner, 'commands'):
            with owner.lock:
                queue = owner.commands
                if not hasattr(queue, '_adb_bridge_original_get'):
                    queue._adb_bridge_original_get = queue.get_nowait
                    def tracked_get():
                        entry = queue._adb_bridge_original_get()
                        self.on_dequeued(owner)
                        owner._adb_dequeued = entry[1]
                        return entry
                    queue.get_nowait = tracked_get
        with self.dispatch_lock, self.lock:
            self.new_session()
            self.owner = owner
            self.simulated = simulated
            self.last_diagnostic_at = 0
            self.last_extension_at = 0

    def capture_owner(self, owner, frame):
        with self.dispatch_lock, self.lock:
            if owner is not self.owner or owner.identity_latched_error or not self.protocol.identity_matches(frame):
                return
            if frame.get('kind') not in ('telemetry', 'diagnostic', 'extension'):
                return
            if frame['kind'] == 'diagnostic':
                self.last_diagnostic_at = time.monotonic()
            elif frame['kind'] == 'extension':
                self.last_extension_at = time.monotonic()
            self.capture(frame)

    def capture(self, frame, *, received_at=None):
        kind = frame.get('kind')
        if kind not in ('telemetry', 'diagnostic', 'extension') or (kind == 'telemetry' and not frame.get('coherent')):
            return
        with self.dispatch_lock, self.lock:
            prev = self._last.get(kind)
            if prev and ((frame['board_ms'] - prev['board_ms']) & 0xffffffff) >= 0x80000000:
                self.new_session(); prev = None
            if (prev and kind == 'telemetry' and frame['control_epoch'] == 1
                and ((frame['control_epoch'] - prev['control_epoch']) & 0xffffffff) != self.epoch_stride
                and 0 < ((frame['sequence'] - prev['sequence']) & 0xffffffff) < 0x80000000):
                self.new_session(); prev = None
            field = 'control_epoch' if kind == 'telemetry' else 'sequence'
            if prev and frame[field] == prev[field]:
                return
            gap = bool(prev and ((frame['sequence'] - prev['sequence']) & 0xffffffff) != 1)
            if prev and kind == 'telemetry':
                gap = gap or ((frame['control_epoch'] - prev['control_epoch']) & 0xffffffff) != self.epoch_stride or ((frame['board_ms'] - prev['board_ms']) & 0xffffffff) != 5
            self.cursor += 1
            self.rows.append(dict(cursor=self.cursor, received_at=time.time() if received_at is None else received_at, frame=json_safe(frame), gap_before=gap))
            self._last[kind] = dict(frame)

    def status(self):
        with self.lock:
            owner = self.owner
        state = owner.snapshot() if owner else {}
        now = time.monotonic()
        with self.lock:
            if owner is not self.owner:
                state, owner = {}, None
            def age(stamp):
                return max(0, now-stamp) if stamp else None
            simulated = self.simulated
            connected = bool(owner and state.get('connected') and not simulated)
            state.pop('history', None)
            state.pop('events', None)
            state.pop('config_reads', None)
            state.pop('config_written', None)
            state['log_error'] = (state.get('logging') or {}).get('error', '')
            self._expire_requests_locked(time.time())
            if not state.get('connected'):
                for request in self.requests.values():
                    if request['state'] in ('queued', 'pending'):
                        request.update(state='indeterminate', message='连接中断，结果未知；不重发')
            return json_safe(dict(protocol='FD2', variant=16, user_code='41444231',
                schema=self.schema, wire_version=3, session=self.session, delivery_profile=self.delivery_profile,
                connected=connected, synced=bool(connected and state.get('authenticated')),
                status_age_s=age(owner.last_frame) if owner else None,
                control_age_s=age(owner.last_control) if owner else None,
                diagnostic_age_s=age(self.last_diagnostic_at), extension_age_s=age(self.last_extension_at), host=state, requests=self.requests,
                transport=dict(usb_connected=connected, ble_connected=False),
                replay=dict(active=simulated, finished=False, simulation=simulated, label='离线模拟' if simulated else ''),
                read_only=False))

    def command(self, body):
        protocol = self.protocol
        actions={'stop':protocol.STOP, 'estop':protocol.ESTOP, 'zero':protocol.ZERO,
                 'balance':protocol.RUN, 'clear-fault':protocol.CLEAR,
                 'start-swing':protocol.START_SWING, 'adapt-enable':protocol.ADAPT_ENABLE,
                 'adapt-disable':protocol.ADAPT_DISABLE, 'set-gains':protocol.SET_GAINS, 'read-gains':protocol.READ_GAINS}
        if not isinstance(body, dict) or set(body) not in ({'action','request_id'}, {'action','request_id','value'}):
            raise ValueError('命令字段无效')
        rid, action=body['request_id'], body['action']
        if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',rid) or action not in actions:
            raise ValueError('请求 ID 或操作无效')
        payload = b''
        if action == 'set-gains':
            values = body.get('value')
            if not isinstance(values, list) or len(values) != 6 or any(type(v) not in (int, float) for v in values):
                raise ValueError('必须提供六个增益')
            payload = protocol.gains_payload(values)
        elif 'value' in body:
            raise ValueError('此操作不接受增益')
        fingerprint = (action, payload)
        with self.lock:
            if self.simulated: raise ValueError('离线模拟禁止硬件命令')
            prior=self.requests.get(rid)
            if prior:
                if self.fingerprints.get(rid) != fingerprint: raise ValueError('请求 ID 内容冲突')
                return dict(prior)
            owner=self.owner
        if owner is None: raise ValueError('Host 未连接')
        # Owner lock serializes queue insertion with its worker, STOP drains, and
        # original Host commands; never acquire the owner while holding bridge.lock.
        with owner.lock:
            with self.lock:
                if owner is not self.owner or self.simulated:
                    raise ValueError('连接已变更，请重新操作')
                prior=self.requests.get(rid)
                if prior:
                    if self.fingerprints.get(rid) != fingerprint: raise ValueError('请求 ID 内容冲突')
                    return dict(prior)
                record=dict(request_id=rid,action=action,state='queued',session=self.session,
                            opcode=actions[action],message='已排队，等待设备 ACK',created_at=time.time())
                self.requests[rid]=record
                self.fingerprints[rid] = fingerprint
                try:
                    owner.set_gains(values) if action == 'set-gains' else owner.command(actions[action], group=255 if action == 'read-gains' else 0, payload=payload)
                    self.queue_requests[owner.order]=rid
                except Exception as exc:
                    record.update(state='rejected',message=str(exc))
                return dict(record)

    def on_dequeued(self, owner):
        with self.lock:
            if owner is not self.owner: return
            rid=self.queue_requests.pop(getattr(owner,'_adb_dequeued',None),None)
            if rid in self.requests:
                self.requests[rid].update(state='rejected',message='在发送前被停止或资格保护取消')
            owner._adb_dequeued=None

    def on_event(self, owner, event, fields):
        with self.lock:
            if owner is not self.owner: return
            if event == 'cancelled_before_send':
                rid=self.queue_requests.pop(getattr(owner,'_adb_dequeued',None),None)
                owner._adb_dequeued=None
                if rid in self.requests:
                    self.requests[rid].update(state='rejected',message='在发送前被停止或资格保护取消')
            if event == 'ack_timeout_no_replay':
                rid=self.wire_requests.get(fields.get('request_id'))
                if rid in self.requests:
                    self.requests[rid].update(state='indeterminate',message='ACK 超时，结果未知；不重发')

    def on_send(self, owner, opcode, wire_id):
        with self.lock:
            if owner is not self.owner: return
            queue_id=getattr(owner,'_adb_dequeued',None)
            rid=self.queue_requests.pop(queue_id,None)
            owner._adb_dequeued=None
            if rid in self.requests:
                if self.requests[rid]['opcode'] != opcode:
                    self.requests[rid].update(state='rejected',message='在发送前被保护操作取消')
                    return
                self.wire_requests[wire_id]=rid
                self.wire_opcodes[wire_id]=opcode
                self.requests[rid].update(state='pending',wire_request_id=wire_id,written_at=time.time(),message='已发送，等待设备 ACK')

    def on_ack(self, owner, item, matched):
        with self.lock:
            if owner is not self.owner or not matched or not self.protocol.identity_matches(item): return
            wire_id=item['request_id']; rid=self.wire_requests.get(wire_id)
            if rid in self.requests and self.wire_opcodes.get(wire_id)==item['opcode']:
                self.requests[rid].update(state='accepted' if item['status']==0 else 'rejected',
                    ack_status=item['status'],message='设备 ACK；动作完成情况另看状态')


def install(bridge, modules=None):
    modules = modules or load_upstream()
    protocol = modules['protocol']
    SerialOwner = modules["client"].SerialOwner
    Window = modules["app"].Window
    original_receive = SerialOwner._receive
    original_send = SerialOwner._send
    original_event = SerialOwner._event
    original_connect = Window._connect
    def receive(owner, raw, item):
        # ADB1 restarts control_epoch at 1 for a new control run while its
        # board clock and telemetry sequence continue. The delivered owner
        # otherwise rejects every new transaction until it reaches the old epoch.
        previous=owner.state.get('latest')
        if (item.get('kind')=='telemetry' and item.get('coherent') and
            item.get('control_epoch')==1 and owner.history and
            ((item['control_epoch'] - owner.history[-1].get('control_epoch',0)) & 0xffffffff) != bridge.epoch_stride and previous and
            owner.state.get('authenticated') and not owner.identity_latched_error and
            protocol.identity_matches(item)):
            seq=(item['sequence']-previous['sequence']) & 0xffffffff
            elapsed=(item['board_ms']-previous['board_ms']) & 0xffffffff
            if 0 < seq < 0x80000000 and 0 < elapsed < 0x80000000:
                old_epoch=owner.history[-1]['control_epoch']
                owner.history.clear()
                owner._event('control_epoch_restart', previous_epoch=old_epoch,
                             epoch=1, board_ms=item['board_ms'])
        pending=owner.pending.get(item.get('request_id')) if item.get('kind')=='ack' else None
        matched=bool(pending and pending[0]==item.get('opcode'))
        original_receive(owner, raw, item)
        if item.get('kind')=='ack': bridge.on_ack(owner,item,matched)
        accepted=(item.get('kind')=='telemetry' and bool(owner.history) and owner.history[-1] is item) or (
            item.get('kind') in ('diagnostic', 'extension') and owner.state.get(item['kind']) is item)
        if accepted: bridge.capture_owner(owner, item)

    def send(owner, opcode, group=0, payload=b'', *, heartbeat=False):
        wire_id=owner.next_id
        bridge.on_send(owner,opcode,wire_id)
        try:
            return original_send(owner,opcode,group,payload,heartbeat=heartbeat)
        except Exception:
            bridge.on_event(owner,'ack_timeout_no_replay',{'request_id':wire_id})
            raise

    def event(owner, name, **fields):
        original_event(owner,name,**fields)
        bridge.on_event(owner,name,fields)

    def connect(window, port):
        original_connect(window, port)
        bridge.attach_owner(window.owner, simulated=not bool(port))

    SerialOwner._receive = receive
    SerialOwner._send = send
    SerialOwner._event = event
    Window._connect = connect
