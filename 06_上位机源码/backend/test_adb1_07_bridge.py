"""Offline boundaries for the 07 release; never construct a real USB transport."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from host_bridge import BridgeError, TelemetryAdapter, identify, validate_action
from test_fd2_telemetry import batch, frame

TEST_TEMP = Path(__file__).resolve().parents[1]/'.test-temp'
TEST_TEMP.mkdir(exist_ok=True)

def snapshot():
    return dict(protocol='FD2', variant=16, user_code='41444231', schema='41444203',
                wire_version=3, delivery_profile='ADB1_07', session='fixture',
                connected=True, synced=True, status_age_s=.01, control_age_s=.01,
                host={}, replay={'active': False})


class ADB107Tests(unittest.TestCase):
    def test_07_profile_is_explicit_and_does_not_relabel_old_adb1(self):
        item = snapshot()
        self.assertEqual(identify(item), 'ADB1_07')
        item.pop('delivery_profile')
        self.assertEqual(identify(item), 'ADB1')
        for changes in ({'schema': '41445001'}, {'wire_version': 4}, {'variant': True}):
            with self.subTest(changes=changes), self.assertRaises(BridgeError):
                identify(dict(snapshot(), **changes))

    def test_three_07_transactions_keep_epoch_stride_one(self):
        item = snapshot()
        frames = []
        for i in range(3):
            value = frame(variant=1, seq=i+1, board=1000+5*i)
            value.update(variant=16, firmware_id=0x41444231, schema=0x41444203,
                         wire_version=3, control_epoch=1+i)
            frames.append(value)
        item['host']['latest'] = frames[-1]
        data, rows, _ = TelemetryAdapter().observe(item, 1000.02, waveform=batch(frames))
        self.assertEqual(data['firmware'], 'ADB1_07')
        self.assertEqual([r['gap_before'] for r in rows], [True, False, False])
        self.assertTrue(data['capabilities']['adaptive'])
        self.assertIn('pid_gains', data)
        self.assertIsNone(data['pid_gains']['independent'])
        for action in ('adapt-enable', 'adapt-disable', 'read-gains', 'start-swing'):
            self.assertEqual(validate_action({'action': action, 'request_id': action}, item)['action'], action)
        with self.assertRaises(ValueError):
            TelemetryAdapter().observe(item, 1000.02, waveform=batch([dict(frames[0], schema=0x41445001)]))

    def test_extension_is_separate_from_control_and_readback(self):
        from host.adb1_07_bridge import ADB107Bridge, load_upstream
        p = load_upstream()['protocol']
        from host.adb1_07.host.fake_transport import FakeTransport
        transport = FakeTransport()
        transport._step()
        t = p.decode(bytes(transport.buffer[:256]))
        e = p.decode(transport._extension())
        transport.close()
        bridge = ADB107Bridge()
        bridge.capture(t, received_at=1000)
        bridge.capture(e, received_at=1000.005)
        item = snapshot()
        item['host']['latest'] = t
        data, rows, _ = TelemetryAdapter().observe(item, 1000.01, waveform=bridge.waveform())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['channels'], [])
        self.assertEqual(rows[0]['raw']['kind'], 'telemetry')
        self.assertEqual(data['adb1_extension']['kind'], 'extension')
        self.assertNotIn('angle_P_ms', data['adb1_extension'])
        self.assertIsNone(data['pid_gains']['independent'])

    def test_bridge_restart_wrap_and_original_parser_isolation(self):
        from host.adb1_07_bridge import ADB107Bridge, load_upstream
        from host.adb1_fastp_bridge import load_upstream as load_fastp
        p = load_upstream()['protocol']
        self.assertEqual(p.SCHEMA, 0x41444203)
        self.assertEqual(load_fastp()['protocol'].SCHEMA, 0x41445001)
        bridge = ADB107Bridge()
        f = dict(kind='telemetry', coherent=True, board_ms=100, sequence=1, control_epoch=0xffffffff)
        bridge.capture(f)
        session = bridge.session
        bridge.capture(dict(f, board_ms=105, sequence=2, control_epoch=0))
        bridge.capture(dict(f, board_ms=110, sequence=3, control_epoch=1))
        self.assertEqual(session, bridge.session)
        self.assertFalse(bridge.waveform()['samples'][-1]['gap_before'])
        bridge.capture(dict(f, board_ms=115, sequence=4, control_epoch=2))
        bridge.capture(dict(f, board_ms=120, sequence=5, control_epoch=1))
        self.assertNotEqual(session, bridge.session)

    def test_starting_window_has_only_drafts_and_no_transport(self):
        from launch_host import configure_tk_runtime
        configure_tk_runtime()
        import tkinter as tk
        from host.adb1_07_bridge import ADB107Bridge, load_upstream
        from host.adb1_07_window import create_window
        modules = load_upstream()
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory, patch.object(modules['client'].SerialOwner, 'start', side_effect=AssertionError('startup must not create owner')):
                window = create_window(root, Path(directory), ADB107Bridge(), modules)
                self.assertIsNone(window.owner)
                self.assertEqual([float(v.get()) for v in window.gain_vars], [53.78, 0, 210.14, 17.67, 0, 616.61])
                self.assertEqual([v.get() for v in window.gain_read], ['尚未独立读回']*6)
                self.assertIn('07', root.title())
        finally:
            root.destroy()

    def test_fake_commands_ack_readback_and_no_replay(self):
        from host.adb1_07_bridge import ADB107Bridge, load_upstream, install
        from host.adb1_07.host.fake_transport import FakeTransport
        modules = load_upstream()
        bridge = ADB107Bridge()
        install(bridge, modules)
        def wait_for(condition):
            end = time.monotonic()+3
            while not condition() and time.monotonic() < end:
                time.sleep(.01)
            self.assertTrue(condition())
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            owner = modules['client'].SerialOwner(FakeTransport, directory).start()
            bridge.attach_owner(owner, simulated=False)  # Explicit fake, never a COM port.
            try:
                wait_for(lambda: owner.snapshot()['authenticated'])
                for action in ('adapt-enable', 'adapt-disable'):
                    body = {'request_id': action, 'action': action}
                    self.assertEqual(bridge.command(body)['state'], 'queued')
                    bridge.command(body)
                    wait_for(lambda: bridge.status()['requests'][action]['state'] == 'accepted')
                    wait_for(lambda: owner.snapshot()['latest']['adaptive_enabled_at_launch'] is (action == 'adapt-enable'))
                bridge.command({'request_id': 'g', 'action': 'set-gains', 'value': [53.78, 0, 210.14, 17.67, 0, 616.61]})
                wait_for(lambda: owner.snapshot()['independent_gains'] is not None)
                self.assertEqual(bridge.status()['requests']['g']['state'], 'accepted')
                self.assertLess(owner.snapshot()['control_gaps'], 2)
            finally:
                owner.close()

    def test_ack_wrong_opcode_timeout_and_stop_cancellation(self):
        from host.adb1_07_bridge import ADB107Bridge, load_upstream, install
        from host.adb1_07.host.fake_transport import FakeTransport
        p = load_upstream()['protocol']
        owner = load_upstream()['client'].SerialOwner(FakeTransport, '')
        owner.state.update(connected=True, authenticated=True)
        owner.last_frame = time.monotonic()
        bridge = ADB107Bridge()
        install(bridge)
        bridge.attach_owner(owner, simulated=False)
        bridge.command({'request_id': 'run', 'action': 'balance'})
        entry = owner.commands.get_nowait()
        bridge.on_send(owner, p.RUN, 42)
        ack = dict(kind='ack', wire_version=3, variant=16, firmware_id=p.FIRMWARE,
                   request_id=42, opcode=p.ZERO, status=0)
        bridge.on_ack(owner, ack, True)
        self.assertEqual(bridge.requests['run']['state'], 'pending')
        bridge.on_event(owner, 'ack_timeout_no_replay', {'request_id': 42})
        self.assertEqual(bridge.requests['run']['state'], 'indeterminate')
        self.assertTrue(owner.commands.empty())
        bridge.command({'request_id': 'next', 'action': 'balance'})
        bridge.command({'request_id': 'stop', 'action': 'stop'})
        self.assertEqual(bridge.requests['next']['state'], 'rejected')
        self.assertEqual(owner.commands.get_nowait()[2][0], p.STOP)

    def test_headless_entry_is_disconnected_and_read_only_offline(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(root/'launch_host.py'),
                                 'ADB1_07', '--headless', '--once', '--port', '0'],
                                cwd=root, capture_output=True, text=True, encoding='utf8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state['delivery_profile'], 'ADB1_07')
        self.assertFalse(state['connected'])
        self.assertFalse(state['synced'])
        from host.adb1_07_bridge import ADB107Bridge
        with self.assertRaises(ValueError):
            ADB107Bridge().command({'request_id': 'offline', 'action': 'adapt-enable'})

    def test_original_dependencies_and_firmware_match_manifests(self):
        root = Path(__file__).resolve().parents[1]
        for base in (root/'host/adb1_07', root/'firmware/releases/20261104'):
            manifest = json.loads((base/'MANIFEST_SHA256.json').read_text(encoding='utf8'))
            for name, digest in manifest['files'].items():
                self.assertEqual(hashlib.sha256((base/name).read_bytes()).hexdigest(), digest, name)

    def test_explicit_usb_lease_is_lazy_and_released_after_open_failure(self):
        from types import SimpleNamespace
        from host.adb1_07_window import LockedUSB
        from unittest.mock import MagicMock, Mock
        lease = MagicMock()
        serial = Mock()
        serial.open.side_effect = OSError('offline open failure')
        with patch('host.adb1_07_window.SharedMutex', return_value=lease) as lease_factory, patch.dict(sys.modules, {'serial': SimpleNamespace(Serial=Mock(return_value=serial))}):
            with self.assertRaises(ValueError):
                LockedUSB('socket://invalid')
            lease_factory.assert_not_called()
            lease.__enter__.assert_not_called()
        with patch('host.adb1_07_window.SharedMutex', return_value=lease), patch.dict(sys.modules, {'serial': SimpleNamespace(Serial=Mock(return_value=serial))}):
            with self.assertRaises(OSError):
                LockedUSB('COM123')
            lease.__enter__.assert_called_once()
            serial.close.assert_called_once()
            lease.__exit__.assert_called_once()
            self.assertFalse(serial.dtr)
            self.assertFalse(serial.rts)
