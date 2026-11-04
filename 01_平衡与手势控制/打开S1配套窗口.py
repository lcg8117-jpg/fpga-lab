"""Original ADB1 UI, S1 entry defaults; default offline, no automatic mutations."""
from pathlib import Path
import argparse, importlib.util, json, os, re, struct, sys, time
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT/'原版完整包'),str(ROOT/'原版完整包/vendor')]
from host import protocol as p
from host import app
import tkinter as tk

S1=(53.78,0.,210.14,17.67,0.,616.61)
p.DEFAULT_GAINS=S1
spec=importlib.util.spec_from_file_location('shared_hardware_mutex',ROOT/'evidence/g3_hardware_mutex.py')
lease=importlib.util.module_from_spec(spec);spec.loader.exec_module(lease)

class LockedUSB:
    def __init__(self,port):
        if not isinstance(port,str) or not re.fullmatch(r'COM[1-9][0-9]*',port,re.IGNORECASE):raise ValueError('请手动填写已确认的本机USB串口，例如COM5')
        import serial
        self.lock=lease.SharedMutex();self.lock.__enter__();self.serial=None
        try:
            self.serial=serial.Serial(port=None,baudrate=2000000,timeout=.002,write_timeout=.02,exclusive=True)
            self.serial.dtr=False;self.serial.rts=False;self.serial.port=port;self.serial.open()
        except BaseException:
            self.lock.__exit__(None,None,None);raise
    def __getattr__(self,name):return getattr(self.serial,name)
    def close(self):
        try:self.serial.close()
        finally:self.lock.__exit__(None,None,None)

app.open_usb=LockedUSB

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port');parser.add_argument('--smoke',action='store_true');args=parser.parse_args()
    assert not (args.smoke and args.port)
    runtime=ROOT/'runtime';runtime.mkdir(exist_ok=True)
    root=tk.Tk();window=app.Window(root,runtime/'logs',initial_port=args.port)
    root.title('ADB1旧版封存 · S1 · 原完整手势功能 · 自适应试验前保持关闭')
    def proof():
        s=window.owner.snapshot();row=s.get('latest') or {};ext=s.get('extension') or {}
        fields=['connected','authenticated','error','received','crc_errors','format_errors','timeouts','unknown_actions','independent_gains']
        data={k:s.get(k) for k in fields}
        transport=window.owner.transport
        data.update(unix=time.time(),pid=os.getpid(),port=getattr(transport,'port',None),simulated=bool(getattr(transport,'simulated',False)) if transport else None,
                    title=root.title(),mapped=bool(root.winfo_ismapped()),
                    mode=row.get('mode'),fault=row.get('fault'),board_ms=row.get('board_ms'),
                    firmware_id=row.get('firmware_id'),schema=row.get('schema'),
                    motor_permitted=row.get('motor_permitted_at_completion'),pwm=row.get('actual_applied_q15'),
                    manual_gains=row.get('manual_gains'),active_gains=row.get('active_gains'),
                    adaptive_enabled=ext.get('adaptation_enabled',row.get('adaptive_enabled_at_launch')),
                    periods_ms=[ext.get(k) for k in ('angle_P_ms','angle_D_ms','position_P_ms','position_D_ms')],
                    automatic_gain_write=False,automatic_ZERO_RUN=False)
        for name in ('manual_gains','active_gains'):
            if data[name]:data[name+'_bits']=[f'{v:08x}' for v in struct.unpack('<6I',struct.pack('<6f',*data[name]))]
        path=runtime/('OFFLINE_SMOKE.json' if args.smoke else 'LIVE_STATE.json');tmp=path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
        root.after(500,proof)
    root.after(1000,proof)
    if args.smoke:root.after(2200,window.close)
    root.mainloop()

if __name__=='__main__':main()
