"""Fake transports exercise one real DualOwner control thread; no hardware."""
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
import collections,importlib.abc,json,struct,sys,tempfile,threading,time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
attempts=[]
class BlockHardware(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'serial','bleak','winrt'}:
            attempts.append(fullname);raise RuntimeError('Offline check forbids '+fullname)
sys.meta_path.insert(0,BlockHardware())
from dual_client import DualOwner
import protocol as p
checks=[]
def check(name,value):
    assert value,name;checks.append(name)
def wait_until(fn,timeout=2):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if fn():return True
        time.sleep(.005)
    return False
def ack(rid,opcode,*,mode=1,status=0,flags=1):
    b=bytearray(80);b[:4]=b'FD2A';b[4:8]=bytes((4,opcode,status,4));struct.pack_into('<I',b,8,rid)
    struct.pack_into('<6f',b,24,*p.DEFAULT_GAINS);b[58]=mode;struct.pack_into('<I',b,60,flags);struct.pack_into('<I',b,72,0x5634523A)
    return p.seal(b)
def compact(seq,ms,mode=3):
    b=bytearray(48);b[:4]=b'FD2B';b[4:8]=bytes((4,4,0x91,mode));struct.pack_into('<IIIqii',b,8,seq,ms,ms,30,1,2)
    return p.seal(b)
