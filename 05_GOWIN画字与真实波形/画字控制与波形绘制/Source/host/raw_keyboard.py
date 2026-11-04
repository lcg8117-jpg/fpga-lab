"""Windows Raw Input for one explicitly selected keypad; no UI automation.

Only selected-device control keys reach the callback. No text/key logging from
other keyboards. Importing this module does not register input or open ports.
"""
import ctypes
from ctypes import wintypes as W
import threading


class Device(ctypes.Structure):_fields_=[('handle',W.HANDLE),('kind',W.DWORD)]
class Header(ctypes.Structure):_fields_=[('kind',W.DWORD),('size',W.DWORD),('handle',W.HANDLE),('wparam',W.WPARAM)]
class Keyboard(ctypes.Structure):_fields_=[('make',W.USHORT),('flags',W.USHORT),('reserved',W.USHORT),('vkey',W.USHORT),('message',W.UINT),('extra',W.ULONG)]
class Registration(ctypes.Structure):_fields_=[('page',W.USHORT),('usage',W.USHORT),('flags',W.DWORD),('target',W.HWND)]


def api():
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.GetRawInputDeviceList.argtypes=[ctypes.c_void_p,ctypes.POINTER(W.UINT),W.UINT];user.GetRawInputDeviceList.restype=W.UINT
    user.GetRawInputDeviceInfoW.argtypes=[W.HANDLE,W.UINT,ctypes.c_void_p,ctypes.POINTER(W.UINT)];user.GetRawInputDeviceInfoW.restype=W.UINT
    return user


def device_name(user,handle):
    count=W.UINT()
    if user.GetRawInputDeviceInfoW(handle,0x20000007,None,ctypes.byref(count))==0xffffffff:return ''
    text=ctypes.create_unicode_buffer(count.value+1)
    if user.GetRawInputDeviceInfoW(handle,0x20000007,text,ctypes.byref(count))==0xffffffff:return ''
    return text.value


def devices():
    user=api();count=W.UINT()
    if user.GetRawInputDeviceList(None,ctypes.byref(count),ctypes.sizeof(Device))==0xffffffff:raise ctypes.WinError(ctypes.get_last_error())
    entries=(Device*count.value)()
    actual=user.GetRawInputDeviceList(entries,ctypes.byref(count),ctypes.sizeof(Device))
    if actual==0xffffffff:raise ctypes.WinError(ctypes.get_last_error())
    return [{'handle':int(x.handle or 0),'path':device_name(user,x.handle)} for x in entries[:actual] if x.kind==1]


def held_control_keys(keys=range(ord('A'),ord('R')+1)):
    user=api();user.GetAsyncKeyState.argtypes=[ctypes.c_int];user.GetAsyncKeyState.restype=ctypes.c_short
    return {key for key in keys if user.GetAsyncKeyState(key)&0x8000}


