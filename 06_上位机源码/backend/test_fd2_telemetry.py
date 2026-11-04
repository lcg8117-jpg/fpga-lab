"""FD2 display contract tests. Synthetic decoded frames; no device access."""
import copy
import math
import unittest

from host_bridge import BridgeError, TelemetryAdapter, identify, validate_action, validate_config

IDS = {1: ('46443231', '46443203', 2), 2: ('46443432', '46443402', 3),
       3: ('46443233', '46443203', 2), 4: ('46443434', '46443404', 4),
       5: ('46443435', '46443405', 5)}


def frame(variant=5, seq=1, board=1000, **changes):
    uid, schema, wire = IDS[variant]
    return dict(kind='telemetry', variant=variant, firmware_id=int(uid, 16), schema=int(schema, 16),
                wire_version=wire, sequence=seq, board_ms=board, control_epoch=seq, coherent=True,
                flags=23, mode=3, fault=0, mean_adc12=3086, target_angle_adc12=3085,
                actual_applied_q15=-16384, wide_encoder=1040, wide_target=2080,
                position_error=-1040, manual_gains=[49, 0, 251, 25, 0, 610],
                active_gains=[50, 0, 252, 25, 0, 610], adaptive_target_gains=[51, 0, 253, 25, 0, 610],
                adaptive_enabled_at_launch=variant == 3, adaptive_used_at_launch=variant == 3,
                freeze_reason=0, gesture_event_seq=3, gesture_code=1, action_reason=4,
                swing_profile=0, swing_stage=0, **changes)


def diagnostic(variant=5, seq=1, board=1010, **changes):
    item = frame(variant, seq, board)
    item.update(kind='diagnostic', coherent=False, origin=1040, final_target=2080,
                final_target_valid=True, motion_request_id=3, motion_action=0x21, planner_phase=2,
                trajectory_done=True, tracking_settled=False, settle_ticks=9,
                measured_velocity=20, tracking_error=100, stop_reason=0)
    item.update(changes)
    return item


def snapshot(variant=5, **changes):
    uid, schema, wire = IDS[variant]
    item = frame(variant)
    result = dict(protocol='FD2', variant=variant, user_code=uid, schema=schema, wire_version=wire,
                  session='offline-test', connected=True, synced=True,
                  status_age_s=.01, control_age_s=.01, diagnostic_age_s=.01,
                  host=dict(latest=item, last_control=item, latest_diagnostic=None,
                            actual_gain_averages=dict(valid=True, windows=[]), crc_errors=0, sequence_gaps=0),
                  requests={}, transport=dict(usb_connected=True, ble_connected=False),
                  replay=dict(active=False, finished=False))
    result.update(changes)
    return result


def batch(items=None, stream='offline-test', start=1, received=1000):
    items = [frame()] if items is None else items
    rows = [dict(cursor=start+i, received_at=received+i*.005, frame=item, gap_before=False)
            for i, item in enumerate(items)]
    return dict(stream=stream, cursor=start+len(rows)-1, head=1, tail=start+len(rows)-1,
                overrun=False, samples=rows)


class FD2IdentityTests(unittest.TestCase):
    def test_five_exact_identities_and_configs(self):
        for variant in IDS:
            self.assertEqual(identify(snapshot(variant)), f'V{variant}')
            self.assertEqual(validate_config(dict(bridge_port=8371, firmware_variant=f'V{variant}'))['firmware_variant'], f'V{variant}')
            for key, value in [('schema', '00000000'), ('wire_version', 99), ('variant', True)]:
                with self.subTest(variant=variant, key=key), self.assertRaises(BridgeError):
                    identify(dict(snapshot(variant), **{key: value}))

    def test_capability_gates_and_no_implicit_swing_for_v5(self):
        self.assertEqual(validate_action(dict(action='trajectory', request_id='one', value=6), snapshot())['value'], 6)
        self.assertEqual(validate_action(dict(action='balance', request_id='one'), snapshot())['action'], 'balance')
        for body, status in [(dict(action='start-swing', request_id='x'), snapshot()),
                             (dict(action='trajectory', request_id='x', value=1), snapshot(4)),
                             (dict(action='position', request_id='x', value=True), snapshot()),
                             (dict(action='position', request_id='x', value=7), snapshot()),
                             (dict(action='adapt-enable', request_id='x'), snapshot(2)),
                             (dict(action='balance', request_id='x', value=1), snapshot())]:
            with self.subTest(body=body), self.assertRaises(BridgeError): validate_action(body, status)

    def test_stale_replay_and_wrong_variant_never_allow_motion(self):
        body = dict(action='position', request_id='p', value=1)
        for status in [snapshot(control_age_s=1), snapshot(synced=False),
                       snapshot(replay=dict(active=True)), snapshot(status_age_s=2)]:
            with self.assertRaises(BridgeError): validate_action(body, status)
        with self.assertRaises(BridgeError): validate_action(body, snapshot(), 'V4')
        self.assertEqual(validate_action(dict(action='stop', request_id='s'), snapshot(control_age_s=9), 'V4')['action'], 'stop')
        with self.assertRaises(BridgeError):
            validate_action(dict(action='stop', request_id='s'), snapshot(replay=dict(active=True)))


