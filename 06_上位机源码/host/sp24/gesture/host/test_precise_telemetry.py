"""Offline wire, precision, gap, recorder and display tests; no hardware access."""
import csv
from decimal import Decimal
import json
import math
from pathlib import Path
import struct
import tempfile
import time
import unittest
from tune_protocol import USER_CODE, CONTROL_SCHEMA, crc16, decode, StreamDecoder, encode_ack
from precise_telemetry import TelemetryHistory, SessionRecorder, physical_values, reduced_segments
from swing_tune_host import CSV_FIELDS, Engine, Window
from test_tune_host import Fixture


def telemetry_packet(sequence=1, stamp=100, capture=None, alpha=12, alpha_rate=-34,
                     theta_rate=56, encoder=-10, target=-2**32+1, reference=-78,
                     pwm=-123, flags=0x7B, fault=0, angle_revision=12, position_revision=7):
    """Synthetic frame generator for offline tests, never physical evidence."""
    payload = (struct.pack('<IIIiii', sequence, stamp, stamp if capture is None else capture,
               alpha, alpha_rate, theta_rate) + encoder.to_bytes(8, 'little', signed=True)
        + target.to_bytes(12, 'little', signed=True) + reference.to_bytes(8, 'little', signed=True)
        + struct.pack('<hBBII', pwm, flags, fault, angle_revision, position_revision))
    body = struct.pack('<4sHII', b'J2T4', 80, USER_CODE, CONTROL_SCHEMA)+payload
    return body+struct.pack('<H', crc16(body))


class PreciseFramingTests(unittest.TestCase):
    def test_full_signed_boundaries_and_q32_fraction_exact(self):
        for sign in (-1, 1):
            values = dict(alpha=-(2**31) if sign < 0 else 2**31-1,
                encoder=-(2**63) if sign < 0 else 2**63-1,
                target=-(2**95) if sign < 0 else 2**95-1,
                reference=-(2**63) if sign < 0 else 2**63-1,
                pwm=-32768 if sign < 0 else 32767)
            frame = decode(telemetry_packet(**values))
            self.assertEqual(frame.kind, 'telemetry')
            for name, source in [('alpha_q20','alpha'), ('encoder_count','encoder'),
                                 ('target_count_q32','target'), ('reference_rad_q20','reference'), ('pwm','pwm')]:
                self.assertEqual(frame.values[name], values[source])
            converted = physical_values(frame.values)
            # Decimal default precision is deliberately insufficient for i96 extremes.
            from decimal import localcontext
            with localcontext() as ctx:
                ctx.prec = 90
                self.assertEqual(Decimal(converted['target_count'])*2**32, values['target'])
        raw = decode(telemetry_packet(target=2**80+1)).values
        self.assertNotEqual(float(raw['target_count_q32']), raw['target_count_q32'])
        self.assertTrue(physical_values(raw)['target_count'].endswith('00000000023283064365386962890625'))

    def test_fragmented_mixed_frames_and_corrupt_resync(self):
        wave, ack = telemetry_packet(), encode_ack(9, 1, 2, bytes(16))
        corrupted = bytearray(wave)
        corrupted[45] ^= 0x81
        wrong_identity = bytearray(wave)
        wrong_identity[6] ^= 1
        wrong_identity[-2:] = struct.pack('<H', crc16(wrong_identity[:-2]))
        stream = b'noise'+wave+ack+bytes(corrupted)+bytes(wrong_identity)+wave+ack
        decoder, frames = StreamDecoder(), []
        for offset in range(0, len(stream), 7):
            frames.extend(decoder.feed(stream[offset:offset+7]))
        self.assertEqual([f.kind for f in frames], ['telemetry','ack','telemetry','ack'])
        self.assertEqual(decoder.crc_errors, 1)
        self.assertEqual(decoder.identity_errors, 1)
        self.assertGreater(decoder.discarded_bytes, 80)
        self.assertLess(len(decoder.buffer), 6)

    def test_length_mode_crc_and_identity_rejected(self):
        raw = telemetry_packet()
        for cut in (0, 14, 79):
            with self.assertRaises(ValueError):
                decode(raw[:cut])
        with self.assertRaisesRegex(ValueError, 'mode'):
            decode(telemetry_packet(flags=7))
        wrong = bytearray(raw)
        wrong[4] = 79
        with self.assertRaisesRegex(ValueError, 'length'):
            decode(bytes(wrong))

    def test_telemetry_cannot_complete_pending_command(self):
        fixture = Fixture()
        fixture.model.tick()
        pending = fixture.model.pending
        self.assertFalse(fixture.model.receive(decode(telemetry_packet())))
        self.assertIs(fixture.model.pending, pending)


