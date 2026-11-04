"""One serial owner; STOP invalidates all unsent packets, never reconnects."""
from collections import deque
from datetime import datetime
from pathlib import Path
import json
import threading
import time
from hardware_mutex import SharedMutex
from tune_protocol import StreamDecoder


class Outbox:
    def __init__(self):
        self.lock = threading.Lock()
        self.queue = deque()
        self.closed = False

    def submit(self, packet, cid, stop=False):
        with self.lock:
            if self.closed:
                raise RuntimeError('串口发送队列已关闭')
            if stop:
                self.queue.clear()
            self.queue.append((bytes(packet), cid))

    def cancel(self):
        with self.lock:
            self.queue.clear()

    def close(self):
        with self.lock:
            self.closed = True
            self.queue.clear()

    def write_one(self, writer):
        # Admission and actual write share this lock. A packet already being
        # written cannot be recalled; STOP is the next packet after that write.
        with self.lock:
            if self.closed or not self.queue:
                return None
            packet, cid = self.queue.popleft()
            sent_at = time.monotonic()
            writer(packet)
            return cid, sent_at


class SerialWorker(threading.Thread):
    def __init__(self, port, events, log_dir):
        super().__init__(daemon=True)
        self.port, self.events, self.log_dir = port, events, Path(log_dir)
        self.quit = threading.Event()
        self.outbox = Outbox()

    def send(self, packet, cid, stop=False):
        self.outbox.submit(packet, cid, stop)

    def close(self):
        self.outbox.close()
        self.quit.set()

    def run(self):
        try:
            from serial_port import SerialPort
            self.log_dir.mkdir(parents=True, exist_ok=True)
            decoder = StreamDecoder()
            with SharedMutex(), SerialPort(self.port) as serial, \
                    (self.log_dir/'raw.bin').open('wb') as raw, \
                    (self.log_dir/'frames.jsonl').open('w', encoding='utf-8') as frames:
                self.events.put(('connected', None, time.monotonic()))
                while not self.quit.is_set():
                    result = self.outbox.write_one(serial.write)
                    if result:
                        self.events.put(('sent', result[0], result[1]))
                    data = serial.read(4096)
                    if data:
                        raw.write(data)
                        raw.flush()
                        for frame in decoder.feed(data):
                            now = time.monotonic()
                            frames.write(json.dumps({'host_time': datetime.now().astimezone().isoformat(),
                                'raw_hex': frame.raw.hex(), 'values': frame.values}, ensure_ascii=False)+'\n')
                            frames.flush()
                            self.events.put(('frame', frame, now))
        except Exception as exc:
            self.events.put(('error', str(exc), time.monotonic()))
        finally:
            self.outbox.close()
            self.events.put(('disconnected', None, time.monotonic()))