class FD2AdapterTests(unittest.TestCase):
    def test_full_200hz_batch_uses_board_time_and_keeps_wide_raw_integers(self):
        items = [frame(seq=i+1, board=1000+5*i) for i in range(200)]
        items[-1]['wide_encoder'] = str(2**60 + 1)
        data, rows, reset = TelemetryAdapter().observe(snapshot(), 1001, waveform=batch(items))
        self.assertTrue(reset)
        self.assertEqual(len(rows), 200)
        self.assertAlmostEqual(rows[-1]['plot_at']-rows[0]['plot_at'], .995)
        self.assertEqual(rows[-1]['raw']['wide_encoder'], str(2**60+1))
        self.assertAlmostEqual(rows[0]['angle_deg'], 345/4096)
        self.assertEqual(rows[0]['arm_angle_deg'], 360)
        self.assertEqual(rows[0]['arm_reference_deg'], 720)
        self.assertEqual(rows[0]['duty_actual_percent'], -50)
        self.assertTrue(data['display_fresh'])
        self.assertFalse(data['capabilities']['swing_up'])

    def test_independent_diagnostic_target_and_reference_finished_is_not_settled(self):
        source = snapshot()
        source['host']['latest_diagnostic'] = diagnostic()
        data, rows, _ = TelemetryAdapter().observe(source, 1000.02, waveform=batch([frame(), diagnostic()]))
        self.assertEqual(rows[1]['channels'], ['final_target_deg'])
        self.assertNotIn('final_target_deg', rows[0]['channels'])
        self.assertAlmostEqual(rows[1]['plot_at']-rows[0]['plot_at'], .01)
        self.assertTrue(data['motion']['trajectory_done'])
        self.assertFalse(data['motion']['tracking_settled'])
        self.assertEqual(data['motion']['settle_ms'], 45)
        self.assertEqual(data['motion']['origin_count'], '1040')

    def test_diagnostic_expiry_clears_target_and_settled_without_expiring_control(self):
        source = snapshot(diagnostic_age_s=1)
        source['host']['latest_diagnostic'] = diagnostic(tracking_settled=True)
        data, _, _ = TelemetryAdapter().observe(source, 1000.02, waveform=batch([frame(), diagnostic()]))
        self.assertTrue(data['display_fresh'])
        self.assertFalse(data['fd2']['diagnostic_fresh'])
        self.assertIsNone(data['motion']['final_target_deg'])
        self.assertFalse(data['motion']['tracking_settled'])

    def test_repeated_cursor_does_not_duplicate_or_refresh_samples(self):
        adapter = TelemetryAdapter()
        adapter.observe(snapshot(), 1000, waveform=batch())
        data, rows, reset = adapter.observe(snapshot(), 1001, waveform=batch())
        self.assertFalse(reset)
        self.assertEqual(rows, [])
        self.assertFalse(data['display_fresh'])
        self.assertIsNone(data['angle_deg'])

    def test_status_frames_are_not_control_samples(self):
        item = frame(); item['coherent'] = False
        data, rows, _ = TelemetryAdapter().observe(snapshot(), 1000, waveform=batch([item]))
        self.assertEqual(rows, [])
        self.assertFalse(data['display_fresh'])

    def test_missing_epochs_break_curves_even_if_http_cursor_is_contiguous(self):
        items = [frame(), frame(seq=4, board=1015)]
        _, rows, _ = TelemetryAdapter().observe(snapshot(), 1000.02, waveform=batch(items))
        self.assertTrue(rows[1]['gap_before'])

    def test_session_reset_and_board_reset_start_new_segments(self):
        adapter = TelemetryAdapter()
        adapter.observe(snapshot(), 1000, waveform=batch([frame(board=5000)]))
        _, rows, reset = adapter.observe(snapshot(), 1000.1, waveform=batch([frame(seq=2, board=10)], start=2, received=1000.1))
        self.assertTrue(reset)
        self.assertTrue(rows[0]['gap_before'])
        old = rows[0]['session_epoch']
        _, rows, reset = adapter.observe(snapshot(session='new'), 1000.2,
                                       waveform=batch([frame()], stream='new', received=1000.2))
        self.assertTrue(reset)
        self.assertNotEqual(rows[0]['session_epoch'], old)

    def test_millisecond_wrap_is_continuous_not_a_reboot(self):
        items = [frame(board=0xfffffffc), frame(seq=2, board=1)]
        _, rows, _ = TelemetryAdapter().observe(snapshot(), 1000.01, waveform=batch(items))
        self.assertAlmostEqual(rows[1]['plot_at']-rows[0]['plot_at'], .005)
        self.assertFalse(rows[1]['gap_before'])

    def test_malformed_batch_is_atomic_and_can_be_refetched(self):
        adapter = TelemetryAdapter(); broken = batch([frame(), frame(seq=2, board=1005)])
        broken['samples'][1]['frame']['firmware_id'] = 0
        with self.assertRaises(ValueError): adapter.observe(snapshot(), 1000.01, waveform=broken)
        _, rows, _ = adapter.observe(snapshot(), 1000.01, waveform=batch([frame(), frame(seq=2, board=1005)]))
        self.assertEqual(len(rows), 2)

    def test_adaptive_uses_actual_gains_and_preserves_window_coverage(self):
        source = snapshot(3)
        averages = dict(valid=True, windows=[dict(window_ms=5000, covered_ms=1200, complete=False, kp=50, kd=252)])
        source['host']['actual_gain_averages'] = averages
        data, rows, _ = TelemetryAdapter().observe(source, 1000, waveform=batch([frame(3)]))
        self.assertEqual(data['adaptive']['averages'], averages)
        self.assertEqual(rows[0]['angle_kp'], 50)
        self.assertEqual(data['adaptive']['target_gains'][0], 51)

    def test_recognition_does_not_invent_causal_acceptance(self):
        data, _, _ = TelemetryAdapter().observe(snapshot(2), 1000, waveform=batch([frame(2)]))
        self.assertEqual(data['gesture_event']['code'], 1)
        self.assertEqual(data['action_reason'], 4)
        self.assertNotIn('accepted', data['gesture_event'])

    def test_replay_shows_data_without_claiming_live_hardware(self):
        source = snapshot(connected=False, synced=False, replay=dict(active=True, finished=True))
        data, _, _ = TelemetryAdapter().observe(source, 1002, waveform=batch())
        self.assertFalse(data['connected'])
        self.assertFalse(data['status_fresh'])
        self.assertTrue(data['display_fresh'])
        self.assertIsNotNone(data['angle_deg'])