def diag(ms,session):
    b=bytearray(256);b[:4]=b'FD2D';b[4:8]=bytes((4,4,5,0));struct.pack_into('<IIIIII',b,8,ms//5,ms,p.SCHEMA,0x5634523A,ms,ms)
    struct.pack_into('<II',b,80,ms,ms);struct.pack_into('<I',b,164,session);struct.pack_into('<I',b,216,ms)
    return p.seal(b)
class Logger:
    def __init__(self,*args):self.error='';self.written=0;self.archive_path=None;self.partial_path=None
    def start(self):return self
    def submit(self,raw,item):self.written+=1;return True
    def close(self):pass
class Model:
    def __init__(self):self.lock=threading.RLock();self.session=None;self.frames=[];self.connections={};self.mode=1;self.hold_mutations=False;self.held=[]
    def written(self,name,raw,conn):
        with self.lock:
            rid=struct.unpack_from('<I',raw,8)[0];session,event=struct.unpack_from('<II',raw,36);opcode=raw[5]
            self.frames.append(dict(name=name,rid=rid,event=event,session=session,opcode=opcode,raw=raw,time=time.monotonic()))
            status=0
            if opcode==p.HELLO:self.session=session
            elif opcode not in (p.STOP,p.ESTOP) and session!=self.session:status=4
            if self.hold_mutations and opcode not in p.SAFE:self.held.append((conn,ack(rid,opcode,mode=self.mode,status=status)))
            else:conn.inject(ack(rid,opcode,mode=self.mode,status=status))
    def release(self):
        with self.lock:
            held,self.held=self.held,[]
            for conn,raw in held:conn.inject(raw)
class Connection:
    records_physical_io=True
    def __init__(self,name,model,*,io_sink=None,event_sink=None,baudrate=230400):
        self.name=name;self.model=model;self.io_sink=io_sink;self.event_sink=event_sink;self.buffer=bytearray();self.lock=threading.Lock();self.closed=False
        self.async_write=name=='ble';self.fail_write=False;self.fail_read=False;self.metadata={'fake':True,'transport':name,'baudrate':baudrate if name=='usb' else None};model.connections[name]=self
    def inject(self,raw):
        with self.lock:self.buffer.extend(raw)
        if self.io_sink:self.io_sink(0 if self.name=='usb' else 1,0,raw)
    def read(self,n):
        if self.fail_read:raise OSError('fake side disconnected')
        with self.lock:data=bytes(self.buffer[:n]);del self.buffer[:n];return data
    def write(self,raw):
        if self.fail_write:raise OSError('fake unknown write')
        rid=struct.unpack_from('<I',raw,8)[0]
        if self.io_sink:self.io_sink(0 if self.name=='usb' else 1,1,raw)
        if self.event_sink:
            if self.name=='ble':
                for offset,length in ((0,20),(20,20),(40,8)):self.event_sink('physical_write',route=1,request_id=rid,offset=offset,bytes=length)
            else:self.event_sink('physical_write',route=0,bytes=48)
        self.model.written(self.name,raw,self);return len(raw)
    def close(self):self.closed=True
def factory(name,model,gate=None):
    def make(address,**kwargs):
        if gate is not None:gate.wait(2)
        return Connection(name,model,**kwargs)
    return make
@contextmanager
def lease():yield

with tempfile.TemporaryDirectory(prefix='dual_owner_',dir=ROOT/'tests') as temp:
    model=Model();owner=DualOwner('AA:BB:CC:DD:EE:FF',temp,transport='usb',port='COM5',usb_factory=factory('usb',model),ble_factory=factory('ble',model),logger_factory=Logger,lease_factory=lease)
    # Explicit mirror-order and duplicate telemetry checks without starting a thread.
    for name in ('usb','ble'):
        owner.links[name]=owner._new_link(name,Connection(name,model));owner.links[name]['ready']=True
    owner.state.connected=True;owner.state.authenticated=True;owner.identity_ack=True
    for name,seq,ms in (('usb',100,1000),('ble',99,995),('usb',101,1005),('ble',100,1000)):
        raw=compact(seq,ms);owner._receive_route(name,raw,p.decode(raw))
    check('USB100 BLE99 USB101 BLE100 never falsely resets',not owner.state.error and owner.state.latest['sequence']==101 and owner.reordered_frames==2)
    raw=compact(101,1005);owner._receive_route('ble',raw,p.decode(raw))
    check('same compact broadcast is deduplicated',owner.duplicate_frames==1 and len(owner.state.history)==2)
    for name,ms in (('usb',2000),('ble',1990),('usb',2010),('ble',2000),('ble',2010)):
        raw=diag(ms,owner.session);owner._receive_route(name,raw,p.decode(raw))
    check('older and duplicate D mirrors never falsely reset',not owner.state.error and owner.state.diagnostic['board_ms']==2010)
    owner.pending[9]=(p.STATUS,0,b'',time.monotonic());owner.pending_routes[9]={'ble'}
    raw=ack(9,p.STATUS);owner._receive_route('usb',raw,p.decode(raw))
    check('ACK arriving on wrong route cannot complete request',9 in owner.pending)
    owner._receive_route('ble',raw,p.decode(raw));check('ACK on expected route completes',9 not in owner.pending)
    owner.pending[10]=(p.POSITION,0,b'\x01',time.monotonic());owner.pending_routes[10]={'ble'}
    owner.next_id=11;rid=owner._send(p.STOP)
    stop_frames=[f for f in model.frames if f['opcode']==p.STOP]
    check('STOP USB first and BLE mirror have identical logical id',[x['name'] for x in stop_frames]==['usb','ble'] and len({(x['rid'],x['event'],x['session']) for x in stop_frames})==1)
    check('STOP retires unsent or unknown older mutation without replay',10 not in owner.pending and owner.state.retired_actions[-1]['request_id']==10)
    check('only route byte and CRC differ in STOP mirror',all(a==b for i,(a,b) in enumerate(zip(stop_frames[0]['raw'],stop_frames[1]['raw'])) if i not in (44,46,47)))
    owner.links['usb']['connection'].fail_write=True
    previous=len(model.frames);owner._send(p.STOP)
    check('failed USB stop still submits same-event emergency BLE mirror',len(model.frames)==previous+1 and model.frames[-1]['name']=='ble' and model.frames[-1]['opcode']==p.STOP)
    owner.links['usb']['connection'].fail_write=False
    owner.links['usb']['last_rx']=time.monotonic();owner.links['ble']['last_rx']=time.monotonic()-1
    owner._health(time.monotonic())
    check('stale BLE side route never locks healthy USB primary',not owner.links['ble']['ready'] and owner.state.authenticated and not owner.state.error)
    for name,seq,ms in (('usb',105,1025),('ble',102,1010)):
        raw=compact(seq,ms);owner._receive_route(name,raw,p.decode(raw))
    owner._publish_routes()
    check('route sequence gaps tracked independently of merged mirrors',owner.state.routes['usb']['sequence_gaps']==3 and owner.state.routes['ble']['sequence_gaps']==0)
    owner.state.diagnostic=None
    for link in owner.links.values():link['last_d']=None;link['ready']=True
    for name,ms in (('usb',0xfffffff0),('ble',0xffffffe0),('usb',0x10)):
        raw=diag(ms,owner.session);owner._receive_route(name,raw,p.decode(raw))
    check('D timestamp wrap still advances globally without false reset',owner.state.diagnostic['board_ms']==0x10 and not owner.state.error)
    custom=DualOwner('AA:BB:CC:DD:EE:FF',temp,transport='usb',port='COM5',baudrate=115200,usb_factory=factory('usb',model),ble_factory=factory('ble',model),logger_factory=Logger,lease_factory=lease)
    custom_usb=custom._make_connection('usb','COM5')
    check('custom USB baud reaches actual connection kwargs',custom_usb.metadata['baudrate']==115200 and custom.state.usb_baudrate==115200)
    custom_usb.close()
    seen=[]
    def spy(address,**kwargs):seen.append(kwargs);return Connection('ble',model,**kwargs)
    custom.factories['ble']=spy;custom_ble=custom._make_connection('ble',custom.ble_address)
    check('USB baud never passed to BLE factory','baudrate' not in seen[0])
    custom_ble.close()

    # Actual owner thread plus slow asynchronous BLE constructor.
    model=Model();gate=threading.Event()
    owner=DualOwner('AA:BB:CC:DD:EE:FF',temp,transport='usb',port='COM5',usb_factory=factory('usb',model),ble_factory=factory('ble',model,gate),logger_factory=Logger,lease_factory=lease).start()
    try:
        check('USB primary gets normal ACK',wait_until(lambda:owner.state.snapshot()['authenticated']))
        owner.attach_ble();before=sum(x['name']=='usb' and x['opcode']==p.STATUS for x in model.frames)
        time.sleep(.35)
        after=sum(x['name']=='usb' and x['opcode']==p.STATUS for x in model.frames)
        check('slow BLE initialization does not block USB heartbeat',after-before>=2 and owner.state.snapshot()['authenticated'])
        gate.set();check('BLE confirms same session with STATUS',wait_until(lambda:owner.state.snapshot().get('ble_ready')))
        check('only one HELLO across both routes',sum(x['opcode']==p.HELLO for x in model.frames)==1 and all(x['name']=='usb' for x in model.frames if x['opcode']==p.HELLO))
        check('all physical requests share session and globally unique id',len({x['session'] for x in model.frames})==1 and len({x['rid'] for x in model.frames})==len(model.frames))
        model.mode=3
        raw=compact(300,3000);model.connections['usb'].inject(raw);model.connections['ble'].inject(raw)
        check('fresh primary control sample received',wait_until(lambda:owner.state.snapshot()['control_fresh']))
        model.hold_mutations=True
        owner.command(p.POSITION,payload=b'\x01')
        check('first position uses BLE',wait_until(lambda:any(x['opcode']==p.POSITION and x['name']=='ble' for x in model.frames)))
        owner.command(p.POSITION,payload=b'\x02');time.sleep(.1)
        check('next mutation waits for prior ACK',sum(x['opcode']==p.POSITION for x in model.frames)==1)
        model.hold_mutations=False;model.release()
        # Refresh the control observation while the first position ACK is released.
        model.connections['usb'].inject(compact(301,3005))
        check('queued mutation sent once after ACK',wait_until(lambda:sum(x['opcode']==p.POSITION for x in model.frames)==2))
        check('all key positions use BLE while ready',all(x['name']=='ble' for x in model.frames if x['opcode']==p.POSITION))
        owner.command(p.READ_GAINS,255)
        check('parameter read stays USB',wait_until(lambda:any(x['opcode']==p.READ_GAINS and x['name']=='usb' for x in model.frames)))
        check('pending mutation acknowledged',wait_until(lambda:not any(x[0] not in p.SAFE for x in owner.pending.values())))
        owner.detach_ble();check('BLE detach leaves USB owner alive',wait_until(lambda:not owner.state.snapshot()['ble_connected']) and not owner.closed.is_set() and owner.state.snapshot()['authenticated'])
        model.connections['usb'].inject(compact(302,3010));time.sleep(.02)
        owner.command(p.POSITION,payload=b'\x03')
        check('USB-only six-position command remains usable',wait_until(lambda:any(x['opcode']==p.POSITION and x['name']=='usb' for x in model.frames)))
    finally:
        gate.set();owner.stop(wait=True)
    check('owner closes both routes normally',owner.closed.is_set() and model.connections['usb'].closed and model.connections['ble'].closed)

    # A failed BLE action is never silently retried over USB.
    model=Model();owner=DualOwner('AA:BB:CC:DD:EE:FF',temp,transport='usb',port='COM5',usb_factory=factory('usb',model),ble_factory=factory('ble',model),logger_factory=Logger,lease_factory=lease).start()
    try:
        check('second owner USB auth',wait_until(lambda:owner.state.snapshot()['authenticated']))
        owner.attach_ble();check('second BLE attaches',wait_until(lambda:owner.state.snapshot()['ble_ready']))
        model.mode=3;model.connections['usb'].inject(compact(10,100));check('second owner fresh control',wait_until(lambda:owner.state.snapshot()['control_fresh']))
        model.connections['ble'].fail_write=True
        owner.command(p.POSITION,payload=b'\x04')
        check('unknown BLE mutation exposes error',wait_until(lambda:bool(owner.state.snapshot()['error'])))
        check('unknown BLE position never replayed over USB',not any(x['name']=='usb' and x['opcode']==p.POSITION for x in model.frames))
    finally:owner.stop(wait=True)
    check('no forbidden hardware imports',not attempts)

report=dict(status='PASS',checks=len(checks),passed=checks,hardware_connections=0,hardware_commands=0,device_import_attempts=attempts,
            scope='Real DualOwner thread with fake UART/BLE and pure per-route duplicate/ACK checks; no plant-performance qualification')
(ROOT/'tests/DUAL_OWNER_OFFLINE_CHECKS.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='passed'},ensure_ascii=False))
