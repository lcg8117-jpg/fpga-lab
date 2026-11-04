"""Five-version protocol/trace/key/transport tests; every device is simulated."""
from pathlib import Path
from contextlib import nullcontext
import asyncio,importlib.util,json,struct,sys,tempfile,time,unittest,zipfile
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'host'))
spec=importlib.util.spec_from_file_location('fixtures_five',ROOT/'tests/host/test_host.py');f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
from protocol import *
from client import SerialOwner
from key_actions import KeyActions,DEFAULT,load_mapping
from trace_log import TraceLogger,iter_chunks,GIB
def compact(sequence=1,epoch=1,variant=4):
    raw=bytearray(48);raw[:4]=b'FD2B';raw[4:8]=bytes((WIRE_VERSIONS[variant],variant,0x87,3))
    struct.pack_into('<IIIqii4hBB',raw,8,sequence,sequence*5,epoch,-2**40,173,1040,-7,0,-1200,520,0,1)
    return f.finish(raw)
class Five(unittest.TestCase):
    def test_continuous_endpoint_is_hidden_until_stop_locks_it(self):
        for variant in (2,4,5):
            for action,done,expected in ((3,False,False),(4,False,False),(5,False,False),(5,True,True),(0x16,False,True),(1,False,True)):
                raw=bytearray(f.diag(2,variant));raw[7]|=2;raw[250]=action
                raw[74]=(raw[74]|1) if done else (raw[74]&254)
                item=decode(f.finish(raw));self.assertEqual(item['final_target_valid'],expected,(variant,action,done,item['trajectory_done']))
    def test_archive_reserves_duplicate_sidecar_without_deleting_history(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'reports') as temp:
            root=Path(temp);raw=root/'active.chunks'
            with raw.open('wb') as handle:handle.truncate(6*1024**2)
            logger=TraceLogger(root,4,max_shared_bytes=19*1024**2,disk_usage=lambda unused:type('Disk',(),{'free':100*GIB})())
            logger.raw_path=raw
            with self.assertRaises(OSError):logger._check()
            self.assertTrue(raw.exists());self.assertEqual(raw.stat().st_size,6*1024**2)
    def test_new_request_contract_and_version_isolation(self):
        for v in (2,4,5):
            raw=request(POSITION,77,payload=b'\x06',version=WIRE_VERSIONS[v],session=123,event=55,route=2)
            self.assertTrue(valid_frame(raw));self.assertEqual(struct.unpack_from('<II',raw,36),(123,55));self.assertEqual(raw[44],2)
            self.assertEqual(raw[13:36],bytes(23))
        with self.assertRaises(ValueError):request(POSITION,1,payload=b'\x00')
        self.assertEqual(len(set(FIRMWARE.values())),5)
    def test_compact_fragment_corruption_and_precision(self):
        raw=compact();parser=FrameParser();items=[]
        for byte in raw:items.extend(parser.feed(bytes((byte,))))
        self.assertEqual(len(items),1);item=items[0][1]
        self.assertEqual(item['wide_encoder'],-2**40);self.assertEqual(item['final_target'],-2**40+1040)
        self.assertNotIn('active_gains',item);self.assertNotIn('firmware_id',item)
        broken=bytearray(raw);broken[18]^=1
        result=parser.feed(b'junk'+broken+raw);self.assertEqual(len(result),1);self.assertGreater(parser.crc_errors,0)
    def test_compact_never_authenticates_or_fabricates_gains(self):
        owner=SerialOwner(4,'OFFLINE',ROOT/'unused',lease_factory=nullcontext);raw=compact();owner._receive(raw,decode(raw))
        self.assertFalse(owner.state.authenticated);self.assertIsNone(owner.state.last_compact)
        owner.state.authenticated=True;owner._receive(raw,decode(raw));stamp=owner.state.last_compact_rx
        owner._receive(raw,decode(raw));self.assertEqual(owner.state.last_compact_rx,stamp)
        self.assertIsNone(owner.state.last_control);self.assertFalse(owner.state.snapshot()['control_fresh'])
    def test_duplicate_epoch_cannot_refresh_freshness(self):
        owner=SerialOwner(2,'OFFLINE',ROOT/'unused');owner.state.connected=True;owner.state.authenticated=True
        raw=f.tlm(10,2);owner._receive(raw,decode(raw));stamp=owner.state.last_control_rx
        owner._receive(raw,decode(raw));self.assertEqual(owner.state.last_control_rx,stamp);self.assertEqual(owner.state.duplicate_controls,1)
    def test_key_repeat_release_repress_and_latest_independent_press(self):
        class Owner:
            def __init__(self):self.calls=[];self.events=[]
            def command(self,*args,**kwargs):self.calls.append((args,kwargs))
            def trace(self,*args,**kwargs):self.events.append((args,kwargs))
        owner=Owner();keys=KeyActions(owner,DEFAULT);keys.set_layer(2);keys.arm()
        for _ in range(100):keys.on_key(ord('L'),True)
        self.assertEqual(len(owner.calls),1);self.assertEqual(owner.calls[0][1]['payload'],b'\x06')
        keys.on_key(ord('L'),False);keys.on_key(ord('L'),True);self.assertEqual(len(owner.calls),2)
        keys.on_key(ord('G'),True);self.assertEqual(len(owner.calls),3)
        keys.set_layer(3);keys.on_key(ord('M'),True);self.assertEqual(len(owner.calls),3)
        for key in 'LGM':keys.on_key(ord(key),False)
        keys.on_key(ord('M'),True);self.assertEqual(len(owner.calls),4)
        keys.removed();self.assertEqual(owner.calls[-1][0][0],STOP)
    def test_shared_quota_preserves_history(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'reports') as temp:
            root=Path(temp);old=root/'old';old.mkdir();evidence=old/'proof.bin';evidence.write_bytes(b'x'*4096)
            logger=TraceLogger(root/'new',4,max_shared_bytes=1024,shared_roots=[old],disk_usage=lambda p:type('Usage',(),{'free':100*GIB})())
            with self.assertRaises(OSError):logger.start()
            self.assertEqual(evidence.stat().st_size,4096)
    def test_physical_raw_once_roundtrip_and_separate_metadata(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'reports') as temp:
            logger=TraceLogger(temp,4,disk_usage=lambda p:type('Usage',(),{'free':100*GIB})()).start()
            chunks=[(n%2,n%3==0,bytes((n,))+b'FD2C') for n in range(100)]
            for route,direction,raw in chunks:self.assertTrue(logger.io(route,int(direction),raw))
            logger.event('write_unknown',request_id=17,expected_bytes=48)
            logger.submit(compact(),decode(compact()));path=logger.close();self.assertFalse(logger.error)
            recovered=[(route,bool(direction),raw) for _,_,route,direction,raw in iter_chunks(path)]
            self.assertEqual(recovered,chunks)
            with zipfile.ZipFile(path) as z:
                events=[json.loads(line) for line in z.read('events.jsonl').splitlines()]
                self.assertEqual(events[-2]['event'],'write_unknown');self.assertNotIn('raw',events[-1])
    def test_unknown_outcome_blocks_mutations_but_allows_stop(self):
        owner=SerialOwner(4,'OFFLINE',ROOT/'unused');owner.state.connected=True;owner.state.authenticated=True;owner.state.error='unknown write'
        with self.assertRaises(RuntimeError):owner.command(POSITION,payload=b'\x01')
        owner.command(STOP);self.assertEqual(owner.queue.qsize(),1)
    def test_stop_retires_queued_start_and_old_position(self):
        owner=SerialOwner(4,'OFFLINE',ROOT/'unused');owner.state.connected=True;owner.state.authenticated=True
        raw=f.tlm(10,4);owner._receive(raw,decode(raw))
        owner.command(START_SWING);owner.command(POSITION,payload=b'\x06');owner.command(STOP)
        remaining=[]
        while not owner.queue.empty():remaining.append(owner.queue.get_nowait()[2])
        self.assertEqual(remaining,[STOP])
    def test_status_alone_cannot_enable_a_new_motion(self):
        owner=SerialOwner(4,'OFFLINE',ROOT/'unused');owner.state.connected=True;owner.state.authenticated=True
        with self.assertRaises(RuntimeError):owner.command(POSITION,payload=b'\x01')
        owner.command(STOP)
    def test_keyboard_uses_ble_while_heartbeat_uses_usb(self):
        from dual_transport import DuplexPort
        class Port:
            def __init__(self):self.writes=[]
            def write(self,raw):self.writes.append(bytes(raw));return len(raw)
        duplex=DuplexPort.__new__(DuplexPort);duplex.usb=Port();duplex.ble=Port();duplex.route_errors={};duplex.logger=None;duplex.event_sink=None
        owner=SerialOwner(4,'OFFLINE',ROOT/'unused');owner._serial=duplex;owner._sending_source='KEYBOARD/L2/POSITION_6'
        owner._send(POSITION,payload=b'\x06');self.assertEqual(len(duplex.usb.writes),0);self.assertEqual(len(duplex.ble.writes),1);self.assertEqual(duplex.ble.writes[-1][44],1)
        owner._send(STATUS,internal=True);self.assertEqual(len(duplex.usb.writes),1);self.assertEqual(len(duplex.ble.writes),1)
        owner._sending_source='GUI';owner._send(STOP);self.assertEqual(duplex.usb.writes[-1],duplex.ble.writes[-1]);self.assertEqual(duplex.ble.writes[-1][44],2)
    def test_compact_epoch_handoff_and_board_reset_are_distinct(self):
        owner=SerialOwner(5,'OFFLINE',ROOT/'unused');owner.state.connected=True;owner.state.authenticated=True
        raw=compact(10,100,5);item=decode(raw);item['mode']=5;owner._receive(raw,item)
        raw=compact(11,1,5);owner._receive(raw,decode(raw));self.assertEqual(owner.state.last_compact['control_epoch'],1);self.assertEqual(len(owner.state.compact_history),1)
        raw=compact(1,1,5);owner._receive(raw,decode(raw));self.assertFalse(owner.state.authenticated);self.assertEqual(owner.state.board_resets,1);self.assertIn('重置',owner.state.error)
    def test_new_session_ids_do_not_inherit_old_global_highwater(self):
        owner=SerialOwner(4,'OFFLINE',ROOT/'unused');owner.state.connected=True;owner.next_id=2;owner.pending[1]=(STATUS,0,b'',time.monotonic())
        raw=f.ack(STATUS,1,4,highwater=0xffffffff);owner._receive(raw,decode(raw));self.assertEqual(owner.next_id,2);self.assertTrue(owner.state.authenticated)
    def test_keymap_case_is_normalized_and_layer_entries_preserved(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'reports') as temp:
            path=Path(temp)/'keys.json';mapping={layer:{key.lower():value for key,value in entries.items()} for layer,entries in DEFAULT.items()};path.write_text(json.dumps(mapping))
            self.assertEqual(load_mapping(path),DEFAULT)
            mapping['2']['g']='STOP';path.write_text(json.dumps(mapping))
            with self.assertRaises(ValueError):load_mapping(path)
