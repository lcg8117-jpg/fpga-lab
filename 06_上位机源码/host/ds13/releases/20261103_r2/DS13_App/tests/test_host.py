"""Offline live protocol, cancellation, actual-wire and plotting tests."""
from pathlib import Path
import sys,struct,threading,time,unittest,json,heapq
from contextlib import nullcontext,ExitStack
from unittest import mock
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
import protocol as p
from client import SerialOwner,HostState
from timeline import Timeline
from live_control import LiveStroke
def seal(b):struct.pack_into('<H',b,len(b)-2,p.crc16(b[:-2]));return bytes(b)
def telemetry(stamp=1000,job=17,state=6,elapsed=100,encoder=15,reference=30,origin=5,sequence=1,point_sequence=0,mode=3,fault=0):
    b=bytearray(256);b[:4]=b'FD2T';b[4:8]=bytes((5,5,5,mode));b[40]=fault
    for o,v in [(8,sequence),(12,stamp),(16,stamp),(28,point_sequence),(32,p.FIRMWARE_ID),(36,p.SCHEMA),(184,elapsed),(188,job)]:struct.pack_into('<I',b,o,v)
    struct.pack_into('<q',b,72,encoder);struct.pack_into('<q',b,80,reference);struct.pack_into('<q',b,176,origin)
    struct.pack_into('<i',b,64,encoder);struct.pack_into('<h',b,192,30);b[194]=state
    struct.pack_into('<6I',b,128,*p.S1_BITS);struct.pack_into('<6I',b,152,*p.S1_BITS);b[248:254]=bytes((1,5,5,25,1,64))
    return seal(b)
def ack(raw,read=0,status=0,identity=p.FIRMWARE_ID):
    b=bytearray(80);b[:4]=b'FD2A';b[4:8]=bytes((5,raw[5],status,5));b[8:12]=raw[8:12]
    struct.pack_into('<6I',b,24,*p.S1_BITS);struct.pack_into('<I',b,48,read)
    b[56:60]=bytes((raw[6],raw[12],3,0));struct.pack_into('<I',b,60,4);struct.pack_into('<I',b,72,identity)
    return seal(b)
class QuietLogger:
    def __init__(self,*args):self.error='';self.events=[]
    def start(self):pass
    def submit(self,item,raw=b''):self.events.append(item)
    def close(self):pass
class FakeSerial:
    def __init__(self,identity=p.FIRMWARE_ID):self.data=bytearray();self.writes=[];self.identity=identity;self.stamp=1000;self.seq=0;self.due=0;self.silent=False
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def write(self,raw):
        self.writes.append(raw)
        if not self.silent:self.data.extend(ack(raw,1 if raw[6]==253 and raw[12]==6 else 0,identity=self.identity))
        return len(raw)
    def read(self,n):
        if self.silent:time.sleep(.001);return b''
        if time.monotonic()>=self.due:
            self.stamp+=5;self.seq+=1;self.data.extend(telemetry(stamp=self.stamp,sequence=self.seq));self.due=time.monotonic()+.005
        result=bytes(self.data[:n]);del self.data[:n]
        if not result:time.sleep(.001)
        return result

class DelayedSerial(FakeSerial):
    """Offline wire: 200Hz telemetry, delayed ACKs and a 200ms point lease."""
    def __init__(self,delay=.125):
        super().__init__();self.delay=delay;self.scheduled=[];self.write_times={};self.ack_times={}
        self.job=0;self.phase=0;self.point_sequence=0;self.target=0;self.mode=3
        self.last_point=0.;self.lease_expired=0;self.reject={};self.drop_acks=set()
        self.due=time.monotonic()
    def write(self,raw):
        now=time.monotonic();self.writes.append(raw);rid=struct.unpack_from('<I',raw,8)[0]
        self.write_times[rid]=now
        if self.silent:return len(raw)
        op=raw[5];status=self.reject.get(op,0)
        if not status:
            if op==p.LIVE_BEGIN:
                self.job=rid;self.phase=6;self.point_sequence=0;self.last_point=now
            elif op==p.LIVE_POINT:
                if self.phase!=6 or struct.unpack_from('<I',raw,12)[0]!=self.job:status=1
                else:self.point_sequence=rid;self.target=struct.unpack_from('<i',raw,16)[0];self.last_point=now
            elif op in (p.LIVE_END,p.CANCEL_HOLD):self.phase=3
            elif op in (p.STOP,p.ESTOP):self.phase=0;self.mode=0
        if op not in self.drop_acks:
            heapq.heappush(self.scheduled,(now+self.delay,rid,ack(raw,1 if raw[6]==253 and raw[12]==6 else 0,status=status)))
        return len(raw)
    def read(self,n):
        # Match pyserial's 5ms read window, including continuously arriving frames.
        deadline=time.monotonic()+.005
        while True:
            now=time.monotonic()
            if not self.silent:
                if self.phase==6 and now-self.last_point>.2:self.phase=3;self.lease_expired+=1
                while self.scheduled and self.scheduled[0][0]<=now:
                    _,rid,raw=heapq.heappop(self.scheduled);self.data.extend(raw);self.ack_times[rid]=now
                while self.due<=now:
                    self.stamp+=5;self.seq+=1;self.due+=.005
                    self.data.extend(telemetry(stamp=self.stamp,sequence=self.seq,job=self.job,state=self.phase,
                        point_sequence=self.point_sequence,reference=self.target,mode=self.mode))
            if len(self.data)>=n or now>=deadline:break
            time.sleep(.001)
        if self.silent:return b''
        result=bytes(self.data[:n]);del self.data[:n];return result

