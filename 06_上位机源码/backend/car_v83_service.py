"""RC83 adapter: one BLE owner, upstream Session/Engine, HTTP leased intent only."""
from dataclasses import dataclass, field
from pathlib import Path
import queue
import secrets
import sys
import threading
import time

from car_service import CarError
_VENDOR = str(Path(__file__).resolve().parents[1] / 'car' / 'v83')
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)
from speed_control import Engine, Intent, load_steering_limits, DEFAULT_STEERING_LIMITS
from speed_protocol import Session, CommandCancelled, FEEDBACK_SECONDS
from speed_transport import BleTransport, scan_devices


class ObservedSession(Session):
    """Observe accepted auxiliary ownership without altering upstream decoding/control."""
    def __init__(self, *args, stop_upgrade=lambda: False, **kwargs):
        super().__init__(*args, **kwargs)
        self.aux_observation = None
        self.stop_upgrade = stop_upgrade

    def _packet(self, packet):
        observation = self.last
        matches = (packet[2] >> 6 == 1 and observation is not None and
                   self.frame_tag == packet[2] & 63 and
                   observation.row['ms'] & 0xffffff == int.from_bytes(packet[3:6], 'little'))
        super()._packet(packet)
        if matches:
            self.aux_observation = observation

    def poll(self, timeout=.01):
        super().poll(timeout)
        if self.stop_upgrade():
            raise CommandCancelled('普通停车等待已升级为急刹')

    def command(self, command, timeout=.9, abort=None):
        if command == 'STOP':
            original_abort = abort
            abort = lambda: self.stop_upgrade() or bool(original_abort and original_abort())
        return super().command(command, timeout, abort)

INPUT_LEASE = .2

@dataclass
class Request:
    action: str
    args: tuple = ()
    done: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: Exception | None = None