class FD2PendulumPoseTests(unittest.TestCase):
    def test_positive_down_pose_keeps_clipped_control_angle_and_raw_frame(self):
        item = frame(quiet_q4=8616, zero_q4=1000)
        item['mean_adc12'] = 4095
        original = copy.deepcopy(item)
        data, rows, _ = TelemetryAdapter().observe(snapshot(), 1000, waveform=batch([item]))
        self.assertIn('pendulum_pose_deg', rows[0])
        self.assertAlmostEqual(rows[0]['pendulum_pose_deg'], 180.01078304746662)
        self.assertAlmostEqual(data['pendulum_pose_deg'], rows[0]['pendulum_pose_deg'])
        self.assertAlmostEqual(data['angle_deg'], 85.07080078125)
        self.assertEqual(rows[0]['channels'], ['angle_deg', 'alpha_ref_deg', 'arm_angle_deg',
                                             'arm_reference_deg', 'duty_actual_percent', 'angle_kp', 'angle_kd'])
        self.assertTrue(rows[0]['angle_display_limited'])
        self.assertTrue(data['angle_display_limited'])
        self.assertEqual(rows[0]['alpha_ref_deg'], 0)
        self.assertEqual(rows[0]['raw']['quiet_q4'], 8616)
        self.assertEqual(item, original)

    def test_negative_down_pose_and_zero_wrap_preserve_signed_direction(self):
        cases = [(1000, 8616, -7616), (100, 16300, 184), (16300, 100, -184),
                 (8192, 0, -8192), (0, 8192, -8192), (16383, 16383, 0)]
        for variant in IDS:
            for quiet, zero, delta in cases:
                with self.subTest(variant=variant, quiet=quiet, zero=zero):
                    item = frame(variant, quiet_q4=quiet, zero_q4=zero)
                    data, rows, _ = TelemetryAdapter().observe(snapshot(variant), 1000,
                                                               waveform=batch([item]))
                    expected = delta * 6921 / (16 * 2**20) * 180 / math.pi
                    self.assertIn('pendulum_pose_deg', rows[0])
                    self.assertAlmostEqual(rows[0]['pendulum_pose_deg'], expected)
                    self.assertAlmostEqual(data['pendulum_pose_deg'], expected)
                    self.assertFalse(data['angle_display_limited'])

    def test_missing_invalid_q4_and_missing_zero_flag_never_invent_pose(self):
        cases = [('quiet_q4', None), ('zero_q4', None), ('quiet_q4', -1),
                 ('zero_q4', 16384), ('quiet_q4', 16384), ('zero_q4', -1),
                 ('quiet_q4', True), ('zero_q4', False), ('quiet_q4', 1.5),
                 ('zero_q4', float('nan')), ('flags', 7), ('flags', None), ('flags', True)]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                item = frame(quiet_q4=8616, zero_q4=1000)
                if value is None:
                    item.pop(key)
                else:
                    item[key] = value
                data, rows, _ = TelemetryAdapter().observe(snapshot(), 1000, waveform=batch([item]))
                self.assertIn('pendulum_pose_deg', rows[0])
                self.assertIsNone(rows[0]['pendulum_pose_deg'])
                self.assertIsNone(data['pendulum_pose_deg'])

    def test_limitation_flag_uses_same_coherent_control_frame(self):
        for mean, limited in [(0, True), (4095, True), (3085, False), (None, False),
                              (True, False), (4096, False)]:
            with self.subTest(mean=mean):
                item = frame(quiet_q4=1000, zero_q4=1000)
                item['mean_adc12'] = mean
                data, rows, _ = TelemetryAdapter().observe(snapshot(), 1000, waveform=batch([item]))
                self.assertIn('angle_display_limited', rows[0])
                self.assertIs(rows[0]['angle_display_limited'], limited)
                self.assertIs(data['angle_display_limited'], limited)
        item = frame(quiet_q4=8616, zero_q4=1000)
        item.update(coherent=False, mean_adc12=4095)
        data, rows, _ = TelemetryAdapter().observe(snapshot(), 1000, waveform=batch([item]))
        self.assertEqual(rows, [])
        self.assertIn('pendulum_pose_deg', data)
        self.assertIsNone(data['pendulum_pose_deg'])
        self.assertFalse(data['angle_display_limited'])

    def test_stale_disconnected_and_invalidated_control_clear_display_fields(self):
        for state, now in [(snapshot(), 1001), (snapshot(control_age_s=1), 1000.01),
                           (snapshot(status_age_s=1), 1000.01),
                           (snapshot(connected=False), 1000.01)]:
            with self.subTest(state=state, now=now):
                item = frame(quiet_q4=8616, zero_q4=1000)
                item['mean_adc12'] = 4095
                adapter = TelemetryAdapter()
                data, _, _ = adapter.observe(snapshot(), 1000, waveform=batch([item]))
                self.assertIn('pendulum_pose_deg', data)
                self.assertIsNotNone(data['pendulum_pose_deg'])
                data, _, _ = adapter.observe(state, now, waveform=batch([]))
                self.assertIsNone(data['pendulum_pose_deg'])
                self.assertFalse(data['angle_display_limited'])
        adapter = TelemetryAdapter()
        adapter.observe(snapshot(), 1000, waveform=batch([item]))
        adapter.invalidate()
        data, _, _ = adapter.observe(snapshot(), 1000.01, waveform=batch([item]))
        self.assertIn('pendulum_pose_deg', data)
        self.assertIsNone(data['pendulum_pose_deg'])
        self.assertFalse(data['angle_display_limited'])

    def test_new_control_with_missing_pose_does_not_retain_previous_pose(self):
        item = frame(quiet_q4=8616, zero_q4=1000)
        item['mean_adc12'] = 4095
        adapter = TelemetryAdapter()
        adapter.observe(snapshot(), 1000, waveform=batch([item]))
        data, _, _ = adapter.observe(snapshot(), 1000.01,
                                     waveform=batch([frame(seq=2, board=1005)], start=2, received=1000.005))
        self.assertIn('pendulum_pose_deg', data)
        self.assertIsNone(data['pendulum_pose_deg'])
        self.assertFalse(data['angle_display_limited'])

    def test_replay_retains_last_pose_without_claiming_live_hardware(self):
        item = frame(quiet_q4=8616, zero_q4=1000)
        item['mean_adc12'] = 4095
        source = snapshot(connected=False, synced=False, replay=dict(active=True, finished=True))
        adapter = TelemetryAdapter()
        data, _, _ = adapter.observe(source, 1002, waveform=batch([item]))
        self.assertIn('pendulum_pose_deg', data)
        self.assertAlmostEqual(data['pendulum_pose_deg'], 180.01078304746662)
        data, _, _ = adapter.observe(source, 1100, waveform=batch([]))
        self.assertAlmostEqual(data['pendulum_pose_deg'], 180.01078304746662)
        self.assertTrue(data['angle_display_limited'])
        self.assertFalse(data['connected'])
        self.assertFalse(data['status_fresh'])


if __name__ == '__main__': unittest.main()
