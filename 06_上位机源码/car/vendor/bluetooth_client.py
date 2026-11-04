"""Explicit Bluetooth transports and an isolated offline car simulator."""
import asyncio
from collections import deque
import re
import time

from wireless_client import FIELDS, STEERING, MotorFaultCandidate, is_motor_fault, parse_record

BLE100_SERVICE_UUID = "0003cdd0-0000-1000-8000-00805f9b0131"
ACKS = {"STATUS": "OK STATUS BLUETOOTH", "STOP": "OK STOP", "ARM": "OK ARM",
        "FWD": "OK ACTION", "REV": "OK ACTION", "PING": "OK PING",
        **{key: "OK STEER" for key in STEERING},
        **{direction + steering: "OK ACTION" for direction in ("FWD", "REV")
           for steering in STEERING},
        **{"KEEP" + steering: "OK KEEP" for steering in STEERING}}
HEADER = "CAR2 fields: " + ",".join(FIELDS)
STARTUP_BANNER = "CAR_BLUETOOTH_V1"
HOLD_IDENTITY = "OK STATUS BLUETOOTH_HOLD"
COMBINED_IDENTITY = "OK STATUS BLUETOOTH_HOLD_V2"
STATUS_IDENTITIES = {ACKS["STATUS"], HOLD_IDENTITY, COMBINED_IDENTITY}
COMBINED_COMMANDS = {direction + steering for direction in ("FWD", "REV", "KEEP")
                     for steering in STEERING}


def _command_replies(command):
    """Limit teardown draining to responses of commands actually sent."""
    replies = {ACKS[command]}
    if command == "PING":
        replies.add("ERR NOT_ACTIVE")
    elif command in STEERING:
        replies.add("ERR FAULT_STOP_REQUIRED")
    elif command.startswith("KEEP"):
        replies.update(("ERR NOT_ACTIVE", "ERR FAULT_STOP_REQUIRED"))
    elif command == "ARM" or command.startswith(("FWD", "REV")):
        replies.update(("ERR BUSY", "ERR COOLDOWN", "ERR FAULT_STOP_REQUIRED"))
        if command != "ARM":
            replies.add("ERR NOT_ARMED")
    return replies


