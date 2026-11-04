"""Real DualOwner threads, deliberately faulty fake routes, no hardware imports.

Assertions use public commands/snapshots and recorded wire frames; no private
owner methods, queues, pending maps or quarantine helpers are called or edited.
Run directly to write SECONDARY_FAILURE_ISOLATION_CHECKS.json beside this file.
"""
from contextlib import contextmanager
import hashlib
import importlib.abc
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
REPORT = Path(__file__).with_name('SECONDARY_FAILURE_ISOLATION_CHECKS.json')
HARDWARE_MODULES = {'serial','bleak','winrt','vendor_serial','serial_transport',
                    'ble_transport','bt10_windows_select','hardware_mutex'}
checks = []
observations = {}
device_import_attempts = []
p = None
DualOwner = None


class BlockHardware(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in HARDWARE_MODULES:
            device_import_attempts.append(fullname)
            raise RuntimeError('Offline fault regression forbids device import: '+fullname)


def check(name, value):
    if not value:
        raise AssertionError(name)
    checks.append(name)


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.005)
    return bool(predicate())


def ack(rid, opcode, *, status=0):
    frame = bytearray(80)
    frame[:4] = b'FD2A'
    frame[4:8] = bytes((4,opcode,status,4))
    struct.pack_into('<I',frame,8,rid)
    struct.pack_into('<6f',frame,24,*p.DEFAULT_GAINS)
    frame[58] = 3
    struct.pack_into('<I',frame,60,1)
    struct.pack_into('<I',frame,72,0x5634523A)
    return p.seal(frame)


def compact(sequence, board_ms):
    frame = bytearray(48)
    frame[:4] = b'FD2B'
    frame[4:8] = bytes((4,4,0x91,3))
    struct.pack_into('<IIIqii',frame,8,sequence,board_ms,board_ms,30,1,2)
    return p.seal(frame)


class Logger:
    def __init__(self,*args):
        self.error = ''
        self.written = 0
        self.archive_path = None
        self.partial_path = None

    def start(self):
        return self

    def submit(self,raw,item):
        self.written += 1
        return True

    def close(self):
        pass


class Model:
    def __init__(self):
        self.lock = threading.RLock()
        self.connections = {}
        self.frames = []
        self.held = []
        self.hold_ack = set()
        self.suppress_rx = set()
        self.session = None

    def physical_frames(self, *, opcode=None):
        with self.lock:
            return [dict(frame) for frame in self.frames
                    if opcode is None or frame['opcode'] == opcode]

    def deliver(self,name,raw):
        with self.lock:
            connection = self.connections.get(name)
            suppressed = name in self.suppress_rx
        if connection is not None and not suppressed and not connection.closed:
            connection.inject(raw)

    def written(self,name,raw,connection):
        if len(raw) != 48 or raw[:4] != b'FD2C' or p.crc16(raw[:-2]) != struct.unpack_from('<H',raw,46)[0]:
            raise AssertionError('Owner emitted an invalid physical request')
        rid = struct.unpack_from('<I',raw,8)[0]
        session,event = struct.unpack_from('<II',raw,36)
        opcode = raw[5]
        with self.lock:
            self.frames.append(dict(route=name,rid=rid,event=event,session=session,
                                    opcode=opcode,payload=raw[12:12+raw[7]].hex(),
                                    route_byte=raw[44],time=time.monotonic(),
                                    owner_thread=threading.get_ident(),raw_hex=raw.hex()))
            status = 0
            if opcode == p.HELLO:
                self.session = session
            elif opcode not in (p.STOP,p.ESTOP) and session != self.session:
                status = 4
            response = ack(rid,opcode,status=status)
            if (name,opcode) in self.hold_ack:
                self.held.append((name,response))
                return
        self.deliver(name,response)

    def release_old_acks(self):
        with self.lock:
            held,self.held = self.held,[]
        for name,raw in held:
            self.deliver(name,raw)
        return len(held)