class Tests(unittest.TestCase):
    def wait_for(self,predicate,timeout=1):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.002)
        self.assertTrue(predicate(),'condition not reached before test deadline')
    def owner(self,fake):
        obj=SerialOwner('FAKE','unused',serial_factory=lambda *a:fake,lease_factory=nullcontext,logger_factory=QuietLogger).start()
        due=time.monotonic()+1
        while time.monotonic()<due:
            if obj.state.authenticated and (253,6) in obj.state.config_reads:return obj
            time.sleep(.005)
        obj.stop(wait=True);self.fail('fake owner never ready')
    def test_codec_identity_and_no_old_upload(self):
        self.assertEqual(p.FIRMWARE_ID,0x44533133);self.assertEqual(p.SCHEMA,0x44535002)
        parser=p.FrameParser();raw=telemetry();rows=[]
        for i in range(0,len(raw),7):rows.extend(parser.feed(raw[i:i+7]))
        self.assertEqual(len(rows),1);self.assertTrue(rows[0][1]['identity_ok']);self.assertEqual(rows[0][1]['active_bits'],p.S1_BITS)
        corrupt=bytearray(raw);corrupt[72]^=1;self.assertFalse(parser.feed(corrupt));self.assertGreater(parser.errors,0)
        wrong=bytearray(raw);struct.pack_into('<I',wrong,32,0x44533131);self.assertFalse(p.decode(seal(wrong))['identity_ok'])
        for opcode in (0x22,0x23,0x30,0x31,0x40,0x41,0x42,0x43,0x44,0x45,0x46):
            with self.assertRaises(ValueError):p.request(opcode,1,session=1,event=1)
        with self.assertRaises(ValueError):p.request(p.WRITE_CONFIG,1,252,b'\0'*5,session=1,event=1)
        with self.assertRaises(ValueError):p.request(p.LIVE_POINT,1,payload=struct.pack('<Ii',7,193),session=1,event=1)
        with self.assertRaises(ValueError):p.gains_payload((53.78,1,210.14,17.67,0,616.61))
        self.assertEqual(struct.unpack('<6I',p.gains_payload(p.DEFAULT_GAINS)),p.S1_BITS)
    def test_time_alignment_retains_real_lag_and_gaps(self):
        a=Timeline();b=Timeline()
        for j in range(20):
            r=p.decode(telemetry(stamp=1000+j*5,elapsed=j*5,origin=0,encoder=j,reference=j+3,sequence=j))
            a.ingest(r,j*.005);b.ingest(r,900+j*.017)
        self.assertEqual([(r['t'],r['actual_deg']) for r in a.rows],[(r['t'],r['actual_deg']) for r in b.rows])
        self.assertAlmostEqual(a.rows[-1]['reference_deg']-a.rows[-1]['actual_deg'],3*360/1040)
        a.ingest(p.decode(telemetry(stamp=1120,elapsed=120,origin=0)),4);self.assertTrue(a.rows[-1]['gap'])
        a.ingest(p.decode(telemetry(stamp=1125,elapsed=125,origin=0,state=3)),5);self.assertEqual(a.job_id,17)
        a.ingest(p.decode(telemetry(stamp=1130,elapsed=130,origin=0,state=0)),6);self.assertFalse(a.tracking)
        size=len(a.rows);a.ingest(p.decode(telemetry(stamp=1130,elapsed=130,origin=0,state=0)),7);self.assertEqual(len(a.rows),size)
        a.ingest(p.decode(telemetry(stamp=1200,elapsed=5,origin=0,state=6,job=18)),10);self.assertEqual(len(a.rows),1)
    def test_release_removes_queued_old_points(self):
        obj=SerialOwner('FAKE','unused',logger_factory=QuietLogger)
        obj.state.connected=obj.state.authenticated=True;obj.state.last_control=p.decode(telemetry());obj.state.last_control_rx=time.monotonic()
        first=obj.command(p.LIVE_BEGIN);point=obj.command(p.LIVE_POINT,payload=struct.pack('<Ii',4,50))
        obj.command(p.LIVE_END,payload=struct.pack('<I',4))
        self.assertTrue(obj.state.command_results[first]['cancelled']);self.assertTrue(obj.state.command_results[point]['cancelled'])
        self.assertEqual(obj.queue.get_nowait()[2],p.LIVE_END);self.assertTrue(obj.queue.empty())
    def test_live_points_exist_before_mouse_release(self):
        fake=FakeSerial();owner=self.owner(fake);stroke=LiveStroke(lambda:owner)
        try:
            stroke.begin(10)
            for i in range(100):stroke.move(i)
            time.sleep(.14)
            points=[r for r in fake.writes if r[5]==p.LIVE_POINT]
            self.assertTrue(stroke.pressed);self.assertGreater(len(points),2);self.assertLess(len(points),15)
            self.assertEqual(struct.unpack_from('<i',points[-1],16)[0],99)
            self.assertFalse(any(r[5] in (p.ZERO,p.RUN,p.SET_GAINS,p.WRITE_CONFIG,0x46) for r in fake.writes))
            stroke.end();time.sleep(.06)
            sent=[r[5] for r in fake.writes];self.assertIn(p.LIVE_END,sent)
            end_index=sent.index(p.LIVE_END);self.assertNotIn(p.LIVE_POINT,sent[end_index+1:])
        finally:stroke.end();owner.stop(wait=True)
    def test_release_before_begin_ack_has_no_late_motion(self):
        fake=FakeSerial();owner=self.owner(fake)
        original=fake.write
        def slow_begin(raw):
            if raw[5]==p.LIVE_BEGIN:
                fake.writes.append(raw);return len(raw)
            return original(raw)
        fake.write=slow_begin;stroke=LiveStroke(lambda:owner)
        try:
            stroke.begin(80);time.sleep(.015);stroke.end();time.sleep(.06)
            self.assertFalse(any(r[5]==p.LIVE_POINT for r in fake.writes))
            self.assertTrue(any(r[5]==p.CANCEL_HOLD for r in fake.writes))
        finally:owner.stop(wait=True)
    def test_missing_point_ack_is_not_replayed(self):
        fake=FakeSerial();owner=self.owner(fake);original=fake.write
        def lose_point(raw):
            if raw[5]==p.LIVE_POINT:fake.writes.append(raw);return len(raw)
            return original(raw)
        fake.write=lose_point;stroke=LiveStroke(lambda:owner)
        try:
            started=time.monotonic();stroke.begin(20)
            self.wait_for(lambda:not stroke.pressed,1.5)
            self.assertGreaterEqual(time.monotonic()-started,.95)
            self.assertFalse(stroke.pressed);self.assertTrue(stroke.error)
            self.assertIn('结果未知',stroke.error)
            points=[r for r in fake.writes if r[5]==p.LIVE_POINT]
            self.assertGreater(len(points),1)
            self.assertEqual(len(points),len({struct.unpack_from('<I',r,8)[0] for r in points}))
            self.assertTrue(any(r[5]==p.STOP for r in fake.writes))
        finally:stroke.end();owner.stop(wait=True)
    def test_button_release_without_tk_callback_ends_stroke(self):
        fake=FakeSerial();owner=self.owner(fake);held=[True];stroke=LiveStroke(lambda:owner,lambda:held[0])
        try:
            stroke.begin(20);time.sleep(.06);held[0]=False;time.sleep(.06)
            self.assertFalse(stroke.pressed);self.assertTrue(any(r[5]==p.LIVE_END for r in fake.writes))
        finally:stroke.end();owner.stop(wait=True)
    def test_connect_read_only_identity_lock(self):
        for wrong in (False,True):
            fake=FakeSerial(0x44533131 if wrong else p.FIRMWARE_ID)
            owner=SerialOwner('FAKE','unused',serial_factory=lambda *a:fake,lease_factory=nullcontext,logger_factory=QuietLogger).start()
            try:
                time.sleep(.2);self.assertEqual(owner.state.authenticated,not wrong)
                self.assertTrue(all(r[5] in p.READS for r in fake.writes))
                if wrong:
                    with self.assertRaises(RuntimeError):owner.command(p.LIVE_BEGIN)
            finally:owner.stop(wait=True)
    def test_delayed_ack_mouse_and_heartbeat_do_not_block_each_other(self):
        for delay in (.062,.070,.094,.125):
            with self.subTest(ack_delay=delay):
                fake=DelayedSerial(delay);owner=self.owner(fake);stroke=LiveStroke(lambda:owner)
                try:
                    start=len(fake.writes);owner.command(p.STATUS,source='HEARTBEAT_FIRST')
                    self.wait_for(lambda:len(fake.writes)>start)
                    heartbeat=next(r for r in fake.writes[start:] if r[5]==p.STATUS)
                    heartbeat_id=struct.unpack_from('<I',heartbeat,8)[0]
                    stroke.begin(10);started=time.monotonic()
                    while time.monotonic()-started<.95:
                        stroke.move(int((time.monotonic()-started)*100)%150);time.sleep(.005)
                    live=[r for r in fake.writes[start:] if r[5]==p.LIVE_BEGIN]
                    points=[r for r in fake.writes[start:] if r[5]==p.LIVE_POINT]
                    self.assertEqual(len(live),1);self.assertGreaterEqual(len(points),5)
                    begin_id=struct.unpack_from('<I',live[0],8)[0]
                    self.assertLess(fake.write_times[begin_id],fake.ack_times[heartbeat_id])
                    self.assertTrue(stroke.pressed,stroke.error);self.assertEqual(stroke.error,'');self.assertEqual(owner.state.error,'')
                    self.assertEqual(fake.lease_expired,0);self.assertGreaterEqual(fake.seq,180)
                    times=[fake.write_times[struct.unpack_from('<I',r,8)[0]] for r in points]
                    self.assertLess(max(b-a for a,b in zip(times,times[1:])),.2)
                    beats=[fake.write_times[struct.unpack_from('<I',r,8)[0]] for r in fake.writes[start:] if r[5]==p.STATUS]
                    self.assertGreaterEqual(len(beats),9);self.assertLess(max(b-a for a,b in zip(beats,beats[1:])),.15)
                    self.assertGreater(struct.unpack_from('<i',points[-1],16)[0],30)
                    released=time.monotonic();stroke.end();self.assertLess(time.monotonic()-released,.1)
                    self.wait_for(lambda:any(r[5]==p.LIVE_END for r in fake.writes[start:]),.1)
                    end_index=next(i for i in range(start,len(fake.writes)) if fake.writes[i][5]==p.LIVE_END)
                    time.sleep(delay+.025)
                    self.assertNotIn(p.LIVE_POINT,[r[5] for r in fake.writes[end_index+1:]])
                    self.assertEqual(sum(r[5]==p.LIVE_END for r in fake.writes[start:]),1)
                    self.assertTrue(all(r[5] in p.READS|p.LIVE_COMMANDS for r in fake.writes))
                    print(f'ACK {delay*1000:.0f}ms: points={len(points)}, max_point_gap={(max(b-a for a,b in zip(times,times[1:])))*1000:.1f}ms, heartbeat_max_gap={(max(b-a for a,b in zip(beats,beats[1:])))*1000:.1f}ms')
                finally:stroke.end();owner.stop(wait=True)
    def test_unsent_begin_has_no_120ms_deadline_and_release_purges_it(self):
        owner=SerialOwner('FAKE','unused',logger_factory=QuietLogger)
        owner.state.connected=owner.state.authenticated=True;owner.state.last_control=p.decode(telemetry())
        owner.state.last_control_rx=time.monotonic();owner.state.config_reads[(253,6)]=1
        stroke=LiveStroke(lambda:owner)
        try:
            stroke.begin(50);time.sleep(.18)
            self.assertTrue(stroke.pressed);self.assertEqual(stroke.error,'');self.assertFalse(owner.pending)
            stroke.end();self.assertFalse(stroke.thread.is_alive())
            self.assertEqual(owner.queue.get_nowait()[2],p.CANCEL_HOLD);self.assertTrue(owner.queue.empty())
        finally:stroke.end()
    def test_release_during_delayed_begin_ack_and_rapid_strokes(self):
        fake=DelayedSerial();owner=self.owner(fake);stroke=LiveStroke(lambda:owner)
        try:
            stroke.begin(80);self.wait_for(lambda:any(r[5]==p.LIVE_BEGIN for r in fake.writes))
            stroke.end();self.assertFalse(stroke.thread.is_alive())
            self.wait_for(lambda:any(r[5]==p.CANCEL_HOLD for r in fake.writes),.1)
            first_end=len(fake.writes)
            for target in (25,-40,70):
                stroke.begin(target)
                self.wait_for(lambda:stroke.job is not None and stroke.sent_points>=1)
                job=stroke.job;released=time.monotonic();stroke.end()
                self.assertLess(time.monotonic()-released,.1);self.assertEqual(stroke.error,'')
                self.wait_for(lambda:any(r[5]==p.LIVE_END and struct.unpack_from('<I',r,12)[0]==job for r in fake.writes),.1)
            time.sleep(.17)
            self.assertFalse(any(r[5]==p.LIVE_POINT for r in fake.writes[:first_end]))
            jobs=[struct.unpack_from('<I',r,8)[0] for r in fake.writes if r[5]==p.LIVE_BEGIN]
            self.assertEqual(len(jobs),4);self.assertEqual(len(set(jobs)),4)
            closed=set()
            for r in fake.writes:
                if r[5]==p.LIVE_END:closed.add(struct.unpack_from('<I',r,12)[0])
                if r[5]==p.LIVE_POINT:self.assertNotIn(struct.unpack_from('<I',r,12)[0],closed)
            self.assertEqual(fake.lease_expired,0);self.assertEqual(owner.state.error,'')
        finally:stroke.end();owner.stop(wait=True)
    def test_physical_release_is_checked_while_waiting_for_ack(self):
        fake=DelayedSerial();owner=self.owner(fake);held=[True];stroke=LiveStroke(lambda:owner,lambda:held[0])
        try:
            stroke.begin(20);self.wait_for(lambda:any(r[5]==p.LIVE_BEGIN for r in fake.writes))
            released=time.monotonic();held[0]=False
            self.wait_for(lambda:not stroke.pressed,.08)
            self.wait_for(lambda:any(r[5]==p.CANCEL_HOLD for r in fake.writes),.08)
            self.assertLess(time.monotonic()-released,.1)
            time.sleep(.15);self.assertFalse(any(r[5]==p.LIVE_POINT for r in fake.writes))
        finally:stroke.end();owner.stop(wait=True)
    def test_stop_invalidates_waiting_stroke_without_late_point(self):
        from app import App
        fake=DelayedSerial();owner=self.owner(fake);stroke=LiveStroke(lambda:owner)
        try:
            stroke.begin(20);self.wait_for(lambda:any(r[5]==p.LIVE_POINT for r in fake.writes))
            # Exercise the real button handler with no Tk window. STOP must be
            # queued before waiting for the mouse worker to leave its ACK wait.
            def finish_ui():
                self.wait_for(lambda:any(r[5]==p.STOP for r in fake.writes),.1)
                stroke.end()
            window=SimpleNamespace(owner=owner,editor=SimpleNamespace(end=finish_ui,last_error=''),connection=mock.Mock(),message='')
            self.assertIsNotNone(App.command(window,p.STOP));self.wait_for(lambda:not stroke.pressed,.1)
            self.wait_for(lambda:any(r[5]==p.STOP for r in fake.writes),.1)
            index=next(i for i,r in enumerate(fake.writes) if r[5]==p.STOP)
            time.sleep(.16);self.assertNotIn(p.LIVE_POINT,[r[5] for r in fake.writes[index+1:]])
        finally:stroke.end();owner.stop(wait=True)
    def test_real_disconnect_keeps_500ms_protection(self):
        fake=DelayedSerial(.07);owner=self.owner(fake);stroke=LiveStroke(lambda:owner)
        try:
            stroke.begin(20);self.wait_for(lambda:stroke.sent_points>=1)
            fake.silent=True;started=time.monotonic()
            self.wait_for(lambda:'500ms' in owner.state.error,.85)
            elapsed=time.monotonic()-started;self.assertGreaterEqual(elapsed,.47);self.assertLess(elapsed,.85)
            self.wait_for(lambda:not stroke.pressed,.1)
            self.assertFalse(owner.state.authenticated);self.assertTrue(any(r[5]==p.STOP for r in fake.writes))
            self.assertFalse(any(r[5] in (p.ZERO,p.RUN) for r in fake.writes))
        finally:stroke.end();owner.stop(wait=True)
    def test_late_stop_ack_does_not_invalidate_new_stroke(self):
        owner=SerialOwner('FAKE','unused',logger_factory=QuietLogger);owner.serial=FakeSerial()
        owner.state.connected=owner.state.authenticated=True;owner.hello_ok=owner.status_ok=owner.schema_ok=True
        owner.state.last_control=p.decode(telemetry());owner.state.last_control_rx=time.monotonic()
        owner.state.config_reads[(253,6)]=1
        ticket=owner.command(p.STOP);owner.queue.get_nowait();owner._send(p.STOP,ticket=ticket)
        delayed_stop=ack(owner.serial.writes[-1]);generation=owner.state.control_generation
        stroke=LiveStroke(lambda:owner)
        try:
            # A new fresh balance observation represents a later explicit RUN;
            # this offline test only delivers the old STOP ACK, never runs I/O.
            stroke.begin(20);owner._receive(delayed_stop,p.decode(delayed_stop));time.sleep(.02)
            self.assertEqual(owner.state.control_generation,generation);self.assertTrue(stroke.pressed,stroke.error)
        finally:stroke.end()
    def test_ack_errors_report_actual_status(self):
        fake=DelayedSerial(.01);owner=self.owner(fake);stroke=LiveStroke(lambda:owner)
        try:
            for status in range(1,7):
                fake.reject[p.LIVE_BEGIN]=status;stroke.begin(20)
                self.wait_for(lambda:not stroke.pressed);stroke.end()
                self.assertEqual(p.STATUS_TEXT[status],stroke.error)
                self.assertTrue(any(row.get('kind')=='ack' and row.get('opcode')==p.LIVE_BEGIN and row.get('status')==status and row.get('mode')==3 and row.get('fault')==0 for row in owner.logger.events))
                self.assertNotIn('尚未停稳',stroke.error)
        finally:stroke.end();owner.stop(wait=True)
    def test_clear_has_no_settling_qualification(self):
        from draw_editor import DrawEditor
        editor=DrawEditor.__new__(DrawEditor);editor.controller=SimpleNamespace(job=17,pressed=True,end=mock.Mock())
        editor.timeline=Timeline();editor.timeline.tracking=True;editor.timeline.job_id=17
        editor.draw=mock.Mock();editor.status=mock.Mock();editor.clear()
        editor.controller.end.assert_called_once();self.assertFalse(editor.timeline.rows);self.assertEqual(editor.cleared_job,17)
    def test_default_disconnected_without_gui_or_port_scan(self):
        import app
        class Variable:
            def __init__(self,value=''):self.value=value
            def get(self):return self.value
            def set(self,value):self.value=value
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(app.tk.Tk,'__init__',lambda obj:setattr(obj,'tk',mock.Mock())))
            for method in ('title','geometry','minsize','configure','protocol','after','ports'):
                stack.enter_context(mock.patch.object(app.App,method))
            for widget in ('Frame','Style','Combobox','Button','Label'):
                stack.enter_context(mock.patch.object(app.ttk,widget))
            stack.enter_context(mock.patch.object(app.tk,'StringVar',Variable))
            stack.enter_context(mock.patch.object(app,'DrawEditor'))
            owner_factory=stack.enter_context(mock.patch.object(app,'SerialOwner'))
            window=app.App();self.assertIsNone(window.owner);self.assertEqual(window.connection.get(),'未连接')
            owner_factory.assert_not_called();window.title.assert_called_once_with('J280 手绘控制 · DS13 · R2')
    def test_full_rtl_wire_and_real_difference(self):
        path=ROOT/'tests/system_run/uart_rx.bin'
        if not path.exists():self.skipTest('等待主代理生成DS13 system_run')
        raw=path.read_bytes();parser=p.FrameParser();decoded=parser.feed(raw)
        if decoded and {r['firmware_id'] for _,r in decoded}=={0x44533132}:self.skipTest('system_run仍为DS12，等待主代理生成DS13')
        self.assertEqual(parser.errors,0);self.assertGreater(len(decoded),100)
        self.assertTrue(all(r['identity_ok'] for _,r in decoded))
        live=[r for _,r in decoded if r['kind']=='telemetry' and r['coherent'] and r['play_state']==6]
        self.assertGreater(len(live),60);self.assertTrue(any(r['live_offset']!=r['wide_encoder'] for r in live))
        self.assertTrue(any(r['point_sequence']>0 for r in live))

if __name__=='__main__':unittest.main(verbosity=2)
