"""Chinese SwingTune UI and loopback bridge; only this Host owns COM."""
import argparse
from collections import deque
import csv
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import queue
import re
import threading
import time
import uuid
from tune_model import Controller
from tune_parameters import PARAMETERS, SWING_PARAMETERS, BALANCE_PARAMETERS, HISTORICAL_PID, RESTORE_FIELDS, EDITABLE, INTEGRATION_PARAMETERS, PROFILE_PARAMETERS
from tune_protocol import USER_CODE, CONTROL_SCHEMA, wide_integer_strings
from tune_transport import SerialWorker
from precise_telemetry import TelemetryHistory, SessionRecorder, reduced_segments

CSV_FIELDS = ('host_monotonic', 'page', 'timestamp_ms', 'alpha_q20', 'alpha_dot_q20',
              'theta_dot_q20', 'encoder_count', 'pwm', 'mode', 'fault', 'flags', 'swing_state', 'retry',
              'phase_end_reason', 'phase_elapsed_ms', 'effective_forward_ms', 'session_reverse_ms',
              'handoff_hold_ms', 'phase_flags', 'phase_active', 'capture_candidate', 'handoff_confirmed', 'phase_failed')

PHASE_NAMES = {0: '空闲', 10: '下垂确认', 11: '前向主动驱动', 13: '反向主动驱动',
               40: '等待接管', 70: '等待重试', 90: '失败'}
PHASE_REASONS = {0: '阶段开始/无', 1: '前向计时到', 2: '反向计时到', 3: '接管候选',
                 4: '20ms资格完成，已请求交接', 5: 'STOP', 6: '零点失效', 7: '急停',
                 8: '前向方向错误', 9: '安排有限重试', 10: '下垂确认超时',
                 11: '尝试次数用尽', 12: '无效配置'}
GESTURE_NAMES = {0:'无',1:'右滑',2:'左滑',3:'上滑',4:'下滑',5:'靠近',6:'远离',
                 7:'顺时针',8:'逆时针',9:'挥手',10:'慢速左右',11:'慢速上下',12:'慢速前后',13:'组合/无序'}
GESTURE_ACTIONS = {0:'无动作',1:'上滑起摆',2:'定位转动',3:'两秒转圈',4:'取消并保持',5:'挥手STOP'}
GESTURE_REASONS = {0:'无事件',1:'手势未开启',2:'传感器未就绪或通信错误',3:'起摆期间已忽略',
    4:'尚未实际平衡接管',5:'平衡HOLD尚未就绪',6:'轨迹忙，未排队',7:'已接受',8:'反向手势取消',
    9:'保护/起摆资格拒绝',10:'该手势没有动作映射',11:'轨迹模块拒绝',12:'等待轨迹模块确认'}


def gesture_description(row):
    if not row or (row['event_count']==0 and row['code']==0):
        return '尚未读到真实手势事件；轮询次数不代表收到手势。'
    return (f"最近事件 #{row['event_count']}（保留快照）：{GESTURE_NAMES.get(row['code'], str(row['code']))} → "
            f"{GESTURE_ACTIONS.get(row['action'], str(row['action']))}；{GESTURE_REASONS.get(row['reason'], str(row['reason']))}。"
            f"板端事件时间={row['event_ms']}ms，事件ID={row['event_id']}。")


def gesture_runtime_description(row):
    if not row:
        return '传感器与接管状态尚未真实读回。'
    return (f"传感器就绪={int(row['ready'])}，开启={int(row['enabled'])}，当前错误={row['error']}，累计错误={row['error_count']}；"
        f"手势屏蔽={int(row['blocked'])}，实际平衡就绪={int(row['fresh_balance_ready'])}，电机许可={int(row['motor'])}，"
        f"零点={int(row['zero'])}，急停/故障={int(row['estop'])}/{int(row['fault'])}。\n"
        f"起摆阶段={PHASE_NAMES.get(row['swing_state'], str(row['swing_state']))}，交接请求={int(row['handoff'])}，"
        f"自动会话={int(row['auto_armed'])}，手动会话={int(row['manual_run'])}，平衡许可={int(row['deadman'])}，"
        f"HOLD初始化={int(row['hold_initialized'])}，轨迹运行/忙={int(row['rotation_active'])}/{int(row['busy'])}；"
        f"查询序号={row['poll_sequence']}，板端当前时间={row['timestamp_ms']}ms。")


