"""Latest-intent manual and finite-route control; no automatic motion retry."""

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import tempfile
import time

from speed_protocol import ProtocolError, is_parked, FEEDBACK_SECONDS

ROUTE_SECONDS = {1: 14.0, 2: 14.0, 3: 14.0, 4: 14.0, 5: 20.0, 6: 12.0, 7: 9.65, 8: 14.0}
DEFAULT_STEERING_LIMITS = (620, 980)


def steering_config_path():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'SmartCarSteeringV8' / 'steering_limits.json'


def validate_steering_limits(left, right):
    if type(left) is not int or type(right) is not int or not 500 <= left < 750 < right <= 1000:
        raise ValueError('左右端点必须为整数，且500≤左<750<右≤1000')
    return left, right


def load_steering_limits(path=None):
    path = Path(path) if path is not None else steering_config_path()
    if not path.exists():
        return DEFAULT_STEERING_LIMITS
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or type(data.get('schema')) is not int or data['schema'] != 1:
            raise ValueError('Invalid steering schema')
        return validate_steering_limits(data.get('left'), data.get('right'))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('转向配置损坏，请重新标定') from error


def save_steering_limits(left, right, path=None):
    validate_steering_limits(left, right)
    path = Path(path) if path is not None else steering_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='steering_', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(dict(schema=1, left=left, right=right), stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class SteeringCalibration:
    """Human-selected five-count steps; feedback verifies PWM, never physical contact."""
    def __init__(self, session):
        self.session = session
        self.target = 750
        self.left = self.right = None
        self.last_sent = -100.0
        self.floor = session.record_sequence
        self.active = False

    def start(self):
        self.session.stop()
        self.session.command('C1')
        self.floor = self.session.record_sequence
        self.active = True

    def reached(self):
        last = self.session.last
        return bool(self.active and last and last.sequence > self.floor and
                    self.session.clock() - last.received < FEEDBACK_SECONDS and
                    is_parked(last.row) and last.row['stop_reason'] == 0 and
                    last.row['servo_compare'] == self.target)

    def pump(self):
        if not self.active:
            return
        last = self.session.last
        if not last or self.session.clock() - last.received >= FEEDBACK_SECONDS or not is_parked(last.row):
            raise ProtocolError('标定反馈中断或后轮未停车，已取消标定')
        if self.session.clock() - self.last_sent >= .2:
            self.session.send(f'S {self.target}')
            self.last_sent = self.session.last_write_at

    def step(self, delta):
        if type(delta) is not int or delta not in (-5, 5):
            raise ValueError('每次仅可调整5个控制单位')
        if not self.reached():
            raise ValueError('等待新的静止和目标反馈后再调整')
        target = self.target + delta
        if not 500 <= target <= 1000:
            raise ValueError('已到本版电子搜索边界500～1000，不能据此认定机械极限')
        self.floor = self.session.record_sequence
        self.target = target
        self.last_sent = -100.0

    def center(self):
        self.target = 750
        self.floor = self.session.record_sequence
        self.last_sent = -100.0

    def record(self, side):
        if not self.reached():
            raise ValueError('等待新的静止和目标反馈后再记录')
        if side == 'left' and self.target < 750:
            self.left = self.target
        elif side == 'right' and self.target > 750:
            self.right = self.target
        else:
            raise ValueError('左端点应小于750，右端点应大于750')

    def save(self, path=None):
        if self.target != 750 or not self.reached():
            raise ValueError('先回中并等待新的静止反馈，再保存')
        validate_steering_limits(self.left, self.right)
        save_steering_limits(self.left, self.right, path)

    def close(self):
        self.active = False
        self.session.command('C0')
        self.session.stop()


@dataclass(frozen=True)
class Intent:
    stamp: float
    direction: int = 0
    servo: int = 750
    speed: int = 768
    route: int = 0
    generation: int = 0
    peak_q8: int = 1536
    slow_percent: int = 50

    @property
    def moving(self):
        return bool(self.direction or self.route)


class Engine:
    def __init__(self, session, latest, emit=lambda text: None, clock=time.monotonic):
        self.session, self.latest, self.emit, self.clock = session, latest, emit, clock
        self.mode = None
        self.direction = self.route = 0
        self.sequence = 0
        self.last_sent = self.steer_sent = -100.0
        self.last_servo = 750
        self.route_parameters = None
        self.started = 0.0
        self.motion_floor = 0
        self.seen_active = False
        self.blocked = False
        self.boost_used = False
        self.preparing = None
        self.prepare_ready = None

    def _fresh(self, intent):
        return 0 <= self.clock() - intent.stamp <= 0.2

    def _valid(self, intent):
        if intent.direction not in (-1, 0, 1) or not 500 <= intent.servo <= 1000:
            raise ValueError("无效方向")
        if intent.route not in range(9) or intent.route and intent.direction:
            raise ValueError("轨迹和手动控制不能同时运行")
        maximum = 4096 if self.session.last and self.session.last.row["profile"] else 1536
        if not 128 <= intent.speed <= 4096:
            raise ValueError("速度目标超出当前档位")
        if type(intent.slow_percent) is not int or intent.slow_percent not in (25, 50, 75):
            raise ValueError("轨迹慢段比例必须为 25%、50% 或 75%")
        # The GUI can still hold its old slider value for one queue cycle after PERF 0.
        return replace(intent, speed=min(maximum, intent.speed))

    def _parked(self):
        last = self.session.last
        return bool(last and self.clock() - last.received <= FEEDBACK_SECONDS and is_parked(last.row)
                    and last.row["stop_reason"] == 0)

    def cancel(self, brake=False, block=False):
        self.mode = None
        self.direction = self.route = 0
        self.last_servo = 750
        self.route_parameters = None
        self.blocked = block
        self.boost_used = False
        self.preparing = self.prepare_ready = None
        self.session.stop(brake)

    def _abort(self, snapshot):
        current = self.latest()
        return (not self._fresh(current) or not current.moving or
                current.direction != snapshot.direction or current.route != snapshot.route)

    def _prepare(self, intent):
        servo = {1: 700, 2: 800, 3: 700, 4: 700}.get(intent.route, 750) if intent.route else intent.servo
        signature = (intent.direction, intent.route, servo)
        if signature != self.preparing:
            self.preparing = signature
            self.prepare_ready = None
            self.steer_sent = -100.0
            self.emit('转向准备：后轮保持零输出，先打方向再行驶')
        if self.clock() - self.steer_sent >= .2:
            self.session.send(f'S {servo}', abort=lambda: self._abort(intent))
            self.steer_sent = self.session.last_write_at
            self.last_servo = servo
        last = self.session.last
        if last.row['servo_compare'] != servo or not is_parked(last.row):
            self.prepare_ready = None
            return False
        if self.prepare_ready is None:
            self.prepare_ready = self.clock()
        return self.clock() - self.prepare_ready >= .2

    def set_profile(self, profile):
        if profile not in (0, 1) or self.mode or self.latest().moving or not self._parked():
            raise ValueError("请松开动作，待车轮静止后再切换速度档")
        floor = self.session.record_sequence
        self.session.command("PERF " + str(profile))
        self.session.wait_profile(profile, floor)

    def _send_motion(self, intent, starting=False):
        sequence = self.sequence
        self.sequence = (self.sequence + 1) & 0xffff
        parameters = None
        if intent.route:
            parameters = route_parameters(intent.speed, intent.slow_percent)
            speed, slow = parameters
            if starting:
                command = f"T {sequence} {intent.route} {speed} {slow}"
            elif parameters != self.route_parameters:
                command = f"U {sequence} {speed} {slow}"
            else:
                command = f"K {sequence}"
        else:
            command = f"D {sequence} {intent.direction * intent.speed} {intent.servo}"
        self.session.send(command, abort=lambda: self._abort(intent))
        self.route_parameters = parameters
        self.last_sent = self.session.last_write_at

    def boost(self, peak_q8):
        intent = self._valid(self.latest())
        last = self.session.last
        if (self.mode != "manual" or self.direction != 1 or not self._fresh(intent) or
                intent.direction != 1 or intent.route or not last or
                self.clock() - last.received > FEEDBACK_SECONDS or not last.row["active"] or
                not last.row["profile"] or last.row["route_id"] or
                abs(intent.servo - 750) > 5 or abs(last.row["servo_compare"] - 750) > 5):
            raise ValueError("急加速需要快速档、持续前进且前轮回正；不会自动启动车辆")
        if self.boost_used:
            raise ValueError("本次行驶已使用急加速；停车并重新前进后可再使用")
        if not 1024 <= peak_q8 <= 6144:
            raise ValueError("急加速峰值必须为 4～24 计数 / 5 ms")
        if peak_q8 <= max(intent.speed, last.row["requested_q8"], last.row["ramped_q8"]):
            raise ValueError("急加速峰值应高于当前前进速度，请降低基础速度或提高峰值")
        sequence = self.sequence
        self.sequence = (self.sequence + 1) & 0xffff
        self.session.send(f"B {sequence} {peak_q8}", abort=lambda: self._abort(intent))
        self.boost_used = True
        self.emit("急加速请求已发送：最多250ms；续发速度指令不等待应答")

    def step(self):
        intent = self.latest()
        intent = self._valid(intent)
        if not self._fresh(intent):
            if self.mode or self.last_servo != 750:
                self.cancel(block=True)
                self.emit("输入已过期：停车并等待松开按键")
            return
        if not intent.moving and intent.servo == 750:
            self.blocked = False
        if self.mode:
            last = self.session.last
            if not last or self.clock() - last.received > FEEDBACK_SECONDS:
                raise TimeoutError("运动状态反馈超时")
            if last.sequence > self.motion_floor and last.row["active"]:
                self.seen_active = True
            if (last.sequence > self.motion_floor and not last.row["active"] and
                    (self.seen_active or last.row["stop_reason"] or self.clock() - self.started > FEEDBACK_SECONDS)):
                reason = last.row["stop_reason"]
                if self.mode == "route" and self.route == 7 and reason == 6 and last.row["braking"]:
                    if not intent.moving:
                        self.cancel()
                    elif self.clock() - self.started > ROUTE_SECONDS[7] + 0.35:
                        raise ProtocolError("末端制动状态超时，已取消轨迹")
                    return
                self.cancel(block=True)
                self.emit("轨迹结束，请松开后重新操作" if reason == 6 else "设备已停止，请松开后重新操作")
                if reason not in (0, 6):
                    raise ProtocolError("小车保护停止，原因码 " + str(reason))
                return
            changed_mode = bool(intent.route) != (self.mode == "route")
            changed_route = intent.route and intent.route != self.route
            reversed_direction = intent.direction and intent.direction != self.direction and self.mode == "manual"
            if not intent.moving or changed_mode or changed_route or reversed_direction:
                self.cancel(block=bool(intent.moving))
                if intent.moving:
                    self.emit("已停车：切换方向或轨迹前必须松开动作")
                return
            elapsed = self.clock() - self.started
            if self.mode == "route" and elapsed >= ROUTE_SECONDS[self.route] + 0.35:
                self.cancel(block=True)
                self.emit("轨迹时限到达：请松开后重新操作")
            elif (self.clock() - self.last_sent >= 0.1 and
                  (self.mode != "route" or elapsed < ROUTE_SECONDS[self.route] - 0.25)):
                self._send_motion(intent)
            return
        if self.blocked:
            return
        if intent.moving:
            if not self._parked() or self.clock() - self.session.stopped_at < 0.55:
                return
            if not self._prepare(intent):
                return
            snapshot = intent
            self.session.command("ARM", abort=lambda: self._abort(snapshot))
            intent = self._valid(self.latest())
            # A release, brake or focus cancellation during ARM must not start D/T.
            if (not self._fresh(intent) or not intent.moving or intent.generation != snapshot.generation or
                    intent.direction != snapshot.direction or intent.route != snapshot.route):
                self.cancel(block=bool(intent.moving))
                return
            self.sequence = 0
            self.boost_used = False
            start = self.clock()
            self._send_motion(intent, starting=True)
            self.mode = "route" if intent.route else "manual"
            self.preparing = self.prepare_ready = None
            self.direction, self.route = intent.direction, intent.route
            self.started = start
            self.motion_floor = self.session.record_sequence
            self.seen_active = False
        elif intent.servo != 750:
            if self._parked() and (intent.servo != self.last_servo or self.clock() - self.steer_sent >= 0.2):
                self.session.send("S " + str(intent.servo))
                self.last_servo, self.steer_sent = intent.servo, self.clock()
        elif self.last_servo != 750:
            self.cancel()


def route_parameters(speed, slow_percent=50):
    """Q8 route targets after the caller applies the selected profile limit."""
    if not 128 <= speed <= 4096 or type(slow_percent) is not int or slow_percent not in (25, 50, 75):
        raise ValueError("无效的轨迹速度或慢段比例")
    return int(speed), max(128, round(speed * slow_percent / 100))


def intent_from_keys(keys, speed, now, route=0, generation=0, peak_q8=1536, steer_span=50, left_limit=650, right_limit=850, slow_percent=50):
    direction = int("w" in keys) - int("s" in keys)
    if not isinstance(steer_span, int) or not 10 <= steer_span <= 100:
        raise ValueError("Steering span must be an integer from 10 to 100")
    validate_steering_limits(left_limit, right_limit)
    left = 750 - round((750 - left_limit) * steer_span / 100)
    right = 750 + round((right_limit - 750) * steer_span / 100)
    servo = left if "a" in keys and "d" not in keys else right if "d" in keys and "a" not in keys else 750
    return Intent(now, direction, servo, int(speed), route, generation, peak_q8, slow_percent)
