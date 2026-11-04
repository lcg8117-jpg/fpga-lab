"""CRC-checked remote feedback, continuous intent and exclusive configuration ACKs."""

from collections import deque
from dataclasses import dataclass
import re
import struct
import time

IDENTITY = "OK STATUS RC83"
CAPABILITIES = "CAPS 83 90 500 200"
MAGIC = b'\xa8\x8a'
FEEDBACK_SECONDS = 1.2


def crc16(data):
    crc = 0xffff
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xffff
    return crc


def encode_frames(row, sequence=0):
    """Offline model encoder; integration checks this against compiled C."""
    tag = sequence & 63
    flags = (row['active'] | row['braking'] << 1 | row['profile'] << 2 |
             row['boost_active'] << 3 | row['stop_reason'] << 4 | int(is_parked(row)) << 7)
    offset = row['servo_compare'] - 500
    main = MAGIC + bytes([tag | ((offset >> 8) << 7)]) + struct.pack('<IhhhhBBB', row['ms'], row['left_pwm8400'],
        row['right_pwm8400'], row['requested_q8'], row['ramped_q8'], offset & 255,
        flags, row['route_id'] << 4 | row['phase_id'])
    aux = MAGIC + bytes([tag | 64]) + (row['ms'] & 0xffffff).to_bytes(3, 'little') + struct.pack(
        '<iihh', row['left_count'], row['right_count'], row['left_delta5'], row['right_delta5'])
    return [p + struct.pack('<H', crc16(p)) for p in (main, aux)]
FIELDS = ("ms", "left_count", "right_count", "left_delta5", "right_delta5",
          "requested_q8", "ramped_q8", "left_pwm8400", "right_pwm8400",
          "servo_compare", "active", "braking", "stop_reason", "profile",
          "boost_active", "route_id", "phase_id")
REASONS = {0: "正常 / 用户停止", 1: "控制租约到期", 2: "车轮堵转保护",
           3: "编码器方向异常", 4: "编码器跳变", 5: "控制周期异常",
           6: "轨迹完成 / 时限到达", 7: "通信 / 指令异常"}


class ProtocolError(RuntimeError):
    """Malformed or unexpected device input cannot authorize movement."""


class CommandCancelled(RuntimeError):
    """Fresh release or emergency intent preempts a reply wait."""


def parse_record(line):
    if not line.startswith("CAR8,"):
        return None
    parts = line.split(",")[1:]
    if len(parts) != len(FIELDS) or any(not re.fullmatch(r"-?\d{1,11}", p) for p in parts):
        raise ProtocolError("CAR8 状态帧字段不完整")
    row = dict(zip(FIELDS, map(int, parts)))
    ranges = [(0, 0xffffffff), (-0x80000000, 0x7fffffff), (-0x80000000, 0x7fffffff),
              (-32768, 32767), (-32768, 32767), (-6144, 6144), (-6144, 6144),
              (-7560, 7560), (-7560, 7560), (500, 1000), (0, 1), (0, 1), (0, 7), (0, 1),
              (0, 1), (0, 8), (0, 9)]
    if any(not lower <= row[key] <= upper for key, (lower, upper) in zip(FIELDS, ranges)):
        raise ProtocolError("CAR8 状态帧数值超出约定")
    drive_keys = ("requested_q8", "ramped_q8", "left_pwm8400", "right_pwm8400")
    if row["braking"] and (row["active"] or any(row[k] for k in drive_keys)):
        raise ProtocolError("制动状态与驱动输出矛盾")
    if not row["active"] and any(row[k] for k in drive_keys):
        raise ProtocolError("停车状态仍报告驱动输出")
    if not row["active"] and any(row[k] for k in ("boost_active", "route_id", "phase_id")):
        raise ProtocolError("停车状态仍报告轨迹或急加速")
    if row["route_id"] and not row["phase_id"] or not row["route_id"] and row["phase_id"]:
        raise ProtocolError("轨迹和阶段编号矛盾")
    if row["boost_active"] and (not row["profile"] or not row["active"] or row["route_id"] not in (0, 6, 7)):
        raise ProtocolError("急加速状态超出许可条件")
    if row["boost_active"] and (row["requested_q8"] <= 0 or abs(row["servo_compare"] - 750) > 5):
        raise ProtocolError("急加速状态不在正向直行条件内")
    if not row["boost_active"] and abs(row["requested_q8"]) > 4096:
        raise ProtocolError("当前目标超过持续速度上限")
    if not row['boost_active'] and any(abs(row[k]) > 6720 for k in ('left_pwm8400', 'right_pwm8400')):
        raise ProtocolError('非冲刺状态超过快速档功率上限')
    if not row["profile"] and (abs(row["requested_q8"]) > 1536 or
                               abs(row["ramped_q8"]) > 1536 or
                               abs(row["left_pwm8400"]) > 4200 or
                               abs(row["right_pwm8400"]) > 4200):
        raise ProtocolError("普通档状态超过普通档限值")
    return row


