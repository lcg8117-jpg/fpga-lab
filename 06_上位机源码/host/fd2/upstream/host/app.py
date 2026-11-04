"""Chinese three-variant Host. No serial connection is made before Connect."""
import argparse
from pathlib import Path
import json
import math
import struct
import time
import tkinter as tk
from tkinter import ttk,filedialog,messagebox

from client import SerialOwner
from protocol import *
from session_log import choose_log_directory,GIB

NAMES=('角度P','角度I','角度D','位置P','位置I','位置D')
FIELD_NAMES={0:'正向驱动',1:'反向驱动',2:'正向时间',3:'底部连续资格',4:'等待底部超时',5:'反向时间',6:'捕获等待',8:'最大尝试次数',9:'重试时间步长',64:'第一组脉冲',65:'第二组脉冲',66:'第三组脉冲'}
PROFILE_NAMES=('单次激励起摆','惯性辅助起摆','峰值触发多组脉冲起摆')

def enable_dpi_awareness():
    import os
    if os.name=='nt':
        import ctypes
        try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError,OSError):ctypes.windll.user32.SetProcessDPIAware()

def swing_description(item):
    if item['mode']!=5:return '起摆未运行'
    stage=item['swing_stage']
    if item['swing_profile']==2:return f'峰值脉冲源阶段{stage}'
    return {0:'源准备 / 等待接平衡',10:'等待连续下垂静止（尚未施力）',
            11:'正向激励',13:'反向激励',40:'自由滑行，等待顶部捕获',
            70:'重试前等待下垂静止',90:'源失败，输出禁止'}.get(stage,f'起摆源阶段{stage}')

def action_description(item):
    # The packet reports the latest action globally, including GUI commands.
    # Do not invent a causal association with the last optical event.
    reason=item['action_reason']
    return {0:'尚无动作判定',1:'已接受：启动起摆',
            2:'起摆未接受：正在运行或起摆资格不足',
            3:'已接受：旋转 / 受控减速',
            4:'动作未接受：状态、重复或范围资格',
            5:'仅识别，无对应动作 / 当前未平衡',
            6:'挥手STOP：已撤销输出'}.get(reason,f'未知动作码{reason}')

def motion_reference_text(item,start,epoch):
    target=item['wide_target'];actual=item['wide_encoder']
    error=actual-target;scale=360/1040
    origin='本段参考起点未观察到（中途连接）' if start is None else (
        f'本段前一保持参考={start}（epoch{epoch}），目标增量={target-start:+d}计数 / {(target-start)*scale:+.8f}°')
    return (origin+'\n'+f'累计目标/实测={target*scale:.8f}° / {actual*scale:.8f}°；'
            f'目标相位={target%1040}/1040（{target%1040*scale:.8f}°）；'
            f'未取模误差={error:+d}计数 / {error*scale:+.8f}°')

def gain_average_text(average):
    if not average['valid']:
        reason={'control_expired':'控制过期 / 未连接','awaiting_control':'等待控制事务',
                'not_coherent':'非控制状态','fault':'故障停机','stopped':'停车 / 未平衡',
                'motor_inhibited':'电机输出未获许可','adaptive_disabled':'自适应已关闭',
                'invalid_gains':'实际参数无效','insufficient_duration':'尚无完整5ms区间',
                'disconnected':'已断开'}.get(average['reason'],average['reason'])
        return '实际角P/D时间平均不可用：'+reason
    def change(value):
        if value is None:return '基准0，比例不可用'
        return ('↑' if value>0 else '↓' if value<0 else '→')+f'{value:+.5g}%'
    return '\n'.join(f"{window['window_ms']//1000}s实际均值：角P={window['kp']:.8g}（{change(window['kp_change_percent'])}） / 角D={window['kd']:.8g}（{change(window['kd_change_percent'])}）；"
        f"覆盖{window['covered_ms']}/{window['window_ms']}ms，{window['intervals']}段{'，部分覆盖' if not window['complete'] else ''}；自适应来源{window['adopted_percent']:.5g}%（含回基准）"
        for window in average['windows'])

