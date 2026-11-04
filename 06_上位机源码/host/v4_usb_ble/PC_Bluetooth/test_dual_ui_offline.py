"""Dual-link UI/keyboard tests using only symbolic owners and widgets."""
from pathlib import Path
from types import SimpleNamespace
import importlib.abc
import json
import sys
import threading
import tempfile

attempts=[]
class BlockDevices(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'serial','bleak','winrt','bluetooth'}:
            attempts.append(fullname);raise RuntimeError('UI QA forbids '+fullname)
sys.meta_path.insert(0,BlockDevices())
before=tuple(threading.enumerate())
import app
from select_transport import TransportSelectionError

class Value:
    def __init__(self,value=''):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value
class Button:
    def __init__(self):self.options={}
    def configure(self,**kwargs):self.options.update(kwargs)
class Root:
    def __init__(self):self.focus=None;self.scheduled=[]
    def focus_get(self):return self.focus
    def after_idle(self,callback):callback()
    def after(self,ms,callback):self.scheduled.append((ms,callback))
class Widget:
    def __init__(self,root,kind='TButton'):self.root=root;self.kind=kind
    def winfo_toplevel(self):return self.root
    def winfo_class(self):return self.kind
class Diagnostics:
    def __init__(self):self.events=[];self.fail=False
    def event(self,name,**kwargs):
        if self.fail:raise OSError('Fake recording failure')
        self.events.append((name,kwargs))
class FakeOwner:
    instances=[]
    def __init__(self,address,logdir,*,transport='usb',port='',baudrate=230400):
        self.address=address;self.transport=transport;self.port=port;self.baudrate=baudrate;self.closed=threading.Event()
        self.diagnostics=Diagnostics();self.commands=[];self.command_threads=[];self.stops=0;self.attachments=0;self.detachments=0
        self.routes={'usb':{'connected':transport=='usb','ready':transport=='usb'},
            'ble':{'connected':transport=='ble','ready':transport=='ble'}}
        FakeOwner.instances.append(self)
    def start(self):return self
    def stop(self,wait=True):self.stops+=1;self.closed.set()
    def attach_ble(self,address=None):
        if self.routes['ble']['connected']:raise RuntimeError('Fake BLE already connected')
        self.attachments+=1;self.routes['ble'].update(connected=True,ready=True)
    def detach_ble(self):self.detachments+=1;self.routes['ble'].update(connected=False,ready=False)
    def command(self,opcode,group=0,payload=b''):
        route='ble' if opcode in (app.POSITION,app.CANCEL_HOLD) and self.routes['ble']['ready'] else self.transport
        self.commands.append((opcode,group,payload,route))
        self.command_threads.append(threading.get_ident())
    def set_gains(self,values):self.gains=values

def fake_app():
    ui=object.__new__(app.App);ui.root=Root();ui.root.focus=Widget(ui.root)
    ui.owner=None;ui.primary_transport=None;ui.disconnecting=False;ui.closing=False;ui.error='';ui.local_notice='';ui.held_keys=set();ui.pressed_keycodes={}
    ui.position_keys=list('123456');ui.keys_text=Value('1,2,3,4,5,6');ui.usb_baud=Value('230400');ui.notice=Value()
    ui.keyboard_device=Value('')
    ui.usb_port=Value('COM5');ui.address=Value(app.DEFAULT_ADDRESS)
    ui.usb_connect_button=Button();ui.ble_connect_button=Button();ui.ble_disconnect_button=Button();ui.disconnect_button=Button()
    return ui

