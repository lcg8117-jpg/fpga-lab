"""Incremental actual-coordinate view and offline replay; no device commands."""
from pathlib import Path
import json
import math
import time
import tkinter as tk
from tkinter import ttk, filedialog

from measured_sequence import SequenceTrace

COLORS = {'G': '#1967c4', 'O': '#0a8057', 'W': '#924fc0', 'I': '#c26a18', 'N': '#d2304c'}


def load_record(path):
    path = Path(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('记录超过32 MiB离线导入上限')
    data = json.loads(path.read_text(encoding='utf-8-sig'),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('记录含非有限数')))
    if not isinstance(data, dict) or data.get('format') != 'J280SequentialInputV1':
        raise ValueError('需要原始ENC帧记录；不接受字形映射导出')
    SequenceTrace(data.get('manifest'))
    if not isinstance(data.get('frames'), list) or len(data['frames']) > 120001:
        raise ValueError('原始帧数量无效')
    return data


class SequenceViewer(tk.Toplevel):
    def __init__(self, master, provider=None):
        super().__init__(master)
        self.title('GOWIN 顺序真实轨迹 · ENC / 平均角速度')
        self.geometry('1120x740')
        self.minsize(880, 580)
        self.provider = provider
        self.trace = None
        self.record = None
        self.cursor = 0
        self.playing = False
        self.last_clock = time.monotonic()
        self.replay_ms = 0
        self.source_key = None
        self.live_bounds = [-128, 128, -48, 48]
        self.outside_count = 0
        self.drawn = 0
        self.previous = None
        self.last_size = None
        self.after_id = None
        top = ttk.Frame(self, padding=8)
        top.pack(fill='x')
        for title, command in [('打开原始记录', self.open_record), ('按时间回放', self.play),
                               ('暂停', self.pause), ('显示记录终点', self.show_all),
                               ('恢复实时', self.live), ('导出可重建数据', self.export)]:
            ttk.Button(top, text=title, command=command).pack(side='left', padx=(0, 5))
        self.rate = tk.StringVar(value='4')
        ttk.Label(top, text='回放倍速').pack(side='left')
        ttk.Combobox(top, textvariable=self.rate, values=('1', '4', '10'), width=3,
                     state='readonly').pack(side='left')
        self.message = tk.StringVar(value='等待本次确认原点和真实采样；没有数据就不画。')
        ttk.Label(self, textvariable=self.message, padding=(8, 4), wraplength=1050).pack(fill='x')
        self.phase_text = tk.StringVar(value='G → O → W → I → N｜按任务实际时段推进')
        ttk.Label(self, textvariable=self.phase_text, font=('Microsoft YaHei UI', 12, 'bold'),
                  padding=(8, 4)).pack(fill='x')
        self.canvas = tk.Canvas(self, background='white', highlightthickness=1, highlightbackground='#c7cdd5')
        self.canvas.pack(fill='both', expand=True, padx=8, pady=5)
        self.canvas.bind('<Configure>', lambda event: self.redraw())
        self.footer = tk.StringVar(value='横轴：ENC−共同原点（计数）｜纵轴：固定窗口平均角速度（rad/s）')
        ttk.Label(self, textvariable=self.footer, padding=8, wraplength=1050).pack(fill='x')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after_id = self.after(100, self.tick)

    def set_record(self, data):
        self.record = data
        self.trace = SequenceTrace(data['manifest'])
        self.cursor = 0
        self.replay_ms = 0
        self.playing = False
        self.source_key = None
        self.redraw()
        self.update_status()

    def open_record(self):
        path = filedialog.askopenfilename(filetypes=[('原始ENC顺序记录', '*.json')])
        if path:
            try:
                self.set_record(load_record(path))
            except Exception as exc:
                self.message.set(str(exc))

    def play(self):
        if self.record is None:
            self.message.set('先打开原始记录；实时显示不使用播放定时器生成数据。')
            return
        if self.cursor >= len(self.record['frames']):
            self.set_record(self.record)
        self.playing = True
        self.last_clock = time.monotonic()

    def pause(self):
        self.playing = False

    def show_all(self):
        if self.record is None:
            return
        self.playing = False
        for frame in self.record['frames'][self.cursor:]:
            self.trace.feed(frame)
        self.cursor = len(self.record['frames'])
        self.draw_new()
        self.update_status()

    def live(self):
        self.record = None
        self.trace = None
        self.source_key = None
        self.playing = False
        self.redraw()
        self.message.set('等待新鲜原始采样；旧会话不会混入。')

    def tick(self):
        try:
            if self.record is not None and self.playing:
                now = time.monotonic()
                self.replay_ms += (now-self.last_clock)*1000*int(self.rate.get())
                self.last_clock = now
                frames = self.record['frames']
                start = self.record['manifest']['play_ms']
                while self.cursor < len(frames) and frames[self.cursor]['board_ms']-start <= self.replay_ms:
                    self.trace.feed(frames[self.cursor])
                    self.cursor += 1
                if self.cursor == len(frames):
                    self.playing = False
            elif self.record is None and self.provider:
                value = self.provider(reset=self.trace is None)
                if value is not None:
                    key = value['key']
                    if self.source_key != key:
                        self.trace = SequenceTrace(value['manifest'])
                        self.source_key = key
                        self.live_bounds = value.get('display_bounds', [-128,128,-48,48])
                        self.redraw()
                    for frame in value['frames']:
                        self.trace.feed(frame)
                    if value.get('cancelled'):
                        self.trace.cancel()
            self.draw_new()
            self.update_status()
        except Exception as exc:
            self.playing = False
            self.message.set('停止推进：' + str(exc))
        self.after_id = self.after(100, self.tick)

    def limits(self):
        bounds = (self.record or {}).get('display_bounds', self.live_bounds)
        if len(bounds) != 4 or not all(type(v) in (int, float) and math.isfinite(v) for v in bounds):
            raise ValueError('显示范围无效')
        if bounds[0] >= bounds[1] or bounds[2] >= bounds[3]:
            raise ValueError('显示范围必须递增')
        scale = 2*math.pi/(1040*40)
        if self.trace:
            manifest = self.trace.manifest
            scale = 2*math.pi/(manifest['counts_per_rev']*manifest['window_ms']/1000)
        return bounds[0], bounds[1], bounds[2]*scale, bounds[3]*scale

    def redraw(self):
        self.canvas.delete('all')
        self.drawn = 0
        self.previous = None
        self.outside_count = 0
        width, height = max(300, self.canvas.winfo_width()), max(200, self.canvas.winfo_height())
        self.last_size = width, height
        left, right, bottom, top = self.limits()
        for j in range(5):
            x = 100+(width-130)*j/4
            y = 25+(height-70)*j/4
            self.canvas.create_line(x, 25, x, height-45, fill='#e5e8ec')
            self.canvas.create_line(100, y, width-30, y, fill='#e5e8ec')
            self.canvas.create_text(x, height-28, text=f'{left+(right-left)*j/4:g}')
            self.canvas.create_text(95, y, text=f'{top-(top-bottom)*j/4:.4g}', anchor='e')
        self.canvas.create_text(110, 12, text='仅实际采样点；无字模叠加、无补线', anchor='w', fill='#324353')
        self.draw_new()

    def draw_new(self):
        if not self.trace:
            return
        count = self.trace.point_count
        if self.drawn == count:
            return
        points = self.trace.points_since(self.drawn)
        # Data remain unaltered. Canvas clipping is the normal viewport, not data clipping.
        width, height = self.last_size or (800, 500)
        lo, hi, bottom, top = self.limits()
        breaks = {item['before_point_index'] for item in self.trace.breaks}
        for index, point in enumerate(points, self.drawn):
            if point is None:
                self.previous = None
                continue
            phase = point['phase']
            if phase not in COLORS and not phase.startswith('transition'):
                self.previous = None
                continue
            if not (lo <= point['x_count'] <= hi and bottom <= point['y_rad_s'] <= top):
                self.outside_count += 1
            x = 100+(point['x_count']-lo)/(hi-lo)*(width-130)
            y = height-45-(point['y_rad_s']-bottom)/(top-bottom)*(height-70)
            color = COLORS.get(phase, '#6b7280')
            if index in breaks:
                self.previous = None
            if self.previous is not None:
                self.canvas.create_line(*self.previous, x, y, fill=color, width=1,
                                        tags='measured_trace')
            else:
                self.canvas.create_oval(x-1, y-1, x+1, y+1, outline=color,
                                        tags='measured_trace')
            self.previous = x, y
        self.drawn = count

    def update_status(self):
        if not self.trace:
            return
        manifest = self.trace.manifest
        label = {'physical_log': '实物原始日志', 'conditional_model': '条件模型联仿',
                 'synthetic_test': '反例测试数据'}[manifest['source_kind']]
        self.message.set(f"{label}｜状态 {self.trace.status}｜采样点 {self.trace.point_count}｜原点 {manifest['origin']}｜窗口 {manifest['window_ms']/1000:g}s｜视窗外{self.outside_count}点（导出保留）")
        self.phase_text.set(f"当前阶段：{self.trace.phase or '等待'}｜后续字母只随所属时段的新采样出现")
        self.footer.set(f"横轴 ENC−{manifest['origin']}（计数）；纵轴 [ENC(t)−ENC(t−{manifest['window_ms']/1000:g}s)]×2π/{manifest['counts_per_rev']}/{manifest['window_ms']/1000:g}（rad/s）。缺帧保留；停止接收时不推进。")

    def export(self):
        if not self.trace:
            return
        path = filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('可重建实际轨迹', '*.json')])
        if path:
            Path(path).write_text(json.dumps(self.trace.export(), ensure_ascii=False, allow_nan=False), encoding='utf-8')

    def close(self):
        if self.after_id is not None:
            self.after_cancel(self.after_id)
        self.destroy()
