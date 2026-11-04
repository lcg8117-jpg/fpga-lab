"""Minimal DS13 live drawing window. Startup never connects to hardware."""
from pathlib import Path
from datetime import datetime
import sys,struct,tkinter as tk
from tkinter import ttk
from client import SerialOwner
from protocol import *
from draw_editor import DrawEditor

class App(tk.Tk):
    def __init__(self):
        super().__init__();self.title('J280 手绘控制 · DS13 · R2');self.tk.call('tk','scaling',1.25)
        self.geometry('1240x720');self.minsize(1040,620);self.configure(bg='#f5f7fa')
        self.owner=None;self.closing=False;self.settings_window=None;self.pending_restore=None;self.pending_kit=None;self.kit_owner=None
        self.port=tk.StringVar();self.connection=tk.StringVar(value='未连接');self.kit=tk.StringVar(value='IP570（1040）')
        self.bank=tk.StringVar(value='未读回');self.message=''
        style=ttk.Style(self);style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',10),background='#f5f7fa',foreground='#26394d')
        style.configure('TButton',padding=(10,6));style.configure('PlotTitle.TLabel',font=('Microsoft YaHei UI',13,'bold'))
        style.configure('Small.TLabel',foreground='#64748b');style.configure('Stop.TButton',foreground='#b42318')
        page=ttk.Frame(self,padding=14);page.pack(fill='both',expand=True)
        bar=ttk.Frame(page);bar.pack(fill='x',pady=(0,8))
        self.port_box=ttk.Combobox(bar,textvariable=self.port,width=10,state='readonly',postcommand=self.ports);self.port_box.pack(side='left',padx=(0,5))
        for label,fn in [('连接',self.connect),('断开',self.disconnect),('校零',lambda:self.command(ZERO)),('平衡',lambda:self.command(RUN))]:
            ttk.Button(bar,text=label,width=6,command=fn).pack(side='left',padx=3)
        ttk.Button(bar,text='停止',width=6,style='Stop.TButton',command=lambda:self.command(STOP)).pack(side='left',padx=(10,3))
        ttk.Button(bar,text='急停',width=6,style='Stop.TButton',command=lambda:self.command(ESTOP)).pack(side='left',padx=3)
        ttk.Button(bar,text='设置',width=6,command=self.settings).pack(side='right')
        ttk.Label(bar,textvariable=self.connection,style='Small.TLabel',width=16).pack(side='right',padx=14)
        self.editor=DrawEditor(page,lambda:self.owner if self.owner and self.owner.state.connected else None)
        self.protocol('WM_DELETE_WINDOW',self.close);self.ports();self.after(120,self.poll)
    def ports(self):
        try:
            sys.path.insert(0,str(Path(__file__).parent/'vendor'))
            from serial.tools.list_ports import comports
            ports=[p.device for p in comports()];self.port_box['values']=ports
            if self.port.get() not in ports:self.port.set('')
        except Exception:self.connection.set('端口不可用')
    def connect(self):
        if self.owner and not self.owner.closed.is_set():return
        if not self.port.get():self.connection.set('选择串口');return
        self.message='';self.editor.last_error=''
        directory=Path(__file__).resolve().parents[1]/'logs'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        self.owner=SerialOwner(self.port.get(),directory).start()
    def disconnect(self):
        self.editor.end()
        if self.owner:self.owner.stop();self.connection.set('断开中')
    def command(self,op,group=0,payload=b''):
        try:
            if not self.owner:raise RuntimeError('未连接')
            self.editor.last_error='';self.message=''
            ticket=self.owner.command(op,group,payload)
            if op in (STOP,ESTOP):self.editor.end()
            return ticket
        except Exception as exc:self.message=str(exc);self.connection.set(self.message);return None
    def settings(self):
        if self.settings_window and self.settings_window.winfo_exists():self.settings_window.lift();return
        win=tk.Toplevel(self);win.title('设置');win.resizable(False,False);self.settings_window=win
        frame=ttk.Frame(win,padding=16);frame.pack(fill='both',expand=True)
        for r,values in enumerate([('S1','P','I','D','P / D'),('角度','53.78','0','210.14','1 / 5 ms'),('位置','17.67','0','616.61','5 / 25 ms')]):
            for c,value in enumerate(values):ttk.Label(frame,text=value).grid(row=r,column=c,sticky='w',padx=8,pady=5)
        ttk.Label(frame,textvariable=self.bank,style='Small.TLabel').grid(row=3,column=0,columnspan=5,sticky='w',padx=8,pady=7)
        ttk.Button(frame,text='读回参数',command=lambda:self.command(READ_GAINS,255)).grid(row=4,column=0,columnspan=2,sticky='w',padx=8,pady=5)
        ttk.Button(frame,text='恢复 S1',command=self.restore).grid(row=4,column=2,columnspan=2,sticky='w',padx=8,pady=5)
        ttk.Combobox(frame,textvariable=self.kit,values=['原套件（1080）','IP570（1040）'],state='readonly',width=18).grid(row=5,column=0,columnspan=3,sticky='w',padx=8,pady=8)
        ttk.Button(frame,text='应用套件',command=self.set_kit).grid(row=5,column=3,columnspan=2,padx=8)
        ttk.Button(frame,text='清除故障',command=lambda:self.command(CLEAR)).grid(row=6,column=0,columnspan=2,sticky='w',padx=8,pady=5)
    def stopped(self):
        if not self.owner:self.connection.set('未连接');return False
        s=self.owner.state.snapshot(include_history=False);row=s['latest']
        if not s['authenticated'] or not row or row['mode'] not in (0,1) or row['fault']:
            self.message='请先停止并清除故障';return False
        return True
    def restore(self):
        if self.stopped():self.pending_restore=self.command(SET_GAINS,255,struct.pack('<6I',*S1_BITS))
    def set_kit(self):
        if self.stopped():self.pending_kit=self.command(WRITE_CONFIG,254,b'\x05'+struct.pack('<I',int(self.kit.get().startswith('IP570'))))
    def poll(self):
        if self.owner:
            s=self.owner.state.snapshot(include_history=False);row=s['latest'] or {};c=s['last_control']
            label='未连接' if not s['connected'] else '连接中' if not s['authenticated'] else {0:'待机',1:'已校零',2:'准备中',3:'平衡中',4:'故障'}.get(row.get('mode'),'已连接')
            if row.get('fault'):label=f"故障 {row['fault']:02X}"
            if self.editor.controller.pressed and s['control_fresh'] and row.get('mode')==3 and not row.get('fault'):label='绘画中'
            self.connection.set(s['error'] or self.editor.last_error or self.editor.controller.error or self.message or label)
            if self.kit_owner is not self.owner and (254,5) in s['config_reads']:
                self.kit_owner=self.owner;self.kit.set('IP570（1040）' if s['config_reads'][(254,5)] else '原套件（1080）')
            bits=c['active_bits'] if c and s['control_fresh'] else s['independent_bits']
            if bits:self.bank.set('S1 一致' if tuple(bits)==S1_BITS else '参数与 S1 不同')
            for key in ('pending_restore','pending_kit'):
                ticket=getattr(self,key)
                if ticket:
                    with self.owner.state.lock:ack=self.owner.state.command_results.pop(ticket,None)
                    if ack:
                        setattr(self,key,None)
                        if ack['status']==0:
                            if key=='pending_restore':self.command(READ_GAINS,255)
                            else:self.command(READ_CONFIG,254,b'\x05');self.kit_owner=None
                        else:self.message='设置未获准'
        if self.closing:
            if not self.owner or self.owner.closed.is_set():self.destroy();return
        self.after(120,self.poll)
    def close(self):
        self.closing=True;self.editor.end();self.editor.cancel.set()
        if self.owner:self.owner.stop()
        else:self.destroy()

if __name__=='__main__':App().mainloop()
