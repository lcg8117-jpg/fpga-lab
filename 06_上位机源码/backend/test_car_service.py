"""Explicit offline transport fixtures; these tests never scan or open Bluetooth."""
from collections import deque
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'car' / 'vendor'))
from bluetooth_client import _AsciiCar, ACKS, COMBINED_IDENTITY, HOLD_IDENTITY
from wireless_client import FIELDS

try:
    from car_service import CarService, CarError
except ImportError:
    CarService = None
    CarError = RuntimeError


class Clock:
    def __init__(self):
        self.value = 10.0
    def __call__(self):
        return self.value
    def advance(self, seconds):
        self.value += seconds


class FixtureCar(_AsciiCar):
    """Scripted physical-link substitute using the delivered parser and commands."""
    def __init__(self, emit, *, identity=COMBINED_IDENTITY, stop_readback=True):
        self._init_stream(emit)
        self.identity = identity
        self.stop_readback = stop_readback
        self.pending = deque()
        self.calls = []
        self.owner = threading.get_ident()
        self.feedback = True
        self.row = dict(zip(FIELDS, [123, 17, -19, 0, 0, 0, 0, 0, 0, 750, 0, 0]))
        self.poll_error = None
        self.arm_entered = None
        self.arm_release = None
        self.pause_poll = None
        self.on_command = None

    def note(self, name):
        if threading.get_ident() != self.owner:
            raise AssertionError('BLE transport called from another thread')
        self.calls.append(name)

    def synchronize_stream(self):
        self.note('synchronize')

    def record(self):
        return 'CAR2,' + ','.join(str(self.row[key]) for key in FIELDS) + '\r\n'

    def _write(self, data):
        cmd = data.decode('ascii').strip()
        self.note(cmd)
        if self.on_command:
            self.on_command(cmd)
        if cmd == 'ARM' and self.arm_entered:
            self.arm_entered.set()
            self.arm_release.wait(1)
        reply = self.identity if cmd == 'STATUS' else ACKS[cmd]
        self.pending.append((reply + '\r\n').encode('ascii'))
        if cmd == 'STOP':
            self.row.update(active=0, left_target5=0, right_target5=0,
                            left_pwm8400=0, right_pwm8400=0, servo_compare=750, stop_reason=0)
            if self.stop_readback:
                self.pending.append(self.record().encode('ascii'))
        elif cmd.startswith(('FWD', 'REV')):
            sign = 1 if cmd.startswith('FWD') else -1
            self.row.update(active=1, left_target5=sign*3, right_target5=sign*3,
                            left_pwm8400=sign*1680, right_pwm8400=sign*1680)
        elif cmd in ('LEFT', 'CENTER', 'RIGHT') or cmd.startswith('KEEP'):
            steering = cmd[4:] if cmd.startswith('KEEP') else cmd
            self.row['servo_compare'] = {'LEFT':700, 'CENTER':750, 'RIGHT':800}[steering]

    def poll(self, timeout=.02):
        self.note('poll')
        time.sleep(.001)
        if self.pause_poll:
            entered, release = self.pause_poll
            entered.set()
            release.wait(1)
            self.pause_poll = None
        if self.poll_error:
            error, self.poll_error = self.poll_error, None
            raise error
        lines = []
        while self.pending:
            lines.extend(self._consume(self.pending.popleft()))
        if self.feedback and self.stop_readback:
            lines.extend(self._consume(self.record().encode('ascii')))
        return lines

    def _close_transport(self):
        self.note('close_transport')


def until(predicate, timeout=1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.002)
    raise AssertionError('Timed out waiting for fixture worker')


class CarServiceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(CarService, '缺少独占 BLE 工作线程的小车服务')
        self.clock = Clock()
        self.cars = []
        self.scans = []
        self.options = {}
        def scan():
            self.scans.append(threading.get_ident())
            return [dict(device=object(), address='fixture-id', name='离线 fixture 小车')]
        def factory(device, emit):
            car = FixtureCar(emit, **self.options)
            self.cars.append(car)
            return car
        self.service = CarService(scanner=scan, car_factory=factory, clock=self.clock,
                                  wall_clock=lambda: 1700000000 + self.clock())
        self.addCleanup(self.service.shutdown)

    def connect(self):
        self.service.scan()
        return self.service.connect('fixture-id')['session']

    def intent(self, session, seq, direction=None, steering='CENTER'):
        return self.service.intent(session, seq, direction, steering)

    def drive(self):
        session = self.connect()
        self.intent(session, 1)
        self.clock.advance(.6)
        until(lambda: self.service.status()['feedback_fresh'])
        self.intent(session, 2)
        self.intent(session, 3, 'FWD')
        until(lambda: 'FWDCENTER' in self.cars[-1].calls)
        return session

    def test_default_status_never_scans_or_connects(self):
        status = self.service.status()
        self.assertFalse(status['connected'])
        self.assertFalse(status['feedback_fresh'])
        self.assertIsNone(status['telemetry'])
        self.assertEqual(self.scans, [])
        self.assertEqual(self.cars, [])

    def test_only_explicit_scan_results_can_be_selected(self):
        with self.assertRaises(CarError):
            self.service.connect('fixture-id')
        self.assertEqual(self.cars, [])
        self.assertEqual(self.service.scan()['devices'],
                         [dict(id='fixture-id', name='离线 fixture 小车')])

    def test_connect_requires_v2_and_own_fresh_stop_readback(self):
        session = self.connect()
        status = self.service.status()
        self.assertTrue(status['identified'])
        self.assertTrue(status['connected'])
        self.assertTrue(status['feedback_fresh'])
        self.assertEqual(status['telemetry']['left_count'], 17)
        self.assertGreaterEqual(self.cars[-1].calls.count('STOP'), 2)
        self.assertNotIn('ARM', self.cars[-1].calls)
        self.assertTrue(session)
        self.assertEqual(set(self.scans), {self.cars[-1].owner})

    def test_legacy_hold_identity_is_rejected_and_closed(self):
        self.options['identity'] = HOLD_IDENTITY
        self.service.scan()
        with self.assertRaises(CarError):
            self.service.connect('fixture-id')
        self.assertFalse(self.service.status()['connected'])
        self.assertIn('close_transport', self.cars[-1].calls)

    def test_stop_ack_without_new_parked_car2_is_not_connection_success(self):
        self.options['stop_readback'] = False
        self.service.scan()
        with self.assertRaises(CarError):
            self.service.connect('fixture-id')
        self.assertFalse(self.service.status()['connected'])
        self.assertIn('close_transport', self.cars[-1].calls)

    def test_input_lease_expires_without_background_renewal_and_requires_neutral(self):
        session = self.drive()
        car = self.cars[-1]
        self.clock.advance(.201)
        until(lambda: not car.row['active'])
        calls = len([x for x in car.calls if x.startswith('KEEP')])
        time.sleep(.025)
        self.assertEqual(len([x for x in car.calls if x.startswith('KEEP')]), calls)
        with self.assertRaises(CarError):
            self.intent(session, 4, 'FWD')
        self.intent(session, 5)
        self.assertTrue(self.service.status()['connected'])

    def test_steering_lease_also_stops_and_centers(self):
        session = self.connect()
        self.intent(session, 1)
        self.intent(session, 2, steering='LEFT')
        until(lambda: self.cars[-1].row['servo_compare'] == 700)
        self.clock.advance(.201)
        until(lambda: self.cars[-1].row['servo_compare'] == 750)
        until(lambda: self.cars[-1].calls.count('STOP') >= 3)
        self.assertGreaterEqual(self.cars[-1].calls.count('STOP'), 3)

    def test_duplicate_sequence_does_not_extend_input_lease(self):
        session = self.drive()
        self.clock.advance(.15)
        with self.assertRaises(CarError):
            self.intent(session, 3, 'FWD')
        self.clock.advance(.06)
        until(lambda: not self.cars[-1].row['active'])

    def test_rejected_non_neutral_input_still_prevents_older_input_replay(self):
        session = self.connect()
        with self.assertRaises(CarError):
            self.intent(session, 10, 'FWD')
        with self.assertRaises(CarError):
            self.intent(session, 9)
        self.intent(session, 11)
        with self.assertRaises(CarError):
            self.intent(session, 10, 'FWD')

    def test_expired_input_cannot_be_replaced_before_worker_observes_expiry(self):
        session = self.connect()
        self.intent(session, 1)
        entered, release = threading.Event(), threading.Event()
        self.cars[-1].pause_poll = (entered, release)
        self.assertTrue(entered.wait(1))
        self.clock.advance(.201)
        try:
            with self.assertRaises(CarError):
                self.intent(session, 2, 'FWD')
        finally:
            release.set()

    def test_late_neutral_and_new_drive_cannot_hide_a_missed_lease_stop(self):
        session = self.drive()
        car = self.cars[-1]
        entered, release = threading.Event(), threading.Event()
        car.pause_poll = (entered, release)
        self.assertTrue(entered.wait(1))
        stops = car.calls.count('STOP')
        self.clock.advance(.201)
        self.intent(session, 4)
        self.intent(session, 5, 'FWD')
        release.set()
        until(lambda: car.calls.count('STOP') > stops)
        until(lambda: not car.row['active'])
        self.assertEqual(car.calls.count('ARM'), 1)

    def test_car2_may_be_absent_while_ack_and_leased_manual_control_continue(self):
        session = self.connect()
        car = self.cars[-1]
        car.feedback = False
        time.sleep(.01)
        received = self.service.status()['received_at']
        self.clock.advance(.6)
        self.intent(session, 1)
        self.intent(session, 2, 'FWD')
        until(lambda: 'FWDCENTER' in car.calls)
        self.clock.advance(.101)
        self.intent(session, 3, 'FWD')
        until(lambda: 'KEEPCENTER' in car.calls)
        self.assertTrue(self.service.status()['connected'])
        self.assertFalse(self.service.status()['feedback_fresh'])
        self.assertEqual(self.service.status()['received_at'], received)
        self.clock.advance(.201)
        until(lambda: not car.row['active'])
        with self.assertRaises(CarError):
            self.intent(session, 4, 'FWD')

    def test_unknown_stop_reason_is_not_treated_as_recoverable_motor_fault(self):
        self.drive()
        self.cars[-1].row.update(active=0, left_target5=0, right_target5=0,
                                left_pwm8400=0, right_pwm8400=0, stop_reason=7)
        until(lambda: not self.service.status()['connected'])
        self.assertIn('reason=7', self.service.status()['error'])

    def test_stop_rotates_token_and_old_stop_and_input_cannot_affect_new_session(self):
        old = self.drive()
        status = self.service.stop(old)
        current = status['session']
        self.assertNotEqual(old, current)
        self.assertTrue(status['connected'])
        with self.assertRaises(CarError):
            self.intent(old, 1000, 'FWD')
        before = self.cars[-1].calls.count('STOP')
        with self.assertRaises(CarError):
            self.service.stop(old)
        self.assertEqual(self.cars[-1].calls.count('STOP'), before)
        with self.assertRaises(CarError):
            self.intent(current, 1, 'FWD')
        self.intent(current, 2)

    def test_feedback_freshness_uses_receive_time_not_cached_record(self):
        self.connect()
        self.cars[-1].feedback = False
        time.sleep(.01)
        received = self.service.status()['received_at']
        self.clock.advance(.6)
        status = self.service.status()
        self.assertTrue(status['connected'])
        self.assertFalse(status['feedback_fresh'])
        self.assertEqual(status['received_at'], received)
        self.assertEqual(status['telemetry']['left_count'], 17)

    def test_unknown_transport_failure_parks_and_closes_without_reconnect(self):
        self.drive()
        self.cars[-1].poll_error = RuntimeError('fixture corrupt frame')
        until(lambda: not self.service.status()['connected'])
        self.assertIn('fixture corrupt frame', self.service.status()['error'])
        self.assertIn('close_transport', self.cars[-1].calls)
        self.assertEqual(len(self.cars), 1)

    def test_failed_stop_confirmation_closes_link_and_invalidates_session(self):
        session = self.connect()
        self.cars[-1].stop_readback = False
        with self.assertRaises(CarError):
            self.service.stop(session)
        status = self.service.status()
        self.assertFalse(status['connected'])
        self.assertIsNone(status['session'])
        self.assertIn('停车未确认', status['error'])

    def test_disconnect_and_reconnect_never_accepts_previous_control_token(self):
        old = self.connect()
        self.service.disconnect(old)
        new = self.service.connect('fixture-id')['session']
        self.assertNotEqual(old, new)
        with self.assertRaises(CarError):
            self.intent(old, 900, 'FWD')
        with self.assertRaises(CarError):
            self.service.disconnect(old)
        self.assertTrue(self.service.status()['connected'])

    def test_known_motor_fault_keeps_link_only_after_confirmed_parking(self):
        session = self.drive()
        car = self.cars[-1]
        car.row.update(active=0, left_target5=0, right_target5=0,
                       left_pwm8400=0, right_pwm8400=0, stop_reason=2)
        until(lambda: '故障' in (self.service.status()['error'] or ''))
        self.assertTrue(self.service.status()['connected'])
        with self.assertRaises(CarError):
            self.intent(session, 4, 'FWD')
        self.intent(session, 5)

    def test_stop_cancels_intent_while_arm_ack_is_waiting(self):
        session = self.connect()
        self.intent(session, 1)
        self.clock.advance(.6)
        until(lambda: self.service.status()['feedback_fresh'])
        self.intent(session, 2)
        car = self.cars[-1]
        car.arm_entered, car.arm_release = threading.Event(), threading.Event()
        self.intent(session, 3, 'FWD')
        self.assertTrue(car.arm_entered.wait(1))
        result = []
        thread = threading.Thread(target=lambda: result.append(self.service.stop(session)))
        thread.start()
        time.sleep(.01)
        car.arm_release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result[0]['connected'])
        self.assertNotIn('FWDCENTER', car.calls)

    def test_lease_expiry_during_arm_requires_stop_readback_even_without_active_state(self):
        session = self.connect()
        self.intent(session, 1)
        self.clock.advance(.6)
        until(lambda: self.service.status()['feedback_fresh'])
        self.intent(session, 2)
        car = self.cars[-1]
        def delay_arm(cmd):
            if cmd == 'ARM':
                self.clock.advance(.201)
        car.on_command = delay_arm
        self.intent(session, 3, 'FWD')
        until(lambda: car.calls.count('STOP') >= 4)
        self.assertNotIn('FWDCENTER', car.calls)

    def test_lease_expiry_during_drive_ack_does_not_accept_stop_ack_as_parking(self):
        session = self.connect()
        self.intent(session, 1)
        self.clock.advance(.6)
        until(lambda: self.service.status()['feedback_fresh'])
        self.intent(session, 2)
        car = self.cars[-1]
        def lose_readback(cmd):
            if cmd == 'FWDCENTER':
                self.clock.advance(.201)
                car.stop_readback = False
        car.on_command = lose_readback
        self.intent(session, 3, 'FWD')
        until(lambda: not self.service.status()['connected'], timeout=2)
        self.assertIn('close_transport', car.calls)
        self.assertIn('fresh stopped state', self.service.status()['error'])

    def test_shutdown_confirms_stop_and_closes_on_same_owner_thread(self):
        self.drive()
        car = self.cars[-1]
        self.service.shutdown()
        self.assertFalse(self.service.status()['connected'])
        self.assertIn('close_transport', car.calls)
        self.assertFalse(self.service.worker_alive)


if __name__ == '__main__':
    unittest.main()
