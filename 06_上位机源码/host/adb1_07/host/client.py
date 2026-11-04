"""One transport owner; UI work and compression cannot block keepalive dispatch."""
from collections import deque
import queue
import re
import struct
import threading
import time

from . import protocol as p
from .session_log import SessionLog


def open_usb(port):
    """Open only the explicitly supplied port; importing never probes hardware."""
    if not port or not re.fullmatch(r"COM[1-9][0-9]*", port.removeprefix("\\\\.\\"), re.IGNORECASE):
        raise ValueError("请输入现场确认的USB串口名称；不接受自动发现或网络串口")
    import serial
    return serial.Serial(port=port, baudrate=2000000, timeout=0.002, write_timeout=0.02,
                         exclusive=True)


class SerialOwner:
    """All read/write/open/close calls occur on exactly one worker thread."""

    def __init__(self, factory, log_directory, *, logger_factory=SessionLog):
        self.factory = factory
        self.log_directory = log_directory
        self.logger_factory = logger_factory
        self.logger = None
        self.transport = None
        self.parser = p.FrameParser()
        self.lock = threading.RLock()
        self.commands = queue.PriorityQueue(maxsize=256)
        self.halt = threading.Event()
        self.thread = None
        self.pending = {}
        self.next_id = 1
        self.order = 0
        self.heartbeat_due = 0
        self.identity_latched_error = False
        self.logging_failure_at = None
        self.safety_stop_id = None
        self.unknown_request_ids = set()
        self.last_frame = 0
        self.last_control = 0
        self.history = deque(maxlen=2000)
        self.events = deque(maxlen=80)
        self.state = dict(connected=False, authenticated=False, identity_seen=False,
                          status_seen=False, capabilities=None, message="未连接", error="",
                          latest=None, diagnostic=None, extension=None, last_ack=None, last_action_ack=None, independent_gains=None,
                          config_reads={}, config_written={}, heartbeat_sent=0,
                          heartbeat_max_gap=0., last_heartbeat=0., received=0,
                          control_received=0, sequence_gaps=0, control_gaps=0,
                          diagnostic_gaps=0, extension_gaps=0,
                          duplicate_controls=0, timeouts=0, unknown_mutations=0,
                          logging_failure_stop="未触发",
                          crc_errors=0, format_errors=0, discarded_bytes=0)

    def start(self):
        if self.thread:
            raise RuntimeError("同一连接只能启动一个串口所有者")
        self.thread = threading.Thread(target=self._run, name="J280SerialOwner", daemon=True)
        self.thread.start()
        return self

    def snapshot(self):
        with self.lock:
            result = dict(self.state)
            result["config_reads"] = dict(self.state["config_reads"])
            result["config_written"] = dict(self.state["config_written"])
            result["history"] = list(self.history)
            result["events"] = list(self.events)
            result["control_age"] = time.monotonic() - self.last_control if self.last_control else None
            result["fresh"] = bool(self.last_frame and time.monotonic() - self.last_frame <= .5)
            result["logging"] = self.logger.metrics() if self.logger else {}
            return result

    def _event(self, event, **fields):
        self.events.append(dict(event=event, **fields))
        if self.logger:
            self.logger.submit(event, **fields)

    def command(self, opcode, group=0, payload=b""):
        payload = bytes(payload)
        p.request(opcode, 1, group, payload)  # Validate before adding intent.
        with self.lock:
            if not self.state["connected"]:
                raise RuntimeError("未连接")
            safe = opcode in p.SAFE
            if not safe and (not self.state["authenticated"] or not self.last_frame or
                             time.monotonic() - self.last_frame > .5):
                raise RuntimeError("需要身份、能力与500ms内的新鲜状态；动作没有排队")
            if not safe and self.logger and self.logger.error:
                raise RuntimeError(self.logger.error)
            if opcode in (p.STOP, p.ESTOP):
                # A stop intent invalidates queued actions that have not left the PC.
                while True:
                    try:
                        _, _, previous = self.commands.get_nowait()
                        self._event("cancelled_before_send", opcode=previous[0])
                    except queue.Empty:
                        break
            self.order += 1
            self.commands.put_nowait((0 if opcode in (p.STOP, p.ESTOP) else 1,
                                      self.order, (opcode, group, payload)))
            self._event("user_intent", opcode=opcode, group=group, payload_hex=payload.hex())

    def set_gains(self, values):
        self.command(p.SET_GAINS, 255, p.gains_payload(values))

    def read_config(self, group, field_id):
        self.command(p.READ_CONFIG, group, bytes((field_id,)))

    def write_config(self, group, field_id, value):
        from .config_fields import validate_value
        validate_value(group, field_id, value)
        self.command(p.WRITE_CONFIG, group, bytes((field_id,)) + struct.pack("<I", value))

    def close(self):
        self.halt.set()
        if self.thread:
            self.thread.join(timeout=3)
        # No shutdown action is invented. The board retains its watchdog ownership.

    def _send(self, opcode, group=0, payload=b"", *, heartbeat=False):
        if self.next_id > 0xffffffff:
            raise RuntimeError("请求ID已耗尽；禁止回绕或自动重发")
        request_id = self.next_id
        self.next_id += 1
        raw = p.request(opcode, request_id, group, payload)
        now = time.monotonic()
        self._event("tx_intent", request_id=request_id, opcode=opcode, group=group,
                    raw_hex=raw.hex())
        self.pending[request_id] = (opcode, group, payload, now)
        try:
            written = self.transport.write(raw)
            if written != len(raw):
                raise OSError(f"只写入{written}/{len(raw)}字节，结果未知，禁止重发")
        except Exception:
            if opcode not in p.READS:
                self._mark_unknown(request_id)
            raise
        self._event("tx_written", request_id=request_id, byte_count=written)
        if heartbeat:
            previous = self.state["last_heartbeat"]
            if previous:
                self.state["heartbeat_max_gap"] = max(self.state["heartbeat_max_gap"], now - previous)
            self.state["last_heartbeat"] = now
            self.state["heartbeat_sent"] += 1
        return request_id

    def _heartbeat(self):
        if self.logger and self.logger.error:
            return
        now = time.monotonic()
        if now >= self.heartbeat_due:
            self._send(p.STATUS, heartbeat=True)
            self.heartbeat_due = now + .1

    def _update_auth(self):
        self.state["authenticated"] = bool(not self.identity_latched_error and
            not (self.logger and self.logger.error) and
            self.state["identity_seen"] and self.state["status_seen"] and
            self.state["capabilities"] == p.CAPABILITIES)

    def _receive(self, raw, item):
        now = time.monotonic()
        self.state["received"] += 1
        self.logger.submit("frame", raw_hex=raw.hex(), decoded=item)
        if not p.identity_matches(item):
            self.identity_latched_error = True
            self.state["authenticated"] = False
            self.state["error"] = "固件身份/schema不匹配；请明确断开检查，没有重放动作"
            return
        self.last_frame = now
        kind = item["kind"]
        if kind != "ack":
            self.state["identity_seen"] = True
        if kind == "ack":
            pending = self.pending.pop(item["request_id"], None)
            if not pending or pending[0] != item["opcode"]:
                self._event("unmatched_ack", request_id=item["request_id"], opcode=item["opcode"])
                return
            opcode, group, payload, _ = pending
            self.next_id = max(self.next_id, item["mutation_highwater"] + 1)
            self.state["last_ack"] = item
            if item['request_id'] == self.safety_stop_id:
                self.state['logging_failure_stop'] = f"保护STOP ACK：{p.STATUS_TEXT.get(item['status'], item['status'])}；已停止保活并关闭连接"
            # Keep the result of the user's control action visible. Routine
            # heartbeat/read replies must not erase a rejection every120ms.
            if opcode not in p.READS:
                self.state['last_action_ack'] = item
            if opcode not in p.READS or self.state['last_action_ack'] is None:
                self.state["message"] = f"{p.STATUS_TEXT.get(item['status'], '未知状态')} · 0x{opcode:02X} · ID {item['request_id']}"
            if item["status"] == 0:
                if opcode == p.STATUS:
                    self.state["status_seen"] = True
                    if self.state["capabilities"] is None and not any(v[:3] == (p.READ_CONFIG, 254, b"\x08") for v in self.pending.values()):
                        self._send(p.READ_CONFIG, 254, b"\x08")
                if opcode == p.READ_GAINS:
                    self.state["independent_gains"] = (item["stored_gains"], item["stored_revision"])
                if opcode == p.READ_CONFIG:
                    key = (item["config_group"], item["config_id"])
                    if key != (group, payload[0]):
                        self._event("readback_mismatch", request_id=item["request_id"])
                        return
                    self.state["config_reads"][key] = (item["config_read_value"], item["config_revision"])
                    if key == (254, 8):
                        self.state["capabilities"] = item["config_read_value"]
                if opcode == p.SET_GAINS:
                    self.state["independent_gains"] = None
                    self._send(p.READ_GAINS, 255)
                if opcode == p.WRITE_CONFIG:
                    key = (group, payload[0])
                    self.state["config_written"][key] = item["config_revision"]
                    self.state["config_reads"].pop(key, None)
                    self._send(p.READ_CONFIG, group, payload[:1])
        elif kind == "diagnostic":
            self._sideband_sequence(item, self.state["diagnostic"], "diagnostic_gaps")
            self.state["diagnostic"] = item
        elif kind == "extension":
            self._sideband_sequence(item, self.state["extension"], "extension_gaps")
            self.state["extension"] = item
        elif kind == "telemetry":
            previous = self.state["latest"]
            if previous:
                distance = (item["sequence"] - previous["sequence"]) & 0xffffffff
                if 1 < distance < 0x80000000:
                    self.state["sequence_gaps"] += distance - 1
                    self._event("packet_gap", previous=previous["sequence"], current=item["sequence"], missing=distance - 1)
                board_distance = (item["board_ms"] - previous["board_ms"]) & 0xffffffff
                if board_distance >= 0x80000000:
                    self.identity_latched_error = True
                    self.state["error"] = "检测到板端时刻回退；控制锁定，没有自动恢复或重放"
            self.state["latest"] = item
            if item["coherent"]:
                prior = self.history[-1] if self.history else None
                if prior:
                    distance = (item["control_epoch"] - prior["control_epoch"]) & 0xffffffff
                    if not 0 < distance < 0x80000000:
                        self.state["duplicate_controls"] += 1
                        self._event("control_order_error", epoch=item["control_epoch"], previous=prior["control_epoch"])
                        self._update_auth()
                        return
                    if distance > 1:
                        self.state["control_gaps"] += distance - 1
                        self._event("control_gap", previous=prior["control_epoch"], current=item["control_epoch"], missing=distance - 1)
                self.history.append(item)
                self.last_control = now
                self.state["control_received"] += 1
        self._update_auth()

    def _sideband_sequence(self, current, previous, metric):
        if previous:
            distance = (current['sequence'] - previous['sequence']) & 0xffffffff
            if 1 < distance < 0x80000000:
                self.state[metric] += distance - 1
                self._event("sideband_gap", kind=current['kind'], missing=distance - 1,
                            previous=previous['sequence'], current=current['sequence'])

    def _mark_unknown(self, request_id):
        self.unknown_request_ids.add(request_id)
        self.state['unknown_mutations'] = len(self.unknown_request_ids)

    def _logging_failed(self):
        """Try one qualified STOP, then close; never sustain an unknown STOP."""
        if not self.logger.error:
            return False
        now = time.monotonic()
        if self.logging_failure_at is None:
            self.logging_failure_at = now
            qualified = (not self.identity_latched_error and self.state['identity_seen'] and
                         self.state['status_seen'] and self.state['capabilities'] == p.CAPABILITIES and
                         self.last_frame and now - self.last_frame <= .5)
            self.state['authenticated'] = False
            self.state['error'] = self.logger.error
            while True:
                try:
                    self.commands.get_nowait()
                except queue.Empty:
                    break
            if not qualified:
                self.state['logging_failure_stop'] = 'ID/身份/新鲜状态未获资格，未拼装STOP；已停止保活并关闭连接'
                return True
            self.state['logging_failure_stop'] = '已生成新ID，单次保护STOP；停止保活，仅等待最多250ms ACK'
            self.safety_stop_id = self._send(p.STOP)
        if self.safety_stop_id not in self.pending:
            return True
        if now - self.logging_failure_at >= .25:
            self.state['logging_failure_stop'] = '保护STOP结果未知；不重试，已停止保活并关闭连接'
            return True
        return False

    def _run(self):
        try:
            self.logger = self.logger_factory(self.log_directory).start()
            self.transport = self.factory()
            self.logger.submit("transport", simulated=bool(getattr(self.transport, "simulated", False)),
                               baudrate=2000000, owner_thread=threading.get_ident())
            with self.lock:
                self.state["connected"] = True
                self.state["message"] = "连接已打开，仅查询身份/能力；操作需明确点击"
            while not self.halt.is_set():
                with self.lock:
                    if self._logging_failed():
                        break
                    self._heartbeat()
                raw = self.transport.read(4096)
                if raw:
                    self.logger.submit("rx_chunk", raw_hex=raw.hex())
                    old = (self.parser.crc_errors, self.parser.format_errors, self.parser.discarded_bytes)
                    frames = self.parser.feed(raw)
                    for wire, item in frames:
                        with self.lock:
                            self._heartbeat()
                            self._receive(wire, item)
                    new = (self.parser.crc_errors, self.parser.format_errors, self.parser.discarded_bytes)
                    if new != old:
                        self._event("parser_error", crc_errors=new[0], format_errors=new[1], discarded_bytes=new[2])
                    with self.lock:
                        self.state.update(crc_errors=new[0], format_errors=new[1], discarded_bytes=new[2])
                with self.lock:
                    self._heartbeat()
                    for request_id, (opcode, group, payload, sent) in list(self.pending.items()):
                        if time.monotonic() - sent > .5:
                            del self.pending[request_id]
                            self.state["timeouts"] += 1
                            if opcode not in p.READS:
                                self._mark_unknown(request_id)
                            self._event("ack_timeout_no_replay", request_id=request_id, opcode=opcode)
                    if self.last_frame and time.monotonic() - self.last_frame > .5:
                        self.identity_latched_error = True
                        self.state["authenticated"] = False
                        self.state["error"] = "超过500ms没有有效新帧；请明确重连，控制锁定且不重放动作"
                    try:
                        _, _, (opcode, group, payload) = self.commands.get_nowait()
                    except queue.Empty:
                        continue
                    if opcode not in p.SAFE and (not self.state["authenticated"] or self.logger.error):
                        self._event("cancelled_before_send", opcode=opcode, reason="qualification_lost")
                    else:
                        self._send(opcode, group, payload)
        except Exception as exc:
            with self.lock:
                self.state["error"] = str(exc)
                self._event("owner_failed", reason=str(exc))
        finally:
            if self.transport:
                self.transport.close()
            if self.logger:
                for request_id, (opcode, _, _, _) in self.pending.items():
                    if opcode not in p.READS:
                        self._mark_unknown(request_id)
                        self._event("disconnect_unknown_no_replay", request_id=request_id, opcode=opcode)
                self.logger.submit("session_end", control_received=self.state["control_received"])
                self.logger.close()
            with self.lock:
                self.state["connected"] = False
                self.state["authenticated"] = False
