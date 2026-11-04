"""Attach the V4 BLE supplement to the bridged, unchanged original Host."""

from tkinter import ttk
from .bt10_windows_select import install

DEFAULT_ADDRESS = 'D0:54:7B:AA:0C:84'


def configure_window(window, ui, app):
    ui.route_mode.set('BLE')
    ui.port.set('')
    ui.ble_address.set(DEFAULT_ADDRESS)
    ui.identity.set(f'等待蓝牙核验 · V4 固件 {app.FIRMWARE[4]:08X} / schema {app.SCHEMAS[4]:08X}')
    ui.notice.set('确认蓝牙地址后点连接；需要完整波形时请选择 USB+BLE 并填写 USB 串口。')
    window.title('V4 蓝牙连接 · 网页桥')

    def walk(widget):
        for child in widget.winfo_children():
            yield child
            yield from walk(child)

    for widget in walk(window):
        if isinstance(widget, ttk.Combobox) and widget.cget('textvariable') == str(ui.route_mode):
            widget.configure(values=('BLE', 'USB+BLE', 'USB'))
        if isinstance(widget, ttk.Notebook):
            for index in range(len(widget.tabs())):
                if '通信' in widget.tab(index, 'text'):
                    widget.select(index)
    install(DEFAULT_ADDRESS)
