"""Chinese desktop UI. The default launch is an isolated fake-transport preview."""
import argparse
from pathlib import Path
import struct
import tkinter as tk
from tkinter import ttk

from . import protocol as p
from .client import SerialOwner, open_usb
from .config_fields import (AUTORETURN_READS, GAIN_NAMES, PROFILES, fields_for)


class Window:
    def __init__(self, root, log_directory, *, initial_port=None):
        self.root = root
        self.log_directory = log_directory
        self.owner = None
        self.draft_profile = tk.StringVar(value=PROFILES[0])
        self.port = tk.StringVar(value=initial_port or "")
        self.status = tk.StringVar(value="离线窗口")
        self.error = tk.StringVar()
        self.gain_vars = [tk.StringVar(value=f"{v:g}") for v in p.DEFAULT_GAINS]
        self.gain_read = [tk.StringVar(value="尚未独立读回") for _ in GAIN_NAMES]
        self.config_rows = {}
        self.config_group = tk.StringVar(value="SWM4")
        self.evidence = {}
        self.root.title("J280 ADB1 快P恢复 · 角P1/位P5ms · 原D5/25ms · 保留转圈修复")
        self.root.geometry("1120x840")
        self.root.minsize(1060, 800)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._connect(initial_port)
        self.root.after(100, self.refresh)

    def _build(self):
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        ttk.Label(bar, text="USB 2M 串口（手工填写）：").pack(side="left")
        ttk.Entry(bar, textvariable=self.port, width=13).pack(side="left", padx=4)
        ttk.Button(bar, text="连接 USB", command=lambda: self.action(lambda: self._connect(self.port.get().strip()))).pack(side="left", padx=3)
        ttk.Button(bar, text="离线演示", command=lambda: self.action(lambda: self._connect(None))).pack(side="left", padx=3)
        ttk.Button(bar, text="断开", command=self.disconnect).pack(side="left", padx=3)
        ttk.Label(bar, text="不发现设备、不保存端口、不恢复动作").pack(side="right")
        ttk.Label(outer, textvariable=self.status, wraplength=1060).pack(anchor="w", pady=(8, 2))
        ttk.Label(outer, textvariable=self.error, foreground="#b42318", wraplength=1060).pack(anchor="w", pady=(0, 6))

        controls = ttk.LabelFrame(outer, text="明确操作（板端仍独立执行保护和资格检查）", padding=6)
        controls.pack(fill="x")
        for caption, opcode in (("STOP 停车", p.STOP), ("急停", p.ESTOP), ("采集零点", p.ZERO),
                                ("手动 RUN", p.RUN), ("清除故障", p.CLEAR)):
            ttk.Button(controls, text=caption, command=lambda op=opcode: self.send(op)).pack(side="left", padx=3)
        ttk.Combobox(controls, textvariable=self.draft_profile, values=PROFILES, state="readonly", width=8).pack(side="left", padx=(16, 3))
        ttk.Button(controls, text="选择起摆源", command=lambda: self.send(p.SELECT_SWING, payload=bytes((PROFILES.index(self.draft_profile.get()),)))).pack(side="left", padx=3)
        ttk.Button(controls, text="启动一次起摆", command=lambda: self.send(p.START_SWING)).pack(side="left", padx=3)
        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True, pady=8)
        overview = ttk.Frame(tabs, padding=8)
        gains = ttk.Frame(tabs, padding=12)
        config = ttk.Frame(tabs, padding=8)
        logging = ttk.Frame(tabs, padding=8)
        for frame, title in ((overview, "实时证据"), (gains, "PID / 自适应"), (config, "SWM4 / 映射 / 运动参数"), (logging, "记录与质量")):
            tabs.add(frame, text=title)

        motion = ttk.Frame(overview)
        motion.pack(fill="x", pady=(0, 8))
        for caption, opcode in (("顺时针一圈", p.MOTION_CW), ("逆时针一圈", p.MOTION_CCW),
                                ("持续向左", p.MOTION_LEFT), ("持续向右", p.MOTION_RIGHT), ("受控制动", p.MOTION_STOP)):
            ttk.Button(motion, text=caption, command=lambda op=opcode: self.send(op)).pack(side="left", padx=3)
        self.summary = self._table(overview, ("控制事务", "控制状态", "手势光学", "动作资格", "旋转轨迹", "参考与实际", "到位与制动", "自适应实际证据"), height=8)
        ttk.Label(overview, text="旋臂实际位置（蓝）／连续参考（橙）／最终目标（绿），单位为控制计数；板端时间，缺帧处断线。", wraplength=1020).pack(anchor="w", pady=(8, 2))
        self.plot = tk.Canvas(overview, height=130, background="#f7f9fc", highlightthickness=1, highlightbackground="#d0d5dd")
        self.plot.pack(fill="both", expand=True)
        ttk.Label(overview, text="控制器倾角：实际（红）／真实目标（灰）；沿用345°/4096标度，无平滑或补点。", wraplength=1020).pack(anchor="w", pady=(5, 2))
        self.angle_plot = tk.Canvas(overview, height=110, background="#f7f9fc", highlightthickness=1, highlightbackground="#d0d5dd")
        self.angle_plot.pack(fill="both", expand=True)

        ttk.Label(gains, text="草稿只在点击写入后发送；采用值来自完成的控制事务，勾选/开启不代表已改变增益。", wraplength=1020).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 12))
        for column, caption in enumerate(("参数", "人工草稿", "独立 READ 读回 / revision", "完成事务的实际采用值")):
            ttk.Label(gains, text=caption).grid(row=1, column=column, sticky="w", padx=8)
        self.actual_gain = []
        for row, name in enumerate(GAIN_NAMES, 2):
            actual = tk.StringVar(value="等待控制事务")
            self.actual_gain.append(actual)
            ttk.Label(gains, text=name).grid(row=row, column=0, sticky="w", padx=8, pady=7)
            ttk.Entry(gains, textvariable=self.gain_vars[row - 2], width=18).grid(row=row, column=1, sticky="w", padx=8)
            ttk.Label(gains, textvariable=self.gain_read[row - 2]).grid(row=row, column=2, sticky="w", padx=8)
            ttk.Label(gains, textvariable=actual).grid(row=row, column=3, sticky="w", padx=8)
        ttk.Button(gains, text="写入六参数并独立读回", command=lambda: self.action(lambda: self.owner.set_gains([v.get() for v in self.gain_vars]))).grid(row=8, column=1, pady=12, sticky="w")
        ttk.Button(gains, text="独立读回人工基值", command=lambda: self.send(p.READ_GAINS, 255)).grid(row=8, column=2, pady=12, sticky="w")
        adaptive = ttk.LabelFrame(gains, text="板上P/D双向响应搜索（默认关闭；P32～80、D96～448）", padding=8)
        adaptive.grid(row=9, column=0, columnspan=4, sticky="nsew", pady=12)
        ttk.Button(adaptive, text="明确开启", command=lambda: self.send(p.ADAPT_ENABLE)).pack(side="left", padx=4)
        ttk.Button(adaptive, text="明确关闭", command=lambda: self.send(p.ADAPT_DISABLE)).pack(side="left", padx=4)
        self.adaptive_text = tk.StringVar(value="等待板端证据")
        ttk.Label(adaptive, textvariable=self.adaptive_text, wraplength=720).pack(side="left", padx=12)
        self.gain_details = tk.StringVar()
        ttk.Label(gains, textvariable=self.gain_details, wraplength=1020, justify="left").grid(row=10, column=0, columnspan=4, sticky="w")
        for column in range(4):
            gains.columnconfigure(column, weight=1)

        config_bar = ttk.Frame(config)
        config_bar.pack(fill="x", pady=(0, 8))
        combo = ttk.Combobox(config_bar, textvariable=self.config_group,
                             values=(*PROFILES, "运动规划", "系统映射"), state="readonly", width=16)
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda _: self._config_fields())
        ttk.Button(config_bar, text="独立读取当前组", command=self.read_group).pack(side="left", padx=8)
        ttk.Label(config, text="参数草稿不会自动发送。本版保留SWM4、转圈修复和原自适应；快P为角度1ms／位置5ms，D仍为5ms／25ms。", wraplength=1020).pack(anchor="w", pady=(0, 6))
        self.config_body = ttk.Frame(config)
        self.config_body.pack(fill="both", expand=True)
        self._config_fields()

        self.log_table = self._table(logging, ("日志文件", "压缩队列", "接收统计", "缺帧与 CRC", "保活间隔", "未知动作结果", "最新 ACK", "身份与能力"), height=8)
        ttk.Label(logging, text="自动保存原始接收块、每帧原文与解码、板端序号/时刻、请求和 ACK、CRC/丢包标记。压缩在线程独立执行；不按UI刷新率删样本。", wraplength=1020).pack(anchor="w", pady=12)
        self.event_box = tk.Text(logging, height=10, wrap="word", state="disabled")
        self.event_box.pack(fill="both", expand=True)

    def _table(self, parent, labels, *, height):
        tree = ttk.Frame(parent)
        tree.rows = []
        for index, label in enumerate(labels):
            ttk.Label(tree, text=label, width=17, anchor="nw").grid(row=index, column=0, sticky="nw", pady=3)
            value = tk.StringVar(value="等待数据")
            tree.rows.append(value)
            ttk.Label(tree, textvariable=value, wraplength=830, justify="left").grid(row=index, column=1, sticky="nw", pady=3)
        tree.columnconfigure(1, weight=1)
        tree.bind("<Configure>", lambda event: [widget.configure(wraplength=max(400, event.width - 155))
                  for widget in tree.grid_slaves(column=1)])
        tree.pack(fill="x")
        return tree

    def _group(self):
        value = self.config_group.get()
        return PROFILES.index(value) if value in PROFILES else 251 if value == "运动规划" else 254 if value == "系统映射" else 253

    def _config_fields(self):
        for widget in self.config_body.winfo_children():
            widget.destroy()
        self.config_rows.clear()
        group = self._group()
        for column, caption in enumerate(("字段", "草稿", "独立读回 / revision", "操作")):
            ttk.Label(self.config_body, text=caption).grid(row=0, column=column, sticky="w", padx=6, pady=4)
        for row, (field_id, name, minimum, maximum) in enumerate(fields_for(group), 1):
            draft, readback = tk.StringVar(), tk.StringVar(value="尚未读取")
            self.config_rows[field_id] = (draft, readback)
            ttk.Label(self.config_body, text=f"{name} [{minimum}..{maximum}]").grid(row=row, column=0, sticky="w", padx=6, pady=6)
            ttk.Entry(self.config_body, textvariable=draft, width=16).grid(row=row, column=1, sticky="w", padx=6)
            ttk.Label(self.config_body, textvariable=readback).grid(row=row, column=2, sticky="w", padx=6)
            ttk.Button(self.config_body, text="写入并读回", command=lambda key=field_id, var=draft: self.action(lambda: self.owner.write_config(group, key, int(var.get())))).grid(row=row, column=3, padx=6)
        self.config_extra = tk.StringVar(value="")
        ttk.Label(self.config_body, textvariable=self.config_extra, wraplength=1000, justify="left").grid(row=20, column=0, columnspan=4, sticky="w", pady=12)
        self.config_body.columnconfigure(2, weight=1)

    def read_group(self):
        def read():
            group = self._group()
            for field_id, *_ in fields_for(group):
                self.owner.read_config(group, field_id)
            if group == 253:
                for field_id, _ in AUTORETURN_READS:
                    self.owner.read_config(group, field_id)
        self.action(read)

    def _connect(self, port):
        if port == "":
            raise ValueError("请先填写现场确认的USB串口；未进行设备发现")
        self.disconnect()
        if port:
            factory = lambda: open_usb(port)
            self.mode_text = f"USB {port} / 2,000,000 baud"
        else:
            from .fake_transport import FakeTransport
            factory = FakeTransport
            self.mode_text = "离线模拟 / 不连接设备 / 模型数据不代表硬件结果"
        self.owner = SerialOwner(factory, self.log_directory).start()

    def disconnect(self):
        if self.owner:
            self.owner.close()
        self.status.set("连接已断开，没有自动发送停车、清除、归零或动作重放")

    def action(self, function):
        try:
            function()
            self.error.set("")
        except Exception as exc:
            self.error.set(str(exc))

    def send(self, opcode, group=0, payload=b""):
        self.action(lambda: self.owner.command(opcode, group, payload))

    @staticmethod
    def _row(tree, index, text):
        tree.rows[index].set(text)

    def refresh(self):
        if self.owner:
            s = self.owner.snapshot()
            t, d, e = s["latest"] or {}, s["diagnostic"] or {}, s["extension"] or {}
            auth = "身份/能力已核验" if s["authenticated"] else "控制未获资格"
            self.status.set(f"{self.mode_text}  |  {auth}  |  {s['message']}")
            if s["error"] or s["logging"].get("error"):
                self.error.set(s["error"] or s["logging"]["error"])
            if t:
                self._row(self.summary, 0, f"{'快P 角1/位5ms，D5/25ms' if t.get('control_profile') else '原P5/25ms（非零I时回原档）'} · epoch-ms {t['control_epoch']} · 板端 {t['board_ms']} ms · packet {t['sequence']} · coherent {int(t['coherent'])} · age {s['control_age'] or 0:.3f}s")
                self._row(self.summary, 1, f"{p.MODE_TEXT.get(t['mode'], t['mode'])} · fault {t['fault']} · 源 {PROFILES[t['swing_profile']] if t['swing_profile'] < len(PROFILES) else '?'} · 阶段 {t['swing_stage']} · 电机完成许可 {int(t['motor_permitted_at_completion'])}")
                self._row(self.summary, 2, f"最近事件 {p.GESTURES.get(t['gesture_code'], t['gesture_code'])} / 序号 {t['gesture_event_seq']}；实时光学raw {e.get('raw43', '?')}/{e.get('raw44', '?')} · ready {e.get('gesture_ready', '?')} · decode {p.GESTURE_DECODE_TEXT.get(e.get('decode_reason'), e.get('decode_reason', '?'))} · 抑制 {e.get('gesture_suppressed', '?')} · I²C错误 {e.get('gesture_errors', '?')}")
                self._row(self.summary, 3, f"事件译码/拒绝 {p.ACTION_REASON_TEXT.get(t['action_reason'], t['action_reason'])} · 光学识别与命令接受分开")
                self._row(self.summary, 4, f"{p.MOTION_TEXT.get(t['motion_state'], t['motion_state'])} · E完成 {e.get('trajectory_done', '?')} / 到位 {e.get('tracking_settled', '?')} · phase {e.get('planner_phase', '?')} · E事务 {e.get('control_epoch', '?')}")
                self._row(self.summary, 5, f"实际 {t['wide_encoder']} · 当前参考 {t['wide_target']} · 跟踪误差 {t['position_error']} · 本地 actual/target {t['local_encoder']}/{t['local_target']} (1040单位/圈)")
                final_text = f"T最终目标 {t['final_target']}" if t['motion_state'] != 2 else f"连续模式无预设终点（原始final {t['final_target']}）"
                self._row(self.summary, 6, f"{final_text}；E原点 {e.get('origin', '?')} · 参考/实际速度 {e.get('reference_velocity', '?')}/{e.get('measured_velocity', '?')} unit/s · 停止原因 {p.MOTION_REASON_TEXT.get(e.get('stop_reason'), e.get('stop_reason', '?'))}")
                enabled, used = t['adaptive_enabled_at_launch'], t['adaptive_used_at_launch']
                self._row(self.summary, 7, f"用户开启 {int(enabled)} · 本事务采用自适应 {int(used)} · 冻结 {p.FREEZE_TEXT.get(t['freeze_reason'], t['freeze_reason'])} · 采用计数 {t['adaptive_adoption_epoch']}")
                for index, value in enumerate(t['active_gains']):
                    self.actual_gain[index].set(f"{value:.9g} · epoch {t['control_epoch']}")
                self.adaptive_text.set(f"T开启 {int(enabled)} / 本事务实际使用 {int(used)}；E实时资格active {e.get('adaptation_active', '?')}\n{p.FREEZE_TEXT.get(t['freeze_reason'], t['freeze_reason'])}；E特征epoch {e.get('feature_epoch', '?')}")
                self.gain_details.set(f"T同事务人工基值：{self._gains(t['manual_gains'])}\nT同事务候选值：{self._gains(t['adaptive_target_gains'])}\n已验证中心 P={t['center_p_offset']/16:.3f} / D={t['center_d_offset']/16:.3f}；I与位置参数保持手动值\nT候选feature epoch {t['candidate_feature_epoch']} · 活动revision {t['active_revision']} · 人工revision {t['stored_revision']}\n最近采用事件：control epoch {d.get('adoption_control_epoch', '?')} / feature epoch {d.get('adoption_feature_epoch', '?')} / 原因 {p.ADOPTION_TEXT.get(d.get('adoption_reason'), d.get('adoption_reason', '?'))}\n学习/回退：{p.LEARNING_TEXT.get(d.get('learning_code'), d.get('learning_code', '?'))} · reason {d.get('learning_reason', '?')} · 候选revision {d.get('candidate_revision', '?')} · 已采用feature epoch {d.get('adopted_feature_epoch', '?')}\nE参考加速度 {e.get('acceleration_q', '?')} Q16 unit/5ms²；E当前特征冻结 {e.get('feature_freeze_reason', '?')}。E光学为实时数据，特征/候选使用各自epoch。")
                self._draw(s["history"])
            read = s["independent_gains"]
            for index, value in enumerate(self.gain_read):
                value.set(f"{read[0][index]:.9g} / rev {read[1]}" if read else "尚未独立读回")
            group = self._group()
            for key, (_, var) in self.config_rows.items():
                value = s['config_reads'].get((group, key))
                var.set(f"{value[0]} / rev {value[1]}" if value else "尚未独立读回")
            if group == 253:
                self.config_extra.set("\n".join(f"{name}：{s['config_reads'].get((253, key), ('?',))[0]}" for key, name in AUTORETURN_READS))
            log = s["logging"]
            self._row(self.log_table, 0, log.get("path", "尚未开始"))
            self._row(self.log_table, 1, f"已入队 {log.get('accepted', 0)} / 已写 {log.get('written', 0)} / 排队 {log.get('queued', 0)} · {log.get('error') or '正常'}")
            self._row(self.log_table, 2, f"全部有效帧 {s['received']} / 完整控制事务 {s['control_received']}；UI仅显示尾部，不删日志")
            self._row(self.log_table, 3, f"T缺口 {s['sequence_gaps']} / control缺口 {s['control_gaps']} / D/E缺口 {s['diagnostic_gaps']}/{s['extension_gaps']} / CRC {s['crc_errors']} / 格式 {s['format_errors']} / 丢弃字节 {s['discarded_bytes']} / 重复事务 {s['duplicate_controls']}")
            self._row(self.log_table, 4, f"目标100ms / 最大实测 {s['heartbeat_max_gap'] * 1000:.1f}ms / 共 {s['heartbeat_sent']} 次；独立串口所有者")
            self._row(self.log_table, 5, f"{s['unknown_mutations']} 个未知执行结果 / ACK超时 {s['timeouts']}；不重发；日志保护：{s['logging_failure_stop']}")
            ack = s['last_ack']
            self._row(self.log_table, 6, f"ID {ack['request_id']} · opcode 0x{ack['opcode']:02X} · {p.STATUS_TEXT.get(ack['status'], ack['status'])} · control epoch {ack['control_epoch']}" if ack else "尚无ACK")
            self._row(self.log_table, 7, f"wire {p.VERSION} / family {p.VARIANT} / firmware 0x{p.FIRMWARE:08X} / schema 0x{p.SCHEMA:08X} / capabilities {s['capabilities']}")
            self.event_box.configure(state="normal")
            self.event_box.delete("1.0", "end")
            self.event_box.insert("end", "\n".join(str(event) for event in s['events'][-12:]))
            self.event_box.configure(state="disabled")
        self.root.after(100, self.refresh)

    @staticmethod
    def _gains(values):
        return " / ".join(f"{value:.9g}" for value in values)

    def _draw(self, history):
        rows = history[-500:]
        self._draw_series(self.plot, rows, (("wide_encoder", "#175cd3"), ("wide_target", "#f79009"), ("final_target", "#079455")))
        scale = 345. / 4096.
        angles = [dict(row, actual_tilt=(row["mean_adc12"] - 3085) * scale,
                       target_tilt=(row["target_angle_adc12"] - 3085) * scale) for row in rows]
        self._draw_series(self.angle_plot, angles, (("actual_tilt", "#d92d20"), ("target_tilt", "#667085")))

    def _draw_series(self, canvas, rows, series):
        canvas.delete("all")
        if len(rows) < 2:
            return
        width, height = canvas.winfo_width(), canvas.winfo_height()
        end = rows[-1]['board_ms']
        span = max(5, (end - rows[0]['board_ms']) & 0xffffffff)
        values = [row[key] for row in rows for key, _ in series]
        low, high = min(values), max(values)
        padding = max(.1, (high - low) * .08)
        low, high = low - padding, high + padding
        canvas.create_text(43, 10, text=f"{low:.3g} … {high:.3g}   {rows[0]['board_ms']} → {end} ms", anchor="w", fill="#475467")
        for key, color in series:
            previous = None
            for row in rows:
                point = (width - 12 - ((end - row['board_ms']) & 0xffffffff) / span * max(1, width - 52),
                         height - 12 - (row[key] - low) / (high - low) * max(1, height - 34))
                if previous and ((row['control_epoch'] - previous[0]) & 0xffffffff) == row.get('control_epoch_stride', 1):
                    canvas.create_line(*previous[1], *point, fill=color)
                elif previous:
                    canvas.create_line(point[0], 22, point[0], height - 12, fill="#98a2b3", dash=(2, 3))
                previous = (row['control_epoch'], point)

    def close(self):
        self.disconnect()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="J280 unified desktop window; defaults to offline simulation")
    parser.add_argument("--port", default=None, help="Explicit USB port. No automatic discovery or saved port.")
    parser.add_argument("--demo", action="store_true", help="Use only FakeTransport (the default).")
    parser.add_argument("--logs", type=Path, default=Path(__file__).resolve().parent / "logs")
    args = parser.parse_args()
    if args.demo and args.port:
        parser.error("--demo 与 --port 互斥")
    root = tk.Tk()
    Window(root, args.logs, initial_port=args.port)
    root.mainloop()


if __name__ == "__main__":
    main()
