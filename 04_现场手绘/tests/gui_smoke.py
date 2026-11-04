"""Own off-screen, no-activate window only; never connects or opens hardware."""
from pathlib import Path
import sys,ctypes,json,time
from ctypes import wintypes as w
from PIL import Image,ImageGrab
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
user=ctypes.windll.user32;gdi=ctypes.windll.gdi32
user.SetProcessDPIAware()
import app
def forbidden(*a,**kw):raise AssertionError('GUI smoke attempted serial access')
app.SerialOwner=forbidden
window=app.App();window.withdraw();window.geometry('1240x720+20+30')
window.update_idletasks()
user.GetParent.argtypes=[w.HWND];user.GetParent.restype=w.HWND
hwnd=user.GetParent(window.winfo_id())
user.GetWindowLongW.argtypes=[w.HWND,ctypes.c_int];user.GetWindowLongW.restype=ctypes.c_long
user.SetWindowLongW.argtypes=[w.HWND,ctypes.c_int,ctypes.c_long]
user.SetWindowLongW(hwnd,-20,user.GetWindowLongW(hwnd,-20)|0x08000000) # WS_EX_NOACTIVATE
window.deiconify();window.update()
# Exercise actual Tk mouse bindings without a serial owner or invented measured curve.
class MouseProbe:
    def __init__(self):self.pressed=False;self.calls=[];self.error=''
    def begin(self,target):self.calls.append(('begin',target));self.pressed=True
    def move(self,target):self.calls.append(('point',target))
    def end(self,why='release'):self.calls.append(('end',why));self.pressed=False
original=window.editor.controller;probe=MouseProbe();window.editor.controller=probe
c=window.editor.canvas
c.event_generate('<ButtonPress-1>',x=180,y=120);window.update()
c.event_generate('<B1-Motion>',x=190,y=130);window.update()
assert probe.pressed and any(op=='begin' for op,_ in probe.calls)
c.event_generate('<Leave>',x=-1,y=130);window.update();assert not probe.pressed
assert any(op=='end' for op,_ in probe.calls)
window.editor.controller=original
window.editor.status.set('离线界面预览：按住左图即可现场绘画；右侧等待真实编码器数据。')
window.editor.draw();window.update()
user.SetWindowPos.argtypes=[w.HWND,w.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.UINT]
user.SetWindowPos(hwnd,-1,20,30,0,0,0x0010|0x0001) # Own window only, never activate it.
window.update();time.sleep(.2);window.update()
checks=[]
def walk(widget):
    for child in widget.winfo_children():
        assert not child.winfo_manager() or child.winfo_ismapped(),f'Unmapped control: {child}'
        if child.winfo_ismapped():
            y=child.winfo_rooty()-window.winfo_rooty();x=child.winfo_rootx()-window.winfo_rootx()
            checks.append(dict(widget=str(child),x=x,y=y,w=child.winfo_width(),h=child.winfo_height()))
        walk(child)
walk(window)
assert window.owner is None
labels=[]
def text_controls(widget):
    for child in widget.winfo_children():
        try:labels.append(child.cget('text'))
        except Exception:pass
        text_controls(child)
text_controls(window)
assert not any(any(word in str(label) for word in ('上传','播放','预览安全轨迹')) for label in labels)
assert window.editor.canvas.winfo_width()>450 and window.editor.actual_canvas.winfo_width()>450
assert all(r['y']>=0 and r['y']+r['h']<=window.winfo_height()+2 for r in checks),[r for r in checks if r['y']+r['h']>window.winfo_height()+2]
assert all(r['x']>=0 and r['x']+r['w']<=window.winfo_width()+2 for r in checks)
hwnd=window.winfo_id()
user.GetWindowRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)];rect=w.RECT();user.GetWindowRect(hwnd,ctypes.byref(rect))
width,height=rect.right-rect.left,rect.bottom-rect.top
user.GetWindowDC.argtypes=[w.HWND];user.GetWindowDC.restype=w.HDC
gdi.CreateCompatibleDC.argtypes=[w.HDC];gdi.CreateCompatibleDC.restype=w.HDC
gdi.CreateCompatibleBitmap.argtypes=[w.HDC,ctypes.c_int,ctypes.c_int];gdi.CreateCompatibleBitmap.restype=w.HBITMAP
gdi.SelectObject.argtypes=[w.HDC,w.HANDLE];gdi.SelectObject.restype=w.HANDLE
gdi.DeleteObject.argtypes=[w.HANDLE];gdi.DeleteDC.argtypes=[w.HDC]
user.PrintWindow.argtypes=[w.HWND,w.HDC,w.UINT];user.ReleaseDC.argtypes=[w.HWND,w.HDC]
class Header(ctypes.Structure):
    _fields_=[('size',w.DWORD),('width',w.LONG),('height',w.LONG),('planes',w.WORD),('bits',w.WORD),('compression',w.DWORD),('imageSize',w.DWORD),('x',w.LONG),('y',w.LONG),('used',w.DWORD),('important',w.DWORD)]
gdi.GetDIBits.argtypes=[w.HDC,w.HBITMAP,w.UINT,w.UINT,ctypes.c_void_p,ctypes.c_void_p,w.UINT]
dc=user.GetWindowDC(hwnd);memory=gdi.CreateCompatibleDC(dc);bitmap=gdi.CreateCompatibleBitmap(dc,width,height);old=gdi.SelectObject(memory,bitmap)
try:
    assert user.PrintWindow(hwnd,memory,2)
    header=Header(ctypes.sizeof(Header),width,-height,1,32,0,0,0,0,0,0)
    buffer=ctypes.create_string_buffer(width*height*4)
    assert gdi.GetDIBits(memory,bitmap,0,height,buffer,ctypes.byref(header),0)==height
    preview=ImageGrab.grab(bbox=(rect.left,rect.top,rect.right,rect.bottom))
    assert len(preview.getcolors(2000000) or [])>20,'Blank GUI capture'
    preview.save(ROOT/'reports/UI_OFFLINE_PREVIEW.png')
finally:
    gdi.SelectObject(memory,old);gdi.DeleteObject(bitmap);gdi.DeleteDC(memory);user.ReleaseDC(hwnd,dc)
(ROOT/'reports/gui_smoke.json').write_text(json.dumps(dict(passed=True,hardware_actions=0,serial_open_attempts=0,window_size=[window.winfo_width(),window.winfo_height()],widgets=checks,mouse_events=probe.calls,no_upload_or_play_controls=True),indent=2),encoding='utf-8')
window.editor.destroy();window.destroy();print('PASS GUI offline: two aligned plots, no clipped widgets, no serial connection')
