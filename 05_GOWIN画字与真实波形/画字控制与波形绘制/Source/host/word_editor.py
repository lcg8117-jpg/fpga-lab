"""D1 finite word presets and measured-only XY panel; no automatic playback."""
import json
import time
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path
from draw_editor import DrawEditor
import draw_trajectory
from word_trajectory import generate, measured_trace
from word_progress import progress, coverage, return_status
from protocol import TRAJECTORY


class WordEditor(DrawEditor):
    def __init__(self,parent,owner,*,result_parent=None,show_results=None):
        self.word_plan=None;self.play_pending=None;self.play_epoch=None
        self.play_owner=None;self.play_request_id=None;self.origin_ms=None
        self.trace_frozen=False;self.data_coverage=None;self.lifecycle=None
        self.motion_interrupted=False
        self.show_results=show_results or (lambda:None)
        self.separate_result=result_parent is not None
        self.last_actual=[];self.last_actual_metadata=None;self.last_actual_lifecycle=None
        self._render_values=[];self._render_title='尚无真实字迹数据';self._render_color='#1565c0'
        super().__init__(parent,owner)
        bar=ttk.Frame(self);bar.pack(fill='x',pady=5,before=self.winfo_children()[0])
        self.play_button.pack_configure(before=self.canvas)
        self.play_hint_label.pack_configure(before=self.canvas)
        for word in ('GOWIN','EasyStart'):
            ttk.Button(bar,text=f'一键上传 {word}（不运动）',command=lambda w=word:self.upload_preset(w)).pack(side='left',padx=4)
        ttk.Label(bar,text='自动生成并上传；不自动ZERO/RUN/播放。角环和PID不改。').pack(side='left')
        self.word_status=tk.StringVar(value='C6：文字采用半速输入补偿；PID不变，包含先行运动、单次书写和返回。模型验证不代表实物已验证。')
        ttk.Label(self,textvariable=self.word_status,wraplength=1000).pack(fill='x')
        result_parent=result_parent if result_parent is not None else self
        if self.separate_result:
            ttk.Button(bar,text='查看真实字迹结果',command=self.show_results).pack(side='left',padx=6)
        self.result_status=tk.StringVar(value='真实ENC与窗口平均角速度；缺帧断线，不插值、不平滑。')
        ttk.Label(result_parent,textvariable=self.result_status,wraplength=1050).pack(fill='x',pady=4)
        self.xy=tk.Canvas(result_parent,height=360,bg='white');self.xy.pack(fill='both',expand=True)
        self.xy.bind('<Configure>',self._resize_result)
        ttk.Button(result_parent,text='导出真实ENC字迹数据（保留缺帧）',command=self.export_actual).pack(anchor='w')
        self.actual=[]

    def invalidate(self):
        super().invalidate()
        self.word_plan=None;self.play_pending=None;self.play_epoch=None
        self.play_owner=None;self.play_request_id=None;self.origin_ms=None
        self.trace_frozen=False;self.data_coverage=None;self.lifecycle=None
        self.motion_interrupted=False

    def generate_word(self,word):
        try:
            if self.recording or (self.worker and self.worker.is_alive()):raise ValueError('先结束记录或取消上传')
            program=generate(word)
            self.invalidate();self.points=program['points'];self.raw=[];self.word_plan=program;self.actual=[];self.draw()
            m=program['word_metadata']
            self.status.set(f"{word} 已生成，{len(self.points)}点 / 总目标{m['total_s']}秒；尚未上传或播放")
            self.word_status.set(f"{word}：先行运动{m['pre_motion_s']}秒，书写{m['word_s']}秒，平均速度窗口{m['window_ms']/1000}秒。"+
                f"计入返回本次起点的目标总长{m['total_s']}秒；FPGA捕获、返回到位等待另计。参考整数化误差<{m['max_integer_projection_error_count']:.2f}计数。")
            if self.separate_result:
                if not self.last_actual:self.render([],'等待实际执行；目标预览在上传页，不能当作实测字迹', '#1565c0')
            else:self.render(program['target_only_xy'],'目标程序预览：未执行，不代表实际字迹', '#888888')
        except Exception as exc:self.status.set(str(exc))

    def upload_preset(self,word):
        if self.worker and self.worker.is_alive():
            self.status.set('正在上传，请等本次核验结果；不重复发送');return
        self.generate_word(word)
        if self.word_plan and self.word_plan['word_metadata']['word']==word:self.start_upload()

    def abort_upload(self,send_abort=True):
        # STOP always cancels any in-flight staging, but a completed program
        # is inert data and remains available for a later explicit play.
        if self.worker and self.worker.is_alive():
            super().abort_upload(send_abort=send_abort);return
        self.play_pending=None;self.play_epoch=None;self.play_owner=None
        self.motion_interrupted=True
        self.status.set('运动已停止；已核验轨迹保留，之后仍需重新明确播放')

    def start_upload(self):
        # Parent invalidates the previous upload ticket, but the generated
        # plan is unchanged. Keep only that same plan across upload setup.
        plan=self.word_plan
        super().start_upload()
        if plan and self.points==plan['points']:self.word_plan=plan

    def play(self):
        try:
            owner=self.owner()
            tracking=int(bool(self.word_plan and self.word_plan.get('tracking_mode')==1))
            if owner is None or self.uploaded!=(owner,draw_trajectory.checksum(self.points,tracking)):
                raise ValueError('必须先在当前连接上传并独立读回核验')
            if not self.word_plan:
                super().play();return
            ticket=owner.command(TRAJECTORY,payload=b'\x07',source='DRAW_PLAY')
            self.play_pending=(owner,ticket,time.monotonic()+1.4)
            self.play_epoch=None;self.actual=[]
            self.play_owner=owner;self.play_request_id=None;self.origin_ms=None
            self.trace_frozen=False;self.data_coverage=None;self.lifecycle=None
            self.motion_interrupted=False
            self.status.set('已明确请求播放；等待本请求ACK，不会重发或自动RUN')
            if not self.last_actual:self.render([], '等待本次播放ACK和真实ENC数据', '#1565c0')
        except Exception as exc:self.status.set(str(exc))

    def poll(self):
        super().poll()
        if not hasattr(self,'xy'):return
        if self.play_pending:
            owner,ticket,deadline=self.play_pending
            with owner.state.lock:ack=owner.state.command_results.pop(ticket,None)
            if ack is not None:
                self.play_pending=None
                if ack['status']==0 and owner is self.owner():
                    self.play_epoch=ack['control_epoch'];self.status.set('播放ACK已确认，等待本次WAVE与完整平均窗口')
                    self.play_request_id=ack.get('request_id')
                else:self.play_epoch=None;self.status.set('播放未接受：需手动进入平衡、故障为0且没有其他运动任务；不限制旧ZERO附近，也不自动重试。')
            elif time.monotonic()>deadline:
                self.play_pending=None;self.play_epoch=None;self.status.set('播放ACK未知；不重发。请看板端状态。')
        if self.word_plan and self.play_epoch is not None:
            owner=self.owner()
            if owner is None:self.play_epoch=None;return
            if owner is not self.play_owner:
                self.status.set('连接已更换，保留旧字迹；不能作新连接的完成证据');return
            state=owner.state.snapshot()
            if not state['control_fresh']:
                self.status.set('真实控制数据过期，保留历史字迹；不补点');return
            meta=self.word_plan['word_metadata']
            phase=progress(state['word_history'],meta,self.play_epoch,origin_ms=self.origin_ms)
            self.origin_ms=phase['origin_ms']
            self.motion_interrupted=self.motion_interrupted or phase.get('interrupted',False)
            if not self.trace_frozen:
                self.actual=measured_trace(state['word_history'],meta,self.play_epoch,origin_ms=self.origin_ms)
                if phase['ended'] or phase.get('interrupted'):
                    self.trace_frozen=True;self.data_coverage=coverage(self.actual,meta)
            if self.motion_interrupted:
                message='本次运动曾中断，不能判为成功；保留已记录曲线'
            elif self.trace_frozen:
                kind,message=return_status(state,self.play_request_id,self.play_epoch,now=time.monotonic())
                quality='字迹数据完整' if self.data_coverage['complete'] else f"字迹数据不完整 {self.data_coverage['received']}/{self.data_coverage['expected']}点"
                message+='｜'+quality+'；字形是否清楚请看蓝线'
            else:message=phase['text']
            self.lifecycle=dict(phase=phase,coverage=self.data_coverage,message=message)
            self.status.set(message)
            self.result_status.set(f"{meta.get('word','本次轨迹')}｜平均窗口{meta['window_ms']/1000:g}秒｜"+message)
            if any(v is not None for v in self.actual):
                first_plot=not self.last_actual or self.last_actual_metadata is not meta
                self.last_actual=list(self.actual);self.last_actual_metadata=meta;self.last_actual_lifecycle=self.lifecycle
                self.render(self.actual,'实际ENC / 实际平均角速度；缺帧断线，未平滑', '#1565c0')
                if first_plot:self.show_results()

    def _resize_result(self,unused=None):
        self.render(self._render_values,self._render_title,self._render_color)

    def render(self,values,title,color):
        self._render_values=values;self._render_title=title;self._render_color=color
        c=self.xy;c.delete('all');w=max(300,c.winfo_width());h=max(180,c.winfo_height())
        c.create_text(15,12,text=title,anchor='nw')
        data=[v for v in values if v is not None]
        c.create_text(w/2,h-4,text='横轴：实际 Forest ENC（计数）；纵轴：窗口平均角速度（rad/s）',anchor='s')
        if not data:return
        xs=[v[-2] for v in data];ys=[v[-1] for v in data]
        lo,hi=min(xs)-1,max(xs)+1;bottom,top=min(ys),max(ys);span=max(top-bottom,.0001)
        bottom-=span*.05;top+=span*.05
        c.create_rectangle(80,35,w-15,h-40,outline='#bbbbbb')
        c.create_text(75,35,text=f'{top:.4g}',anchor='e');c.create_text(75,h-40,text=f'{bottom:.4g}',anchor='e')
        c.create_text(80,h-37,text=str(lo),anchor='nw');c.create_text(w-15,h-37,text=str(hi),anchor='ne')
        line=[]
        for row in [*values,None]:
            if row is None:
                if len(line)>=4:c.create_line(*line,fill=color,width=1.2)
                line=[];continue
            x,y=row[-2:];line.extend((80+(x-lo)/(hi-lo)*(w-95),h-40-(y-bottom)/(top-bottom)*(h-75)))

    def export_actual(self):
        try:
            values=self.actual if any(v is not None for v in self.actual) else self.last_actual
            meta=self.word_plan['word_metadata'] if values is self.actual else self.last_actual_metadata
            lifecycle=self.lifecycle if values is self.actual else self.last_actual_lifecycle
            if not any(v is not None for v in values):raise ValueError('没有真实ENC端点数据；不导出目标作实测')
            path=filedialog.asksaveasfilename(defaultextension='.json',filetypes=[('真实轨迹JSON','*.json')])
            if path:Path(path).write_text(json.dumps(dict(format='J280MeasuredWordV1',metadata=meta,
                completion=lifecycle,
                onset='first observed WAVE after accepted play ACK; complete data uncertainty<=5ms',
                columns=['relative_board_ms','actual_Forest_ENC','actual_average_rad_s'],rows=values),ensure_ascii=False),encoding='utf8')
        except Exception as exc:self.status.set(str(exc))