class App:
    def __init__(self,root,variant):
        self.root=root;self.variant=variant;self.owner=None;self.closing=False;self.ui_error=''
        self.schema=json.loads((Path(__file__).parent/'swingup_schema.json').read_text(encoding='utf8'))
        root.title(f'Forest D1 / J280 — V{variant} 独立控制台（FD2）');root.geometry('1280x960');root.minsize(1180,900)
        style=ttk.Style(root);style.configure('TButton',padding=(8,5));style.configure('TLabel',font=('Microsoft YaHei UI',10))
        outer=ttk.Frame(root,padding=10);outer.pack(fill='both',expand=True)
        connection=ttk.Frame(outer);connection.pack(fill='x')
        self.port=tk.StringVar();self.logdir=tk.StringVar(value=str(choose_log_directory()))
        ttk.Label(connection,text='串口').pack(side='left');ttk.Entry(connection,textvariable=self.port,width=12).pack(side='left',padx=5)
        ttk.Label(connection,text='2,000,000 baud · 8N1').pack(side='left',padx=5)
        ttk.Button(connection,text='连接',command=self.connect).pack(side='left',padx=5)
        ttk.Button(connection,text='断开并停止',command=self.disconnect).pack(side='left')
        self.identity=tk.StringVar(value=f'等待V{variant}身份确认 · 连接不会自动校零或启动')
        ttk.Label(connection,textvariable=self.identity).pack(side='left',padx=12)
        controls=ttk.Frame(outer);controls.pack(fill='x',pady=8)
        tk.Button(controls,text='STOP 停止',bg='#c62828',fg='white',font=('Microsoft YaHei UI',14,'bold'),command=lambda:self.send(STOP)).pack(side='left',padx=(0,8))
        for label,opcode in [('ESTOP 锁定急停',ESTOP),('ZERO 直立校零',ZERO),('RUN 仅平衡',RUN),('CLEAR 清除故障',CLEAR)]:
            ttk.Button(controls,text=label,command=lambda op=opcode:self.send(op)).pack(side='left',padx=4)
        self.notice=tk.StringVar(value='V5已移除自动起摆；请手动扶直、ZERO后使用RUN。关闭窗口会请求STOP。' if variant==5 else '请先扶直并明确执行ZERO；本窗口不会恢复旧参数。关闭窗口会请求STOP。')
        ttk.Label(outer,textvariable=self.notice,foreground='#9c3310',wraplength=1130).pack(fill='x',pady=(0,5))
        self.summary=tk.StringVar(value='尚无板端数据')
        ttk.Label(outer,textvariable=self.summary,wraplength=1130).pack(fill='x',pady=4)
        actual_box=ttk.LabelFrame(outer,text='唯一当前值：本控制事务实际使用的六参数',padding=(8,4));actual_box.pack(fill='x',pady=3)
        self.actual_primary=tk.StringVar(value='尚无新鲜控制事务；状态帧/诊断帧不会冒充当前参数')
        ttk.Label(actual_box,textvariable=self.actual_primary,wraplength=1100,font=('Microsoft YaHei UI',11,'bold')).pack(anchor='w')
        self.actual_meta=tk.StringVar(value='等待control epoch、版本和输出许可')
        ttk.Label(actual_box,textvariable=self.actual_meta,wraplength=1100).pack(anchor='w')
        notebook=ttk.Notebook(outer,height=260);notebook.pack(fill='x',expand=False,pady=3)
        pid=self._page(notebook,'PID草稿 / 读回 / 实际采用',10)
        self.drafts=[tk.StringVar(value=format(v,'.9g')) for v in DEFAULT_GAINS]
        self.saved=[tk.StringVar(value='—') for _ in NAMES];self.reads=[tk.StringVar(value='—') for _ in NAMES];self.adopted=[tk.StringVar(value='—') for _ in NAMES]
        for column,text in enumerate(('参数','待提交草稿','已保存（ACK）','独立READ读回','已用于控制')):ttk.Label(pid,text=text).grid(row=0,column=column,padx=12,pady=3,sticky='w')
        for row,name in enumerate(NAMES,1):
            ttk.Label(pid,text=name).grid(row=row,column=0,sticky='w',padx=12,pady=3)
            ttk.Entry(pid,textvariable=self.drafts[row-1],width=18).grid(row=row,column=1,padx=12)
            for column,variables in ((2,self.saved),(3,self.reads),(4,self.adopted)):ttk.Label(pid,textvariable=variables[row-1],width=18).grid(row=row,column=column,padx=12,sticky='w')
        action=ttk.Frame(pid);action.grid(row=7,column=0,columnspan=5,sticky='w',pady=8)
        ttk.Button(action,text='原子提交6项PID',command=self.set_gains).pack(side='left',padx=5)
        ttk.Button(action,text='独立读回PID',command=lambda:self.send(READ_GAINS,255)).pack(side='left',padx=5)
        ttk.Button(action,text='49/0/251/25/0/610 填入草稿',command=self.default_draft).pack(side='left',padx=5)
        self.revisions=tk.StringVar(value='已保存 / 独立读回 / 实际采用版本：—')
        ttk.Label(pid,textvariable=self.revisions).grid(row=8,column=0,columnspan=5,sticky='w',padx=12)
        ttk.Label(pid,text='I默认0；I仅允许STOP后修改。P/D运行中原子提交；手动提交会撤销自适应所有权。草稿不会被轮询覆盖。').grid(row=9,column=0,columnspan=5,sticky='w',padx=12,pady=5)
        self.swing_values={};self.swing_reads={};self.swing_saved={};self.swing_tabs=None
        if variant in (1,2,4):self._build_swing(notebook)
        if variant==2:self._build_motion(notebook)
        if variant==3:self._build_adaptive(notebook)
        if variant>=4:self._build_positions(notebook)
        if variant in (2,4,5):self._build_trace(notebook)
        self._build_kit(notebook)
        self._build_logging(notebook)
        plotbox=ttk.LabelFrame(outer,text='实际控制事务波形 · 完整200Hz样本写日志',padding=4);plotbox.pack(fill='both',expand=True,pady=5)
        self.canvas=tk.Canvas(plotbox,height=100,bg='white',highlightthickness=0);self.canvas.pack(fill='both',expand=True)
        self.detail=tk.StringVar(value='控制未就绪时的10Hz状态帧不会计为新控制样本。')
        ttk.Label(pid,textvariable=self.detail,wraplength=1080).grid(row=10,column=0,columnspan=5,sticky='w',padx=12,pady=5)
        self.log_status=tk.StringVar(value='日志尚未开始')
        ttk.Label(outer,textvariable=self.log_status,wraplength=1130).pack(fill='x',pady=3)
        ttk.Label(outer,text='断开串口不等于硬件急停；停止请先STOP并确认实际输出为0。未实机验证的版本须按随包说明调试。',foreground='#9c3310',wraplength=1130).pack(fill='x')
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(100,self.refresh)
        self._wrap_job=None
        root.bind('<Configure>',self.resize_text,add='+')
    def resize_text(self,event):
        if event.widget is not self.root:return
        if self._wrap_job:self.root.after_cancel(self._wrap_job)
        self._wrap_job=self.root.after(80,self._apply_wrap)
    def _apply_wrap(self):
        self._wrap_job=None
        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child,ttk.Label) and int(child.cget('wraplength') or 0)>0:
                    child.configure(wraplength=max(300,self.root.winfo_width()-70))
                walk(child)
        walk(self.root)
    def _page(self,notebook,title,padding=8):
        wrapper=ttk.Frame(notebook);notebook.add(wrapper,text=title)
        canvas=tk.Canvas(wrapper,highlightthickness=0,borderwidth=0)
        scrollbar=ttk.Scrollbar(wrapper,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set);scrollbar.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        content=ttk.Frame(canvas,padding=padding);window=canvas.create_window(0,0,window=content,anchor='nw')
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(window,width=event.width))
        content.bind('<Configure>',lambda unused:canvas.configure(scrollregion=canvas.bbox('all')))
        def wheel(event):
            under=canvas.winfo_containing(event.x_root,event.y_root)
            if under and str(under).startswith(str(wrapper)):
                canvas.yview_scroll(int(-event.delta/120) or (-1 if event.delta>0 else 1),'units');return 'break'
        canvas.bind_all('<MouseWheel>',wheel,add='+');wrapper.scroll_canvas=canvas;wrapper.scroll_content=content
        return content
    def _build_swing(self,notebook):
        frame=self._page(notebook,'起摆独立参数库')
        bar=ttk.Frame(frame);bar.pack(fill='x')
        ttk.Button(bar,text='选择当前页方案（不启动）',command=self.select_swing).pack(side='left',padx=5)
        ttk.Button(bar,text='启动板端已选方案',command=lambda:self.send(START_SWING)).pack(side='left',padx=5)
        ttk.Button(bar,text='读回本页参数',command=self.read_swing).pack(side='left',padx=5)
        self.swing_tabs=ttk.Notebook(frame);self.swing_tabs.pack(fill='both',expand=True,pady=6)
        for profile in range(3 if self.variant in (1,4) else 1):
            page=ttk.Frame(self.swing_tabs,padding=5);self.swing_tabs.add(page,text=PROFILE_NAMES[profile])
            for c,text in enumerate(('参数 / 范围','待提交草稿','独立读回','保存版本','操作')):ttk.Label(page,text=text).grid(row=0,column=c,padx=8,sticky='w')
            for row,field in enumerate((x for x in self.schema['fields'] if profile in x['profiles']),1):
                key=(profile,field['id']);draft=tk.StringVar(value=str(field['defaults'][profile]));read=tk.StringVar(value='—');saved=tk.StringVar(value='—')
                self.swing_values[key]=draft;self.swing_reads[key]=read;self.swing_saved[key]=saved
                ttk.Label(page,text=f"{FIELD_NAMES[field['id']]}  {field['min']}～{field['max']} {field['unit']}").grid(row=row,column=0,padx=8,pady=2,sticky='w')
                ttk.Entry(page,textvariable=draft,width=14).grid(row=row,column=1,padx=8)
                ttk.Label(page,textvariable=read,width=16).grid(row=row,column=2,padx=8,sticky='w')
                ttk.Label(page,textvariable=saved,width=12).grid(row=row,column=3,padx=8,sticky='w')
                ttk.Button(page,text='提交并独立读回',command=lambda k=key,f=field:self.write_swing(k,f)).grid(row=row,column=4,padx=8)
            if profile==2:ttk.Label(page,text='保留原固定60ms启动等待、20ms捕获、第二组91ms补偿、第三组内部减1、第四组以后继续；不限制为三组。').grid(row=5,column=0,columnspan=5,sticky='w',pady=8)
    def _build_motion(self,notebook):
        frame=self._page(notebook,'转圈 / 手势')
        commandbar=ttk.Frame(frame);commandbar.grid(row=0,column=0,columnspan=4,sticky='w')
        for index,(label,opcode) in enumerate((('顺时针恰好一圈',MOTION_CW),('逆时针恰好一圈',MOTION_CCW),('持续向左旋转',MOTION_LEFT),('持续向右旋转',MOTION_RIGHT),('受控减速并保持当前位置',MOTION_STOP))):
            ttk.Button(commandbar,text=label,command=lambda op=opcode:self.send(op)).grid(row=index//3,column=index%3,padx=8,pady=3,sticky='ew')
        ttk.Label(frame,text='360°为一机械圈：原1080或IP570 1040物理计数，均为1040 Forest单位。单圈参考从旧保持目标增加/减少1040，终点与起点同相位。\n参考结束可接下一动作，实际停稳另行核验；反向滑动减速停转，挥手/STOP撤销输出。',wraplength=1060).grid(row=1,column=0,columnspan=4,padx=8,pady=2,sticky='nw')
        self.motion_detail=tk.StringVar(value='等待独立诊断中的真实速度、轨迹终点和收敛结果')
        ttk.Label(frame,textvariable=self.motion_detail,wraplength=1080).grid(row=2,column=0,columnspan=4,sticky='w',padx=8,pady=2)
        self.gesture_cfg={};self.gesture_reads={}
        for row,(field,label,maximum,default) in enumerate(((1,'方向交换位（LR/CW/前后）',7,0),(2,'左转对应正方向',1,1),(3,'顺时针对应正方向',1,1),(4,'启用手势',1,1)),3):
            variable=tk.StringVar(value=str(default));self.gesture_cfg[field]=variable
            actual=tk.StringVar(value='未读回');self.gesture_reads[field]=actual
            ttk.Label(frame,text=label).grid(row=row,column=0,sticky='w',padx=8)
            ttk.Entry(frame,textvariable=variable,width=8).grid(row=row,column=1,sticky='w',padx=20)
            ttk.Button(frame,text='STOP后提交并读回',command=lambda f=field,m=maximum:self.system_config(f,m)).grid(row=row,column=2,pady=3)
            ttk.Label(frame,textvariable=actual).grid(row=row,column=3,padx=12,sticky='w')
        ttk.Button(frame,text='独立读回方向与手势配置',command=lambda:[self.send(READ_CONFIG,254,bytes((f,))) for f in self.gesture_cfg]).grid(row=7,column=0,columnspan=3,sticky='w',pady=6)
    def _build_positions(self,notebook):
        frame=self._page(notebook,'六位置'+(' / 六轨迹' if self.variant==5 else ''))
        for n in range(1,7):
            ttk.Button(frame,text=f'{n}：相对 {n*60}°',command=lambda i=n:self.send(POSITION,payload=bytes((i,)))).grid(row=0,column=n-1,padx=5,pady=4)
        ttk.Label(frame,text='每次独立按下，以当时实测累计位置为原点；360°完整转一圈。新目标立即替换旧目标；保持平衡，参考速度/加速度继续过渡。',wraplength=1040).grid(row=1,column=0,columnspan=6,sticky='w')
        ttk.Button(frame,text='取消当前计划并平滑停转保持',command=lambda:self.send(CANCEL_HOLD)).grid(row=2,column=0,columnspan=3,sticky='w',pady=5)
        if self.variant==5:
            names=('渐入渐出正弦','圆滑三角往返','分段台阶驻留','圆顶驻留往返','递增幅度三段','有界扫频往返')
            for n,name in enumerate(names,1):ttk.Button(frame,text=f'{n}：{name}',command=lambda i=n:self.send(TRAJECTORY,payload=bytes((i,)))).grid(row=3+(n-1)//3,column=(n-1)%3*2,columnspan=2,sticky='ew',pady=3,padx=4)
            ttk.Label(frame,text='有限次数结束后返回该次实测原点并保持；预期播放时间=周期×次数，另加起点捕获与末端收敛。实际完成以板端到位资格为准。',wraplength=1030).grid(row=5,column=0,columnspan=6,sticky='w',pady=5)
            ttk.Label(frame,text='本V5无自动起摆：手动扶直、ZERO后使用顶部RUN进入平衡，再执行位置或轨迹。',wraplength=1030).grid(row=6,column=0,columnspan=6,sticky='w',pady=5)
    def _build_trace(self,notebook):
        frame=self._page(notebook,'通信 / 目标 / 操作证据')
        self.goal_text=tk.StringVar(value='等待板端起点、最终目标、动作请求与实际收敛诊断')
        ttk.Label(frame,textvariable=self.goal_text,wraplength=1060).pack(anchor='w',pady=4)
        self.trace_text=tk.StringVar(value='尚无操作记录')
        ttk.Label(frame,textvariable=self.trace_text,wraplength=1060).pack(anchor='w',pady=4)
        if self.variant>=4:
            row=ttk.Frame(frame);row.pack(fill='x',pady=6)
            self.route_mode=tk.StringVar(value='USB');self.ble_address=tk.StringVar()
            ttk.Label(row,text='连接方式').pack(side='left');ttk.Combobox(row,textvariable=self.route_mode,values=('USB','USB+BLE'),state='readonly',width=10).pack(side='left',padx=5)
            ttk.Label(row,text='BT10地址').pack(side='left');ttk.Entry(row,textvariable=self.ble_address,width=22).pack(side='left',padx=5)
            ttk.Label(row,text='随机地址 / FFE0 / UART 230400').pack(side='left',padx=5)
            ttk.Label(frame,text=f'本V{self.variant}按两次完整资源失败的例外暂缓BLE遥测；蓝牙按键使用USB+BLE，波形与新鲜状态由USB取得。',wraplength=1060).pack(anchor='w',pady=3)
            row=ttk.Frame(frame);row.pack(fill='x',pady=4)
            self.keyboard_match=tk.StringVar();self.key_layer=tk.StringVar(value='1');self.key_reader=None;self.key_actions=None
            ttk.Label(row,text='指定键盘设备路径片段').pack(side='left');ttk.Entry(row,textvariable=self.keyboard_match,width=30).pack(side='left',padx=5)
            ttk.Button(row,text='枚举设备',command=self.list_keyboards).pack(side='left',padx=4)
            box=ttk.Combobox(row,textvariable=self.key_layer,values=('1','2','3'),state='readonly',width=3);box.pack(side='left',padx=4);box.bind('<<ComboboxSelected>>',lambda unused:self.change_key_layer())
            ttk.Button(row,text='启用指定键盘',command=self.arm_keyboard).pack(side='left',padx=4)
            ttk.Button(row,text='禁用键盘',command=self.disarm_keyboard).pack(side='left',padx=4)
            keyboard_help='V5层1 A/F：STOP/保持；B-E原起摆键已禁用。请手动扶直、ZERO后用顶部RUN；层2 G-L：60..360°；层3 M-R：轨迹1..6。改层前松开全部键。' if self.variant==5 else '默认层1 A-F：STOP/起摆/选SWM4/选SA21/选144/保持；层2 G-L：60..360°；层3 M-R：轨迹1..6。键位见keymap.json，可编辑；改层前松开全部键。'
            ttk.Label(frame,text=keyboard_help,wraplength=1060).pack(anchor='w',pady=4)
        config=self._page(notebook,'运动限速 / 轨迹参数')
        self.planner_values={};self.planner_reads={}
        fields=[(0,'有限动作速度（计数/s）',260),(1,'加速度 80/160/320/640/1200',320),(2,'加加速度 400/800/1600/3200/12000',1600)]
        if self.variant>=4:fields += [(6,'方向 0负/1正',1)]
        if self.variant==5:fields += [(7,'轨迹幅度（计数，10..520）',87),(8,'周期 ms（见下方可选值）',16000),(9,'有限周期次数 1..8',2)]
        for row,(field,label,default) in enumerate(fields):
            value=tk.StringVar(value=str(default));read=tk.StringVar(value='未读回');self.planner_values[field]=value;self.planner_reads[field]=read
            ttk.Label(config,text=label).grid(row=row,column=0,sticky='w',padx=5,pady=4);ttk.Entry(config,textvariable=value,width=14).grid(row=row,column=1,padx=5)
            ttk.Label(config,textvariable=read).grid(row=row,column=2,padx=12)
            ttk.Button(config,text='STOP后提交并读回',command=lambda f=field:self.write_planner(f)).grid(row=row,column=3,padx=5)
        ttk.Label(config,text='周期可选：4000/5000/6000/8000/10000/12000/16000/20000/24000/30000/40000/48000/60000 ms。\n制动模型只读：600计数/s²、200ms滞后、12计数余量；这是离线保守假设，实物仍须验证。',wraplength=1050).grid(row=len(fields),column=0,columnspan=4,sticky='w',pady=8)
        ttk.Button(config,text='独立读回全部本页字段',command=lambda:[self.send(READ_CONFIG,253,bytes((f,))) for f in self.planner_values]).grid(row=len(fields)+1,column=0,columnspan=3,sticky='w')
    def write_planner(self,field):
        try:self.send(WRITE_CONFIG,253,bytes((field,))+struct.pack('<I',int(self.planner_values[field].get())))
        except Exception as exc:self.show_error(exc)
    def list_keyboards(self):
        try:
            from raw_keyboard import devices
            messagebox.showinfo('可选键盘设备','\n\n'.join(d['path'] for d in devices()))
        except Exception as exc:self.show_error(exc)
    def arm_keyboard(self):
        try:
            if not self.owner or not self.owner.state.snapshot()['authenticated']:raise RuntimeError('先连接并核验本版本身份')
            from raw_keyboard import RawKeyboard,held_control_keys
            from key_actions import KeyActions,load_mapping
            self.disarm_keyboard();mapping=load_mapping(Path(__file__).parent/'keymap.json');self.key_actions=KeyActions(self.owner,mapping)
            control_keys={ord(key) for entries in mapping.values() for key in entries}
            self.key_actions.set_layer(self.key_layer.get());self.key_actions.arm(held_control_keys(control_keys))
            self.key_reader=RawKeyboard(self.keyboard_match.get(),self.key_actions.on_key,self.key_actions.removed);self.key_reader.start()
        except Exception as exc:self.disarm_keyboard();self.show_error(exc)
    def change_key_layer(self):
        if self.key_actions:self.key_actions.set_layer(self.key_layer.get())
    def disarm_keyboard(self):
        if getattr(self,'key_actions',None):self.key_actions.disarm()
        if getattr(self,'key_reader',None):self.key_reader.stop();self.key_reader=None
    def draw_motion_overlay(self,state):
        from motion_plot import draw_motion
        draw_motion(self.canvas,state)
    def _build_adaptive(self,notebook):
        frame=self._page(notebook,'板侧自适应')
        ttk.Button(frame,text='明确启用 / 重新交回自适应所有权',command=lambda:self.send(ADAPT_ENABLE)).pack(anchor='w',pady=5)
        ttk.Button(frame,text='关闭自适应，使用手动基准',command=lambda:self.send(ADAPT_DISABLE)).pack(anchor='w',pady=5)
        self.adapt_detail=tk.StringVar(value='等待板端自适应状态')
        ttk.Label(frame,textvariable=self.adapt_detail,wraplength=1060).pack(anchor='w',pady=6)
        self.adapt_average=tk.StringVar(value='等待真实coherent事务的实际P/D时间平均')
        ttk.Label(frame,textvariable=self.adapt_average,wraplength=1060).pack(anchor='w',pady=3)
        self.adapt_events=tk.StringVar(value='等待实际采用变化与板侧学习事件；两者计数独立')
        ttk.Label(frame,textvariable=self.adapt_events,wraplength=1060).pack(anchor='w',pady=6)
        ttk.Label(frame,text='板侧完成候选、采用和回滚；电脑仅显示和记录。手动PID撤销自适应；非零I冻结P/D自调。\n偏移单位为基准/256；D不低于手动基准。均值仅说明实际参数趋势，不证明稳定性改善。',wraplength=1060).pack(anchor='w',pady=6)
    def _build_kit(self,notebook):
        frame=self._page(notebook,'套件 / 编码器 / 手动平衡诊断' if self.variant==5 else '套件 / 编码器 / 起摆诊断',15)
        self.kit_choice=tk.StringVar(value='0 原套件（1080物理计数/圈）')
        ttk.Combobox(frame,textvariable=self.kit_choice,state='readonly',width=46,
                     values=('0 原套件（1080物理计数/圈）','1 WHEELTEC IP570（1040物理计数/圈）')).grid(row=0,column=0,sticky='w',pady=5)
        ttk.Button(frame,text='STOP后明确提交套件并读回',command=self.write_kit).grid(row=0,column=1,padx=8)
        ttk.Button(frame,text='独立读回套件与分辨率',command=self.read_kit).grid(row=1,column=0,sticky='w',pady=5)
        self.kit_readback=tk.StringVar(value='未独立读回；默认0原套件，不能根据显示猜接线')
        ttk.Label(frame,textvariable=self.kit_readback,wraplength=1060).grid(row=2,column=0,columnspan=2,sticky='w',pady=7)
        self.kit_detail=tk.StringVar(value='等待编码器和手动平衡诊断' if self.variant==5 else '等待编码器和起摆诊断')
        ttk.Label(frame,textvariable=self.kit_detail,wraplength=1060).grid(row=3,column=0,columnspan=2,sticky='w',pady=7)
        ttk.Label(frame,text='切换只改变套件明确配置，不猜PID。板端将使ZERO失效：请先确认实物接线与A/B方向，再扶直手动ZERO。\n两套套件均经过输入确认；电气与最高转速的实物兼容性以随包验证说明为准。不会自动ZERO或RUN。',wraplength=1060).grid(row=4,column=0,columnspan=2,sticky='w',pady=7)
    def write_kit(self):
        try:
            if not self.owner:raise RuntimeError('请先连接并核验固件身份')
            self.owner.set_kit(int(self.kit_choice.get().split()[0]));self.ui_error=''
        except Exception as exc:self.show_error(exc)
    def read_kit(self):
        for field in (5,6,7):self.send(READ_CONFIG,254,bytes((field,)))
    def _build_logging(self,notebook):
        frame=self._page(notebook,'日志 / 存储策略',15)
        ttk.Label(frame,text='日志目录（可配置，优先选择数据盘）').grid(row=0,column=0,sticky='w')
        ttk.Entry(frame,textvariable=self.logdir,width=84).grid(row=1,column=0,columnspan=3,sticky='ew',pady=5)
        ttk.Button(frame,text='选择目录',command=self.choose_log).grid(row=1,column=3,padx=8)
        self.max_gib=tk.StringVar(value='2');self.free_gib=tk.StringVar(value='10');self.reserve_mib=tk.StringVar(value='8')
        for row,(label,variable) in enumerate((('本目录共享额度 GiB',self.max_gib),('磁盘最小空闲 GiB',self.free_gib),('关键预留 MiB',self.reserve_mib)),2):
            ttk.Label(frame,text=label).grid(row=row,column=0,sticky='w',pady=6);ttk.Entry(frame,textvariable=variable,width=12).grid(row=row,column=1,sticky='w')
        ttk.Label(frame,text='策略在本次连接建立时锁定。200Hz原始帧每条只保存一次，后台无损压缩，含本机双时钟、板时刻、sequence、epoch索引。\n断开后自动完成ZIP；历史日志不会自动删除。满额/磁盘错误/队列溢出明确告警、请求STOP并阻止非STOP控制变更，保活线程继续运行。\n位置P/I/D保持25ms，日志携带position_component_epoch；未就绪10Hz状态帧明确标记为非控制样本。',wraplength=1060).grid(row=5,column=0,columnspan=4,sticky='w',pady=12)
    def choose_log(self):
        value=filedialog.askdirectory(initialdir=self.logdir.get() if Path(self.logdir.get()).exists() else str(Path.home()))
        if value:self.logdir.set(value)
    def connect(self):
        try:
            if self.owner and not self.owner.closed.is_set():raise RuntimeError('已有一个串口所有者；请先断开')
            port=self.port.get().strip()
            mode=self.route_mode.get() if hasattr(self,'route_mode') else 'USB'
            if mode!='BLE' and not port:raise ValueError('请填写实际串口名称，不使用固定COM默认值')
            options={'max_shared_bytes':int(float(self.max_gib.get())*GIB),'min_free_bytes':int(float(self.free_gib.get())*GIB),'reserve_bytes':int(float(self.reserve_mib.get())*1024**2)}
            if self.variant in (1,2,3,4,5):
                from trace_log import TraceLogger
                from log_policy import shared_roots
                options['shared_roots']=shared_roots(Path(self.logdir.get()))
                owner=SerialOwner(self.variant,port,Path(self.logdir.get()),logger_factory=TraceLogger,logger_options=options)
                if mode!='USB':
                    from dual_transport import DuplexPort
                    address=self.ble_address.get().strip()
                    if not address:raise ValueError('请填写本机扫描确认的BT10地址；不会使用历史地址自动连接')
                    owner.serial_factory=lambda unused_port,unused_baud:DuplexPort(port if mode=='USB+BLE' else '',address,logger=owner.logger,event_sink=owner.trace)
                self.owner=owner;owner.start()
            else:self.owner=SerialOwner(self.variant,port,Path(self.logdir.get()),logger_options=options).start()
            self.ui_error=''
        except Exception as exc:self.show_error(exc)
    def send(self,opcode,group=0,payload=b''):
        try:
            if not self.owner:raise RuntimeError('请先连接并核验固件身份')
            self.owner.command(opcode,group,payload)
            self.ui_error=''
        except Exception as exc:self.show_error(exc)
    def set_gains(self):
        try:
            if not self.owner:raise RuntimeError('请先连接')
            values=[float(v.get()) for v in self.drafts]
            with self.owner.state.lock:self.owner.state.manual_drafts=[v.get() for v in self.drafts]
            self.owner.set_gains(values)
            self.ui_error=''
        except Exception as exc:self.show_error(exc)
    def show_error(self,error):
        self.ui_error=str(error);self.notice.set(self.ui_error)
    def default_draft(self):
        for variable,value in zip(self.drafts,DEFAULT_GAINS):variable.set(format(value,'.9g'))
    def select_swing(self):self.send(SELECT_SWING,0,bytes((self.swing_tabs.index('current'),)))
    def read_swing(self):
        profile=self.swing_tabs.index('current')
        for p,field in self.swing_values:
            if p==profile:self.send(READ_CONFIG,profile,bytes((field,)))
        self.send(READ_CONFIG,254,b'\0')
    def write_swing(self,key,field):
        try:
            value=int(self.swing_values[key].get())
            if not field['min']<=value<=field['max']:raise ValueError('起摆参数超出范围')
            self.send(WRITE_CONFIG,key[0],bytes((key[1],))+struct.pack('<I',value))
        except Exception as exc:self.show_error(exc)
    def system_config(self,field,maximum):
        try:
            value=int(self.gesture_cfg[field].get())
            if not 0<=value<=maximum:raise ValueError('系统配置超出范围')
            self.send(WRITE_CONFIG,254,bytes((field,))+struct.pack('<I',value))
        except Exception as exc:self.show_error(exc)
    def disconnect(self):
        self.disarm_keyboard()
        if self.owner:self.owner.stop(wait=False)
    def close(self):
        if self.closing:return
        self.closing=True
        self.disarm_keyboard()
        if self.owner:self.owner.stop(wait=False)
        self._finish_close()
    def _finish_close(self):
        if self.owner and not self.owner.closed.is_set():self.root.after(50,self._finish_close)
        else:self.root.destroy()
    def refresh(self):
        if self.closing:return
        if self.owner:
            state=self.owner.state.snapshot();latest=state['latest'];control=state['last_control']
            fresh=state['control_fresh'];diagnostic=state['latest_diagnostic']
            self.identity.set(f"V{self.variant} 身份{'已核验' if state['authenticated'] else '未核验'} · {'已连接' if state['connected'] else '未连接'} · schema {SCHEMAS[self.variant]:08X}")
            self.notice.set(state['error'] or self.ui_error or state['message'])
            for variables,values in ((self.saved,state['stored']),(self.reads,state['independent']),(self.adopted,control['active_gains'] if fresh else None)):
                for variable,value in zip(variables,values or [None]*6):variable.set('已过期/不可用' if value is None else format(value,'.9g'))
            self.revisions.set(f"已保存rev={state['stored_revision']} · 独立READ rev={state['independent_revision']} · 本事务采用rev={control['active_revision'] if fresh else '不可用'} · 待确认命令={state['pending_mutations']}")
            if fresh:
                gains=control['active_gains'];base=control['manual_gains']
                pieces=[]
                for name,value,reference in zip(NAMES,gains,base):
                    change=f'{(value/reference-1)*100:+.5g}%' if reference else ('0%' if value==0 else '基准0')
                    pieces.append(f'{name}={value:.9g} ({change})')
                self.actual_primary.set('  '.join(pieces))
                self.actual_meta.set(f"control epoch={control['control_epoch']} · rev={control['active_revision']} · 板时{control['board_ms']}ms · 接收龄{state['control_age_seconds']*1000:.0f}ms · 许可开启={control['adaptive_enabled_at_launch']} / 本事务自适应增益来源={control['adaptive_used_at_launch']} / 完成时电机许可={control['motor_permitted_at_completion']}")
            else:
                self.actual_primary.set('已过期/不可用：没有500ms内、已连接且身份核验的控制事务')
                age='无' if state['control_age_seconds'] is None else f"{state['control_age_seconds']:.3f}s"
                self.actual_meta.set(f"上次控制epoch={control['control_epoch'] if control else '无'} · 接收龄={age}；状态/诊断接收不会刷新此年龄")
            for key,variable in self.swing_reads.items():
                if key in state['config_reads']:
                    value,revision=state['config_reads'][key];variable.set(f'{value} · rev{revision}')
                if key in state['config_saved']:self.swing_saved[key].set(str(state['config_saved'][key]))
            if self.variant==2:
                for field,variable in self.gesture_reads.items():
                    if (254,field) in state['config_reads']:
                        value,revision=state['config_reads'][(254,field)];variable.set(f'读回{value} · rev{revision}')
            kit_values=[]
            for field,name in ((5,'套件'),(6,'物理计数/圈'),(7,'Forest单位/圈')):
                if (254,field) in state['config_reads']:
                    value,revision=state['config_reads'][(254,field)];kit_values.append(f'{name}={value} (rev{revision})')
            self.kit_readback.set('独立READ：'+' · '.join(kit_values) if kit_values else '未独立读回；套件0原1080 / 套件1 IP5701040；默认0')
            if latest:
                coherent=latest['coherent'];angle=(latest['mean_adc12']-3085)*345/4096;target=(latest['target_angle_adc12']-3085)*345/4096
                start_description='V5手动平衡，无自动起摆' if self.variant==5 else swing_description(latest)
                self.summary.set(f"完成时状态：模式={latest['mode']} 故障=0x{latest['fault']:02X} 零点={'有效' if latest['flags']&16 else '无效'} 电机许可={bool(latest['flags']&4)} Q15={latest['actual_applied_q15']}；{start_description}\n"
                    f"摆杆目标/实际={format(target,'.8g') if coherent else '不可用'}° / {format(angle,'.8g') if coherent else '不可用'}°（Forest标度）  旋臂目标/实际={latest['wide_target']} / {latest['wide_encoder']}  误差={latest['position_error']}  圈数={latest['wide_encoder']/1040:.8g}\n"
                    f"旋转={MOTION_TEXT.get(latest['motion_state'],'未知')}；最近识别#{latest['gesture_event_seq']} {GESTURES.get(latest['gesture_code'],'未知')}；最近动作：{action_description(latest)}")
            if control:
                components=' / '.join(format(v,'.9g') for v in control['components'])
                self.detail.set(f"控制epoch={control['control_epoch']}，周期={control['control_period_us']}us，位置分量epoch={control['position_component_epoch']}（{'保持值' if control['position_components_held'] else '本次更新'}）；P/I/D分量={components}\n"
                    f"请求Moto={control['requested_moto']} 板时={control['board_ms']}ms {'有效' if fresh else '历史过期'} · T缺口={state['sequence_gaps']} / D缺口={state['diagnostic_sequence_gaps']} · CRC错={state['crc_errors']} 板端丢帧={control['telemetry_dropped']}")
            if self.variant==3 and control:
                self.adapt_average.set(gain_average_text(state['actual_gain_averages']))
                status=FREEZE_TEXT.get(control['freeze_reason'],'未知原因')
                if not control['adaptive_enabled_at_launch']:status='关闭 / 仅固定基准'
                elif not control['motor_permitted_at_completion']:status='停车禁止输出；'+status
                elif control['freeze_reason']==0 and control['center_d_offset']:status='中心已保留 / 快速偏移按实时特征变化'
                if not fresh:status='已过期，不作为当前状态；'+status
                if control['learning_revision']==0 and control['center_p_offset']==0 and control['center_d_offset']==0:
                    learning_status='尚无长期中心保留；'+('当前仅快速调节（守卫/冷却见原因）' if fresh and control['adaptive_enabled_at_launch'] and control['motor_permitted_at_completion'] else '关闭 / 停车 / 数据过期，调节不可用')
                else:learning_status='中心是否保留请核对版本与学习事件；不代表已达到最优'
                self.adapt_detail.set(f"{status} · 启用许可={control['adaptive_enabled_at_launch']} · 本事务增益来源={control['adaptive_used_at_launch']} · 完成时电机许可={control['motor_permitted_at_completion']}\n"
                    f"手动基准（独立于当前值）={control['manual_gains']}\n候选目标（尚不等于实际）={control['adaptive_target_gains']}\n"
                    f"保留中心P/D code={control['center_p_offset']}/{control['center_d_offset']}；瞬时快速P/D code={control['fast_p_offset']}/{control['fast_d_offset']}（相对基准/256）\n"
                    f"学习版本={control['learning_revision']}；候选特征epoch={control['candidate_feature_epoch']}；25ms采用计数={control['adaptive_adoption_epoch']}（不同域）\n{learning_status}")
                lines=[f"独立事件流：遗漏中间学习事件{state['learning_event_gaps']} / D采用事件{state['adoption_event_gaps']}；完整已收事务增益变化保存于原始日志"]
                for event in state['control_gain_events'][-2:]:
                    before,after=event['before'],event['after']
                    lines.append(f"实际采用 @ {event['board_ms']}ms epoch{event['control_epoch']}：角P {before[0]:.7g}→{after[0]:.7g}，D {before[2]:.7g}→{after[2]:.7g}；{'自适应来源' if event['adaptive_used'] else '固定基准'}；{FREEZE_TEXT.get(event['freeze'],'未知')}")
                for event in state['learning_events'][-2:]:
                    lines.append(f"学习#{event['learning_event_seq']} @ {event['learning_event_ms']}ms 特征{event['learning_feature_epoch']}：{LEARNING_TEXT.get(event['learning_code'],'未知')} / {FREEZE_TEXT.get(event['learning_reason'],'未知')}；中心D {event['center_before']}→{event['center_after']}；能量 {event['window_energy']} ({event['event_samples']}点)")
                self.adapt_events.set('\n'.join(lines))
            if diagnostic:
                age=time.monotonic()-state['last_diagnostic_rx'];mark='新鲜' if state['connected'] and age<=.5 else '历史过期'
                self.kit_detail.set(f"独立诊断 {mark}：套件={diagnostic['kit']}，物理/Forest每圈={diagnostic['physical_counts_per_rev']}/{diagnostic['forest_counts_per_rev']}；编码器raw={diagnostic['physical_encoder_raw']}，非法累计={diagnostic['invalid_transitions']}（新增即保护）；离线已测边沿间隔={diagnostic['minimum_edge_clocks']}拍\n"
                    f"起摆完成={diagnostic['swing_finished_ms']}ms；接平衡={diagnostic['handoff_ms']}ms；失败={diagnostic['swing_failure_ms']}ms / { {0:'尚无失败',1:'源失败',2:'父级接管超时'}.get(diagnostic['swing_failure_reason'],'未知来源')}；等待={diagnostic['handoff_wait_ms']}ms\n"
                    f"源角/角速度/臂速度={diagnostic['source_alpha']}/{diagnostic['source_alpha_rate']}/{diagnostic['source_arm_rate']}；capture flags=0x{diagnostic['source_capture_flags']:02X}，持续资格={diagnostic['capture_hold_ms']}ms\n"
                    f"当前源阶段原因码={diagnostic['source_reason']}（5为后续取消，不代表原失败根因）；最后control epoch={diagnostic['last_completed_epoch']}，实际/候选特征epoch={diagnostic['adopted_feature_epoch']}/{diagnostic['candidate_feature_epoch']}；采用计数={diagnostic['adoption_count']}")
                if self.variant==5:
                    self.kit_detail.set(f"独立诊断 {mark}：套件={diagnostic['kit']}，物理/Forest每圈={diagnostic['physical_counts_per_rev']}/{diagnostic['forest_counts_per_rev']}；编码器raw={diagnostic['physical_encoder_raw']}，非法累计={diagnostic['invalid_transitions']}（新增即保护）；离线已测边沿间隔={diagnostic['minimum_edge_clocks']}拍\n"
                        f"V5无自动起摆；手动扶直、ZERO后RUN。最后control epoch={diagnostic['last_completed_epoch']}，实际/候选特征epoch={diagnostic['adopted_feature_epoch']}/{diagnostic['candidate_feature_epoch']}；采用计数={diagnostic['adoption_count']}")
                if self.variant==2:
                    reference_ready=bool(fresh and latest and latest['mode']==3 and latest['fault']==0 and latest['flags']&4 and abs(latest['position_error'])<=200 and
                                         (latest['motion_state']==0 or (latest['motion_state']==4 and diagnostic['trajectory_done'] and diagnostic['reference_velocity']==0)))
                    reference_text=motion_reference_text(latest,state['motion_reference_start'],state['motion_reference_start_epoch']) if latest else '等待参考坐标'
                    self.motion_detail.set(f"{mark}：实际速度={diagnostic['measured_velocity']} / 参考离散速度={diagnostic['reference_velocity']} Forest计数/s；目标误差={diagnostic['tracking_error']}\n"
                        f"参考完成={diagnostic['trajectory_done']}；下一动作参考资格={'可接（以板端结果为准）' if reference_ready and mark=='新鲜' else '未满足 / 状态过期'}；实际停稳={diagnostic['tracking_settled']}；连续资格={diagnostic['settle_ticks']}×5ms\n"
                        f"{MOTION_REASON_TEXT.get(diagnostic['stop_reason'],'未知停转原因')}；{reference_text}")
            self.log_status.set(state['log_status']+(' · 错误：'+state['log_error'] if state['log_error'] else '')+f" · 保活最大间隔={state['max_heartbeat_gap']*1000:.1f}ms · 命令超时={state['timeouts']}")
            if hasattr(self,'trace_text'):
                entries=state['command_events'][-3:]
                self.trace_text.set('\n'.join(f"{entry['event']} · source={entry.get('source','—')} · request={entry.get('request_id','—')} · op={entry.get('opcode','—')} · status={entry.get('status','—')} · {entry.get('reason','')}"[:180] for entry in entries))
                d=state['latest_diagnostic'];b=state['last_compact']
                if d and 'final_target' in d:
                    final=str(d['final_target']) if d.get('final_target_valid',True) else '连续/刹车中，无预定终点'
                    gesture_note=(f'V{self.variant}键盘版：光学手势未启用。' if self.variant>=4 else
                                  f"手势 armed/held/zero 状态=0x{d['gesture_state']:02X}，解码原因={d['decode_reason']}；")
                    self.goal_text.set(f"原点={d['origin']} / 最终目标={final} Forest计数；最近已接受运动请求ID={d['motion_request_id']} / 动作0x{d['motion_action']:02X} / 规划阶段={d['planner_phase']}；D龄={(time.monotonic()-state['last_diagnostic_rx'])*1000:.0f}ms\n"
                        f"参考完成={d['trajectory_done']}；实际连续到位={d['tracking_settled']}（{d['settle_ticks']}×5ms）；实际/参考速度={d['measured_velocity']}/{d['reference_velocity']}；停转原因={MOTION_REASON_TEXT.get(d['stop_reason'],d['stop_reason'])}\n"
                        +gesture_note+"D为独立诊断快照。")
                if b and not fresh:
                    self.summary.set(f"BLE紧凑状态：实际/参考/最终={b['wide_encoder']}/{b['wide_target']}/{b['final_target']}；摆角={b['angle_error']*345/4096:.5f}°；速度={b['measured_velocity']}；Q15={b['actual_applied_q15']}\n"
                        f"epoch={b['control_epoch']} / 板时={b['board_ms']}ms / 接收龄={time.monotonic()-state['last_compact_rx']:.3f}s；故障={b['fault']}；范围有效={b['range_valid']}。此帧不包含六PID实际参数。")
                if hasattr(self,'planner_reads'):
                    for field,text in self.planner_reads.items():
                        value=state['config_reads'].get((253,field))
                        if value:text.set(f'{value[0]} · rev{value[1]}')
            if self.variant in (2,4,5):self.draw_motion_overlay(state)
            else:self.draw(state['history'])
        self.root.after(100,self.refresh)
    def draw(self,history):
        canvas=self.canvas;canvas.delete('all');width=max(canvas.winfo_width(),200);height=max(canvas.winfo_height(),100)
        if len(history)<2:canvas.create_text(15,15,text='等待真实5ms控制事务；状态帧不画成新样本。',anchor='nw',fill='#555');return
        elapsed=[((row[6]-history[0][6])&0xffffffff)/1000 for row in history] if self.variant in (2,4,5) else None
        duration=elapsed[-1] if elapsed else 0
        arm=(1,(4,5),'旋臂累计角度：实测蓝 / 参考红（°）') if self.variant in (2,4,5) else (1,(3,),'旋臂位置误差（Forest计数）')
        for panel,indices,title in ((0,(1,2),'摆杆角度 / 目标（°）'),arm):
            left=panel*width/2+58;right=(panel+1)*width/2-15;top=25;bottom=height-22
            scale=360/1040 if self.variant in (2,4,5) and panel==1 else 1
            values=[row[index]*scale for row in history for index in indices];low=min(values);high=max(values)
            if high-low<0.01:low-=0.005;high+=0.005
            span=high-low;low-=span*0.05;high+=span*0.05
            canvas.create_text(left,5,text=title,anchor='nw');canvas.create_rectangle(left,top,right,bottom,outline='#bbb')
            canvas.create_text(left-4,top,text=f'{high:.5g}',anchor='e');canvas.create_text(left-4,bottom,text=f'{low:.5g}',anchor='e')
            for index,color in zip(indices,('#1565c0','#c62828')):
                points=[]
                previous_ms=None
                for n,row in enumerate(history):
                    if elapsed and previous_ms is not None and ((row[6]-previous_ms)&0xffffffff)!=5:
                        if len(points)>=4:canvas.create_line(*points,fill=color,width=1.3)
                        points=[]
                    x=left+(elapsed[n]/duration if duration else n/(len(history)-1))*(right-left)
                    points.extend((x,bottom-(row[index]*scale-low)*(bottom-top)/(high-low)))
                    previous_ms=row[6] if elapsed else None
                if len(points)>=4:canvas.create_line(*points,fill=color,width=1.3)
            if self.variant in (2,4,5):
                canvas.create_text(left,bottom+3,text=f'-{duration:.3f}s',anchor='nw')
                canvas.create_text(right,bottom+3,text='0s（板时刻）',anchor='ne')

def main():
    parser=argparse.ArgumentParser(description='Forest D1新版独立控制台');parser.add_argument('--variant',type=int,choices=(1,2,3,4,5),required=True)
    args=parser.parse_args();enable_dpi_awareness();root=tk.Tk();App(root,args.variant);root.mainloop()

if __name__=='__main__':main()
