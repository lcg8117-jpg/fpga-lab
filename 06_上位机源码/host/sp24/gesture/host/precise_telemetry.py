"""Native-precision telemetry, bounded-time buffered recording and plot reduction."""
from collections import deque
import csv
from datetime import datetime
from decimal import Decimal, localcontext
import json
from pathlib import Path
import queue
import threading
import time

WAVE_FIELDS = ('host_monotonic', 'host_time', 'session_epoch', 'sequence', 'timestamp_ms',
    'capture_ms', 'board_elapsed_ms', 'epoch_age_ms', 'sequence_gap', 'gap_before',
    'alpha_q20', 'alpha_dot_q20', 'theta_dot_q20', 'encoder_count', 'target_count_q32',
    'reference_rad_q20', 'pwm', 'flags', 'fault', 'mode', 'pid_valid', 'velocity_valid',
    'motor', 'zero_valid', 'saturation', 'angle_revision', 'position_revision',
    'alpha_rad', 'alpha_deg', 'alpha_rate_rad_s', 'theta_rate_rad_s',
    'encoder_deg', 'target_count', 'target_deg', 'reference_rad', 'reference_deg', 'pwm_percent')
WAVE_FIELDS += ('encoder_count_hex', 'target_count_q32_hex', 'reference_rad_q20_hex')
WAVE_FIELDS += ('host_measurement_fresh', 'host_measurement_held')
PI = Decimal('3.14159265358979323846264338327950288419716939937510')
MEASUREMENT_MAX_AGE_MS = 10


def physical_values(row):
    """Raw integer columns remain authoritative; Q32 conversion never uses float."""
    with localcontext() as ctx:
        ctx.prec = 80
        q20, q32 = Decimal(2**20), Decimal(2**32)
        alpha = Decimal(row['alpha_q20']) / q20
        reference = Decimal(row['reference_rad_q20']) / q20
        target = Decimal(row['target_count_q32']) / q32
        return dict(alpha_rad=format(alpha, 'f'), alpha_deg=f'{alpha * 180 / PI:.9f}',
            alpha_rate_rad_s=format(Decimal(row['alpha_dot_q20']) / q20, 'f'),
            theta_rate_rad_s=format(Decimal(row['theta_dot_q20']) / q20, 'f'),
            encoder_deg=f'{Decimal(row["encoder_count"]) / 3:.12f}',
            target_count=format(target, 'f'), target_deg=f'{target / 3:.12f}',
            reference_rad=format(reference, 'f'), reference_deg=f'{reference * 180 / PI:.9f}',
            pwm_percent=format(Decimal(row['pwm']) * 100 / 32768, 'f'),
            encoder_count_hex=f'0x{row["encoder_count"] & (2**64-1):016X}',
            target_count_q32_hex=f'0x{row["target_count_q32"] & (2**96-1):024X}',
            reference_rad_q20_hex=f'0x{row["reference_rad_q20"] & (2**64-1):016X}')


class TelemetryHistory:
    """Track board time separately from host arrival time; expose every discontinuity."""
    def __init__(self, limit=6000):
        self.rows = deque(maxlen=limit)
        self.arrivals = deque(maxlen=501)
        self.accepted = self.gaps = self.duplicates = self.resets = 0
        self.session_epoch = 0
        self.last = None
        self.sample_observed = False
        self.decoder = dict(bad_frames=0, crc_errors=0, identity_errors=0, discarded_bytes=0)

    def receive(self, values, at=None):
        now = time.monotonic() if at is None else at
        row = dict(values)
        gap, reset = 0, False
        if self.last is not None:
            delta = (row['sequence'] - self.last['sequence']) & 0xFFFFFFFF
            elapsed = (row['capture_ms'] - self.last['capture_ms']) & 0xFFFFFFFF
            reset = delta >= 0x80000000 or elapsed >= 0x80000000
            if delta == 0 and not reset:
                self.duplicates += 1
                return None
            if reset:
                self.session_epoch += 1
                self.resets += 1
                self.arrivals.clear()
                self.rows.clear()
                self.sample_observed = False
                board_ms = 0
            else:
                gap = delta - 1
                board_ms = self.last['board_elapsed_ms'] + elapsed
        else:
            board_ms = 0
        self.gaps += gap
        epoch_age = (row['capture_ms'] - row['timestamp_ms']) & 0xFFFFFFFF
        self.sample_observed |= row['timestamp_ms'] != 0 or row['pid_valid']
        held = self.last is not None and not reset and row['timestamp_ms'] == self.last['timestamp_ms']
        row.update(host_monotonic=now, host_time=datetime.now().astimezone().isoformat(timespec='microseconds'),
            session_epoch=self.session_epoch, board_elapsed_ms=board_ms,
            epoch_age_ms=epoch_age,
            host_measurement_fresh=self.sample_observed and epoch_age <= MEASUREMENT_MAX_AGE_MS,
            host_measurement_held=held,
            sequence_gap=gap, gap_before=bool(gap or reset))
        row.update(physical_values(row))
        self.last = row
        self.rows.append(row)
        self.arrivals.append((now, board_ms, self.session_epoch))
        self.accepted += 1
        return row

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        age = None if self.last is None else max(0.0, now - self.last['host_monotonic'])
        samples = len(self.arrivals)
        host_span = self.arrivals[-1][0] - self.arrivals[0][0] if samples > 1 else 0
        board_span = self.arrivals[-1][1] - self.arrivals[0][1] if samples > 1 else 0
        return dict(accepted=self.accepted, missing=self.gaps, duplicates=self.duplicates,
            resets=self.resets, session_epoch=self.session_epoch, age_s=age,
            epoch_age_ms=None if self.last is None else self.last['epoch_age_ms'],
            stale=age is None or age > .25,
            measurement_state=('waiting' if not self.sample_observed else
                'stale' if age is None or age > .25 or not self.last['host_measurement_fresh'] else
                'held' if self.last['host_measurement_held'] else 'fresh'),
            received_hz=(samples - 1) / host_span if host_span > 0 else 0,
            board_hz=(samples - 1) * 1000 / board_span if board_span > 0 else 0,
            **self.decoder)


