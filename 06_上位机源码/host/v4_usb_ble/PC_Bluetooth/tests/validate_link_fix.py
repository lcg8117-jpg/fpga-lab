from pathlib import Path
import importlib.abc,json,struct,sys,tempfile,time,types,zipfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'bleak','winrt','serial','bluetooth'}:raise AssertionError('Unexpected hardware import '+fullname)
sys.meta_path.insert(0,Block())
import client,protocol,link_diagnostics,app
checks=[]
def check(name,value):
    assert value,name
    checks.append(name)
clock=types.SimpleNamespace(value=0.,monotonic=lambda:clock.value)
original_time=client.time;client.time=clock
source=ROOT.parent/'reports/peer_log_20261103_182421/ForestD1_V4_20261103_182421_RECOVERED_PREFIX.zip'
with zipfile.ZipFile(source) as archive:records=archive.read('records.bin')
with tempfile.TemporaryDirectory(prefix='link_fix_',dir=ROOT/'tests') as temp:
    owner=client.BLEOwner('OFFLINE_NEVER_CONNECT',temp);owner.state.connected=True
    i=0;count=0;first=None
    while i+18<=len(records):
        wall,mono,n=struct.unpack_from('<QQH',records,i);raw=records[i+18:i+18+n];i+=18+n
        clock.value=mono/1e9
        if first is None:first=clock.value
        for frame,item in owner.parser.feed(raw):
            if item['kind']=='ack':owner.pending[item['request_id']]=(item['opcode'],0,b'',clock.value-.01)
            owner._receive(frame,item)
        owner._check_link_health(clock.value,first+3)
        count+=1
    check('peer log recovered 8863 complete records replayed',count==8863)
    check('peer ACKs establish usable communication without long D',owner.identity_ack and not owner.identity_d and owner.state.authenticated)
    check('no D gate or firmware-check error during real accepted-frame replay',not owner.state.error and owner.state.diagnostic_warning)
    check('correct actual firmware observed from ACK',owner.state.observed_firmware==0x56345239)
    check('fault01 stays visible and blocks RUN',owner.state.latest['fault']==1 and owner.state.latest['mode']==4)
    try:owner.command(protocol.RUN)
    except RuntimeError:pass
    else:raise AssertionError('Fault stop was bypassed')
    owner.command(protocol.CLEAR)
    check('CLEAR can be sent on normal ACK communication without D',any(e[2]==protocol.CLEAR for e in owner.queue.queue))
    owner.port=types.SimpleNamespace(async_write=True)
    entry=(protocol.STATUS,0,b'',clock.value-2)
    check('queue time alone is not reported as physical BLE completion',owner._response_sent_at(999,entry) is None)
    owner._transport_event('physical_write',route=1,request_id=999,offset=0,bytes=20)
    check('first fragment does not start response timer',owner._response_sent_at(999,entry) is None)
    owner._transport_event('physical_write',route=1,request_id=999,offset=40,bytes=8)
    check('one-second response timer starts at final physical fragment',owner._response_sent_at(999,entry)==clock.value)
    check('link parameters are rendered from actual negotiated report',
          'MTU 247' in app.link_summary({'connection_metadata':{'connection_tuning':{'after_startup_sync':{'mtu_size':247,'parameters':{'connection_interval_ms':15.0}}}}}))
    capture=link_diagnostics.LinkDiagnostics(temp,max_bytes=4096)
    for number in range(100):capture.io(1,0,bytes([number])*80)
    capture.io(1,1,b'hello');capture.event('test',raw=False)
    capture.export_on_first_error({'error':'test snapshot','hardware_actions':0})
    capture.thread.join(3)
    check('first failure produces closed readable diagnostics ZIP without disconnect',Path(capture.archive_path).is_file() and not capture.error)
    with zipfile.ZipFile(capture.archive_path) as archive:
        check('diagnostic archive CRC valid',archive.testzip() is None)
        metadata=json.loads(archive.read('diagnostics.json'));raw=archive.read('io.bin')
    check('pre-parser capture bound and explicit overflow counters',len(raw)<=4096 and metadata['dropped_due_to_capture_budget']['records']>0)
    blocks=[];i=0
    while i+link_diagnostics.IO_HEADER.size<=len(raw):
        wall,mono,route,direction,n=link_diagnostics.IO_HEADER.unpack_from(raw,i)
        payload=raw[i+link_diagnostics.IO_HEADER.size:i+link_diagnostics.IO_HEADER.size+n]
        blocks.append((direction,payload));i+=link_diagnostics.IO_HEADER.size+n
    check('startup and recent physical byte boundaries preserved',blocks[0]==(0,bytes([0])*80) and blocks[-2]==(0,bytes([99])*80) and blocks[-1]==(1,b'hello'))
    capture.close({'finished':True})
    check('final export remains valid',not capture.error)
    import serial_transport
    class FakeSerial:
        def __init__(self,**kwargs):self.writes=[];self.in_waiting=0
        def write(self,raw):self.writes.append(bytes(raw));return len(raw)
        def read(self,n):return b''
        def close(self):pass
    usb_owner=client.BLEOwner('FAKECOM',temp,transport='usb')
    usb_owner.port=serial_transport.SerialPort('FAKECOM',serial_factory=FakeSerial,
        io_sink=usb_owner._physical_io,event_sink=usb_owner._transport_event)
    usb_owner._send(protocol.HELLO)
    wire=usb_owner.port.port.writes[0]
    check('actual owner to serial path sends exactly one route0 CRC-valid HELLO',
          len(wire)==48 and wire[44]==0 and wire[5]==protocol.HELLO and protocol.crc16(wire[:-2])==struct.unpack_from('<H',wire,46)[0])
    check('USB synchronous write callback does not use BLE request-fragment timer',not usb_owner.physical_sent_times)
    modified=bytearray(next(raw for raw,item in owner.parser.feed(b'') if False)) if False else bytearray()
    with zipfile.ZipFile(source) as archive:
        capture_bytes=archive.read('records.bin')
    cursor=0;ack_frame=None
    while cursor+18<=len(capture_bytes):
        _,_,size=struct.unpack_from('<QQH',capture_bytes,cursor)
        candidate=capture_bytes[cursor+18:cursor+18+size];cursor+=18+size
        if candidate[:4]==b'FD2A':ack_frame=bytearray(candidate);break
    assert ack_frame is not None
    struct.pack_into('<I',ack_frame,72,0x5634523A)
    check('USB-restored communication firmware 3A reports its actual identity',protocol.decode(protocol.seal(ack_frame))['firmware_id']==0x5634523A)
    struct.pack_into('<I',ack_frame,72,0x56345237)
    try:protocol.decode(protocol.seal(ack_frame))
    except ValueError:pass
    else:raise AssertionError('Incompatible firmware decoded')
    check('unrelated firmware remains rejected',True)
    usb_owner.port.close()
client.time=original_time
report=dict(status='PASS',checks=len(checks),passed=checks,replayed_peer_records=count,
    replay_limit='Accepted-frame RX replay; original TX queue timestamps were not in the peer attachment. Pending ACK fixtures are matched explicitly; no claim of reconstructing original TX timing.',
    hardware_connections=0,hardware_commands=0)
(ROOT/'LINK_FIX_INTEGRATION_CHECKS.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='passed'},ensure_ascii=False))
