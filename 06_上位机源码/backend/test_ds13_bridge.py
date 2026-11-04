import unittest
import struct
from host.ds13_bridge import DS13Bridge

class ACompleteStrokeTests(unittest.TestCase):
    def test_native_window_uses_real_begin_and_cannot_send_a_point_after_release(self):
        import time
        from host.ds13_bridge import load_upstream, install, NativeStrokeProxy
        modules=load_upstream();bridge=DS13Bridge();install(bridge,modules)
        owner=modules['client'].SerialOwner('not-opened','unused');bridge.attach_owner(owner)
        class Serial:
            def __init__(self):self.writes=[]
            def write(self,raw):self.writes.append(raw);return len(raw)
        owner.serial=Serial();owner.state.connected=True;owner.state.authenticated=True
        owner.hello_ok=owner.schema_ok=owner.status_ok=True
        owner.state.last_control={'mode':3,'fault':0};owner.state.last_control_rx=time.monotonic()
        owner.state.config_reads[(253,6)]=1
        held=[True];native=NativeStrokeProxy(bridge,lambda:held[0])
        try:
            native.begin(10)
            _,ticket,opcode,group,payload,source=owner.queue.get_nowait()
            owner._send(opcode,group,payload,ticket,source)
            ack=bytearray(80);ack[:8]=b'FD2A'+bytes([5,opcode,0,5])
            struct.pack_into('<I',ack,8,owner.next_id-1);struct.pack_into('<I',ack,72,0x44533133)
            struct.pack_into('<H',ack,78,modules['protocol'].crc16(ack[:-2]))
            owner._receive(bytes(ack),modules['protocol'].decode(ack))
            deadline=time.monotonic()+.2
            while owner.queue.empty() and time.monotonic()<deadline:time.sleep(.001)
            _,ticket,opcode,group,payload,source=owner.queue.get_nowait()
            self.assertEqual(opcode,modules['protocol'].LIVE_POINT)
            held[0]=False
            owner._send(opcode,group,payload,ticket,source)
            self.assertEqual(len(owner.serial.writes),1)
            self.assertTrue(owner.state.command_results[ticket]['cancelled'])
        finally:
            bridge.cancel_stroke();owner.state.connected=False

    def test_begin_ack_binds_real_job_before_point_send(self):
        import time
        from host.ds13_bridge import load_upstream, install
        modules=load_upstream();bridge=DS13Bridge();install(bridge,modules)
        owner=modules['client'].SerialOwner('not-opened','unused');bridge.attach_owner(owner)
        class Serial:
            def __init__(self):self.writes=[]
            def write(self,raw):self.writes.append(raw);return len(raw)
        owner.serial=Serial();owner.state.connected=True;owner.state.authenticated=True
        owner.hello_ok=owner.schema_ok=owner.status_ok=True
        owner.state.last_control={'mode':3,'fault':0};owner.state.last_control_rx=time.monotonic()
        owner.state.config_reads[(253,6)]=1
        try:
            bridge.command(dict(action='stroke-begin',request_id='r',session=bridge.session,stroke_id='s',seq=1,target=10))
            _,ticket,opcode,group,payload,source=owner.queue.get_nowait()
            self.assertEqual(opcode,modules['protocol'].LIVE_BEGIN)
            owner._send(opcode,group,payload,ticket,source)
            wire=owner.next_id-1
            ack=bytearray(80);ack[:8]=b'FD2A'+bytes([5,opcode,0,5])
            struct.pack_into('<I',ack,8,wire);struct.pack_into('<I',ack,72,0x44533133)
            struct.pack_into('<H',ack,78,modules['protocol'].crc16(ack[:-2]))
            owner._receive(bytes(ack),modules['protocol'].decode(ack))
            deadline=time.monotonic()+.15
            while owner.queue.empty() and time.monotonic()<deadline:time.sleep(.001)
            _,ticket,opcode,group,payload,source=owner.queue.get_nowait()
            self.assertEqual(opcode,modules['protocol'].LIVE_POINT)
            owner._send(opcode,group,payload,ticket,source)
            self.assertEqual(len(owner.serial.writes),2)
        finally:
            bridge.cancel_stroke();owner.state.connected=False

    def test_r2_keeps_streaming_while_point_ack_is_delayed_and_stops_on_release(self):
        import time
        from queue import Empty
        from host.ds13_bridge import load_upstream, install
        modules=load_upstream();bridge=DS13Bridge();install(bridge,modules)
        owner=modules['client'].SerialOwner('not-opened','unused');bridge.attach_owner(owner)
        class Serial:
            def __init__(self):self.writes=[]
            def write(self,raw):self.writes.append(bytes(raw));return len(raw)
        owner.serial=Serial();owner.state.connected=True;owner.state.authenticated=True
        owner.hello_ok=owner.schema_ok=owner.status_ok=True
        owner.state.last_control={'mode':3,'fault':0};owner.state.last_control_rx=time.monotonic()
        owner.state.config_reads[(253,6)]=1
        try:
            bridge.command(dict(action='stroke-begin',request_id='begin',session=bridge.session,stroke_id='stroke',seq=1,target=10))
            _,ticket,opcode,group,payload,source=owner.queue.get_nowait()
            owner._send(opcode,group,payload,ticket,source)
            ack=bytearray(80);ack[:8]=b'FD2A'+bytes([5,opcode,0,5])
            struct.pack_into('<I',ack,8,owner.next_id-1);struct.pack_into('<I',ack,72,0x44533133)
            struct.pack_into('<H',ack,78,modules['protocol'].crc16(ack[:-2]))
            owner._receive(bytes(ack),modules['protocol'].decode(ack))
            start=time.monotonic();next_hold=start;seq=1;point_writes=0
            # No point ACK is delivered in the first 250 ms. The browser lease
            # is renewed, just as it is during a genuine held pointer.
            while time.monotonic()-start<.25:
                now=time.monotonic()
                if now>=next_hold:
                    seq+=1
                    bridge.command(dict(action='stroke-hold',request_id=f'hold-{seq}',session=bridge.session,stroke_id='stroke',seq=seq))
                    next_hold=now+.05
                try: _,ticket,op,group,payload,source=owner.queue.get_nowait()
                except Empty: time.sleep(.002);continue
                owner._send(op,group,payload,ticket,source)
                if op==modules['protocol'].LIVE_POINT:point_writes+=1
            self.assertGreaterEqual(point_writes,6,'a delayed point ACK must not stall the 20 ms stream')
            seq+=1
            bridge.command(dict(action='stroke-end',request_id='end',session=bridge.session,stroke_id='stroke',seq=seq))
            while True:
                try: _,ticket,op,group,payload,source=owner.queue.get_nowait()
                except Empty: break
                self.assertNotEqual(op,modules['protocol'].LIVE_POINT,'release must cancel queued points')
                owner._send(op,group,payload,ticket,source)
        finally:
            bridge.cancel_stroke();owner.state.connected=False

    def test_status_does_not_copy_the_full_drawing_history(self):
        from collections import deque
        from host.ds13_bridge import load_upstream
        class UncopyableHistory(deque):
            def __iter__(self):raise AssertionError('status copied the full history')
        modules=load_upstream();bridge=DS13Bridge()
        owner=modules['client'].SerialOwner('not-opened','unused');bridge.attach_owner(owner)
        owner.state.history=UncopyableHistory([{'control_epoch':1}])
        self.assertNotIn('history',bridge.status()['host'])

