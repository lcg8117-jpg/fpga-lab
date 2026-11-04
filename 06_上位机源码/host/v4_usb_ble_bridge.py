"""Local HTTP adapter around the unmodified V4 USB/BLE session owner."""
from pathlib import Path
import importlib
import queue
import sys
import threading
import time

from host.v4_r28_bridge import V4R28Bridge


ROOT = Path(__file__).resolve().parent / 'v4_usb_ble' / 'PC_Bluetooth'
FIRMWARE = 0x5634523A
SCHEMA = 0x56345208
_modules = None


def load_upstream():
    """Isolate the delivery's generic module names from other Host versions."""
    global _modules
    if _modules is None:
        names = ('protocol', 'session_log', 'link_diagnostics', 'client', 'dual_client')
        saved = {name: sys.modules.pop(name, None) for name in names}
        sys.path.insert(0, str(ROOT))
        try:
            _modules = {name: importlib.import_module(name) for name in names}
        finally:
            sys.path.remove(str(ROOT))
            for name in names:
                sys.modules.pop(name, None)
                if saved[name] is not None:
                    sys.modules[name] = saved[name]
    return _modules


class V4UsbBleBridge(V4R28Bridge):
    def __init__(self, *, capacity=64000):
        super().__init__(capacity=capacity)
        self.identity_ack = False
        self.identity_d = False
        self.identity_error = ''
        self._async_writes = {}
        self.last_position_request = None

    def upstream(self):
        modules = load_upstream()
        return modules['dual_client'], modules['protocol']

    def attach_owner(self, owner):
        with self.dispatch_lock, self.lock:
            if owner is self.owner:
                return
            self.identity_ack = False
            self.identity_d = False
            self.identity_error = ''
            self._async_writes.clear()
            self.last_position_request = None
            super().attach_owner(owner)
            put0 = owner.queue.put_nowait
            get0 = owner.queue.get_nowait

            def put(entry):
                requeued = None
                with self.lock:
                    active = self._active if owner is self.owner else None
                    if (threading.current_thread() is owner.thread and active and
                            active[1:] == (entry[2], entry[3], entry[4])):
                        requeued = active[0]
                        self._queue_ids[entry[1]] = requeued
                        self._active = None
                try:
                    put0(entry)
                except Exception:
                    if requeued:
                        with self.lock:
                            self._queue_ids.pop(entry[1], None)
                    raise

            owner.queue.put_nowait = put

            def get():
                try:
                    return get0()
                except queue.Empty:
                    with self.lock:
                        active = self._active if owner is self.owner else None
                        if threading.current_thread() is owner.thread and active:
                            record = self.requests.get(active[0])
                            if record and record['state'] == 'queued':
                                record.update(state='rejected', message='发送前资格检查未通过')
                            self._active = None
                    raise

            owner.queue.get_nowait = get

    def capture_owner(self, owner, item):
        with self.lock:
            if not (self.identity_ack and self.identity_d and not self.identity_error):
                return
        super().capture_owner(owner, item)

    def _status_locked(self, owner, state):
        routes = state.get('routes') or {}
        for retired in state.get('retired_actions') or []:
            if not str(retired.get('reason', '')).startswith('secondary_'):
                continue
            rid = self.wire_requests.get(retired.get('request_id'))
            record = self.requests.get(rid)
            if record and record['state'] in ('queued', 'pending'):
                record.update(state='indeterminate', message='蓝牙位置确认异常，结果未知；未换路重发')
        for wire, route in list(self._async_writes.items()):
            rid = self.wire_requests.get(wire)
            record = self.requests.get(rid)
            if not record or record['state'] != 'queued':
                self._async_writes.pop(wire, None)
                continue
            sent = owner.physical_sent_times.get((wire, route)) if owner else None
            if sent is not None:
                elapsed = max(0, time.monotonic() - sent)
                record.update(state='pending', written_at=time.time()-elapsed, message='已实际写出，等待设备 ACK')
                self._async_writes.pop(wire, None)
            elif (state.get('error') or state.get('log_error') or not (routes.get(route) or {}).get('connected') or
                  (owner is not None and wire not in owner.pending)):
                record.update(state='indeterminate', message='传输中断，发送结果未知；不重发')
                self._async_writes.pop(wire, None)
        result = super()._status_locked(owner, state)
        host = result['host']
        routes = host.get('routes') or {}
        def ready(name):
            route = routes.get(name) or {}
            last_rx = route.get('last_rx')
            return bool(route.get('connected') and route.get('ready') and
                        isinstance(last_rx, (int, float)) and 0 <= time.monotonic()-last_rx <= .5)
        result.update(profile='V4_USB_BLE', user_code='5634523A', schema='56345208',
                      synced=bool(result['synced'] and self.identity_ack and self.identity_d and not self.identity_error),
                      transport=dict(usb_connected=bool((routes.get('usb') or {}).get('connected')),
                                     ble_connected=bool((routes.get('ble') or {}).get('connected')),
                                     usb_ready=ready('usb'),
                                     ble_ready=ready('ble'),
                                     ble_quarantined=bool((routes.get('ble') or {}).get('quarantined')),
                                     position_route=host.get('position_route')))
        host['link_diagnostics'] = routes
        host['last_position_request'] = self.last_position_request if result['synced'] else None
        host['bridge_identity_verified'] = bool(self.identity_ack and self.identity_d and not self.identity_error)
        if self.identity_error:
            host['error'] = self.identity_error
        elif owner and state.get('connected') and not host['bridge_identity_verified']:
            host['identity_warning'] = '网页等待匹配 3A 回包与当前会话诊断帧'
        return result

    def command(self, body):
        with self.dispatch_lock:
            if not isinstance(body, dict):
                raise ValueError('命令字段无效')
            body = dict(body)
            expected_session = body.pop('session', None)
            with self.lock:
                if body.get('action') not in ('stop', 'estop'):
                    if not isinstance(expected_session, str) or not expected_session or expected_session != self.session:
                        raise ValueError('网页请求所属会话已变化，请重新确认状态')
                    if not (self.identity_ack and self.identity_d and not self.identity_error):
                        raise ValueError('网页尚未核验 3A 固件与当前会话诊断')
            return super().command(body)