class Connection:
    records_physical_io = True

    def __init__(self,name,model,*,io_sink=None,event_sink=None,baudrate=230400):
        self.name = name
        self.model = model
        self.io_sink = io_sink
        self.event_sink = event_sink
        self.async_write = name == 'ble'
        self.metadata = {'fake':True,'transport':name,'baudrate':baudrate if name == 'usb' else None}
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.closed = False
        with model.lock:
            model.connections[name] = self

    def inject(self,raw):
        with self.lock:
            self.buffer.extend(raw)
        if self.io_sink:
            self.io_sink(0 if self.name == 'usb' else 1,0,raw)

    def read(self,n):
        with self.lock:
            raw = bytes(self.buffer[:n])
            del self.buffer[:n]
        return raw

    def write(self,raw):
        rid = struct.unpack_from('<I',raw,8)[0]
        if self.io_sink:
            self.io_sink(0 if self.name == 'usb' else 1,1,raw)
        if self.event_sink:
            if self.name == 'ble':
                for offset,length in ((0,20),(20,20),(40,8)):
                    self.event_sink('physical_write',route=1,request_id=rid,offset=offset,bytes=length)
            else:
                self.event_sink('physical_write',route=0,bytes=48)
        self.model.written(self.name,raw,self)
        return len(raw)

    def close(self):
        self.closed = True


class FreshSamples:
    def __init__(self,model,routes):
        self.model = model
        self.routes = tuple(routes)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run,name='FakeFreshTelemetry',daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        sequence = 100
        while not self.stop_event.is_set():
            raw = compact(sequence,sequence*5)
            for route in self.routes:
                self.model.deliver(route,raw)
            sequence += 1
            self.stop_event.wait(.04)

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=1)


@contextmanager
def fake_lease():
    yield


@contextmanager
def running_owner(directory, *, transport='usb', secondary=False, samples=('usb',)):
    model = Model()
    def usb_factory(address,**kwargs):
        return Connection('usb',model,**kwargs)
    def ble_factory(address,**kwargs):
        return Connection('ble',model,**kwargs)
    owner = DualOwner('AA:BB:CC:DD:EE:FF',directory,transport=transport,port='COM_FAKE',
                      usb_factory=usb_factory,ble_factory=ble_factory,
                      logger_factory=Logger,lease_factory=fake_lease).start()
    producer = FreshSamples(model,samples)
    try:
        check(f'{transport} owner authenticates using actual fake ACKs',
              wait_until(lambda:owner.state.snapshot()['authenticated']))
        if secondary:
            owner.attach_ble()
            check('BLE side route initially confirms actual STATUS',
                  wait_until(lambda:owner.state.snapshot()['ble_ready']))
        producer.start()
        check(f'{transport} owner has fresh balance samples',
              wait_until(lambda:owner.state.snapshot()['control_fresh']))
        yield owner,model
    finally:
        producer.stop()
        owner.stop(wait=True)
        check(f'{transport} owner closes after fake regression',
              not owner.state.snapshot()['connected'] and all(c.closed for c in model.connections.values()))


def no_global_error_for(owner,seconds):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        snapshot = owner.state.snapshot()
        if snapshot['error'] or not snapshot['authenticated'] or not snapshot['control_fresh']:
            return False
        time.sleep(.005)
    return True


def quarantined(snapshot):
    return not snapshot['ble_ready'] and snapshot['routes']['ble'].get('quarantined') is True


