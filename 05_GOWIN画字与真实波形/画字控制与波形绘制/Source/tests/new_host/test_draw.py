"""Bounded mouse programs and ACK-based uploads; all owners are simulated."""
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import random
import struct
import sys
import threading
import unittest

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'host'))
import draw_trajectory as draw
from protocol import STATUS,STOP,ESTOP,CANCEL_HOLD,WRITE_CONFIG,READ_CONFIG,TRAJECTORY,decode
from client import SerialOwner

class FakeOwner:
    def __init__(self):
        self.calls=[];self.points=[];self.count=0;self.expected=0;self.valid=False;self.corrupt=False
        self.motion=0;self.fresh=True;self.ready_queries=0;self.requested_tracking=0;self.tracking=0
        self.state=SimpleNamespace(lock=threading.RLock(),command_results={},connected=True,error='')
        self.state.snapshot=lambda:dict(control_fresh=self.fresh,last_control=dict(mode=3,motion_state=self.motion))
    def command(self,op,group=0,payload=b'',source='GUI'):
        self.calls.append((op,group,payload,source));ticket=len(self.calls)
        ack=dict(status=0,mode=3,fault=0,flags=4,config_read_value=0)
        if op==WRITE_CONFIG:
            field=payload[0];value=struct.unpack('<I',payload[1:])[0]
            if field==7:self.requested_tracking=value;self.valid=False
            elif field==0:
                self.expected=value;self.points=[];self.valid=False
                self.tracking=self.requested_tracking;self.requested_tracking=0
            elif field==1:
                self.assert_index=value>>16
                if self.assert_index!=len(self.points):ack['status']=3
                self.points.append(struct.unpack('<h',payload[1:3])[0])
            elif field==2:self.valid=(value==draw.checksum(self.points,self.tracking))
            elif field==3:self.valid=False
        if op==READ_CONFIG:
            if payload[0]==6:
                self.ready_queries+=1
                if self.motion and self.ready_queries>1:self.motion=0
            ack['config_read_value']={0:self.expected,1:len(self.points),2:draw.checksum(self.points,self.tracking)^(1 if self.corrupt else 0),
                                      3:int(self.valid),4:draw.BUFFER_SIGNATURE,6:int(self.motion==0),7:self.tracking}[payload[0]]
        self.state.command_results[ticket]=ack
        return ticket

class DrawTests(unittest.TestCase):
    def test_prepare_preserves_order_and_adds_bounded_zero_return(self):
        program=draw.prepare([0,20,0,-20,0])
        self.assertEqual(draw.validate(program),program)
        self.assertEqual((program[0],program[-2],program[-1]),(0,0,0))
        self.assertLessEqual(max(program),20*16);self.assertGreaterEqual(min(program),-20*16)
        self.assertGreater(max(program),10*16);self.assertLess(min(program),-10*16)
        self.assertLessEqual(max(abs(b-a) for a,b in zip(program,program[1:])),96)

    def test_representative_recordings_obey_integer_speed_acceleration_and_travel(self):
        rng=random.Random(280)
        for _ in range(100):
            raw=[rng.randint(-8,8) for _ in range(12)]
            program=draw.prepare(raw);draw.validate(program)
            self.assertLessEqual(sum(abs(b-a) for a,b in zip(program,program[1:])),1040*16)

    def test_reject_nonfinite_overlong_and_unsafe_files(self):
        for raw in ([],[float('nan')],[float('inf')],[193],[True],['0'],[0]*513):
            with self.subTest(raw=repr(raw)[:30]):
                with self.assertRaises(ValueError):draw.prepare(raw)
        for points in ([0,7,0,0],[0,3,0,0],[0,0,1],[1,0,0],[0,0,float('nan')]):
            with self.assertRaises(ValueError):draw.validate(points)
        with self.assertRaises(ValueError):draw.prepare([192,-192,192,-192])

    def test_upload_preserves_balance_and_never_runs_or_stops_motor(self):
        owner=FakeOwner();program=draw.prepare([0,20,0,-20,0])
        result=draw.upload(owner,program,threading.Event())
        self.assertTrue(owner.valid);self.assertEqual(owner.points,program)
        self.assertEqual(result['checksum'],draw.checksum(program))
        self.assertTrue(all(call[0] not in (STOP,ESTOP,TRAJECTORY) for call in owner.calls))
        self.assertTrue(all(call[3]=='DRAW_UPLOAD' for call in owner.calls))
        self.assertEqual([call[2] for call in owner.calls if call[0]==READ_CONFIG][-6:],
                         [b'\x00',b'\x01',b'\x02',b'\x03',b'\x04',b'\x07'])

    def test_existing_finite_motion_finishes_without_disarming_or_preemption(self):
        owner=FakeOwner();owner.motion=6
        draw.upload(owner,draw.prepare([0,5,0]),threading.Event())
        self.assertEqual(owner.calls[0][0],STATUS)
        self.assertGreaterEqual(owner.ready_queries,2)
        self.assertFalse(any(call[0]==CANCEL_HOLD for call in owner.calls))
        self.assertFalse(any(call[0] in (STOP,ESTOP) for call in owner.calls))

    def test_cancelled_or_bad_readback_upload_never_plays(self):
        owner=FakeOwner();cancel=threading.Event()
        with self.assertRaises(RuntimeError):
            draw.upload(owner,draw.prepare([0,20,0]),cancel,lambda done,total:cancel.set() if done==3 else None)
        self.assertFalse(owner.valid);self.assertFalse(any(call[0]==TRAJECTORY for call in owner.calls))
        owner=FakeOwner();owner.corrupt=True
        with self.assertRaisesRegex(RuntimeError,'读回'):draw.upload(owner,draw.prepare([0,5,0]),threading.Event())
        self.assertFalse(any(call[0] in (STOP,TRAJECTORY) for call in owner.calls))

    def test_staging_needs_fresh_ACK_but_not_quiet_balance_control(self):
        owner=FakeOwner();owner.fresh=False
        result=draw.upload(owner,draw.prepare([0,1,0]),threading.Event())
        self.assertTrue(owner.valid)
        self.assertFalse(any(call[0] in (STOP,ESTOP,TRAJECTORY)for call in owner.calls))

    def test_real_owner_tickets_do_not_accept_other_request_ack(self):
        spec=importlib.util.spec_from_file_location('draw_fixtures',ROOT/'tests/host/test_host.py')
        fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
        owner=SerialOwner(5,'OFFLINE',ROOT/'unused');owner.state.connected=True;owner.state.authenticated=True
        ticket=owner.command(READ_CONFIG,252,b'\x06',source='DRAW_UPLOAD')
        self.assertIsInstance(ticket,int)
        owner.pending[123]=(READ_CONFIG,252,b'\x06',0);owner.pending_queue_ids[123]=ticket
        raw=fixtures.ack(READ_CONFIG,124,5);owner._receive(raw,decode(raw))
        self.assertNotIn(ticket,owner.state.command_results)
        raw=fixtures.ack(READ_CONFIG,123,5);owner._receive(raw,decode(raw))
        self.assertEqual(owner.state.command_results[ticket]['request_id'],123)

    def test_usb_route_is_used_for_upload_without_mirroring(self):
        from dual_transport import DuplexPort
        port=DuplexPort.__new__(DuplexPort);port.usb=object();port.ble=object()
        self.assertEqual(port.route_for_source('DRAW_UPLOAD'),'usb')

if __name__=='__main__':unittest.main()
