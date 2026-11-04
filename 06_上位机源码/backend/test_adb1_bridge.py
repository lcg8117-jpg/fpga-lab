import sys
import time
import unittest
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from host.adb1_bridge import ADB1Bridge
from host.adb1 import protocol as p
from host_bridge import identify, validate_config, validate_action, HostClient, TelemetryAdapter
from fd2_telemetry import FD2TelemetryAdapter


class Owner:
    def __init__(self):
        self.last_frame = self.last_control = time.monotonic()
        self.identity_latched_error = False
        self.state = dict(connected=True, authenticated=True, error='', latest=None)

    def snapshot(self):
        return dict(self.state)


def frame(epoch=1):
    return dict(kind='telemetry', variant=p.VARIANT, wire_version=p.VERSION,
                firmware_id=p.FIRMWARE, schema=p.SCHEMA, coherent=True,
                sequence=epoch, control_epoch=epoch, board_ms=epoch*5,
                flags=17, mode=3, fault=0, mean_adc12=3085, zero_q4=1600,
                quiet_q4=1600, target_angle_adc12=3085, wide_encoder=100,
                wide_target=110, final_target=130, actual_applied_q15=100,
                active_gains=[49,0,260,25,0,620], manual_gains=[49,0,260,25,0,620])


class AdbBridgeTests(unittest.TestCase):
    def test_control_epoch_restart_with_forward_board_time_is_accepted(self):
        from host.adb1.client import SerialOwner
        from host.adb1.app import Window
        from host.adb1_bridge import install
        original=(SerialOwner._receive, SerialOwner._send, SerialOwner._event, Window._connect)
        bridge=ADB1Bridge(); install(bridge)
        try:
            owner=SerialOwner(lambda:None,'.')
            class Logger:
                error=None
                def submit(self,*args,**kwargs): pass
                def metrics(self): return {}
            owner.logger=Logger()
            owner.state.update(connected=True,authenticated=True,identity_seen=True,status_seen=True,capabilities=p.CAPABILITIES)
            bridge.attach_owner(owner,simulated=False)
            before=frame(6797); before.update(sequence=7453,board_ms=99706)
            after=frame(1); after.update(sequence=7454,board_ms=99713)
            with owner.lock:
                owner._receive(b'',before)
                owner._receive(b'',after)
            self.assertEqual(owner.history[-1]['control_epoch'],1)
            self.assertEqual(owner.state['duplicate_controls'],0)
            data,rows,_=FD2TelemetryAdapter().observe(bridge.status(),time.time(),bridge.waveform(),{})
            self.assertTrue(data['display_fresh'])
            self.assertTrue(rows[-1]['gap_before'])
        finally:
            SerialOwner._receive, SerialOwner._send, SerialOwner._event, Window._connect=original

    def test_old_epoch_frame_is_not_accepted_as_restart(self):
        from host.adb1.client import SerialOwner
        from host.adb1.app import Window
        from host.adb1_bridge import install
        original=(SerialOwner._receive, SerialOwner._send, SerialOwner._event, Window._connect)
        bridge=ADB1Bridge(); install(bridge)
        try:
            owner=SerialOwner(lambda:None,'.')
            class Logger:
                error=None
                def submit(self,*args,**kwargs): pass
                def metrics(self): return {}
            owner.logger=Logger()
            owner.state.update(connected=True,authenticated=True,identity_seen=True,status_seen=True,capabilities=p.CAPABILITIES)
            bridge.attach_owner(owner,simulated=False)
            before=frame(6797); before.update(sequence=7453,board_ms=99706)
            old=frame(1); old.update(sequence=7452,board_ms=99700)
            with owner.lock:
                owner._receive(b'',before)
                owner._receive(b'',old)
            self.assertEqual(owner.history[-1]['control_epoch'],6797)
            self.assertEqual(len(bridge.waveform()['samples']),1)
        finally:
            SerialOwner._receive, SerialOwner._send, SerialOwner._event, Window._connect=original

    def test_live_identity_telemetry_and_controls(self):
        bridge=ADB1Bridge(); owner=Owner(); bridge.attach_owner(owner, simulated=False)
        owner.state['latest']=frame(); bridge.capture_owner(owner,frame())
        status=bridge.status(); self.assertEqual(identify(status),'ADB1')
        self.assertEqual(validate_config({'firmware_variant':'ADB1'})['firmware_variant'],'ADB1')
        self.assertEqual(validate_action({'action':'start-swing','request_id':'abc'},status)['action'],'start-swing')
        data, rows, _=FD2TelemetryAdapter().observe(status,time.time(),bridge.waveform(),{})
        self.assertEqual(data['firmware'],'ADB1')
        self.assertEqual(data['encoder'],'100')
        self.assertEqual(rows[0]['final_target_deg'],130*360/1040)
        self.assertEqual(data['motion']['final_target_count'],'130')

    def test_demo_never_appears_as_live_and_old_owner_frames_are_ignored(self):
        bridge=ADB1Bridge(); old=Owner(); bridge.attach_owner(old,simulated=False)
        current=Owner(); bridge.attach_owner(current,simulated=True)
        bridge.capture_owner(old,frame()); bridge.capture_owner(current,frame())
        status=bridge.status()
        self.assertFalse(status['connected'])
        self.assertTrue(status['replay']['active'])
        self.assertEqual(status['replay']['label'],'离线模拟')
        self.assertEqual(len(bridge.waveform()['samples']),1)
        with self.assertRaises(ValueError):
            bridge.command({'action':'stop','request_id':'demo-stop'})

    def test_simulation_stops_being_fresh_after_its_owner_stalls(self):
        bridge=ADB1Bridge(); owner=Owner(); bridge.attach_owner(owner,simulated=True)
        bridge.capture_owner(owner,frame())
        owner.last_frame=owner.last_control=time.monotonic()-2
        status=bridge.status()
        data, _, _=FD2TelemetryAdapter().observe(status,time.time(),bridge.waveform(),{})
        self.assertFalse(data['display_fresh'])

    def test_original_fake_owner_to_http_and_web_adapter(self):
        from host.adb1.client import SerialOwner
        from host.adb1.app import Window
        from host.adb1.fake_transport import FakeTransport
        from host.adb1_bridge import install
        originals = (SerialOwner._receive, SerialOwner._send, SerialOwner._event, Window._connect)
        bridge=ADB1Bridge(); server=bridge.serve(0); install(bridge)
        try:
            with tempfile.TemporaryDirectory() as folder:
                owner=SerialOwner(FakeTransport,folder)
                bridge.attach_owner(owner,simulated=True); owner.start()
                try:
                    deadline=time.monotonic()+3
                    while time.monotonic()<deadline and owner.snapshot()['control_received']<5:
                        time.sleep(.01)
                    client=HostClient(server.server_port)
                    status=client.status(); page=client.waveform()
                    data,rows,_=TelemetryAdapter().observe(status,time.time(),'ADB1',waveform=page)
                    self.assertEqual(data['firmware'],'ADB1')
                    self.assertTrue(data['replay']['active'])
                    self.assertFalse(data['connected'])
                    self.assertGreaterEqual(len(rows),5)
                    self.assertTrue(any(row.get('angle_deg') is not None for row in rows))
                finally:
                    owner.close()
        finally:
            server.shutdown(); server.server_close()
            SerialOwner._receive, SerialOwner._send, SerialOwner._event, Window._connect=originals

    def test_commands_use_original_owner_and_match_ack_without_duplicates(self):
        from host.adb1.client import SerialOwner
        from host.adb1.app import Window
        from host.adb1.fake_transport import FakeTransport
        from host.adb1_bridge import install
        original=(SerialOwner._receive, SerialOwner._send, SerialOwner._event, Window._connect)
        bridge=ADB1Bridge(); server=bridge.serve(0); install(bridge)
        try:
            with tempfile.TemporaryDirectory() as folder:
                owner=SerialOwner(FakeTransport,folder)
                bridge.attach_owner(owner,simulated=False); owner.start()
                try:
                    deadline=time.monotonic()+3
                    while time.monotonic()<deadline and not owner.snapshot()['authenticated']: time.sleep(.01)
                    self.assertTrue(owner.snapshot()['authenticated'])
                    client=HostClient(server.server_port)
                    for action,opcode in [('start-swing',p.START_SWING),('balance',p.RUN),('stop',p.STOP)]:
                        request={'action':action,'request_id':'test-'+action}
                        validate_action(request,client.status(),'ADB1')
                        client.command(request)
                        deadline=time.monotonic()+3
                        while time.monotonic()<deadline and bridge.status()['requests'][request['request_id']]['state'] not in ('accepted','rejected','indeterminate'): time.sleep(.01)
                        self.assertEqual(bridge.status()['requests'][request['request_id']]['state'],'accepted')
                        bridge.command(request)
                        self.assertEqual(sum(frame[5]==opcode for frame in owner.transport.writes),1)
                    self.assertEqual(owner.transport.called_threads,{owner.transport.owner_thread})
                finally: owner.close()
        finally:
            server.shutdown(); server.server_close()
            SerialOwner._receive, SerialOwner._send, SerialOwner._event, Window._connect=original

    def test_stop_cancels_unsent_web_swing(self):
        from host.adb1.client import SerialOwner
        from host.adb1.app import Window
        from host.adb1_bridge import install
        original=(SerialOwner._receive, SerialOwner._send, SerialOwner._event, Window._connect)
        bridge=ADB1Bridge(); install(bridge)
        try:
            owner=SerialOwner(lambda:None,'.'); owner.state.update(connected=True,authenticated=True)
            owner.last_frame=time.monotonic()
            bridge.attach_owner(owner,simulated=False)
            bridge.command({'action':'start-swing','request_id':'swing'})
            bridge.command({'action':'stop','request_id':'stop'})
            self.assertEqual(bridge.status()['requests']['swing']['state'],'rejected')
            self.assertEqual(bridge.status()['requests']['stop']['state'],'queued')
        finally:
            SerialOwner._receive, SerialOwner._send, SerialOwner._event, Window._connect=original

    def test_lost_ack_has_unknown_result_and_is_not_retried(self):
        from host.adb1.client import SerialOwner
        from host.adb1.app import Window
        from host.adb1.fake_transport import FakeTransport
        from host.adb1_bridge import install
        original=(SerialOwner._receive, SerialOwner._send, SerialOwner._event, Window._connect)
        bridge=ADB1Bridge(); install(bridge)
        try:
            with tempfile.TemporaryDirectory() as folder:
                owner=SerialOwner(lambda:FakeTransport(suppress_opcodes=(p.RUN,)),folder)
                bridge.attach_owner(owner,simulated=False); owner.start()
                try:
                    deadline=time.monotonic()+3
                    while time.monotonic()<deadline and not owner.snapshot()['authenticated']: time.sleep(.01)
                    request={'action':'balance','request_id':'lost'}
                    bridge.command(request)
                    deadline=time.monotonic()+3
                    while time.monotonic()<deadline and bridge.status()['requests']['lost']['state']!='indeterminate': time.sleep(.01)
                    self.assertEqual(bridge.status()['requests']['lost']['state'],'indeterminate')
                    bridge.command(request)
                    self.assertEqual(sum(frame[5]==p.RUN for frame in owner.transport.writes),1)
                finally: owner.close()
        finally:
            SerialOwner._receive, SerialOwner._send, SerialOwner._event, Window._connect=original

    def test_wrong_opcode_ack_never_confirms_the_request(self):
        bridge=ADB1Bridge(); owner=Owner(); bridge.attach_owner(owner,simulated=False)
        bridge.requests['x']={'state':'pending','action':'start-swing'}
        bridge.wire_requests[42]='x'; bridge.wire_opcodes[42]=p.START_SWING
        ack={'kind':'ack','wire_version':p.VERSION,'variant':p.VARIANT,
             'firmware_id':p.FIRMWARE,'request_id':42,'opcode':p.RUN,'status':0}
        bridge.on_ack(owner,ack,True)
        self.assertEqual(bridge.requests['x']['state'],'pending')


if __name__=='__main__': unittest.main()