def is_parked(row):
    return bool(row is not None and not row["active"] and not row["braking"] and
                row.get('parked_feedback', True) and
                not any(row[k] for k in ("requested_q8", "ramped_q8", "left_pwm8400",
                                          "right_pwm8400")) and
                abs(row["left_delta5"]) <= 1 and abs(row["right_delta5"]) <= 1)


def command_ack(command):
    exact = {"STATUS": IDENTITY, "CAPS": CAPABILITIES, "STOP": "OK STOP",
             "BRAKE": "OK BRAKE", "ARM": "OK ARM", "C1": "OK CAL 1", "C0": "OK CAL 0"}
    if command in exact:
        return exact[command]
    match = re.fullmatch(r"PERF ([01])", command)
    if match:
        return "OK PERF " + match[1]
    match = re.fullmatch(r"S (\d{3,4})", command)
    if match and 500 <= int(match[1]) <= 1000:
        return "OK S " + match[1]
    match = re.fullmatch(r"D (\d{1,5}) (-?\d{1,4}) (\d{3,4})", command)
    if (match and int(match[1]) <= 65535 and 128 <= abs(int(match[2])) <= 4096
            and 500 <= int(match[3]) <= 1000):
        return "OK D " + match[1]
    match = re.fullmatch(r"T (\d{1,5}) ([1-8]) (\d{1,4}) (\d{1,4})", command)
    if (match and int(match[1]) <= 65535 and
            128 <= int(match[4]) <= int(match[3]) <= 4096):
        return "OK T " + match[1]
    match = re.fullmatch(r"U (\d{1,5}) (\d{1,4}) (\d{1,4})", command)
    if (match and int(match[1]) <= 65535 and
            128 <= int(match[3]) <= int(match[2]) <= 4096):
        return "OK U " + match[1]
    match = re.fullmatch(r"B (\d{1,5}) (\d{4})", command)
    if match and int(match[1]) <= 65535 and 1024 <= int(match[2]) <= 6144:
        return "OK B " + match[1]
    match = re.fullmatch(r"K (\d{1,5})", command)
    if match and int(match[1]) <= 65535:
        return "OK K " + match[1]
    raise ValueError("不支持或超限的 V8.3 指令")


@dataclass(frozen=True)
class Observation:
    sequence: int
    received: float
    row: dict


