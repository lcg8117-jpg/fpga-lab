"""Explicit BLE discovery and an isolated, deterministic offline model."""

import asyncio
from collections import deque
import time

from speed_protocol import CAPABILITIES, FIELDS, IDENTITY, command_ack, encode_frames

SERVICE_UUID = "0003cdd0-0000-1000-8000-00805f9b0131"


def scan_devices(scanner_factory=None):
    if scanner_factory is None:
        from bleak import BleakScanner
        scanner_factory = BleakScanner
    return asyncio.run(scanner_factory.discover(timeout=5.0))


class BleTransport:
    def __init__(self, device, client_factory=None, cancel=None):
        if client_factory is None:
            from bleak import BleakClient
            client_factory = BleakClient
        self.loop = asyncio.new_event_loop()
        self.chunks = deque(maxlen=128)
        self.overflow = self.disconnected = self.closed = False
        self.prepared = False
        self.setup_error = None
        self.client = client_factory(device, disconnected_callback=self._disconnected, timeout=16.0)
        try:
            self.prepare(cancel)
        except Exception as error:
            if cancel and cancel():
                self.close()
                raise
            if self.online:
                # Service errors lock control; they must not tear down an established link.
                self.setup_error = error
            else:
                self.close(disconnect=False)
                raise

    @property
    def online(self):
        return not self.closed and bool(self.client.is_connected)

    async def _prepare_wait(self, cancel):
        task = asyncio.create_task(self._connect())
        deadline = self.loop.time() + 17.0
        try:
            while not task.done():
                if cancel and cancel():
                    raise ConnectionError('已取消连接')
                if self.loop.time() >= deadline:
                    raise TimeoutError('建立连接超时，稍后继续重连')
                await asyncio.wait({task}, timeout=.05)
            return task.result()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def prepare(self, cancel=None):
        if not self.prepared:
            self.loop.run_until_complete(self._prepare_wait(cancel))
            self.setup_error = None

    def _disconnected(self, client):
        self.disconnected = True

    def _notify(self, sender, data):
        if len(self.chunks) == self.chunks.maxlen:
            self.overflow = True
        else:
            self.chunks.append(bytes(data))

    async def _connect(self):
        if not self.client.is_connected:
            await self.client.connect()
        self.disconnected = False
        service = self.client.services.get_service(SERVICE_UUID)
        if service is None:
            raise RuntimeError("未找到原装 BLE 串口服务")
        writers = [item for item in service.characteristics if set(item.properties) & {"write", "write-without-response"}]
        readers = [item for item in service.characteristics if set(item.properties) & {"notify", "indicate"}]
        if len(writers) != 1 or len(readers) != 1:
            raise RuntimeError("蓝牙服务的读写通道不唯一，连接保留但运动锁定")
        self.tx, self.rx = writers[0], readers[0]
        self.response = "write-without-response" not in self.tx.properties
        await self.client.start_notify(self.rx, self._notify)
        self.prepared = True

    def write(self, payload):
        if not self.online:
            raise ConnectionError("蓝牙已断开")
        if not self.prepared:
            raise RuntimeError('蓝牙连接仍在，正在等待串口服务就绪')
        if len(payload) > 20:
            raise ValueError("蓝牙指令超过 20 字节")
        self.loop.run_until_complete(asyncio.wait_for(
            self.client.write_gatt_char(self.tx, payload, response=self.response), 0.18))

    def read(self, timeout):
        self.loop.run_until_complete(asyncio.sleep(max(0, min(timeout, 0.03))))
        if not self.online:
            raise ConnectionError("蓝牙已断开；小车的独立租约将停止驱动")
        if self.overflow:
            # Retire the overrun batch; only subsequent checked packets can renew freshness.
            self.overflow = False
            self.chunks.clear()
            return [b'\xff\n']
        result = list(self.chunks)
        self.chunks.clear()
        return result

    def close(self, disconnect=True):
        """Dispose dead WinRT handles; only the operator may tear down a live link."""
        if self.closed:
            return
        try:
            if disconnect or not self.client.is_connected:
                self.loop.run_until_complete(asyncio.wait_for(self.client.disconnect(), 0.6))
        finally:
            self.closed = True
            self.loop.close()


