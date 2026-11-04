"""Read-only cursor extension for the unchanged delivered SP24 HTTP bridge."""
from itertools import islice
import math
import time
from urllib.parse import parse_qs, urlsplit
import uuid

WIDE_FIELDS = frozenset(('encoder_count', 'target_count_q32', 'reference_rad_q20'))


def portable(value, name=None):
    """Keep raw wide integers exact and prohibit non-finite JSON numbers."""
    if isinstance(value, dict):
        return {key: portable(item, key) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [portable(item) for item in value]
    if type(value) is int and (name in WIDE_FIELDS or abs(value) > 2**53-1):
        return str(value)
    if type(value) is float and not math.isfinite(value):
        return None
    return value


class WaveformReader:
    def __init__(self, engine):
        self.engine = engine
        self.wave = None
        self.stream = None
        self.clock_offset = time.time()-time.monotonic()

    def read(self, path):
        query = parse_qs(urlsplit(path).query, keep_blank_values=True)
        if set(query)-{'after', 'stream', 'limit'} or any(len(v) != 1 for v in query.values()):
            raise ValueError('waveform 查询字段无效')
        def integer(name, default):
            value = query.get(name, [None])[0]
            if value is None:
                return default
            if not value.isascii() or not value.isdecimal() or len(value) > 20:
                raise ValueError(name+' 必须是非负整数')
            return int(value)
        after, limit = integer('after', None), integer('limit', 512)
        if not 1 <= limit <= 512:
            raise ValueError('limit 须为 1..512')
        stream = query.get('stream', [None])[0]
        with self.engine.lock:
            wave = self.engine.wave
            if wave is not self.wave:
                self.wave, self.stream = wave, uuid.uuid4().hex
            tail = wave.accepted
            head = tail-len(wave.rows)+1
            overrun = ((stream is not None and stream != self.stream) or
                       (after is not None and (after < head-1 or after > tail)))
            start = head-1 if after is None or overrun else after
            # Copy only the requested bounded slice while holding the vendor lock.
            rows = [dict(row) for row in islice(wave.rows, start-(head-1), start-(head-1)+limit)]
            token = self.stream
        for row in rows:
            row['received_at'] = row['host_monotonic']+self.clock_offset
        return portable(dict(stream=token, cursor=start+len(rows), head=head, tail=tail,
                             overrun=overrun, samples=rows))


def install(module):
    """Extend only HTTP reads; all commands, guards and control run in vendor code."""
    if getattr(module, '_sp24_waveform_installed', False):
        return
    original = module.start_bridge

    def start_bridge(engine, port):
        reader = WaveformReader(engine)
        server = original(engine, port)
        base = server.RequestHandlerClass

        class Handler(base):
            def respond(self, code, value):
                return super().respond(code, portable(value))

            def do_GET(self):
                if urlsplit(self.path).path != '/waveform':
                    return super().do_GET()
                if not self.guard():
                    return
                try:
                    self.respond(200, reader.read(self.path))
                except (ValueError, TypeError) as exc:
                    self.respond(400, {'error': str(exc)})

        server.RequestHandlerClass = Handler
        return server

    module.start_bridge = start_bridge
    module._sp24_waveform_installed = True
