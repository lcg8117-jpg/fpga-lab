"""Portable07/S1 UI. Opening only fills PID/port fields; no automatic commands."""
from pathlib import Path
import argparse,importlib.util,json,os,re,struct,sys,time
ROOT=Path(__file__).resolve().parent
ORIGINAL=ROOT/'原版完整包'
sys.path[:0]=[str(ORIGINAL),str(ORIGINAL/'vendor')]
from host import app,protocol as p
S1=(53.78,0.,210.14,17.67,0.,616.61)
assert p.FIRMWARE==0x41444231 and p.SCHEMA==0x41444203
p.DEFAULT_GAINS=S1
spec=importlib.util.spec_from_file_location('team07_hardware_lease',ROOT/'evidence/hardware_mutex.py')
lease=importlib.util.module_from_spec(spec);spec.loader.exec_module(lease)

class LockedUSB:
    def __init__(self,port):
        if not isinstance(port,str) or not re.fullmatch(r'COM[1-9][0-9]*',port,re.I):
            raise ValueError('请选择本机USB串口')
        import serial
        self.lock=lease.SharedMutex();self.lock.__enter__();self.serial=None;self.closed=False
        try:
            self.serial=serial.Serial(port=None,baudrate=2000000,timeout=.002,write_timeout=.02,exclusive=True)
            self.serial.dtr=False;self.serial.rts=False;self.serial.port=port;self.serial.open()
        except BaseException:
            self.lock.__exit__(None,None,None);raise
    def __getattr__(self,name):return getattr(self.serial,name)
    def close(self):
        if self.closed:return
        self.closed=True
        try:self.serial.close()
        finally:self.lock.__exit__(None,None,None)

app.open_usb=LockedUSB

class Window(app.Window):
    def __init__(self,root,logs):
        self.initializing=True
        super().__init__(root,logs,initial_port=None)
        self.initializing=False
    def _connect(self,port):
        if self.initializing:
            self.mode_text='USB尚未连接';return
        return super()._connect(port)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port');parser.add_argument('--smoke',action='store_true')
    args=parser.parse_args();state=ROOT/'runtime_state';state.mkdir(exist_ok=True)
    root=app.tk.Tk()
    if args.smoke:root.withdraw()
    window=Window(root,state/'logs')
    root.title('J280 07号 · 指令调度修复 · S1 · P5/25ms')
    port=args.port
    if not port and not args.smoke:
        from serial.tools import list_ports
        ports=[x.device for x in list_ports.comports() if x.vid==0x1a86 and x.pid==0x7523]
        if len(ports)==1:port=ports[0]
    if port:window.port.set(port)
    window.status.set('07号 / S1草稿已填；点击“连接 USB”，再写入六参数并独立读回。')
    def proof():
        s=window.owner.snapshot() if window.owner else {};t=s.get('latest') or {};e=s.get('extension') or {}
        row=dict(observed_unix=time.time(),pid=os.getpid(),title=root.title(),
            input_gains=[float(x.get()) for x in window.gain_vars],
            connected=s.get('connected',False),authenticated=s.get('authenticated',False),
            manual_gains=t.get('manual_gains'),active_gains=t.get('active_gains'),
            independent_gains=s.get('independent_gains'),mode=t.get('mode'),fault=t.get('fault'),
            actual_applied_q15=t.get('actual_applied_q15'),adaptive_enabled=e.get('adaptation_enabled'),
            firmware_id=t.get('firmware_id'),schema=t.get('schema'),logging=s.get('logging'),
            automatic_gain_write=False,automatic_ZERO_RUN_SWING=False)
        if args.smoke:
            assert window.owner is None
            assert struct.pack('<6f',*row['input_gains'])==struct.pack('<6f',*S1)
            assert p.gains_payload(row['input_gains'])==struct.pack('<6f',*S1)
            row.update(passed=True,no_transport_created=True,firmware_id=p.FIRMWARE,schema=p.SCHEMA)
        path=state/('SMOKE_REPORT.json' if args.smoke else 'LIVE_STATE.json')
        tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(row,ensure_ascii=False,indent=2),'utf-8');tmp.replace(path)
        if args.smoke:window.close()
        else:root.after(1000,proof)
    root.after(500,proof);root.mainloop()

if __name__=='__main__':main()
