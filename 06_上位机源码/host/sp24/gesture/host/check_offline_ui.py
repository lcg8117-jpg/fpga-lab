"""Offline Tk startup and geometry evidence; never opens COM."""
import json
from pathlib import Path
import tkinter as tk
from swing_tune_host import Engine, Window, phase_description, gesture_description, gesture_runtime_description
from tune_parameters import HISTORICAL_PID


def main():
    out = Path(__file__).resolve().parents[1]/'reports/host'
    out.mkdir(parents=True, exist_ok=True)
    engine = Engine(out/'unused_offline_sessions', allow_device=False)
    root = tk.Tk()
    root.withdraw()
    window = Window(root, engine, 8371)
    root.geometry('1180x850+20000+20000')
    root.update_idletasks()
    root.deiconify()
    root.update()
    checks = []
    try:
        assert len(window.inputs) == 36 and engine.worker is None
        assert len(window.presets) == 6 and len(window.preset_box['values']) == 6
        assert not any(name in window.inputs for name in ('brake_quiet_ms','brake_max_ms','drive_ms'))
        assert window.inputs['forward_ms'].get() == '100'
        assert window.inputs['reverse_ms'].get() == '220'
        before = len(engine.model.requests)
        window.prefill()
        assert len(engine.model.requests) == before and engine.worker is None
        assert all(int(window.inputs[k].get()) == v for k,v in HISTORICAL_PID.items())
        def inspect(widget, result):
            if widget.winfo_viewable():
                box = [widget.winfo_rootx()-root.winfo_rootx(), widget.winfo_rooty()-root.winfo_rooty(), widget.winfo_width(), widget.winfo_height()]
                text = str(widget.cget('text')) if 'text' in widget.keys() else ''
                result.append(dict(widget=str(widget), type=widget.winfo_class(), box=box, text=text))
                assert box[0] >= 0 and box[1] >= 0
                assert box[0]+box[2] <= root.winfo_width()+2
                assert box[1]+box[3] <= root.winfo_height()+2
                if widget.winfo_class() in ('TLabel', 'TButton', 'Button', 'TEntry', 'TCheckbutton'):
                    assert widget.winfo_height() >= widget.winfo_reqheight(), (str(widget), 'text height clipped', box, widget.winfo_reqheight())
            for child in widget.winfo_children():
                inspect(child, result)
        for index, name in ((0, 'swing'), (1, 'pid'),(2,'integration'),(3,'profile'),(4,'presets')):
            window.book.select(index)
            root.update()
            if index == 0:
                window.phase.set(phase_description(dict(swing_state=40, phase_end_reason=4, phase_elapsed_ms=10000,
                    effective_forward_ms=1000, session_reverse_ms=1000, capture_candidate=False,
                    handoff_hold_ms=20, handoff_confirmed=True, timestamp_ms=4294967295)))
                root.update_idletasks()
            if index == 2:
                window.gesture_event.set(gesture_description(dict(event_count=4294967295,event_ms=4294967295,
                    code=8,action=3,reason=11,event_flags=255,event_id=4294967295)))
                window.gesture_runtime.set(gesture_runtime_description(dict(ready=True,enabled=True,error=8,
                    error_count=65535,blocked=True,fresh_balance_ready=False,motor=False,zero=True,estop=True,fault=True,
                    swing_state=40,handoff=True,auto_armed=True,manual_run=False,deadman=True,hold_initialized=True,
                    rotation_active=True,busy=True,poll_sequence=4294967295,timestamp_ms=4294967295)))
                root.update_idletasks()
            assert window.canvas.winfo_height() >= 150
            widgets = []
            inspect(root, widgets)
            checks.append(dict(tab=name, widgets=widgets))
        root.geometry('1080x820+20000+20000')
        window.loglabel.set('记录目录：'+str(out/'a_long_session_directory_20261015_180000/samples.csv'))
        for index in range(5):
            window.book.select(index)
            root.update()
            widgets = []
            inspect(root, widgets)
            assert window.canvas.winfo_height() >= 100
            checks.append(dict(tab='minimum_window_long_log_path_'+str(index), widgets=widgets))
        print('PASS offline Tk startup, 36 fields, 9-field fill-only, 5-tab geometry and long event/runtime labels, no COM')
        (out/'ui_check.json').write_text(json.dumps({'status':'PASS', 'scope':'startup and widget geometry',
            'visual_limitation':'Off-screen Windows PrintWindow omitted Tk children; no full visual screenshot acceptance claimed.',
            'tabs':checks, 'dimensions':[root.winfo_width(),root.winfo_height()], 'hardware_opened':False}, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        window.alive = False
        root.destroy()
        engine.close()


if __name__ == '__main__':
    main()
