"""30-second real-Tk load with 40k plotted samples and simulated delayed ACKs."""
from pathlib import Path
import ctypes,json,math,struct,sys,time,hashlib
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'host'),str(ROOT/'tests')]
import app,protocol as p
from test_host import Tests
from test_r2_stream import PointDelayedSerial
from ctypes import wintypes as wt

def forbidden(*a,**k):raise AssertionError('No physical serial access in GUI load check')
app.SerialOwner=forbidden
check=Tests();fake=PointDelayedSerial(.02);owner=check.owner(fake)
window=app.App();window.withdraw();window.geometry('1040x620-20000-20000');window.update_idletasks()
user=ctypes.windll.user32;user.GetParent.argtypes=[wt.HWND];user.GetParent.restype=wt.HWND
user.GetWindowLongW.argtypes=[wt.HWND,ctypes.c_int];user.GetWindowLongW.restype=ctypes.c_long
user.SetWindowLongW.argtypes=[wt.HWND,ctypes.c_int,ctypes.c_long]
hwnd=user.GetParent(window.winfo_id());user.SetWindowLongW(hwnd,-20,user.GetWindowLongW(hwnd,-20)|0x08000000)
window.owner=owner;e=window.editor;e.last_owner=owner;e.controller.is_held=None
try:
    e.controller.begin(0);check.wait_for(lambda:e.controller.job is not None)
    e.timeline.job_id=e.controller.job;e.timeline.origin=0;e.timeline.tracking=False;e.timeline.cursor=150
    for i in range(40000):
        e.timeline.rows.append(dict(t=i*150/40000,target_deg=25*math.sin((i//4)*.003),actual_deg=24*math.sin(i*.00075-.2),reference_deg=0,
            point_sequence=i//4,gap=False))
    with owner.state.lock:
        c=owner.state.last_control;e.last_epoch=c['control_epoch']
        owner.state.history.clear()
        owner.state.history.extend(dict(c,control_epoch=(c['control_epoch']-(39999-i)*5)&0xffffffff,received_at=time.monotonic()) for i in range(40000))
    window.deiconify();started=time.perf_counter();events=0
    while time.perf_counter()-started<30:
        e.move(SimpleNamespace(y=250+120*math.sin((time.perf_counter()-started)*2)))
        events+=1;window.update();time.sleep(.005)
    points=[r for r in fake.writes if r[5]==p.LIVE_POINT]
    times=[fake.write_times[struct.unpack_from('<I',r,8)[0]] for r in points]
    result=dict(passed=False,hardware_actions=0,duration_seconds=time.perf_counter()-started,display_rows=len(e.timeline.rows),
        stored_control_rows=len(owner.state.history),mouse_events=events,points=len(points),
        point_interval_max_ms=max((b-a)*1000 for a,b in zip(times,times[1:])),lease_expired=fake.lease_expired,
        stroke_error=e.controller.error,host_error=owner.state.error,pressed=e.controller.pressed)
    assert result['pressed'] and result['lease_expired']==0 and not result['stroke_error'] and not result['host_error'],result
    assert len(points)>900 and result['point_interval_max_ms']<180,result
    result['passed']=True
    result['source_sha256']={str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in [ROOT/'host/draw_editor.py',ROOT/'host/client.py',ROOT/'host/live_control.py',Path(__file__)]}
    (ROOT/'reports/R2_GUI_LOAD_CHECK.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
finally:
    e.controller.end();owner.stop(wait=True);e.destroy();window.destroy()
