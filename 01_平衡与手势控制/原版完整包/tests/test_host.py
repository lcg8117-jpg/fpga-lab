"""Offline host acceptance: no test calls open_usb or discovers any device."""
import gzip
import json
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest

from host import protocol as p
from host.client import SerialOwner
from host.config_fields import validate_value
from host.fake_transport import FakeTransport, finish
from host.session_log import SessionLog


def wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("Offline host condition timed out")


class SlowCompressionLog(SessionLog):
    def _run(self):
        # Compression may stall; the serial owner must continue reading/writing.
        time.sleep(.45)
        super()._run()


class ProtocolTests(unittest.TestCase):
    def test_motion_start_guard_field_and_reasons(self):
        for value in (0, 200, 300, 500, 750, 1000, 1500, 2000):
            self.assertEqual(validate_value(251, 10, value), value)
        for value in (-1, 199, 501, 2001):
            with self.assertRaises(ValueError):
                validate_value(251, 10, value)
        self.assertIn('保护', p.MOTION_REASON_TEXT[17])
        self.assertIn('屏蔽全部手势', p.GESTURE_DECODE_TEXT[4])
        self.assertIn('接续', p.MOTION_REASON_TEXT[16])

    def test_machine_contract_identity_and_every_extension_field(self):
        contract = json.loads((Path(__file__).resolve().parents[1] / 'protocol' / 'contract.json').read_text(encoding='utf-8'))
        for key, value in (('firmware_id', p.FIRMWARE), ('schema', p.SCHEMA), ('wire_version', p.VERSION),
                           ('variant', p.VARIANT), ('capabilities', p.CAPABILITIES)):
            self.assertEqual(contract[key], value)
        aliases = dict(uptime_ms='board_ms', actual_position='wide_encoder', continuous_reference='wide_target',
                       controller_tracking_error='position_error', reference_velocity_cps='reference_velocity',
                       measured_velocity_cps='measured_velocity', velocity_q16_per_5ms='velocity_q',
                       acceleration_q16_per_5ms2='acceleration_q', planner_action='motion_action',
                       planner_request='motion_request_id', gesture_sequence='gesture_event_seq', gesture_poll='gesture_poll_seq',
                       gesture_init='gesture_init_state', gesture_error_count='gesture_errors', suppressed='gesture_suppressed',
                       candidate_epoch='candidate_feature_epoch', feature_freeze='feature_freeze_reason',
                       adaptive_enabled='adaptation_enabled', adaptive_active='adaptation_active', control_revision='active_revision',
                       manual_run_q4='run_q4', source_flags='source_capture_flags', source_capture_hold_ms='capture_hold_ms',
                       profile='swing_profile', kit_ip570='kit', last_mutation='mutation_highwater',
                       angle_target_adc12='target_angle_adc12', auto_downward_q4='auto_bottom_q4')
        formats = dict(u8='B', i8='b', u16='H', i16='h', u32='I', i32='i', u64='Q', i64='q')
        expected = {}
        frame = bytearray(contract['lengths']['E'])
        fixed = dict(magic=contract['E']['magic'], wire_version=p.VERSION, variant=p.VARIANT,
                     schema=p.SCHEMA, firmware_id=p.FIRMWARE, capabilities=p.CAPABILITIES, fastp_tag=0x41465031)
        for index, field in enumerate(contract['E']['fields']):
            kind, name, offset = field['type'], field['name'], field['offset']
            value = fixed.get(name, -100 - index if kind.startswith('i') else 100 + index)
            if kind == 'i8':
                value = -3
            struct.pack_into('<' + formats[kind], frame, offset, value)
            if name != 'magic':
                expected[aliases.get(name, name)] = value
        item = p.decode(finish(frame))
        for key, value in expected.items():
            self.assertEqual(item[key], value, key)
        for field in contract['T_changes']:
            t = bytearray(256)
            t[:8] = b'FD2T\x03\x10\x01\x03'
            value = -12345 if field['type'] != 'i64' else -9876543210
            struct.pack_into('<' + formats[field['type']], t, field['offset'], value)
            self.assertEqual(p.decode(finish(t))[field['name']], value)

    def test_crc_golden_and_request_shape(self):
        self.assertEqual(p.crc16(b"123456789"), 0x29b1)
        request = p.request(p.SET_GAINS, 33, 255, p.gains_payload(p.DEFAULT_GAINS))
        self.assertEqual(len(request), 48)
        self.assertEqual(request[:8], b"FD2C\x03\x20\xff\x18")
        self.assertEqual(request[36:46], bytes(10))
        self.assertTrue(p.checked_crc(request))
        for values in ((1, 2), (49, 0, float("nan"), 25, 0, 610), (-1, 0, 1, 1, 0, 1)):
            with self.assertRaises(ValueError):
                p.gains_payload(values)
        with self.assertRaises(ValueError):
            p.request(p.RUN, 0)
        with self.assertRaises(ValueError):
            p.request(p.START_SWING, 1, payload=b"\0")

    def test_signed_pose_final_target_and_gain_transactions(self):
        # Independent wire fixture: sentinels catch the old physical/final alias.
        frame = bytearray(256)
        frame[:8] = b"FD2T\x03\x10\x65\x03"
        struct.pack_into("<8I", frame, 8, 7, 12345, 41, 3, 3, 9, p.FIRMWARE, p.SCHEMA)
        struct.pack_into("<i2h3qi", frame, 64, -1234, 9998, 10000,
                         -9876543210, -9876543208, -9876544000, -2)
        for offset, values in ((128, p.DEFAULT_GAINS), (152, (49, 0, 252, 25, 0, 610)),
                               (176, (49, 0, 253, 25, 0, 610)), (200, (1, 2, 3, 4, 5, 6))):
            struct.pack_into("<6f", frame, offset, *values)
        struct.pack_into("<I", frame, 240, 40)
        struct.pack_into('<IH6B5I', frame, 220, 0x41465031, 1000, 1,5,5,25,1,5, 295,275,295,59,295)
        item = p.decode(finish(frame))
        self.assertTrue(p.identity_matches(item))
        self.assertEqual((item['board_ms'], item['control_epoch']), (12345, 41))
        self.assertEqual(item['final_target'], -9876544000)
        self.assertEqual(item['wide_encoder'], -9876543210)
        self.assertEqual(item['position_error'], -2)
        self.assertEqual(item['active_gains'][2], 252)
        self.assertEqual(item['adaptive_target_gains'][2], 253)
        self.assertTrue(item['adaptive_enabled_at_launch'])
        self.assertTrue(item['adaptive_used_at_launch'])

    def test_fragmented_stream_crc_and_raw_counts(self):
        fake = FakeTransport()
        fake._step()
        raw = bytes(fake.buffer)
        corrupt = bytearray(raw)
        corrupt[60] ^= 1
        parser = p.FrameParser()
        result = []
        wire = b"garbage" + corrupt + raw
        for offset in range(0, len(wire), 13):
            result.extend(parser.feed(wire[offset:offset + 13]))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], raw)
        self.assertEqual(parser.crc_errors, 1)
        self.assertGreaterEqual(parser.discarded_bytes, len(corrupt) + 7)

    def test_base_source_fields_and_hard_caps(self):
        self.assertEqual(validate_value(0, 8, 1), 1)
        for group, field, value in ((0, 8, 2), (2, 2, 90), (2, 66, 255), (253, 14, 36), (251, 1, 81), (251, 8, 4500)):
            with self.assertRaises(ValueError):
                validate_value(group, field, value)

    def test_extension_context_live_optical_and_distinct_epochs(self):
        frame = bytearray(256)
        frame[:8] = b"FD2E\x03\x10\0\x03"
        struct.pack_into('<8I', frame, 8, 6, 300, p.SCHEMA, p.FIRMWARE, p.CAPABILITIES,
                         59, 295, 0x5d)
        struct.pack_into('<6q4i', frame, 40, -1080, -1040, -2080, -1041, -1040, -1,
                         -260, -261, -85197, -13)
        frame[104:112] = bytes((3, 255, 2, 13, 4, 3, 2, 0))
        frame[128:136] = bytes((0x40, 0, 2, 0x11, 1, 0, 0, 4))
        struct.pack_into('<4I', frame, 144, 58, 57, 3, 5)
        frame[160:164] = bytes((11, 11, 1, 0))
        struct.pack_into('<3I2H', frame, 164, 3, 3, 55, 634, 272)
        struct.pack_into('<IH6B5I', frame, 220, 0x41465031, 1000, 1,5,5,25,1,5, 295,275,295,59,295)
        item = p.decode(finish(frame))
        self.assertTrue(p.identity_matches(item))
        self.assertEqual(item['kind'], 'extension')
        self.assertEqual((item['control_epoch'], item['control_ms']), (59, 295))
        self.assertEqual(item['position_error'], -1)
        self.assertEqual(item['physical_wide'], -1080)
        self.assertEqual(item['direction'], -1)
        self.assertEqual(item['acceleration_q'], -13)
        self.assertEqual(item['raw43'], 0x40)
        self.assertEqual(item['feature_epoch'], 58)
        self.assertEqual(item['candidate_feature_epoch'], 57)
        self.assertEqual(item['adopted_feature_epoch'], 55)
        self.assertTrue(item['context_used'])
        self.assertFalse(item['adaptation_active'])
        frame[252] = 1
        with self.assertRaises(ValueError):
            p.decode(finish(frame))

    def test_nonfinite_wire_evidence_rejected(self):
        fake = FakeTransport()
        fake._step()
        raw = bytearray(fake.buffer)
        struct.pack_into('<f', raw, 152, float('nan'))
        parser = p.FrameParser()
        self.assertEqual(parser.feed(finish(raw)), [])
        self.assertEqual(parser.format_errors, 1)