class HistoryTests(unittest.TestCase):
    def test_wrap_gaps_duplicate_stale_and_real_reset(self):
        history = TelemetryHistory()
        a = decode(telemetry_packet(sequence=0xFFFFFFFF, stamp=0xFFFFFFF3, capture=0xFFFFFFF8)).values
        history.receive(a, 10)
        b = decode(telemetry_packet(sequence=0, stamp=0xFFFFFFFD, capture=2)).values
        row = history.receive(b, 10.01)
        self.assertEqual(row['board_elapsed_ms'], 10)
        self.assertEqual(row['epoch_age_ms'], 5)
        self.assertEqual(history.resets, 0)
        self.assertIsNone(history.receive(b, 10.02))
        row = history.receive(decode(telemetry_packet(sequence=3, stamp=27, capture=32)).values, 10.04)
        self.assertEqual(row['sequence_gap'], 2)
        self.assertTrue(row['gap_before'])
        self.assertEqual(history.snapshot(10.4)['missing'], 2)
        self.assertTrue(history.snapshot(10.4)['stale'])
        row = history.receive(decode(telemetry_packet(sequence=0, stamp=0, capture=10)).values, 11)
        self.assertEqual(row['session_epoch'], 1)
        self.assertEqual(row['board_elapsed_ms'], 0)
        self.assertEqual(len(history.rows), 1)
        self.assertTrue(row['gap_before'])

    def test_100hz_board_and_arrival_rates_and_epoch_age(self):
        history = TelemetryHistory()
        for n in range(501):
            history.receive(decode(telemetry_packet(sequence=n, stamp=n*10, capture=n*10+4)).values, n*.01)
        stats = history.snapshot(5.01)
        self.assertAlmostEqual(stats['received_hz'], 100)
        self.assertAlmostEqual(stats['board_hz'], 100)
        self.assertEqual(stats['epoch_age_ms'], 4)
        self.assertFalse(stats['stale'])

    def test_engine_preserves_every_burst_sample_without_acks(self):
        engine = Engine(Path(tempfile.gettempdir())/'unused_sp24_test', allow_device=False)
        for n in range(100):
            engine.events.put(('frame', decode(telemetry_packet(sequence=n, stamp=n*10)), 1+n*.01))
        engine.tick()
        self.assertEqual(engine.wave.accepted, 100)
        self.assertFalse(engine.model.synced)
        self.assertIsNone(engine.worker)
        self.assertEqual(engine.snapshot()['precise_latest']['sequence'], 99)
        self.assertEqual(engine.snapshot()['precise_latest']['wide_integer_decimal']['target_count_q32'], str(-2**32+1))
        engine.close()

    def test_normal_close_drains_decoded_telemetry_before_csv_flush(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(directory, allow_device=False)
            engine.recorder = SessionRecorder(directory, CSV_FIELDS)
            for n in range(50):
                engine.events.put(('frame', decode(telemetry_packet(sequence=n, stamp=n*10)), n*.01))
            engine.close()
            with (Path(directory)/'telemetry.csv').open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 50)
            self.assertEqual(engine.wave.accepted, 50)

    def test_explicit_disconnect_records_queued_frames_but_ignores_late_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(directory, allow_device=False)
            engine.recorder = SessionRecorder(directory, CSV_FIELDS)
            engine.dispatch(dict(action='disconnect'))
            engine.events.put(('frame', decode(telemetry_packet(sequence=17)), 1))
            engine.events.put(('frame', decode(encode_ack(99, 1, 0, bytes(16))), 1))
            engine.events.put(('disconnected', None, 1.1))
            engine.tick()
            self.assertFalse(engine.model.synced)
            self.assertIsNone(engine.model.pending)
            with (Path(directory)/'telemetry.csv').open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            self.assertEqual(int(rows[0]['sequence']), 17)
            engine.close()


