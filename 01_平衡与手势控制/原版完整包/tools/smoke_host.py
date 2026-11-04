"""Hidden Tk startup check using FakeTransport only; never opens a USB port."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import time
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'vendor')]
from host.app import Window
from host import protocol as p


def forbidden_usb(*args, **kwargs):
    raise AssertionError('USB access is forbidden in offline smoke test')


def main():
    import host.app
    host.app.open_usb = forbidden_usb
    errors = []
    root = tk.Tk()
    root.withdraw()
    root.report_callback_exception = lambda *args: errors.append(str(args))
    with tempfile.TemporaryDirectory(prefix='adb1_guard_gui_') as temporary:
        window = Window(root, Path(temporary), initial_port=None)
        try:
            # Match the existing config selector, so the added field is
            # exercised through the actual UI construction path.
            window.config_group.set('运动规划')
            assert window._group() == 251
            window._config_fields()
            assert 10 in window.config_rows
            deadline = time.monotonic()+3
            while time.monotonic() < deadline:
                root.update()
                if window.owner.snapshot()['authenticated']:
                    break
                time.sleep(.01)
            state = window.owner.snapshot()
            assert state['authenticated'] and not state['error'] and not errors, (state, errors)
            assert p.FIRMWARE == 0x41444231 and '保护' in p.MOTION_REASON_TEXT[17]
        finally:
            window.close()
    result = dict(passed=True, transport='FakeTransport only', window_visible=False, hardware_actions=0,
                  checked='Tk startup, identity, config10 rendered, reason17 available',
                  source_sha256={p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (ROOT/'host').glob('*.py')})
    (ROOT/'reports').mkdir(exist_ok=True)
    (ROOT/'reports/HOST_GUI_SMOKE.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', 'utf-8')
    print('PASS hidden offline host startup; no hardware access')


if __name__ == '__main__':
    main()
