"""Minimal manual V4 desktop: explicit BLE connect, balance, six positions."""
from pathlib import Path
import re
import time
import tkinter as tk
from tkinter import ttk

from client import BLEOwner
from protocol import *

ROOT=Path(__file__).resolve().parent
NAMES=('角度P','角度I','角度D','位置P','位置I','位置D')
DEFAULT_ADDRESS='D0:54:7B:AA:0C:84'

def enable_dpi_awareness():
    import os
    if os.name=='nt':
        import ctypes
        try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError,OSError):ctypes.windll.user32.SetProcessDPIAware()

def telemetry_text(s):
    """Format only current-session compact data. Used by offline QA too."""
    item=s['latest']
    if not item:return '等待已核验蓝牙数据','尚无位置数据','尚无误差数据'
    fresh=s['control_fresh'];scale=360/1040
    state={0:'未校零',1:'已停止',2:'准备中',3:'平衡中',4:'故障停机'}.get(item['mode'],f"状态{item['mode']}")
    status=f"{state} · {'数据有效' if fresh else '等待新控制数据'} · 故障{item['fault']} · 电机许可{'开' if item['flags']&4 else '关'}"
    actual=item['wide_encoder'];reference=item['wide_target'];final=item['final_target']
    position=f"实测 {actual} / {actual*scale:.3f}°    当前参考 {reference} / {reference*scale:.3f}°    最终目标 {final} / {final*scale:.3f}°"
    error=(f"跟踪误差 {actual-reference:+d}计数 / {(actual-reference)*scale:+.3f}°    "
           f"最终误差 {actual-final:+d}计数 / {(actual-final)*scale:+.3f}°    "
           f"摆杆角误差 {item['angle_error']*345/4096:+.3f}°（目标 {item['target_angle_error']*345/4096:+.3f}°）")
    return status,position,error

