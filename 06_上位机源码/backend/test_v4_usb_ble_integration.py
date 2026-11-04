"""Real dual owner and HTTP bridge with in-memory links; no hardware drivers."""
from contextlib import nullcontext
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest

from host.v4_usb_ble_bridge import V4UsbBleBridge, install, load_upstream
from host_bridge import HostClient, TelemetryAdapter, validate_action


class FakeLogger:
    def __init__(self, *args):
        self.error = ''
        self.written = 0
        self.archive_path = None
        self.partial_path = None
    def start(self): return self
    def submit(self, raw, item):
        self.written += 1
        return True
    def close(self): pass


class FakeBoard:
    def __init__(self, protocol):
        self.p = protocol
        self.lock = threading.Lock()
        self.writes = []
        self.session = None
        self.sequence = 0
        self.last_sample = 0

    def factory(self, name):
        board = self
        class Connection:
            metadata = {}
            async_write = False
            records_physical_io = True
            def __init__(self, address, **kwargs):
                self.buffer = bytearray()
                self.lock = threading.Lock()
            def write(self, raw):
                opcode, rid = raw[5], struct.unpack_from('<I', raw, 8)[0]
                session = struct.unpack_from('<I', raw, 36)[0]
                with board.lock:
                    if opcode == board.p.HELLO: board.session = session
                    board.writes.append((name, opcode, rid, session))
                frame = bytearray(80)
                frame[:8] = b'FD2A' + bytes((4, opcode, 0, 4))
                struct.pack_into('<I', frame, 8, rid)
                struct.pack_into('<6f', frame, 24, *board.p.DEFAULT_GAINS)
                frame[58] = 3
                struct.pack_into('<I', frame, 72, 0x5634523A)
                with self.lock: self.buffer.extend(board.p.seal(frame))
                return len(raw)
            def read(self, size):
                with self.lock:
                    pending = bytes(self.buffer[:size])
                    del self.buffer[:size]
                if name == 'usb' and board.session and time.monotonic()-board.last_sample > .025:
                    board.last_sample = time.monotonic()
                    board.sequence += 1
                    ms = board.sequence * 5
                    compact = bytearray(48)
                    compact[:8] = b'FD2B' + bytes((4, 4, 0x91, 3))
                    struct.pack_into('<III', compact, 8, board.sequence, ms, ms)
                    diagnostic = bytearray(256)
                    diagnostic[:8] = b'FD2D' + bytes((4, 4, 5, 1))
                    struct.pack_into('<IIII', diagnostic, 8, board.sequence, ms, board.p.SCHEMA, 0x5634523A)
                    struct.pack_into('<I', diagnostic, 164, board.session)
                    return pending + board.p.seal(diagnostic) + board.p.seal(compact)
                return pending
            def close(self): pass
        return Connection


def wait_for(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate(): return True
        time.sleep(.01)
    return False


class V4DualIntegrationTests(unittest.TestCase):
    def test_http_bridge_uses_one_owner_for_usb_telemetry_and_ble_position(self):
        modules = load_upstream()
        p = modules['protocol']
        bridge = V4UsbBleBridge()
        install(bridge, modules)
        server = bridge.serve(0)
        board = FakeBoard(p)
        with tempfile.TemporaryDirectory() as directory:
            owner = modules['dual_client'].DualOwner('AA:BB:CC:DD:EE:FF', directory, transport='usb', port='COM5',
                usb_factory=board.factory('usb'), ble_factory=board.factory('ble'), logger_factory=FakeLogger,
                lease_factory=lambda: nullcontext()).start()
            try:
                client = HostClient(server.server_port)
                self.assertTrue(wait_for(lambda: bridge.status()['synced'] and bool(bridge.waveform()['samples'])))
                owner.attach_ble()
                self.assertTrue(wait_for(lambda: bridge.status()['transport']['ble_ready']))
                status = client.status()
                data, rows, _ = TelemetryAdapter().observe(status, time.time(), waveform=client.waveform())
                self.assertEqual(data['firmware'], 'V4_USB_BLE')
                self.assertTrue(rows)
                body = {'request_id': 'offline-position', 'action': 'position', 'value': 2, 'session':status['session']}
                validate_action(body, status)
                client.command(body)
                self.assertTrue(wait_for(lambda: bridge.requests['offline-position']['state'] == 'accepted'))
                positions = [item for item in board.writes if item[1] == p.POSITION]
                self.assertEqual(len(positions), 1)
                self.assertEqual(positions[0][0], 'ble')
                self.assertEqual(len({item[3] for item in board.writes}), 1)
                self.assertEqual(sum(item[1] == p.HELLO for item in board.writes), 1)
            finally:
                owner.stop(wait=True)
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