class FakeStroke:
    def __init__(self): self.pressed=False; self.target=0; self.sent_points=0; self.message=''; self.error=''; self.ends=0
    def begin(self,target): self.pressed=True; self.target=target
    def move(self,target): self.target=target
    def end(self,*args): self.pressed=False; self.ends+=1

class DS13Tests(unittest.TestCase):
    def setUp(self):
        self.now=10.; self.bridge=DS13Bridge(clock=lambda:self.now); self.bridge.stroke=FakeStroke()
        self.bridge.owner=object(); self.s=self.bridge.session
        self.addCleanup(self.bridge.cancel_stroke)
    def call(self, action, seq, stroke='a', target=None, session=None):
        body=dict(action=action,request_id=f'{stroke}-{seq}',session=session or self.s,stroke_id=stroke,seq=seq)
        if target is not None: body['target']=target
        return self.bridge.command(body)
    def test_expiry_is_latched_and_old_packets_cannot_revive(self):
        self.call('stroke-begin',1,target=20)
        self.now+=.21
        self.assertFalse(self.bridge.is_held())
        with self.assertRaises(ValueError): self.call('stroke-hold',2)
        self.assertFalse(self.bridge.is_held())
    def test_order_session_and_bounds(self):
        self.call('stroke-begin',1,target=0)
        with self.assertRaises(ValueError): self.call('stroke-move',1,target=10)
        with self.assertRaises(ValueError): self.call('stroke-move',2,target=193)
        with self.assertRaises(ValueError): self.call('stroke-hold',2,session='old')
        self.call('stroke-hold',2)
        self.call('stroke-end',3)
        with self.assertRaises(ValueError): self.call('stroke-begin',4,target=1)
    def test_stale_end_cannot_cancel_new_stroke(self):
        self.call('stroke-begin',1,target=0)
        self.call('stroke-end',2)
        self.call('stroke-begin',1,stroke='b',target=3)
        with self.assertRaises(ValueError): self.call('stroke-end',3)
        self.assertTrue(self.bridge.is_held())

    def test_no_auto_renew_and_status(self):
        self.call('stroke-begin',1,target=-192)
        self.now+=.1; self.call('stroke-hold',2)
        self.now+=.11; self.assertTrue(self.bridge.is_held())
        self.now+=.1; self.assertFalse(self.bridge.is_held())

    def test_web_lease_cannot_keep_a_stopped_host_stroke_alive(self):
        self.call('stroke-begin',1,target=0)
        self.bridge.stroke.pressed=False
        self.assertFalse(self.bridge.is_held())
        with self.assertRaises(ValueError):self.call('stroke-hold',2)

    def test_unstarted_and_ended_web_strokes_have_distinct_error_codes(self):
        with self.assertRaises(ValueError) as early:self.call('stroke-hold',2,stroke='early')
        self.assertEqual(getattr(early.exception,'code',None),'stroke_not_started')
        self.call('stroke-begin',1,stroke='done',target=0)
        self.call('stroke-end',2,stroke='done')
        with self.assertRaises(ValueError) as late:self.call('stroke-hold',3,stroke='done')
        self.assertEqual(getattr(late.exception,'code',None),'stroke_ended')

    def test_idle_drawing_watchdog_stops_after_a_stroke(self):
        import time
        self.call('stroke-begin',1,target=0);self.assertTrue(self.bridge._watchdog_started)
        self.call('stroke-end',2)
        deadline=time.monotonic()+.3
        while self.bridge._watchdog_started and time.monotonic()<deadline:time.sleep(.005)
        self.assertFalse(self.bridge._watchdog_started)

    def test_end_before_delayed_begin_permanently_closes_its_id(self):
        self.call('stroke-end',2,stroke='late')
        with self.assertRaises(ValueError): self.call('stroke-begin',1,stroke='late',target=1)
        self.assertFalse(self.bridge.is_held())

    def test_old_browser_packet_cannot_extend_a_stroke_after_input_stops(self):
        wall=[1000.0]; self.bridge.wall_clock=lambda:wall[0]
        begin=dict(action='stroke-begin',request_id='r',session=self.s,stroke_id='timed',seq=1,target=0,issued_ms=1000000)
        self.bridge.command(begin)
        wall[0]+=.21;self.now+=.21
        with self.assertRaises(ValueError):
            self.bridge.command(dict(action='stroke-move',request_id='old',session=self.s,stroke_id='timed',seq=2,target=5,issued_ms=1000000))
        self.assertFalse(self.bridge.is_held())

    def test_delay_consumes_the_original_200ms_input_lease(self):
        wall=[1000.15];self.bridge.wall_clock=lambda:wall[0]
        self.bridge.command(dict(action='stroke-begin',request_id='r',session=self.s,stroke_id='timed',seq=1,target=0,issued_ms=1000000))
        self.now+=.04;self.assertTrue(self.bridge.is_held())
        self.now+=.02;self.assertFalse(self.bridge.is_held())

    def test_native_and_browser_strokes_exclude_each_other(self):
        from host.ds13_bridge import NativeStrokeProxy
        mouse=[True];native=NativeStrokeProxy(self.bridge,lambda:mouse[0])
        native.begin(20);self.assertTrue(native.pressed);self.assertTrue(self.bridge.is_held())
        native.move(30);self.assertEqual(self.bridge.stroke.target,30)
        with self.assertRaises(ValueError):self.call('stroke-begin',1,target=0)
        native.end();self.assertFalse(native.pressed)
        self.call('stroke-begin',1,stroke='web',target=2)
        with self.assertRaises(ValueError):native.begin(10)
        native.end();self.assertTrue(self.bridge.is_held())
        self.call('stroke-end',2,stroke='web')

    def test_native_mouse_release_revokes_the_shared_send_gate(self):
        from host.ds13_bridge import NativeStrokeProxy
        mouse=[True];native=NativeStrokeProxy(self.bridge,lambda:mouse[0]);native.begin(0)
        mouse[0]=False
        self.assertFalse(self.bridge.is_held())
        self.bridge.cancel_stroke(expired_only=True)
        self.assertIsNone(self.bridge.active_id)

