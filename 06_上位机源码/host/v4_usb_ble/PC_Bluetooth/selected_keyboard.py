"""Queue adapter for the original raw_keyboard.py; importing captures nothing."""
import queue
import threading
from protocol import STOP,POSITION

OEM_KEYS={';':0xba,'=':0xbb,',':0xbc,'-':0xbd,'.':0xbe,'/':0xbf,'`':0xc0,
    '[':0xdb,'\\':0xdc,']':0xdd,"'":0xde}

def bindings_for_keys(keys):
    result={}
    for position,key in enumerate(keys,1):
        if len(key)==1 and key.isascii() and key.isalnum():vkey=ord(key.upper())
        elif key in OEM_KEYS:vkey=OEM_KEYS[key]
        else:raise ValueError('指定键盘请使用字母、数字或常见标点；普通窗口键盘仍支持当前字符')
        result[vkey]=position
        if key.isascii() and key.isdigit():result[0x60+int(key)]=position
    return result

class SelectedKeyboard:
    def __init__(self,*,reader_factory=None,held_factory=None,max_queue=128):
        self.reader_factory=reader_factory;self.held_factory=held_factory
        self.events=queue.Queue(maxsize=max_queue);self.removed=threading.Event()
        self.reader=None;self.active=False;self.generation=0;self.bindings={};self.held=set();self.wait_release=False
        self.status='未启用指定键盘；普通窗口键盘可用';self.removal_reason='';self.selected=[]
    def _clear_queue(self):
        while True:
            try:self.events.get_nowait()
            except queue.Empty:return
    def disable(self,*,status=None):
        self.active=False;self.generation+=1;self.held.clear();self.wait_release=False
        self.removed.clear();self._clear_queue();reader=self.reader;self.reader=None
        if reader:reader.stop()
        self.status=status or '指定键盘已禁用；普通窗口键盘恢复'
    def enable(self,match,keys):
        if not str(match).strip():raise ValueError('请填写能唯一选定键盘的设备路径片段')
        bindings=bindings_for_keys(keys);self.disable();generation=self.generation
        self.bindings=bindings;self.removal_reason=''
        factory=self.reader_factory;held_factory=self.held_factory
        if factory is None:
            from raw_keyboard import RawKeyboard,held_control_keys
            factory=RawKeyboard
            if held_factory is None:held_factory=held_control_keys
        def on_key(vkey,down):
            if generation!=self.generation or not self.active or (vkey!=0x20 and vkey not in bindings):return
            try:self.events.put_nowait((generation,int(vkey),bool(down)))
            except queue.Full:
                self.removal_reason='指定键盘消息积压，已禁用并请求STOP';self.removed.set()
        def on_removed():
            if generation==self.generation:
                self.removal_reason='指定键盘已拔除或读取出错，已禁用并请求STOP';self.removed.set()
        self.reader=factory(str(match).strip(),on_key,on_removed)
        try:
            self.selected=self.reader.start()
            if len(self.selected)!=1:raise RuntimeError('指定键盘必须唯一匹配')
            if self.removed.is_set():raise RuntimeError(self.removal_reason)
            self.held=set(held_factory(set(bindings)|{0x20}) if held_factory else ())
            self.wait_release=bool(self.held);self.active=True
            self.status='指定键盘已启用（后台也响应）；窗口位置键暂停，空格STOP保留'
            if self.wait_release:self.status+='；先松开已按住的控制键'
            return self.selected
        except Exception:
            self.disable();raise
    def poll(self):
        """Only call on Tk's thread; callbacks merely queue selected control edges."""
        if self.active and getattr(self.reader,'error',None):
            self.removal_reason='指定键盘读取失败，已禁用并请求STOP：'+str(self.reader.error);self.removed.set()
        if self.removed.is_set():
            self.active=False;self.held.clear();self._clear_queue();self.status=self.removal_reason
            return [(STOP,b'','RAW_KEYBOARD_REMOVED')]
        actions=[]
        for _ in range(128):
            try:generation,vkey,down=self.events.get_nowait()
            except queue.Empty:break
            if not self.active or generation!=self.generation:continue
            if not down:
                self.held.discard(vkey)
                if not self.held:self.wait_release=False
                continue
            if vkey in self.held:continue
            self.held.add(vkey)
            if vkey==0x20:actions.append((STOP,b'','RAW_KEYBOARD_SPACE'))
            elif not self.wait_release and vkey in self.bindings:actions.append((POSITION,bytes((self.bindings[vkey],)),'RAW_KEYBOARD_POSITION'))
        if self.removed.is_set():
            self.active=False;self.held.clear();self._clear_queue();self.status=self.removal_reason
            return [(STOP,b'','RAW_KEYBOARD_REMOVED')]
        stops=[action for action in actions if action[0]==STOP]
        return stops[:1] if stops else actions
