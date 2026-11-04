"""Lossless raw transport and decoded evidence, compressed off the owner thread."""
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import queue
import threading
import time
import uuid


class SessionLog:
    """A bounded nonblocking handoff; failure is explicit, never a silent drop."""

    def __init__(self, directory, *, capacity=100000):
        self.directory = Path(directory)
        self.queue = queue.Queue(maxsize=capacity)
        self.path = None
        self.error = ""
        self.accepted = 0
        self.written = 0
        self.thread = None
        self.stopping = threading.Event()

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = self.directory / f"j280_{stamp}_{uuid.uuid4().hex[:8]}.jsonl.gz"
        self.thread = threading.Thread(target=self._run, name="J280GzipLog", daemon=True)
        self.thread.start()
        self.submit("session", format=1, mode="raw_and_decoded", utc=stamp)
        return self

    def submit(self, event, **fields):
        if self.error:
            return False
        item = dict(event=event, host_monotonic_ns=time.monotonic_ns(), **fields)
        try:
            self.queue.put_nowait(item)
            self.accepted += 1
            return True
        except queue.Full:
            self.error = "日志队列已满：控制锁定，不能声称日志完整"
            return False

    def _run(self):
        try:
            with gzip.open(self.path, "xt", encoding="utf-8", compresslevel=3) as stream:
                flushed = time.monotonic()
                while not self.stopping.is_set() or not self.queue.empty():
                    try:
                        item = self.queue.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    stream.write(json.dumps(item, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
                    self.written += 1
                    if time.monotonic() - flushed >= 1:
                        stream.flush()
                        flushed = time.monotonic()
        except Exception as exc:
            self.error = f"日志写入失败：{exc}"

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                self.error = "日志压缩未在10秒内完成；请保留进程并检查磁盘"

    def metrics(self):
        return dict(path=str(self.path) if self.path else "", accepted=self.accepted,
                    written=self.written, queued=self.queue.qsize(), error=self.error)
