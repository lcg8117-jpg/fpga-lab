"""Visible read-only progress monitor for the owner's offline build."""
import json
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter.scrolledtext import ScrolledText

ROOT = Path(__file__).resolve().parents[1]
app = tk.Tk()
app.title('J280 ADC64 平衡＋自适应 — 实时编译进度')
app.geometry('980x650')
app.minsize(760, 450)
state = tk.StringVar(value='准备工程')
detail = tk.StringVar(value='读取进度记录…')
elapsed = tk.StringVar()
tk.Label(app, textvariable=state, font=('Microsoft YaHei UI', 20, 'bold'), anchor='w').pack(fill='x', padx=18, pady=(15, 5))
tk.Label(app, textvariable=detail, font=('Microsoft YaHei UI', 11), anchor='w', wraplength=930, justify='left').pack(fill='x', padx=18)
tk.Label(app, textvariable=elapsed, anchor='w').pack(fill='x', padx=18, pady=8)
log = ScrolledText(app, font=('Consolas', 10), background='#111827', foreground='#e5e7eb', state='disabled')
log.pack(fill='both', expand=True, padx=18, pady=(0, 12))
tk.Label(app, text='窗口只显示真实阶段与日志；阶段百分比不代表总进度。关闭窗口不会中断编译。', anchor='w').pack(fill='x', padx=18, pady=(0, 12))
last = None


def refresh():
    global last
    try:
        progress = json.loads((ROOT/'reports/progress.json').read_text(encoding='utf8'))
        state.set(progress.get('state', '准备工程'))
        detail.set(progress.get('detail', ''))
        elapsed.set('本轮已用时：%d 分 %02d 秒' % divmod(int(time.time()-progress['started']), 60))
        lines = progress.get('events', [])
        native_path = progress.get('native_status')
        if native_path and Path(native_path).exists():
            native = json.loads(Path(native_path).read_text(encoding='utf8'))
            state.set('编译：'+native.get('status', '运行中'))
            build = native.get('build_directory')
            if build:
                candidates = [Path(build)/'compile.log']
                candidates += list((Path(build)/'impl/pnr').glob('*.log'))
                candidates += list((Path(build)/'impl/gwsynthesis').glob('*.log'))
                for path in candidates:
                    if path.exists():
                        text = path.read_text(encoding='utf8', errors='replace')[-18000:]
                        lines += ['\n--- '+path.name+' ---\n', text]
            detail.set(progress.get('detail', '')+'  '+str(native.get('reason', '')))
        rendered = '\n'.join(lines)
        if rendered != last:
            log.configure(state='normal')
            log.delete('1.0', 'end')
            log.insert('end', rendered)
            log.see('end')
            log.configure(state='disabled')
            last = rendered
    except (OSError, ValueError, KeyError):
        pass
    app.after(1000, refresh)


app.lift()
app.attributes('-topmost', True)
app.after(1500, lambda: app.attributes('-topmost', False))
refresh()
app.mainloop()
