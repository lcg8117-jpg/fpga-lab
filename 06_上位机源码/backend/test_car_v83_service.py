import hashlib
import json
from pathlib import Path
import threading
import time
import unittest
try:
    from car_v83_service import CarService, CarError, Session, Engine
    from speed_transport import SimTransport
except ImportError:
    CarService = None

def until(predicate, timeout=3):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate(): return
        time.sleep(.005)
    raise AssertionError('fixture timeout')

class RecordingTransport(SimTransport):
    def __init__(self,device):
        super().__init__()
        self.owner=threading.get_ident()
        self.commands=[]
        self.feedback=True
        self.poll_error=None
        self.arm_hook=None
    @property
    def online(self): return not self.closed
    def write(self,payload):
        assert threading.get_ident()==self.owner
        command=payload.decode().strip()
        self.commands.append(command)
        if command=='ARM' and self.arm_hook: self.arm_hook()
        super().write(payload)
    def read(self,timeout):
        assert threading.get_ident()==self.owner
        if self.poll_error:
            error,self.poll_error=self.poll_error,None
            raise error
        rows=super().read(timeout)
        return rows if self.feedback else []

class V83Tests(unittest.TestCase):
    def make_service(self, factory=RecordingTransport):
        self.transports=[]
        def create(device):
            t=factory(device); self.transports.append(t); return t
        service=CarService(scanner=lambda:[dict(address='offline',name='明确离线fixture',device='fixture')],transport_factory=create)
        self.addCleanup(service.shutdown)
        return service
    def connect(self, service):
        service.scan()
        result=service.connect('offline')
        self.assertTrue(result['identified'])
        self.assertIsInstance(service._session,Session)
        self.assertIsInstance(service._engine,Engine)
        return result['session']
    def test_split_aux_updates_snapshot_without_renewing_main_freshness(self):
        from speed_protocol import encode_frames, FIELDS
        from car_v83_service import ObservedSession
        service=CarService()
        session=ObservedSession(None)
        service._session=session
        service._engine=Engine(session,service._latest)
        row=dict.fromkeys(FIELDS,0)
        row.update(ms=100,servo_compare=750,left_count=123,right_count=456,left_delta5=4,right_delta5=5)
        main,aux=encode_frames(row,3)
        session._feed(main); service._observe()
        state=service.status()
        self.assertFalse(state['telemetry']['encoder_valid'])
        self.assertIsNone(state['telemetry']['left_count'])
        self.assertIsNone(state['telemetry']['left_delta5'])
        received=state['received_at']; mono=service._received
        session._feed(aux); service._observe()
        state=service.status()
        self.assertTrue(state['telemetry']['encoder_valid'])
        self.assertEqual((state['telemetry']['left_count'],state['telemetry']['right_count'],state['telemetry']['left_delta5'],state['telemetry']['right_delta5']),(123,456,4,5))
        self.assertEqual((state['received_at'],service._received),(received,mono))
        row['ms']=101
        session._feed(encode_frames(row,4)[0]); service._observe()
        self.assertFalse(service.status()['telemetry']['encoder_valid'])
        session._feed(aux); service._observe()
        self.assertFalse(service.status()['telemetry']['encoder_valid'])

    def test_brake_upgrades_inflight_stop_before_single_session_rotation(self):
        service=self.make_service(); token=self.connect(service)
        transport=self.transports[0]
        entered=threading.Event(); release=threading.Event()
        original_write=transport.write
        def write(payload):
            if payload.startswith(b'STOP'):
                entered.set(); release.wait(2)
            original_write(payload)
        transport.write=write
        results=[]; errors=[]
        def call(method):
            try: results.append(method(token))
            except Exception as error: errors.append(error)
        stop_thread=threading.Thread(target=call,args=(service.stop,)); stop_thread.start()
        self.assertTrue(entered.wait(2))
        brake_thread=threading.Thread(target=call,args=(service.brake,)); brake_thread.start()
        time.sleep(.03); release.set()
        stop_thread.join(3); brake_thread.join(3)
        self.assertFalse(errors)
        self.assertEqual(len(results),2)
        self.assertIn('BRAKE',transport.commands)
        self.assertEqual(results[0]['session'],results[1]['session'])
        self.assertNotEqual(results[0]['session'],token)
        with self.assertRaises(CarError): service.brake(token)

    def test_brake_preempts_stop_parked_feedback_wait(self):
        service=self.make_service(); token=self.connect(service)
        entered=threading.Event()
        original_wait=service._session.wait_parked
        def wait(floor,timeout=1):
            if service._stop_abort_enabled:
                entered.set()
                return original_wait(99999999,timeout)
            return original_wait(floor,timeout)
        service._session.wait_parked=wait
        results=[]; errors=[]
        def call(method):
            try: results.append(method(token))
            except Exception as error: errors.append(error)
        thread=threading.Thread(target=call,args=(service.stop,)); thread.start()
        self.assertTrue(entered.wait(2))
        started=time.monotonic()
        service.brake(token)
        thread.join(2)
        self.assertFalse(errors)
        self.assertLess(time.monotonic()-started,1)
        self.assertEqual(self.transports[0].commands.count('BRAKE'),1)

    def test_rc83_service_is_lazy(self):
        self.assertIsNotNone(CarService,'缺少RC83服务')
        service=CarService()
        self.assertFalse(service.worker_alive)
        self.assertEqual(service.status()['protocol'],'RC83')
        self.assertIsNone(service.status()['profile'])
    def test_manifest_original_bytes(self):
        folder=Path(__file__).resolve().parents[1]/'car/v83'
        for row in json.loads((folder/'MANIFEST_SHA256.json').read_text(encoding='utf-8-sig')):
            self.assertEqual(hashlib.sha256((folder/row['path']).read_bytes()).hexdigest(),row['sha256'])
    def test_handshake_explicit_selection_and_profile_readback(self):
        service=self.make_service()
        with self.assertRaises(CarError): service.connect('offline')
        token=self.connect(service)
        self.assertEqual(self.transports[0].commands[:3],['STATUS','CAPS','STOP'])
        service.intent(token,1,None,'CENTER')
        result=service.profile(token,1)
        self.assertEqual(result['profile'],1)
        self.assertEqual(result['telemetry']['profile'],1)
        self.assertIn('PERF 1',self.transports[0].commands)
    def test_lease_and_old_sequence_never_renew(self):
        service=self.make_service(); token=self.connect(service)
        service.intent(token,1,None,'CENTER')
        service.intent(token,2,'FWD','CENTER')
        stamp=service._intent.stamp
        with self.assertRaises(CarError): service.intent(token,2,'FWD','CENTER')
        self.assertEqual(service._intent.stamp,stamp)
        time.sleep(.25)
        until(lambda:service.status()['neutral_required'])
        with self.assertRaises(CarError): service.intent(token,3,'FWD','CENTER')
        until(lambda:service._handled_gap==service._gap)
        service.intent(token,4,None,'CENTER')
        stopped=service.stop(token)
        self.assertNotEqual(stopped['session'],token)
        with self.assertRaises(CarError): service.intent(token,5,None,'CENTER')
    def test_unhandled_gap_cannot_be_hidden_by_late_neutral(self):
        service=self.make_service(); token=self.connect(service)
        service.intent(token,1,None,'CENTER')
        service.intent(token,2,'FWD','CENTER')
        with service._lock:
            service._intent=__import__('dataclasses').replace(service._intent,stamp=time.monotonic()-.3)
            with self.assertRaises(CarError): service.intent(token,3,None,'CENTER')
            self.assertFalse(service._latest().moving)
            with self.assertRaises(CarError): service.intent(token,4,'FWD','CENTER')
        until(lambda:service._handled_gap==service._gap)
        service.intent(token,5,None,'CENTER')
    def hold(self,service,token,**params):
        stop=threading.Event()
        seq=[2]
        def run():
            while not stop.is_set():
                try:
                    seq[0]+=1
                    service.intent(token,seq[0],params.get('direction','FWD'),'CENTER',params.get('speed',1024),params.get('route',0))
                except CarError: pass
                stop.wait(.04)
        thread=threading.Thread(target=run); thread.start()
        self.addCleanup(lambda:(stop.set(),thread.join(1)))
        return stop,seq
    def test_manual_boost_once_and_brake_rotates(self):
        service=self.make_service(); token=self.connect(service)
        service.intent(token,1,None,'CENTER'); service.profile(token,1)
        stop,seq=self.hold(service,token)
        until(lambda:service.status()['motion']=='manual')
        until(lambda:service.status()['telemetry']['active'])
        with service._lock:
            seq[0]+=1
            boost_seq=seq[0]
        service.boost(token,boost_seq,2048)
        with service._lock:
            seq[0]+=1
            next_seq=seq[0]
        with self.assertRaises(CarError): service.boost(token,next_seq,2048)
        self.assertEqual(sum(c.startswith('B ') for c in self.transports[0].commands),1)
        result=service.brake(service.status()['session'])
        stop.set()
        self.assertIn('BRAKE',self.transports[0].commands)
        self.assertNotEqual(result['session'],token)
    def test_route_start_update_keep_and_completion_release_gate(self):
        service=self.make_service(); token=self.connect(service)
        service.intent(token,1,None,'CENTER')
        hold,seq=self.hold(service,token,direction=None,route=8)
        until(lambda:any(c.startswith('T ') for c in self.transports[0].commands))
        until(lambda:any(c.startswith('K ') for c in self.transports[0].commands))
        hold.set()
        with service._lock:
            seq[0]+=1
            service.intent(token,seq[0],None,'CENTER',1280,8,25)
        until(lambda:any(c.startswith('U ') for c in self.transports[0].commands))
        # Board completion while input is still held may not start a second T.
        transport=self.transports[0]
        transport._stop(reason=6)
        transport._frame()
        until(lambda:service._engine.blocked)
        with service._lock:
            seq[0]+=1
            service.intent(token,seq[0],None,'CENTER',1280,8)
        time.sleep(.05)
        self.assertEqual(sum(c.startswith('T ') for c in transport.commands),1)

    def test_same_gatt_fault_requires_stop_and_dead_link_does_not_reconnect(self):
        service=self.make_service(); token=self.connect(service)
        service.intent(token,1,None,'CENTER')
        self.transports[0].poll_error=RuntimeError('offline injected fault')
        until(lambda:service.status()['session'] != token)
        until(lambda:not service._session.ready)
        current=service.status()['session']
        self.assertNotEqual(current,token)
        with self.assertRaises(CarError): service.intent(current,2,'FWD','CENTER')
        result=service.stop(current)
        self.assertTrue(result['connected'])
        self.assertIsNone(result['motion'])
        self.assertTrue(result['neutral_required'])
        self.transports[0].closed=True
        until(lambda:not service.status()['connected'])
        self.assertEqual(len(self.transports),1)

    def test_old_hold_identity_rejected(self):
        class OldIdentity(RecordingTransport):
            def write(self,payload):
                if payload.startswith(b'STATUS'):
                    self.pending.append(b'OK STATUS HOLD_V2\r\n')
                    self.commands.append('STATUS')
                else: super().write(payload)
        service=self.make_service(OldIdentity)
        service.scan()
        with self.assertRaises(CarError): service.connect('offline')
        self.assertFalse(service.status()['connected'])
    def test_stale_feedback_not_refreshed_by_status(self):
        service=self.make_service(); self.connect(service)
        self.addCleanup(lambda:setattr(self.transports[0],'feedback',True))
        self.transports[0].feedback=False
        before=service.status()['received_at']
        time.sleep(1.3)
        self.assertFalse(service.status()['feedback_fresh'])
        self.assertEqual(service.status()['received_at'],before)
    def test_queued_old_stop_and_disconnect_cannot_touch_new_session(self):
        from car_v83_service import Request
        service=self.make_service(); token=self.connect(service)
        result=service.stop(token)
        commands=list(self.transports[0].commands)
        # This exercises the same owner dispatch authorization before any BLE operation.
        for action in ('stop','brake','disconnect'):
            with self.assertRaises(CarError): service._handle(Request(action,(token,)))
        self.assertEqual(service.status()['session'],result['session'])
        self.assertEqual(self.transports[0].commands,commands)
        self.assertTrue(service.status()['connected'])

    def test_stop_cancels_arm_before_motion(self):
        service=self.make_service(); token=self.connect(service)
        service.intent(token,1,None,'CENTER')
        entered=threading.Event(); release=threading.Event()
        self.transports[0].arm_hook=lambda:(entered.set(),release.wait(2))
        self.addCleanup(release.set)
        hold,seq=self.hold(service,token)
        self.assertTrue(entered.wait(3))
        results=[]
        worker=threading.Thread(target=lambda:results.append(service.stop(token))); worker.start()
        until(lambda:service._suspended)
        self.assertFalse(service._latest().moving)
        release.set(); worker.join(3); hold.set()
        self.assertFalse(any(c.startswith('D ') for c in self.transports[0].commands))
        self.assertTrue(results)