class Session:
    """One writer; motion never waits for ACK and is never automatically replayed."""

    def __init__(self, transport, emit=lambda text: None, clock=time.monotonic):
        self.transport, self.emit, self.clock = transport, emit, clock
        self.buffer = bytearray()
        self.synchronizing = True
        self.sync_bytes = 0
        self.pending = None
        self.acknowledged = False
        self.last = None
        self.records = deque(maxlen=64)
        self.record_sequence = 0
        self.identified = self.ready = self.closed = False
        self.stopped_at = -100.0
        self.abandoned = deque(maxlen=16)
        self.last_write_at = -100.0
        self.bad_frames = 0
        self.bad_report_at = -100.0
        self.frame_tag = None
        self.device_fault_reason = None

    def _damaged(self):
        self.bad_frames += 1
        if self.clock() - self.bad_report_at >= 1:
            self.emit(f'RX_RESYNC discarded={self.bad_frames}：丢弃坏帧，蓝牙连接保留')
            self.bad_report_at = self.clock()

    def _packet(self, packet):
        kind, tag = packet[2] >> 6, packet[2] & 63
        if kind == 1:
            tick = int.from_bytes(packet[3:6], 'little')
            if self.last and self.frame_tag == tag and self.last.row['ms'] & 0xffffff == tick:
                values = struct.unpack('<iihh', packet[6:18])
                self.last.row.update(zip(('left_count','right_count','left_delta5','right_delta5'), values))
            return
        if kind not in (0, 2):
            self._damaged()
            return
        tick, left, right, requested, ramped, servo, flags, route = struct.unpack('<IhhhhBBB', packet[3:18])
        if self.last:
            advance = (tick - self.last.row['ms']) & 0xffffffff
            if not advance:
                return
            if advance >= 0x80000000:
                self._damaged()
                return
        row = dict.fromkeys(FIELDS, 0)
        if self.last:
            row['left_count'], row['right_count'] = self.last.row['left_count'], self.last.row['right_count']
        row.update(ms=tick, left_pwm8400=left, right_pwm8400=right, requested_q8=requested,
                   ramped_q8=ramped, servo_compare=servo+500+((packet[2] >> 7) << 8), active=flags&1, braking=flags>>1&1,
                   profile=flags>>2&1, boost_active=flags>>3&1, stop_reason=flags>>4&7,
                   route_id=route>>4, phase_id=route&15,
                   left_delta5=0 if flags&128 else 2, right_delta5=0 if flags&128 else 2)
        try:
            row = parse_record('CAR8,' + ','.join(str(row[k]) for k in FIELDS))
        except ProtocolError:
            self._damaged()
            return
        row['parked_feedback'] = bool(flags & 128)
        self.record_sequence += 1
        self.last = Observation(self.record_sequence, self.clock(), row)
        self.records.append(self.last)
        self.frame_tag = tag
        self.synchronizing = False
        if row['stop_reason'] not in (0, 6):
            self.device_fault_reason = row['stop_reason']

    def _line(self, raw):
        try:
            line = raw.decode('ascii').rstrip('\r')
        except UnicodeDecodeError:
            self._damaged()
            return
        if not line:
            return
        if line == 'CAR_REMOTE_V83':
            if self.ready:
                self.ready = self.identified = False
                raise ProtocolError('小车已重新启动，请松开后重新连接')
            return
        if (line.startswith('CAR_REMOTE_') or
                line.startswith(('CAR7', 'CAR6', 'CAR5', 'CAR4', 'CAR3', 'CAR2', 'CAR1', 'OK STATUS ', 'CAPS '))
                and line not in (IDENTITY, CAPABILITIES)):
            self.ready = self.identified = False
            raise ProtocolError('固件版本不匹配，请烧录配套V8.3固件')
        if re.fullmatch(r'FAULT [0-7]', line):
            self.device_fault_reason = int(line[-1])
            self.emit('DEVICE_FAULT ' + REASONS[self.device_fault_reason])
            return
        if line.startswith('ERR '):
            self.emit(line)
            if self.pending in ('OK STOP', 'OK BRAKE'):
                self.emit('DRAINED_BEFORE_STOP ' + line)
                return
            raise ProtocolError(line)
        if line == self.pending:
            self.acknowledged = True
            self.emit(line)
        elif any(line == expected and self.clock() <= expiry for expected, expiry in self.abandoned):
            self.abandoned = deque(((a,b) for a,b in self.abandoned if a != line and b >= self.clock()), maxlen=16)
            self.emit('LATE_ACK_DRAINED ' + line)
        elif line.startswith(('OK ', 'CAPS ')):
            raise ProtocolError('指令应答顺序或序号不匹配')
        else:
            self._damaged()

    def _feed(self, chunk):
        self.buffer.extend(chunk)
        if len(self.buffer) > 4096:
            self.buffer[:] = self.buffer[-2048:]
            self._damaged()
        while self.buffer:
            if self.buffer.startswith(MAGIC):
                if len(self.buffer) < 20:
                    break
                packet = bytes(self.buffer[:20])
                if crc16(packet[:18]) != int.from_bytes(packet[18:], 'little'):
                    del self.buffer[0]
                    self._damaged()
                    continue
                del self.buffer[:20]
                self._packet(packet)
                continue
            magic = self.buffer.find(MAGIC)
            newline = self.buffer.find(b'\n')
            if magic > 0 and (newline < 0 or magic < newline):
                del self.buffer[:magic]
                self._damaged()
            elif newline >= 0:
                raw = bytes(self.buffer[:newline])
                del self.buffer[:newline+1]
                self._line(raw)
            elif len(self.buffer) > 128:
                self.buffer[:] = self.buffer[-1:] if self.buffer[-1] == MAGIC[0] else b''
                self._damaged()
                break
            else:
                break

    def send(self, command, abort=None):
        """Send one latest intent, never waiting for or retrying a motion ACK."""
        command_ack(command)
        if command.split()[0] not in {'D','S','K','T','U','B'} or not self.ready or self.pending:
            raise ProtocolError('持续控制尚未就绪或指令不属于运动通道')
        if abort and abort():
            raise CommandCancelled('动作已松开，取消尚未发送的指令')
        ending = '\n' if command.split()[0] in {'T', 'U'} else '\r\n'
        payload = (command + ending).encode('ascii')
        if len(payload) > 20:
            raise ValueError('蓝牙指令超过20字节')
        self.emit('TX ' + command)
        self.last_write_at = self.clock()
        self.transport.write(payload)

    def stop(self, brake=False):
        floor = self.record_sequence
        try:
            self.command('BRAKE' if brake else 'STOP', 0.9)
        except TimeoutError:
            self.emit('STOP_ACK_MISSING：等待新的停车反馈，保持蓝牙连接')
        last = self.wait_parked(floor, 1.5)
        if not brake and last.row['stop_reason'] != 0:
            raise ProtocolError('停车反馈仍含故障，等待STOP清除')
        self.stopped_at = self.clock()

    def poll(self, timeout=0.01):
        if self.closed:
            raise ConnectionError("连接已关闭")
        for chunk in self.transport.read(timeout):
            self._feed(chunk)

    def command(self, command, timeout=0.9, abort=None):
        if command.split()[0] in {'D','S','K','T','U','B'}:
            raise ValueError('运动指令必须使用持续发送通道，不能等待应答')
        if self.closed or self.pending is not None:
            raise ProtocolError("连接已关闭或已有指令等待应答")
        expected = command_ack(command)
        if command not in {"STATUS", "CAPS", "STOP", "BRAKE"} and not self.ready:
            raise ProtocolError("固件身份与停车状态尚未确认")
        payload = (command + "\r\n").encode("ascii")
        if len(payload) > 20:
            raise ValueError("蓝牙单包超过 20 字节")
        self.pending, self.acknowledged = expected, False
        started = self.clock()
        completed = written = False
        try:
            if abort and abort():
                raise CommandCancelled('动作已松开，取消尚未发送的指令')
            self.emit("TX " + command)
            self.last_write_at = self.clock()
            written = True
            self.transport.write(payload)
            while self.clock() - started < timeout:
                self.poll(min(0.01, timeout))
                if abort and abort():
                    raise CommandCancelled('动作已松开，中断应答等待并停车')
                if self.acknowledged:
                    completed = True
                    self.emit(f'ACK_MS {command.split()[0]} {round((self.clock()-started)*1000)}')
                    if command == "STATUS":
                        self.identified = True
                    elif command in {"STOP", "BRAKE"}:
                        self.stopped_at = self.clock()
                    return
            raise TimeoutError("等待应答超时：" + command.split()[0])
        finally:
            if written and not completed:
                self.abandoned.append((expected, self.clock() + 2.0))
            self.pending, self.acknowledged = None, False

    def wait_parked(self, floor, timeout=1.0):
        started = self.clock()
        while self.clock() - started < timeout:
            if self.last and self.last.sequence > floor and is_parked(self.last.row):
                return self.last
            self.poll(0.01)
        raise TimeoutError("未收到新的静止状态")

    def handshake(self):
        started = self.clock()
        while self.synchronizing or self.last is None:
            if self.clock() - started > 1.5:
                raise TimeoutError("初始状态同步超时")
            self.poll(0.01)
        self.command("STATUS", 0.6)
        self.command("CAPS", 0.6)
        self.command("STOP", 0.6)
        row = self.wait_parked(self.record_sequence)
        if row.row["stop_reason"] != 0:
            raise ProtocolError("停车后的故障未清除")
        self.ready = True
        # A new session never silently inherits a previous fast selection.
        if row.row["profile"]:
            self.command("PERF 0")
            self.wait_profile(0, self.record_sequence)
        self.emit("V83_READY：身份、参数和停车反馈已确认")

    def wait_profile(self, profile, floor):
        started = self.clock()
        while self.clock() - started < 0.8:
            if self.last and self.last.sequence > floor and is_parked(self.last.row) and self.last.row["profile"] == profile:
                return
            self.poll(0.01)
        raise TimeoutError("速度档位反馈未确认")

    def reset_handshake(self):
        """Retire old authorization and framing while retaining the same live GATT link."""
        self.ready = self.identified = False
        self.buffer.clear()
        self.records.clear()
        self.record_sequence = 0
        self.last = self.frame_tag = None
        self.synchronizing = True
        self.pending = None
        self.acknowledged = False
        self.abandoned.clear()
        self.device_fault_reason = None

    def close(self, disconnect=True):
        if not self.closed:
            try:
                if disconnect:
                    self.transport.close()
                else:
                    self.transport.close(disconnect=False)
            finally:
                self.closed = True
