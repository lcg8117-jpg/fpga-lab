"""Display-only conversion of SP24 capture batches. No control or transport writes."""
from collections import deque
import math
import re

CHANNELS = ('angle_deg', 'angle_velocity_deg_s', 'alpha_ref_deg',
            'arm_relative_deg', 'target_relative_deg', 'duty_actual_percent')
RAW_FIELDS = ('alpha_q20', 'alpha_dot_q20', 'theta_dot_q20', 'encoder_count',
              'target_count_q32', 'reference_rad_q20', 'pwm', 'flags', 'fault', 'mode',
              'pid_valid', 'velocity_valid', 'motor', 'zero_valid', 'saturation',
              'angle_revision', 'position_revision', 'sequence_gap', 'epoch_age_ms',
              'host_measurement_fresh', 'host_measurement_held')


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def raw_integer(value, bits, signed=True):
    if type(value) is int:
        result = value
    elif isinstance(value, str) and len(value) <= 40 and re.fullmatch(r'-?(0|[1-9][0-9]*)', value):
        result = int(value)
    else:
        return None
    low, high = (-2**(bits-1), 2**(bits-1)-1) if signed else (0, 2**bits-1)
    return result if low <= result <= high else None


def scaled(value, scale):
    result = None if value is None else float(value*scale)
    return result if result is not None and math.isfinite(result) else None


def safe_json(value):
    if isinstance(value, dict):
        return {key: safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(item) for item in value]
    if type(value) is float and not math.isfinite(value):
        return None
    if type(value) is int and abs(value) > 2**53-1:
        return str(value)
    return value


