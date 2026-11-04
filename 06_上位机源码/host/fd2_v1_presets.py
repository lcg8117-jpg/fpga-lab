"""Draft-only presets for the 2026-10-29 tested V1; never sends commands."""

from tkinter import ttk


PRESETS = {
    'A': (0, {0: 70, 1: 60, 2: 248, 3: 1, 4: 3000, 5: 230, 6: 1800, 8: 1, 9: 10}),
    'B': (2, {0: 71, 64: 90, 65: 97, 66: 97}),
    'C': (0, {0: 64, 1: 60, 2: 232, 3: 1, 4: 3000, 5: 240, 6: 1800, 8: 1, 9: 10}),
}


def load_preset(app, name):
    profile, fields = PRESETS[name]
    if any((profile, field) not in app.swing_values for field in fields):
        raise ValueError('当前 Host 缺少预置字段，未填入任何草稿')
    for field, value in fields.items():
        app.swing_values[(profile, field)].set(str(value))
    app.swing_tabs.select(profile)


def install(app):
    """Add draft selection outside the unchanged upstream Host."""
    tabs = app.swing_tabs
    bar = ttk.Frame(tabs.master)
    bar.pack(fill='x', before=tabs)
    ttk.Label(bar, text='10月29日 V1 命名预置：').pack(side='left')

    def choose(name):
        try:
            load_preset(app, name)
            app.notice.set(f'预置 {name} 已填入草稿；未写入板端。请逐项提交并核对独立读回。')
        except Exception as exc:
            app.show_error(exc)

    for name, label in (('A', 'A · SWM4 70/60'), ('B', 'B · 144 71'), ('C', 'C · SWM4 64/60')):
        ttk.Button(bar, text=label, command=lambda n=name: choose(n)).pack(side='left', padx=3)
    ttk.Label(bar, text='只填草稿；A/C 共用 bank0，B 为 bank2。', wraplength=500).pack(side='left', padx=6)
    ttk.Label(tabs.master, text='请逐项提交并核对独立读回；选择方案需另行操作。固件身份相同，不能确认板上是否为 10 月 29 日位流。',
              wraplength=1050).pack(fill='x', before=tabs)

    # The upstream Host describes an older 144 image.  The identity is shared,
    # so present both interpretations without asserting which image is loaded.
    page = tabs.nametowidget(tabs.tabs()[2])
    for widget in page.winfo_children():
        if isinstance(widget, ttk.Label) and '第四组以后继续' in str(widget.cget('text')):
            widget.configure(text='10月29日三起摆 V1：144 固定三组正反；旧版行为可能不同。身份相同，需核实实际烧录位流。')
    app.notice.set('10月29日预置仅填草稿；固件身份无法区分旧版，使用前核实实际位流。')
