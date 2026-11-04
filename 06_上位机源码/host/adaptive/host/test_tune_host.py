"""Offline real-frame and adversarial flow checks. Never opens COM."""
import json
import io
import struct
import threading
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import urllib.error
import urllib.request
from pathlib import Path
from tune_protocol import crc16, decode, encode_ack, request, StreamDecoder, USER_CODE
from tune_parameters import PARAMETERS, HISTORICAL_PID, BY_ID, SWING_PARAMETERS
from tune_model import Controller
from tune_transport import Outbox
from swing_tune_host import Engine, start_bridge, phase_description, CSV_FIELDS
import swing_tune_cli


class Fixture:
    def __init__(self):
        self.now = 10.0
        self.sent = []
        self.cancelled = 0
        self.revision = 0
        self.highwater = 99
        self.mode, self.fault, self.flags, self.pwm, self.state = 1, 0, 0xC1, 0, 0
        self.values = {p[0]: p[4] for p in PARAMETERS}
        self.values[0x20] = 900
        self.model = Controller(self.send, self.cancel, lambda: self.now)
        self.model.opened()
        self.answer()
        self.model.parameters = {p[1]: {'value': self.values[p[0]], 'revision': 0} for p in PARAMETERS}

    def send(self, packet, cid, stop):
        self.sent.append((packet, cid, stop))

    def cancel(self):
        self.cancelled += 1

    def status(self):
        return struct.pack('<BBBBhBBII', self.mode, self.fault, self.flags, self.state,
                           self.pwm, 0, 0, self.highwater, self.revision)

    def answer(self, override=None, status=0, reason=0):
        p = self.model.pending
        if p is None:
            raise AssertionError('No request pending')
        self.model.sent(p['cid'], self.now)
        self.now += 0.02
        op, payload = p['op'], p['payload']
        if override is not None:
            data = override
        elif op == 1:
            page = payload[0]
            data = (bytes(16) if page >=4 else self.status() if page == 0 else
                    struct.pack('<Iiii', 200, -1048576, 2, 3) if page == 1 else
                    struct.pack('<IihBBBBBB', 201, -99, -29491, 1, 0, 0xC1, 0, 0, 0) if page == 2 else
                    struct.pack('<BBHHHIBBBB', 13, 1, 42, 1000, 220, 202, 1, 0, 1, 0))
        elif op in (0x70, 0x71):
            if op == 0x70 and status == 0:
                self.values[payload[0]] = int.from_bytes(payload[1:], 'little')
                self.revision += 1
                self.highwater = p['cid']
            data = struct.pack('<BI', payload[0], self.values[payload[0]])
        else:
            data = b''
            if status == 0:
                if op:
                    self.highwater = p['cid']
                if op == 0:
                    self.flags, self.pwm = (self.flags | 0xC0) & ~6, 0
                if op == 4:
                    self.fault, self.mode = 0, 1
        return self.model.receive(decode(encode_ack(p['cid'], op, self.revision, data, status, reason)), self.now)

    def tick(self, advance=0.06):
        self.now += advance
        self.model.tick()


