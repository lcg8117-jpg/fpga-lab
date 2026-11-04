import struct
import queue
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from host.v4_usb_ble_bridge import V4UsbBleBridge, install, load_upstream
from host.v4_r28_bridge import V4R28Bridge


class MemoryConnection:
    def __init__(self):
        self.writes = []
        self.metadata = {}

    def write(self, raw):
        self.writes.append(bytes(raw))
        return len(raw)


class V4DualBridgeTests(unittest.TestCase):
    def setUp(self):
        self.modules = load_upstream()
        self.p = self.modules['protocol']
        self.bridge = V4UsbBleBridge()
        install(self.bridge, self.modules)
        self.owner = self.modules['dual_client'].DualOwner('AA:BB:CC:DD:EE:FF', '.', transport='usb', port='COM5')
        for route in ('usb', 'ble'):
            self.owner.links[route] = self.owner._new_link(route, MemoryConnection())
        self.owner._publish_routes()

    def ack(self, rid, opcode, *, firmware=0x5634523A, status=0):
        b = bytearray(80)
        b[:8] = b'FD2A' + bytes((4, opcode, status, 4))
        struct.pack_into('<I', b, 8, rid)
        struct.pack_into('<6f', b, 24, *self.p.DEFAULT_GAINS)
        b[58] = 1
        struct.pack_into('<I', b, 72, firmware)
        return self.p.seal(b)

    def diagnostic(self, *, firmware=0x5634523A, session=None):
        b = bytearray(256)
        b[:8] = b'FD2D' + bytes((4, 4, 5, 1))
        struct.pack_into('<IIII', b, 8, 1, 1000, self.p.SCHEMA, firmware)
        struct.pack_into('<I', b, 164, self.owner.session if session is None else session)
        return self.p.seal(b)

    def compact(self, sequence=1):
        b = bytearray(48)
        b[:8] = b'FD2B' + bytes((4, 4, 0x91, 3))
        struct.pack_into('<III', b, 8, sequence, 1000 + sequence * 5, 1000 + sequence * 5)
        return self.p.seal(b)

    def receive(self, route, raw):
        self.owner._receive_route(route, raw, self.p.decode(raw))

    def authenticate(self, firmware=0x5634523A):
        self.owner.pending[1] = (self.p.HELLO, 0, b'', time.monotonic())
        self.owner.pending_routes[1] = {'usb'}
        self.receive('usb', self.ack(1, self.p.HELLO, firmware=firmware))
        self.owner.next_id = 2

    def test_requires_matched_3a_ack_and_current_session_diagnostic(self):
        self.authenticate()
        self.receive('usb', self.compact())
        self.assertFalse(self.bridge.status()['synced'])
        self.assertEqual(self.bridge.waveform()['samples'], [])
        self.receive('usb', self.diagnostic(session=self.owner.session + 1))
        self.assertFalse(self.bridge.status()['synced'])
        self.receive('usb', self.diagnostic())
        self.receive('usb', self.compact(2))
        self.assertTrue(self.bridge.status()['synced'])
        self.assertEqual([row['frame']['kind'] for row in self.bridge.waveform()['samples']], ['diagnostic', 'compact'])

    def test_primary_diagnostic_still_verifies_when_ble_duplicate_arrives_first(self):
        self.authenticate()
        self.owner.links['ble']['ready'] = True
        self.receive('ble', self.diagnostic())
        self.assertFalse(self.bridge.status()['synced'])
        self.receive('usb', self.diagnostic())
        self.assertTrue(self.bridge.status()['synced'])

    def test_old_39_identity_never_authenticates_the_new_web_profile(self):
        self.authenticate(firmware=0x56345239)
        self.receive('usb', self.diagnostic(firmware=0x56345239))
        self.assertFalse(self.bridge.status()['synced'])
        self.assertEqual(self.bridge.waveform()['samples'], [])
        with self.assertRaises(ValueError):
            self.bridge.command({'request_id': 'run', 'action': 'balance'})
        self.assertEqual(self.bridge.command({'request_id': 'stop', 'action': 'stop'})['state'], 'queued')

    def test_non_stop_request_requires_the_current_verified_session(self):
        self.authenticate()
        self.receive('usb', self.diagnostic())
        with self.assertRaises(ValueError):
            self.bridge.command({'request_id': 'old', 'action': 'clear-fault', 'session': 'previous'})
        result = self.bridge.command({'request_id': 'current', 'action': 'clear-fault', 'session': self.bridge.session})
        self.assertEqual(result['state'], 'queued')

    def test_owner_cannot_switch_between_session_check_and_enqueue(self):
        self.authenticate()
        self.receive('usb', self.diagnostic())
        old = self.owner
        cls = self.modules['dual_client'].DualOwner
        other = object.__new__(cls)
        cls._bridge_originals[0](other, 'AA:BB:CC:DD:EE:FE', '.', transport='usb', port='COM6')
        other.links['usb'] = other._new_link('usb', MemoryConnection())
        other.links['usb']['ready'] = True
        other._publish_routes()
        other.state.authenticated = True
        entered, proceed = threading.Event(), threading.Event()
        original = V4R28Bridge.command
        result = []
        def hold(bridge, body):
            entered.set()
            self.assertTrue(proceed.wait(2))
            return original(bridge, body)
        def request():
            try:
                result.append(self.bridge.command({'request_id':'race', 'action':'clear-fault', 'session':str(old.session)}))
            except Exception as exc:
                result.append(exc)
        with patch.object(V4R28Bridge, 'command', hold):
            sender = threading.Thread(target=request)
            sender.start()
            self.assertTrue(entered.wait(2))
            switch = threading.Thread(target=self.bridge.attach_owner, args=(other,))
            switch.start()
            time.sleep(.05)
            proceed.set()
            sender.join(2)
            switch.join(2)
        self.assertFalse(sender.is_alive() or switch.is_alive())
        self.assertEqual(old.queue.qsize(), 1)
        self.assertEqual(other.queue.qsize(), 0)
        self.assertIsInstance(result[0], dict)

    def test_real_routes_and_matching_ack_are_preserved(self):
        self.authenticate()
        self.receive('usb', self.diagnostic())
        self.owner.links['ble']['ready'] = True
        self.owner.links['ble']['last_rx'] = time.monotonic()
        self.owner._publish_routes()
        status = self.bridge.status()
        self.assertTrue(status['transport']['usb_connected'])
        self.assertTrue(status['transport']['ble_connected'])
        self.assertTrue(status['transport']['usb_ready'])
        self.assertTrue(status['transport']['ble_ready'])
        self.assertEqual(status['transport']['position_route'], 'ble')
        self.assertEqual(status['host']['position_route'], 'ble')
        self.bridge.command({'request_id': 'stop-1', 'action': 'stop'})
        self.owner.thread = threading.current_thread()
        _, _, opcode, group, payload, route = self.owner.queue.get_nowait()
        wire = self.owner._send(opcode, group, payload, route)
        self.assertEqual(self.bridge.requests['stop-1']['state'], 'pending')
        self.receive('ble', self.ack(wire, self.p.STATUS))
        self.assertEqual(self.bridge.requests['stop-1']['state'], 'pending')
        self.receive('usb', self.ack(wire, opcode))
        self.assertEqual(self.bridge.requests['stop-1']['state'], 'accepted')
        self.assertEqual(len(self.owner.links['usb']['connection'].writes), 1)
        self.assertEqual(len(self.owner.links['ble']['connection'].writes), 1)

    def test_route_ready_does_not_claim_stale_primary_has_replies(self):
        self.authenticate()
        self.receive('usb', self.diagnostic())
        self.owner.links['usb']['last_rx'] = time.monotonic() - 1
        self.owner._publish_routes()
        status = self.bridge.status()
        self.assertTrue(status['transport']['usb_connected'])
        self.assertFalse(status['transport']['usb_ready'])

    def test_keyboard_position_is_visible_only_after_matching_successful_ack(self):
        self.authenticate(); self.receive('usb', self.diagnostic()); self.receive('usb', self.compact())
        self.owner.links['ble']['ready'] = True
        self.owner.links['ble']['last_rx'] = time.monotonic()
        self.owner._publish_routes()
        wire = self.owner._send(self.p.POSITION, 0, b'\x03', 'ble')
        self.assertIsNone(self.bridge.status()['host'].get('last_position_request'))
        self.receive('usb', self.ack(wire, self.p.POSITION))
        self.assertIsNone(self.bridge.status()['host'].get('last_position_request'))
        self.receive('ble', self.ack(wire, self.p.POSITION))
        result = self.bridge.status()['host']['last_position_request']
        self.assertEqual((result['value'], result['wire_request_id'], result['route']), (3, wire, 'ble'))

    def test_mismatched_diagnostic_on_secondary_route_locks_web(self):
        self.authenticate()
        self.receive('usb', self.diagnostic())
        self.owner.links['ble']['ready'] = True
        other = bytearray(self.diagnostic(firmware=0x56345239))
        struct.pack_into('<I', other, 12, 1005)
        self.receive('ble', self.p.seal(other))
        self.assertFalse(self.bridge.status()['synced'])
        self.assertNotIn(0x56345239, [row['frame'].get('firmware_id') for row in self.bridge.waveform()['samples']])

    def test_queued_web_motion_is_not_written_after_identity_failure(self):
        self.authenticate()
        self.receive('usb', self.diagnostic())
        self.receive('usb', self.compact())
        self.owner.links['ble']['ready'] = True
        self.owner._publish_routes()
        self.bridge.command({'request_id':'move', 'action':'position', 'value':2, 'session':self.bridge.session})
        other = bytearray(self.diagnostic(firmware=0x56345239))
        struct.pack_into('<I', other, 12, 1005)
        self.receive('ble', self.p.seal(other))
        self.owner.thread = threading.current_thread()
        _, _, opcode, group, payload, route = self.owner.queue.get_nowait()
        with self.assertRaises(RuntimeError):
            self.owner._send(opcode, group, payload, route)
        self.assertEqual(self.bridge.requests['move']['state'], 'rejected')
        self.assertEqual(self.owner.links['ble']['connection'].writes, [])

    def test_ble_request_ack_timeout_starts_only_after_physical_write(self):
        self.authenticate()
        self.receive('usb', self.diagnostic())
        self.receive('usb', self.compact())
        link = self.owner.links['ble']
        link['ready'] = True
        link['last_rx'] = time.monotonic()
        link['connection'].async_write = True
        self.owner._publish_routes()
        self.bridge.command({'request_id':'ble-move', 'action':'position', 'value':2, 'session':self.bridge.session})
        self.owner.thread = threading.current_thread()
        _, _, opcode, group, payload, route = self.owner.queue.get_nowait()
        wire = self.owner._send(opcode, group, payload, route)
        self.assertEqual(self.bridge.status()['requests']['ble-move']['state'], 'queued')
        self.assertNotIn((wire, 'ble'), self.owner.physical_sent_times)
        self.owner.physical_sent_times[(wire, 'ble')] = time.monotonic()
        self.assertEqual(self.bridge.status()['requests']['ble-move']['state'], 'pending')
        self.receive('ble', self.ack(wire, opcode))
        self.assertEqual(self.bridge.status()['requests']['ble-move']['state'], 'accepted')

    def test_lost_async_pending_is_reported_unknown(self):
        self.authenticate(); self.receive('usb', self.diagnostic()); self.receive('usb', self.compact())
        self.owner.links['ble']['ready'] = True
        self.owner.links['ble']['last_rx'] = time.monotonic()
        self.owner.links['ble']['connection'].async_write = True
        self.owner._publish_routes()
        self.bridge.command({'request_id':'lost', 'action':'position', 'value':1, 'session':self.bridge.session})
        self.owner.thread = threading.current_thread()
        _, _, opcode, group, payload, route = self.owner.queue.get_nowait()
        wire = self.owner._send(opcode, group, payload, route)
        self.owner.pending.pop(wire)
        self.assertEqual(self.bridge.status()['requests']['lost']['state'], 'indeterminate')

    def test_stop_does_not_claim_retired_motion_completed(self):
        self.authenticate()
        self.receive('usb', self.diagnostic())
        self.receive('usb', self.compact())
        self.bridge.command({'request_id': 'move', 'action': 'position', 'value': 2, 'session': self.bridge.session})
        self.owner.thread = threading.current_thread()
        _, _, opcode, group, payload, route = self.owner.queue.get_nowait()
        self.owner._send(opcode, group, payload, route)
        self.assertEqual(self.bridge.requests['move']['state'], 'pending')
        self.bridge.command({'request_id': 'stop', 'action': 'stop'})
        _, _, opcode, group, payload, route = self.owner.queue.get_nowait()
        self.owner._send(opcode, group, payload, route)
        self.assertEqual(self.bridge.requests['move']['state'], 'indeterminate')

    def test_secondary_failure_marks_old_request_unknown_and_new_request_uses_usb(self):
        self.authenticate(); self.receive('usb', self.diagnostic()); self.receive('usb', self.compact())
        self.owner.links['ble']['ready'] = True
        self.owner.links['ble']['last_rx'] = time.monotonic()
        self.owner._publish_routes()
        self.bridge.command({'request_id':'old-move','action':'position','value':1,'session':self.bridge.session})
        self.owner.thread = threading.current_thread()
        _,_,opcode,group,payload,route = self.owner.queue.get_nowait()
        wire = self.owner._send(opcode,group,payload,route)
        self.assertTrue(self.owner._quarantine_secondary_position(wire,'secondary_ack_timeout'))
        self.assertEqual(self.bridge.status()['requests']['old-move']['state'], 'indeterminate')
        self.bridge.command({'request_id':'new-move','action':'position','value':2,'session':self.bridge.session})
        self.assertEqual(self.owner.queue.get_nowait()[5], 'usb')

    def test_queued_motion_retains_request_id_when_owner_waits_for_an_ack(self):
        self.authenticate()
        self.receive('usb', self.diagnostic())
        self.receive('usb', self.compact())
        self.bridge.command({'request_id': 'move', 'action': 'position', 'value': 2, 'session': self.bridge.session})
        self.owner.thread = threading.current_thread()
        entry = self.owner.queue.get_nowait()
        self.owner.queue.put_nowait(entry)  # The real worker requeues while another mutation is pending.
        self.assertEqual(self.bridge.requests['move']['state'], 'queued')
        _, _, opcode, group, payload, route = self.owner.queue.get_nowait()
        self.owner._send(opcode, group, payload, route)
        self.assertEqual(self.bridge.requests['move']['state'], 'pending')

    def test_owner_skipped_action_is_settled_even_if_no_further_heartbeat_is_sent(self):
        self.authenticate()
        self.receive('usb', self.diagnostic())
        self.receive('usb', self.compact())
        self.bridge.command({'request_id':'skipped', 'action':'position', 'value':2, 'session':self.bridge.session})
        self.owner.thread = threading.current_thread()
        self.owner.queue.get_nowait()  # Worker skips the dequeued action after its eligibility changes.
        with self.assertRaises(queue.Empty): self.owner.queue.get_nowait()
        self.assertEqual(self.bridge.requests['skipped']['state'], 'rejected')

    def test_recorded_rtl_usb_stream_reaches_web_as_real_compact_and_diagnostic(self):
        raw = (Path(__file__).parent / 'fixtures' / 'v4_usb_ble' / 'usb_wire_frames.bin').read_bytes()
        parser = self.p.FrameParser()
        self.owner.session = 431
        self.bridge.session = '431'
        self.owner.pending[1] = (self.p.HELLO, 0, b'', time.monotonic())
        self.owner.pending_routes[1] = {'usb'}
        for frame, item in parser.feed(raw):
            if item['kind'] == 'ack' and item['request_id'] != 1:
                continue
            self.owner._receive_route('usb', frame, item)
        self.assertEqual((parser.crc_errors, parser.format_errors), (0, 0))
        self.assertTrue(self.bridge.status()['synced'])
        rows = self.bridge.waveform()['samples']
        self.assertTrue(any(row['frame']['kind'] == 'diagnostic' for row in rows))
        self.assertTrue(any(row['frame']['kind'] == 'compact' for row in rows))
        self.assertTrue(all(row['session'] == '431' for row in rows))


if __name__ == '__main__':
    unittest.main()
