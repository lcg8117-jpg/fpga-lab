import unittest
from host.fd2_bridge import Bridge, load_upstream, json_safe


class BridgeTests(unittest.TestCase):
    def test_live_waveform_page_does_not_scan_the_entire_ring(self):
        from collections import deque
        class CountedDeque(deque):
            visits=0
            def __iter__(self):
                for item in super().__iter__():
                    self.visits+=1;yield item
            def __reversed__(self):
                for item in super().__reversed__():
                    self.visits+=1;yield item
        b=Bridge(5);b.rows=CountedDeque(({'cursor':n} for n in range(1,64001)),maxlen=64000);b.cursor=64000
        self.assertEqual([r['cursor'] for r in b.waveform(after=63995,limit=5)['samples']],[63996,63997,63998,63999,64000])
        self.assertLessEqual(b.rows.visits,10)
        b.rows.visits=0
        self.assertEqual([r['cursor'] for r in b.waveform(after=0,limit=3)['samples']],[1,2,3])
        self.assertLessEqual(b.rows.visits,3)

    def test_waveform_fast_pages_match_original_cursor_and_overrun_semantics(self):
        from collections import deque
        b=Bridge(5);rows=[{'cursor':n} for n in range(9,26)]
        b.rows=deque(rows,maxlen=17);b.cursor=25
        for after in range(0,29):
            for stream in (None,'other-session'):
                for limit in (1,3,10,30):
                    overrun=bool(stream and stream!=b.session) or after<8 or after>25
                    expected=[r for r in rows if overrun or r['cursor']>after][:limit]
                    actual=b.waveform(after=after,stream=stream,limit=limit)
                    self.assertEqual(actual['samples'],expected,(after,stream,limit))
                    self.assertEqual(actual['overrun'],overrun)
                    self.assertEqual(actual['cursor'],expected[-1]['cursor'] if expected else 25)

    def test_ring_wide_cursor_and_gap(self):
        b = Bridge(5, capacity=3)
        for n in range(5):
            b.capture(
                {
                    "kind": "telemetry",
                    "coherent": True,
                    "sequence": n,
                    "board_ms": n * 5,
                    "control_epoch": n,
                    "wide_encoder": 2**60,
                },
                received_at=100 + n * 0.005,
            )
        page = b.waveform(after=0, limit=2)
        self.assertTrue(page["overrun"])
        self.assertEqual(len(page["samples"]), 2)
        self.assertEqual(page["head"], 3)
        self.assertEqual(page["tail"], 5)
        self.assertEqual(page["samples"][0]["frame"]["wide_encoder"], str(2**60))
        self.assertEqual(b.waveform(after=page["cursor"])["samples"][0]["cursor"], 5)
        self.assertTrue(b.waveform(after=99)["overrun"])
        self.assertEqual(b.waveform(after=99)["samples"][0]["cursor"], 3)

    def test_replay_rejects_stop(self):
        b = Bridge(5, replay=True)
        with self.assertRaises(ValueError):
            b.command({"request_id": "r", "action": "stop"})
        self.assertFalse(b.status()["connected"])

    def test_variant_restrictions_and_dedup(self):
        class Owner:
            def __init__(self):
                self.calls = []

            def command(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        b = Bridge(5)
        b.owner = Owner()
        with self.assertRaises(ValueError):
            b.command({"request_id": "x", "action": "start-swing"})
        request = {"request_id": "x", "action": "stop"}
        self.assertEqual(b.command(request), b.command(request))
        self.assertEqual(len(b.owner.calls), 1)
        self.assertEqual(b.requests["x"]["state"], "queued")
        with self.assertRaises(ValueError):
            b.command({"request_id": "x", "action": "estop"})

    def test_all_original_identities(self):
        p = load_upstream()["protocol"]
        for v in range(1, 6):
            s = Bridge(v).status()
            self.assertEqual(s["user_code"], f"{p.FIRMWARE[v]:08X}")
            self.assertEqual(s["wire_version"], p.WIRE_VERSIONS[v])


def native_frame(kind="telemetry", variant=5, sequence=1, board_ms=5):
    import struct

    p = load_upstream()["protocol"]
    raw = bytearray(256)
    raw[:4] = b"FD2T" if kind == "telemetry" else b"FD2D"
    raw[4] = p.WIRE_VERSIONS[variant]
    raw[5] = variant
    raw[6] = 1
    struct.pack_into("<II", raw, 8, sequence, board_ms)
    if kind == "telemetry":
        struct.pack_into("<I", raw, 16, sequence)
        struct.pack_into("<II", raw, 32, p.FIRMWARE[variant], p.SCHEMAS[variant])
        struct.pack_into("<q", raw, 72, 2**60)
    else:
        struct.pack_into("<II", raw, 16, p.SCHEMAS[variant], p.FIRMWARE[variant])
    struct.pack_into("<H", raw, 254, p.crc16(raw[:-2]))
    return bytes(raw)


class NativeReplayTests(unittest.TestCase):
    def test_native_five_codecs_and_capture(self):
        from host.fd2_bridge import install

        modules = load_upstream()
        cls = modules["client"].SerialOwner
        saved = (cls.__init__, cls._receive, cls.trace)
        try:
            b = Bridge(5)
            install(b)
            owner = cls(5, "", "unused")
            for n in range(1, 201):
                raw = native_frame(sequence=n, board_ms=n * 5)
                owner._receive(raw, modules["protocol"].decode(raw))
            raw = native_frame("diagnostic", board_ms=1002)
            owner._receive(raw, modules["protocol"].decode(raw))
            rows = b.waveform()["samples"]
            self.assertEqual(len(rows), 201)
            self.assertEqual(rows[-1]["frame"]["kind"], "diagnostic")
            self.assertNotIn("history", b.status()["host"])
            self.assertEqual(rows[0]["frame"]["wide_encoder"], str(2**60))
            old = b.session
            raw = native_frame(sequence=201, board_ms=1)
            owner._receive(raw, modules["protocol"].decode(raw))
            self.assertNotEqual(old, b.session)
        finally:
            cls.__init__, cls._receive, cls.trace = saved
        for v in range(1, 6):
            self.assertEqual(
                modules["protocol"].decode(native_frame(variant=v))["variant"], v
            )

    def test_native_rx_only_archive_and_crc(self):
        import tempfile, zipfile, json, zlib, time
        from unittest.mock import patch
        from host.fd2_replay import Replay

        modules = load_upstream()
        header = modules["trace_log"].CHUNK
        with tempfile.TemporaryDirectory() as directory:
            path = __import__("pathlib").Path(directory) / "session.zip"
            raw = native_frame()
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "session.json",
                    json.dumps({"format": "ForestFive.Physical.v1", "variant": 5}),
                )
                archive.writestr(
                    "physical_chunks.bin",
                    header.pack(100, 100, 0, 1, len(raw), zlib.crc32(raw))
                    + raw
                    + header.pack(101, 101, 0, 0, len(raw), zlib.crc32(raw))
                    + raw,
                )
            b = Bridge(5, replay=True)
            with patch.object(
                modules["client"],
                "open_serial",
                side_effect=AssertionError("no hardware"),
            ):
                replay = Replay(b, path)
                replay.run()
            self.assertEqual(len(b.waveform()["samples"]), 1)
            self.assertTrue(b.status()["replay"]["finished"])
            self.assertFalse(b.status()["connected"])
            self.assertFalse(b.status()["synced"])
            with self.assertRaises(ValueError):
                b.command({"request_id": "x", "action": "stop"})

    def test_corrupt_original_chunk_is_reported(self):
        import tempfile, zipfile, json
        from pathlib import Path
        from host.fd2_replay import Replay

        header = load_upstream()["trace_log"].CHUNK
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corrupt.zip"
            raw = native_frame()
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "session.json",
                    json.dumps({"format": "ForestFive.Physical.v1", "variant": 5}),
                )
                archive.writestr(
                    "physical_chunks.bin", header.pack(1, 1, 0, 0, len(raw), 0) + raw
                )
            b = Bridge(5, replay=True)
            Replay(b, path).run()
            self.assertIn("CRC", b.status()["host"]["error"])
            self.assertFalse(b.waveform()["samples"])

    def test_v3_replay_gain_averages_remain_visible_after_finish(self):
        import tempfile, zipfile, json, zlib, struct, time
        from pathlib import Path
        from unittest.mock import patch
        from host.fd2_replay import Replay

        modules = load_upstream()
        p = modules["protocol"]
        header = modules["trace_log"].CHUNK
        chunks = []
        for n in range(1, 202):
            raw = bytearray(native_frame(variant=3, sequence=n, board_ms=n * 5))
            raw[6] = 1 | 2 | 4 | 32 | 64
            raw[7] = 3
            struct.pack_into("<6f", raw, 128, 10, 0, 20, 0, 0, 0)
            struct.pack_into("<6f", raw, 152, 12, 0, 24, 0, 0, 0)
            struct.pack_into("<H", raw, 254, p.crc16(raw[:-2]))
            chunks.append(header.pack(n, n, 0, 0, len(raw), zlib.crc32(raw)) + raw)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v3.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "session.json",
                    json.dumps({"format": "ForestFive.Physical.v1", "variant": 3}),
                )
                archive.writestr("physical_chunks.bin", b"".join(chunks))
            b = Bridge(3, replay=True)
            Replay(b, path).run()
            with patch(
                "host.fd2_bridge.time.monotonic", return_value=time.monotonic() + 10
            ):
                result = b.status()
            self.assertFalse(result["connected"])
            self.assertFalse(result["synced"])
            average = result["host"]["actual_gain_averages"]
            self.assertTrue(average["valid"])
            self.assertAlmostEqual(average["windows"][0]["kp"], 12)
            self.assertEqual(average["windows"][0]["covered_ms"], 1000)

    def test_native_send_ack_mapping_once(self):
        import struct
        from host.fd2_bridge import install

        modules = load_upstream()
        cls = modules["client"].SerialOwner
        p = modules["protocol"]
        saved = (cls.__init__, cls._receive, cls.trace)

        class Port:
            def __init__(self):
                self.writes = []

            def write(self, raw):
                self.writes.append(raw)
                return len(raw)

        try:
            b = Bridge(5)
            install(b)
            owner = cls(5, "", "unused")
            owner.state.connected = True
            owner.state.authenticated = True
            owner._serial = Port()
            request = {"request_id": "physical", "action": "stop"}
            b.command(request)
            b.command(request)
            entry = owner.queue.get_nowait()
            owner._sending_source = owner.queued_sources.pop(entry[1])
            wire = owner._send(entry[2], entry[3], entry[4])
            self.assertEqual(b.requests["physical"]["state"], "pending")
            self.assertEqual(len(owner._serial.writes), 1)
            self.assertTrue(owner.queue.empty())
            raw = bytearray(80)
            raw[:4] = b"FD2A"
            raw[4] = 5
            raw[5] = p.STOP
            raw[7] = 5
            struct.pack_into("<I", raw, 8, wire)
            struct.pack_into("<I", raw, 72, p.FIRMWARE[5])
            struct.pack_into("<H", raw, 78, p.crc16(raw[:-2]))
            raw[5] = p.ZERO
            struct.pack_into("<H", raw, 78, p.crc16(raw[:-2]))
            owner._receive(bytes(raw), p.decode(raw))
            self.assertEqual(b.requests["physical"]["state"], "pending")
            raw[5] = p.STOP
            struct.pack_into("<H", raw, 78, p.crc16(raw[:-2]))
            owner._receive(bytes(raw), p.decode(raw))
            self.assertEqual(b.requests["physical"]["state"], "accepted")
        finally:
            cls.__init__, cls._receive, cls.trace = saved

    def test_status_ack_readback_does_not_invert_host_locks(self):
        import threading, struct, time
        from host.fd2_bridge import install

        modules = load_upstream()
        cls = modules["client"].SerialOwner
        p = modules["protocol"]
        saved = (cls.__init__, cls._receive, cls.trace)
        try:
            b = Bridge(5)
            install(b)
            owner = cls(5, "", "unused")
            owner.state.connected = True
            owner.state.authenticated = True
            owner.pending[1] = (p.STATUS, 0, b"", time.monotonic())
            attempted = threading.Event()
            held = threading.Event()
            completed = threading.Event()
            original = owner.command

            def command(*args, **kwargs):
                attempted.set()
                return original(*args, **kwargs)

            owner.command = command

            def simultaneous_queue_trace():
                with owner.command_lock:
                    held.set()
                    if attempted.wait(2):
                        owner.trace("probe")
                        completed.set()

            worker = threading.Thread(target=simultaneous_queue_trace, daemon=True)
            worker.start()
            self.assertTrue(held.wait(2))
            raw = bytearray(80)
            raw[:4] = b"FD2A"
            raw[4] = 5
            raw[5] = p.STATUS
            raw[7] = 5
            struct.pack_into("<I", raw, 8, 1)
            struct.pack_into("<I", raw, 72, p.FIRMWARE[5])
            struct.pack_into("<H", raw, 78, p.crc16(raw[:-2]))
            receiver = threading.Thread(
                target=lambda: owner._receive(bytes(raw), p.decode(raw)), daemon=True
            )
            receiver.start()
            self.assertTrue(
                completed.wait(2),
                "state.lock held while original STATUS readback waits for command_lock",
            )
            worker.join(2)
            receiver.join(2)
            self.assertFalse(receiver.is_alive())
        finally:
            cls.__init__, cls._receive, cls.trace = saved

    def test_session_switch_before_dispatch_fails_closed(self):
        class Owner:
            def __init__(self):
                self.calls = []

            def command(self, *a, **kw):
                self.calls.append(a)

        b = Bridge(5)
        old = Owner()
        b.owner = old
        original = b._dispatch_request

        def change_then_dispatch(*args):
            b.new_session()
            return original(*args)

        b._dispatch_request = change_then_dispatch
        result = b.command({"request_id": "racing", "action": "stop"})
        self.assertEqual(result["state"], "indeterminate")
        self.assertFalse(old.calls)

    def test_original_manifest(self):
        import hashlib, json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "host/fd2"
        manifest = json.loads(
            (root / "MANIFEST_SHA256.json").read_text(encoding="utf8")
        )
        for name, entry in manifest.items():
            raw = (root / "upstream" / name).read_bytes()
            self.assertEqual(len(raw), entry["size"], name)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["sha256"], name)