def secondary_timeout_case(directory):
    with running_owner(directory,secondary=True,samples=('usb',)) as (owner,model):
        baseline = owner.state.snapshot()
        with model.lock:
            model.hold_ack.add(('ble',p.POSITION))
        owner.command(p.POSITION,payload=b'\x01')
        check('first explicit position physically uses BLE',
              wait_until(lambda:len(model.physical_frames(opcode=p.POSITION)) == 1))
        old = model.physical_frames(opcode=p.POSITION)[0]
        check('first position carries BLE route and its own event',
              old['route'] == 'ble' and old['route_byte'] == 1 and old['event'] == old['rid'])
        owner.command(p.POSITION,payload=b'\x02')
        check('queued old BLE position waits rather than overtaking unknown action',
              no_global_error_for(owner,.08) and len(model.physical_frames(opcode=p.POSITION)) == 1)
        corrupt = bytearray(ack(old['rid'],p.POSITION))
        corrupt[-1] ^= 1
        model.deliver('ble',bytes(corrupt))
        check('BLE CRC rejection is observed through public route counters',
              wait_until(lambda:owner.state.snapshot()['routes']['ble']['crc_errors'] > baseline['routes']['ble']['crc_errors']))
        # Keep real time running beyond the unacknowledged physical write's
        # one-second limit while real owner USB heartbeats/telemetry continue.
        remaining = max(0,old['time']+1.20-time.monotonic())
        check('BLE ACK withheld beyond one second plus bad CRC never creates global error',
              no_global_error_for(owner,remaining))
        snapshot = owner.state.snapshot()
        check('unknown BLE position quarantines only its secondary route',quarantined(snapshot))
        check('unknown old action is explicitly retired with unknown outcome and no replay',
              any(a.get('request_id') == old['rid'] and a.get('outcome') == 'unknown' and a.get('replayed') is False
                  for a in snapshot['retired_actions']))
        check('secondary ACK timeout is counted without weakening primary freshness',
              snapshot['timeouts'] > baseline['timeouts'] and snapshot['authenticated'] and snapshot['control_fresh'])
        check('neither timed-out action nor unsent older queue entry is replayed or switched to USB',
              len(model.physical_frames(opcode=p.POSITION)) == 1)
        owner.command(p.POSITION,payload=b'\x03')
        check('a new human position command is physically submitted',
              wait_until(lambda:len(model.physical_frames(opcode=p.POSITION)) == 2))
        new = model.physical_frames(opcode=p.POSITION)[1]
        check('new explicit position uses USB once with greater request/event and same session',
              new['route'] == 'usb' and new['route_byte'] == 0 and new['rid'] > old['rid'] and
              new['event'] == new['rid'] and new['event'] > old['event'] and
              new['session'] == old['session'] and new['payload'] == '03')
        check('exactly one withheld old ACK is released after the new event',model.release_old_acks() == 1)
        # Cross another BLE heartbeat, so a normal STATUS ACK cannot silently
        # reopen a side route quarantined by an older unknown command either.
        check('USB remains usable after late old ACK and continued BLE STATUS replies',no_global_error_for(owner,.65))
        check('late old ACK and normal READS ACK cannot unlock quarantined BLE',quarantined(owner.state.snapshot()))
        check('late ACK never causes replay of either old payload or another copy of new payload',
              [(f['route'],f['payload']) for f in model.physical_frames(opcode=p.POSITION)] == [('ble','01'),('usb','03')])
        owner.command(p.POSITION,payload=b'\x04')
        check('another later explicit input still chooses USB rather than returning to BLE',
              wait_until(lambda:len(model.physical_frames(opcode=p.POSITION)) == 3) and
              model.physical_frames(opcode=p.POSITION)[2]['route'] == 'usb')
        last = model.physical_frames(opcode=p.POSITION)[2]
        check('later explicit USB position receives its matching ACK',
              wait_until(lambda:(owner.state.snapshot().get('last_usb_ack') or {}).get('request_id') == last['rid']))
        six_inputs = []
        for position in range(1,7):
            before_count = len(model.physical_frames(opcode=p.POSITION))
            owner.command(p.POSITION,payload=bytes((position,)))
            check(f'fresh manual key-equivalent position {position} is sent once',
                  wait_until(lambda:len(model.physical_frames(opcode=p.POSITION)) == before_count+1))
            sent = model.physical_frames(opcode=p.POSITION)[-1]
            check(f'position {position} uses USB with a new monotonic event',
                  sent['route'] == 'usb' and sent['route_byte'] == 0 and
                  sent['payload'] == f'{position:02x}' and sent['rid'] > last['rid'] and
                  sent['event'] == sent['rid'] and sent['session'] == old['session'])
            check(f'position {position} receives its ACK before the next human input',
                  wait_until(lambda:(owner.state.snapshot().get('last_usb_ack') or {}).get('request_id') == sent['rid']) and
                  not owner.state.snapshot()['error'])
            six_inputs.append(sent)
            last = sent
        check('all six sequential human positions remain exactly once on USB with BLE quarantined',
              no_global_error_for(owner,.12) and quarantined(owner.state.snapshot()) and
              len(model.physical_frames(opcode=p.POSITION)) == 9 and
              [f['payload'] for f in six_inputs] == ['01','02','03','04','05','06'])
        action_frames = model.physical_frames(opcode=p.POSITION)
        check('all actual requests are owned by one control thread',
              len({f['owner_thread'] for f in model.physical_frames()}) == 1 and
              action_frames[0]['owner_thread'] != threading.get_ident())
        observations['secondary_timeout_crc_late_ack'] = dict(snapshot=owner.state.snapshot(),positions=action_frames,six_fresh_manual_inputs=six_inputs,
            withheld_seconds_before_new_event=new['time']-old['time'])


