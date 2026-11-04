"""Mouse waveform capture, explicit bounded preview, and background upload."""
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk,filedialog

import draw_trajectory as trajectory
from protocol import TRAJECTORY,WRITE_CONFIG

class DrawEditor(ttk.Frame):
    def __init__(self,parent,owner):
        super().__init__(parent);self.pack(fill='both',expand=True)
        self.owner=owner;self.raw=[];self.points=[];self.recording=False;self.pointer=0
        self.uploaded=None;self.worker=None;self.cancel=threading.Event();self.events=queue.Queue()
        self._poll_job=None;self._sample_job=None
        self.status=tk.StringVar(value='按住图中鼠标左键，上下画目标；横轴按时间推进。松开结束记录。')
        ttk.Label(self,textvariable=self.status,wraplength=1020).pack(fill='x',pady=4)
        self.canvas=tk.Canvas(self,height=230,bg='#ffffff',highlightthickness=1,highlightbackground='#b7c5d4')
        self.canvas.pack(fill='x',expand=True,pady=4)
        self.canvas.bind('<ButtonPress-1>',self.begin);self.canvas.bind('<B1-Motion>',self.move)
        self.canvas.bind('<ButtonRelease-1>',self.end);self.canvas.bind('<Configure>',lambda event:self.draw())
        controls=ttk.Frame(self);controls.pack(fill='x',pady=4)
        for title,action in [('清空重画',self.clear),('生成并预览安全轨迹',self.prepare),
                             ('保存轨迹',self.save),('读取轨迹',self.load),
                             ('上传并核验（不运动）',self.start_upload),('取消上传（保持平衡）',self.abort_upload)]:
            ttk.Button(controls,text=title,command=action).pack(side='left',padx=3)
        self.play_button=ttk.Button(self,text='明确播放已核验的手绘轨迹',command=self.play,state='disabled')
        self.play_button.pack(anchor='w',pady=4)
        self.play_hint=tk.StringVar(value='播放按钮尚未启用：请先生成轨迹并完成上传、提交和独立读回。')
        self.play_hint_label=ttk.Label(self,textvariable=self.play_hint,wraplength=1020,foreground='#9c3310');self.play_hint_label.pack(fill='x')
        ttk.Label(self,text='灰线：原记录；蓝线：受限输入节点，C4在板端按固定时标生成连续参考。生成会重定时，必须查看新时长。上传只写暂存RAM；上传成功也不会自动播放。',wraplength=1020).pack(fill='x',pady=3)
        ttk.Label(self,text='目标±192计数（±66.46°），最多512个100ms节点、50.9秒、累计目标路程1圈；板端检查数据完整性、速度/加速度/jerk和任务范围。首尾参考静止后才交回原停稳逻辑。',wraplength=1020).pack(fill='x',pady=3)
        self._poll_job=self.after(50,self.poll)

    def invalidate(self):
        self.uploaded=None;self.play_button.configure(state='disabled')
        if hasattr(self,'play_hint'):self.play_hint.set('播放按钮未启用：本次连接尚无完整、已核验的上传。')

    def move(self,event):
        height=max(100,self.canvas.winfo_height())
        self.pointer=round(max(-192,min(192,(height/2-event.y)*384/(height-40))))

    def begin(self,event):
        if self.worker and self.worker.is_alive():
            self.status.set('上传期间请先取消上传，再修改记录');return
        if self._sample_job is not None:self.after_cancel(self._sample_job);self._sample_job=None
        self.invalidate();self.raw=[];self.points=[];self.recording=True;self.started=time.monotonic();self.move(event)
        self.sample()

    def sample(self):
        self._sample_job=None
        if not self.recording:return
        wanted=min(512,int((time.monotonic()-self.started)*10)+1)
        while len(self.raw)<wanted:self.raw.append(self.pointer)
        self.status.set(f'记录中：{len(self.raw)/10:.1f}s，当前{self.pointer*360/1040:+.2f}°；横轴持续推进')
        self.draw()
        if len(self.raw)>=512:self.end();return
        self._sample_job=self.after(50,self.sample)

    def end(self,event=None):
        self.recording=False
        if self._sample_job is not None:self.after_cancel(self._sample_job);self._sample_job=None
        self.status.set(f'已记录{len(self.raw)}点 / {len(self.raw)/10:.1f}s。点击生成预览，尚未上传或播放。')
        self.draw()

    def clear(self):
        if self.worker and self.worker.is_alive():self.status.set('请先取消上传');return
        self.recording=False
        if self._sample_job is not None:self.after_cancel(self._sample_job);self._sample_job=None
        self.raw=[];self.points=[];self.invalidate();self.draw()
        self.status.set('已清空；按住左键上下画目标，横轴由时间推进')

    def prepare(self):
        try:
            if self.recording:raise ValueError('先松开鼠标结束记录')
            if self.worker and self.worker.is_alive():raise ValueError('先取消上传')
            self.points=trajectory.prepare(self.raw);self.invalidate();self.draw()
            travel=sum(abs(b-a) for a,b in zip(self.points,self.points[1:]))
            self.status.set(f'蓝线已生成：{(len(self.points)-3)/10:.1f}s / {len(self.points)}点，最大{max(map(abs,self.points))/16*360/1040:.2f}°，累计{travel/16*360/1040:.1f}°；查看预览后再上传')
        except Exception as exc:self.status.set(str(exc))

    def draw(self):
        c=self.canvas;c.delete('all');width=max(300,c.winfo_width());height=max(100,c.winfo_height())
        left,right,top,bottom=58,width-12,20,height-20
        for value in (-192,0,192):
            y=top+(192-value)/384*(bottom-top)
            c.create_line(left,y,right,y,fill='#e5eaf0');c.create_text(left-5,y,text=f'{value*360/1040:+.1f}°',anchor='e')
        tracking=int(bool(getattr(self,'word_plan',None) and self.word_plan.get('tracking_mode')==1))
        step=.2 if tracking else .1
        total=max(1.,len(self.raw)*.1,len(self.points)*step)
        start=max(0,total-10) if self.recording else 0
        duration=max(1,total-start)
        for values,color in ((self.raw,'#a0aab7'),([v/16 for v in self.points],'#1769aa')):
            line=[]
            for index,value in enumerate(values):
                t=index*(step if color=='#1769aa' else .1)
                if t<start:continue
                line.extend((left+(t-start)/duration*(right-left),top+(192-value)/384*(bottom-top)))
            if len(line)>=4:c.create_line(*line,fill=color,width=2)
        c.create_text(left,height-5,text=f'{start:.1f}s',anchor='sw')
        c.create_text(right,height-5,text=f'{total:.1f}s',anchor='se')

    def save(self):
        try:
            points=trajectory.validate(self.points)
            path=filedialog.asksaveasfilename(defaultextension='.json',filetypes=[('轨迹JSON','*.json')])
            if path:Path(path).write_text(json.dumps(dict(format=trajectory.FORMAT,sample_ms=100,points=points),indent=2),encoding='utf-8')
        except Exception as exc:self.status.set(str(exc))

    def load(self):
        try:
            if self.worker and self.worker.is_alive():raise ValueError('先取消上传')
            path=filedialog.askopenfilename(filetypes=[('轨迹JSON','*.json')])
            if not path:return
            file=Path(path)
            if file.suffix.lower()!='.json' or file.stat().st_size>65536:raise ValueError('仅允许64KiB以内JSON轨迹')
            data=json.loads(file.read_text(encoding='utf-8'))
            if isinstance(data,dict) and data.get('tracking_mode',0)!=0:raise ValueError('补偿文字文件请使用文字专用上传入口，不能按普通手绘播放')
            if not isinstance(data,dict) or data.get('format')!=trajectory.FORMAT or data.get('sample_ms')!=100:raise ValueError('轨迹格式不匹配')
            self.points=trajectory.validate(data.get('points'));self.raw=[];self.recording=False;self.invalidate();self.draw()
            self.status.set(f'已读取并验证{len(self.points)}点 / {(len(self.points)-3)/10:.1f}s；尚未上传或播放')
        except Exception as exc:self.status.set(str(exc))

    def start_upload(self):
        try:
            if self.recording:raise ValueError('先结束记录并预览')
            if self.worker and self.worker.is_alive():raise ValueError('正在上传')
            points=trajectory.validate(self.points);owner=self.owner()
            tracking=int(bool(getattr(self,'word_plan',None) and self.word_plan.get('tracking_mode')==1))
            if owner is None:raise ValueError('先连接匹配固件并手动进入平衡')
            self.invalidate();self.cancel.clear();self.status.set('等待当前有限轨迹完成并静止；PID继续运行，然后写入暂存RAM')
            def work():
                try:
                    result=trajectory.upload(owner,points,self.cancel,lambda done,total:self.events.put(('progress',done,total)),tracking_mode=tracking)
                    self.events.put(('done',owner,result))
                except Exception as exc:self.events.put(('error',str(exc)))
            self.worker=threading.Thread(target=work,name='DrawUpload',daemon=True);self.worker.start()
        except Exception as exc:self.status.set(str(exc))

    def abort_upload(self,send_abort=True):
        self.cancel.set();self.invalidate()
        if send_abort and self.worker and self.worker.is_alive() and self.owner() is not None:
            try:self.owner().command(WRITE_CONFIG,252,b'\x03'+bytes(4),source='DRAW_UPLOAD')
            except Exception:pass
        self.status.set('已取消上传，不自动播放；正常取消不撤销平衡许可')

    def poll(self):
        self._poll_job=None
        try:
            while True:
                item=self.events.get_nowait()
                if item[0]=='progress':
                    self.status.set(f'正在上传：{item[1]}/{item[2]}点；尚未提交核验')
                    self.play_hint.set(f'播放按钮未启用：已传{item[1]}/{item[2]}点，等待提交和独立读回。')
                elif item[0]=='done':
                    owner,result=item[1:]
                    if not self.cancel.is_set() and owner is self.owner():
                        self.uploaded=(owner,result['checksum']);self.play_button.configure(state='normal')
                        self.status.set(f"上传、提交与独立读回通过：{result['duration_ms']/1000:.1f}s；等待明确播放")
                        self.play_hint.set('已核验，轨迹已保存。运行前手动扶直ZERO/RUN；播放会以当时位置为本次原点。STOP不会清除已核验轨迹。')
                else:
                    self.invalidate();self.status.set(item[1]);self.play_hint.set('播放按钮未启用：'+item[1])
        except queue.Empty:pass
        self._poll_job=self.after(50,self.poll)

    def destroy(self):
        self.cancel.set();self.recording=False
        for job in (self._poll_job,self._sample_job):
            if job is not None:
                try:self.after_cancel(job)
                except tk.TclError:pass
        self._poll_job=None;self._sample_job=None
        super().destroy()

    def play(self):
        try:
            owner=self.owner()
            if self.uploaded!=(owner,trajectory.checksum(self.points)) or owner is None:raise ValueError('本连接尚无匹配的已核验上传')
            owner.command(TRAJECTORY,payload=b'\x07',source='DRAW_PLAY')
            self.status.set('已请求有限手绘轨迹；以板端ACK、参考、实测和到位状态为准，PID持续运行')
        except Exception as exc:self.status.set(str(exc))