class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.owners = []
        self.fakes = []

    def tearDown(self):
        for owner in self.owners:
            owner.close()
        self.tmp.cleanup()

    def owner(self, *, logger_factory=SessionLog, **fake_options):
        def factory():
            fake = FakeTransport(**fake_options)
            self.fakes.append(fake)
            return fake
        owner = SerialOwner(factory, self.tmp.name, logger_factory=logger_factory).start()
        self.owners.append(owner)
        return owner

    def test_keepalive_200hz_lossless_when_ui_and_compression_wait(self):
        owner = self.owner(logger_factory=SlowCompressionLog)
        wait_until(lambda: owner.snapshot()['authenticated'])
        # This represents a blocked GUI/main thread. It never owns the transport.
        time.sleep(.85)
        wait_until(lambda: owner.snapshot()['heartbeat_sent'] >= 9)
        snapshot = owner.snapshot()
        owner.close()
        self.assertGreaterEqual(snapshot['control_received'], 160)
        self.assertGreaterEqual(snapshot['heartbeat_sent'], 9)
        self.assertLess(snapshot['heartbeat_max_gap'], .16)
        self.assertEqual(snapshot['control_gaps'], 0)
        self.assertEqual(snapshot['crc_errors'], 0)
        fake = self.fakes[0]
        self.assertEqual(fake.called_threads, {fake.owner_thread})
        self.assertNotEqual(fake.owner_thread, threading.get_ident())
        self.assertTrue(set(frame[5] for frame in fake.writes) <= p.READS)
        self.assertEqual(owner.logger.accepted, owner.logger.written)
        with gzip.open(owner.logger.path, 'rt', encoding='utf-8') as stream:
            rows = [json.loads(line) for line in stream]
        control = [row for row in rows if row['event'] == 'frame' and
                   row['decoded']['kind'] == 'telemetry' and row['decoded']['coherent']]
        self.assertEqual(len(control), owner.snapshot()['control_received'])
        self.assertTrue(all(row['decoded']['board_ms'] == row['decoded']['control_epoch'] for row in control))
        parser = p.FrameParser()
        replayed = []
        for row in rows:
            if row['event'] == 'rx_chunk':
                replayed.extend(parser.feed(bytes.fromhex(row['raw_hex'])))
        self.assertEqual(len(replayed), sum(row['event'] == 'frame' for row in rows))

    def test_independent_readback_and_mutation_highwater(self):
        owner = self.owner(initial_highwater=400)
        wait_until(lambda: owner.snapshot()['authenticated'])
        owner.set_gains((50, 0, 252, 25, 0, 610))
        wait_until(lambda: owner.snapshot()['independent_gains'] is not None)
        self.assertEqual(owner.snapshot()['independent_gains'][0][0], 50)
        owner.write_config(0, 2, 97)
        wait_until(lambda: (0, 2) in owner.snapshot()['config_reads'])
        self.assertEqual(owner.snapshot()['config_reads'][(0, 2)][0], 97)
        owner.close()
        mutations = [frame for frame in self.fakes[0].writes if frame[5] not in p.READS]
        self.assertTrue(all(struct.unpack_from('<I', frame, 8)[0] > 400 for frame in mutations))
        opcodes = [frame[5] for frame in self.fakes[0].writes]
        self.assertLess(opcodes.index(p.SET_GAINS), opcodes.index(p.READ_GAINS))
        read_config = [frame for frame in self.fakes[0].writes if frame[5] == p.READ_CONFIG and frame[6] == 0]
        self.assertEqual(read_config[-1][12], 2)

    def test_crc_loss_marking_without_smoothing_or_reindex(self):
        owner = self.owner(corrupt_epochs=(5,), drop_epochs=(8,))
        wait_until(lambda: owner.snapshot()['control_received'] > 20)
        snapshot = owner.snapshot()
        self.assertEqual(snapshot['crc_errors'], 1)
        self.assertEqual(snapshot['control_gaps'], 2)
        self.assertEqual(snapshot['sequence_gaps'], 2)
        epochs = [row['control_epoch'] for row in snapshot['history']]
        self.assertNotIn(25, epochs)
        self.assertNotIn(40, epochs)
        self.assertEqual(snapshot['history'][-1]['board_ms'], epochs[-1])

    def test_identity_mismatch_blocks_controls(self):
        owner = self.owner(identity=0x46443233)
        wait_until(lambda: owner.snapshot()['received'] > 3)
        self.assertFalse(owner.snapshot()['authenticated'])
        with self.assertRaises(RuntimeError):
            owner.command(p.RUN)
        owner.command(p.STOP)
        wait_until(lambda: any(frame[5] == p.STOP for frame in self.fakes[0].writes))

    def test_action_rejection_is_not_hidden_by_keepalive_or_readback(self):
        owner = self.owner()
        wait_until(lambda: owner.snapshot()['authenticated'])
        # Fake starts stopped: the board refuses a motion request honestly.
        owner.command(p.MOTION_CW)
        wait_until(lambda: (owner.snapshot()['last_action_ack'] or {}).get('opcode') == p.MOTION_CW)
        state = owner.snapshot()
        self.assertEqual(state['last_action_ack']['status'], 1)
        message, beats = state['message'], state['heartbeat_sent']
        owner.read_config(254, 8)
        wait_until(lambda: owner.snapshot()['heartbeat_sent'] >= beats + 3)
        self.assertEqual(owner.snapshot()['message'], message)
        self.assertEqual(owner.snapshot()['last_action_ack']['opcode'], p.MOTION_CW)
        owner.command(p.STOP)
        wait_until(lambda: (owner.snapshot()['last_action_ack'] or {}).get('opcode') == p.STOP)
        self.assertEqual(owner.snapshot()['last_action_ack']['status'], 0)

    def test_unknown_command_result_is_never_replayed(self):
        owner = self.owner(suppress_opcodes=(p.RUN,))
        wait_until(lambda: owner.snapshot()['authenticated'])
        owner.command(p.RUN)
        wait_until(lambda: owner.snapshot()['unknown_mutations'] == 1)
        owner.close()
        self.assertEqual(sum(frame[5] == p.RUN for frame in self.fakes[0].writes), 1)
        second = self.owner()
        wait_until(lambda: second.snapshot()['authenticated'])
        time.sleep(.12)
        self.assertTrue(set(frame[5] for frame in self.fakes[1].writes) <= p.READS)

    def test_stale_receive_locks_mutations(self):
        owner = self.owner()
        wait_until(lambda: owner.snapshot()['authenticated'])
        self.fakes[0].freeze_rx = True
        wait_until(lambda: not owner.snapshot()['fresh'])
        with self.assertRaises(RuntimeError):
            owner.command(p.START_SWING)
        self.assertFalse(any(frame[5] == p.START_SWING for frame in self.fakes[0].writes))
        self.fakes[0].freeze_rx = False
        time.sleep(.1)
        self.assertFalse(owner.snapshot()['authenticated'])
        with self.assertRaises(RuntimeError):
            owner.command(p.RUN)

    def test_logging_failure_single_stop_ack_then_close(self):
        owner = self.owner(initial_highwater=700)
        wait_until(lambda: owner.snapshot()['authenticated'])
        owner.logger.error = 'Injected disk failure'
        wait_until(lambda: not owner.snapshot()['connected'])
        fake = self.fakes[0]
        stops = [frame for frame in fake.writes if frame[5] == p.STOP]
        self.assertEqual(len(stops), 1)
        self.assertGreater(struct.unpack_from('<I', stops[0], 8)[0], 700)
        self.assertEqual(fake.writes[-1][5], p.STOP)
        self.assertEqual(owner.snapshot()['unknown_mutations'], 0)
        self.assertIn('STOP ACK', owner.snapshot()['logging_failure_stop'])
        self.assertTrue(fake.closed)

    def test_logging_failure_unknown_stop_no_keepalive_or_retry(self):
        owner = self.owner(suppress_opcodes=(p.STOP,))
        wait_until(lambda: owner.snapshot()['authenticated'])
        owner.logger.error = 'Injected disk failure'
        wait_until(lambda: not owner.snapshot()['connected'])
        fake = self.fakes[0]
        self.assertEqual(sum(frame[5] == p.STOP for frame in fake.writes), 1)
        self.assertEqual(fake.writes[-1][5], p.STOP)
        self.assertEqual(owner.snapshot()['unknown_mutations'], 1)
        self.assertIn('结果未知', owner.snapshot()['logging_failure_stop'])
        self.assertTrue(fake.closed)

    def test_logging_failure_without_identity_closes_without_unknown_id(self):
        owner = self.owner(identity=0x46443233, initial_highwater=700)
        wait_until(lambda: owner.snapshot()['received'] > 3)
        owner.logger.error = 'Injected disk failure'
        wait_until(lambda: not owner.snapshot()['connected'])
        self.assertTrue(set(frame[5] for frame in self.fakes[0].writes) <= p.READS)
        self.assertIn('未拼装STOP', owner.snapshot()['logging_failure_stop'])
        self.assertTrue(self.fakes[0].closed)


if __name__ == '__main__':
    unittest.main()
