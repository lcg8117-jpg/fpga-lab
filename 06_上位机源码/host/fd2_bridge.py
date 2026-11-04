"""Read-only FD2 observation and explicit, once-only commands around original Host."""

from collections import deque
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import importlib.util
from itertools import islice
import json
import math
import re
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parent / "fd2" / "upstream" / "host"
_MODULES = None


def load_upstream():
    """Load delivered dependencies without replacing the application's app/protocol modules."""
    global _MODULES
    if _MODULES is not None:
        return _MODULES
    names = (
        "protocol",
        "session_log",
        "gain_average",
        "client",
        "trace_log",
        "log_policy",
        "bt10_profile",
        "key_actions",
        "hardware_mutex",
        "dual_transport",
        "motion_plot",
        "raw_keyboard",
        "ble_transport",
        "app",
    )
    saved = {name: sys.modules.get(name) for name in names}
    sys.path.insert(0, str(ROOT))
    try:
        for name in names:
            sys.modules.pop(name, None)
        import importlib

        result = {
            name: importlib.import_module(name)
            for name in ("protocol", "client", "trace_log")
        }
        _MODULES = result
    finally:
        sys.path.remove(str(ROOT))
        for name in names:
            if saved[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved[name]
    return _MODULES


def json_safe(value):
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > 2**53 - 1 else value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {
            str(k): json_safe(v) for k, v in value.items() if isinstance(k, (str, int))
        }
    if isinstance(value, (list, tuple, deque)):
        return [json_safe(v) for v in value]
    return str(value)


class Bridge:
    def __init__(self, variant, *, capacity=64000, replay=False):
        if variant not in range(1, 6):
            raise ValueError("variant must be 1..5")
        self.dispatch_lock = threading.RLock()
        self.variant = variant
        self.lock = threading.RLock()
        self.rows = deque(maxlen=capacity)
        self.fingerprints = {}
        self.cursor = 0
        self.session = uuid.uuid4().hex
        self.owner = None
        self.requests = {}
        self.wire_requests = {}
        self.queue_requests = {}
        self.wire_opcodes = {}
        self.replay = dict(active=replay, finished=False, label="")
        self._last = {}
        self._reset_count = 0
        self.state = load_upstream()["client"].HostState(variant)
        self.server = None

    def new_session(self):
        with self.dispatch_lock, self.lock:
            self.session = uuid.uuid4().hex
            self.rows.clear()
            self._last.clear()
            for item in self.requests.values():
                if item["state"] in ("queued", "pending"):
                    item.update(
                        state="indeterminate",
                        message="Host session changed; outcome unknown",
                    )
            self.wire_requests.clear()
            self.queue_requests.clear()
            self.wire_opcodes.clear()

    def attach(self, owner):
        with self.dispatch_lock, self.lock:
            self.new_session()
            self.owner = owner
            self.state = owner.state
            self._reset_count = owner.state.board_resets

    def capture(self, frame, *, received_at=None):
        kind = frame.get("kind")
        if kind not in ("telemetry", "diagnostic") or (
            kind == "telemetry" and not frame.get("coherent")
        ):
            return
        with self.dispatch_lock, self.lock:
            previous = self._last.get(kind)
            if (
                previous
                and frame.get("board_ms", 0) < previous.get("board_ms", 0)
                and previous.get("board_ms", 0) - frame.get("board_ms", 0) < 0x80000000
            ):
                self.new_session()
                previous = None
            field = "control_epoch" if kind == "telemetry" else "sequence"
            if previous and frame.get(field) == previous.get(field):
                return
            gap = bool(
                previous
                and (
                    (frame.get("sequence", 0) - previous.get("sequence", 0))
                    & 0xFFFFFFFF
                )
                != 1
            )
            if kind == "telemetry" and previous:
                gap = (
                    gap
                    or (
                        (frame.get("board_ms", 0) - previous.get("board_ms", 0))
                        & 0xFFFFFFFF
                    )
                    != 5
                )
            self.cursor += 1
            self.rows.append(
                dict(
                    cursor=self.cursor,
                    received_at=time.time() if received_at is None else received_at,
                    frame=json_safe(frame),
                    gap_before=gap,
                )
            )
            self._last[kind] = dict(frame)

    def waveform(self, after=0, stream=None, limit=512):
        if after < 0 or not 1 <= limit <= 512:
            raise ValueError("invalid cursor or limit")
        with self.lock:
            head = self.rows[0]["cursor"] if self.rows else self.cursor
            mismatch = bool(stream and stream != self.session)
            overrun = mismatch or after < head - 1 or after > self.cursor
            # Cursors are contiguous in this ring. A live reader is normally
            # near the tail; walking 64,000 rows for every small page holds the
            # same lock needed by time-sensitive drawing commands.
            count = len(self.rows)
            if not count:
                selected = []
            elif overrun:
                selected = list(islice(iter(self.rows), limit))
            else:
                start = max(0, after - head + 1)
                stop = min(start + limit, count)
                if start <= count - stop:
                    selected = list(islice(iter(self.rows), start, stop))
                else:
                    selected = list(reversed(list(islice(reversed(self.rows), count - stop, count - start))))
            return dict(
                stream=self.session,
                cursor=selected[-1]["cursor"] if selected else self.cursor,
                head=head,
                tail=self.cursor,
                overrun=overrun,
                samples=selected,
            )

    def _host_snapshot(self, now):
        snapshot = self.state.snapshot()
        if self.replay["active"] and self.variant == 3:
            with self.state.lock:
                replay_now = (
                    self.state.last_control_rx if self.replay["finished"] else now
                )
                snapshot["actual_gain_averages"] = load_upstream()[
                    "client"
                ].gain_average.summarize(
                    self.state.gain_average,
                    replay_now,
                    bool(self.state.last_control_rx),
                )
        return snapshot

    def _expire_requests_locked(self, now):
        for item in self.requests.values():
            if item["state"] == "pending" and now - item.get("written_at", now) > 1.25:
                item.update(
                    state="indeterminate",
                    message="Physical ACK timeout; outcome unknown; never retried",
                )

    def status(self):
        p = load_upstream()["protocol"]
        now = time.monotonic()
        snapshot = self._host_snapshot(now)
        with self.lock:
            self._expire_requests_locked(time.time())
            for key in (
                "history",
                "compact_history",
                "goal_history",
                "config_reads",
                "config_saved",
                "read_config_requests",
            ):
                snapshot.pop(key, None)

            def age(key):
                value = snapshot.get(key, 0)
                return max(0, now - value) if value else None

            serial = getattr(self.owner, "_serial", None)
            errors = getattr(serial, "route_errors", {})
            connected = bool(snapshot.get("connected") and not self.replay["active"])
            usb = connected and (
                getattr(serial, "usb", None) is not None
                if hasattr(serial, "usb")
                else serial is not None
            )
            ble = connected and getattr(serial, "ble", None) is not None
            return json_safe(
                dict(
                    protocol="FD2",
                    variant=self.variant,
                    user_code=f"{p.FIRMWARE[self.variant]:08X}",
                    schema=f"{p.SCHEMAS[self.variant]:08X}",
                    wire_version=p.WIRE_VERSIONS[self.variant],
                    session=self.session,
                    connected=connected,
                    synced=connected
                    and snapshot.get("authenticated", False)
                    and not snapshot.get("error"),
                    status_age_s=age("latest_rx"),
                    control_age_s=age("last_control_rx"),
                    diagnostic_age_s=age("last_diagnostic_rx"),
                    host=snapshot,
                    requests=self.requests,
                    transport=dict(
                        usb_connected=bool(usb and 0 not in errors),
                        ble_connected=bool(ble and 1 not in errors),
                        route_errors=errors,
                    ),
                    replay=self.replay,
                )
            )

    def _validate_command(self, body):
        p = load_upstream()["protocol"]
        actions = {
            "stop": p.STOP,
            "estop": p.ESTOP,
            "zero": p.ZERO,
            "balance": p.RUN,
            "clear-fault": p.CLEAR,
            "start-swing": p.START_SWING,
            "adapt-enable": p.ADAPT_ENABLE,
            "adapt-disable": p.ADAPT_DISABLE,
            "position": p.POSITION,
            "trajectory": p.TRAJECTORY,
            "hold": p.CANCEL_HOLD,
        }
        if not isinstance(body, dict):
            raise ValueError("command must be a JSON object")
        rid = body.get("request_id")
        action = body.get("action")
        value = body.get("value")
        if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", rid):
            raise ValueError("request_id required")
        if set(body) - {"request_id", "action", "value"}:
            raise ValueError("unknown command fields")
        if action not in actions:
            raise ValueError("unsupported action")
        if action in ("position", "trajectory"):
            if type(value) is not int or not 1 <= value <= 6:
                raise ValueError("value must be integer 1..6")
        elif "value" in body:
            raise ValueError("action does not accept value")
        if action == "start-swing" and self.variant not in (1, 2, 4):
            raise ValueError("variant has no swing-up")
        if action.startswith("adapt-") and self.variant != 3:
            raise ValueError("variant has no adaptive control")
        if action in ("position", "hold") and self.variant < 4:
            raise ValueError("variant has no positions")
        if action == "trajectory" and self.variant != 5:
            raise ValueError("variant has no trajectories")
        return rid, action, value, actions[action]

    def command(self, body):
        rid, action, value, opcode = self._validate_command(body)
        with self.lock:
            if self.replay["active"]:
                raise ValueError("replay rejects all hardware commands")
            previous = self.requests.get(rid)
            signature = dict(action=action, value=value)
            if previous:
                if self.fingerprints[rid] != signature:
                    raise ValueError("request_id content conflict")
                return dict(previous)
            if self.owner is None:
                raise ValueError("Host is disconnected")
            result = dict(
                request_id=rid,
                state="queued",
                action=action,
                message="Queued once; awaiting physical ACK",
                session=self.session,
                created_at=time.time(),
            )
            self.requests[rid] = result
            self.fingerprints[rid] = signature
            owner = self.owner
        try:
            self._dispatch_request(
                owner,
                result,
                opcode,
                bytes((value,)) if action in ("position", "trajectory") else b"",
            )
        except Exception as exc:
            with self.lock:
                result.update(state="rejected", message=str(exc))
        with self.lock:
            return dict(result)

    def _dispatch_request(self, owner, result, opcode, payload):
        # Gate lifecycle changes through queue insertion. The original command
        # obtains its own state/command locks; never hold bridge.lock across it.
        with self.dispatch_lock:
            with self.lock:
                if owner is not self.owner or result["session"] != self.session:
                    result.update(
                        state="indeterminate",
                        message="Session changed before queue insertion; command not sent",
                    )
                    return
            owner.command(opcode, payload=payload, source="WEB/" + result["request_id"])

    def trace(self, event, fields):
        with self.lock:
            source = fields.get("source", "")
            wire_id = fields.get("request_id")
            if event == "write_requested" and source.startswith("WEB/"):
                rid = source[4:]
                if rid in self.requests:
                    self.wire_requests[wire_id] = rid
                    self.wire_opcodes[wire_id] = fields.get("opcode")
                    self.requests[rid].update(
                        state="pending", wire_request_id=wire_id, written_at=time.time()
                    )
            if event == "queued" and source.startswith("WEB/"):
                self.queue_requests[fields.get("queue_id")] = source[4:]
            if event == "queue_cancelled_by_stop":
                cancelled = self.queue_requests.pop(fields.get("queue_id"), None)
                if cancelled in self.requests:
                    self.requests[cancelled].update(
                        state="rejected",
                        message="Cancelled by STOP before transmission",
                    )
            rid = self.wire_requests.get(wire_id)
            if (
                rid
                and event == "ack"
                and fields.get("matched")
                and fields.get("opcode") == self.wire_opcodes.get(wire_id)
            ):
                self.requests[rid].update(
                    state="accepted" if fields["status"] == 0 else "rejected",
                    ack_status=fields["status"],
                    message="Physical ACK; mechanical completion is reported separately",
                )
            if rid and event in ("write_unknown", "timeout"):
                self.requests[rid].update(
                    state="indeterminate",
                    message="Physical outcome unknown; never retried",
                )

    def serve(self, port=8371):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, data):
                raw = json.dumps(json_safe(data), ensure_ascii=False).encode("utf8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def local(self):
                return (
                    self.headers.get("Origin") is None
                    and self.headers.get("X-J280-Local") == "1"
                    and self.headers.get("Host")
                    == f"127.0.0.1:{self.server.server_port}"
                )

            def do_GET(self):
                if not self.local():
                    return self.reply(403, {"error": "local bridge clients only"})
                try:
                    url = urlparse(self.path)
                    q = parse_qs(url.query)
                    if url.path == "/status":
                        return self.reply(200, bridge.status())
                    if url.path == "/waveform":
                        return self.reply(
                            200,
                            bridge.waveform(
                                int(q.get("after", ["0"])[0]),
                                q.get("stream", [None])[0],
                                int(q.get("limit", ["512"])[0]),
                            ),
                        )
                    if url.path.startswith("/request/"):
                        item = bridge.requests.get(url.path.split("/")[-1])
                        return self.reply(
                            200 if item else 404, item or {"error": "request not found"}
                        )
                    self.reply(404, {"error": "not found"})
                except (ValueError, TypeError) as exc:
                    self.reply(400, {"error": str(exc)})

            def do_POST(self):
                if not self.local():
                    return self.reply(403, {"error": "local bridge clients only"})
                try:
                    if self.path != "/command":
                        return self.reply(404, {"error": "not found"})
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= length <= 8192:
                        raise ValueError("Content-Length must be 1..8192")
                    self.reply(200, bridge.command(json.loads(self.rfile.read(length))))
                except (ValueError, TypeError, KeyError) as exc:
                    result = {"error": str(exc)}
                    if getattr(exc, "code", None):
                        result["code"] = exc.code
                    self.reply(400, result)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(
            target=self.server.serve_forever, name="FD2HTTP", daemon=True
        ).start()
        return self.server


def install(bridge, modules=None):
    modules = load_upstream() if modules is None else modules
    cls = modules["client"].SerialOwner
    original_init = cls.__init__
    original_receive = cls._receive
    original_trace = cls.trace

    def init(owner, *args, **kwargs):
        original_init(owner, *args, **kwargs)
        bridge.attach(owner)

    def receive(owner, raw, item):
        with owner.state.lock:
            old_control = owner.state.last_control
            old_diagnostic = owner.state.latest_diagnostic
        # Original receive deliberately releases state.lock before independent
        # readback commands. Wrapping it in that lock creates a lock inversion.
        original_receive(owner, raw, item)
        with owner.state.lock:
            resets = owner.state.board_resets
            accepted = (
                item.get("kind") == "telemetry"
                and owner.state.last_control is item
                and old_control is not item
            ) or (
                item.get("kind") == "diagnostic"
                and owner.state.latest_diagnostic is item
                and old_diagnostic is not item
            )
        with bridge.dispatch_lock:
            if owner is not bridge.owner:
                return
            if resets != bridge._reset_count:
                bridge.new_session()
                bridge._reset_count = resets
            if accepted:
                bridge.capture(item)

    def trace(owner, event, **fields):
        original_trace(owner, event, **fields)
        if owner is bridge.owner:
            bridge.trace(event, fields)

    cls.__init__ = init
    cls._receive = receive
    cls.trace = trace
