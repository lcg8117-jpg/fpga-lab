"""Bounded TCP control for the USR transparent Wi-Fi link."""
from collections import deque
import socket
import time

FIELDS = ("ms", "left_count", "right_count", "left_delta5", "right_delta5",
          "left_target5", "right_target5", "left_pwm8400", "right_pwm8400",
          "servo_compare", "active", "stop_reason")
STEERING = {"LEFT", "CENTER", "RIGHT"}


def is_motor_fault(row):
    """Recognize a stopped motor fault without authorizing a recovery."""
    return (row["stop_reason"] in (2, 3, 4, 5) and not row["active"] and
            not any(row[key] for key in ("left_target5", "right_target5",
                                         "left_pwm8400", "right_pwm8400")) and
            row["servo_compare"] == 750)


class MotorFaultCandidate(RuntimeError):
    """A possible motor stop; the transport must still prove safe parking."""

    def __init__(self, message, fault_row=None, episode=None):
        super().__init__(message)
        self.fault_row = fault_row.copy() if fault_row is not None else None
        self.episode = episode


def parse_record(line):
    if not line.startswith("CAR2,"):
        return None
    values = line.split(",")[1:]
    if len(values) != len(FIELDS):
        raise RuntimeError("Malformed wireless telemetry")
    try:
        row = dict(zip(FIELDS, map(int, values)))
    except ValueError as error:
        raise RuntimeError("Non-integer wireless telemetry") from error
    if (row["active"] not in (0, 1) or not 0 <= row["stop_reason"] <= 7 or
            row["servo_compare"] not in (700, 750, 800) or
            any(abs(row[key]) > 2100 for key in ("left_pwm8400", "right_pwm8400")) or
            any(abs(row[key]) > 3 for key in ("left_target5", "right_target5")) or
            (not row["active"] and (row["left_pwm8400"] or row["right_pwm8400"]))):
        raise RuntimeError("Wireless telemetry is outside the firmware contract")
    return row


class TcpCar:
    """Own one socket and preserve packet fragments across acknowledgements."""

    def __init__(self, host, port, emit, connector=socket.create_connection):
        self.socket = connector((host, port), timeout=0.8)
        self.buffer = bytearray()
        self.records = deque(maxlen=64)
        self.emit = emit
        self.identified = False

    def poll(self, timeout=0.02):
        self.socket.settimeout(timeout)
        try:
            data = self.socket.recv(2048)
        except socket.timeout:
            return []
        if not data:
            raise ConnectionError("Wi-Fi connection closed")
        self.buffer.extend(data)
        if len(self.buffer) > 4096:
            raise RuntimeError("Wireless receive buffer overflow")
        lines = []
        while b"\n" in self.buffer:
            raw, _, rest = self.buffer.partition(b"\n")
            self.buffer[:] = rest
            try:
                line = raw.decode("ascii").strip()
            except UnicodeDecodeError as error:
                raise RuntimeError("Non-ASCII wireless data") from error
            if not line:
                continue
            self.emit(line)
            record = parse_record(line)
            if record is not None:
                self.records.append(record)
            if line.startswith("ERR "):
                raise RuntimeError(line)
            lines.append(line)
        return lines

    def command(self, command, expected, seconds=0.3):
        if command not in {"STATUS", "STOP", "ARM", "FWD", "REV", "PING"} | STEERING:
            raise ValueError("Unsupported wireless command")
        self.socket.settimeout(0.15)
        self.emit("TX " + command)
        self.socket.sendall((command + "\r\n").encode("ascii"))
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if expected in self.poll():
                return
        raise RuntimeError("Missing acknowledgement: " + expected)

    def identify(self):
        self.command("STATUS", "OK STATUS WIRELESS", 0.6)
        self.identified = True
        self.command("STOP", "OK STOP")
        self.records.clear()

    def close(self):
        try:
            if self.identified:
                self.command("STOP", "OK STOP", 0.2)
        finally:
            self.socket.close()


def new_control(now, max_run=0.8):
    return dict(active=None, started=0.0, ping_at=0.0, steer="CENTER",
                steer_at=0.0, cool_until=now + 0.5, blocked=True, saw_active=False,
                max_run=max_run)