class PreciseTelemetryAdapter:
    def __init__(self):
        self.clear()

    def clear(self):
        self.stream = None
        self.device_epoch = None
        self.origin = None
        self.anchor = None
        self.measurement = None
        self.seen = set()
        self.order = deque()
        self.latest = None
        self.latest_raw = None
        self.pages = {}
        self.page_keys = {}
        self.pending_gap = True
        self.reconnecting = True
        self.overruns = 0

    def invalidate(self):
        # Retain deduplication and the local origin within the same device epoch.
        self.latest = self.latest_raw = None
        if 'gesture_event' in self.pages:
            self.pages['gesture_event'] = dict(self.pages['gesture_event'], new_since_poll=False)
        self.pending_gap = True
        self.reconnecting = True

    def observe(self, snapshot, data, now, live, waveform, reset):
        rows = []
        telemetry = snapshot.get('telemetry') or {}
        if not isinstance(telemetry, dict):
            telemetry = {}
        if isinstance(waveform, dict):
            token = waveform.get('stream')
            if not isinstance(token, str) or not token:
                raise ValueError('SP24 waveform 缺少 stream')
            if token != self.stream:
                self.clear()
                self.stream = token
                reset = True
            if waveform.get('overrun'):
                self.overruns += 1
                self.pending_gap = True
            source_rows = waveform.get('samples')
            if not isinstance(source_rows, list):
                raise ValueError('SP24 waveform samples 不是数组')
            # Reject the whole batch before changing deduplication, so the Hub's
            # retained cursor can recover every row after a malformed response.
            for source in source_rows:
                self.decode_clock(source)
            for source in source_rows:
                row, changed = self.convert(source)
                reset |= changed
                if row is not None:
                    rows.append(row)
        precision = snapshot.get('precise_telemetry')
        precision = dict(precision) if isinstance(precision, dict) else {}
        age = precision.get('age_s')
        latest_age = None if self.latest is None else max(0, now-self.latest['received_at'])
        stale = (not live or not finite(age) or age < 0 or age > .25 or
                 precision.get('stale') is True or latest_age is None or latest_age > .25)
        precision.update(stale=stale, recording_error=snapshot.get('recording_error'),
                         recording_backlog=snapshot.get('recording_backlog', 0),
                         recording_lost_rows=snapshot.get('recording_lost_rows', 0),
                         bridge_overrun=bool(isinstance(waveform, dict) and waveform.get('overrun')),
                         bridge_overruns=self.overruns,
                         origin_count=None if self.origin is None else str(self.origin),
                         received_age_s=latest_age, pid_sample_age_ms=precision.get('epoch_age_ms'))
        data.update(precision=safe_json(precision), arm_relative_deg=None, target_relative_deg=None,
                    gesture_event=None, gesture_runtime=None)
        if live:
            status = data['status']
            mode = raw_integer(status.get('mode'), 8, False)
            data.update(mode=mode, mode_name='SWING' if status.get('swing_active') else
                        {0:'IDLE',1:'ZERO',2:'ARMED',3:'BALANCE',4:'FAULT'}.get(mode,'UNKNOWN'),
                        fault=raw_integer(status.get('fault'),8,False),revision=status.get('revision'))
            self.observe_pages(telemetry, now)
            data.update(gesture_event=self.pages.get('gesture_event'),gesture_runtime=self.pages.get('gesture_runtime'),
                        adaptive=safe_json(telemetry.get('adaptive')),adaptive_gains=safe_json(telemetry.get('adaptive_gains')))
        if not stale:
            latest, raw = self.latest, self.latest_raw
            for name in CHANNELS:
                data[name] = latest[name]
            # Repeated PID epochs do not erase a still-fresh measurement from the display.
            if raw.get('host_measurement_fresh') is True:
                velocity_valid = raw.get('velocity_valid') is True
                data.update(angle_deg=scaled(raw_integer(raw.get('alpha_q20'),32),180/math.pi/2**20),
                            angle_velocity_deg_s=scaled(raw_integer(raw.get('alpha_dot_q20'),32),180/math.pi/2**20) if velocity_valid else None,
                            arm_velocity_deg_s=scaled(raw_integer(raw.get('theta_dot_q20'),32),180/math.pi/2**20) if velocity_valid else None)
                encoder = raw_integer(raw.get('encoder_count'),64)
                data['arm_relative_deg'] = None if encoder is None or self.origin is None else (encoder-self.origin)/3
            data.update(received_at=latest['received_at'],angle_received_at=latest['received_at'] if data['angle_deg'] is not None else 0,
                        arm_received_at=latest['received_at'],fpga_time_ms=latest['sample_ms'],
                        arm_fpga_time_ms=latest['capture_ms'],telemetry_fresh=True,
                        encoder=str(raw.get('encoder_count')) if data['arm_relative_deg'] is not None else None)
        return safe_json(data), rows, reset

    def observe_pages(self, telemetry, now):
        for page, fields in (('gesture_event', ('event_count', 'event_ms')),
                             ('gesture_runtime', ('poll_sequence', 'timestamp_ms'))):
            source = telemetry.get(page)
            if not isinstance(source, dict) or any(type(source.get(key)) is not int for key in fields):
                continue
            key = tuple(source[name] for name in fields)
            changed = self.page_keys.get(page) != key
            if changed or page not in self.pages:
                row = safe_json(dict(source))
                row['received_at'] = now
                if page == 'gesture_event':
                    previous = self.page_keys.get(page)
                    advance = None if previous is None else (key[0]-previous[0])&0xffffffff
                    row['new_since_poll'] = bool(changed and advance is not None and
                                                 0 < advance < 0x80000000 and not self.reconnecting)
                self.pages[page] = row
                self.page_keys[page] = key
            elif page == 'gesture_event':
                self.pages[page] = dict(safe_json(source),received_at=self.pages[page]['received_at'],new_since_poll=False)
            if page == 'gesture_event':
                self.reconnecting = False

    @staticmethod
    def decode_clock(source):
        if not isinstance(source, dict):
            raise ValueError('SP24 waveform 包含非对象样本')
        sequence = raw_integer(source.get('sequence'),32,False)
        capture = raw_integer(source.get('capture_ms'),32,False)
        stamp = raw_integer(source.get('timestamp_ms'),32,False)
        epoch = raw_integer(source.get('session_epoch'),32,False)
        board = source.get('board_elapsed_ms')
        received = source.get('received_at')
        if any(value is None for value in (sequence,capture,stamp,epoch)) or not finite(received) or not finite(board) or board < 0:
            raise ValueError('SP24 waveform 缺少有效板时或 Host 接收时刻')
        return sequence,capture,stamp,epoch,board,received

    def convert(self, source):
        sequence,capture,stamp,epoch,board,received = self.decode_clock(source)
        identity = (self.stream,epoch,sequence)
        if identity in self.seen:
            return None,False
        changed = self.device_epoch is not None and self.device_epoch != epoch
        if self.device_epoch != epoch:
            self.device_epoch = epoch
            self.origin = None
            self.anchor = received-board/1000
            self.measurement = None
            self.pending_gap = True
            self.reconnecting = True
        encoder = raw_integer(source.get('encoder_count'),64)
        target = raw_integer(source.get('target_count_q32'),96)
        reference = raw_integer(source.get('reference_rad_q20'),64)
        if self.origin is None and encoder is not None:
            self.origin = encoder
        plot_at = self.anchor+board/1000
        measurement_at = plot_at-((capture-stamp)&0xffffffff)/1000
        measurement = (epoch,stamp)
        measured = (source.get('host_measurement_fresh') is True and
                    source.get('host_measurement_held') is not True and self.measurement != measurement)
        pid = source.get('pid_valid') is True
        gap = self.pending_gap or source.get('gap_before') is True
        row = dict(sample_id=f'{self.stream}:{epoch}:{sequence}',received_at=received,
                   plot_at=plot_at,measurement_at=measurement_at,sequence=sequence,session_epoch=epoch,
                   sample_ms=stamp,capture_ms=capture,fpga_time_ms=stamp,gap_before=bool(gap),
                   channels=list(CHANNELS),raw={key:safe_json(source[key]) for key in RAW_FIELDS if key in source},
                   angle_deg=scaled(raw_integer(source.get('alpha_q20'),32),180/math.pi/2**20) if measured else None,
                   angle_velocity_deg_s=scaled(raw_integer(source.get('alpha_dot_q20'),32),180/math.pi/2**20) if measured and source.get('velocity_valid') is True else None,
                   alpha_ref_deg=scaled(reference,180/math.pi/2**20) if measured and pid else None,
                   arm_relative_deg=(encoder-self.origin)/3 if measured and encoder is not None and self.origin is not None else None,
                   target_relative_deg=(target-self.origin*2**32)/(3*2**32)
                       if measured and pid and target is not None and self.origin is not None else None,
                   duty_actual_percent=scaled(raw_integer(source.get('pwm'),16),100/32768))
        for name in ('encoder_count','target_count_q32','reference_rad_q20'):
            if name in row['raw']:
                row['raw'][name] = str(source[name])
        if measured:
            self.measurement = measurement
        self.pending_gap = False
        self.latest, self.latest_raw = row, dict(source)
        self.seen.add(identity)
        self.order.append(identity)
        while len(self.order)>12000:
            self.seen.discard(self.order.popleft())
        return row,changed
