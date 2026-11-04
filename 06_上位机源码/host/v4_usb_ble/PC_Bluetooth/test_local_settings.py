"""Configuration round trips, corrupt recovery and atomic-write failure checks."""
from pathlib import Path
import json
import tempfile
import local_settings as settings

def run():
    checks=[]
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append(name)
    def invalid(value,expected):
        try:settings.validate_settings(value)
        except ValueError as exc:return expected in str(exc)
        return False
    with tempfile.TemporaryDirectory(prefix='.settings_qa_',dir=Path(__file__).parent) as directory:
        path=Path(directory)/'settings.json'
        value,error=settings.load_settings(path)
        check('new PC starts with empty automatic COM,230400 and project BT10 default',value['usb_port']=='' and value['usb_baud']==230400 and value['ble_address']=='D0:54:7B:AA:0C:84' and value['position_keys']==list('123456') and not error)
        value['position_keys'][0]='q'
        check('default lists are independent across loads',settings.defaults()['position_keys']==list('123456'))
        requested=dict(usb_port=' com77 ',usb_baud='115200',ble_address='d0:54:7b:aa:0c:84',position_keys='q,w,e,a,s,d')
        saved=settings.save_settings(path,requested);restored,error=settings.load_settings(path)
        check('legacy four-field settings load with keyboard selection disabled',restored==saved==dict(usb_port='COM77',usb_baud=115200,ble_address='D0:54:7B:AA:0C:84',position_keys=list('qweasd'),keyboard_device='') and not error)
        check('settings file contains only five PC fields',set(json.loads(path.read_text('utf-8')))=={'usb_port','usb_baud','ble_address','position_keys','keyboard_device'})
        selected=settings.save_settings(path,dict(requested,keyboard_device=' HID#VID_1234&PID_5678 '))
        check('optional unique keyboard device persists without enabling capture',settings.load_settings(path)[0]['keyboard_device']=='HID#VID_1234&PID_5678')
        check('successful atomic save leaves no transient file',[p.name for p in path.parent.iterdir()]==['settings.json'])
        before=path.read_bytes();replace=settings.os.replace
        def fail_replace(*unused):raise OSError('simulated replace failure')
        settings.os.replace=fail_replace
        try:
            try:settings.save_settings(path,dict(requested,usb_baud=57600))
            except OSError:failed=True
            else:failed=False
        finally:settings.os.replace=replace
        check('failed atomic replacement preserves prior complete settings',failed and path.read_bytes()==before)
        check('failed replacement cleans only its own temporary file',[p.name for p in path.parent.iterdir()]==['settings.json'])
        for raw in ('{not json','[]','{"usb_baud": false}','{"position_keys": ["q","q","e","a","s","d"]}'):
            path.write_text(raw,encoding='utf-8');value,error=settings.load_settings(path)
            check('corrupt settings recover defaults with visible error '+raw,value==settings.defaults() and bool(error))
        check('duplicate case-insensitive keys report both positions',invalid(dict(position_keys='q,Q,e,a,s,d'),'位置1和位置2'))
        check('empty key reports its position',invalid(dict(position_keys='q,,e,a,s,d'),'位置2的按键为空'))
        check('space cannot be mapped to a position',invalid(dict(position_keys=['q','w','e','a','s',' ']),'空格固定为STOP'))
        check('named space cannot be mapped',invalid(dict(position_keys=['q','w','e','a','s','space']),'空格固定为STOP'))
        check('key map must contain exactly six values',invalid(dict(position_keys='q,w,e'),'需要6个'))
        check('multi-character mapping is rejected',invalid(dict(position_keys='qq,w,e,a,s,d'),'单个可打印字符'))
        check('PID settings are not accepted in PC configuration',invalid(dict(kp=99),'不支持的本地设置项'))
        check('noninteger baud is rejected',invalid(dict(usb_baud='230400.0'),'必须是整数'))
        check('boolean baud is rejected',invalid(dict(usb_baud=True),'必须是整数'))
        check('unsupported low baud is rejected',invalid(dict(usb_baud=1),'300至4000000'))
        check('bad COM syntax is rejected',invalid(dict(usb_port='BT10'),'COM号'))
        check('bad MAC syntax is rejected',invalid(dict(ble_address='bad'),'完整MAC'))
        check('Unicode punctuation separator is accepted',settings.normalize_position_keys('q，w，e，a，s，d')==list('qweasd'))
    result=dict(status='PASS',checks=len(checks),passed=checks,hardware_connections=0,hardware_commands=0)
    (Path(__file__).parent/'LOCAL_SETTINGS_CHECKS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='passed'},ensure_ascii=False))

if __name__=='__main__':run()