class RawKeyboard:
    def __init__(self,match,on_key,on_removed):
        if not match.strip():raise ValueError('Explicit keypad device substring required')
        self.match=match.casefold();self.on_key=on_key;self.on_removed=on_removed
        self.ready=threading.Event();self.thread=None;self.thread_id=None;self.error=None;self.names={}
    def start(self):
        selected=[d for d in devices() if self.match in d['path'].casefold()]
        if not selected:raise RuntimeError('没有匹配的小键盘；先运行devices选择实际设备')
        if len(selected)!=1:raise RuntimeError('匹配了多个键盘；请填写能唯一选择设备的更长路径片段')
        self.thread=threading.Thread(target=self._run,name='J280KeypadRawInput',daemon=True);self.thread.start()
        if not self.ready.wait(5):raise RuntimeError('RawInput startup timeout')
        if self.error:raise RuntimeError(self.error)
        return selected
    def stop(self):
        if self.thread_id:
            user=api();user.PostThreadMessageW.argtypes=[W.DWORD,W.UINT,W.WPARAM,W.LPARAM]
            user.PostThreadMessageW(self.thread_id,0x0012,0,0)
        if self.thread:self.thread.join(3)
    def _run(self):
        user=api();kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetCurrentThreadId.restype=W.DWORD;kernel.GetModuleHandleW.argtypes=[W.LPCWSTR];kernel.GetModuleHandleW.restype=W.HMODULE
        self.thread_id=kernel.GetCurrentThreadId()
        PROC=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
        class Class(ctypes.Structure):
            _fields_=[('style',W.UINT),('proc',PROC),('extra',ctypes.c_int),('window_extra',ctypes.c_int),
                ('instance',W.HINSTANCE),('icon',W.HICON),('cursor',W.HANDLE),('brush',W.HBRUSH),('menu',W.LPCWSTR),('name',W.LPCWSTR)]
        user.DefWindowProcW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];user.DefWindowProcW.restype=ctypes.c_ssize_t
        user.RegisterClassW.argtypes=[ctypes.POINTER(Class)];user.RegisterClassW.restype=W.ATOM
        user.CreateWindowExW.argtypes=[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,W.HWND,W.HMENU,W.HINSTANCE,ctypes.c_void_p]
        user.CreateWindowExW.restype=W.HWND
        user.GetRawInputData.argtypes=[W.HANDLE,W.UINT,ctypes.c_void_p,ctypes.POINTER(W.UINT),W.UINT];user.GetRawInputData.restype=W.UINT
        user.RegisterRawInputDevices.argtypes=[ctypes.POINTER(Registration),W.UINT,W.UINT];user.RegisterRawInputDevices.restype=W.BOOL
        user.GetMessageW.argtypes=[ctypes.POINTER(W.MSG),W.HWND,W.UINT,W.UINT];user.GetMessageW.restype=ctypes.c_int
        user.TranslateMessage.argtypes=[ctypes.POINTER(W.MSG)];user.DispatchMessageW.argtypes=[ctypes.POINTER(W.MSG)];user.DispatchMessageW.restype=ctypes.c_ssize_t
        user.DestroyWindow.argtypes=[W.HWND];user.UnregisterClassW.argtypes=[W.LPCWSTR,W.HINSTANCE]
        def chosen(handle):
            handle=int(handle or 0)
            if handle not in self.names:self.names[handle]=device_name(user,handle)
            return self.match in self.names[handle].casefold()
        @PROC
        def proc(hwnd,message,wparam,lparam):
            try:
                if message==0x00ff:
                    count=W.UINT()
                    if user.GetRawInputData(lparam,0x10000003,None,ctypes.byref(count),ctypes.sizeof(Header))==0xffffffff:return 0
                    data=ctypes.create_string_buffer(count.value)
                    if user.GetRawInputData(lparam,0x10000003,data,ctypes.byref(count),ctypes.sizeof(Header))==0xffffffff:return 0
                    header=Header.from_buffer_copy(data)
                    if header.kind==1 and chosen(header.handle):
                        key=Keyboard.from_buffer_copy(data,ctypes.sizeof(Header))
                        if key.vkey!=255:self.on_key(int(key.vkey),not bool(key.flags&1))
                elif message==0x00fe and wparam==2 and chosen(lparam):
                    self.on_removed();self.names.pop(int(lparam),None)
            except Exception as error:
                self.error=repr(error);self.on_removed()
            return user.DefWindowProcW(hwnd,message,wparam,lparam)
        instance=kernel.GetModuleHandleW(None);name='J280KeypadInput_'+str(self.thread_id)
        wc=Class(0,proc,0,0,instance,None,None,None,None,name);hwnd=None
        try:
            if not user.RegisterClassW(ctypes.byref(wc)):raise ctypes.WinError(ctypes.get_last_error())
            hwnd=user.CreateWindowExW(0,name,'J280KeypadInput',0,0,0,0,0,W.HWND(-3),None,instance,None)
            if not hwnd:raise ctypes.WinError(ctypes.get_last_error())
            registration=Registration(1,6,0x100|0x2000,hwnd)
            if not user.RegisterRawInputDevices(ctypes.byref(registration),1,ctypes.sizeof(Registration)):raise ctypes.WinError(ctypes.get_last_error())
            self.ready.set();message=W.MSG()
            while True:
                result=user.GetMessageW(ctypes.byref(message),None,0,0)
                if result==0:break
                if result<0:raise ctypes.WinError(ctypes.get_last_error())
                user.TranslateMessage(ctypes.byref(message));user.DispatchMessageW(ctypes.byref(message))
        except Exception as error:self.error=repr(error)
        finally:
            self.ready.set()
            if hwnd:
                registration=Registration(1,6,1,None)
                user.RegisterRawInputDevices(ctypes.byref(registration),1,ctypes.sizeof(Registration));user.DestroyWindow(hwnd)
            user.UnregisterClassW(name,instance)