class FramingTests(unittest.TestCase):
    def test_crc_known_vector_and_command_layout(self):
        self.assertEqual(USER_CODE, 0x53413231)
        self.assertEqual(crc16(b'123456789'), 0x29B1)
        raw = request(123, 0x70, struct.pack('<BI', 0, 80))
        self.assertEqual(len(raw), 26)
        self.assertEqual(raw[:4], b'J2C3')
        self.assertEqual(struct.unpack_from('<HIIIB', raw, 4), (26, USER_CODE, 0x53415421, 123, 0x70))
        self.assertEqual(crc16(raw[:-2]), int.from_bytes(raw[-2:], 'little'))

    def test_real_ack_crc_identity_and_partial_resync(self):
        raw = encode_ack(3, 1, 4, bytes(16))
        bad = bytearray(raw)
        bad[31] ^= 1
        with self.assertRaises(ValueError):
            decode(bytes(bad))
        bad = bytearray(raw)
        bad[6] ^= 1
        bad[-2:] = struct.pack('<H', crc16(bad[:-2]))
        with self.assertRaises(ValueError):
            decode(bytes(bad))
        decoder = StreamDecoder()
        self.assertEqual(list(decoder.feed(b'noise'+raw[:13])), [])
        frames = list(decoder.feed(raw[13:]+bytes(bad)+raw))
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].values['revision'], 4)

    def test_bad_lengths_and_readback_rejected(self):
        raw = bytearray(encode_ack(1, 1, 0))
        raw[25] = 17
        raw[-2:] = struct.pack('<H', crc16(raw[:-2]))
        with self.assertRaises(ValueError):
            decode(bytes(raw))
        with self.assertRaises(ValueError):
            request(-1, 0)

    def test_swm1_ack_rejected_even_with_correct_crc(self):
        for uid, schema in ((0x53574D31,0x53575401), (0x53574D31,0x53415421), (0x53413231,0x53575401)):
            raw = bytearray(encode_ack(3, 1, 4, bytes(16)))
            struct.pack_into('<II', raw, 6, uid, schema)
            raw[-2:] = struct.pack('<H', crc16(raw[:-2]))
            with self.assertRaisesRegex(ValueError, 'identity'):
                decode(bytes(raw))
        decoder = StreamDecoder()
        self.assertEqual(list(decoder.feed(bytes(raw))), [])
        self.assertEqual(len(list(decoder.feed(encode_ack(4, 1, 0, bytes(16))))), 1)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.m = self.f.model

    def test_bootstrap_highwater_and_readonly_unique_ids(self):
        self.assertEqual(self.m.next_id, 100)
        self.f.tick()
        first = self.m.pending['cid']
        self.f.answer()
        self.f.tick()
        self.assertGreater(self.m.pending['cid'], first)

    def test_matching_sent_ack_only(self):
        self.m.submit('set1', 'set', 'forward_percent', 80)
        p = self.m.pending
        raw = decode(encode_ack(p['cid'], p['op'], 1, struct.pack('<BI', 0, 80)))
        self.assertFalse(self.m.receive(raw))
        self.m.sent(p['cid'], self.f.now)
        wrong = decode(encode_ack(p['cid']-1, p['op'], 1, struct.pack('<BI', 0, 80)))
        self.assertFalse(self.m.receive(wrong))
        self.assertTrue(self.m.receive(raw))
        self.assertEqual(self.m.pending['op'], 0x71)

    def test_write_requires_independent_actual_readback(self):
        result = self.m.submit('a', 'set', 'forward_percent', 80)
        self.assertEqual(result['state'], 'pending')
        self.f.answer()
        self.assertNotEqual(self.m.requests['a']['state'], 'verified')
        self.f.answer()
        self.assertEqual(self.m.requests['a']['state'], 'verified')
        self.assertEqual(self.m.parameters['forward_percent']['value'], 80)

    def test_mismatching_readback_indeterminate_no_run(self):
        self.m.submit('a', 'set', 'forward_percent', 80)
        self.f.answer()
        self.f.answer(struct.pack('<BI', 0, 79))
        self.assertEqual(self.m.requests['a']['state'], 'indeterminate')
        self.assertFalse(any(p[0][18] == 2 for p in self.f.sent))

    def test_independent_read_revision_must_match_write(self):
        self.m.submit('a', 'set', 'reverse_ms', 1000)
        self.f.answer()
        self.f.revision += 1
        self.f.answer()
        self.assertEqual(self.m.requests['a']['state'], 'indeterminate')
        self.assertNotEqual(self.m.parameters['reverse_ms']['value'], 1000)

    def test_timeout_never_resends_or_replays_duplicate(self):
        self.m.submit('a', 'set', 'forward_percent', 80)
        self.m.sent(self.m.pending['cid'], self.f.now)
        self.f.tick(3.1)
        count = len([p for p in self.f.sent if p[0][18] == 0x70])
        result = self.m.submit('a', 'set', 'forward_percent', 80)
        self.assertEqual(result['state'], 'indeterminate')
        self.assertEqual(len([p for p in self.f.sent if p[0][18] == 0x70]), count)
        with self.assertRaises(ValueError):
            self.m.submit('a', 'set', 'forward_percent', 79)

    def test_stop_cancels_queued_run_and_late_query(self):
        self.f.tick()
        p = dict(self.m.pending)
        self.assertEqual(self.m.submit('r', 'run')['state'], 'queued')
        self.m.submit('s', 'stop')
        self.assertEqual(self.m.requests['r']['state'], 'cancelled')
        self.assertFalse(self.m.receive(decode(encode_ack(p['cid'], p['op'], 0, self.f.status()))))
        self.assertEqual(self.m.pending['op'], 0)
        self.f.answer()
        self.f.answer()
        self.assertEqual(self.m.requests['s']['state'], 'verified')
        self.assertFalse(any(p[0][18] == 2 for p in self.f.sent))

    def test_stop_cancels_already_pending_run_without_replay(self):
        self.m.submit('r', 'run')
        old = dict(self.m.pending)
        self.m.submit('s', 'stop')
        self.assertFalse(self.m.receive(decode(encode_ack(old['cid'], old['op'], 0))))
        self.assertEqual(self.m.submit('r', 'run')['state'], 'cancelled')

    def test_disconnect_drops_pending_no_auto_resume(self):
        self.m.submit('r', 'run')
        old_next = self.m.next_id
        self.m.closed()
        count = len(self.f.sent)
        self.f.tick(10)
        self.assertEqual(len(self.f.sent), count)
        self.m.opened()
        self.assertGreaterEqual(self.m.pending['cid'], old_next)
        self.assertEqual(self.m.pending['op'], 1)
        self.assertEqual(self.m.requests['r']['state'], 'indeterminate')

    def test_invalid_values_cross_bounds_and_readonly(self):
        for name, value in [('forward_percent', 91), ('forward_percent', True), ('brake_quiet_ms', 3), ('encoder_mode', 0), ('qualify_ms', 3001), ('wait_timeout_ms', 20)]:
            self.assertEqual(self.m.submit(str((name,value)), 'set', name, value)['state'], 'rejected')

    def test_four_primary_parameters_bounds_and_16bit_times(self):
        for name, valid in [('forward_percent',(1,49,90)), ('reverse_percent',(1,36,90)),
                            ('forward_ms',(1,100,256,1000)), ('reverse_ms',(1,220,256,1000))]:
            for value in valid:
                with self.subTest(name=name, value=value):
                    f = Fixture()
                    self.assertEqual(f.model.submit('t', 'set', name, value)['state'], 'pending')
                    self.assertEqual(int.from_bytes(f.model.pending['payload'][1:], 'little'), value)
                    f.answer()
                    f.answer()
                    self.assertEqual(f.model.requests['t']['state'], 'verified')
                    self.assertEqual(f.model.parameters[name]['value'], value)
            for value in (0, -1, True, 91 if name.endswith('percent') else 1001):
                self.assertEqual(self.m.submit(str((name,value)), 'set', name, value)['state'], 'rejected')

    def test_obsolete_parameter_and_names_removed(self):
        self.assertEqual(len(SWING_PARAMETERS), 9)
        self.assertNotIn(7, BY_ID)
        for name in ('brake_quiet_ms', 'brake_max_ms', 'drive_ms', 'drive_percent', 'brake_percent'):
            self.assertEqual(self.m.submit(name, 'set', name, 50)['state'], 'rejected')

    def test_latest_attachment_prefill_values_only(self):
        self.assertEqual(HISTORICAL_PID, dict(angle_kp=900, angle_ki=870, angle_kd=90,
            position_kp=112, position_ki=0, position_kd=48, ki_enable=1, angle_d_filter=650, angle_window=5))
        self.assertEqual(self.m.parameters['position_kp']['value'], 0)

    def test_running_and_stale_state_rejected(self):
        self.m.status['pwm'] = 100
        self.assertEqual(self.m.submit('p', 'set', 'forward_percent', 50)['state'], 'rejected')
        self.m.status['pwm'] = 0
        self.f.now += 1.1
        self.assertEqual(self.m.submit('r', 'run')['state'], 'rejected')

    def test_zero_pid_missing_pid_and_invalid_zero_rejected(self):
        self.m.parameters['angle_kp']['value'] = 0
        self.assertEqual(self.m.submit('z', 'run')['state'], 'rejected')
        self.m.parameters['angle_kp']['value'] = 900
        del self.m.parameters['ki_enable']
        self.assertEqual(self.m.submit('k', 'run')['state'], 'rejected')
        self.m.status['zero_valid'] = False
        self.assertEqual(self.m.submit('n', 'run')['state'], 'rejected')

    def test_clear_fault_guard_no_run_and_real_confirmation(self):
        self.assertEqual(self.m.submit('none', 'clear-fault')['state'], 'rejected')
        self.m.status['fault'] = self.f.fault = 7
        self.m.submit('clear', 'clear-fault')
        self.f.answer()
        self.assertEqual(self.m.requests['clear']['state'], 'accepted')
        self.f.answer()
        self.assertEqual(self.m.requests['clear']['state'], 'verified')
        self.assertEqual(self.m.status['fault'], 0)
        self.assertTrue(self.m.status['zero_valid'])
        self.assertFalse(any(p[0][18] == 2 for p in self.f.sent))

    def test_fault_or_estop_rejection(self):
        self.m.status['fault'] = 2
        self.assertEqual(self.m.submit('wrong', 'clear-fault')['state'], 'rejected')
        self.m.status['fault'] = 1
        self.m.status['estop'] = True
        self.assertEqual(self.m.submit('estop', 'clear-fault')['state'], 'rejected')

    def test_run_ack_not_balance_until_actual_mode3(self):
        self.m.submit('r', 'run')
        self.f.answer()
        self.assertEqual(self.m.requests['r']['state'], 'accepted')
        self.f.mode, self.f.flags = 2, 0x87
        self.f.answer()
        self.assertEqual(self.m.requests['r']['state'], 'swinging')
        self.f.mode, self.f.flags = 3, 0x83
        self.f.tick(.2)
        self.f.answer()
        self.assertEqual(self.m.requests['r']['state'], 'balance')

    def test_actual_swing_failure(self):
        self.m.submit('r', 'run')
        self.f.answer()
        self.f.flags = 0xC9
        self.f.answer()
        self.assertEqual(self.m.requests['r']['state'], 'failed')

    def test_id_exhaustion_still_allows_stop_and_unique_status(self):
        self.m.next_id = 0x100000000
        self.assertEqual(self.m.submit('noid', 'run')['state'], 'rejected')
        self.assertEqual(self.m.submit('stopid', 'stop')['state'], 'pending')
        first = self.m.pending['cid']
        self.f.answer()
        self.assertNotEqual(self.m.pending['cid'], first)
        self.f.answer()
        self.assertEqual(self.m.requests['stopid']['state'], 'verified')

    def test_stop_updates_active_run_only_after_actual_readback(self):
        self.m.submit('r', 'run')
        self.f.answer()
        self.f.flags = 0x87
        self.f.answer()
        self.m.submit('s', 'stop')
        self.assertEqual(self.m.requests['r']['state'], 'stopping')
        self.f.answer()
        self.assertEqual(self.m.requests['r']['state'], 'stopping')
        self.f.answer()
        self.assertEqual(self.m.requests['r']['state'], 'stopped')

    def test_rtl_rejection_is_not_success(self):
        self.m.submit('r', 'run')
        self.f.answer(status=1, reason=4)
        self.assertEqual(self.m.requests['r']['state'], 'rejected')

    def test_pid_batch_sequential_real_readback_no_run(self):
        self.m.submit('pid', 'restore-pid', value=HISTORICAL_PID.copy())
        for _ in range(100):
            self.f.tick()
            if self.m.pending:
                self.f.answer()
            if self.m.requests['pid']['state'] == 'verified':
                break
        self.assertEqual(self.m.requests['pid']['state'], 'verified')
        self.assertEqual(len(self.m.requests['pid']['confirmed']), 9)
        ops = [p[0][18] for p in self.f.sent]
        self.assertEqual(ops.count(0x70), 9)
        self.assertEqual(ops.count(0x71), 9)
        self.assertNotIn(2, ops)

    def test_pid_batch_stop_and_timeout_cancel_remaining(self):
        self.m.submit('pid', 'restore-pid', value=HISTORICAL_PID.copy())
        self.f.tick()
        self.m.submit('s', 'stop')
        self.assertIsNone(self.m.batch)
        self.assertEqual(self.m.requests['pid']['state'], 'cancelled')
        self.assertEqual(sum(p[0][18] == 0x70 for p in self.f.sent), 1)

    def test_pid_batch_timeout_cancels_remaining(self):
        self.m.submit('pid', 'restore-pid', value=HISTORICAL_PID.copy())
        self.f.tick()
        self.f.tick(3.1)
        self.assertIsNone(self.m.batch)
        self.assertEqual(self.m.requests['pid']['state'], 'indeterminate')
        self.assertEqual(sum(p[0][18] == 0x70 for p in self.f.sent), 1)

    def test_no_parallel_mutations(self):
        self.m.submit('a', 'set', 'forward_percent', 80)
        self.assertEqual(self.m.submit('b', 'set', 'reverse_percent', 40)['state'], 'rejected')

    def test_pages_keep_separate_board_timestamps(self):
        self.m._issue(1, b'\1', 'status', page=1)
        self.f.answer()
        self.m._issue(1, b'\2', 'status', page=2)
        self.f.answer()
        self.assertEqual(self.m.telemetry['angle']['timestamp_ms'], 200)
        self.assertEqual(self.m.telemetry['arm']['timestamp_ms'], 201)
        self.assertEqual(self.m.telemetry['angle']['alpha_q20'], -1048576)
        self.assertEqual(self.m.telemetry['arm']['encoder_count'], -99)

    def test_phase_page_actual_layout_and_csv_fields(self):
        samples = []
        self.m.sample = samples.append
        self.m._issue(1, b'\3', 'status', page=3)
        self.f.answer()
        row = self.m.telemetry['phase']
        self.assertEqual(row['timestamp_ms'], 202)
        self.assertEqual(row['swing_state'], 13)
        self.assertEqual(row['phase_elapsed_ms'], 42)
        self.assertEqual(row['effective_forward_ms'], 1000)
        self.assertEqual(row['session_reverse_ms'], 220)
        self.assertEqual(row['phase_end_reason'], 1)
        self.assertEqual(row['retry'], 1)
        self.assertTrue(row['phase_active'])
        self.assertFalse(row['capture_candidate'] or row['handoff_confirmed'])
        self.assertTrue(set(samples[0]) <= set(CSV_FIELDS))
        self.assertIn('反向主动驱动', phase_description(row))
        self.assertIn('前向计时到', phase_description(row))

    def test_capture_and_handoff_do_not_claim_balance(self):
        self.m.submit('r', 'run')
        self.f.answer()
        self.f.flags = 0x87
        self.f.answer()
        for hold, flags, reason in ((1, 3, 3), (19, 3, 3), (20, 4, 4)):
            self.m._issue(1, b'\3', 'status', page=3)
            self.f.answer(struct.pack('<BBHHHIBBBB', 40, reason, 20, 100, 220, 300, 0, hold, flags, 0))
            self.assertEqual(self.m.requests['r']['state'], 'swinging')
            self.assertNotEqual(self.m.status['mode'], 3)
            self.assertIn('非BALANCE', phase_description(self.m.telemetry['phase']))

    def test_polling_includes_page3_without_unrequested_commands(self):
        for _ in range(30):
            self.f.tick()
            if self.m.pending:
                self.f.answer()
        read_pages = {packet[19] for packet,_,_ in self.f.sent if packet[18] == 1}
        self.assertEqual(read_pages, {0,1,2,3,4,5,6})
        self.assertTrue(all(packet[18] in (1,0x71) for packet,_,_ in self.f.sent))


