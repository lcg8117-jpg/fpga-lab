"""Paced RX-only replay of the delivered ForestFive.Physical.v1 log format."""

import json
import threading
import time
import zipfile
from pathlib import Path
from host.fd2_bridge import load_upstream


class Replay:
    def __init__(self, bridge, path, *, speed=1.0):
        if speed <= 0:
            raise ValueError("replay speed must be positive")
        self.bridge = bridge
        self.path = Path(path)
        self.speed = speed
        self.cancel = threading.Event()
        self.thread = None
        with zipfile.ZipFile(self.path) as z:
            session = json.loads(z.read("session.json"))
        if session.get("format") != "ForestFive.Physical.v1":
            raise ValueError("unsupported log format")
        if session.get("variant") != bridge.variant:
            raise ValueError("replay variant does not match selection")
        if session.get("chunk_header") not in (None, "<QQBBII"):
            raise ValueError("unsupported chunk header")
        bridge.replay.update(active=True, finished=False, label=self.path.name)
        self.modules = load_upstream()
        self.owner = self.modules["client"].SerialOwner(
            bridge.variant, "", self.path.parent
        )
        bridge.attach(self.owner)

    def run(self):
        parsers = {
            0: self.modules["protocol"].FrameParser(),
            1: self.modules["protocol"].FrameParser(),
        }
        first = None
        start = time.monotonic()
        try:
            for wall, mono, route, direction, raw in self.modules[
                "trace_log"
            ].iter_chunks(self.path):
                if self.cancel.is_set():
                    return
                if first is None:
                    first = mono
                delay = (mono - first) / 1e9 / self.speed - (time.monotonic() - start)
                if delay > 0 and self.cancel.wait(delay):
                    return
                if direction != 0:
                    continue
                if route not in parsers:
                    raise ValueError("unknown physical route")
                for frame, item in parsers[route].feed(raw):
                    # ACKs are historical evidence only. Never run the live ACK branch,
                    # which may schedule independent configuration readbacks.
                    if item["kind"] not in ("telemetry", "diagnostic"):
                        continue
                    self.owner._receive(frame, item)
                    with self.owner.state.lock:
                        accepted = (
                            self.owner.state.last_control is item
                            or self.owner.state.latest_diagnostic is item
                        )
                    if accepted:
                        self.bridge.capture(item)
                with self.owner.state.lock:
                    self.owner.state.crc_errors = sum(
                        p.crc_errors for p in parsers.values()
                    )
        except Exception as exc:
            with self.owner.state.lock:
                self.owner.state.error = "Replay: " + str(exc)
        finally:
            self.bridge.replay["finished"] = True

    def start(self):
        self.thread = threading.Thread(target=self.run, name="FD2Replay", daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.cancel.set()
        if self.thread:
            self.thread.join(timeout=2)
