"""Identity and capability boundaries for the four November deliveries."""
import unittest
import subprocess
import sys
import json
from pathlib import Path

from host_bridge import BridgeError, HostClient, TelemetryAdapter, identify, validate_action, validate_config
from test_fd2_telemetry import frame, batch


PROFILES = {
    'ADB1_FASTP': (16, '41444231', '41445001', 3),
    'V4_R28': (4, '56345239', '56345208', 4),
    'V4_USB_BLE': (4, '5634523A', '56345208', 4),
    'G3_C1': (5, '56354733', '46443405', 5),
    'DS13': (5, '44533133', '44535002', 5),
}


def snapshot(profile):
    variant, uid, schema, wire = PROFILES[profile]
    return dict(protocol='FD2', variant=variant, user_code=uid, schema=schema,
                wire_version=wire, session='fixture', connected=True, synced=True,
                status_age_s=.01, control_age_s=.01, host={}, replay={'active': False})


class DeliveryProfileTests(unittest.TestCase):
    def test_exact_identity_and_configuration(self):
        for profile in PROFILES:
            item = snapshot(profile)
            with self.subTest(profile=profile):
                self.assertEqual(identify(item), profile)
                self.assertEqual(validate_config({'bridge_port': 8371, 'firmware_variant': profile})['firmware_variant'], profile)
                for field, wrong in [('schema', '00000000'), ('wire_version', 9), ('variant', True)]:
                    with self.assertRaises(BridgeError):
                        identify(dict(item, **{field: wrong}))

    def test_actual_capabilities_gate_commands(self):
        for profile, action in [('V4_R28', 'adapt-enable'), ('V4_R28', 'start-swing'),
                                ('G3_C1', 'start-swing'), ('DS13', 'start-swing'),
                                ('DS13', 'adapt-enable')]:
            with self.subTest(profile=profile, action=action), self.assertRaises(BridgeError):
                validate_action({'action': action, 'request_id': 'request'}, snapshot(profile))
        for action in ('adapt-enable', 'adapt-disable'):
            self.assertEqual(validate_action({'action':action,'request_id':action}, snapshot('ADB1_FASTP'))['action'], action)
        self.assertEqual(validate_action({'action': 'start-swing', 'request_id': 'r'}, snapshot('ADB1_FASTP'))['action'], 'start-swing')
        self.assertEqual(validate_action({'action': 'position', 'request_id': 'r', 'value': 6}, snapshot('V4_R28'))['value'], 6)
        for profile in ('G3_C1', 'DS13'):
            with self.assertRaises(BridgeError):
                validate_action({'action': 'position', 'request_id': 'r', 'value': 1}, snapshot(profile))
        self.assertEqual(validate_action({'action': 'play-c1', 'request_id': 'r'}, snapshot('G3_C1'))['action'], 'play-c1')
        with self.assertRaises(BridgeError): validate_action({'action': 'play-c1', 'request_id': 'r'}, snapshot('DS13'))

    def test_fastp_recorded_stride_and_identity_are_not_legacy_v1(self):
        item = snapshot('ADB1_FASTP')
        frames = []
        for index in range(3):
            value = frame(variant=1, seq=index+1, board=1000+5*index)
            value.update(variant=16, firmware_id=0x41444231, schema=0x41445001,
                         wire_version=3, control_epoch=100+5*index)
            frames.append(value)
        item['host']['latest'] = frames[-1]
        data, rows, _ = TelemetryAdapter().observe(item, 1000.02, waveform=batch(frames))
        self.assertEqual(data['firmware'], 'ADB1_FASTP')
        self.assertEqual([row['gap_before'] for row in rows], [True, False, False])
        self.assertTrue(data['capabilities']['adaptive'])
        bad = dict(frames[0], schema=0x41444203)
        with self.assertRaises(ValueError):
            TelemetryAdapter().observe(item, 1000.02, waveform=batch([bad]))

    def test_compact_samples_are_bound_to_authenticated_session(self):
        item = snapshot('V4_R28')
        compact = dict(kind='compact', wire_version=4, variant=4, sequence=1, board_ms=1000,
                       control_epoch=100, coherent=True, range_valid=True, flags=129, mode=3, fault=0,
                       angle_error=10, target_angle_error=0, wide_encoder=1040,
                       wide_target=1050, final_target=1060, actual_applied_q15=0)
        item['host']['latest'] = compact
        source = batch([compact])
        source['samples'][0]['session'] = item['session']
        data, rows, _ = TelemetryAdapter().observe(item, 1000, waveform=source)
        self.assertEqual(data['firmware'], 'V4_R28')
        self.assertEqual(rows[0]['arm_angle_deg'], 360)
        self.assertAlmostEqual(rows[0]['angle_deg'], 10*345/4096)
        self.assertIsNone(rows[0]['pendulum_pose_deg'])
        self.assertNotIn('firmware_id', rows[0]['raw'])
        source['samples'][0]['session'] = 'old-session'
        with self.assertRaises(ValueError):
            TelemetryAdapter().observe(item, 1000, waveform=source)

    def test_usb_ble_firmware_has_its_own_identity_and_r28_capabilities(self):
        item = snapshot('V4_USB_BLE')
        self.assertEqual(identify(item), 'V4_USB_BLE')
        self.assertEqual(validate_action({'action': 'position', 'request_id': 'p', 'value': 2, 'session':'fixture'}, item)['value'], 2)
        with self.assertRaises(BridgeError):
            validate_action({'action': 'start-swing', 'request_id': 's', 'session':'fixture'}, item)
        with self.assertRaises(BridgeError):
            validate_action({'action': 'set-gains', 'request_id': 'g', 'value': [1, 1, 1, 1, 0, 1], 'session':'fixture'}, item)
        with self.assertRaises(BridgeError):
            validate_action({'action': 'position', 'request_id': 'p', 'value': 2, 'session':'old'}, item)

    def test_v4_reports_acknowledged_position_and_measured_remaining_angle(self):
        item = snapshot('V4_USB_BLE')
        item['host']['last_position_request'] = {'value':3,'wire_request_id':17,'route':'ble','accepted_at':999.9}
        compact = dict(kind='compact',wire_version=4,variant=4,sequence=1,board_ms=1000,control_epoch=1000,
            coherent=True,range_valid=True,flags=129,mode=3,fault=0,angle_error=0,target_angle_error=0,
            wide_encoder=1040,wide_target=1100,final_target=1560,actual_applied_q15=0)
        item['host']['latest'] = compact
        page = batch([compact]);page['samples'][0]['session'] = item['session']
        data,_,_ = TelemetryAdapter().observe(item,1000,waveform=page)
        self.assertEqual(data['motion']['last_position_request']['value'],3)
        self.assertEqual(data['motion']['remaining_to_target_deg'],180)
        self.assertAlmostEqual(data['motion']['tracking_error_deg'], (1040-1100)*360/1040)
        self.assertIsNone(data['motion']['trajectory_done'])

    def test_explicit_gain_write_is_scoped_and_validated(self):
        body = {'action': 'set-gains', 'request_id': 'g', 'value': [53.78, 0, 210.14, 17.67, 0, 616.61]}
        self.assertEqual(validate_action(body, snapshot('V4_R28')), body)
        self.assertEqual(validate_action(body, snapshot('ADB1_FASTP')), body)
        for item in [dict(body, value=[1, 1, 1, 1, 0, 1]), dict(body, value=[float('nan')]*6)]:
            with self.assertRaises(BridgeError): validate_action(item, snapshot('V4_R28'))
        with self.assertRaises(BridgeError): validate_action(body, snapshot('G3_C1'))

    def test_fastp_extension_does_not_replace_diagnostic(self):
        item = snapshot('ADB1_FASTP')
        extension = dict(kind='extension', variant=16, firmware_id=0x41444231,
                         schema=0x41445001, wire_version=3, sequence=4, board_ms=1000,
                         angle_P_ms=1, angle_D_ms=5, position_P_ms=5, position_D_ms=25)
        _, rows, _ = TelemetryAdapter().observe(item, 1000, waveform=batch([extension]))
        self.assertEqual(rows[0]['channels'], [])
        self.assertEqual(rows[0]['raw']['angle_P_ms'], 1)

    def test_draw_messages_are_session_scoped_and_only_for_ds13(self):
        body = {'action': 'stroke-begin', 'request_id': 'r', 'session': 'fixture',
                'stroke_id': 'stroke-1', 'seq': 1, 'target': 192, 'issued_ms': 1700000000000}
        self.assertEqual(validate_action(body, snapshot('DS13')), body)
        for bad in [dict(body, target=193), dict(body, session='other'), dict(body, seq=True), dict(body, issued_ms=True)]:
            with self.assertRaises(BridgeError): validate_action(bad, snapshot('DS13'))
        with self.assertRaises(BridgeError): validate_action(body, snapshot('G3_C1'))

    def test_gain_evidence_does_not_turn_draft_into_readback(self):
        item = snapshot('ADB1_FASTP')
        item['host']['independent_gains'] = None
        data, _, _ = TelemetryAdapter().observe(item, 1000, waveform=batch([]))
        self.assertIsNone(data['pid_gains']['independent'])
        item['host']['independent_gains'] = [[53.78, 0, 210.14, 17.67, 0, 616.61], 2]
        data, _, _ = TelemetryAdapter().observe(item, 1000, waveform=batch([]))
        self.assertEqual(data['pid_gains']['independent'][0], 53.78)
        item = snapshot('V4_R28')
        item['host']['independent'] = [53.78, 0, 210.14, 17.67, 0, 616.61]
        data, _, _ = TelemetryAdapter().observe(item, 1000, waveform=batch([]))
        self.assertEqual(data['pid_gains']['independent'][0], 53.78)

    def test_gowin_cursor_uses_encoded_query(self):
        client = HostClient(8371)
        client.exchange = lambda path: {'path': path}
        self.assertEqual(client.gowin(12, 'session A')['path'], '/gowin?limit=512&after=12&stream=session+A')

    def test_offline_launchers_default_to_disconnected(self):
        root = Path(__file__).resolve().parents[1]
        for profile in PROFILES:
            with self.subTest(profile=profile):
                proc = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(root/'launch_host.py'),
                                       profile, '--headless', '--once', '--port', '0'], cwd=root,
                                      capture_output=True, text=True, encoding='utf-8', timeout=15)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                item = json.loads(proc.stdout)
                self.assertEqual(identify(item), profile)
                self.assertFalse(item['connected'])

    def test_replay_retention_uses_recorded_time_not_current_wall_clock(self):
        from main import retention_reference
        rows=[{'received_at': 100.0}, {'received_at': 105.0}]
        self.assertEqual(retention_reference({'replay': {'active': True}}, rows, 10000.0),105.0)
        self.assertEqual(retention_reference({'replay': {'active': False}}, rows, 10000.0),10000.0)


if __name__ == '__main__':
    unittest.main()
