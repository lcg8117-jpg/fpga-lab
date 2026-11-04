"""Fake WinRT/session checks; no BLE imports, threads, or real connections."""
from pathlib import Path
from types import SimpleNamespace, ModuleType
import ast, asyncio, hashlib, importlib.abc, json, sys, threading

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/'host'
sys.path.insert(0,str(BASE));sys.path.insert(0,str(ROOT))
attempts=[]
class BlockHardware(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'bleak','winrt','serial'}:
            attempts.append(fullname);raise RuntimeError('Offline check forbids '+fullname)
sys.meta_path.insert(0,BlockHardware())
before=tuple(threading.enumerate())
import ble_transport as b
checks=[]
def check(name,condition):
    assert condition,name
    checks.append(name)

class Request:
    status=1
    def __init__(self):self.closed=False
    def close(self):self.closed=True
class Native:
    def __init__(self):self.interval=80;self.request=Request();self.requested=[]
    def get_connection_parameters(self):return SimpleNamespace(connection_interval=self.interval,connection_latency=0,link_timeout=500)
    def get_connection_phy(self):
        info=SimpleNamespace(is_coded_phy=False,is_uncoded1_m_phy=False,is_uncoded2_m_phy=True)
        return SimpleNamespace(receive_info=info,transmit_info=info)
    def request_preferred_connection_parameters(self,policy):
        self.requested.append(policy);self.interval=12;return self.request
preferred=SimpleNamespace(throughput_optimized=object())
def client(native):return SimpleNamespace(mtu_size=247,_backend=SimpleNamespace(_requester=native))

native=Native();tune=b._ConnectionTuning(client(native),preferred_type=preferred)
check('supported request is made once',native.requested==[preferred.throughput_optimized])
check('preference held through connection',tune.request is native.request and not native.request.closed)
check('actual before interval read in ms',tune.report['before']['parameters']['connection_interval_ms']==100.0)
check('actual after interval read in ms',tune.report['after_request']['parameters']['connection_interval_ms']==15.0)
check('actual MTU and PHY recorded',tune.report['after_request']['mtu_size']==247 and tune.report['after_request']['phy']['receive']['is_uncoded2_m_phy'])
check('request status recorded separately from effective parameters',tune.report['request_status_code']==1 and tune.report['effective_parameters_must_be_read_back'])
tune.close();check('preference closed on disconnect',native.request.closed and tune.request is None)
tune.close();check('cleanup idempotent',tune.report['request_closed'])

unsupported=SimpleNamespace(get_connection_parameters=lambda:SimpleNamespace(connection_interval=80,connection_latency=1,link_timeout=300))
tune=b._ConnectionTuning(client(unsupported),preferred_type=preferred)
check('old OS method absence recorded without raising',tune.report['request_state']=='unsupported' and tune.request is None)
check('query failure visible in report','phy' in tune.report['after_request']['query_errors'])
tune.close()

class BrokenQuery(Native):
    def get_connection_parameters(self):raise OSError('query failed')
native=BrokenQuery();tune=b._ConnectionTuning(client(native),preferred_type=preferred)
check('optional query failure does not destroy preference',tune.report['request_state']=='requested' and 'parameters' in tune.report['after_request']['query_errors'])
tune.close();check('cleanup runs despite query error',native.request.closed)

class Denied(Native):
    def request_preferred_connection_parameters(self,policy):raise OSError('access denied')
tune=b._ConnectionTuning(client(Denied()),preferred_type=preferred)
check('request failure not reported as successful',tune.report['request_state']=='request_failed' and 'access denied' in tune.report['request_error'])
tune.close()

native=Native()
def broken_close():raise OSError('close failed')
native.request.close=broken_close
tune=b._ConnectionTuning(client(native),preferred_type=preferred);tune.close()
check('close failure preserved without replacing a primary BLE error',tune.report['request_closed'] is False and 'close failed' in tune.report['request_close_error'])

class Services:
    def __init__(self):
        self.service=SimpleNamespace(characteristics=[SimpleNamespace(uuid=b.WRITE,properties=['write-without-response']),SimpleNamespace(uuid=b.NOTIFY,properties=['notify'])])
    def get_service(self,uuid):return self.service if uuid==b.SERVICE else None
class FakeClient:
    last=None;fail_notify=False
    def __init__(self,device,**kwargs):
        type(self).last=self;self._backend=SimpleNamespace(_requester=Native());self.mtu_size=247;self.services=Services();self.actions=[]
    async def __aenter__(self):return self
    async def __aexit__(self,*args):self.actions.append('context_exit')
    async def start_notify(self,*args):
        self.actions.append('notify')
        if self.fail_notify:raise OSError('original notification failure')
    async def stop_notify(self,*args):self.actions.append('stop_notify')