class CliTests(unittest.TestCase):
    def test_cli_new_identity_flag_and_timing_set(self):
        calls = []
        def exchange(port, path, body=None):
            calls.append((port,path,body))
            return {'state':'verified'}
        with patch.object(swing_tune_cli, 'exchange', side_effect=exchange), redirect_stdout(io.StringIO()):
            self.assertEqual(swing_tune_cli.main(['connect','COM3','--firmware-swm2']), 0)
            self.assertEqual(swing_tune_cli.main(['--request-id','r1000','set','reverse_ms','1000']), 0)
        self.assertTrue(calls[0][2]['firmware_swm2'])
        self.assertNotIn('firmware_swm1', calls[0][2])
        self.assertEqual(calls[1][2]['value'], 1000)
        self.assertEqual(calls[1][2]['name'], 'reverse_ms')

    def test_cli_timeout_never_reissues_post(self):
        with patch.object(swing_tune_cli, 'exchange', side_effect=OSError('offline timeout')) as exchange, redirect_stdout(io.StringIO()):
            self.assertEqual(swing_tune_cli.main(['--request-id','one-run','--wait','3','run']), 2)
        self.assertEqual(exchange.call_count, 1)

    def test_cli_restore_uses_explicit_nine_values(self):
        arguments = ['restore-pid']
        for name, value in HISTORICAL_PID.items():
            arguments.extend(['--'+name.replace('_','-'), str(value)])
        with patch.object(swing_tune_cli, 'exchange', return_value={'state':'verified'}) as exchange, redirect_stdout(io.StringIO()):
            self.assertEqual(swing_tune_cli.main(arguments), 0)
        self.assertEqual(exchange.call_args.args[2]['value'], HISTORICAL_PID)


