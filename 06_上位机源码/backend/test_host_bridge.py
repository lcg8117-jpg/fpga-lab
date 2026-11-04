import copy
import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

from host_bridge import BridgeError, HostClient, TelemetryAdapter, identify, validate_action, validate_config


def fixture(variant='SG21', stamp=200):
    adaptive = variant == 'SA21'
    return dict(user_code='53413231' if adaptive else '53473231', schema='53415421' if adaptive else '53475421',
                connected=True, synced=True, status_age_s=.02, log_directory='session-a',
                status=dict(mode=3, fault=0, pwm=-16384, revision=7, stopped_config_allowed=False,
                            pwm_zero=False, run_session=True, swing_active=False, zero_capture=False, estop=False),
                telemetry=dict(angle=dict(timestamp_ms=stamp, alpha_q20=-1048576, alpha_dot_q20=2097152, theta_dot_q20=3),
                               arm=dict(timestamp_ms=stamp+1, encoder_count=-99, pwm=-16384, mode=3, fault=0),
                               gesture=dict(sensor_status='2076010000380200', motion_status='0500000009000000')),
                parameters=dict(angle_kp=dict(value=900, revision=7)), parameter_specs=[], requests={}, note='fixture')


class AdapterTests(unittest.TestCase):
    def test_identity_is_a_pair_and_expected_version_is_enforced(self):
        self.assertEqual(identify(fixture()), 'SG21')
        self.assertEqual(identify(fixture('SA21'), 'SA21'), 'SA21')
        for patch in ({'schema':'53415421'}, {'user_code':'53473230'}, {'schema':None}):
            with self.assertRaises(BridgeError): identify(dict(fixture(), **patch))
        with self.assertRaises(BridgeError): identify(fixture(), 'SA21')

    def test_signed_scaling_missing_fields_and_separate_sample_epochs(self):
        data, rows, reset = TelemetryAdapter().observe(fixture(), 1000)
        self.assertTrue(reset)
        self.assertAlmostEqual(data['angle_deg'], -180/math.pi)
        self.assertEqual(data['duty_actual_percent'], -50)
        self.assertEqual(data['encoder'], -99)
        self.assertIsNone(data['arm_angle_deg'])
        self.assertIsNone(data['position_error'])
        self.assertEqual([r['fpga_time_ms'] for r in rows], [200, 201])
        self.assertEqual(rows[0]['channels'], ['angle_deg','angle_velocity_deg_s'])
        self.assertEqual(rows[1]['channels'], ['duty_actual_percent'])
        self.assertEqual(data['gesture']['code'], 7)
        self.assertNotIn('ready', data['gesture'])

    def test_repeated_snapshots_are_not_samples_and_pages_expire_independently(self):
        adapter = TelemetryAdapter()
        source = fixture()
        _, rows, _ = adapter.observe(source, 1000)
        data, repeat, _ = adapter.observe(source, 1000.5)
        self.assertEqual(repeat, [])
        self.assertEqual(data['angle_received_at'], 1000)
        source['telemetry']['arm']['timestamp_ms'] += 2
        data, rows, _ = adapter.observe(source, 1002.1)
        self.assertIsNone(data['angle_deg'])
        self.assertEqual(data['duty_actual_percent'], -50)
        self.assertEqual(len(rows), 1)

    def test_session_and_device_clock_reset_drop_previous_page_evidence(self):
        adapter = TelemetryAdapter()
        adapter.observe(fixture(stamp=500), 1000)
        data, rows, reset = adapter.observe(fixture(stamp=20), 1001)
        self.assertTrue(reset)
        self.assertEqual(len(rows), 2)
        self.assertEqual(data['fpga_time_ms'], 20)
        next_session = fixture(stamp=20)
        next_session['log_directory'] = 'session-b'
        self.assertTrue(adapter.observe(next_session, 1002)[2])

    def test_status_recovery_requires_new_page_evidence_not_a_cached_replay(self):
        adapter=TelemetryAdapter(); source=fixture()
        adapter.observe(source,1000)
        adapter.observe(dict(source,status_age_s=5),1001)
        data,rows,_=adapter.observe(source,1002)
        self.assertEqual(rows,[])
        self.assertIsNone(data['angle_deg'])
        self.assertEqual(data['angle_received_at'],0)
        source['telemetry']['angle']['timestamp_ms']+=2
        data,rows,_=adapter.observe(source,1002.1)
        self.assertEqual(len(rows),1)
        self.assertIsNone(data['duty_actual_percent'])

    def test_reboot_pages_can_arrive_separately_without_replaying_the_other_page(self):
        adapter=TelemetryAdapter(); source=fixture(stamp=500)
        adapter.observe(source,1000)
        source['telemetry']['angle']['timestamp_ms']=20
        data,rows,reset=adapter.observe(source,1001)
        self.assertTrue(reset)
        self.assertEqual([r['fpga_time_ms'] for r in rows],[20])
        self.assertIsNone(data['duty_actual_percent'])
        source['telemetry']['arm']['timestamp_ms']=21
        data,rows,reset=adapter.observe(source,1001.1)
        self.assertFalse(reset)
        self.assertEqual([r['fpga_time_ms'] for r in rows],[21])
        self.assertEqual(data['angle_received_at'],1001)

    def test_reboot_suppresses_an_unobserved_old_epoch_page_advance(self):
        adapter=TelemetryAdapter();source=fixture(stamp=500)
        adapter.observe(source,1000)
        source['telemetry']['angle']['timestamp_ms']=20
        source['telemetry']['arm']['timestamp_ms']=550
        data,rows,_=adapter.observe(source,1001)
        self.assertEqual([r['fpga_time_ms'] for r in rows],[20])
        self.assertIsNone(data['duty_actual_percent'])
        source['telemetry']['arm']['timestamp_ms']=551
        self.assertEqual(adapter.observe(source,1001.05)[1],[])
        source['telemetry']['arm']['timestamp_ms']=21
        data,rows,reset=adapter.observe(source,1001.1)
        self.assertFalse(reset)
        self.assertEqual([r['fpga_time_ms'] for r in rows],[21])

    def test_disconnect_stale_status_and_invalid_numbers_never_become_live_zero(self):
        for patch in ({'connected':False}, {'synced':False}, {'status_age_s':5}, {'status_age_s':float('nan')}):
            data, rows, _ = TelemetryAdapter().observe(dict(fixture(), **patch), 1000)
            self.assertFalse(data['status_fresh'])
            self.assertIsNone(data['angle_deg'])
            self.assertEqual(rows, [])
        source = fixture()
        source['telemetry']['angle']['alpha_q20'] = True
        source['telemetry']['arm']['pwm'] = -40000
        data, rows, _ = TelemetryAdapter().observe(source, 1000)
        self.assertIsNone(data['angle_deg'])
        self.assertIsNone(data['duty_actual_percent'])

    def test_old_parameter_revision_is_not_current_and_adaptive_epochs_remain_distinct(self):
        source=fixture('SA21')
        source['parameters']['angle_kp']['revision']=6
        source['telemetry'].update(adaptive=dict(selected=1,effective=1,sample_ms=19),
                                   adaptive_gains=dict(kp_q24=16777216,ki_q24=0,kd_q24=33554432,sample_ms=20))
        data, _, _=TelemetryAdapter().observe(source,1000)
        self.assertFalse(data['parameters']['angle_kp']['current'])
        self.assertEqual(data['adaptive']['sample_ms'],19)
        self.assertEqual(data['adaptive_gains']['sample_ms'],20)


