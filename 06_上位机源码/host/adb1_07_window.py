"""Disconnected 07/S1 window and explicit USB lease, outside upstream sources."""
import re

from host.adb1_07.hardware_mutex import SharedMutex


class LockedUSB:
    """Acquire the delivered lease only in the worker's explicit connect call."""
    def __init__(self, port):
        if not isinstance(port, str) or not re.fullmatch(r'COM[1-9][0-9]*', port, re.I):
            raise ValueError('请选择本机 USB 串口')
        import serial
        self.lease = SharedMutex()
        self.lease.__enter__()
        self.serial = None
        self.closed = False
        try:
            self.serial = serial.Serial(port=None, baudrate=2000000, timeout=.002,
                                        write_timeout=.02, exclusive=True)
            self.serial.dtr = False
            self.serial.rts = False
            self.serial.port = port
            self.serial.open()
        except BaseException:
            try:
                if self.serial is not None:
                    self.serial.close()
            finally:
                self.lease.__exit__(None, None, None)
            raise

    def __getattr__(self, name):
        return getattr(self.serial, name)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.serial.close()
        finally:
            self.lease.__exit__(None, None, None)


def create_window(root, logs, bridge, modules=None):
    from host.adb1_07_bridge import install, load_upstream
    modules = modules or load_upstream()
    install(bridge, modules)
    app = modules['app']
    app.open_usb = LockedUSB

    class Window(app.Window):
        def __init__(self):
            self.initializing = True
            super().__init__(root, logs, initial_port=None)
            self.initializing = False

        def _connect(self, port):
            if self.initializing:
                self.mode_text = 'USB 尚未连接'
                return
            return super()._connect(port)

    window = Window()
    root.title('J280 ADB1 07 · 指令调度修复 · S1 · 角5/位置25ms')
    window.status.set('07 / S1 草稿已填；请明确连接 USB，再写入六参数并独立读回。')
    return window