class PlotTests(unittest.TestCase):
    def test_stopped_fresh_measurements_draw_without_pid_target_or_reference(self):
        history = TelemetryHistory()
        for n in range(5):
            history.receive(decode(telemetry_packet(sequence=n, stamp=100+n*10, capture=103+n*10,
                alpha=100+n, encoder=300+n, flags=0x40)).values, n*.01)
        rows = list(history.rows)
        self.assertFalse(rows[-1]['pid_valid'])
        self.assertTrue(reduced_segments(rows, 'alpha_q20', 1, 500, require_measurement=True))
        self.assertFalse(reduced_segments(rows, 'reference_rad_q20', 1, 500,
                                         require_pid=True, require_measurement=True))
        class Canvas:
            def __init__(self): self.colors = []
            def winfo_width(self): return 1180
            def winfo_height(self): return 600
            def delete(self, *_): pass
            def create_line(self, *_, **options): self.colors.append(options['fill'])
            def create_oval(self, *_, **options): self.colors.append(options['fill'])
            def create_text(self, *_, **options): pass
        canvas = Canvas()
        Window.draw_wave(canvas, rows)
        self.assertIn('#59b9ff', canvas.colors)
        self.assertIn('#f6bd4f', canvas.colors)
        self.assertNotIn('#e5a5ed', canvas.colors)
        self.assertNotIn('#f09068', canvas.colors)

    def test_held_or_stale_measurement_is_not_extended_as_a_new_point(self):
        history = TelemetryHistory()
        for seq, capture in enumerate((100, 110, 120)):
            history.receive(decode(telemetry_packet(sequence=seq, stamp=100, capture=capture,
                alpha=123, encoder=456, flags=0x40)).values, seq*.01)
        self.assertEqual(history.snapshot(.021)['measurement_state'], 'stale')
        rows = list(history.rows)
        self.assertTrue(rows[1]['host_measurement_held'])
        self.assertFalse(rows[2]['host_measurement_fresh'])
        groups = reduced_segments(rows, 'alpha_q20', 1, 500, require_measurement=True)
        self.assertEqual(sum(len(group) for group in groups), 1)

    def make_rows(self):
        history = TelemetryHistory()
        for n in range(1001):
            alpha = 200000 if n == 502 else round(math.sin(n*.08)*100)
            row = decode(telemetry_packet(sequence=n, stamp=n*10, alpha=alpha)).values
            history.receive(row, n*.01)
        return list(history.rows)

    def test_pixel_extrema_and_chronological_order(self):
        groups = reduced_segments(self.make_rows(), 'alpha_q20', 1, 30, require_pid=True)
        points = [p for g in groups for p in g]
        self.assertEqual(max(y for _, y in points), 200000)
        self.assertLessEqual(len(points), 4*32)
        self.assertEqual([p[0] for p in points], sorted(p[0] for p in points))

    def test_no_lines_across_loss_or_invalid_pid(self):
        rows = self.make_rows()
        rows[400]['gap_before'] = True
        rows[600]['pid_valid'] = False
        groups = reduced_segments(rows, 'alpha_q20', 1, 200, require_pid=True)
        self.assertEqual(len(groups), 3)
        self.assertLess(groups[0][-1][0], groups[1][0][0])
        self.assertLess(groups[1][-1][0], groups[2][0][0])

    def test_huge_target_sub_count_difference_survives_before_float(self):
        rows = self.make_rows()[-3:]
        origin = 2**62
        for n, row in enumerate(rows):
            row['target_count_q32'] = origin*2**32+n
        points = reduced_segments(rows, lambda r: r['target_count_q32']-origin*2**32,
                                  1/2**32, 100)[0]
        self.assertEqual(points[-1][1], 2/2**32)


class RecorderTests(unittest.TestCase):
    def test_exact_csv_readonly_and_close_flush(self):
        with tempfile.TemporaryDirectory() as directory:
            history = TelemetryHistory()
            recorder = SessionRecorder(directory, CSV_FIELDS)
            target = 2**90+1
            for n in range(250):
                row = history.receive(decode(telemetry_packet(sequence=n, stamp=n*10, target=target)).values, n*.01)
                recorder.submit('wave', row)
            recorder.submit('readonly', dict(page=6, gains_q24=[1,2,3]))
            recorder.close()
            with (Path(directory)/'telemetry.csv').open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 250)
            self.assertEqual(int(rows[-1]['target_count_q32']), target)
            self.assertEqual(int(rows[-1]['sequence']), 249)
            self.assertEqual(json.loads((Path(directory)/'readonly.jsonl').read_text())['page'], 6)
            self.assertIsNone(recorder.error)
            with self.assertRaises(RuntimeError):
                recorder.submit('wave', rows[0])

    def test_bounded_periodic_flush(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = SessionRecorder(directory, CSV_FIELDS)
            row = TelemetryHistory().receive(decode(telemetry_packet()).values)
            try:
                recorder.submit('wave', row)
                deadline = time.monotonic()+1
                path = Path(directory)/'telemetry.csv'
                while time.monotonic() < deadline:
                    if path.exists() and len(path.read_text(encoding='utf-8-sig').splitlines()) >= 2:
                        break
                    time.sleep(.02)
                self.assertTrue(path.exists())
                self.assertEqual(len(path.read_text(encoding='utf-8-sig').splitlines()), 2)
            finally:
                recorder.close()

    def test_long_stream_decode_log_throughput_above_100hz(self):
        count = 4000
        stream = bytearray()
        for n in range(count):
            stream.extend(telemetry_packet(sequence=n, stamp=n*10))
            if n % 5 == 0:
                stream.extend(encode_ack(n, 1, 0, bytes(16)))
        started = time.monotonic()
        history, decoder, acks = TelemetryHistory(limit=1000), StreamDecoder(), 0
        with tempfile.TemporaryDirectory() as directory:
            recorder = SessionRecorder(directory, CSV_FIELDS)
            for offset in range(0, len(stream), 4096):
                for frame in decoder.feed(stream[offset:offset+4096]):
                    if frame.kind == 'telemetry':
                        recorder.submit('wave', history.receive(frame.values, frame.values['sequence']*.01))
                    else:
                        acks += 1
            recorder.close()
            self.assertEqual(recorder.rows_written, count)
            self.assertEqual(recorder.lost_rows, 0)
            self.assertEqual(len(history.rows), 1000)
            self.assertEqual(history.gaps, 0)
            self.assertEqual(acks, 800)
            seconds = time.monotonic()-started
            self.assertGreater(count/seconds, 100, f'{count/seconds:.1f}samples/s')
            print(f'PASS synthetic40s/100Hz stream plus800ACK: {count/seconds:.1f}samples/s offline decode+CSV')


if __name__ == '__main__':
    unittest.main()