class HTTPTests(unittest.TestCase):
    def test_local_only_and_input_validation(self):
        import http.client, json

        b = Bridge(5)
        server = b.serve(0)

        def query(method, path, body=None, headers=None):
            c = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            result = (r.status, json.loads(r.read()))
            c.close()
            return result

        try:
            self.assertEqual(query("GET", "/status")[0], 403)
            local = {"X-J280-Local": "1"}
            self.assertEqual(query("GET", "/status", headers=local)[0], 200)
            self.assertEqual(
                query(
                    "GET", "/status", headers=dict(local, Origin="http://evil.example")
                )[0],
                403,
            )
            self.assertEqual(query("GET", "/waveform?after=-1", headers=local)[0], 400)
            self.assertEqual(query("POST", "/command", "[]", local)[0], 400)
            self.assertEqual(
                query("POST", "/command", "", dict(local, **{"Content-Length": "-1"}))[
                    0
                ],
                400,
            )
        finally:
            server.shutdown()
            server.server_close()

    def test_real_ack_and_old_session_result(self):
        class Owner:
            def command(self, *a, **kw):
                pass

        b = Bridge(5)
        b.owner = Owner()
        b.command({"action": "stop", "request_id": "x"})
        b.trace("write_requested", dict(source="WEB/x", request_id=7))
        self.assertEqual(b.requests["x"]["state"], "pending")
        b.trace("ack", dict(request_id=7, status=0, matched=True))
        self.assertEqual(b.requests["x"]["state"], "accepted")
        self.assertNotIn("signature", b.status()["requests"]["x"])
        b.command({"action": "stop", "request_id": "y"})
        b.new_session()
        self.assertEqual(b.requests["y"]["state"], "indeterminate")
        self.assertFalse(b.wire_requests)


if __name__ == "__main__":
    unittest.main()