class App:
    def __init__(self,root):
        self.root=root;self.owner=None;self.closing=False;self.error=''
        root.title('V4 · 平衡 / 六位置 · 蓝牙');root.geometry('1130x800');root.minsize(1000,720)
        style=ttk.Style(root);style.configure('TLabel',font=('Microsoft YaHei UI',10));style.configure('TButton',padding=(10,6))
        outer=ttk.Frame(root,padding=16);outer.pack(fill='both',expand=True)
        row=ttk.Frame(outer);row.pack(fill='x')
        ttk.Label(row,text='BT10 蓝牙地址').pack(side='left')
        self.address=tk.StringVar(value=DEFAULT_ADDRESS)
        ttk.Entry(row,textvariable=self.address,width=22).pack(side='left',padx=8)
        self.connect_button=ttk.Button(row,text='连接',command=self.connect);self.connect_button.pack(side='left',padx=4)
        ttk.Button(row,text='断开并停止',command=self.disconnect).pack(side='left',padx=4)
        self.identity=tk.StringVar(value='未连接 · 启动后由你点击连接')
        ttk.Label(outer,textvariable=self.identity).pack(fill='x',pady=(8,5))
        row=ttk.Frame(outer);row.pack(fill='x',pady=7)
        tk.Button(row,text='STOP 停止',bg='#bb242d',fg='white',font=('Microsoft YaHei UI',13,'bold'),
            padx=20,pady=6,command=lambda:self.send(STOP)).pack(side='left',padx=(0,12))
        for label,op in (('扶直校零',ZERO),('开始平衡',RUN),('清除故障',CLEAR)):
            ttk.Button(row,text=label,command=lambda opcode=op:self.send(opcode)).pack(side='left',padx=4)
        self.notice=tk.StringVar(value='先停止并扶直校零，再开始平衡；六位置按钮按下时，以当时实测位置为原点。')
        ttk.Label(outer,textvariable=self.notice,foreground='#9b351b',wraplength=1060).pack(fill='x',pady=4)
        frame=ttk.LabelFrame(outer,text='六位置',padding=10);frame.pack(fill='x',pady=8)
        self.position_buttons=[]
        for n in range(1,7):
            button=ttk.Button(frame,text=f'{n} · 相对 {n*60}°',command=lambda i=n:self.send(POSITION,payload=bytes((i,))))
            button.grid(row=0,column=n-1,sticky='ew',padx=4);frame.columnconfigure(n-1,weight=1);self.position_buttons.append(button)
        self.cancel_button=ttk.Button(frame,text='取消移动，平滑停转并保持',command=lambda:self.send(CANCEL_HOLD))
        self.cancel_button.grid(row=1,column=0,columnspan=6,sticky='w',padx=4,pady=(10,0))
        self.status=tk.StringVar(value='尚无板端数据');self.position=tk.StringVar(value='尚无位置数据');self.errors=tk.StringVar(value='尚无误差数据')
        for variable in (self.status,self.position,self.errors):
            ttk.Label(outer,textvariable=variable,wraplength=1060).pack(fill='x',pady=3)
        plot=ttk.LabelFrame(outer,text='误差 · 跟踪蓝色 / 最终位置橙色（计数） · 摆杆角绿色（°） · 零参考始终水平',padding=4)
        plot.pack(fill='both',expand=True,pady=10)
        self.canvas=tk.Canvas(plot,height=200,bg='#fafbfc',highlightthickness=0);self.canvas.pack(fill='both',expand=True)
        frame=ttk.LabelFrame(outer,text='平衡参数 · 停止后才能提交，积分固定为0',padding=10);frame.pack(fill='x',pady=6)
        self.drafts=[tk.StringVar(value=format(v,'.9g')) for v in DEFAULT_GAINS]
        for i,name in enumerate(NAMES):
            ttk.Label(frame,text=name).grid(row=0,column=i,padx=8,sticky='w')
            ttk.Entry(frame,textvariable=self.drafts[i],width=12,state='readonly' if i in (1,4) else 'normal').grid(row=1,column=i,padx=8,pady=4,sticky='ew')
            frame.columnconfigure(i,weight=1)
        self.submit=ttk.Button(frame,text='停机后提交',command=self.set_gains);self.submit.grid(row=2,column=0,columnspan=2,sticky='w',pady=4)
        ttk.Button(frame,text='独立读回',command=lambda:self.send(READ_GAINS,255)).grid(row=2,column=2,columnspan=2,sticky='w')
        self.gains=tk.StringVar(value='实际采用 / 已保存 / 独立读回：等待蓝牙数据')
        ttk.Label(frame,textvariable=self.gains,wraplength=1030).grid(row=3,column=0,columnspan=6,sticky='w',pady=4)
        self.log=tk.StringVar(value='');ttk.Label(outer,textvariable=self.log,wraplength=1060).pack(fill='x',pady=3)
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(100,self.refresh)
        root.bind('<Configure>',self._resize,add='+')
    def _resize(self,event):
        if event.widget is not self.root:return
        def walk(parent):
            for widget in parent.winfo_children():
                if isinstance(widget,ttk.Label) and int(widget.cget('wraplength') or 0)>0:
                    widget.configure(wraplength=max(300,min(self.root.winfo_width()-70,widget.master.winfo_width()-24)))
                walk(widget)
        walk(self.root)
    def connect(self):
        if self.owner and not self.owner.closed.is_set():self.error='连接正在运行，请先断开';return
        address=self.address.get().strip().upper()
        if not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}',address):self.error='请输入完整的BT10地址';return
        self.error='';self.owner=BLEOwner(address,ROOT/'logs').start()
    def send(self,opcode,group=0,payload=b''):
        try:
            if not self.owner:raise RuntimeError('请先连接蓝牙')
            self.owner.command(opcode,group,payload);self.error=''
        except Exception as exc:self.error=str(exc)
    def set_gains(self):
        try:
            if not self.owner:raise RuntimeError('请先连接蓝牙')
            self.owner.set_gains([v.get() for v in self.drafts]);self.error=''
        except Exception as exc:self.error=str(exc)
    def disconnect(self):
        if self.owner:self.owner.stop(wait=False)
    def close(self):
        if self.closing:return
        self.closing=True;self.disconnect();self._close_when_ready()
    def _close_when_ready(self):
        if not self.owner or self.owner.closed.is_set():self.root.destroy();return
        self.root.after(100,self._close_when_ready)
    def refresh(self):
        if self.closing:return
        if self.owner:
            s=self.owner.state.snapshot()
            self.identity.set(('R27身份已核验' if s['authenticated'] else '等待R27身份核验' if not self.owner.closed.is_set() else '已断开')+
                f' · FW {FW:08X} / schema {SCHEMA:08X} · 蓝牙')
            self.notice.set(self.error or s['error'] or s['log_error'] or s['message'])
            self.status.set(telemetry_text(s)[0]);self.position.set(telemetry_text(s)[1]);self.errors.set(telemetry_text(s)[2])
            def fmt(values):return ' / '.join(format(v,'.7g') for v in values) if values else '等待数据'
            d=s['diagnostic']
            actual=fmt(d['active_gains']) if d else '等待数据'
            age=f"（{max(0,time.monotonic()-s['diagnostic_rx']):.1f}s前诊断）" if d else ''
            self.gains.set(f"实际采用{age}：{actual}\n已保存：{fmt(s['stored'])}    独立读回：{fmt(s['independent'])}")
            ready=bool(s['control_fresh'] and s['latest']['mode']==3 and not s['latest']['fault'] and not s['error'] and not s['log_error'])
            for button in self.position_buttons+[self.cancel_button]:button.configure(state='normal' if ready else 'disabled')
            writable,_=self.owner.parameter_write_status();self.submit.configure(state='normal' if writable else 'disabled')
            self.connect_button.configure(state='normal' if self.owner.closed.is_set() else 'disabled')
            self.log.set(f"{s['log_status']} · CRC错误{s['crc_errors']} · 格式错误{s['format_errors']} · 丢帧{s['sequence_gaps']} · 超时{s['timeouts']}")
            self.draw(s['history'])
        else:
            for button in self.position_buttons+[self.cancel_button,self.submit]:button.configure(state='disabled')
            if self.error:self.notice.set(self.error)
        self.root.after(100,self.refresh)
    def draw(self,history):
        c=self.canvas;c.delete('all');w=max(300,c.winfo_width());h=max(120,c.winfo_height())
        if len(history)<2:c.create_text(w/2,h/2,text='等待已核验的平衡数据',fill='#77818f');return
        end=history[-1]['board_ms'];rows=[r for r in history if ((end-r['board_ms'])&0xffffffff)<=12000]
        if len(rows)<2:return
        left=70;right=w-12;span=max(100,end-rows[0]['board_ms'])
        def panel(top,bottom,series,unit,minimum):
            limit=max(minimum,max(abs(fn(r)) for _,_,fn in series for r in rows))*1.12
            middle=(top+bottom)/2
            c.create_line(left,middle,right,middle,fill='#6b7785',width=1)
            for value,y in ((limit,top),(0,middle),(-limit,bottom)):
                c.create_text(left-8,y,text=f'{value:.1f}',anchor='e',fill='#657183')
            c.create_text(left+4,top+2,text=unit,anchor='nw',fill='#657183')
            for color,width,fn in series:
                points=[]
                for r in rows:
                    points.extend((left+(r['board_ms']-rows[0]['board_ms'])/span*(right-left),middle-fn(r)/limit*(bottom-top)/2))
                c.create_line(*points,fill=color,width=width)
        panel(12,h*.51,(
            ('#cf7c20',1,lambda r:r['wide_encoder']-r['final_target']),
            ('#3283c6',2,lambda r:r['wide_encoder']-r['wide_target'])), '位置误差 / 计数',2)
        panel(h*.62,h-26,(('#15805b',2,lambda r:r['angle_error']*345/4096),), '摆杆角误差 / °',.5)
        c.create_text(left,h-10,text=f'-{span/1000:.1f}s',anchor='w',fill='#657183');c.create_text(right,h-10,text='现在',anchor='e',fill='#657183')

def main():
    enable_dpi_awareness();root=tk.Tk();App(root);root.mainloop()

if __name__=='__main__':main()