def run():
    checks=[];selections=[];FakeOwner.instances.clear()
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append(name)
    original_owner=app.DualOwner;original_choose=app.choose_transport
    try:
        app.DualOwner=FakeOwner
        def choose(hint):selections.append(hint);return dict(transport='usb',port='COM5')
        app.choose_transport=choose
        ui=fake_app();check('constructing symbolic UI opens nothing',not FakeOwner.instances and not selections)
        ui.connect_usb();owner=ui.owner
        check('USB connect creates one USB primary owner',len(FakeOwner.instances)==1 and owner.transport=='usb' and owner.port=='COM5')
        check('USB connect does not automatically attach BLE',owner.attachments==0 and not owner.routes['ble']['connected'])
        check('USB ports are selected only by explicit USB connect',selections==['COM5'])
        for key in '123456':
            ui._key_press(SimpleNamespace(keysym=key));ui._key_release(SimpleNamespace(keysym=key))
        check('all six number keys work over USB without BLE',[x[2] for x in owner.commands]==[bytes((n,)) for n in range(1,7)] and all(x[3]=='usb' for x in owner.commands))
        n=len(owner.commands);ui._key_press(SimpleNamespace(keysym='2'));ui._key_press(SimpleNamespace(keysym='2'))
        check('held position key sends exactly once',len(owner.commands)==n+1)
        ui._key_release(SimpleNamespace(keysym='2'));ui._key_press(SimpleNamespace(keysym='2'));ui._key_release(SimpleNamespace(keysym='2'))
        check('released position key may be pressed again',len(owner.commands)==n+2)
        for kind in ('Entry','TEntry','Text','TCombobox','Spinbox','TSpinbox'):
            ui.root.focus=Widget(ui.root,kind);n=len(owner.commands);ui._key_press(SimpleNamespace(keysym='3'))
            check('numeric typing does not move while '+kind+' focused',len(owner.commands)==n)
        ui.root.focus=None;n=len(owner.commands);ui._key_press(SimpleNamespace(keysym='4'))
        check('inactive window cannot issue position commands',len(owner.commands)==n)
        ui.root.focus=Widget(Root());ui._key_press(SimpleNamespace(keysym='4'))
        check('focus in another toplevel cannot issue position commands',len(owner.commands)==n)
        ui.root.focus=Widget(ui.root,'TEntry');ui._key_press(SimpleNamespace(keysym='space'))
        check('space STOP has priority even inside parameter input',owner.commands[-1][0]==app.STOP)
        check('space STOP records source and opcode',owner.diagnostics.events[-1][0]=='ui_keyboard' and owner.diagnostics.events[-1][1]['key']=='space' and owner.diagnostics.events[-1][1]['opcode']==app.STOP)
        n=len(owner.commands);ui._key_press(SimpleNamespace(keysym='space'))
        check('held space does not flood STOP commands',len(owner.commands)==n)
        ui._key_release(SimpleNamespace(keysym='space'))
        owner.diagnostics.fail=True;ui._key_press(SimpleNamespace(keysym='space'));ui._key_release(SimpleNamespace(keysym='space'))
        check('recording failure never blocks space STOP',len(owner.commands)==n+1 and owner.commands[-1][0]==app.STOP and '记录失败' in ui.error)
        owner.diagnostics.fail=False;ui.root.focus=Widget(ui.root);ui.held_keys.add('position1');ui.root.focus=None;ui._focus_out(None)
        check('losing window focus clears held-key state',not ui.held_keys)
        ui.root.focus=Widget(ui.root);ui.connect_ble()
        check('adding BLE reuses the single primary owner and session',ui.owner is owner and len(FakeOwner.instances)==1 and owner.attachments==1 and owner.routes['usb']['connected'] and owner.routes['ble']['connected'])
        n=len(owner.commands)
        for key in '123456':ui._key_press(SimpleNamespace(keysym=key));ui._key_release(SimpleNamespace(keysym=key))
        check('six number keys use BLE position route when both connected',all(x[3]=='ble' for x in owner.commands[n:]) and len(owner.commands[n:])==6)
        ui.send(app.POSITION,payload=b'\x06')
        check('position buttons use the same position command path as keys',owner.commands[-1][0]==app.POSITION and owner.commands[-1][2]==b'\x06' and owner.commands[-1][3]=='ble')
        ui.send(app.READ_GAINS,255)
        check('normal parameter read still uses USB primary',owner.commands[-1][0]==app.READ_GAINS and owner.commands[-1][3]=='usb')
        ui.disconnect_ble()
        check('disconnect BLE leaves USB primary open',owner.detachments==1 and owner.routes['usb']['connected'] and not owner.routes['ble']['connected'] and not owner.closed.is_set())
        ui._key_press(SimpleNamespace(keysym='KP_1'));ui._key_release(SimpleNamespace(keysym='KP_1'))
        check('USB position remains usable after BLE disconnect',owner.commands[-1][3]=='usb' and owner.commands[-1][2]==b'\x01')
        ui.disconnect()
        check('disconnect all requests stop and clears held keys',owner.stops==1 and ui.disconnecting and not ui.held_keys)
        n=len(FakeOwner.instances);ui=fake_app();ui.connect_ble()
        check('explicit BLE alone is supported without USB enumeration',len(FakeOwner.instances)==n+1 and ui.owner.transport=='ble' and selections==['COM5'])
        n=len(FakeOwner.instances);ui.connect_usb()
        check('BLE primary cannot silently become USB primary or create second owner',len(FakeOwner.instances)==n and '先停止并断开全部' in ui.error)
        ui.disconnect_ble();check('disconnect BLE primary closes its sole owner',ui.owner.closed.is_set())
        ui=fake_app();n=len(FakeOwner.instances);app.choose_transport=lambda hint:dict(transport='ble',port=None)
        ui.connect_usb()
        check('missing USB never auto-connects BLE',len(FakeOwner.instances)==n and '未检测到USB' in ui.error)
        def multiple(hint):raise TransportSelectionError('多个USB，请明确选择',[{'port':'COM5'},{'port':'COM6'}])
        app.choose_transport=multiple;ui.connect_usb()
        check('ambiguous USB lists explicit choices and opens nothing',len(FakeOwner.instances)==n and 'COM5, COM6' in ui.error)
        snap=dict(routes={'usb':dict(connected=True,ready=True),'ble':dict(connected=True,ready=True)},position_route='ble')
        check('two indicators independently report simultaneous links','已连接' in app.route_indicator(snap,'usb') and '已连接' in app.route_indicator(snap,'ble'))
        check('position route label explicitly reports BLE','→ 蓝牙位置路' in app.position_route_text(snap))
        check('position route label explicitly reports USB-only usability','USB主路' in app.position_route_text(dict(position_route='usb')))
        app.choose_transport=choose;ui=fake_app();ui.keys_text.set('q,w,e,a,s,d');ui.usb_baud.set('115200');ui.connect_usb()
        check('editable USB baud reaches the owner constructor',ui.owner.baudrate==115200)
        for key in 'qweasd':ui._key_press(SimpleNamespace(keysym=key));ui._key_release(SimpleNamespace(keysym=key))
        check('custom letter mapping sends all six positions',[item[2] for item in ui.owner.commands]==[bytes((n,)) for n in range(1,7)])
        n=len(ui.owner.commands);ui._key_press(SimpleNamespace(keysym='1'))
        check('old number mapping does not move after letters are selected',len(ui.owner.commands)==n)
        ui._apply_keys([';','w','e','a','s','d']);ui._key_press(SimpleNamespace(keysym='semicolon',char=';',keycode=186));ui._key_release(SimpleNamespace(keysym='semicolon',char='',keycode=186))
        check('single character punctuation mapping releases without stale held state',ui.owner.commands[-1][2]==b'\x01' and not ui.held_keys)
        with tempfile.TemporaryDirectory(prefix='.ui_settings_qa_',dir=Path(__file__).parent) as directory:
            ui=fake_app();ui.settings_path=Path(directory)/'settings.json';ui.keys_text.set('q,w,e,a,s,d');ui.usb_baud.set('57600')
            n=len(FakeOwner.instances);ui.save_local_settings()
            check('save settings applies custom mapping without connecting hardware',ui.position_keys==list('qweasd') and len(FakeOwner.instances)==n and not ui.error)
            stored=json.loads(ui.settings_path.read_text('utf-8'))
            check('UI save persists actual selected baud and mapping',stored['usb_baud']==57600 and stored['position_keys']==list('qweasd'))
            ui.keys_text.set('q,q,e,a,s,d');prior=ui.settings_path.read_bytes();ui.save_local_settings()
            check('invalid mapping reports duplicate without replacing valid settings','重复' in ui.error and ui.settings_path.read_bytes()==prior)
        original_list=app.list_usb_choices;app.list_usb_choices=lambda:[{'port':'COM5'},{'port':'COM6'}]
        try:
            ui=fake_app();ui.usb_port_box=Button();n=len(FakeOwner.instances);ui.refresh_usb_ports()
            check('refresh ports only updates editable choices without opening ports',ui.usb_port_box.options['values']==('COM5','COM6') and len(FakeOwner.instances)==n)
        finally:app.list_usb_choices=original_list
        check('closing flag prevents keyboard actions',app.keyboard_command('1',active=False) is None)
        check('no hardware drivers or threads were started',not attempts and tuple(threading.enumerate())==before)
        result=dict(status='PASS',checks=len(checks),passed=checks,actual_owner_instances=0,actual_device_connections=0,
            hardware_commands=0,device_import_attempts=attempts,threads_started=0,windows_started=0)
        (Path(__file__).parent/'DUAL_UI_OFFLINE_CHECKS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k!='passed'},ensure_ascii=False))
    finally:app.DualOwner=original_owner;app.choose_transport=original_choose

if __name__=='__main__':run()