class OriginalFrameTests(unittest.TestCase):
    def test_authenticated_idle_status_is_fresh_before_first_control_transaction(self):
        import time
        from host.ds13_bridge import DS13Bridge, load_upstream
        m=load_upstream(); bridge=DS13Bridge()
        owner=m['client'].SerialOwner('not-opened','unused'); bridge.attach_owner(owner)
        owner.state.connected=True; owner.state.authenticated=True; owner.last_rx=time.monotonic()
        status=bridge.status()
        self.assertLess(status['status_age_s'],.5)
        self.assertIsNone(status['control_age_s'])

    def test_begin_requires_real_direction_readback(self):
        import time
        from host.ds13_bridge import DS13Bridge, load_upstream
        m=load_upstream(); bridge=DS13Bridge()
        owner=m['client'].SerialOwner('not-opened','unused'); bridge.attach_owner(owner)
        owner.state.connected=True; owner.state.authenticated=True
        owner.state.last_control={'mode':3,'fault':0}; owner.state.last_control_rx=time.monotonic()
        with self.assertRaisesRegex(ValueError,'方向'):
            bridge.command(dict(action='stroke-begin',request_id='r',session=bridge.session,stroke_id='s',seq=1,target=0))
        self.assertFalse(bridge.is_held())
        self.assertTrue(owner.queue.empty())

    def test_lost_begin_ack_and_lease_expiry_stop_the_worker(self):
        import time
        from host.ds13_bridge import DS13Bridge, load_upstream
        m=load_upstream(); now=[10.0]; bridge=DS13Bridge(clock=lambda:now[0])
        owner=m['client'].SerialOwner('not-opened','unused'); bridge.attach_owner(owner)
        owner.state.connected=True; owner.state.authenticated=True
        owner.state.last_control={'mode':3,'fault':0}; owner.state.last_control_rx=time.monotonic()
        owner.state.config_reads[(253,6)]=1
        bridge.command(dict(action='stroke-begin',request_id='r',session=bridge.session,stroke_id='s',seq=1,target=10))
        now[0]+=.21
        deadline=time.monotonic()+.5
        while bridge.stroke.pressed and time.monotonic()<deadline: time.sleep(.01)
        self.assertFalse(bridge.stroke.pressed)
        self.assertFalse(bridge.is_held())
        queued=list(owner.queue.queue)
        self.assertFalse(any(row[2]==m['protocol'].LIVE_POINT for row in queued))
        with self.assertRaises(ValueError):
            bridge.command(dict(action='stroke-hold',request_id='late',session=bridge.session,stroke_id='s',seq=2))

    def test_only_authenticated_accepted_raw_frame_enters_waveform(self):
        from host.ds13_bridge import DS13Bridge, load_upstream, install
        m=load_upstream(); bridge=DS13Bridge(); install(bridge,m)
        owner=m['client'].SerialOwner('not-opened','unused')
        bridge.attach_owner(owner)
        owner.state.connected=True
        frame=bytearray(256); frame[:4]=b'FD2T'; frame[4:8]=bytes((5,5,1,3))
        struct.pack_into('<H',frame,52,12345)
        struct.pack_into('<IIII',frame,8,1,5,1,1)
        struct.pack_into('<II',frame,32,0x44533133,0x44535002)
        struct.pack_into('<H',frame,254,m['protocol'].crc16(frame[:254]))
        raw=bytes(frame); item=m['protocol'].decode(raw)
        owner._receive(raw,item)
        self.assertEqual(bridge.waveform()['samples'],[])
        owner.hello_ok=True; owner.status_ok=True
        struct.pack_into('<II',frame,8,2,10); struct.pack_into('<I',frame,16,2)
        struct.pack_into('<H',frame,254,m['protocol'].crc16(frame[:254]))
        raw=bytes(frame); item=m['protocol'].decode(raw); owner._receive(raw,item)
        sample=bridge.waveform()['samples'][0]['frame']
        self.assertEqual((sample['firmware_id'],sample['schema'],sample['wire_version']), (0x44533133,0x44535002,5))
        self.assertEqual(sample['quiet_q4'],12345)
        self.assertNotIn('pendulum_pose_deg',sample)

