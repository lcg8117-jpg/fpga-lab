import binascii
from contextlib import nullcontext
from pathlib import Path
import json
import math
import queue
import struct
import sys
import tempfile
import threading
import time
import unittest
import zipfile

HOST=Path(__file__).resolve().parents[2]/'host';sys.path.insert(0,str(HOST))
from protocol import *
from client import SerialOwner,HostState
from session_log import SessionLogger,iter_records,RECORD_HEADER

def finish(frame):
    struct.pack_into('<H',frame,len(frame)-2,binascii.crc_hqx(frame[:-2],0xffff));return bytes(frame)

def ack(opcode=STATUS,request_id=1,variant=1,highwater=500,gains=DEFAULT_GAINS,revision=0,status=0,group=0,field=0,value=0):
    frame=bytearray(80);frame[:4]=b'FD2A';frame[4:8]=bytes((WIRE_VERSIONS[variant],opcode,status,variant))
    struct.pack_into('<4I',frame,8,request_id,revision,highwater,123);struct.pack_into('<6f',frame,24,*gains)
    struct.pack_into('<II',frame,48,value,7);frame[56:60]=bytes((group,field,0,0));struct.pack_into('<4I',frame,60,16,revision,123,FIRMWARE[variant]);return finish(frame)

def tlm(sequence=0,variant=1):
    frame=bytearray(256);frame[:4]=b'FD2T';frame[4:8]=bytes((WIRE_VERSIONS[variant],variant,0x71,3))
    struct.pack_into('<8I',frame,8,sequence,sequence*5,sequence,9,10,8,FIRMWARE[variant],SCHEMAS[variant])
    frame[40:48]=bytes((0,2,34,2,8,17,4,255))
    struct.pack_into('<5H3hi2h3q4i2H',frame,48,1023,8192,8191,3081,3082,-4,-32768,-29491,-2147483648,-32768,32767,-2**44,2**44,-2**46,-123456,-6900,-2300,4600,3085,5000)
    struct.pack_into('<3I',frame,116,sequence//5*5,345,456)
    for offset in (128,152,176):struct.pack_into('<6f',frame,offset,*DEFAULT_GAINS)
    struct.pack_into('<6f',frame,200,-1.25,0,2.5,-3.75,0,4.125)
    struct.pack_into('<5I',frame,224,7,8,9,10,11);struct.pack_into('<H2B2h2b',frame,244,65535,0x80,0x01,-12,64,-3,4)
    return finish(frame)

def diag(sequence=0,variant=3,learning=10,adoption=20):
    frame=bytearray(256);frame[:4]=b'FD2D';frame[4:8]=bytes((WIRE_VERSIONS[variant],variant,1,0x14))
    struct.pack_into('<6I',frame,8,sequence,123450,SCHEMAS[variant],FIRMWARE[variant],1000,123445)
    frame[32:36]=bytes((1,1,2,0x32));struct.pack_into('<HhiHHIIiiq',frame,36,250,-120,-2147483648,29,30,1040,1040,-1040,520,-910)
    frame[72:80]=bytes((45,11,1,2,3,1,0,6))
    struct.pack_into('<4I',frame,80,adoption,123425,995,994)
    frame[96:100]=bytes((1,1,10,9));struct.pack_into('<3I',frame,100,learning,999,123440)
    struct.pack_into('<BbbBBBHH',frame,112,10,8,4,3,0,4,30,40)
    struct.pack_into('<II',frame,124,25600,6400)
    struct.pack_into('<6f',frame,132,*DEFAULT_GAINS)
    struct.pack_into('<6f',frame,156,48.6171875,0,258.84375,25,0,610)
    struct.pack_into('<9I3i',frame,180,999,4,994,4,4,200,123000,123010,123020,-100,200,-300)
    frame[228:230]=bytes((0x57,64));return finish(frame)

class FakeSerial:
    def __init__(self,variant=1,*,telemetry=False,ignore_set=False):
        self.variant=variant;self.highwater=500;self.gains=DEFAULT_GAINS;self.revision=0;self.mode=0
        self.rx=bytearray();self.lock=threading.Lock();self.commands=[];self.closed=False;self.telemetry=telemetry;self.sequence=0;self.due=time.monotonic();self.ignore_set=ignore_set
        self.config={};self.timestamps=[]
    def write(self,data):
        assert len(data)==48 and data[:4]==b'FD2C' and valid_frame(data)
        op,group,length=data[5:8];request_id=struct.unpack_from('<I',data,8)[0];payload=data[12:12+length]
        if data[4]==2:assert data[12+length:46]==bytes(34-length)
        else:
            assert data[12+length:36]==bytes(24-length)
            assert struct.unpack_from('<I',data,36)[0]!=0 and struct.unpack_from('<I',data,40)[0]==request_id
        with self.lock:
            self.commands.append((op,request_id,group,payload));self.timestamps.append((op,time.monotonic()))
            if op==SET_GAINS and self.ignore_set:return len(data)
            status=0
            if op not in READS:
                if request_id<=self.highwater:status=5
                else:self.highwater=request_id
            if not status and op==SET_GAINS:self.gains=struct.unpack('<6f',payload);self.revision+=1
            if not status and op==WRITE_CONFIG:self.config[(group,payload[0])]=struct.unpack_from('<I',payload,1)[0]
            field=payload[0] if op in (READ_CONFIG,WRITE_CONFIG) else 0
            value=self.config.get((group,field),0)
            self.rx.extend(ack(op,request_id,self.variant,self.highwater,self.gains,self.revision,status,group,field,value))
        return len(data)
    def read(self,count):
        with self.lock:
            now=time.monotonic()
            if self.telemetry:
                while now>=self.due:
                    self.rx.extend(tlm(self.sequence,self.variant));self.sequence+=1;self.due+=0.005
            data=bytes(self.rx[:count]);del self.rx[:count]
        if not data:time.sleep(0.001)
        return data
    def __enter__(self):return self
    def __exit__(self,*_):self.closed=True

def wait_for(condition,timeout=2):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if condition():return
        time.sleep(0.005)
    raise AssertionError('condition timeout')

class ProtocolTests(unittest.TestCase):
    def test_fieldfix_identity_stage_action_and_reference_phase(self):
        from app import App,swing_description,action_description,motion_reference_text
        self.assertEqual(FIRMWARE,{1:0x46443231,2:0x46443432,3:0x46443233,4:0x46443434,5:0x56354334})
        item=decode(tlm(1,2));item.update(mode=5,swing_profile=0,swing_stage=10)
        self.assertIn('尚未施力',swing_description(item))
        item['swing_stage']=40;self.assertIn('等待顶部捕获',swing_description(item))
        item['swing_profile']=2;self.assertIn('峰值脉冲源',swing_description(item))
        item['mode']=1;self.assertEqual(swing_description(item),'起摆未运行')
        item.update(action_reason=4,motion_state=4,gesture_code=8)
        self.assertIn('动作未接受',action_description(item))
        self.assertNotIn('传感器',action_description(item))
        item.update(wide_target=-155,wide_encoder=-143)
        text=motion_reference_text(item,885,19)
        self.assertIn('目标增量=-1040计数 / -360.00000000°',text)
        self.assertIn('目标相位=885/1040',text)
        self.assertIn('未取模误差=+12计数 / +4.15384615°',text)
        self.assertIn('未观察到',motion_reference_text(item,None,None))
        class Canvas:
            def __init__(self):self.lines=[]
            def delete(self,*_):pass
            def winfo_width(self):return 1000
            def winfo_height(self):return 120
            def create_text(self,*_,**__):pass
            def create_rectangle(self,*_,**__):pass
            def create_line(self,*points,**_):self.lines.append(points)
        app=App.__new__(App);app.variant=2;app.canvas=Canvas()
        history=[(n,0,0,12,100+n,88+n,board) for n,board in enumerate((100,105,110,130,135))]
        app.draw(history)
        self.assertEqual(len(app.canvas.lines),8)
        self.assertEqual([len(line) for line in app.canvas.lines],[6,4]*4)
        self.assertAlmostEqual(app.canvas.lines[4][4],558+(10/35)*(985-558))

    def test_diagnostic_layout_and_independent_packet_kind(self):
        item=decode(diag())
        self.assertEqual(item['kind'],'diagnostic');self.assertFalse(item['coherent'])
        self.assertEqual(item['layout'],1);self.assertEqual(item['schema'],0x46443203)
        self.assertEqual(item['minimum_edge_clocks'],30);self.assertNotIn('invalid_limit',item)
        for name,value in {'delta_q4':-120,'physical_encoder_raw':-2147483648,
                           'measured_velocity':-1040,'reference_velocity':520,'tracking_error':-910,
                           'center_before':8,'center_after':4,'reference_energy':25600,'window_energy':6400,
                           'event_samples':64,'adopted_feature_epoch':994,'candidate_feature_epoch':999,
                           'adoption_count':200,'source_alpha':-100,'source_arm_rate':-300}.items():
            self.assertEqual(item[name],value,name)
        self.assertEqual(item['adoption_before_gains'],DEFAULT_GAINS)
        self.assertEqual(item['adoption_after_gains'][0],48.6171875)
        parser=FrameParser();stream=diag()+tlm(0,3)+ack(variant=3)
        records=[]
        for offset in range(0,len(stream),11):records.extend(parser.feed(stream[offset:offset+11]))
        self.assertEqual([item['kind'] for _,item in records],['diagnostic','telemetry','ack'])
        bad=bytearray(diag());bad[6]=2
        with self.assertRaisesRegex(ValueError,'布局'):decode(finish(bad))
    def test_crc_and_request_layout(self):
        self.assertEqual(crc16(b'123456789'),0x29b1)
        raw=request(SET_GAINS,0xffffffff,255,gains_payload(DEFAULT_GAINS))
        self.assertEqual(raw[:8],b'FD2C\2\x20\xff\x18');self.assertEqual(raw[12:36],struct.pack('<6f',*DEFAULT_GAINS));self.assertEqual(raw[36:46],bytes(10));self.assertTrue(valid_frame(raw))
        for v in (float('nan'),float('inf'),-1,10001):
            with self.assertRaises(ValueError):gains_payload((v,0,0,0,0,0))
        self.assertEqual(gains_payload((-0.0,0,0,0,0,0))[:4],bytes(4))
        for request_id in (0,2**32):
            with self.assertRaises(ValueError):request(STOP,request_id)
    def test_all_signed_fields_and_float_banks(self):
        item=decode(tlm(17))
        expected={'direction':-1,'angle_error':-4,'angle_diff':-32768,'actual_applied_q15':-29491,'physical_encoder_raw':-2147483648,'local_encoder':-32768,'local_target':32767,'wide_encoder':-2**44,'wide_target':2**44,'wide_physical_encoder':-2**46,'position_error':-123456,'requested_moto':-6900,'angle_pwm':-2300,'position_pwm':4600,'center_p_offset':-12,'center_d_offset':64,'fast_p_offset':-3,'fast_d_offset':4}
        for key,value in expected.items():self.assertEqual(item[key],value,key)
        self.assertEqual(item['components'],(-1.25,0,2.5,-3.75,0,4.125));self.assertEqual(item['manual_gains'],DEFAULT_GAINS)
        self.assertTrue(item['coherent']);self.assertTrue(item['position_components_held']);self.assertEqual(item['control_period_us'],5000)
        a=decode(ack());self.assertEqual(a['mutation_highwater'],500);self.assertEqual(a['firmware_id'],FIRMWARE[1])
    def test_resync_crc_and_partial_frames(self):
        parser=FrameParser();bad=bytearray(tlm(2));bad[150]^=1
        stream=b'noiseFD2'+bytes(bad)+b'xxx'+ack()+tlm(3)
        output=[]
        for i in range(0,len(stream),7):output+=parser.feed(stream[i:i+7])
        self.assertEqual([x[1]['kind'] for x in output],['ack','telemetry']);self.assertGreater(parser.crc_errors,0);self.assertEqual(output[-1][1]['sequence'],3)
    def test_request_groups_and_reserved_contract(self):
        for op,group,payload in ((READ_GAINS,255,b''),(READ_CONFIG,254,b'\4'),(WRITE_CONFIG,2,b'\x40'+struct.pack('<I',90)),(SELECT_SWING,0,b'\2')):self.assertTrue(valid_frame(request(op,1,group,payload)))
        with self.assertRaises(ValueError):request(SET_GAINS,1,0,bytes(24))
        with self.assertRaises(ValueError):request(READ_CONFIG,1,3,b'\0')
        with self.assertRaises(ValueError):request(START_SWING,1,payload=b'\0')

class ClientTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.owners=[]
    def tearDown(self):
        for owner in self.owners:owner.stop()
        self.temp.cleanup()
    def owner(self,variant=1,fake=None,logger_factory=SessionLogger):
        fake=fake or FakeSerial(variant)
        owner=SerialOwner(variant,'FAKE_ONLY',Path(self.temp.name),serial_factory=lambda *_:fake,lease_factory=nullcontext,logger_factory=logger_factory,logger_options={'min_free_bytes':0,'max_shared_bytes':64*1024**2,'reserve_bytes':1024})
        self.owners.append(owner);owner.start();wait_for(lambda:owner.state.authenticated and owner.state.independent is not None);return owner,fake
    def test_handshake_highwater_once_set_independent_read_drafts(self):
        owner,fake=self.owner();self.assertTrue(all(x[0] in READS for x in fake.commands))
        owner.state.manual_drafts=['draft']*6
        values=(51,0,255,26,0,620);owner.set_gains(values)
        wait_for(lambda:owner.state.independent==values)
        self.assertEqual(sum(x[0]==SET_GAINS for x in fake.commands),1);self.assertTrue(any(x[0]==READ_GAINS and x[1]>next(y[1] for y in fake.commands if y[0]==SET_GAINS) for x in fake.commands))
        self.assertTrue(all(x[1]>500 for x in fake.commands if x[0] not in READS));self.assertEqual(owner.state.manual_drafts,['draft']*6)
        owner.command(WRITE_CONFIG,2,b'\x40'+struct.pack('<I',91));wait_for(lambda:owner.state.config_reads.get((2,64))==(91,7))
        self.assertTrue(any(x[0]==READ_CONFIG and x[2]==2 and x[3]==b'\x40' for x in fake.commands))
    def test_identity_mismatch_blocks_mutation(self):
        owner=SerialOwner(1,'FAKE',self.temp.name,serial_factory=lambda *_:FakeSerial(2),lease_factory=nullcontext,logger_options={'min_free_bytes':0,'max_shared_bytes':64*1024**2,'reserve_bytes':1024});self.owners.append(owner);owner.start()
        wait_for(lambda:'不匹配' in owner.state.error)
        with self.assertRaises(RuntimeError):owner.command(RUN)
        self.assertFalse(owner.state.authenticated)
    def test_v2_reference_origin_board_time_history_and_gesture_observation(self):
        owner=SerialOwner(2,'NEVER_OPENED',self.temp.name,lease_factory=nullcontext)
        owner.state.connected=True;owner.state.authenticated=True
        def receive(sequence,epoch,board_ms,motion,target,event,mode=3):
            raw=bytearray(tlm(sequence,2));raw[6]=0x17;raw[7]=mode;raw[43]=motion
            struct.pack_into('<II',raw,12,board_ms,epoch)
            struct.pack_into('<qq',raw,72,target-12,target)
            struct.pack_into('<I',raw,120,event);raw=finish(raw)
            owner._receive(raw,decode(raw))
        receive(1,1,100,0,885,345)
        receive(2,2,105,1,885,348)
        self.assertEqual(owner.state.motion_reference_start,885)
        self.assertEqual(owner.state.motion_reference_start_epoch,1)
        self.assertEqual(owner.state.gesture_event_gaps,2)
        receive(3,3,125,4,-155,348)
        receive(4,4,130,1,-155,349)
        self.assertEqual(owner.state.motion_reference_start,-155)
        self.assertEqual([row[6] for row in owner.state.history],[100,105,125,130])
        self.assertEqual(owner.state.history[-1][4:6],(-167,-155))
        self.assertEqual(len(owner.state.gesture_events),3)
        self.assertEqual(owner.state.history.maxlen,4000)
        self.assertEqual(HostState(1).history.maxlen,2000)
        self.assertEqual(HostState(3).history.maxlen,2000)
        receive(5,1,135,0,0,349,mode=1)
        self.assertIsNone(owner.state.motion_reference_start)
        old=bytearray(diag(1,2));struct.pack_into('<I',old,20,0x46443232);old=finish(old)
        owner._receive(old,decode(old))
        self.assertFalse(owner.state.authenticated)
    def test_manual_I_live_guard_and_variant_capabilities(self):
        owner,_=self.owner(3)
        owner.state.latest=decode(tlm(1,3));owner.state.latest['flags']|=2
        with self.assertRaises(ValueError):owner.set_gains((49,1,251,25,0,610))
        owner.command(ADAPT_ENABLE);owner.command(ADAPT_DISABLE)
        with self.assertRaises(ValueError):owner.command(START_SWING)
        with self.assertRaises(ValueError):owner.command(MOTION_CW)
    def test_200Hz_logging_and_keepalive_without_UI(self):
        def slow_disk(path):
            time.sleep(0.15)
            return type('Space',(),{'free':100*1024**3})()
        def logger_factory(*args,**kwargs):return SessionLogger(*args,**kwargs,disk_usage=slow_disk)
        owner,fake=self.owner(2,FakeSerial(2,telemetry=True),logger_factory)
        time.sleep(0.8)  # No Tk loop or UI work occurs during this interval.
        self.assertGreater(len(owner.state.history),140);self.assertLess(owner.state.max_heartbeat_gap,0.22)
        self.assertEqual(owner.state.sequence_gaps,0);owner.stop();self.assertTrue(owner.closed.is_set())
        frames=list(iter_records(owner.logger.archive_path));self.assertGreater(len(frames),140)
        self.assertEqual(len(frames),owner.logger.written);self.assertEqual(owner.logger.rejected,0)
        print(f'METRIC keepalive_max_gap_ms={owner.state.max_heartbeat_gap*1000:.3f} coherent_records={len(owner.state.history)} saved_frames={len(frames)} log_queue_peak_bytes={owner.logger.peak_queue_bytes}')
    def test_unknown_timeout_never_replays_mutation(self):
        owner,fake=self.owner(1,FakeSerial(1,ignore_set=True));owner.set_gains(DEFAULT_GAINS)
        wait_for(lambda:owner.state.timeouts>0,2)
        self.assertEqual(sum(x[0]==SET_GAINS for x in fake.commands),1);self.assertIn('不会自动重发',owner.state.error)
    def test_logging_failure_blocks_non_stop_but_not_keepalive(self):
        owner,fake=self.owner();owner.logger.error='injected disk full'
        wait_for(lambda:owner.state.log_error!='');wait_for(lambda:any(x[0]==STOP for x in fake.commands))
        with self.assertRaises(RuntimeError):owner.command(RUN)
        owner.command(READ_GAINS,255);before=sum(x[0]==STATUS for x in fake.commands);time.sleep(0.22)
        self.assertGreater(sum(x[0]==STATUS for x in fake.commands),before)
        owner.stop();self.assertTrue(owner.logger.partial_path.exists());self.assertFalse(owner.logger.archive_path.exists())
    def test_stale_and_unmatched_ACK_cannot_claim_success_or_replay(self):
        owner=SerialOwner(1,'NEVER_OPENED',self.temp.name,lease_factory=nullcontext)
        owner.state.connected=True;owner.state.pending_mutations=1;owner.pending[42]=(RUN,0,b'',time.monotonic())
        owner._receive(ack(RUN,42,highwater=1000,status=5),decode(ack(RUN,42,highwater=1000,status=5)))
        self.assertEqual(owner.next_id,1001);self.assertEqual(owner.state.last_ack['status'],5);self.assertEqual(owner.state.pending_mutations,0);self.assertTrue(owner.queue.empty())
        owner._receive(ack(RUN,99,highwater=0xffffffff),decode(ack(RUN,99,highwater=0xffffffff)))
        self.assertEqual(owner.next_id,1001);self.assertEqual(owner.state.unmatched_acks,1)
    def test_identity_failure_is_latched_until_explicit_reconnect(self):
        owner=SerialOwner(1,'NEVER_OPENED',self.temp.name,lease_factory=nullcontext);owner.state.connected=True
        wrong=ack(STATUS,1,variant=2);owner._receive(wrong,decode(wrong))
        owner.pending[2]=(STATUS,0,b'',time.monotonic())
        good=ack(STATUS,2);owner._receive(good,decode(good));self.assertFalse(owner.state.authenticated)
    def test_real_refresh_never_overwrites_editable_PID_drafts(self):
        from app import App
        class Variable:
            def __init__(self,value=''):self.value=value
            def set(self,value):self.value=value
            def get(self):return self.value
        class Root:
            def after(self,*_):pass
        app=App.__new__(App);app.root=Root();app.variant=3;app.closing=False;app.ui_error=''
        app.owner=type('Owner',(),{'state':HostState(3)})();app.owner.state.latest=decode(tlm(12,3));app.owner.state.stored=DEFAULT_GAINS;app.owner.state.independent=DEFAULT_GAINS
        app.owner.state.last_control=app.owner.state.latest;app.owner.state.last_control_rx=time.monotonic()
        app.owner.state.connected=True;app.owner.state.authenticated=True
        app.drafts=[Variable('user-'+str(i)) for i in range(6)]
        app.saved=[Variable() for _ in range(6)];app.reads=[Variable() for _ in range(6)];app.adopted=[Variable() for _ in range(6)]
        for name in ('identity','notice','revisions','summary','detail','adapt_detail','adapt_average','adapt_events','log_status','actual_primary','actual_meta','kit_readback','kit_detail'):setattr(app,name,Variable())
        app.swing_reads={};app.swing_saved={};app.draw=lambda _:None
        app.refresh();self.assertEqual([v.get() for v in app.drafts],['user-'+str(i) for i in range(6)])
        self.assertEqual(app.adopted[0].get(),'49');self.assertIn('控制epoch=12',app.detail.get())
        self.assertIn('本事务自适应增益来源=True',app.actual_meta.get())
        app.owner.state.last_control.update(learning_revision=0,center_p_offset=0,center_d_offset=0,motor_permitted_at_completion=True)
        app.refresh();self.assertIn('尚无长期中心保留；当前仅快速调节',app.adapt_detail.get())
        app.owner.state.last_control_rx-=1;app.refresh()
        self.assertIn('已过期/不可用',app.actual_primary.get());self.assertEqual(app.adopted[0].get(),'已过期/不可用')
        self.assertIn('数据过期，调节不可用',app.adapt_detail.get())

    def test_control_diagnostic_and_learning_sequences_are_independent(self):
        owner=SerialOwner(3,'NEVER_OPENED',self.temp.name,lease_factory=nullcontext)
        owner.state.connected=True;owner.state.authenticated=True
        for raw in (tlm(10,3),diag(100),tlm(11,3),diag(101),diag(104,learning=14,adoption=23)):
            owner._receive(raw,decode(raw))
        self.assertEqual(owner.state.sequence_gaps,0);self.assertEqual(owner.state.diagnostic_sequence_gaps,2)
        self.assertEqual(owner.state.learning_event_gaps,3);self.assertEqual(owner.state.adoption_event_gaps,2)
        self.assertEqual(len(owner.state.history),2);self.assertEqual(owner.state.last_control['control_epoch'],11)
        self.assertEqual(owner.state.latest_diagnostic['sequence'],104)
        stamp=owner.state.last_control_rx;owner.state.last_control_rx-=1
        raw=bytearray(tlm(12,3));raw[6]&=~1;raw=finish(raw);owner._receive(raw,decode(raw))
        owner._receive(diag(105),decode(diag(105)))
        self.assertFalse(owner.state.snapshot()['control_fresh']);self.assertEqual(owner.state.last_control_rx,stamp-1)
        self.assertEqual(len(owner.state.history),2)
        owner.state.last_control_rx=time.monotonic();owner.state.connected=False
        self.assertFalse(owner.state.snapshot()['control_fresh'])

    def test_wrong_diagnostic_schema_and_old_firmware_latch_identity_failure(self):
        for offset,value in ((16,0x46443202),(20,0x46443133)):
            owner=SerialOwner(3,'NEVER_OPENED',self.temp.name,lease_factory=nullcontext)
            owner.state.connected=True;owner.state.authenticated=True
            frame=bytearray(diag());struct.pack_into('<I',frame,offset,value);frame=finish(frame)
            owner._receive(frame,decode(frame));self.assertFalse(owner.state.authenticated)
            self.assertIn('不匹配',owner.state.error)
            with self.assertRaises(RuntimeError):owner.command(RUN)

    def test_unframed_legacy_substring_is_not_identity_evidence(self):
        class LegacySerial(FakeSerial):
            def __init__(self):super().__init__(3);self.legacy=True
            def read(self,count):
                if self.legacy:self.legacy=False;return b'FD1W'+bytes(20)
                return super().read(count)
        owner=SerialOwner(3,'FAKE_LEGACY_ONLY',self.temp.name,serial_factory=lambda *_:LegacySerial(),
            lease_factory=nullcontext,logger_options={'min_free_bytes':0,'reserve_bytes':1024,'max_shared_bytes':64*1024**2})
        # This fixture subsequently returns matching CRC-valid FD2 identity.
        # A preceding four-byte substring must not override that real proof.
        self.owners.append(owner);owner.start();wait_for(lambda:owner.state.authenticated)
        self.assertEqual(owner.state.error,'');self.assertFalse(owner._identity_failed)

    def test_kit_explicit_stop_only_and_independent_readback(self):
        owner,_=self.owner(3)
        owner.set_kit(1);wait_for(lambda:owner.state.config_reads.get((254,5))==(1,7))
        owner.state.last_ack['mode']=3
        with self.assertRaisesRegex(RuntimeError,'STOP'):owner.set_kit(0)
        owner.state.last_ack['mode']=1;owner.state.last_ack['fault']=1
        with self.assertRaises(RuntimeError):owner.set_kit(0)
        owner.state.last_ack['fault']=0;owner.state.last_ack_rx-=1
        with self.assertRaisesRegex(RuntimeError,'新鲜'):owner.set_kit(0)

class LoggerTests(unittest.TestCase):
    def test_2000_frames_once_zip_roundtrip_and_index(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            logger=SessionLogger(temp,1,min_free_bytes=0,reserve_bytes=1024,max_shared_bytes=64*1024**2).start()
            originals=[tlm(n) for n in range(2000)]
            for n,raw in enumerate(originals):self.assertTrue(logger.submit(raw,decode(raw),wall_ns=n,mono_ns=n+1))
            logger.close();self.assertFalse(logger.error);self.assertLessEqual(logger.peak_queue_bytes,logger.max_queue_bytes)
            records=list(iter_records(logger.archive_path));self.assertEqual([x[2] for x in records],originals)
            with zipfile.ZipFile(logger.archive_path) as archive:
                self.assertEqual(set(archive.namelist()),{'records.bin','index.csv','session.json'})
                data=archive.read('records.bin');self.assertEqual(len(data),len(originals)*(256+RECORD_HEADER.size))
                for raw in (originals[0],originals[1000],originals[-1]):self.assertEqual(data.count(raw),1)
                self.assertEqual(len(archive.read('index.csv').splitlines()),2001)
            self.assertEqual(len(list(Path(temp).iterdir())),1)
            print(f'METRIC log_roundtrip_frames={len(records)} raw_record_bytes={len(data)} zip_bytes={logger.archive_path.stat().st_size} no_duplicate_raw_payload=True')
    def test_queue_limit_and_disk_limits_preserve_history(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            history=Path(temp)/'old.bin';history.write_bytes(b'historical evidence')
            logger=SessionLogger(temp,1,max_queue_bytes=32,min_free_bytes=0,reserve_bytes=1)
            self.assertFalse(logger.submit(tlm(),decode(tlm())));self.assertIn('队列',logger.error)
            low=SessionLogger(temp,1,min_free_bytes=10,reserve_bytes=1,disk_usage=lambda _:type('S',(),{'free':0})())
            with self.assertRaises(OSError):low.start()
            self.assertEqual(history.read_bytes(),b'historical evidence')
            capped=SessionLogger(temp,1,min_free_bytes=0,reserve_bytes=1,max_shared_bytes=5)
            with self.assertRaises(OSError):capped.start()
            with self.assertRaises(ValueError):SessionLogger(temp,1,max_shared_bytes=0)

class GainAverageTests(unittest.TestCase):
    def owner(self):
        owner=SerialOwner(3,'NEVER_OPENED',Path(__file__).parent,lease_factory=nullcontext)
        owner.state.connected=True;owner.state.authenticated=True
        return owner
    def feed(self,owner,n,*,board=None,epoch=None,mode=3,fault=0,enabled=True,base=DEFAULT_GAINS):
        raw=bytearray(tlm(n,3));used=n<500
        raw[6]=0x17|(32 if enabled else 0)|(64 if used else 0);raw[7]=mode;raw[40]=fault
        struct.pack_into('<II',raw,12,n*5 if board is None else board,n if epoch is None else epoch)
        actual=(51+(-2 if n%2==0 else 2),0,255+(-4 if n%2==0 else 4),25,0,610) if used else base
        for offset,bank in ((128,base),(152,actual),(176,(10000,0,10000,10000,0,10000))):struct.pack_into('<6f',raw,offset,*bank)
        raw=finish(raw);owner._receive(raw,decode(raw))
    def test_actual_time_weighted_windows_keep_rapid_values_and_base_fallback(self):
        from app import gain_average_text
        owner=self.owner()
        for n in range(1001):self.feed(owner,n)
        summary=owner.state.snapshot()['actual_gain_averages'];one,five=summary['windows']
        self.assertTrue(summary['valid']);self.assertEqual((one['covered_ms'],five['covered_ms']),(1000,5000))
        self.assertEqual((one['kp'],one['kd']),(49,251));self.assertEqual((five['kp'],five['kd']),(50,253))
        self.assertEqual((one['adopted_percent'],five['adopted_percent']),(0,50))
        self.assertEqual(five['intervals'],1000);self.assertTrue(five['complete'])
        self.assertEqual(owner.state.last_control['active_gains'],DEFAULT_GAINS)
        self.assertIn('5s实际均值',gain_average_text(summary));self.assertIn('↑',gain_average_text(summary))
        prior_len=len(owner.state.gain_average['points']);prior_stamp=owner.state.gain_average['last_rx']
        self.feed(owner,1000)
        self.assertEqual(len(owner.state.gain_average['points']),prior_len)
        self.assertEqual(owner.state.gain_average['last_rx'],prior_stamp)
        owner.state.gain_average['last_rx']-=1
        self.assertFalse(owner.state.snapshot()['actual_gain_averages']['valid'])
    def test_average_resets_on_gap_stop_fault_switch_manual_and_epoch(self):
        from app import gain_average_text
        owner=self.owner()
        self.feed(owner,1);self.feed(owner,2)
        initial=owner.state.snapshot()['actual_gain_averages']
        self.assertTrue(initial['valid']);self.assertEqual(initial['windows'][0]['covered_ms'],5)
        self.assertIn('部分覆盖',gain_average_text(initial))
        self.feed(owner,4)
        self.assertFalse(owner.state.snapshot()['actual_gain_averages']['valid'])
        self.assertEqual(len(owner.state.gain_average['points']),1)
        self.feed(owner,5);self.assertTrue(owner.state.snapshot()['actual_gain_averages']['valid'])
        for n,options,reason in ((6,dict(mode=1),'stopped'),(7,dict(fault=1),'fault'),(8,dict(enabled=False),'adaptive_disabled')):
            self.feed(owner,n,**options)
            result=owner.state.snapshot()['actual_gain_averages']
            self.assertFalse(result['valid']);self.assertEqual(result['reason'],reason)
            self.assertEqual(len(owner.state.gain_average['points']),0)
        self.feed(owner,9);self.feed(owner,10)
        self.feed(owner,11,base=(50,0,250,25,0,610))
        self.assertFalse(owner.state.snapshot()['actual_gain_averages']['valid'])
        self.feed(owner,12,epoch=1,base=(50,0,250,25,0,610))
        self.assertEqual(owner.state.gain_average['reason'],'epoch_reset')
        self.assertEqual(len(owner.state.gain_average['points']),1)
        owner.state.connected=False
        self.assertEqual(owner.state.snapshot()['actual_gain_averages']['reason'],'control_expired')

if __name__=='__main__':unittest.main()