class TransportTests(unittest.TestCase):
    def test_stop_invalidates_unsent_queue_and_close(self):
        box = Outbox()
        box.submit(b'RUN', 1)
        box.submit(b'WRITE', 2)
        box.submit(b'STOP', 3, True)
        output = []
        self.assertEqual(box.write_one(output.append)[0], 3)
        self.assertEqual(output, [b'STOP'])
        self.assertIsNone(box.write_one(output.append))
        box.close()
        with self.assertRaises(RuntimeError):
            box.submit(b'RUN', 4)

    def test_inflight_bytes_finish_then_stop_is_next(self):
        box = Outbox()
        entered, release = threading.Event(), threading.Event()
        output = []
        box.submit(b'INFLIGHT', 1)
        box.submit(b'UNSENT_RUN', 2)
        def writer(packet):
            entered.set()
            self.assertTrue(release.wait(2))
            output.append(packet)
        worker = threading.Thread(target=box.write_one, args=(writer,))
        worker.start()
        self.assertTrue(entered.wait(2))
        stopper = threading.Thread(target=box.submit, args=(b'STOP', 3, True))
        stopper.start()
        release.set()
        worker.join(2)
        stopper.join(2)
        self.assertFalse(worker.is_alive() or stopper.is_alive())
        box.write_one(output.append)
        self.assertEqual(output, [b'INFLIGHT', b'STOP'])


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(Path(__file__).resolve().parents[1]/'reports/host/test_sessions', allow_device=False)
        self.server = start_bridge(self.engine, 0)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.engine.close()

    def http(self, body=None, headers=None, path='/status'):
        headers = {'X-J280-Local': '1', 'Content-Type': 'application/json'} if headers is None else headers
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base+path, data=data, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as response:
            return json.load(response)

    def test_snapshot_and_no_hardware_on_start(self):
        s = self.http()
        self.assertFalse(s['connected'])
        self.assertEqual(s['user_code'], '53413231')
        self.assertEqual(len(s['parameter_specs']), 34)
        self.assertIsNone(self.engine.worker)

    def test_bridge_exposes_real_phase_page_to_cli(self):
        f = Fixture()
        f.model._issue(1, b'\3', 'status', page=3)
        f.answer()
        self.engine.model = f.model
        s = self.http()
        self.assertEqual(s['schema'], '53415421')
        self.assertEqual(s['telemetry']['phase']['phase_elapsed_ms'], 42)
        self.assertEqual(s['telemetry']['phase']['effective_forward_ms'], 1000)
        self.assertEqual(s['telemetry']['phase']['session_reverse_ms'], 220)
        self.assertEqual(s['telemetry']['phase']['phase_end_reason'], 1)
        self.assertIsNone(self.engine.worker)

    def test_origin_host_and_content_type_rejected(self):
        for headers in ({'Origin':'https://example.com', 'X-J280-Local':'1'},
                        {'X-J280-Local':'1', 'Host':'example.com'}, {}):
            with self.assertRaises(urllib.error.HTTPError) as context:
                self.http(headers=headers)
            self.assertEqual(context.exception.code, 403)
            context.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.http({'action':'run'}, headers={'X-J280-Local':'1','Content-Type':'text/plain'}, path='/command')
        context.exception.close()

    def test_offline_connect_cannot_open_com(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.http({'action':'connect','port':'COM3','firmware_swm2':True}, path='/command')
        self.assertEqual(context.exception.code, 400)
        context.exception.close()
        self.assertIsNone(self.engine.worker)

    def test_duplicate_http_does_not_execute_twice(self):
        body = {'action':'run', 'request_id':'same'}
        first = self.http(body, path='/command')
        second = self.http(body, path='/command')
        self.assertEqual(first, second)
        self.assertEqual(first['state'], 'rejected')


if __name__ == '__main__':
    unittest.main(verbosity=2)