if __name__ == '__main__': unittest.main()

class ConcurrencyTests(unittest.TestCase):
    def test_r2_bridge_and_serial_owner_stream_with_delayed_point_acks(self):
        import importlib.util,sys,time
        from contextlib import nullcontext
        from host.ds13_bridge import DS13Bridge,ROOT,install,load_upstream
        m=load_upstream();bridge=DS13Bridge();install(bridge,m)
        # Load the delivered fake transport against exactly the same protocol
        # and owner modules as the bridge. It never opens a physical port.
        names=('client','protocol','live_control','timeline')
        saved={name:sys.modules.get(name) for name in names}
        try:
            for name in names:sys.modules[name]=m[name]
            spec=importlib.util.spec_from_file_location('ds13_r2_fake_transport',ROOT.parent/'tests'/'test_host.py')
            fake_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(fake_module)
        finally:
            for name in names:
                if saved[name] is None:sys.modules.pop(name,None)
                else:sys.modules[name]=saved[name]
        class SlowPointACK(fake_module.DelayedSerial):
            def write(self,raw):
                self.delay=.25 if raw[5]==m['protocol'].LIVE_POINT else .02
                return super().write(raw)
        fake=SlowPointACK(.02)
        owner=m['client'].SerialOwner('FAKE','unused',serial_factory=lambda *a:fake,
            lease_factory=nullcontext,logger_factory=fake_module.QuietLogger)
        bridge.attach_owner(owner);owner.start()
        try:
            deadline=time.monotonic()+2
            while time.monotonic()<deadline and not (owner.state.authenticated and (253,6) in owner.state.config_reads):time.sleep(.005)
            self.assertTrue(owner.state.authenticated);self.assertIn((253,6),owner.state.config_reads)
            bridge.command(dict(action='stroke-begin',request_id='begin',session=bridge.session,stroke_id='stroke',seq=1,target=10))
            start=time.monotonic();next_hold=start;seq=1
            while time.monotonic()-start<.65:
                now=time.monotonic()
                if now>=next_hold:
                    seq+=1;bridge.command(dict(action='stroke-hold',request_id=f'hold-{seq}',session=bridge.session,stroke_id='stroke',seq=seq));next_hold=now+.05
                time.sleep(.003)
            points=[raw for raw in fake.writes if raw[5]==m['protocol'].LIVE_POINT]
            self.assertGreaterEqual(len(points),15)
            self.assertEqual(fake.lease_expired,0);self.assertFalse(owner.state.error)
            seq+=1;bridge.command(dict(action='stroke-end',request_id='end',session=bridge.session,stroke_id='stroke',seq=seq))
            deadline=time.monotonic()+.5
            while time.monotonic()<deadline and not any(raw[5]==m['protocol'].LIVE_END for raw in fake.writes):time.sleep(.005)
            end=next(i for i,raw in enumerate(fake.writes) if raw[5]==m['protocol'].LIVE_END)
            time.sleep(.08)
            self.assertFalse(any(raw[5]==m['protocol'].LIVE_POINT for raw in fake.writes[end+1:]))
        finally:
            bridge.cancel_stroke();owner.stop(wait=True)

    def test_async_cancel_finishes_before_next_stroke_can_begin(self):
        import threading
        bridge=DS13Bridge(); entered=threading.Event(); release=threading.Event()
        class SlowEnd(FakeStroke):
            def end(self,*args):
                entered.set();release.wait(1);super().end(*args)
        bridge.owner=object();bridge.stroke=SlowEnd();session=bridge.session
        bridge.command(dict(action='stroke-begin',request_id='a',session=session,stroke_id='old',seq=1,target=0))
        bridge.cancel_stroke();self.assertTrue(entered.wait(1))
        try:
            with self.assertRaises(ValueError):
                bridge.command(dict(action='stroke-begin',request_id='b',session=session,stroke_id='new',seq=1,target=1))
        finally:
            release.set()

    def test_expired_pointer_cannot_be_written_to_serial(self):
        from host.ds13_bridge import DS13Bridge, load_upstream, install
        modules=load_upstream(); bridge=DS13Bridge(); install(bridge,modules)
        owner=modules['client'].SerialOwner('not-opened','unused'); bridge.attach_owner(owner)
        class Serial:
            def __init__(self): self.writes=[]
            def write(self,raw): self.writes.append(raw); return len(raw)
        owner.serial=Serial()
        owner._send(modules['protocol'].LIVE_POINT, payload=struct.pack('<Ii',1,0), ticket=88, source='LIVE_POINTER')
        self.assertEqual(owner.serial.writes,[])
        self.assertTrue(owner.state.command_results[88]['cancelled'])
        bridge.active_id='new-stroke';bridge.deadline=bridge.clock()+1;bridge.stroke.pressed=True
        bridge._job_bindings[1]=(bridge.session,'old-stroke')
        owner._send(modules['protocol'].LIVE_POINT, payload=struct.pack('<Ii',1,0), ticket=89, source='LIVE_POINTER')
        self.assertEqual(owner.serial.writes,[])
        self.assertTrue(owner.state.command_results[89]['cancelled'])
        bridge._job_bindings[2]=(bridge.session,'new-stroke')
        owner._send(modules['protocol'].LIVE_POINT, payload=struct.pack('<Ii',2,0), ticket=90, source='LIVE_POINTER')
        self.assertEqual(len(owner.serial.writes),1)

    def test_bridge_does_not_hold_owner_state_lock_across_command(self):
        import threading
        class State:
            lock=threading.RLock()
        class Owner:
            state=State()
            pending={}
            def command(self,*args,**kwargs):
                if self.state.lock._is_owned(): raise RuntimeError('state lock held across command')
                return 7
        bridge=DS13Bridge(); bridge.owner=Owner()
        result=bridge.command(dict(action='balance',request_id='lock-test'))
        self.assertNotEqual(result['state'],'rejected')

    def test_cancel_invalidates_while_begin_is_blocked(self):
        import threading
        bridge=DS13Bridge(); entered=threading.Event(); release=threading.Event()
        class Blocked(FakeStroke):
            def begin(self,target):
                entered.set(); release.wait(2); super().begin(target)
        bridge.owner=object(); bridge.stroke=Blocked(); session=bridge.session
        errors=[]
        def begin():
            try: bridge.command(dict(action='stroke-begin',request_id='a',session=session,stroke_id='s',seq=1,target=0))
            except ValueError as exc: errors.append(str(exc))
        t=threading.Thread(target=begin,daemon=True)
        t.start(); self.assertTrue(entered.wait(1))
        cancel=threading.Thread(target=bridge.cancel_stroke,daemon=True); cancel.start(); cancel.join(.1)
        try:
            self.assertFalse(cancel.is_alive(), 'cancel blocked by pending begin')
            self.assertFalse(bridge.is_held())
        finally:
            release.set(); t.join(1); cancel.join(1)
        self.assertTrue(errors)
        self.assertFalse(bridge.stroke.pressed)