class SessionRecorder(threading.Thread):
    """Disk work has its own thread; flush every250ms and on clean close."""
    FLUSH_SECONDS = .25

    def __init__(self, directory, legacy_fields):
        super().__init__(daemon=True)
        self.directory, self.legacy_fields = Path(directory), legacy_fields
        self.queue = queue.Queue(maxsize=8192)
        self.error = None
        self.rows_written = 0
        self.high_water = 0
        self.lost_rows = 0
        self.closed = False
        self.start()

    def submit(self, kind, value):
        if self.closed:
            raise RuntimeError('session recorder is closed')
        if self.error:
            self.lost_rows += 1
            raise RuntimeError(self.error)
        try:
            self.queue.put_nowait((kind, dict(value)))
        except queue.Full:
            self.lost_rows += 1
            self.error = 'Recording queue overflow; new CSV rows are not being recorded. Check disk.'
            raise RuntimeError(self.error)
        self.high_water = max(self.high_water, self.queue.qsize())

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.queue.put(None, timeout=1)
            except queue.Full:
                raise RuntimeError('Recording queue blocked; close could not drain pending records')
            self.join(timeout=5)
        if self.is_alive():
            raise RuntimeError('session recorder close exceeded5s; records still pending')
        if self.error:
            raise RuntimeError(self.error)

    def run(self):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory/'samples.csv').open('w', newline='', encoding='utf-8-sig') as legacy, \
                    (self.directory/'telemetry.csv').open('w', newline='', encoding='utf-8-sig') as wave, \
                    (self.directory/'readonly.jsonl').open('w', encoding='utf-8') as readonly:
                old_writer, wave_writer = csv.DictWriter(legacy, self.legacy_fields), csv.DictWriter(wave, WAVE_FIELDS)
                old_writer.writeheader()
                wave_writer.writeheader()
                flushed = time.monotonic()
                while True:
                    timeout = max(.001, self.FLUSH_SECONDS - (time.monotonic() - flushed))
                    try:
                        item = self.queue.get(timeout=timeout)
                    except queue.Empty:
                        item = ('flush', {})
                    if item is None:
                        break
                    kind, row = item
                    if kind == 'wave':
                        wave_writer.writerow(row)
                        self.rows_written += 1
                    elif kind == 'legacy':
                        old_writer.writerow(row)
                    elif kind == 'readonly':
                        readonly.write(json.dumps(row, ensure_ascii=False)+'\n')
                    if time.monotonic() - flushed >= self.FLUSH_SECONDS:
                        for stream in (legacy, wave, readonly):
                            stream.flush()
                        flushed = time.monotonic()
                for stream in (legacy, wave, readonly):
                    stream.flush()
        except Exception as exc:
            self.error = f'Recording failed: {exc}'


def reduced_segments(rows, field, scale, width, window_ms=10000, require_pid=False, require_measurement=False):
    """Keep chronological first/min/max/last per pixel, never bridge missing data."""
    if not rows:
        return []
    end = rows[-1]['board_elapsed_ms']
    groups, segment, bucket, values = [], [], None, []

    def flush_bucket():
        if values:
            selected = {0, len(values)-1,
                        min(range(len(values)), key=lambda i: values[i][1]),
                        max(range(len(values)), key=lambda i: values[i][1])}
            segment.extend(values[i] for i in sorted(selected))
            values.clear()

    for row in rows:
        age = end - row['board_elapsed_ms']
        if age > window_ms:
            continue
        invalid = ((require_pid and not row['pid_valid']) or
                   (require_measurement and (not row['host_measurement_fresh'] or row['host_measurement_held'])))
        if row['gap_before'] or invalid:
            flush_bucket()
            if segment:
                groups.append(segment)
                segment = []
            bucket = None
        if invalid:
            continue
        x = (window_ms - age) / window_ms * max(1, width)
        pixel = int(x)
        if bucket != pixel:
            flush_bucket()
            bucket = pixel
        value = field(row) if callable(field) else row[field]
        values.append((x, value * scale))
    flush_bucket()
    if segment:
        groups.append(segment)
    return groups