class CommandTests(unittest.TestCase):
    def test_only_delivered_actions_are_allowed_and_request_id_is_retained(self):
        source=fixture()
        body=dict(action='stop',request_id='one-explicit-click')
        self.assertEqual(validate_action(body,source),body)
        for action in ('connect','disconnect','PROBE','START'):
            with self.assertRaises(BridgeError): validate_action(dict(body,action=action),source)
        with self.assertRaises(BridgeError): validate_action(dict(body,extra=1),source)
        with self.assertRaises(BridgeError): validate_action(dict(body,request_id=''),source)

    def test_stop_remains_available_for_an_identified_other_variant(self):
        self.assertEqual(validate_action(dict(action='stop',request_id='s'),fixture(),'SA21')['action'],'stop')
        with self.assertRaises(BridgeError): validate_action(dict(action='run',request_id='r'),fixture(),'SA21')

    def test_pid_restore_is_exactly_nine_explicit_values_without_defaults(self):
        values=dict(angle_kp=900,angle_ki=870,angle_kd=90,position_kp=112,position_ki=0,position_kd=48,
                    ki_enable=1,angle_d_filter=650,angle_window=5)
        body=dict(action='restore-pid',request_id='restore',value=values)
        self.assertEqual(validate_action(body,fixture())['value'],values)
        bad=copy.deepcopy(body); del bad['value']['angle_ki']
        with self.assertRaises(BridgeError): validate_action(bad,fixture())
        for value in (-1,65536,True,1.5):
            with self.assertRaises(BridgeError): validate_action(dict(action='set',request_id='x',name='angle_kp',value=value),fixture())

    def test_adaptive_and_profile_paths_are_variant_guarded(self):
        body=dict(action='set',request_id='mode',name='adaptive_mode',value=1)
        with self.assertRaises(BridgeError): validate_action(body,fixture())
        self.assertEqual(validate_action(body,fixture('SA21'))['value'],1)
        with self.assertRaises(BridgeError): validate_action(dict(body,name='adaptive_profile'),fixture('SA21'))
        with self.assertRaises(BridgeError): validate_action(dict(action='load-profile',request_id='p',value={'profile':{},'control':False}),fixture())

    def test_bridge_configuration_cannot_select_a_remote_address(self):
        self.assertEqual(validate_config({'bridge_port':8371,'firmware_variant':'auto'})['bridge_port'],8371)
        for patch in ({'bridge_port':0},{'bridge_port':True},{'bridge_url':'http://other'},{'firmware_variant':'V512'}):
            with self.assertRaises(BridgeError): validate_config(dict(bridge_port=8371,firmware_variant='auto',**{} ) | patch)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        calls=self.calls
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                calls.append((self.command,self.path,dict(self.headers)))
                raw=json.dumps(fixture()).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(raw)
            def do_POST(self):
                calls.append((self.command,self.path,dict(self.headers)))
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(400); self.end_headers(); self.wfile.write(b'{"error":"fixture rejection"}')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.client=HostClient(self.server.server_port)
    def tearDown(self):
        self.server.shutdown(); self.server.server_close()
    def test_loopback_headers_and_no_retry_after_rejection(self):
        self.assertEqual(self.client.status()['user_code'],'53473231')
        with self.assertRaises(BridgeError): self.client.command({'action':'stop','request_id':'s'})
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.calls[0][2]['X-J280-Local'],'1')
        self.assertNotIn('Origin',self.calls[0][2])

    def test_request_id_round_trips_the_delivered_hosts_raw_path_lookup(self):
        self.client.result('scope:one')
        self.assertEqual(self.calls[0][1],'/request/scope:one')


if __name__=='__main__': unittest.main()