def phase_description(row):
    if not row:
        return '阶段诊断未读回。接管候选或交接请求均不等于BALANCE，只有实际mode=3表示已平衡接管。'
    state = PHASE_NAMES.get(row['swing_state'], str(row['swing_state']))
    reason = PHASE_REASONS.get(row['phase_end_reason'], str(row['phase_end_reason']))
    return (f"实读阶段：{state}，已计时 {row['phase_elapsed_ms']}ms；本次前/反向 {row['effective_forward_ms']}/{row['session_reverse_ms']}ms。"
            f"最近转移：{reason}；候选={int(row['capture_candidate'])}，资格计时={row['handoff_hold_ms']}ms，交接请求={int(row['handoff_confirmed'])}（非BALANCE）。"
            f" FPGA时间={row['timestamp_ms']}ms")


class Engine:
    def __init__(self, log_root, allow_device=True, worker_factory=SerialWorker):
        self.lock = threading.RLock()
        self.events = queue.Queue()
        self.worker = None
        self.disconnecting = False
        self.last_error = None
        self.worker_factory = worker_factory
        self.allow_device = allow_device
        self.log_root = Path(log_root)
        self.log_dir = None
        self.csv_file = self.csv_writer = None
        self.recorder = None
        self.wave = TelemetryHistory()
        self.samples = deque(maxlen=1200)
        self.model = Controller(self._send, self._cancel, sample=self._sample)

    def _send(self, packet, cid, stop):
        if not self.worker:
            raise RuntimeError('串口未连接')
        self.worker.send(packet, cid, stop)

    def _cancel(self):
        if self.worker:
            self.worker.outbox.cancel()

    def _sample(self, row):
        self.samples.append(row)
        self._record('legacy', row)

    def _record(self, kind, row):
        if self.recorder:
            try:
                self.recorder.submit(kind, row)
            except RuntimeError as exc:
                self.last_error = str(exc)
                self.model.note = str(exc)

    def _close_csv(self):
        if self.recorder:
            try:
                self.recorder.close()
            except RuntimeError as exc:
                self.last_error = str(exc)
                self.model.note = str(exc)
            self.recorder = None
        if self.csv_file:
            self.csv_file.close()
            self.csv_file = self.csv_writer = None

    def dispatch(self, body):
        with self.lock:
            if not isinstance(body, dict):
                raise ValueError('请求必须是JSON对象')
            action = body.get('action')
            if action == 'connect':
                if not self.allow_device:
                    raise ValueError('离线预览禁止连接硬件')
                if self.worker:
                    raise ValueError('已有唯一Host连接；先显式断开')
                port = body.get('port', '')
                if not isinstance(port, str) or not re.fullmatch(r'COM[1-9][0-9]*', port, re.I):
                    raise ValueError('端口必须为COM编号')
                if body.get('firmware_swm2') is not True:
                    raise ValueError('请确认已烧录配套SP24固件；旧固件身份不兼容')
                self.log_dir = self.log_root / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                self.log_dir.mkdir(parents=True)
                self.recorder = SessionRecorder(self.log_dir, CSV_FIELDS)
                self.wave = TelemetryHistory()
                self.events = queue.Queue()
                self.samples.clear()
                self.disconnecting = False
                self.last_error = None
                self.worker = self.worker_factory(port.upper(), self.events, self.log_dir)
                self.worker.start()
                return dict(state='connecting', log_directory=str(self.log_dir))
            if action == 'disconnect':
                self.disconnecting = True
                if self.worker:
                    self.worker.close()
                self.model.closed('已请求断开；不会自动重连或恢复运动')
                return dict(state='disconnecting')
            if action not in ('set', 'restore-pid', 'apply-preset', 'load-profile', 'stop', 'run', 'zero', 'clear-fault', 'balance'):
                raise ValueError('操作不在允许列表')
            if set(body) - {'action', 'request_id', 'name', 'value'}:
                raise ValueError('未知请求字段')
            return self.model.submit(body.get('request_id'), action, body.get('name'), body.get('value'))

    def tick(self):
        with self.lock:
            while True:
                try:
                    kind, value, at = self.events.get_nowait()
                except queue.Empty:
                    break
                if kind == 'connected' and not self.disconnecting:
                    self.model.opened()
                elif kind == 'sent' and not self.disconnecting:
                    self.model.sent(value, at)
                elif kind == 'frame':
                    if value.kind == 'telemetry':
                        row = self.wave.receive(value.values, at)
                        if row is not None and self.recorder:
                            self._record('wave', row)
                    elif not self.disconnecting:
                        pending = dict(self.model.pending or {})
                        accepted = self.model.receive(value, at)
                        if accepted and pending.get('kind') == 'status' and pending.get('page', 0) >= 4 and self.recorder:
                            self._record('readonly', dict(host_monotonic=at,
                                host_time=datetime.now().astimezone().isoformat(timespec='microseconds'),
                                page=pending['page'], raw_hex=value.raw.hex(),
                                values=value.values, telemetry=dict(self.model.telemetry)))
                elif kind == 'decoder':
                    self.wave.decoder = value
                elif kind == 'error':
                    self.last_error = value
                    self.model.note = value
                elif kind == 'disconnected':
                    self.model.closed(self.last_error or '连接已关闭；不会自动恢复运动')
                    self.worker = None
                    self._close_csv()
            try:
                self.model.tick()
            except Exception as exc:
                self.model.note = str(exc)

    def snapshot(self):
        with self.lock:
            data = self.model.snapshot()
            data['requests'] = dict(list(data['requests'].items())[-50:])
            data.update(user_code=f'{USER_CODE:08X}', schema=f'{CONTROL_SCHEMA:08X}',
                        precise_telemetry=self.wave.snapshot(),
                        precise_latest=None if self.wave.last is None else dict(self.wave.last,
                            wide_integer_decimal=wide_integer_strings(self.wave.last)),
                        recording_error=None if self.recorder is None else self.recorder.error,
                        recording_backlog=0 if self.recorder is None else self.recorder.queue.qsize(),
                        recording_lost_rows=0 if self.recorder is None else self.recorder.lost_rows,
                        log_directory=None if self.log_dir is None else str(self.log_dir),
                        parameter_specs=[dict(id=p[0], name=p[1], label=p[2], unit=p[3],
                                              default=p[4], minimum=p[5], maximum=p[6], editable=p[1] in EDITABLE) for p in PARAMETERS])
            return data

    def close(self):
        with self.lock:
            self.disconnecting = True
            if self.worker:
                self.worker.close()
                self.worker.join(timeout=3)
            # Consume already decoded telemetry before closing the recorder;
            # delayed command ACKs cannot resume or complete control operations.
            self.tick()
            self.model.closed()
            self._close_csv()


