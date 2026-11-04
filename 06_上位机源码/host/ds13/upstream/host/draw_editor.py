"""Live drawing and truthful encoder comparison. No upload or replay stage."""
import csv,ctypes,threading,time,tkinter as tk
from tkinter import ttk,filedialog
from live_control import LiveStroke
from timeline import Timeline,DEGREES_PER_COUNT

def target_plot_rows(rows):
    """One vertex per received target update; preserve holds and telemetry gaps."""
    previous=None;tail=None
    for row in rows:
        if row['gap']:
            if tail is not None and tail is not previous:yield tail
            yield row;previous=row;tail=row
        else:
            if previous is None or row['point_sequence']!=previous['point_sequence'] or row['target_deg']!=previous['target_deg']:
                yield row;previous=row
            tail=row
    if tail is not None and tail is not previous:yield tail

class DrawEditor(ttk.Frame):
    def __init__(self,parent,get_owner):
        super().__init__(parent);self.pack(fill='both',expand=True)
        self.get_owner=get_owner;self.controller=LiveStroke(get_owner,lambda:bool(ctypes.windll.user32.GetAsyncKeyState(1)&0x8000));self.timeline=Timeline()
        self.cancel=threading.Event();self.pointer=0;self.last_owner=None;self.last_epoch=None;self.closed=False;self.last_error=''
        self.cleared_job=None
        self.status=tk.StringVar(value='按住左图上下画，实物实时跟随；松开平滑停止')
        charts=ttk.Frame(self);charts.pack(fill='both',expand=True);charts.columnconfigure((0,1),weight=1,uniform='charts');charts.rowconfigure(1,weight=1)
        ttk.Label(charts,text='手绘',style='PlotTitle.TLabel').grid(row=0,column=0,sticky='w',padx=8,pady=5)
        ttk.Label(charts,text='实际',style='PlotTitle.TLabel').grid(row=0,column=1,sticky='w',padx=8,pady=5)
        self.canvas=tk.Canvas(charts,height=330,bg='white',highlightthickness=1,highlightbackground='#cbd5e1',cursor='crosshair')
        self.actual_canvas=tk.Canvas(charts,height=330,bg='white',highlightthickness=1,highlightbackground='#cbd5e1')
        self.canvas.grid(row=1,column=0,sticky='nsew',padx=(0,7));self.actual_canvas.grid(row=1,column=1,sticky='nsew',padx=(7,0))
        ttk.Label(charts,text='目标').grid(row=2,column=0,sticky='w',padx=8,pady=5)
        ttk.Label(charts,text='目标（虚线） / 实际').grid(row=2,column=1,sticky='w',padx=8,pady=5)
        self.canvas.bind('<ButtonPress-1>',self.begin);self.canvas.bind('<B1-Motion>',self.move)
        self.canvas.bind('<ButtonRelease-1>',self.end);self.canvas.bind('<Leave>',self.leave)
        self.canvas.bind('<FocusOut>',self.focus_out)
        for c in (self.canvas,self.actual_canvas):c.bind('<Configure>',lambda e:self.draw())
        bar=ttk.Frame(self);bar.pack(fill='x',pady=(8,3))
        ttk.Button(bar,text='清空',command=self.clear).pack(side='left',padx=6)
        ttk.Button(bar,text='导出',command=self.export).pack(side='left',padx=6)
        self.readout=tk.StringVar(value='真实数据等待连接')
        ttk.Label(bar,textvariable=self.readout,style='Small.TLabel').pack(side='right',padx=6)
        self._poll_job=self.after(40,self.poll)
    def move(self,event):
        h=max(180,self.canvas.winfo_height());top,bottom=22,h-34
        degree=120-(event.y-top)*240/(bottom-top)
        self.pointer=max(-192,min(192,round(degree/DEGREES_PER_COUNT)))
        self.controller.move(self.pointer)
        if self.controller.pressed:self.draw()
    def begin(self,event):
        self.canvas.focus_set();self.move(event)
        self.last_error=''
        try:self.controller.begin(self.pointer)
        except Exception as exc:self.last_error=str(exc);return
        self.status.set('实时跟随中 · 松开鼠标停止');self.draw()
    def end(self,event=None):
        self.controller.end();self.draw()
    def leave(self,event=None):
        self.controller.end('鼠标离开绘图区，平滑停止')
    def focus_out(self,event=None):
        self.controller.end('窗口失去焦点，平滑停止')
    def clear(self):
        self.cleared_job=self.controller.job or self.timeline.job_id
        self.controller.end('清空绘图区，本笔结束')
        self.timeline=Timeline();self.draw();self.status.set('按住左图开始新的一笔')
    def poll(self):
        if self.closed:return
        owner=self.get_owner()
        if owner is not self.last_owner:
            self.controller.end('设备连接已变化');self.last_owner=owner;self.last_epoch=None;self.cleared_job=None;self.timeline=Timeline()
        if self.cancel.is_set():self.controller.end('窗口正在关闭')
        fresh=False
        if owner:
            s=owner.state.snapshot();fresh=s['control_fresh']
            for row in s['history']:
                epoch=row['control_epoch']
                if self.last_epoch is not None and not 0<((epoch-self.last_epoch)&0xffffffff)<0x80000000:continue
                self.last_epoch=epoch
                if row['job_id']!=self.cleared_job:self.timeline.ingest(row,row.get('received_at'))
            if self.controller.pressed and (not fresh or not s['last_control'] or s['last_control']['mode']!=3 or s['last_control']['fault']):
                self.controller.end('数据或平衡状态变化，本笔结束')
            if s['error']:self.status.set(s['error'])
            elif self.controller.error:self.status.set(self.controller.error)
            elif self.controller.pressed:self.status.set('边画边动 · 松开鼠标平滑停止')
            elif self.timeline.rows:self.status.set(self.timeline.state)
            elif s['authenticated']:self.status.set('启动平衡后，按住左图上下画')
        else:self.status.set('先连接设备并启动平衡，再按住左图绘画')
        if self.timeline.rows:
            last=self.timeline.rows[-1]
            self.readout.set(f"{last['t']:.2f}s   目标 {last['target_deg']:+.1f}°   实际 {last['actual_deg']:+.1f}°"+('' if fresh else ' · 数据已过期'))
        else:self.readout.set('')
        self.draw();self._poll_job=self.after(40,self.poll)
    def draw(self):
        rows=list(self.timeline.rows);total=max(5,self.timeline.cursor+1)
        for c,real in ((self.canvas,False),(self.actual_canvas,True)):
            c.delete('all');w=max(260,c.winfo_width());h=max(180,c.winfo_height());left,right,top,bottom=53,w-16,22,h-34
            def xy(t,value):return left+t/total*(right-left),top+(120-value)/240*(bottom-top)
            for value in (-120,-60,0,60,120):
                _,y=xy(0,value);c.create_line(left,y,right,y,fill='#e1e9f0' if value else '#aebdce');c.create_text(left-6,y,text=f'{value}°',anchor='e',fill='#526477')
            for n in range(5):
                t=total*n/4;x,_=xy(t,0);c.create_line(x,top,x,bottom,fill='#eff3f7');c.create_text(x,bottom+16,text=f'{t:.1f}s',fill='#526477',anchor='w' if n==0 else 'e' if n==4 else 'center')
            stride=max(1,len(rows)//max(1,int(right-left)));shown=[]
            for i in range(0,len(rows),stride):
                batch=rows[i:i+stride];indices={0,len(batch)-1}
                for key in ('actual_deg','target_deg','reference_deg'):
                    indices.add(min(range(len(batch)),key=lambda j:batch[j][key]));indices.add(max(range(len(batch)),key=lambda j:batch[j][key]))
                indices.update(j for j,r in enumerate(batch) if r['gap']);shown.extend(batch[j] for j in sorted(indices))
            traces=[('target_deg','#8db8e2' if real else '#1769b5',1 if real else 2)]
            if real:traces.extend([('actual_deg','#df701d',2)])
            for key,color,width in traces:
                segment=[]
                def flush():
                    if len(segment)>=4:c.create_line(*segment,fill=color,width=width,joinstyle='round',capstyle='round',dash=(4,3) if real and key=='target_deg' else ())
                for row in target_plot_rows(rows) if key=='target_deg' else shown:
                    if row['gap']:flush();segment=[]
                    segment.extend(xy(row['t'],row[key]))
                flush()
            if rows:
                x,_=xy(self.timeline.cursor,0);c.create_line(x,top,x,bottom,fill='#64788e',dash=(3,3))
                if real:
                    x,y=xy(rows[-1]['t'],rows[-1]['actual_deg']);c.create_oval(x-4,y-4,x+4,y+4,fill='#df701d',outline='white')
            if not real:
                if self.controller.pressed:
                    _,y=xy(0,self.pointer*DEGREES_PER_COUNT);c.create_line(left,y,right,y,fill='#71859c',dash=(2,3));c.create_text(right-5,y-12,text=f'笔尖 {self.pointer*DEGREES_PER_COUNT:+.1f}°',anchor='e',fill='#1769b5')
                elif not rows:c.create_text((left+right)/2,(top+bottom)/2,text='按住绘画',fill='#71859c',font=('Microsoft YaHei UI',15))
    def export(self):
        rows=list(self.timeline.rows)
        if not rows:self.status.set('还没有真实对照数据');return
        path=filedialog.asksaveasfilename(defaultextension='.csv',filetypes=[('CSV','*.csv')])
        if path:
            with open(path,'w',newline='',encoding='utf-8-sig') as f:
                writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
            self.status.set(f'已导出 {len(rows)} 条原始实测对照数据')
    def destroy(self):
        self.closed=True;self.controller.end('绘图区关闭')
        if self._poll_job is not None:self.after_cancel(self._poll_job);self._poll_job=None
        super().destroy()