class BLEWrites(unittest.IsolatedAsyncioTestCase):
    async def test_startup_cache_is_logged_once_but_not_admitted(self):
        from ble_transport import BLEPort
        from unittest.mock import patch
        import threading
        port=BLEPort.__new__(BLEPort);port.lock=threading.Lock();port.buffer=bytearray();port.synchronizing=True;port.startup_bytes=0;port.error=None;port.closed=False;port.stop=threading.Event()
        chunks=[];events=[];delays=[];port.io_sink=lambda *args:chunks.append(args);port.event_sink=lambda *args,**fields:events.append((args,fields))
        async def wait(seconds):delays.append(seconds);port._notification(None,b'old-half-frame')
        with patch('ble_transport.asyncio.sleep',wait):await port._startup_sync()
        port._notification(None,b'fresh')
        self.assertEqual(delays,[2.0]);self.assertEqual(port.startup_bytes,14);self.assertEqual(port.read(100),b'fresh')
        self.assertEqual(chunks,[(1,0,b'old-half-frame'),(1,0,b'fresh')]);self.assertEqual(len(events),2)
    async def test_stop_has_reserved_queue_capacity(self):
        from ble_transport import BLEPort
        import threading
        port=BLEPort.__new__(BLEPort);port.lock=threading.Lock();port.write_lock=asyncio.Lock();port.pending_writes=16;port.error=None;port.closed=False;port.stop=threading.Event();port.writer='mock';port.response=False;port.io_sink=None;port.event_sink=None;port.command_generation=0;port.loop=asyncio.get_running_loop()
        calls=[]
        class Client:
            async def write_gatt_char(self,writer,raw,response):calls.append(bytes(raw))
        port.client=Client();raw=request(STOP,2,version=4,session=9,event=2)
        self.assertEqual(port.write(raw),48);await asyncio.sleep(.02);self.assertEqual(b''.join(calls),raw);self.assertEqual(port.command_generation,1)
    async def test_stop_generation_cancels_older_unsent_frame(self):
        from ble_transport import BLEPort
        import threading
        port=BLEPort.__new__(BLEPort);port.lock=threading.Lock();port.write_lock=asyncio.Lock();port.pending_writes=2;port.error=None;port.closed=False;port.stop=threading.Event();port.writer='mock';port.response=False;port.io_sink=None;port.event_sink=None;port.command_generation=1
        calls=[]
        class Client:
            async def write_gatt_char(self,writer,raw,response):calls.append(bytes(raw))
        port.client=Client()
        old=request(POSITION,1,payload=b'\x06',version=4,session=9,event=1);stop=request(STOP,2,version=4,session=9,event=2)
        self.assertEqual(await port._write(old,0),0);self.assertEqual(await port._write(stop,1),48);self.assertEqual(b''.join(calls),stop);self.assertEqual(port.pending_writes,0)
    async def test_48byte_frames_are_serialized_20_20_8(self):
        from ble_transport import BLEPort
        import threading
        port=BLEPort.__new__(BLEPort);port.lock=threading.Lock();port.write_lock=asyncio.Lock();port.pending_writes=2;port.error=None;port.closed=False;port.stop=threading.Event();port.writer='mock';port.response=False
        writes=[];events=[];io=[]
        class Client:
            async def write_gatt_char(self,writer,raw,response):await asyncio.sleep(.002);writes.append(bytes(raw))
        port.client=Client();port.io_sink=lambda *args:io.append(args);port.event_sink=lambda *args,**kw:events.append((args,kw))
        a=request(POSITION,1,payload=b'\x01',version=4,session=9,event=1)
        b=request(POSITION,2,payload=b'\x06',version=4,session=9,event=2)
        await asyncio.gather(port._write(a),port._write(b))
        self.assertEqual(list(map(len,writes)),[20,20,8,20,20,8]);self.assertEqual(b''.join(writes),a+b);self.assertEqual(len(io),6);self.assertEqual(port.pending_writes,0)
    async def test_unknown_fragment_is_never_retried(self):
        from ble_transport import BLEPort
        import threading
        port=BLEPort.__new__(BLEPort);port.lock=threading.Lock();port.write_lock=asyncio.Lock();port.pending_writes=1;port.error=None;port.closed=False;port.stop=threading.Event();port.writer='mock';port.response=False;port.io_sink=None;port.event_sink=None
        calls=[]
        class Client:
            async def write_gatt_char(self,writer,raw,response):
                calls.append(bytes(raw))
                if len(calls)==2:await asyncio.sleep(1)
        port.client=Client()
        with self.assertRaises(TimeoutError):await port._write(request(STOP,1,version=4,session=9,event=1))
        self.assertEqual(len(calls),2);self.assertTrue(port.stop.is_set());self.assertIn('unknown',port.error)
if __name__=='__main__':unittest.main()