def primary_timeout_case(directory,transport):
    with running_owner(directory,transport=transport,samples=(transport,)) as (owner,model):
        with model.lock:
            model.hold_ack.add((transport,p.POSITION))
        owner.command(p.POSITION,payload=b'\x05')
        check(f'{transport} primary mutation reaches physical route exactly once',
              wait_until(lambda:len(model.physical_frames(opcode=p.POSITION)) == 1))
        sent = model.physical_frames(opcode=p.POSITION)[0]
        check(f'{transport} primary unknown mutation remains a global error after one second',
              wait_until(lambda:bool(owner.state.snapshot()['error']),timeout=1.8))
        snapshot = owner.state.snapshot()
        check(f'{transport} primary unknown error identifies an unconfirmed command',
              '未知' in snapshot['error'] and snapshot['timeouts'] >= 1 and time.monotonic()-sent['time'] >= .95)
        rejected = False
        try:
            owner.command(p.POSITION,payload=b'\x06')
        except RuntimeError:
            rejected = True
        check(f'{transport} primary error still rejects further mutating commands',rejected)
        check(f'{transport} primary unknown mutation is never automatically resent',
              len(model.physical_frames(opcode=p.POSITION)) == 1)
        observations[transport+'_primary_unknown'] = dict(snapshot=snapshot,positions=model.physical_frames(opcode=p.POSITION))


def primary_stale_case(directory):
    with running_owner(directory,secondary=True,samples=('usb','ble')) as (owner,model):
        check('primary USB starts with healthy independent RX',owner.state.snapshot()['usb_ready'])
        with model.lock:
            model.suppress_rx.add('usb')
        freeze = time.monotonic()
        time.sleep(.35)
        check('primary RX guard does not expire before 500ms',not owner.state.snapshot()['error'])
        check('USB primary still locks on 500ms silence despite fresh BLE traffic',
              wait_until(lambda:bool(owner.state.snapshot()['error']),timeout=.9))
        snapshot = owner.state.snapshot()
        check('500ms primary stale error still revokes authentication',
              '500ms' in snapshot['error'] and not snapshot['authenticated'] and
              .40 <= time.monotonic()-freeze < 1.20)
        rejected = False
        try:
            owner.command(p.POSITION,payload=b'\x06')
        except RuntimeError:
            rejected = True
        check('fresh BLE traffic cannot authorize a new action after USB becomes stale',rejected)
        observations['usb_primary_stale_with_ble_alive'] = dict(snapshot=snapshot,
            observed_silence_seconds=time.monotonic()-freeze)


def main():
    global p,DualOwner
    started = time.monotonic()
    blocker = BlockHardware()
    report = dict(status='FAIL',hardware_connections=0,hardware_commands=0,
                  scope='Real DualOwner threads with injected fake wire faults; no device or plant qualification')
    source = ROOT/'dual_client.py'
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    sys.path.insert(0,str(ROOT))
    sys.meta_path.insert(0,blocker)
    try:
        check('test starts without imported hardware drivers',not HARDWARE_MODULES.intersection(sys.modules))
        import protocol as protocol_module
        from dual_client import DualOwner as OwnerClass
        p,DualOwner = protocol_module,OwnerClass
        with tempfile.TemporaryDirectory(prefix='secondary_isolation_',dir=ROOT/'tests') as temporary:
            base = Path(temporary)
            secondary_timeout_case(base/'secondary')
            primary_stale_case(base/'stale')
            primary_timeout_case(base/'usb_unknown','usb')
            primary_timeout_case(base/'ble_only_unknown','ble')
        check('no device driver import was attempted',not device_import_attempts)
        check('production dual_client source remained unchanged during the run',
              hashlib.sha256(source.read_bytes()).hexdigest() == source_sha)
        report['status'] = 'PASS'
        return 0
    except BaseException as error:
        report.update(error=repr(error),traceback=traceback.format_exc())
        raise
    finally:
        if blocker in sys.meta_path:
            sys.meta_path.remove(blocker)
        report.update(checks=len(checks),passed=checks,observations=observations,
                      device_import_attempts=device_import_attempts,source_sha256=source_sha,
                      test_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      seconds=round(time.monotonic()-started,3))
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k not in ('passed','observations','traceback')},ensure_ascii=False))


if __name__ == '__main__':
    raise SystemExit(main())