class Scanner:
    @staticmethod
    async def find_device_by_address(address,timeout):return SimpleNamespace(address=address,name='Fake BT10')
class DefaultAdapter:
    device_id='FAKE_ADAPTER';bluetooth_address=12345;is_low_energy_supported=True;is_central_role_supported=True;is_low_energy_uncoded2_m_phy_supported=True
    async def get_radio_async(self):return SimpleNamespace(name='Fake radio',state=1,kind=3)
class AdapterType:
    @staticmethod
    async def get_default_async():return DefaultAdapter()
class DeviceInformation:
    @staticmethod
    async def create_from_id_async(device_id,wanted):return SimpleNamespace(name='Fake adapter',properties={'System.Devices.DriverVersion':'1.2.3'})

async def session_case(fail):
    port=object.__new__(b.BLEPort)
    port.address='00:11:22:33:44:55';port.stop=threading.Event();port.metadata={};port.events=[]
    port.event_sink=lambda name,**values:port.events.append((name,values))
    port.ready=SimpleNamespace(set=port.stop.set)
    async def startup():await asyncio.sleep(.05)
    port._startup_sync=startup;port._disconnected=lambda unused:None
    FakeClient.fail_notify=fail
    original=b.api;b.api=lambda:(FakeClient,Scanner)
    try:
        if fail:
            try:await port._session()
            except OSError as exc:check('original BLE notification error propagated',str(exc)=='original notification failure')
            else:raise AssertionError('Original BLE error was hidden')
        else:
            await port._session()
            check('same FFE0 FFE1 FFE2 profile remains',port.metadata['service']==b.SERVICE and port.metadata['write_uuid']==b.WRITE and port.metadata['notify_uuid']==b.NOTIFY)
            check('adapter and driver data recorded '+repr(port.metadata['default_adapter']),port.metadata['default_adapter'].get('properties',{}).get('System.Devices.DriverVersion')=='1.2.3')
            check('connected tuning event recorded',any(n=='connection_tuning_report' and v['stage']=='connected' for n,v in port.events))
            check('normal session retains notify stop semantics',FakeClient.last.actions==['notify','stop_notify','context_exit'])
        check('request cleanup on every session outcome',FakeClient.last._backend._requester.request.closed)
        check('disconnect cleanup event recorded',any(n=='connection_tuning_report' and v['stage']=='disconnecting' for n,v in port.events))
    finally:b.api=original

fake_bt=ModuleType('winrt.windows.devices.bluetooth');fake_bt.BluetoothLEPreferredConnectionParameters=preferred;fake_bt.BluetoothAdapter=AdapterType
fake_en=ModuleType('winrt.windows.devices.enumeration');fake_en.DeviceInformation=DeviceInformation
fake_modules={}
for name in ('winrt','winrt.windows','winrt.windows.devices'):
    module=ModuleType(name);module.__path__=[];fake_modules[name]=module
fake_modules['winrt.windows.devices.bluetooth']=fake_bt;fake_modules['winrt.windows.devices.enumeration']=fake_en
prior={k:sys.modules.get(k) for k in fake_modules}
try:
    sys.modules.update(fake_modules)
    asyncio.run(session_case(False));asyncio.run(session_case(True))
finally:
    for key,value in prior.items():
        if value is None:sys.modules.pop(key,None)
        else:sys.modules[key]=value

def class_methods(path):
    tree=ast.parse(path.read_text('utf-8'))
    cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='BLEPort')
    return {node.name:ast.dump(node,include_attributes=False) for node in cls.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}
old,new=class_methods(BASE/'ble_transport.py'),class_methods(ROOT/'ble_transport.py')
for name in ('__init__','_notification','_startup_sync','_disconnected','_run','_check','in_waiting','read','_write','write','close'):
    check('unchanged original '+name,old[name]==new[name])
check('no hardware imports attempted',not attempts)
check('no threads started',tuple(threading.enumerate())==before)
report=dict(status='PASS',checks=len(checks),passed=checks,hardware_connections=0,hardware_commands=0,device_import_attempts=attempts,threads_started=0,
            original_sha256=hashlib.sha256((BASE/'ble_transport.py').read_bytes()).hexdigest(),
            patch_sha256=hashlib.sha256((ROOT/'ble_transport.py').read_bytes()).hexdigest())
(ROOT/'BLE_TRANSPORT_OFFLINE_CHECKS.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='passed'},ensure_ascii=False))
