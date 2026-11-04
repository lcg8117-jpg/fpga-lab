"""Fake RawInput reader tests. No enumeration, registration or device connection."""
from pathlib import Path
import hashlib
import json
import threading
from types import SimpleNamespace
from selected_keyboard import SelectedKeyboard
from protocol import POSITION,STOP
from test_dual_ui_offline import fake_app,FakeOwner,Value,Button

class FakeReader:
    instances=[]
    def __init__(self,match,on_key,on_removed):
        self.match=match;self.on_key=on_key;self.on_removed=on_removed;self.error=None;self.stopped=False
        self.instances.append(self)
    def start(self):
        if self.match=='ambiguous':return [{'path':'one'},{'path':'two'}]
        return [{'path':'HID#UNIQUE_SELECTED_KEYPAD','handle':7}]
    def stop(self):self.stopped=True

def run():
    checks=[]
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append(name)
    controller=SelectedKeyboard(reader_factory=FakeReader,held_factory=lambda keys:())
    check('default constructs no reader and captures nothing',not controller.active and controller.reader is None and not FakeReader.instances)
    controller.enable('UNIQUE',list('ghijkl'));reader=controller.reader
    check('explicit enable uses unique selected device',controller.active and len(controller.selected)==1)
    reader.on_key(ord('A'),True)
    check('unmapped text is neither queued nor logged',controller.events.empty() and not controller.poll())
    reader.on_key(ord('G'),True);reader.on_key(ord('G'),True)
    check('held selected key sends one position',controller.poll()==[(POSITION,b'\x01','RAW_KEYBOARD_POSITION')])
    reader.on_key(ord('G'),False);reader.on_key(ord('G'),True)
    check('release permits a new edge',len(controller.poll())==1)
    reader.on_key(ord('H'),True);reader.on_key(0x20,True)
    check('space STOP preempts queued position events',controller.poll()==[(STOP,b'','RAW_KEYBOARD_SPACE')])
    controller.disable();reader.on_key(ord('I'),True)
    check('disabled old callbacks cannot submit commands',reader.stopped and not controller.poll())
    try:controller.enable('ambiguous',list('123456'))
    except RuntimeError:ambiguous=True
    else:ambiguous=False
    check('ambiguous reader selection fails closed',ambiguous and not controller.active)
    try:controller.enable('',list('123456'))
    except ValueError:empty=True
    else:empty=False
    check('blank device selector cannot enable capture',empty and not controller.active)
    held=SelectedKeyboard(reader_factory=FakeReader,held_factory=lambda keys:{ord('G')})
    held.enable('UNIQUE',list('ghijkl'));held.reader.on_key(ord('H'),True)
    check('arming while held waits for release',not held.poll())
    held.reader.on_key(ord('G'),False);held.reader.on_key(ord('H'),False);held.reader.on_key(ord('H'),True)
    check('fresh press works after arming keys are released',held.poll()==[(POSITION,b'\x02','RAW_KEYBOARD_POSITION')]);held.disable()
    overflow=SelectedKeyboard(reader_factory=FakeReader,held_factory=lambda keys:(),max_queue=1)
    overflow.enable('UNIQUE',list('123456'));overflow.reader.on_key(ord('1'),True);overflow.reader.on_key(ord('1'),False)
    check('queue overflow cannot lose emergency STOP',overflow.poll()==[(STOP,b'','RAW_KEYBOARD_REMOVED')] and not overflow.active);overflow.disable()
    ui=fake_app();ui.keyboard_status=Value();ui.keyboard_button=Button();ui.keyboard_device=Value('UNIQUE')
    ui.selected_keyboard=SelectedKeyboard(reader_factory=FakeReader,held_factory=lambda keys:())
    ui.owner=FakeOwner('FAKE',Path(__file__).parent,transport='usb',port='COM5');ui.primary_transport='usb'
    ui.enable_selected_keyboard();reader=ui.selected_keyboard.reader
    check('UI explicit enable switches capture mode',ui.selected_keyboard.active and ui.keyboard_button.options['text']=='指定键盘已启用')
    ui._key_press(SimpleNamespace(keysym='1'))
    check('Tk position path is suppressed while RawInput is active',not ui.owner.commands)
    main_thread=threading.get_ident();worker=threading.Thread(target=lambda:reader.on_key(ord('1'),True));worker.start();worker.join()
    check('RawInput callback never sends directly from worker',not ui.owner.commands)
    ui._poll_selected_keyboard()
    check('queued selected key is sent by Tk main thread only',ui.owner.commands==[(POSITION,0,b'\x01','usb')] and ui.owner.command_threads==[main_thread])
    check('RawInput position records control action rather than keyboard text',ui.owner.diagnostics.events[-1][0]=='selected_keyboard_action' and 'key' not in ui.owner.diagnostics.events[-1][1])
    reader.on_key(ord('2'),True);reader.on_removed();ui._poll_selected_keyboard()
    check('device removal discards pending movement and submits STOP',ui.owner.commands[-1][0]==STOP and not any(item[2]==b'\x02' for item in ui.owner.commands))
    check('device removal disables capture and stops reader',not ui.selected_keyboard.active and reader.stopped and '拔除' in ui.keyboard_status.get())
    n=len(ui.owner.commands);ui._key_press(SimpleNamespace(keysym='3'))
    check('normal Tk keyboard resumes after device removal',len(ui.owner.commands)==n+1 and ui.owner.commands[-1][2]==b'\x03')
    ui.old_position_preset()
    check('old preset imports only six G-L positions',ui.position_keys==list('ghijkl') and ui.keys_text.get()=='g,h,i,j,k,l')
    copy=Path(__file__).parent/'raw_keyboard.py'
    digest=hashlib.sha256(copy.read_bytes()).hexdigest()
    check('original RawInput source is reused byte for byte',digest=='35e28331231c396e55ff32cb908184a86c5d4745defae1077061d0f98b53aa8b')
    result=dict(status='PASS',checks=len(checks),passed=checks,real_rawinput_registrations=0,real_keyboard_enumerations=0,
        actual_device_connections=0,test_callback_threads=1,raw_input_threads=0,source_reuse_sha256=digest)
    (Path(__file__).parent/'SELECTED_KEYBOARD_CHECKS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='passed'},ensure_ascii=False))

if __name__=='__main__':run()