class CarService:
    def __init__(self, *, scanner=scan_devices, transport_factory=BleTransport,
                 clock=time.monotonic, wall_clock=time.time, steering_path=None):
        self._scanner, self._factory = scanner, transport_factory
        self._clock, self._wall = clock, wall_clock
        self._lock = threading.RLock()
        self._queue = queue.PriorityQueue()
        self._order = 0
        self._thread = None
        self._closing = False
        self._suspended = True
        self._neutral = True
        self._gap = self._handled_gap = 0
        self._sequence = -1
        self._generation = 0
        self._intent = Intent(float('-inf'))
        self._devices = {}
        self._transport = self._session = self._engine = None
        self._observed = 0
        self._stop_request = None
        self._brake_upgrade = False
        self._stop_abort_enabled = False
        self._brake_waiters = []
        self._received = float('-inf')
        warning = None
        try:
            self._limits = load_steering_limits(steering_path)
        except (ValueError, OSError, TypeError) as exc:
            self._limits = DEFAULT_STEERING_LIMITS
            warning = '转向配置无效，使用确认默认620/980：' + str(exc)
        self._state = dict(protocol='RC83', connected=False, identified=False, session=None,
            phase='未连接，请先搜索蓝牙小车', error=warning, telemetry=None, received_at=0,
            profile=None, motion=None, route_id=0, preparing=False,
            steering_limits=dict(left=self._limits[0], center=750, right=self._limits[1]))

    @property
    def worker_alive(self):
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        with self._lock:
            if self._closing:
                raise CarError('小车服务已关闭')
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='car-rc83-owner', daemon=True)
                self._thread.start()

    def status(self):
        with self._lock:
            state = dict(self._state)
            state['telemetry'] = dict(state['telemetry']) if state['telemetry'] else None
            state['steering_limits'] = dict(state['steering_limits'])
            state['feedback_fresh'] = bool(state['connected'] and 0 <= self._clock()-self._received <= FEEDBACK_SECONDS)
            state['neutral_required'] = self._neutral or bool(self._engine and self._engine.blocked)
            return state

    def _authorize(self, token):
        if not token or token != self._state['session'] or not self._state['connected']:
            raise CarError('小车控制会话已失效，请使用当前连接会话')

    def _revoke(self):
        self._suspended = True
        self._neutral = True
        self._generation += 1
        self._intent = Intent(float('-inf'), generation=self._generation)

    def _latest(self):
        with self._lock:
            if (self._closing or self._suspended or self._gap != self._handled_gap or self._neutral):
                return Intent(float('-inf'), generation=self._generation)
            return self._intent

    def intent(self, session, sequence, direction, steering, speed_q8=1024, route=0,
               slow_percent=50, steer_percent=100):
        if (type(sequence) is not int or not 0 <= sequence <= 9007199254740991 or
            direction not in (None,'FWD','REV') or steering not in ('LEFT','CENTER','RIGHT') or
            type(speed_q8) is not int or not 128 <= speed_q8 <= 4096 or
            type(route) is not int or route not in range(9) or
            type(slow_percent) is not int or slow_percent not in (25,50,75) or
            type(steer_percent) is not int or not 10 <= steer_percent <= 100 or
            route and (direction is not None or steering != 'CENTER')):
            raise CarError('小车输入超出RC83约定',422)
        with self._lock:
            self._authorize(session)
            if self._suspended:
                raise CarError('正在停车或故障锁定，请先显式STOP')
            if sequence <= self._sequence:
                raise CarError('输入序号已过期，未续租')
            self._sequence = sequence
            now = self._clock()
            # Latch gaps before accepting a late release; owner must park first.
            if (self._intent.moving or self._intent.servo != 750) and now-self._intent.stamp > INPUT_LEASE:
                self._gap += 1
                self._neutral = True
                self._generation += 1
                self._intent = Intent(float('-inf'), generation=self._generation)
            neutral = direction is None and steering == 'CENTER' and route == 0
            if self._gap != self._handled_gap:
                raise CarError('输入租约已断档，等待停车后重新松开')
            if self._neutral and not neutral:
                raise CarError('请先松开全部动作并发送中性输入')
            left,right = self._limits
            servo = 750-round((750-left)*steer_percent/100) if steering=='LEFT' else 750+round((right-750)*steer_percent/100) if steering=='RIGHT' else 750
            self._intent = Intent(now, {'FWD':1,'REV':-1,None:0}[direction],servo,speed_q8,route,self._generation,slow_percent=slow_percent)
            if neutral:
                self._neutral = False
            return dict(accepted=True, session=session, sequence=sequence)

    def scan(self): return self._request('scan')
    def connect(self, device_id): return self._request('connect',device_id)
    def stop(self, session): return self._request('stop',session,priority=-1)
    def brake(self, session): return self._request('brake',session,priority=-1)
    def disconnect(self, session): return self._request('disconnect',session,priority=-1)
    def profile(self, session, profile):
        if type(profile) is not int or profile not in (0,1): raise CarError('无效速度档',422)
        return self._request('profile',session,profile)
    def boost(self, session, sequence, peak_q8):
        if type(sequence) is not int or not 0 <= sequence <= 9007199254740991 or type(peak_q8) is not int or not 1024 <= peak_q8 <= 6144:
            raise CarError('无效急加速请求',422)
        return self._request('boost',session,sequence,peak_q8)

    def _request(self, action, *args, priority=1):
        self.start()
        request = Request(action,args)
        with self._lock:
            if action not in ('scan','connect','shutdown'):
                self._authorize(args[0])
            if action in ('stop','brake','disconnect','shutdown'):
                self._revoke() # _abort sees cancellation even while ARM is waiting.
            if action=='boost':
                if args[1] <= self._sequence: raise CarError('急加速序号已过期')
                self._sequence = args[1]
            if action == 'brake' and self._stop_request and self._stop_request.args[0] == args[0]:
                self._brake_upgrade = True
                self._brake_waiters.append(request)
            else:
                if action == 'stop' and self._stop_request is None:
                    self._stop_request = request
                    self._brake_upgrade = False
                self._order += 1
                self._queue.put((priority,self._order,request))
        if not request.done.wait(20): raise CarError('请求结果未知，请查询状态；未重发',504)
        if request.error: raise request.error
        return request.result

    def _observe(self):
        s = self._session
        if not s: return
        with self._lock:
            if s.last and s.last.sequence != self._observed:
                self._observed = s.last.sequence
                self._received = s.last.received
                self._state.update(received_at=self._wall()-(self._clock()-s.last.received), profile=s.last.row['profile'])
            if s.last:
                row = dict(s.last.row)
                row['encoder_valid'] = getattr(s, 'aux_observation', None) is s.last
                if not row['encoder_valid']:
                    for key in ('left_count','right_count','left_delta5','right_delta5'):
                        row[key] = None
                self._state['telemetry'] = row
            self._state.update(identified=s.identified, motion=self._engine.mode,
                route_id=self._engine.route, preparing=bool(self._engine.preparing))

    def _rotate(self):
        with self._lock:
            self._revoke()
            self._state['session'] = secrets.token_urlsafe(24)
            self._sequence = -1
            self._handled_gap = self._gap
            self._suspended = False
            self._state.update(error=None,phase='已连接，等待松开并启用遥控')

    def _close(self):
        try:
            if self._session: self._session.close()
            elif self._transport: self._transport.close()
        finally:
            self._transport = self._session = self._engine = None
            with self._lock:
                self._revoke()
                self._state.update(connected=False,identified=False,session=None,motion=None,preparing=False,route_id=0,phase='已断开')

    def _handle(self,r):
        action,args = r.action,r.args
        if action=='scan':
            if self._transport: raise CarError('请先断开再搜索')
            devices = self._scanner()
            self._devices = {}
            rows=[]
            for item in devices:
                address = item.get('address') if isinstance(item,dict) else item.address
                name = item.get('name') if isinstance(item,dict) else item.name
                self._devices[address] = item.get('device',item) if isinstance(item,dict) else item
                rows.append(dict(id=address,address=address,name=name or '未命名BLE设备'))
            return dict(devices=rows)
        if action=='connect':
            if self._transport: raise CarError('已有连接，请先显式断开')
            if args[0] not in self._devices: raise CarError('请先搜索并选择设备')
            self._transport = self._factory(self._devices[args[0]])
            self._session = ObservedSession(self._transport,clock=self._clock,
                stop_upgrade=lambda: self._stop_abort_enabled and self._brake_upgrade)
            self._engine = Engine(self._session,self._latest,clock=self._clock)
            self._observed = 0
            try:
                self._session.handshake()
            except Exception:
                self._close()
                raise
            with self._lock: self._state['connected']=True
            self._rotate()
        elif action in ('stop','brake','disconnect','shutdown'):
            if action != 'shutdown':
                with self._lock: self._authorize(args[0])
            if self._session:
                if not self._session.ready and getattr(self._transport,'online',True):
                    self._session.reset_handshake()
                    self._observed=0
                    self._session.handshake()
                self._stop_abort_enabled = action == 'stop' and r is self._stop_request
                try:
                    self._engine.cancel(brake=action=='brake',block=True)
                except CommandCancelled:
                    if action != 'stop' or not self._brake_upgrade:
                        raise
                finally:
                    self._stop_abort_enabled = False
            if action in ('disconnect','shutdown'): self._close()
            elif action == 'stop':
                # The original token owns this one parking transaction, including
                # emergency upgrades accepted before the atomic rotation boundary.
                brake_sent = False
                while True:
                    with self._lock:
                        upgrade = self._brake_upgrade and r is self._stop_request
                        if not upgrade or brake_sent:
                            self._rotate()
                            break
                    self._engine.cancel(brake=True,block=True)
                    brake_sent = True
            else: self._rotate()
        elif action in ('profile','boost'):
            with self._lock:
                self._authorize(args[0]) # queued old tokens cannot act after STOP.
                if self._suspended: raise CarError('遥控已取消')
                if action=='boost' and (args[1] != self._sequence or not self._latest().moving):
                    raise CarError('急加速意图已失效')
            if action=='profile': self._engine.set_profile(args[1])
            else: self._engine.boost(args[2])
        self._observe()
        return self.status()

    def _fault(self,exc):
        with self._lock:
            self._revoke()
            self._state.update(session=secrets.token_urlsafe(24),error=str(exc),phase='故障锁定，请显式STOP确认停车')
            self._sequence=-1
        if not getattr(self._transport,'online',not getattr(self._transport,'closed',True)):
            self._close()
            with self._lock: self._state['error']=str(exc)
        elif self._session:
            # A live GATT fault gets one upstream STOP/readback attempt, never motion retry.
            if self._session.ready:
                try:
                    self._engine.cancel(block=True)
                except Exception as park_error:
                    with self._lock:
                        self._state['error'] = str(exc) + '；停车未确认：' + str(park_error)
            self._session.ready=False

    def _run(self):
        while True:
            try:
                _,_,r = self._queue.get(timeout=.01)
            except queue.Empty: r=None
            if r:
                try: r.result=self._handle(r)
                except Exception as exc:
                    r.error=exc if isinstance(exc,CarError) else CarError(str(exc),502)
                    if self._session and not isinstance(exc,(ValueError,CarError)):
                        self._fault(exc)
                    if r.action == 'shutdown' or (r.action == 'disconnect' and not isinstance(exc,CarError)):
                        self._close()
                finally:
                    with self._lock:
                        if r is self._stop_request:
                            for waiter in self._brake_waiters:
                                waiter.result, waiter.error = r.result, r.error
                                waiter.done.set()
                            self._brake_waiters = []
                            self._stop_request = None
                            self._brake_upgrade = False
                        r.done.set()
                if r.action=='shutdown': return
            if self._session:
                try:
                    if not getattr(self._transport,'online',not getattr(self._transport,'closed',False)):
                        raise ConnectionError('蓝牙已断开，请显式重新搜索连接')
                    self._session.poll(.005)
                    with self._lock:
                        expired = (self._intent.moving or self._intent.servo != 750) and self._clock()-self._intent.stamp > INPUT_LEASE
                        if expired:
                            self._gap+=1
                            self._neutral=True
                            self._intent=Intent(float('-inf'),generation=self._generation)
                        gap=self._gap
                    if gap != self._handled_gap:
                        self._engine.cancel(block=True)
                        with self._lock:
                            self._handled_gap=gap
                            self._neutral=True
                    if not self._suspended:
                        if self._session.last and self._clock()-self._session.last.received > FEEDBACK_SECONDS:
                            raise TimeoutError('状态反馈中断，已取消动作')
                        self._engine.step()
                    self._observe()
                except CommandCancelled:
                    # Cancellation preempts ARM; queued priority STOP performs confirmation.
                    pass
                except Exception as exc:
                    if not self._suspended or not getattr(self._transport,'online',True):
                        self._fault(exc)

    def shutdown(self):
        if not self._thread:
            self._closing=True
            return
        if self._closing: return
        try:
            self._request('shutdown',priority=-2)
        finally:
            self._closing=True
            self._thread.join(5)
        if self._thread.is_alive(): raise CarError('工作线程关闭结果未知',504)