def start_bridge(engine, port=8371):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, code, value):
            raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw)

        def guard(self):
            # Browser pages cannot submit commands through cross-origin forms.
            if (self.headers.get('Origin') is not None or self.headers.get('X-J280-Local') != '1'
                    or self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}'):
                self.respond(403, {'error': '仅允许本机专用CLI；浏览器来源禁止'})
                return False
            return True

        def do_GET(self):
            if not self.guard():
                return
            if self.path == '/status':
                self.respond(200, engine.snapshot())
            elif self.path.startswith('/request/'):
                rid = self.path.removeprefix('/request/')
                with engine.lock:
                    result = engine.model.requests.get(rid)
                    self.respond(200 if result else 404, result or {'error': '未知请求ID'})
            else:
                self.respond(404, {'error': '不存在的接口'})

        def do_POST(self):
            if not self.guard():
                return
            try:
                size = int(self.headers.get('Content-Length', '-1'))
                if self.path != '/command' or not 1 <= size <= 4096:
                    raise ValueError('请求路径或长度错误')
                # Consume a bounded body before rejecting its type: unread POST
                # bytes can reset the Windows connection before the error reply.
                raw = self.rfile.read(size)
                if self.headers.get_content_type() != 'application/json':
                    raise ValueError('请求类型错误')
                value = json.loads(raw)
                self.respond(200, engine.dispatch(value))
            except (ValueError, TypeError) as exc:
                self.respond(400, {'error': str(exc)})
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Window:
    def __init__(self, root, engine, bridge_port):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.root, self.engine = tk, root, engine
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei', 9))
        style.configure('TButton', padding=(4, 1))
        self.inputs, self.actual = {}, {}
        root.title('J280 起摆调参 — SP24 唯一Host')
        root.geometry('1180x850')
        root.minsize(1080, 820)
        base = ttk.Frame(root, padding=10)
        base.pack(fill='both', expand=True)
        top = ttk.Frame(base)
        top.pack(fill='x')
        self.port = tk.StringVar(value='COM3')
        ttk.Label(top, text='串口').pack(side='left')
        ttk.Entry(top, textvariable=self.port, width=9).pack(side='left', padx=5)
        self.firmware = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text='已烧录本包 SP24 固件', variable=self.firmware).pack(side='left')
        ttk.Button(top, text='连接', command=self.connect).pack(side='left', padx=5)
        ttk.Button(top, text='断开', command=lambda: self.call({'action': 'disconnect'})).pack(side='left')
        ttk.Label(top, text=f'本机助手桥 127.0.0.1:{bridge_port}；不另开串口').pack(side='right')
        actions = ttk.Frame(base)
        actions.pack(fill='x', pady=10)
        tk.Button(actions, text='STOP 停止', bg='#b82020', fg='white', font=('Microsoft YaHei', 13, 'bold'),
                  command=lambda: self.command('stop')).pack(side='left', padx=(0, 10))
        for label, action in [('清除 F01/F07', 'clear-fault'), ('零点标定', 'zero'), ('RUN 自动起摆', 'run'), ('仅平衡启动', 'balance')]:
            ttk.Button(actions, text=label, command=lambda a=action: self.command(a)).pack(side='left', padx=4)
        self.state = tk.StringVar(value='未连接')
        ttk.Label(base, textvariable=self.state, font=('Microsoft YaHei', 11), wraplength=1030).pack(anchor='w')
        self.message = tk.StringVar(value='冷启动PID为0。先STOP，显式恢复已有PID并读回；必要时标定，再单独RUN。')
        ttk.Label(base, textvariable=self.message, wraplength=1030, foreground='#924000').pack(anchor='w', pady=(3, 8))
        book = ttk.Notebook(base)
        self.book = book
        book.pack(fill='x')
        swing = ttk.Frame(book, padding=7)
        pid = ttk.Frame(book, padding=7)
        book.add(swing, text='前反向定时起摆（9项）')
        book.add(pid, text='原有平衡配置显式恢复')
        self.table(swing, SWING_PARAMETERS)
        self.table(pid, BALANCE_PARAMETERS)
        integration=ttk.Frame(book,padding=7);book.add(integration,text='手势 / 平衡后自适应')
        self.table(integration,INTEGRATION_PARAMETERS)
        ttk.Label(integration,text='上滑起摆；起摆期间所有手势丢弃。平衡后左右滑定位，反向滑取消；顺/逆时针各两秒一圈。\n实控须实机资格；工程影子可先导入，导入保持关闭，随后显式选影子。',wraplength=1000).grid(row=8,column=0,columnspan=6,sticky='w')
        self.adaptive_status=tk.StringVar(value='自适应实际状态尚未读回')
        ttk.Label(integration,textvariable=self.adaptive_status,wraplength=1000).grid(row=9,column=0,columnspan=6,sticky='w',pady=5)
        self.gesture_event=tk.StringVar(value=gesture_description(None))
        self.gesture_runtime=tk.StringVar(value=gesture_runtime_description(None))
        ttk.Label(integration,textvariable=self.gesture_event,wraplength=1000,foreground='#234c72').grid(row=10,column=0,columnspan=6,sticky='w',pady=3)
        ttk.Label(integration,textvariable=self.gesture_runtime,wraplength=1000).grid(row=11,column=0,columnspan=6,sticky='w',pady=3)
        profile=ttk.Frame(book,padding=7);book.add(profile,text='自适应配置 / 来源校验')
        self.table(profile,PROFILE_PARAMETERS)
        self.profile_control=tk.BooleanVar(value=False)
        ttk.Checkbutton(profile,text='按实机资格提交（配置必须声明已筛选，仿真配置禁止）',variable=self.profile_control).grid(row=12,column=0,columnspan=5,sticky='w')
        ttk.Button(profile,text='加载 E 配置 JSON 并逐项读回',command=self.load_profile).grid(row=13,column=0,columnspan=4,sticky='w')
        ttk.Label(profile,text='实控须实机资格；工程影子可先导入，导入保持关闭，随后显式选影子。不会自动起摆。',wraplength=1000).grid(row=14,column=0,columnspan=6,sticky='w')
        preset_tab=ttk.Frame(book,padding=10);book.add(preset_tab,text='明确选择参数预设')
        from tune_presets import load_presets
        self.presets=load_presets()
        self.preset_choice=tk.StringVar(value=self.presets[0]['label'])
        ttk.Label(preset_tab,text='现场记录与工程候选分开保存。选择不会写入；显式应用前必须STOP并完成真实停止读回。',wraplength=1000).grid(row=0,column=0,columnspan=3,sticky='w',pady=6)
        self.preset_box=ttk.Combobox(preset_tab,textvariable=self.preset_choice,values=[p['label'] for p in self.presets],state='readonly',width=64)
        self.preset_box.grid(row=1,column=0,columnspan=2,sticky='w',pady=6)
        ttk.Button(preset_tab,text='应用所选预设并逐项读回',command=self.apply_preset).grid(row=1,column=2,sticky='w',padx=10)
        ttk.Label(preset_tab,text='位置基线112/0/48；阻尼候选112/0/56；慢积分候选112/2/48。角度可停机调整：D80、I780各只改一个增益。\n角度和位置六个PID增益均可显式修改；上述候选尚无实物稳定性验证。\n现场起摆70%/230ms、70%/230ms、最多1次须单独选择；不覆盖为出厂值。所有预设不自动标零、RUN或开启自适应。',wraplength=1000).grid(row=2,column=0,columnspan=3,sticky='w',pady=8)
        ttk.Button(preset_tab,text='加载本包E工程候选（仅影子资格）',command=self.load_engineering_profile).grid(row=3,column=0,columnspan=3,sticky='w',pady=8)
        ttk.Label(preset_tab,text='E工程候选的Ku/Tu是软件换算构造量，不是机台实测；加载后保持E关闭，需停止时另选2影子。禁止当作实机已调好。',wraplength=1000).grid(row=4,column=0,columnspan=3,sticky='w')
        ttk.Label(swing, text='反向时间从发出反向命令开始计时，包含减速和过零；不是停到零速就结束。', wraplength=1030).grid(row=11, column=0, columnspan=6, sticky='w', padx=8, pady=4)
        self.phase = tk.StringVar(value=phase_description(None))
        ttk.Label(swing, textvariable=self.phase, wraplength=1030, foreground='#234c72').grid(row=12, column=0, columnspan=6, sticky='w', padx=8, pady=4)
        ttk.Button(pid, text='附件数值填表（不发送）', command=self.prefill).grid(row=13, column=0, columnspan=2, sticky='w', pady=5)
        ttk.Label(pid, text='附件最后报告位置P112/D48、DF650/W5；尚未独立实物验证。').grid(row=13, column=2, columnspan=4, sticky='w')
        ttk.Button(pid, text='写入已填整组9项（停止时）', command=self.restore_pid).grid(row=14, column=0, columnspan=3, sticky='w', pady=4)
        ttk.Label(base, text='修改前先STOP并确认PWM=0。断开连接不等于急停，FPGA可能继续运动；停止请先STOP。下垂确认 ≤ 等待最长时间。').pack(anchor='w', pady=7)
        footer = ttk.Frame(base)
        footer.pack(side='bottom', fill='x')
        wave_bar = ttk.Frame(footer)
        wave_bar.pack(fill='x')
        ttk.Label(wave_bar, text='同步波形100Hz；摆角/参考、旋臂/目标、实际PWM。原始精度记录，不平滑、不补帧。').pack(side='left')
        ttk.Button(wave_bar, text='放大波形', command=self.expand_wave).pack(side='right')
        self.wave_status = tk.StringVar(value='尚无有效同步遥测；连接不会自动运行或修改参数。')
        ttk.Label(footer, textvariable=self.wave_status, wraplength=1030).pack(anchor='w')
        self.loglabel = tk.StringVar(value='CSV / 原始帧：连接后自动保存到 sessions 文件夹')
        ttk.Label(footer, textvariable=self.loglabel, wraplength=1030).pack(anchor='w')
        self.canvas = tk.Canvas(base, height=180, bg='#111923', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.large_canvas = None
        self.alive = True
        root.after(25, self.update)

    def table(self, frame, specs):
        from tkinter import ttk
        for col, text in enumerate(('参数', '待提交值', '实际读回', '单位', '范围', '默认 / 提交')):
            ttk.Label(frame, text=text).grid(row=0, column=col, sticky='w', padx=8)
        for row, p in enumerate(specs, 1):
            pid, name, label, unit, default, lo, hi = p
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', padx=8, pady=2)
            var, actual = self.tk.StringVar(value=str(default)), self.tk.StringVar(value='未读回')
            self.inputs[name], self.actual[name] = var, actual
            ttk.Entry(frame, textvariable=var, width=13, state='normal' if name in EDITABLE else 'disabled').grid(row=row, column=1, padx=8)
            ttk.Label(frame, textvariable=actual, width=22).grid(row=row, column=2, sticky='w', padx=8)
            ttk.Label(frame, text=unit).grid(row=row, column=3, sticky='w', padx=8)
            ttk.Label(frame, text=f'{lo}..{hi}').grid(row=row, column=4, sticky='w', padx=8)
            cell = ttk.Frame(frame)
            cell.grid(row=row, column=5, sticky='w', padx=8)
            ttk.Label(cell, text=str(default), width=11).pack(side='left')
            if name in EDITABLE:
                ttk.Button(cell, text='提交并读回', command=lambda n=name: self.set_value(n)).pack(side='left')

    def load_profile(self):
        from tkinter import filedialog
        path=filedialog.askopenfilename(title='选择实机配置或工程影子配置',filetypes=[('JSON','*.json')])
        if not path:return
        try:
            from e68_profile import compile_profile
            profile=json.loads(Path(path).read_text(encoding='utf-8-sig'))
            control=self.profile_control.get()
            compile_profile(profile,control=control)
            self.call(dict(action='load-profile',request_id=str(uuid.uuid4()),value=dict(profile=profile,control=control)))
        except (OSError,ValueError) as exc:
            self.message.set(str(exc))

    def apply_preset(self):
        preset=next(p for p in self.presets if p['label']==self.preset_choice.get())
        self.call(dict(action='apply-preset',request_id=str(uuid.uuid4()),value=preset))

    def load_engineering_profile(self):
        try:
            from tune_presets import PRESET_ROOT
            profile=json.loads((PRESET_ROOT/'e_engineering_shadow.json').read_text(encoding='utf-8-sig'))
            self.call(dict(action='load-profile',request_id=str(uuid.uuid4()),value=dict(profile=profile,control=False)))
        except (OSError,ValueError) as exc:
            self.message.set(str(exc))

    def call(self, body):
        try:
            result = self.engine.dispatch(body)
            self.message.set(result.get('message', result.get('state', '已提交')))
        except Exception as exc:
            self.message.set(str(exc))

    def command(self, action):
        self.call(dict(action=action, request_id=str(uuid.uuid4())))

    def connect(self):
        self.call(dict(action='connect', port=self.port.get().strip(), firmware_swm2=self.firmware.get()))

    def set_value(self, name):
        try:
            value = int(self.inputs[name].get())
        except ValueError:
            self.message.set('请输入整数')
            return
        self.call(dict(action='set', request_id=str(uuid.uuid4()), name=name, value=value))

    def prefill(self):
        for name, value in HISTORICAL_PID.items():
            self.inputs[name].set(str(value))
        self.message.set('只填入附件最后报告的9项数值：位置P112/D48、DF650/W5，未独立实测、尚未发送。需STOP后明确整组写入并读回。')

    def restore_pid(self):
        try:
            values = {name: int(self.inputs[name].get()) for name in RESTORE_FIELDS}
        except ValueError:
            self.message.set('PID及积分使能必须是整数')
            return
        self.call(dict(action='restore-pid', request_id=str(uuid.uuid4()), value=values))

    def update(self):
        if not self.alive:
            return
        self.engine.tick()
        snap = self.engine.snapshot()
        s = snap['status']
        if s:
            modes = {0: '安全待机', 1: '已标定', 2: 'ARMED待接管', 3: 'BALANCE平衡', 4: '故障停止'}
            self.state.set(f"{modes.get(s['mode'], '模式'+str(s['mode']))}  F={s['fault']:02X}  实际PWM={s['pwm']}  零点={'有效' if s['zero_valid'] else '无效'}  起摆状态={s['swing_state']}  重试={s['retry']}  状态龄={snap['status_age_s']}s")
        else:
            self.state.set('未取得匹配SP24身份及CRC的实际状态')
        self.phase.set(phase_description(snap['telemetry'].get('phase') if snap['connected'] else None))
        self.gesture_event.set(gesture_description(snap['telemetry'].get('gesture_event') if snap['connected'] else None))
        self.gesture_runtime.set(gesture_runtime_description(snap['telemetry'].get('gesture_runtime') if snap['connected'] else None))
        e=snap['telemetry'].get('adaptive');g=snap['telemetry'].get('adaptive_gains')
        if not snap['connected'] or e is None:
            self.adaptive_status.set('自适应实际状态尚未读回')
        else:
            text=f"选择={e['selected']}，当前生效={e['effective']}，配置标志={e['profile_flags']}，故障={e['fault']}，配置版本={e['profile_revision']}"
            if g:text+=f"；计算时刻={g['sample_ms']} ms，Kp={g['kp_q24']/2**24:.5g}，Ki={g['ki_q24']/2**24:.5g}，Kd={g['kd_q24']/2**24:.5g}"
            self.adaptive_status.set(text)
        for name, var in self.actual.items():
            p = snap['parameters'].get(name)
            var.set('未读回' if not p else f"{p['value']}"+('（旧版本）' if s and p['revision'] != s['revision'] else '（实读）'))
        if snap['note'] != getattr(self, 'last_note', None):
            self.message.set(snap['note'])
            self.last_note = snap['note']
        if snap['log_directory']:
            self.loglabel.set('记录目录：'+snap['log_directory'])
        self.plot()
        self.root.after(33, self.update)

    def plot(self):
        with self.engine.lock:
            rows = list(self.engine.wave.rows)
            metrics = self.engine.wave.snapshot()
            recorder = self.engine.recorder
        if metrics['age_s'] is not None:
            state = '数据陈旧' if metrics['stale'] else '实时'
            measurement = {'waiting':'等待测量快照','fresh':'测量更新','held':'测量保持','stale':'测量快照陈旧'}[metrics['measurement_state']]
            self.wave_status.set(f"{state} | 到达 {metrics['received_hz']:.1f}Hz / 板时 {metrics['board_hz']:.1f}Hz | "
                f"接收龄 {metrics['age_s']*1000:.1f}ms / PID样本龄 {metrics['epoch_age_ms']}ms | "
                f"有效 {metrics['accepted']} / 缺帧 {metrics['missing']} / CRC {metrics['crc_errors']} / "
                f"重启或乱序 {metrics['resets']} / 重复 {metrics['duplicates']} | {measurement}")
            if recorder is not None and recorder.error:
                self.wave_status.set(self.wave_status.get()+f' | 日志错误 / 未记录 {recorder.lost_rows}行')
        else:
            self.wave_status.set('尚无有效同步遥测；旧页面仍可读，但不拼接成同步波形。')
        for c in (self.canvas, self.large_canvas):
            if c is not None and c.winfo_exists():
                self.draw_wave(c, rows)

    def expand_wave(self):
        from tkinter import ttk
        if self.large_canvas is not None and self.large_canvas.winfo_exists():
            self.large_canvas.winfo_toplevel().lift()
            return
        top = self.tk.Toplevel(self.root)
        top.title('J280 SP24 同步波形 — 原始数据 / 无平滑')
        top.geometry('1180x700')
        top.minsize(800, 480)
        ttk.Label(top, textvariable=self.wave_status, wraplength=1100).pack(fill='x', padx=12, pady=8)
        self.large_canvas = self.tk.Canvas(top, bg='#111923', highlightthickness=0)
        self.large_canvas.pack(fill='both', expand=True, padx=10, pady=10)
        self.plot()

    @staticmethod
    def draw_wave(c, rows):
        c.delete('all')
        width, height = max(c.winfo_width(), 100), max(c.winfo_height(), 30)
        if not rows:
            c.create_text(20, 25, text='尚无实测数据（离线预览不会生成虚假波形）', anchor='w', fill='#b8c7da')
            return
        left, right, bottom = 152, max(160, width-18), height-24
        span, end = right-left, rows[-1]['board_elapsed_ms']
        view = [r for r in rows if end-r['board_elapsed_ms'] <= 10000]
        origin = view[-1]['encoder_count']
        angle_scale = 180/math.pi/1048576
        bands = [
            ('摆角 / 参考 °', [('alpha_q20', angle_scale, '#59b9ff', 'measurement'),
                            ('reference_rad_q20', angle_scale, '#e5a5ed', 'pid')], angle_scale*4),
            (f'旋臂 / 目标 °\n局部原点 {origin}count',
                [(lambda r: r['encoder_count']-origin, 1/3, '#f6bd4f', 'measurement'),
                 (lambda r: r['target_count_q32']-origin*2**32, 1/(3*2**32), '#f09068', 'pid')], 1/3),
            ('实际 PWM %\n采集时刻实时状态', [('pwm', 100/32768, '#70db8a', 'live')], 100/32768*4)]
        for index, (label, traces, floor) in enumerate(bands):
            y0, y1 = index*bottom/3+8, (index+1)*bottom/3-12
            series = [(color, reduced_segments(view, field, scale, span,
                        require_pid=validity == 'pid', require_measurement=validity != 'live'))
                      for field, scale, color, validity in traces]
            values = [y for _, groups in series for group in groups for _, y in group]
            if not values:
                c.create_text(8, y0, text=label+'\n等待新鲜测量快照', anchor='nw', fill='#b8c7da')
                continue
            lo, hi = min(values), max(values)
            margin = max((hi-lo)*.12, floor/2)
            lo, hi = lo-margin, hi+margin
            digits = max(0, min(6, int(-math.floor(math.log10(hi-lo)))+2))
            c.create_text(6, y0, text=label, anchor='nw', fill='#c5d3e4', font=('Microsoft YaHei', 9))
            for fraction in (0, .5, 1):
                y = y0+(y1-y0)*fraction
                value = hi-(hi-lo)*fraction
                c.create_line(left, y, right, y, fill='#2b3a49')
                c.create_text(left-7, y, text=f'{value:.{digits}f}', anchor='e', fill='#a8b7ca')
            for tick in range(0, 11, 2):
                x = left+span*(1-tick/10)
                c.create_line(x, y0, x, y1, fill='#1e2c3b')
            for trace_index, (color, groups) in enumerate(series):
                for group in groups:
                    xy = [(left+x, y0+(hi-y)/(hi-lo)*(y1-y0)) for x, y in group]
                    if len(xy) > 1:
                        c.create_line(*[n for point in xy for n in point], fill=color, width=1.5, smooth=False)
                    elif xy:
                        x, y = xy[0]
                        c.create_oval(x-1, y-1, x+1, y+1, outline=color, fill=color)
                if groups:
                    prefix = ('测得 ', '参考 ')[trace_index] if index == 0 else ('测得 ', '目标 ')[trace_index] if index == 1 else ''
                    c.create_text(6, y0+36+trace_index*15, text=prefix+f'{groups[-1][-1][1]:.{max(4,digits)}f}',
                                  anchor='nw', fill=color)
            for row in view:
                if row['gap_before']:
                    x = left+span*(1-(end-row['board_elapsed_ms'])/10000)
                    c.create_line(x, y0, x, y1, fill='#ef6a72', dash=(3, 3))
        for tick in range(0, 11, 2):
            c.create_text(left+span*(1-tick/10), height-9, text=f'-{tick}s' if tick else '现在', fill='#a8b7ca')


def main():
    parser = argparse.ArgumentParser(description='SP24唯一串口Host；连接和运动均需显式操作')
    parser.add_argument('--bridge-port', type=int, default=8371)
    parser.add_argument('--offline-preview', action='store_true')
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()
    engine = Engine(Path(__file__).resolve().parent/'sessions', allow_device=not args.offline_preview)
    server = start_bridge(engine, args.bridge_port)
    try:
        if args.headless:
            print(f'Host ready 127.0.0.1:{args.bridge_port}; no COM open until explicit CLI connect', flush=True)
            while True:
                engine.tick()
                time.sleep(0.025)
        else:
            import tkinter as tk
            root = tk.Tk()
            window = Window(root, engine, args.bridge_port)
            def close():
                window.alive = False
                root.destroy()
            root.protocol('WM_DELETE_WINDOW', close)
            root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        engine.close()


if __name__ == '__main__':
    main()
