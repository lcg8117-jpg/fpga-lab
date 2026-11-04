"""USB selection and unknown-write checks with fakes; never open a real port."""
from pathlib import Path
from types import SimpleNamespace
import ast,hashlib,importlib.abc,json,sys,threading

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
before=tuple(threading.enumerate());attempts=[]
class BlockDevices(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'serial','bleak','winrt'}:
            attempts.append(fullname);raise RuntimeError('Offline test forbids '+fullname)
sys.meta_path.insert(0,BlockDevices())
import serial_transport as s
import select_transport as choose
checks=[]
def check(name,value):
    assert value,name;checks.append(name)
def fails(fn):
    try:fn()
    except (ValueError,RuntimeError,OSError):return True
    return False
def usb(name,vid=0x1a86,pid=0x7523):return SimpleNamespace(device=name,vid=vid,pid=pid,description='Fake USB',serial_number='FAKE',manufacturer='Test')
def found(*ports):return lambda:list(ports)
check('module import does not import drivers',not attempts)
check('no ports chooses BLE',choose.choose_transport(list_ports_factory=found())==dict(transport='ble',port=None,choices=[]))
check('Bluetooth virtual COM excluded',choose.choose_transport(list_ports_factory=found(usb('COM8',None)))['transport']=='ble')
check('one true USB chosen over Bluetooth COM',choose.choose_transport(list_ports_factory=found(usb('COM5'),usb('COM8',None)))['port']=='COM5')
check('explicit USB hint case insensitive',choose.choose_transport('com7',list_ports_factory=found(usb('COM5'),usb('COM7')))['port']=='COM7')
check('unknown explicit hint rejected',fails(lambda:choose.choose_transport('COM9',list_ports_factory=found(usb('COM5')))))
check('explicit Bluetooth virtual COM rejected',fails(lambda:choose.choose_transport('COM8',list_ports_factory=found(usb('COM8',None)))))
try:choose.choose_transport(list_ports_factory=found(usb('COM5'),usb('COM7')))
except choose.TransportSelectionError as exc:check('ambiguous USB exposes choices without probing',[x['port'] for x in exc.choices]==['COM5','COM7'])
else:raise AssertionError('Multiple USB ports were guessed')
check('VID zero is still a supplied VID',choose.list_usb_choices(list_ports_factory=found(usb('COM2',0)))[0]['vid']==0)

class FakeSerial:
    def __init__(self,**kwargs):self.open_args=kwargs;self.rx=bytearray(b'abc');self.writes=[];self.closed=0;self.fail_write=False;self.partial=False
    @property
    def in_waiting(self):return len(self.rx)
    def read(self,n):data=bytes(self.rx[:n]);del self.rx[:n];return data
    def write(self,data):
        self.writes.append(bytes(data))
        if self.fail_write:raise OSError('simulated unknown outcome')
        return 7 if self.partial else len(data)
    def close(self):self.closed+=1
def packet(route=0):
    data=bytearray(48);data[:4]=b'FD2C';data[44]=route;return bytes(data)
io=[];events=[]
port=s.SerialPort('COM5',serial_factory=FakeSerial,io_sink=lambda *x:io.append(x),event_sink=lambda name,**x:events.append((name,x)))
check('serial fixed baud and nonblocking read',port.port.open_args['baudrate']==230400 and port.port.open_args['timeout']==0)
check('serial bounded write timeout',port.port.open_args['write_timeout']==.2)
check('read has physical route0 IO',port.read(2)==b'ab' and io[-1]==(0,0,b'ab'))
check('in_waiting and empty read remain nonblocking',port.in_waiting==1 and port.read(2)==b'c' and port.read(2)==b'')
check('write accepts one full USB frame',port.write(packet())==48 and io[-1]==(0,1,packet()) and len(port.port.writes)==1)
check('BLE route not sent over USB',fails(lambda:port.write(packet(1))) and len(port.port.writes)==1)
check('partial request rejected before physical write',fails(lambda:port.write(b'FD2C')) and len(port.port.writes)==1)
check('physical IO and synchronous write advertised',port.records_physical_io and not port.async_write and port.metadata['route']==0)
port.close();port.close();check('close idempotent without sending control',port.port.closed==1 and len(port.port.writes)==1)
check('closed port blocks further write',fails(lambda:port.write(packet())))

port=s.SerialPort('COM6',serial_factory=FakeSerial,event_sink=lambda name,**x:events.append((name,x)))
port.port.fail_write=True
check('unknown write raises and never retries',fails(lambda:port.write(packet())) and len(port.port.writes)==1)
check('unknown write latches lock on further writes',fails(lambda:port.write(packet())) and len(port.port.writes)==1)
check('unknown write event does not claim confirmed bytes',events[-1][0]=='write_unknown' and events[-1][1]['confirmed_bytes'] is None)
port.close()

partial_io=[];port=s.SerialPort('COM7',serial_factory=FakeSerial,io_sink=lambda *x:partial_io.append(x),event_sink=lambda name,**x:events.append((name,x)))
port.port.partial=True
check('short physical write is unknown and not retried',fails(lambda:port.write(packet())) and len(port.port.writes)==1)
check('only confirmed prefix recorded on short write',partial_io==[(0,1,packet()[:7])] and events[-1][1]['confirmed_bytes']==7)
port.close()
with s.SerialPort('COM9',serial_factory=FakeSerial) as managed:pass
check('context manager releases serial resource',managed.port.closed==1)
check('invalid baud rejected before opening',fails(lambda:s.SerialPort('COM5',baudrate=299,serial_factory=FakeSerial)))
custom=s.SerialPort('COM55',baudrate=115200,serial_factory=FakeSerial)
check('custom baud used by FakeSerial and metadata',custom.port.open_args['baudrate']==115200 and custom.metadata['baudrate']==115200)
custom.close()
for invalid in (4000001,230400.5,True,'230400'):
    check('noninteger or out-of-range baud rejected '+repr(invalid),fails(lambda:s.SerialPort('COM5',baudrate=invalid,serial_factory=FakeSerial)))
from client import USBConnection
original=s.SerialPort
try:
    s.SerialPort=lambda address,**kwargs:original(address,serial_factory=FakeSerial,**kwargs)
    wrapper=USBConnection('COM56',baudrate=57600)
    check('USBConnection forwards custom baud to FakeSerial',wrapper.port.port.open_args['baudrate']==57600 and wrapper.port.metadata['baudrate']==57600)
    wrapper.close()
finally:s.SerialPort=original
check('no hardware driver imports attempted',not attempts)
check('no threads started',tuple(threading.enumerate())==before)
files={str(p.relative_to(ROOT)).replace('\\','/'):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in (ROOT/'vendor_serial/serial').rglob('*') if p.is_file()}
check('bundled pyserial package complete',len(files)>=28 and 'vendor_serial/serial/serialwin32.py' in files and 'vendor_serial/serial/tools/list_ports_windows.py' in files)
report=dict(status='PASS',checks=len(checks),passed=checks,hardware_ports_opened=0,hardware_commands_sent=0,driver_import_attempts=attempts,threads_started=0,bundled_pyserial_version='3.5',vendor_files=files)
(ROOT/'SERIAL_TRANSPORT_OFFLINE_CHECKS.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k not in ('passed','vendor_files')},ensure_ascii=False))
