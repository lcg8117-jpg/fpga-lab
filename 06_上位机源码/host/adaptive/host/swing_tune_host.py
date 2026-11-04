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
from tune_protocol import USER_CODE, CONTROL_SCHEMA
from tune_transport import SerialWorker

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
        if self.csv_writer:
            self.csv_writer.writerow(row)
            self.csv_file.flush()

    def _close_csv(self):
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
                    raise ValueError('请确认已烧录配套SA21固件；旧固件身份不兼容')
                self.log_dir = self.log_root / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                self.log_dir.mkdir(parents=True)
                self.csv_file = (self.log_dir/'samples.csv').open('w', newline='', encoding='utf-8-sig')
                self.csv_writer = csv.DictWriter(self.csv_file, fieldnames=CSV_FIELDS)
                self.csv_writer.writeheader()
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
            if action not in ('set', 'restore-pid', 'load-profile', 'stop', 'run', 'zero', 'clear-fault', 'balance'):
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
                elif kind == 'frame' and not self.disconnecting:
                    self.model.receive(value, at)
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
                        log_directory=None if self.log_dir is None else str(self.log_dir),
                        parameter_specs=[dict(id=p[0], name=p[1], label=p[2], unit=p[3],
                                              default=p[4], minimum=p[5], maximum=p[6], editable=p[1] in EDITABLE) for p in PARAMETERS])
            return data

    def close(self):
        with self.lock:
            if self.worker:
                self.worker.close()
                self.worker.join(timeout=3)
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
        root.title('J280 起摆调参 — SA21 唯一Host')
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
        ttk.Checkbutton(top, text='已烧录本包 SA21 固件', variable=self.firmware).pack(side='left')
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
        ttk.Label(integration,text='停止时设置：自适应0关/1开/2影子。仅正式BALANCE后生效；先加载已标定配置。').grid(row=6,column=0,columnspan=6,sticky='w')
        self.adaptive_status=tk.StringVar(value='自适应实际状态尚未读回')
        ttk.Label(integration,textvariable=self.adaptive_status,wraplength=1000).grid(row=7,column=0,columnspan=6,sticky='w',pady=8)
        profile=ttk.Frame(book,padding=7);book.add(profile,text='自适应配置 / 来源校验')
        self.table(profile,PROFILE_PARAMETERS)
        self.profile_control=tk.BooleanVar(value=False)
        ttk.Checkbutton(profile,text='按实机资格提交（配置必须声明已筛选，仿真配置禁止）',variable=self.profile_control).grid(row=12,column=0,columnspan=5,sticky='w')
        ttk.Button(profile,text='加载标定JSON并逐项写入、读回',command=self.load_profile).grid(row=13,column=0,columnspan=4,sticky='w')
        ttk.Label(profile,text='缺少真实Ku/Tu及误差尺度时保持关闭。导入后不会自动启用或起摆。',wraplength=1000).grid(row=14,column=0,columnspan=6,sticky='w')
        ttk.Label(swing, text='反向时间从发出反向命令开始计时，包含减速和过零；不是停到零速就结束。', wraplength=1030).grid(row=11, column=0, columnspan=6, sticky='w', padx=8, pady=4)
        self.phase = tk.StringVar(value=phase_description(None))
        ttk.Label(swing, textvariable=self.phase, wraplength=1030, foreground='#234c72').grid(row=12, column=0, columnspan=6, sticky='w', padx=8, pady=4)
        ttk.Button(pid, text='附件数值填表（不发送）', command=self.prefill).grid(row=13, column=0, columnspan=2, sticky='w', pady=5)
        ttk.Label(pid, text='附件最后报告位置P112/D48、DF650/W5；尚未独立实物验证。').grid(row=13, column=2, columnspan=4, sticky='w')
        ttk.Button(pid, text='写入已填整组9项（停止时）', command=self.restore_pid).grid(row=14, column=0, columnspan=3, sticky='w', pady=4)
        ttk.Label(base, text='修改前先STOP并确认PWM=0。断开连接不等于急停，FPGA可能继续运动；停止请先STOP。下垂确认 ≤ 等待最长时间。').pack(anchor='w', pady=7)
        footer = ttk.Frame(base)
        footer.pack(side='bottom', fill='x')
        ttk.Label(footer, text='实际查询采样：摆角(°) / 编码器(count) / 实际PWM(%)。各页时间独立；不表示1ms连续采样。').pack(anchor='w')
        self.loglabel = tk.StringVar(value='CSV / 原始帧：连接后自动保存到 sessions 文件夹')
        ttk.Label(footer, textvariable=self.loglabel, wraplength=1030).pack(anchor='w')
        self.canvas = tk.Canvas(base, height=180, bg='#111923', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
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
        path=filedialog.askopenfilename(title='选择已标定的E配置',filetypes=[('JSON','*.json')])
        if not path:return
        try:
            from e68_profile import compile_profile
            profile=json.loads(Path(path).read_text(encoding='utf-8-sig'))
            control=self.profile_control.get()
            compile_profile(profile,control=control)
            self.call(dict(action='load-profile',request_id=str(uuid.uuid4()),value=dict(profile=profile,control=control)))
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
            self.state.set('未取得匹配SA21身份及CRC的实际状态')
        self.phase.set(phase_description(snap['telemetry'].get('phase') if snap['connected'] else None))
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
        self.root.after(50, self.update)

    def plot(self):
        c = self.canvas
        c.delete('all')
        width, height = max(c.winfo_width(), 100), max(c.winfo_height(), 30)
        with self.engine.lock:
            rows = list(self.engine.samples)
        if not rows:
            c.create_text(20, 25, text='尚无实测数据（离线预览不会生成虚假波形）', anchor='w', fill='#b8c7da')
            return
        end = rows[-1]['timestamp_ms']
        series = [('摆角 °', '#59b9ff', 'alpha_q20', 180/math.pi/1048576),
                  ('旋臂 count', '#f6bd4f', 'encoder_count', 1), ('实际 PWM %', '#70db8a', 'pwm', 100/32768)]
        for index, (label, color, field, scale) in enumerate(series):
            points = [(((end-r['timestamp_ms']) & 0xFFFFFFFF), r[field]*scale)
                      for r in rows if field in r and ((end-r['timestamp_ms']) & 0xFFFFFFFF) <= 10000]
            y0, band = index*height/3, height/3
            c.create_line(0, y0+band/2, width, y0+band/2, fill='#2b3a49')
            if not points:
                continue
            lo, hi = min(p[1] for p in points), max(p[1] for p in points)
            margin = max((hi-lo)*0.15, 1)
            lo, hi = lo-margin, hi+margin
            xy = [(110+(10000-age)/10000*(width-120), y0+8+(hi-v)/(hi-lo)*(band-16)) for age, v in points]
            c.create_text(5, y0+12, text=f'{label}\n{points[-1][1]:.2f}', anchor='nw', fill=color)
            if len(xy) > 1:
                c.create_line(*[n for point in xy for n in point], fill=color, width=2)


def main():
    parser = argparse.ArgumentParser(description='SA21唯一串口Host；连接和运动均需显式操作')
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
