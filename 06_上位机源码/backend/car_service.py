"""One owner thread for the delivered BLE driver; HTTP only publishes leased input."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import queue
import secrets
import sys
import threading
import time
from typing import Callable

# The delivered Bluetooth module imports its sibling by its original module name.
# Keep both source files byte-for-byte intact, including that import contract.
_VENDOR = str(Path(__file__).resolve().parents[1] / 'car' / 'vendor')
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)
from bluetooth_client import BleCar, scan_devices
from wireless_client import MotorFaultCandidate, control_step, is_motor_fault, new_control, parse_record

INPUT_LEASE = .2
FEEDBACK_LEASE = .5


class CarError(RuntimeError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class _Request:
    action: str
    argument: str | None = None
    done: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: Exception | None = None


class CarService:
    def __init__(self, *, scanner: Callable = scan_devices, car_factory: Callable = BleCar,
                 clock: Callable = time.monotonic, wall_clock: Callable = time.time):
        self._scanner, self._factory = scanner, car_factory
        self._clock, self._wall_clock = clock, wall_clock
        self._lock = threading.RLock()
        self._queue = queue.PriorityQueue()
        self._request_sequence = 0
        self._thread = None
        self._closing = False
        self._suspended = True
        self._devices = {}
        self._car = None  # Accessed only by the BLE owner thread.
        self._control = None
        self._intent = (None, 'CENTER', float('-inf'))
        self._sequence = -1
        self._needs_neutral = True
        self._released = False
        self._lease_gaps = 0
        self._handled_gap = 0
        self._stops_sent = 0
        self._received_mono = float('-inf')
        self._state = dict(connected=False, identified=False, telemetry=None, received_at=0,
                           session=None, phase='未连接，请先搜索蓝牙小车', error=None)

    @property
    def worker_alive(self):
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def start(self):
        with self._lock:
            if self._closing:
                raise CarError('小车服务已关闭')
            if not self._thread:
                self._thread = threading.Thread(target=self._run, name='car-ble-owner', daemon=True)
                self._thread.start()

    def status(self):
        with self._lock:
            state = dict(self._state)
            if state['telemetry'] is not None:
                state['telemetry'] = dict(state['telemetry'])
            state['feedback_fresh'] = bool(state['connected'] and
                0 <= self._clock() - self._received_mono <= FEEDBACK_LEASE)
            state['neutral_required'] = self._needs_neutral
            return state

    def _session_locked(self, session):
        if not isinstance(session, str) or not session or session != self._state['session']:
            raise CarError('小车控制会话已失效，请使用当前连接会话')
        if not self._state['connected']:
            raise CarError('小车尚未连接并完成身份与停车确认')

    def intent(self, session, sequence, direction, steering):
        # No driver call, queue, sleep, or ACK wait is permitted in this path.
        if type(sequence) is not int or not 0 <= sequence <= 9007199254740991:
            raise CarError('输入序号必须是非负安全整数', 422)
        if direction not in (None, 'FWD', 'REV') or steering not in ('LEFT', 'CENTER', 'RIGHT'):
            raise CarError('小车方向或转向输入无效', 422)
        with self._lock:
            self._session_locked(session)
            if self._closing or self._suspended:
                raise CarError('正在停车或断开，遥控输入已取消')
            if sequence <= self._sequence:
                raise CarError('输入序号已过期，未续租')
            # Rejected held input must not become replayable after a later release.
            # A new sequence is observed without renewing its input lease yet.
            self._sequence = sequence
            now = self._clock()
            if not 0 <= now - self._intent[2] <= INPUT_LEASE:
                self._needs_neutral = True
                self._released = False
                self._lease_gaps += 1
            neutral = direction is None and steering == 'CENTER'
            if self._needs_neutral and not neutral:
                raise CarError('请先松开全部方向键并发送中性输入，再重新操作')
            self._intent = (direction, steering, now)
            if neutral:
                self._needs_neutral = False
                self._released = True
                self._state.update(error=None, phase='已连接，等待遥控输入')
            return dict(accepted=True, session=session, sequence=sequence)

    def scan(self):
        return self._request('scan')

    def connect(self, device_id):
        if not isinstance(device_id, str) or not device_id:
            raise CarError('请先搜索并选择蓝牙设备', 422)
        return self._request('connect', device_id)

    def stop(self, session):
        return self._request('stop', session, priority=-1)

    def disconnect(self, session):
        return self._request('disconnect', session, priority=-1)

    def _request(self, action, argument=None, priority=1):
        self.start()
        request = _Request(action, argument)
        with self._lock:
            if self._closing:
                raise CarError('小车服务正在关闭')
            if action in ('stop', 'disconnect'):
                self._session_locked(argument)
                # Invalidate in-flight ARM/KEEP checks immediately, before any ACK wait ends.
                self._suspended = True
                self._needs_neutral = True
                self._released = False
                self._intent = (None, 'CENTER', float('-inf'))
                self._state['phase'] = '正在停车并确认新静止反馈'
            self._request_sequence += 1
            self._queue.put((priority, self._request_sequence, request))
        if not request.done.wait(15):
            # The request is never retried: a caller timeout is an unknown result.
            raise CarError('小车请求结果未知，请查询状态；请求未重发', 504)
        if request.error:
            raise request.error
        return request.result

    def shutdown(self):
        with self._lock:
            if self._closing:
                thread = self._thread
            else:
                self._closing = True
                self._suspended = True
                self._needs_neutral = True
                self._intent = (None, 'CENTER', float('-inf'))
                thread = self._thread
                if thread:
                    self._request_sequence += 1
                    self._queue.put((-2, self._request_sequence, _Request('shutdown')))
        if thread and thread is not threading.current_thread():
            thread.join(15)
            if thread.is_alive():
                raise CarError('小车工作线程未能在关闭期限内结束，停车结果未知', 504)

    def _emit(self, line):
        # Only delivered-parser-compatible CAR2 lines become feedback. No command
        # or cached last_record can advance these independent receive timestamps.
        if line == 'TX STOP':
            self._stops_sent += 1
        row = parse_record(line)
        if row is not None:
            with self._lock:
                self._received_mono = self._clock()
                self._state.update(telemetry=row.copy(), received_at=self._wall_clock())

    def _get_intent(self):
        with self._lock:
            if (self._closing or self._suspended or self._needs_neutral or
                    self._lease_gaps != self._handled_gap):
                return None, 'CENTER', float('-inf')
            return self._intent

    def _park(self, candidate=None):
        result = self._car.stop_and_confirm(candidate)
        blocked = self._control['blocked'] if self._control else True
        self._control = new_control(self._clock(), max_run=None)
        self._control['blocked'] = blocked
        return result

    def _drop(self, error=None):
        with self._lock:
            self._suspended = True
            self._needs_neutral = True
            self._released = False
            self._handled_gap = self._lease_gaps
            self._intent = (None, 'CENTER', float('-inf'))
            self._state['phase'] = '正在停车并断开'
        close_error = None
        if self._car:
            try:
                self._car.close()  # Delivered close includes STOP + fresh readback.
            except Exception as exc:
                close_error = '停车未确认：' + str(exc)
            finally:
                self._car = None
                self._control = None
        if close_error:
            error = (error + '；' if error else '') + close_error
        with self._lock:
            self._state.update(connected=False, identified=False, session=None,
                               phase='已断开' if error is None else '连接已关闭，请检查错误', error=error)
        return error

    def _handle(self, request):
        action = request.action
        if action == 'scan':
            with self._lock:
                if self._state['connected']:
                    raise CarError('请先停车并断开当前连接，再搜索设备')
                self._state.update(phase='正在搜索蓝牙设备', error=None)
                self._devices = {}
            try:
                rows = self._scanner()
            except Exception as exc:
                with self._lock:
                    self._state.update(phase='搜索失败', error='蓝牙设备搜索失败：' + str(exc))
                raise CarError('蓝牙设备搜索失败：' + str(exc), 502) from exc
            with self._lock:
                self._devices = {row['address']: row for row in rows}
                self._state['phase'] = '搜索完成，请选择设备并连接'
            return dict(devices=[dict(id=key, name=row['name']) for key, row in self._devices.items()])
        if action == 'connect':
            with self._lock:
                if self._state['connected']:
                    raise CarError('已有小车连接，请先显式断开')
                target = self._devices.get(request.argument)
                if target is None:
                    raise CarError('所选设备不是本次显式搜索结果，请重新搜索并选择')
                self._state.update(phase='正在连接并验证 GATT、HOLD_V2 身份与停车反馈',
                                   error=None, telemetry=None, received_at=0)
                self._received_mono = float('-inf')
            try:
                self._car = self._factory(target['device'], self._emit)
                self._car.synchronize_stream()
                self._car.identify()
                if not self._car.combined_supported or not self._car.hold_supported:
                    raise RuntimeError('仅支持当前 BLUETOOTH_HOLD_V2 固件')
                self._control = new_control(self._clock(), max_run=None)
                self._park()
                with self._lock:
                    if self._closing:
                        raise CarError('服务正在关闭，已取消连接')
                    self._sequence = -1
                    self._intent = (None, 'CENTER', float('-inf'))
                    self._needs_neutral = True
                    self._released = False
                    self._handled_gap = self._lease_gaps
                    self._suspended = False
                    self._state.update(connected=True, identified=True, session=secrets.token_urlsafe(24),
                                       phase='已连接并确认停车，请手动启用遥控', error=None)
                return self.status()
            except Exception as exc:
                error = self._drop('小车连接验证失败：' + str(exc))
                raise CarError(error, 502) from exc
        with self._lock:
            self._session_locked(request.argument)  # A queued old STOP cannot stop a later token.
        if action == 'disconnect':
            error = self._drop()
            if error:
                raise CarError(error, 502)
            return self.status()
        if action == 'stop':
            try:
                self._park()
            except Exception as exc:
                error = self._drop('停车未确认：' + str(exc))
                raise CarError(error, 502) from exc
            with self._lock:
                self._sequence = -1
                self._suspended = False
                self._needs_neutral = True
                self._released = False
                self._handled_gap = self._lease_gaps
                self._state.update(session=secrets.token_urlsafe(24), error=None,
                                   phase='已确认停车并回正，请松开输入后手动启用遥控')
            return self.status()
        raise CarError('未知小车操作', 400)

    def _tick(self):
        try:
            self._car.poll(.01)
            now = self._clock()
            with self._lock:
                leased = 0 <= now - self._intent[2] <= INPUT_LEASE
                if not leased:
                    if not self._needs_neutral:
                        self._lease_gaps += 1
                    self._needs_neutral = True
                observed_gap = self._lease_gaps
                # Remember an accepted full release even if the next HTTP input
                # arrives before this worker tick. No BLE state is mutated by HTTP.
                if (self._released and not self._needs_neutral and not self._suspended and
                        self._lease_gaps == self._handled_gap):
                    self._control['blocked'] = False
                    self._released = False
            was_active, was_steer = self._control['active'], self._control['steer']
            stops_before = self._stops_sent
            control_step(self._control, self._car, self._get_intent, now, clock=self._clock,
                         keepalive=self._car.keepalive, start_drive=self._car.start_drive)
            # The original control_step STOP acknowledgement is not parking proof.
            if (self._stops_sent != stops_before or
                    (was_active and not self._control['active']) or
                    (was_steer != 'CENTER' and self._control['steer'] == 'CENTER')):
                self._park()
            with self._lock:
                self._handled_gap = observed_gap
                if self._needs_neutral and not self._suspended:
                    self._state['phase'] = '已停车，等待松开输入并重新启用'
                elif self._control['active']:
                    self._state['phase'] = '遥控输入有效，运行状态以 CAR2 反馈为准'
        except MotorFaultCandidate as candidate:
            with self._lock:
                self._suspended = True
                self._needs_neutral = True
                self._intent = (None, 'CENTER', float('-inf'))
            try:
                result = self._park(candidate)
                fault = result.get('fault')
                if fault is None or not is_motor_fault(fault):
                    raise RuntimeError('缺少当前动作的已确认电机故障证据')
                with self._lock:
                    self._suspended = False
                    self._state.update(phase='电机故障已确认停车，请松开输入',
                        error=f"电机故障已确认停车；停止原因={fault['stop_reason']}，请松开全部输入")
            except Exception as exc:
                self._drop('电机故障停车确认失败：' + str(exc))
        except Exception as exc:
            self._drop('小车连接或动作异常：' + str(exc))

    def _run(self):
        try:
            while True:
                try:
                    _, _, request = self._queue.get(timeout=.005 if self._car else .1)
                except queue.Empty:
                    request = None
                if request:
                    if request.action == 'shutdown':
                        break
                    try:
                        with self._lock:
                            if self._closing:
                                raise CarError('小车服务正在关闭，操作已取消')
                        request.result = self._handle(request)
                    except Exception as exc:
                        request.error = exc if isinstance(exc, CarError) else CarError('小车操作失败：' + str(exc), 502)
                    finally:
                        request.done.set()
                elif self._car:
                    self._tick()
        finally:
            self._drop()
            while True:
                try:
                    _, _, request = self._queue.get_nowait()
                except queue.Empty:
                    break
                request.error = CarError('小车服务已关闭，操作已取消')
                request.done.set()