def install(bridge, modules=None):
    modules = modules or load_upstream()
    p = modules['protocol']
    cls = modules['dual_client'].DualOwner
    if not hasattr(cls, '_bridge_originals'):
        cls._bridge_originals = (cls.__init__, cls._receive_route, cls._send)
    init0, receive0, send0 = cls._bridge_originals

    def init(owner, *args, **kwargs):
        init0(owner, *args, **kwargs)
        bridge.attach_owner(owner)

    def receive(owner, route, raw, item):
        pending = owner.pending.get(item.get('request_id')) if item['kind'] == 'ack' else None
        matched = bool(pending and pending[0] == item.get('opcode') and
                       route in owner.pending_routes.get(item.get('request_id'), set()))
        receive0(owner, route, raw, item)
        if owner is not bridge.owner:
            return
        if item['kind'] == 'ack' and matched:
            with bridge.dispatch_lock, bridge.lock:
                if owner is not bridge.owner:
                    return
                if item.get('firmware_id') != FIRMWARE:
                    bridge.identity_error = '网页收到非 5634523A 固件回包，请确认版本'
                elif route == owner.primary and item['opcode'] in p.READS and item['status'] == 0:
                    bridge.identity_ack = True
                if (item.get('firmware_id') == FIRMWARE and bridge.identity_ack and bridge.identity_d and
                        not bridge.identity_error and item['opcode'] == p.POSITION and item['status'] == 0 and
                        len(pending[2]) == 1 and pending[2][0] in range(1, 7) and
                        (bridge.last_position_request is None or item['request_id'] > bridge.last_position_request['wire_request_id'])):
                    bridge.last_position_request = dict(value=pending[2][0], wire_request_id=item['request_id'],
                                                        route=route, accepted_at=time.time())
                rid = bridge.wire_requests.get(item['request_id'])
                if rid in bridge.requests and bridge.wire_opcodes.get(item['request_id']) == item['opcode']:
                    bridge._async_writes.pop(item['request_id'], None)
                    if item.get('firmware_id') != FIRMWARE:
                        bridge.requests[rid].update(state='indeterminate', message='回包固件不匹配，动作结果未知')
                    else:
                        bridge.requests[rid].update(state='accepted' if item['status'] == 0 else 'rejected',
                            ack_status=item['status'], message='设备 ACK；动作完成情况另看状态')
        elif item['kind'] in ('compact', 'diagnostic'):
            link = owner.links.get(route) or {}
            if item['kind'] == 'diagnostic' and link.get('ready') and link.get('last_d') is item:
                with bridge.dispatch_lock, bridge.lock:
                    if owner is bridge.owner:
                        if (item.get('firmware_id') == FIRMWARE and item.get('schema') == SCHEMA and
                                item.get('session') == owner.session):
                            if route == owner.primary:
                                bridge.identity_d = True
                        else:
                            bridge.identity_error = '网页收到不匹配的固件、schema 或会话诊断'
            bridge.capture_owner(owner, item)

    def send(owner, opcode, group=0, payload=b'', route=None):
        with bridge.lock:
            active = bridge._active if owner is bridge.owner else None
            if owner is bridge.owner:
                bridge._active = None
            rid = active[0] if active and active[1:] == (opcode, group, payload) else None
            if active and rid is None and active[0] in bridge.requests:
                bridge.requests[active[0]].update(state='rejected', message='发送前资格检查未通过')
            if rid and opcode not in (p.STOP, p.ESTOP):
                record = bridge.requests.get(rid)
                if (not record or record.get('session') != bridge.session or
                        not bridge.identity_ack or not bridge.identity_d or bridge.identity_error):
                    if record:
                        record.update(state='rejected', message='发送前会话或固件身份已失效')
                    raise RuntimeError('发送前会话或固件身份已失效')
        try:
            name = route or owner._choose_route(opcode)
            wire = send0(owner, opcode, group, payload, route)
        except Exception:
            if rid:
                with bridge.lock:
                    if owner is bridge.owner:
                        bridge.requests[rid].update(state='indeterminate', message='发送结果未知；不重发')
            raise
        with bridge.lock:
            if owner is bridge.owner:
                if opcode in (p.STOP, p.ESTOP):
                    for retired in owner.state.retired_actions:
                        old = bridge.wire_requests.get(retired['request_id'])
                        if old in bridge.requests and bridge.requests[old]['state'] in ('queued','pending'):
                            bridge.requests[old].update(state='indeterminate', message='STOP 前的动作结果未知；不重发')
                if rid:
                    bridge.wire_requests[wire] = rid
                    bridge.wire_opcodes[wire] = opcode
                    connection = (owner.links.get(name) or {}).get('connection')
                    if getattr(connection, 'async_write', False):
                        bridge._async_writes[wire] = name
                        bridge.requests[rid].update(state='queued', wire_request_id=wire,
                            message='已提交传输队列，等待实际写出')
                    else:
                        bridge.requests[rid].update(state='pending', wire_request_id=wire,
                            written_at=time.time(), message='已实际写出，等待设备 ACK')
        return wire

    cls.__init__, cls._receive_route, cls._send = init, receive, send