class SimTransport:
    """Protocol practice only; not a motor or braking performance model."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.origin = self.last = self.last_frame = clock()
        self.pending = deque([b"OFFLINE\r\n"])
        self.row = dict.fromkeys(FIELDS, 0)
        self.row["servo_compare"] = 750
        self.armed = self.active = self.closed = self.calibrating = False
        self.fault_latched = self.brake_used = False
        self.lease = self.cooldown = self.brake_until = self.arm_until = 0.0
        self.steer_until = 0.0
        self.steer_at = self.servo_stable_at = clock()
        self.target_servo = self.drive_servo = 750
        self.steering_wait = False
        self.quiet_time = 0.015
        self.route = 0
        self.route_started = self.route_until = self.phase_started = 0.0
        self.phase = 0
        self.parked_hold = None
        self.sequence = None
        self.speed = self.slow = 0
        # Phase intent is independent from the published, constrained current target.
        self.phase_request = 0
        self.boost_until = 0.0
        self.boost_peak = 0
        self.boost_used = self.boost_active = False
        self.report_sequence = 0
        self._frame()

    def _frame(self):
        self.row["ms"] = int((self.clock() - self.origin) * 1000) & 0xffffffff
        self.pending.extend(encode_frames(self.row, self.report_sequence))
        self.report_sequence = (self.report_sequence + 1) & 63

    def _stop(self, reason=0, brake=False, now=None):
        if brake and (self.row["braking"] or self.brake_used):
            return
        self.calibrating = False
        now = self.clock() if now is None else now
        self.active = self.armed = self.steering_wait = False
        self.route = self.phase = self.phase_request = 0
        self.sequence = None
        self.boost_until = 0.0
        self.boost_active = False
        self.parked_hold = None
        self.row.update(active=0, braking=int(brake), requested_q8=0, ramped_q8=0,
                        left_pwm8400=0, right_pwm8400=0, stop_reason=reason,
                        boost_active=0, route_id=0, phase_id=0)
        self.brake_until = now + 0.15 if brake else 0
        if brake:
            self.brake_used = True
        if reason in (2, 3, 4, 5, 7):
            self.fault_latched = True
        self.cooldown = now + 0.5
        self.target_servo = 750

    def _reject(self, message):
        self._stop(7)
        return message

    @staticmethod
    def _interpolate(elapsed, start, begin, end):
        part = max(0.0, min(1.0, (elapsed - start) / .8))
        return begin + int((end - begin) * part)

    def _route_boost(self, now, elapsed, start):
        self.boost_peak = self.speed
        if not self.boost_used and elapsed >= start:
            self.boost_used = True
            self.boost_until = self.route_started + start + .25
            self.boost_active = bool(self.row["profile"] and now < self.boost_until)
        if not self.row["profile"] or elapsed < start or elapsed >= start + .25:
            self.boost_active = False

    def _route_phase(self, now):
        elapsed = now - self.route_started
        goal = self.speed
        phase = 1
        if self.route == 1:
            self.target_servo = 700
        elif self.route == 2:
            self.target_servo = 800
        elif self.route == 3:
            phase = 1 if elapsed < 4 else 2
            self.target_servo = self._interpolate(elapsed, 3.6, 700, 800)
        elif self.route == 4:
            phase = min(4, int(elapsed / 2) + 1)
            self.target_servo = (self._interpolate(elapsed, 1.6, 700, 800) if elapsed < 3.2 else
                                 self._interpolate(elapsed, 3.6, 800, 700) if elapsed < 5.2 else
                                 self._interpolate(elapsed, 5.6, 700, 800))
        elif self.route == 5:
            self.target_servo = 750
            if self.phase in (1, 3) and now - self.phase_started >= 2:
                self.phase += 1
                self.parked_hold = None
            if self.phase in (2, 4):
                goal = 0
                if self.row["ramped_q8"] or self.quiet_time < .015:
                    self.parked_hold = None
                elif self.parked_hold is None:
                    self.parked_hold = now
                elif now - self.parked_hold >= .5:
                    if self.phase == 4:
                        self._stop(6, now=now)
                        return
                    self.phase, self.phase_started, self.parked_hold = 3, now, None
                    goal = self.speed
            phase = self.phase
        elif self.route == 6:
            self.target_servo = 750
            phase = 1 if elapsed < 1.5 else 2 if elapsed < 4.5 else 3 if elapsed < 6 else 4
            goal = self.speed if phase == 2 else 0 if phase == 4 else self.slow
            self._route_boost(now, elapsed, 1.5)
            if phase == 4 and not self.row["ramped_q8"] and self.quiet_time >= .015:
                self._stop(6, now=now)
                return
        elif self.route == 7:
            self.target_servo = 750
            phase = 1 if elapsed < 1 else 2
            goal = self.slow if phase == 1 else self.speed
            self._route_boost(now, elapsed, 1)
        elif self.route == 8:
            points = (750, 736, 730, 736, 750, 764, 770, 764, 750)
            index = min(7, int(elapsed))
            self.target_servo = points[index] + int((points[index + 1] - points[index]) * (elapsed - index))
            phase = index + 1
        self.phase, self.phase_request = phase, goal
        self.row["route_id"], self.row["phase_id"] = self.route, phase

    def _current_target(self, now):
        centered = abs(self.target_servo - 750) <= 5 and abs(self.row["servo_compare"] - 750) <= 5
        if not centered or now >= self.boost_until or self.steering_wait:
            self.boost_active = False
        allowed = self.active and not self.row["braking"] and not self.steering_wait and not self.fault_latched
        target = self.phase_request if allowed else 0
        boosting = bool(allowed and self.boost_active and self.row["profile"] and centered and target > 0)
        cap = 4096 if self.row["profile"] else 1536
        if boosting:
            target = self.boost_peak
            if not self.route:
                cap = 6144
        self.row["boost_active"] = int(boosting)
        self.row["requested_q8"] = max(-cap, min(cap, target))
        if self.route:
            self.row["phase_id"] = 9 if self.steering_wait else self.phase
        return self.row["requested_q8"]

    def _step_model(self, now, delta):
        if self.active and self.route and self.steering_wait:
            self.route_started += delta
            self.phase_started += delta
            if self.parked_hold is not None:
                self.parked_hold += delta
        if self.active and now >= self.lease:
            self._stop(1, True, now=now)
        if self.armed and now >= self.arm_until:
            self.armed = False
        if self.row["braking"] and now >= self.brake_until:
            self.row["braking"] = 0
        if self.active and self.route:
            elapsed = now - self.route_started
            if self.route == 7 and elapsed >= 3.5:
                self._stop(6, True, now=now)
            elif now >= self.route_until or self.route not in (5, 6, 7) and elapsed >= 8:
                self._stop(6, now=now)
            else:
                self._route_phase(now)
        if self.active:
            changed = abs(self.target_servo - self.drive_servo) > 5 if self.route else self.target_servo != self.drive_servo
            if changed:
                self.steering_wait = True
            if not self.steering_wait:
                self.drive_servo = self.target_servo
            else:
                self.row["ramped_q8"] = self.row["left_pwm8400"] = self.row["right_pwm8400"] = 0
                self.boost_active = False
        elif not self.armed and now >= self.steer_until:
            self.target_servo = 750
        if now - self.steer_at >= .02 - 1e-9:
            self.steer_at = now
            desired = self.target_servo
            if self.active and self.steering_wait and self.quiet_time < .015:
                desired = self.row["servo_compare"]
            difference = desired - self.row["servo_compare"]
            if difference:
                self.servo_stable_at = now
                self.row["servo_compare"] += max(-5, min(5, difference))
        if (self.active and self.steering_wait and self.quiet_time >= .015 and
                self.row["servo_compare"] == self.target_servo and now - self.servo_stable_at >= .2):
            self.steering_wait = False
            self.drive_servo = self.target_servo
        target = self._current_target(now)
        if self.active and not self.steering_wait:
            accelerating = target > self.row["ramped_q8"]
            ramp_step = round(delta * (51200 if self.row["boost_active"] and accelerating else 3200))
            self.row["ramped_q8"] += max(-ramp_step, min(ramp_step, target - self.row["ramped_q8"]))
            cap = 7560 if self.row["boost_active"] else 6720 if self.row["profile"] else 4200
            pwm = max(-cap, min(cap, int(self.row["ramped_q8"] * 1.5)))
            self.row["left_pwm8400"] = self.row["right_pwm8400"] = pwm
            count = int(self.row["ramped_q8"] / 256 * delta * 200)
            for key in ("left_count", "right_count"):
                self.row[key] += count
            self.row["left_delta5"] = self.row["right_delta5"] = int(self.row["ramped_q8"] / 256)
        else:
            self.row["left_delta5"] = self.row["right_delta5"] = 0
        if abs(self.row["left_delta5"]) <= 1 and abs(self.row["right_delta5"]) <= 1:
            self.quiet_time = min(.015, self.quiet_time + delta)
        else:
            self.quiet_time = 0.0

    def _advance(self):
        now = self.clock()
        # Small model steps preserve continuous planned steering even across sparse reads.
        while self.last < now - 1e-9:
            delta = min(.005, now - self.last)
            self.last += delta
            self._step_model(self.last, delta)
        if now - self.last_frame >= 0.2:
            self.last_frame = now
            self._frame()

    def _begin(self, sequence):
        self.active, self.armed = True, False
        self.sequence = sequence
        self.row.update(active=1, stop_reason=0, ramped_q8=0, left_pwm8400=0, right_pwm8400=0)
        self.lease = self.clock() + .5
        self.steering_wait = self.clock() - self.servo_stable_at < .2
        self.drive_servo = self.row["servo_compare"]

    def _execute(self, command):
        parts = command.split()
        kind = parts[0] if parts else ''
        now = self.clock()
        if command == "STATUS":
            return IDENTITY
        if command == "CAPS":
            return CAPABILITIES
        if command in {"STOP", "BRAKE"}:
            self._stop(brake=command == "BRAKE")
            if command == "STOP":
                self.fault_latched = self.brake_used = self.boost_used = False
            return "OK " + command
        if command == "C1":
            if self.active or self.armed or self.row["braking"] or self.fault_latched:
                return "ERR NOT_PARKED"
            self.calibrating = True
            return "OK CAL 1"
        if command == "C0":
            self._stop()
            return "OK CAL 0"
        if self.calibrating and kind in {"ARM", "D", "T", "U", "B", "K"}:
            return "ERR CALIBRATING"
        try:
            # The C parser also accepts a one-off T without slow, defaulting to half speed.
            if kind == 'T' and len(parts) == 4:
                command += ' ' + str(max(128, int(parts[3]) // 2))
                parts = command.split()
            ack = command_ack(command)
        except (ValueError, IndexError):
            return self._reject("ERR RANGE" if kind in {'D', 'S', 'T', 'U', 'B', 'K'} else "ERR COMMAND")
        if kind == "PERF":
            if self.active or self.armed or self.row["braking"] or self.fault_latched:
                return self._reject("ERR NOT_PARKED")
            self.row["profile"] = int(parts[1])
            return ack
        if command == "ARM":
            if self.fault_latched:
                return "ERR FAULT_STOP_REQUIRED"
            if self.row["braking"] or now < self.cooldown:
                return "ERR COOLDOWN"
            if self.active or self.armed or self.quiet_time < .015:
                return self._reject("ERR NOT_PARKED")
            self.armed = True
            self.arm_until = now + 2
            self.boost_used = self.brake_used = False
            self.sequence = None
            return ack
        if kind == "S":
            if self.active or self.armed or self.row["braking"] or self.fault_latched:
                return self._reject("ERR NOT_PARKED")
            self.target_servo = int(parts[1])
            self.steer_until = now + .5
            return ack
        sequence = int(parts[1])
        maximum = 4096 if self.row["profile"] else 1536
        if kind in {'D', 'T', 'U'}:
            speed = int(parts[3] if kind == 'T' else parts[2])
            if abs(speed) > maximum:
                return self._reject("ERR RANGE")
        # A valid late U/K acknowledges completion without renewing or rearming anything.
        if kind in {'K', 'U'} and not self.active and self.row['stop_reason'] == 6:
            return ack
        newer = self.sequence is not None and 0 < (sequence - self.sequence) % 65536 < 32768
        if kind in {'K', 'U'}:
            if not self.active or not self.route or not newer:
                return self._reject("ERR SEQUENCE")
            self.sequence, self.lease = sequence, now + .5
            if kind == 'U':
                self.speed, self.slow = int(parts[2]), int(parts[3])
                self._route_phase(now)
            return ack
        if kind == 'B':
            peak = int(parts[2])
            if not self.active or self.route or not newer:
                return self._reject("ERR SEQUENCE")
            if self.boost_used:
                self.sequence = sequence
                return ack
            if (not self.row["profile"] or self.phase_request <= 0 or
                    abs(self.target_servo - 750) > 5 or abs(self.row["servo_compare"] - 750) > 5 or
                    peak <= max(self.phase_request, self.row["ramped_q8"])):
                return self._reject("ERR BOOST_GATE")
            self.sequence, self.boost_used, self.boost_active = sequence, True, True
            self.boost_until, self.boost_peak = now + .25, peak
            return ack
        if self.active:
            if kind == 'T':
                return self._reject("ERR BUSY")
            if self.route or not newer:
                return self._reject("ERR SEQUENCE")
            if (speed > 0) != (self.phase_request > 0):
                return self._reject("ERR REVERSE_STOP")
        else:
            if self.fault_latched:
                return "ERR FAULT_STOP_REQUIRED"
            if self.row["braking"] or now < self.cooldown:
                return "ERR COOLDOWN"
            if self.quiet_time < .015:
                return "ERR NOT_PARKED"
            if not self.armed:
                return "ERR NOT_ARMED"
            self._begin(sequence)
        self.sequence, self.lease = sequence, now + .5
        if kind == 'D':
            self.phase_request, self.target_servo = speed, int(parts[3])
            if self.target_servo != self.drive_servo:
                self.steering_wait = True
                self.row["ramped_q8"] = self.row["left_pwm8400"] = self.row["right_pwm8400"] = 0
            if abs(self.target_servo - 750) > 5:
                self.boost_active = False
        elif kind == 'T':
            self.route, self.speed, self.slow = int(parts[2]), speed, int(parts[4])
            self.route_started = self.phase_started = now
            self.route_until = now + {5: 20, 6: 12, 7: 9.65}.get(self.route, 14)
            self.phase, self.parked_hold = 1, None
            self._route_phase(now)
            if abs(self.target_servo - self.drive_servo) > 5:
                self.steering_wait = True
        return ack

    def write(self, payload):
        self._advance()
        try:
            command = payload.decode('ascii').strip()
            ack = self._execute(command) if len(payload) <= 20 else self._reject("ERR RANGE")
        except (ValueError, IndexError, UnicodeError):
            command, ack = '', self._reject("ERR COMMAND")
        self._current_target(self.clock())
        kind = command.split()[0] if command else ''
        if not (ack.startswith('OK ') and kind in {'D', 'S', 'K', 'T', 'U', 'B'}):
            self.pending.append((ack + "\r\n").encode())

    def read(self, timeout):
        self.sleep(min(0.01, timeout))
        self._advance()
        result = list(self.pending)
        self.pending.clear()
        return [data[start:start + 18] for data in result for start in range(0, len(data), 18)]

    @property
    def online(self):
        return not self.closed

    def close(self, disconnect=True):
        self.closed = True