def fresh_intent(get_intent, now):
    direction, steering, stamp = get_intent()
    if direction not in (None, "FWD", "REV") or steering not in STEERING:
        raise ValueError("Invalid control intent")
    fresh = 0 <= now - stamp <= 0.2
    return direction if fresh else None, steering if fresh else "CENTER", fresh


def stop_control(state, car, now, blocked=False):
    car.command("STOP", "OK STOP")
    state.update(active=None, steer="CENTER", cool_until=now + 0.5,
                 blocked=blocked, saw_active=False)
    car.records.clear()


def control_step(state, car, get_intent, now, clock=time.monotonic, keepalive=None,
                 start_drive=None):
    direction, steering, fresh = fresh_intent(get_intent, now)
    if not fresh:
        state["blocked"] = True
    elif direction is None:
        state["blocked"] = False

    if state["active"]:
        while car.records:
            row = car.records.popleft()
            state["saw_active"] |= bool(row["active"])
            if not row["active"] and (row["stop_reason"] or state["saw_active"]):
                if is_motor_fault(row):
                    raise MotorFaultCandidate(f"Firmware stopped; reason={row['stop_reason']}",
                                              row, getattr(car, "motion_episode", None))
                raise RuntimeError(f"Firmware stopped; reason={row['stop_reason']}")
        if (direction != state["active"] or not fresh or
                (state["max_run"] is not None and now - state["started"] >= state["max_run"])):
            stop_control(state, car, now, blocked=direction is not None or not fresh)
            return

        if keepalive is not None:
            if (steering != state["steer"] or now - state["ping_at"] >= 0.1 or
                    (steering != "CENTER" and now - state["steer_at"] >= 0.1)):
                # Steering ACK latency must not delay the motor lease renewal.
                keepalive(steering)
                state.update(steer=steering, steer_at=now, ping_at=now)
                if start_drive is not None:
                    latest_direction, _, current = fresh_intent(get_intent, clock())
                    if not current or latest_direction != direction:
                        stop_control(state, car, clock(),
                                     blocked=latest_direction is not None or not current)
                        state["cool_until"] = clock() + 0.5
            return

    if state["blocked"]:
        direction = None
        steering = "CENTER"

    if start_drive is not None and not state["active"] and direction and now >= state["cool_until"]:
        car.command("ARM", "OK ARM")
        latest_direction, latest_steering, current = fresh_intent(get_intent, clock())
        if not current or latest_direction != direction:
            stop_control(state, car, clock(), blocked=latest_direction is not None or not current)
            state["cool_until"] = clock() + 0.5
            return
        car.records.clear()
        started = clock()
        # V2 applies steering and rear drive in the same guarded MCU transaction.
        start_drive(direction, latest_steering)
        state.update(active=direction, started=started, ping_at=started,
                     steer=latest_steering, steer_at=started, saw_active=False)
        latest_direction, _, current = fresh_intent(get_intent, clock())
        if not current or latest_direction != direction:
            stop_control(state, car, clock(), blocked=latest_direction is not None or not current)
            state["cool_until"] = clock() + 0.5
        return

    if (steering != state["steer"] or
            (steering != "CENTER" and now - state["steer_at"] >= 0.1)):
        car.command(steering, "OK STEER")
        state.update(steer=steering, steer_at=now)

    if not state["active"] and direction and now >= state["cool_until"]:
        car.command("ARM", "OK ARM")
        latest_direction, latest_steering, current = fresh_intent(get_intent, clock())
        if not current or latest_direction != direction:
            stop_control(state, car, clock(), blocked=not current)
            return
        car.command(latest_steering, "OK STEER")
        latest_direction, _, current = fresh_intent(get_intent, clock())
        if not current or latest_direction != direction:
            stop_control(state, car, clock(), blocked=not current)
            return
        car.records.clear()
        started = clock()
        car.command(direction, "OK ACTION")
        state.update(active=direction, started=started, ping_at=started,
                     steer=latest_steering, steer_at=started, saw_active=False)
    elif state["active"] and now - state["ping_at"] >= 0.1:
        car.command("PING", "OK PING")
        state["ping_at"] = now
