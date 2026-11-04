import unittest
from host.adb1_fastp_bridge import ADB1FastPBridge, load_upstream

class FastPBridgeTests(unittest.TestCase):
    def test_identity_and_adaptive_rejected_offline(self):
        bridge = ADB1FastPBridge()
        self.assertEqual((bridge.status()['schema'], bridge.status()['variant']), ('41445001', 16))
        with self.assertRaises(ValueError):
            bridge.command({'request_id':'r1', 'action':'adapt-enable'})

    def test_epoch_stride_and_restart(self):
        bridge = ADB1FastPBridge()
        base = dict(kind='telemetry', coherent=True, board_ms=100, sequence=1, control_epoch=1)
        bridge.capture(base)
        bridge.capture(dict(base, board_ms=105, sequence=2, control_epoch=6))
        self.assertFalse(bridge.waveform()['samples'][-1]['gap_before'])
        bridge.capture(dict(base, board_ms=110, sequence=3, control_epoch=16))
        self.assertTrue(bridge.waveform()['samples'][-1]['gap_before'])

    def test_upstream_schema(self):
        self.assertEqual(load_upstream()['protocol'].SCHEMA, 0x41445001)
    def test_restart_and_wrap(self):
        bridge = ADB1FastPBridge()
        f = dict(kind='telemetry', coherent=True, board_ms=100, sequence=1, control_epoch=0xfffffffc)
        bridge.capture(f)
        original = bridge.session
        bridge.capture(dict(f, board_ms=105, sequence=2, control_epoch=1))
        self.assertEqual(bridge.session, original)
        self.assertFalse(bridge.waveform()['samples'][-1]['gap_before'])
        bridge.capture(dict(f, board_ms=110, sequence=3, control_epoch=6))
        bridge.capture(dict(f, board_ms=115, sequence=4, control_epoch=1))
        self.assertNotEqual(bridge.session, original)

    def test_gain_command_is_validated_and_rejected_offline(self):
        bridge = ADB1FastPBridge()
        with self.assertRaises(ValueError):
            bridge.command({'request_id':'g1', 'action':'set-gains', 'value':[53.78,0,210.14,17.67,0,616.61]})
    def test_recorded_wire_frames_are_original_identity(self):
        from pathlib import Path
        protocol = load_upstream()['protocol']
        directory = Path(__file__).parent / 'fixtures' / 'adb1_fastp'
        for kind in ('telemetry', 'diagnostic', 'extension'):
            with self.subTest(kind=kind):
                frames = protocol.FrameParser().feed((directory / (kind + '.bin')).read_bytes())
                self.assertEqual(len(frames), 1)
                item = frames[0][1]
                self.assertEqual(item['kind'], kind)
                self.assertTrue(protocol.identity_matches(item))
                if kind == 'telemetry': self.assertEqual(item['control_epoch_stride'], 5)
                if kind == 'extension': self.assertIn('angle_P_ms', item)

    def test_explicit_gain_write_and_readback_using_fake_transport(self):
        import tempfile, time
        from host.adb1_fastp_bridge import install
        from host.adb1_fastp.host.fake_transport import FakeTransport
        modules = load_upstream()
        bridge = ADB1FastPBridge()
        install(bridge, modules)
        with tempfile.TemporaryDirectory() as directory:
            owner = modules['client'].SerialOwner(FakeTransport, directory).start()
            bridge.attach_owner(owner, simulated=False)
            try:
                deadline = time.monotonic() + 3
                while not owner.snapshot()['authenticated'] and time.monotonic() < deadline: time.sleep(.01)
                self.assertTrue(owner.snapshot()['authenticated'])
                result = bridge.command({'request_id':'g', 'action':'set-gains', 'value':[53.78,0,210.14,17.67,0,616.61]})
                self.assertEqual(result['state'], 'queued')
                deadline = time.monotonic() + 3
                while (bridge.status()['requests']['g']['state'] not in ('accepted','rejected') or owner.snapshot()['independent_gains'] is None) and time.monotonic() < deadline: time.sleep(.01)
                self.assertEqual(bridge.status()['requests']['g']['state'], 'accepted')
                self.assertIsNotNone(owner.snapshot()['independent_gains'])
                for action, enabled in [('adapt-enable', True), ('adapt-disable', False)]:
                    record = bridge.command({'request_id':action, 'action':action})
                    self.assertEqual(record['state'], 'queued')
                    # Same request id must never enqueue a second intent.
                    bridge.command({'request_id':action, 'action':action})
                    deadline = time.monotonic() + 3
                    while (bridge.status()['requests'][action]['state'] not in ('accepted','rejected') or
                           owner.snapshot()['latest'].get('adaptive_enabled_at_launch') is not enabled) and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertEqual(bridge.status()['requests'][action]['state'], 'accepted')
                    self.assertIs(owner.snapshot()['latest']['adaptive_enabled_at_launch'], enabled)
            finally: owner.close()
    def test_release_files_match_manifest(self):
        import hashlib, json
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / 'host' / 'adb1_fastp'
        manifest = json.loads((root / 'MANIFEST_SHA256.json').read_text(encoding='utf8'))
        for name, digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest, name)
    def test_extension_has_independent_cursor_and_time(self):
        from pathlib import Path
        bridge = ADB1FastPBridge()
        raw = (Path(__file__).parent / 'fixtures' / 'adb1_fastp' / 'extension.bin').read_bytes()
        item = load_upstream()['protocol'].FrameParser().feed(raw)[0][1]
        bridge.capture(item, received_at=123.0)
        sample = bridge.waveform()['samples'][0]
        self.assertEqual(sample['frame']['kind'], 'extension')
        self.assertEqual(sample['received_at'], 123.0)