class _AsciiCar:
    """Keep one bounded stream parser across notifications and commands."""

    def _init_stream(self, emit):
        self.emit = emit
        self.buffer = bytearray()
        self.records = deque(maxlen=64)
        self.last_record = None
        self.identified = False
        self.hold_supported = False
        self.combined_supported = False
        self.closed = False
        self._pending_replies = deque()
        self._closing = False
        self.motion_episode = 0
        self._record_sequence = 0
        self._episode_record_floor = 0
        self._fault_evidence = None
        self._fault_history = deque(maxlen=16)
        self._episode_open = False

    def _new_motion_episode(self):
        self.motion_episode += 1
        self._episode_record_floor = self._record_sequence
        self._fault_evidence = None
        self._fault_history.clear()
        self._episode_open = True

    def _current_motor_fault(self, candidate):
        evidence = self._fault_evidence
        if (candidate is None or not self._episode_open or
                candidate.episode != self.motion_episode or evidence is None):
            return None
        episode, sequence, received, row = evidence
        if (episode != candidate.episode or sequence <= self._episode_record_floor or
                not 0 <= time.monotonic() - received <= 1.0 or not is_motor_fault(row)):
            return None
        if candidate.fault_row is not None:
            # The controller may have popped an earlier row from this same poll.
            if not any(item[0] == episode and item[1] > self._episode_record_floor and
                       0 <= time.monotonic() - item[2] <= 1.0 and
                       item[3] == candidate.fault_row and is_motor_fault(item[3])
                       for item in self._fault_history):
                return None
        return row.copy()

    def _observe_reply(self, line):
        if self._pending_replies and line in self._pending_replies[0]:
            self._pending_replies.popleft()
            return True
        if self._closing and line != "OK STOP":
            raise RuntimeError("Unexpected STOP cleanup acknowledgement")
        if self._closing and self._pending_replies:
            raise RuntimeError("STOP acknowledgement preceded outstanding command replies")
        return False

    def _consume(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > 4096:
            raise RuntimeError("Bluetooth receive buffer overflow")
        lines = []
        while b"\n" in self.buffer:
            raw, _, rest = self.buffer.partition(b"\n")
            self.buffer[:] = rest
            try:
                line = raw.decode("ascii").strip()
            except UnicodeDecodeError as error:
                raise RuntimeError("Non-ASCII Bluetooth data") from error
            if not line:
                continue
            self.emit(line)
            record = parse_record(line)
            if record is not None:
                self.records.append(record)
                self.last_record = record.copy()
                self._record_sequence += 1
                if self._episode_open and not record["active"] and record["stop_reason"]:
                    self._fault_evidence = (self.motion_episode, self._record_sequence,
                                            time.monotonic(), record.copy())
                    self._fault_history.append(self._fault_evidence)
            elif line.startswith("ERR "):
                consumed = self._observe_reply(line)
                if not self._closing or not consumed:
                    if consumed and not self._closing and line in {
                            "ERR NOT_ACTIVE", "ERR FAULT_STOP_REQUIRED"}:
                        candidate = MotorFaultCandidate(line, episode=self.motion_episode)
                        candidate.fault_row = self._current_motor_fault(candidate)
                        raise candidate
                    raise RuntimeError(line)
            elif (line not in set(ACKS.values()) | {HEADER, STARTUP_BANNER} and
                  not line.startswith("OK STATUS ") and
                  not re.fullmatch(r"UART[135],\d+,\d+,\d+,\d+", line)):
                raise RuntimeError("Unexpected Bluetooth payload: " + line[:80])
            elif line in set(ACKS.values()) | STATUS_IDENTITIES or (self._closing and line.startswith("OK ")):
                self._observe_reply(line)
            lines.append(line)
        return lines

    def command(self, cmd, expected, seconds=0.3):
        if self.closed:
            raise ConnectionError("Bluetooth connection is closed")
        if cmd not in ACKS or expected != ACKS[cmd]:
            raise ValueError("Unsupported Bluetooth command/acknowledgement")
        if cmd not in {"STATUS", "STOP"} and not self.identified:
            raise RuntimeError("Identify the Bluetooth firmware before control")
        if cmd in COMBINED_COMMANDS and not self.combined_supported:
            raise RuntimeError("Combined-control V2 firmware is required")
        if not 0 < seconds <= 5:
            raise ValueError("Acknowledgement timeout must be within 0..5 seconds")
        v2_control = self.combined_supported and (cmd == "ARM" or cmd in COMBINED_COMMANDS)
        if v2_control and seconds == 0.3:
            seconds = 0.4
        deadline = time.monotonic() + seconds if v2_control else None
        if cmd == "ARM" or cmd.startswith(("FWD", "REV")):
            self._new_motion_episode()
        if cmd not in {"STATUS", "STOP"}:
            self._pending_replies = deque((_command_replies(cmd),))
        self.emit("TX " + cmd)
        self._write((cmd + "\r\n").encode("ascii"))
        if deadline is None:
            deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            lines = self.poll(min(0.02, max(0, deadline - time.monotonic())))
            if v2_control and time.monotonic() > deadline:
                break
            if cmd == "STATUS":
                identities = [line for line in lines if line.startswith("OK STATUS ")]
                if any(line not in STATUS_IDENTITIES for line in identities):
                    raise RuntimeError("Incompatible firmware identity; Bluetooth image required")
                if identities:
                    self.hold_supported = identities[-1] in {HOLD_IDENTITY, COMBINED_IDENTITY}
                    self.combined_supported = identities[-1] == COMBINED_IDENTITY
                    return
            if v2_control:
                acknowledgements = [line for line in lines if line in set(ACKS.values()) or
                                    line.startswith("OK STATUS ")]
                if acknowledgements and acknowledgements != [expected]:
                    raise RuntimeError("Unexpected V2 control acknowledgement")
            if expected in lines:
                if cmd == "STOP":
                    self._pending_replies.clear()
                    self._episode_open = False
                    self._fault_evidence = None
                    self._fault_history.clear()
                return
        raise RuntimeError("Missing acknowledgement: " + expected)

    def keepalive(self, steering):
        """Renew both leases in one packet instead of two BLE round trips."""
        if self.closed:
            raise ConnectionError("Bluetooth connection is closed")
        if not self.identified:
            raise RuntimeError("Identify the Bluetooth firmware before control")
        if steering not in STEERING:
            raise ValueError("Unsupported Bluetooth steering")
        if self.combined_supported:
            return self.command("KEEP" + steering, "OK KEEP", 0.4)
        self._pending_replies = deque((_command_replies("PING"), _command_replies(steering)))
        self.emit("TX PING")
        self.emit("TX " + steering)
        self._write(("PING\r\n" + steering + "\r\n").encode("ascii"))
        pending = deque((ACKS["PING"], "OK STEER"))
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            lines = self.poll(min(0.02, max(0, deadline - time.monotonic())))
            if time.monotonic() > deadline:
                break
            for line in lines:
                if line in set(ACKS.values()) | STATUS_IDENTITIES:
                    if not pending or line != pending[0]:
                        raise RuntimeError("Unexpected combined-control acknowledgement")
                    pending.popleft()
            if not pending:
                return
        raise RuntimeError("Missing combined-control acknowledgement")

    def start_drive(self, direction, steering):
        """Start one fixed V2 motor-and-steering transaction after explicit ARM."""
        if self.closed:
            raise ConnectionError("Bluetooth connection is closed")
        if not self.identified:
            raise RuntimeError("Identify the Bluetooth firmware before control")
        if not self.combined_supported:
            raise RuntimeError("Combined-control V2 firmware is required")
        if direction not in {"FWD", "REV"} or steering not in STEERING:
            raise ValueError("Unsupported combined drive direction or steering")
        return self.command(direction + steering, "OK ACTION", 0.4)

    def identify(self):
        self.command("STATUS", ACKS["STATUS"], 0.6)
        self.command("STOP", ACKS["STOP"])
        self.identified = True
        self.records.clear()

    def stop_and_confirm(self, candidate=None):
        """Keep the link only after this STOP and a newly received parked state."""
        if self.closed or not self.identified:
            raise ConnectionError("Verified Bluetooth connection is required for STOP confirmation")
        if candidate is not None and not isinstance(candidate, MotorFaultCandidate):
            raise ValueError("Motor fault recovery requires a typed candidate")
        fault = self._current_motor_fault(candidate)
        deadline = time.monotonic() + 1.0
        self._closing = True
        try:
            # Attempt STOP even if writing the log has failed.
            self._write(b"STOP\r\n")
            try:
                self.emit("TX STOP")
            except Exception:
                pass
            acknowledged = False
            while time.monotonic() < deadline:
                stopped = None
                lines = self.poll(min(0.02, max(0, deadline - time.monotonic())))
                if time.monotonic() > deadline:
                    break
                # A fault may follow its ERR in the same fragmented notification.
                if candidate is not None and self._fault_evidence is not None:
                    evidence_row = self._fault_evidence[-1]
                    if not is_motor_fault(evidence_row):
                        fault = None
                    else:
                        current_fault = self._current_motor_fault(candidate)
                        if current_fault is not None:
                            fault = current_fault
                for line in lines:
                    if line == "OK STOP":
                        if acknowledged:
                            raise RuntimeError("Duplicate STOP cleanup acknowledgement")
                        acknowledged = True
                    elif acknowledged:
                        row = parse_record(line)
                        if row is not None:
                            if (any(row[key] for key in ("active", "left_target5", "right_target5",
                                                         "left_pwm8400", "right_pwm8400", "stop_reason")) or
                                    row["servo_compare"] != 750):
                                raise RuntimeError("STOP cleanup state is not stopped and centered")
                            stopped = row.copy()
                if acknowledged and stopped is not None and not self._pending_replies:
                    if candidate is not None and fault is None:
                        raise RuntimeError("Current motion episode has no confirmed motor fault")
                    self.records.clear()
                    self._pending_replies.clear()
                    self._episode_open = False
                    self._fault_evidence = None
                    self._fault_history.clear()
                    return {"stopped": stopped, "fault": fault}
            raise RuntimeError("Missing acknowledgement or fresh stopped state after STOP")
        except Exception:
            self.closed = True
            self.identified = False
            try:
                self._close_transport()
            except Exception:
                pass
            raise
        finally:
            self._closing = False

    def close(self):
        if self.closed:
            return
        try:
            if self.identified:
                self.stop_and_confirm()
        finally:
            if not self.closed:
                self.closed = True
                self.identified = False
                self._close_transport()


class SerialCar(_AsciiCar):
    """Optional Windows outgoing SPP COM port; never search or pair implicitly."""

    def __init__(self, port, baud=115200, emit=lambda line: None, serial_factory=None):
        if not isinstance(port, str) or not port.strip() or not 1200 <= baud <= 921600:
            raise ValueError("Choose an explicit COM port and valid baud rate")
        self._init_stream(emit)
        if serial_factory is None:
            from serial import Serial
            serial_factory = Serial
        # Configure modem lines before opening, avoiding an unintended reset pulse.
        self.serial = serial_factory(port=None, baudrate=baud, timeout=0.02,
                                     write_timeout=0.15, xonxoff=False,
                                     rtscts=False, dsrdtr=False)
        try:
            self.serial.dtr = False
            self.serial.rts = False
            self.serial.port = port
            self.serial.open()
        except Exception:
            self.serial.close()
            raise

    def _write(self, data):
        if self.serial.write(data) != len(data):
            raise ConnectionError("Partial Bluetooth serial write")

    def poll(self, timeout=0.02):
        if self.closed or not self.serial.is_open:
            raise ConnectionError("Bluetooth serial port is closed")
        self.serial.timeout = max(0, min(timeout, 0.1))
        return self._consume(self.serial.read(min(2048, self.serial.in_waiting or 1)))

    def _close_transport(self):
        self.serial.close()


def discover_ports():
    """Enumerate descriptors only; no serial port is opened."""
    from serial.tools import list_ports
    result = []
    for item in list_ports.comports():
        description = item.description or "Serial port"
        metadata = " ".join(str(value or "") for value in
                            (description, item.hwid, item.manufacturer)).casefold()
        result.append({"port": item.device, "label": f"{item.device} — {description}",
                       "bluetooth": any(word in metadata for word in
                                        ("bluetooth", "bthenum", "bth", "蓝牙"))})
    return sorted(result, key=lambda row: (not row["bluetooth"], row["port"]))


def scan_devices(timeout=5, scanner_factory=None):
    """Run an explicitly requested BLE scan; never open or select a device."""
    if not 0 < timeout <= 15:
        raise ValueError("BLE scan timeout must be within 0..15 seconds")
    if scanner_factory is None:
        from bleak import BleakScanner
        scanner_factory = BleakScanner
    loop = asyncio.new_event_loop()
    try:
        devices = loop.run_until_complete(scanner_factory.discover(timeout=timeout))
        return [{"device": item, "name": item.name or "未命名设备",
                 "address": item.address} for item in devices]
    finally:
        loop.close()


class BleCar(_AsciiCar):
    """Own an asyncio loop in the connection worker, with verified GATT selection."""

    def __init__(self, device, emit, tx_uuid=None, rx_uuid=None,
                 service_uuid=BLE100_SERVICE_UUID, client_factory=None, trace_ble=False,
                 connect_timeout=5.0):
        self._init_stream(emit)
        self.connect_phase = "runtime_setup"
        if (type(connect_timeout) not in (int, float) or
                not 0 < connect_timeout <= 30):
            raise ValueError("BLE connection timeout must be within 0..30 seconds")
        self.trace_ble = trace_ble
        self.receive_trace = deque(maxlen=16)
        self.receive_sequence = 0
        if bool(tx_uuid) != bool(rx_uuid):
            raise ValueError("Provide both explicit GATT characteristic UUIDs")
        if client_factory is None:
            from bleak import BleakClient
            client_factory = BleakClient
        self.loop = asyncio.new_event_loop()
        self.notifications = deque(maxlen=128)
        self.notification_overflow = False
        self.disconnected = False
        self.subscribed = False
        self.client = None
        self._sync_started = False
        self._sync_discard = False
        self._sync_bytes = 0
        try:
            self.connect_phase = "client_create"
            self.client = client_factory(device, disconnected_callback=self._disconnected,
                                         timeout=connect_timeout)
            self.loop.run_until_complete(asyncio.wait_for(
                self._connect(tx_uuid, rx_uuid, service_uuid), timeout=connect_timeout + 1.0))
        except Exception:
            try:
                if self.client is not None:
                    self.loop.run_until_complete(asyncio.wait_for(
                        self.client.disconnect(), timeout=0.5))
            except Exception:
                # Preserve the original connection/selection error for the user.
                pass
            finally:
                self.loop.close()
                self.closed = True
            raise

    def _disconnected(self, client):
        self.disconnected = True

    def _notify(self, sender, data):
        if self.trace_ble:
            self.receive_sequence += 1
            self.receive_trace.append((self.receive_sequence, time.monotonic(), bytes(data)))
        if len(self.notifications) == self.notifications.maxlen:
            self.notification_overflow = True
            return
        self.notifications.append(bytes(data))

    async def _connect(self, tx_uuid, rx_uuid, service_uuid):
        self.connect_phase = "link_and_services"
        await self.client.connect()
        self.connect_phase = "service_selection"
        services = self.client.services
        if tx_uuid:
            self.connect_phase = "characteristic_selection"
            self.tx = services.get_characteristic(tx_uuid)
            self.rx = services.get_characteristic(rx_uuid)
            if self.tx is None or self.rx is None:
                raise RuntimeError("Explicit GATT characteristics were not found")
        else:
            service = services.get_service(service_uuid)
            if service is None:
                raise RuntimeError("Verified BLE100 UART service was not found; select explicit UUIDs")
            self.connect_phase = "characteristic_selection"
            writers = [char for char in service.characteristics
                       if {"write", "write-without-response"} & set(char.properties)]
            readers = [char for char in service.characteristics
                       if {"notify", "indicate"} & set(char.properties)]
            if len(writers) != 1 or len(readers) != 1:
                raise RuntimeError("Ambiguous BLE100 characteristics; select explicit TX/RX UUIDs")
            self.tx, self.rx = writers[0], readers[0]
        if not {"write", "write-without-response"} & set(self.tx.properties):
            raise RuntimeError("Selected TX characteristic is not writable")
        if not {"notify", "indicate"} & set(self.rx.properties):
            raise RuntimeError("Selected RX characteristic cannot notify")
        self.write_response = "write-without-response" not in self.tx.properties
        self.connect_phase = "notification_subscription"
        await self.client.start_notify(self.rx, self._notify)
        self.subscribed = True
        self.connect_phase = "ready"

    def _write(self, data):
        if self.disconnected or not self.client.is_connected:
            raise ConnectionError("Bluetooth LE connection closed")
        # The documented BLE100 UART packet is at most 20 bytes.
        if len(data) > 20:
            raise ValueError("BLE100 command exceeds the 20-byte packet limit")
        self.loop.run_until_complete(asyncio.wait_for(self.client.write_gatt_char(
            self.tx, data, response=self.write_response), timeout=0.2))

    def _consume(self, data):
        if self._sync_discard:
            boundary = data.find(b"\n")
            self._sync_bytes += len(data) if boundary < 0 else boundary + 1
            if self._sync_bytes > 4096:
                raise RuntimeError("Initial Bluetooth line exceeds the 4096-byte limit")
            if boundary < 0:
                return []
            self._sync_discard = False
            self.emit(f"STREAM_SYNC_BOUNDARY bytes={self._sync_bytes}")
            data = data[boundary + 1:]
        return super()._consume(data)

    def synchronize_stream(self):
        """Align only the initial subscription segment before any command is sent."""
        if self._sync_started or self.identified or self.buffer or self.records:
            raise RuntimeError("Initial Bluetooth alignment is allowed only once before identity")
        self._sync_started = self._sync_discard = True
        deadline = time.monotonic() + 1.0
        self.emit("STREAM_SYNC：等待初始行边界及完整的静止状态。")
        try:
            while time.monotonic() < deadline:
                self.poll(min(0.02, max(0, deadline - time.monotonic())))
                if time.monotonic() <= deadline and self.records:
                    for row in self.records:
                        if (any(row[key] for key in ("active", "left_target5", "right_target5",
                                                     "left_pwm8400", "right_pwm8400")) or
                                row["servo_compare"] != 750):
                            raise RuntimeError("Initial Bluetooth state is not stopped and centered")
                    self.emit("STREAM_SYNC_READY：接收已对齐，开始核对小车身份。")
                    return
            raise RuntimeError("Initial Bluetooth alignment and idle state timed out")
        finally:
            self._sync_discard = False

    def poll(self, timeout=0.02):
        try:
            if self.closed:
                raise ConnectionError("Bluetooth LE connection closed")
            self.loop.run_until_complete(asyncio.sleep(max(0, min(timeout, 0.1))))
            if self.disconnected or not self.client.is_connected:
                raise ConnectionError("Bluetooth LE connection closed")
            if self.notification_overflow:
                raise RuntimeError("Bluetooth notification buffer overflow")
            lines = []
            # An ERR can leave complete tail lines buffered before it is raised.
            if b"\n" in self.buffer:
                lines.extend(self._consume(b""))
            while self.notifications:
                lines.extend(self._consume(self.notifications.popleft()))
            return lines
        except Exception:
            self._trace_receive_error()
            raise

    def _trace_receive_error(self):
        if not self.trace_ble:
            return
        try:
            for sequence, stamp, data in tuple(self.receive_trace):
                # Match the parser's 4096-byte bound even for an unexpected chunk size.
                suffix = f" omitted={len(data) - 4096}" if len(data) > 4096 else ""
                self.emit(f"RX_BLE seq={sequence} t={stamp:.9f} len={len(data)} "
                          f"hex={data[:4096].hex()}{suffix}")
        except Exception:
            # A full disk or failed UI/logger must not replace the receive failure.
            pass

    def _close_transport(self):
        async def disconnect():
            try:
                if self.subscribed and self.client.is_connected:
                    await asyncio.wait_for(self.client.stop_notify(self.rx), timeout=0.5)
            finally:
                await asyncio.wait_for(self.client.disconnect(), timeout=0.5)
        try:
            self.loop.run_until_complete(disconnect())
        finally:
            self.loop.close()


class SimulationCar(_AsciiCar):
    """A bounded command model for UI practice, never an actual vehicle."""

    def __init__(self, emit, clock=time.monotonic, continuous=False, combined=False):
        self._init_stream(lambda line: emit("SIMULATION " + line))
        self.hold_supported = continuous or combined
        self.combined_supported = combined
        self.lease_seconds = 0.5 if combined else 0.25
        self.clock = clock
        self.origin = self.last = clock()
        self.pending = deque()
        self.active = False
        self.armed_until = 0
        self.cool_until = 0
        self.started = self.lease_until = 0
        self.servo = 750
        self.servo_until = 0
        self.direction = 0
        self.count = 0.0
        self.reason = 0
        self.emit("OFFLINE MOCK; no Bluetooth or serial connection is opened")

    def _stop(self, now, reason=0):
        self.active = False
        self.armed_until = 0
        self.cool_until = now + 0.5
        self.direction = 0
        self.servo = 750
        self.reason = reason

    def _advance(self):
        now = self.clock()
        if self.active:
            hard_limit = float("inf") if self.hold_supported else self.started + 1.0
            end = min(now, self.lease_until, hard_limit)
            self.count += max(0, end - self.last) * self.direction * 600
            if now >= min(self.lease_until, hard_limit):
                self._stop(end, 6 if hard_limit <= self.lease_until else 1)
        if now >= self.servo_until:
            self.servo = 750
        self.last = now
        return now

    def _write(self, data):
        commands = data.decode("ascii").split()
        if not commands or any(command not in ACKS for command in commands):
            raise ValueError("Unsupported simulation command")
        for command in commands:
            self._apply_command(command)

    def _apply_command(self, cmd):
        now = self._advance()
        reply = ACKS[cmd]
        direction = cmd
        steering = None
        if cmd in COMBINED_COMMANDS:
            if not self.combined_supported:
                self.pending.append(b"ERR COMMAND\r\n")
                return
            for prefix in ("FWD", "REV", "KEEP"):
                if cmd.startswith(prefix):
                    direction, steering = prefix, cmd[len(prefix):]
                    break
        if cmd == "STATUS":
            reply = COMBINED_IDENTITY if self.combined_supported else HOLD_IDENTITY if self.hold_supported else ACKS[cmd]
        if cmd == "STOP":
            self._stop(now)
        elif direction in {"PING", "KEEP"}:
            if not self.active:
                reply = "ERR NOT_ACTIVE"
            else:
                self.lease_until = now + self.lease_seconds
                if steering is not None:
                    self.servo = {"LEFT": 700, "CENTER": 750, "RIGHT": 800}[steering]
                    self.servo_until = now + self.lease_seconds
        elif cmd in STEERING:
            if cmd != "CENTER" and self.reason in (2, 3, 4, 5, 7):
                reply = "ERR FAULT_STOP_REQUIRED"
            else:
                self.servo = {"LEFT": 700, "CENTER": 750, "RIGHT": 800}[cmd]
                self.servo_until = now + self.lease_seconds
        elif direction in {"ARM", "FWD", "REV"}:
            if self.active:
                reply = "ERR BUSY"
            elif now < self.cool_until:
                reply = "ERR COOLDOWN"
            elif self.reason in (2, 3, 4, 5, 7):
                reply = "ERR FAULT_STOP_REQUIRED"
            elif direction == "ARM":
                self.armed_until = now + 2.0
            elif now >= self.armed_until:
                reply = "ERR NOT_ARMED"
            else:
                self.active = True
                self.armed_until = 0
                self.started = now
                self.lease_until = now + self.lease_seconds
                self.direction = 1 if direction == "FWD" else -1
                self.reason = 0
                if steering is not None:
                    self.servo = {"LEFT": 700, "CENTER": 750, "RIGHT": 800}[steering]
                    self.servo_until = now + self.lease_seconds
        self.pending.append((reply + "\r\n").encode("ascii"))

    def poll(self, timeout=0.02):
        if self.closed:
            raise ConnectionError("Offline simulation is closed")
        if timeout > 0 and not self.pending:
            time.sleep(min(timeout, 0.02))
        now = self._advance()
        lines = []
        while self.pending:
            lines.extend(self._consume(self.pending.popleft()))
        delta = self.direction * 3 if self.active else 0
        pwm = self.direction * 1680 if self.active else 0
        values = (int((now - self.origin) * 1000), int(self.count), int(self.count),
                  delta, delta, delta, delta, pwm, pwm, self.servo,
                  int(self.active), self.reason)
        lines.extend(self._consume(("CAR2," + ",".join(map(str, values)) + "\r\n").encode("ascii")))
        return lines

    def _close_transport(self):
        self.pending.clear()
