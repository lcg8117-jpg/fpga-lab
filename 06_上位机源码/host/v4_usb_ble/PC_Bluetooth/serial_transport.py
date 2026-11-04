"""Explicit USB UART route. Importing opens no port and imports no driver."""
from pathlib import Path
import importlib
import sys

BAUDRATE=230400
WRITE_TIMEOUT=.2

def serial_api():
    """Load only the pySerial bundled beside this module, when requested."""
    vendor=Path(__file__).resolve().parent/'vendor_serial'
    if not (vendor/'serial/__init__.py').is_file():
        raise RuntimeError('缺少包内USB串口依赖，请完整解压电脑端')
    if str(vendor) not in sys.path:sys.path.insert(0,str(vendor))
    module=importlib.import_module('serial')
    origin=Path(module.__file__).resolve()
    if not origin.is_relative_to(vendor.resolve()):
        raise RuntimeError('已加载其他位置的串口依赖，请关闭后重新打开本窗口')
    return module

class SerialPort:
    records_physical_io=True
    async_write=False
    def __init__(self,port,*,io_sink=None,event_sink=None,baudrate=BAUDRATE,serial_factory=None):
        self.name=str(port).strip();self.error=None;self.closed=False
        self.io_sink=io_sink;self.event_sink=event_sink
        self.metadata={'transport':'usb','port':self.name,'baudrate':baudrate,'timeout':0,'write_timeout':WRITE_TIMEOUT,'route':0}
        if not self.name:raise ValueError('USB串口名称不能为空')
        if not isinstance(baudrate,int) or isinstance(baudrate,bool) or not 300<=baudrate<=4000000:
            raise ValueError('USB波特率需要是300～4000000之间的整数')
        factory=serial_factory if serial_factory is not None else serial_api().Serial
        # One open attempt only. No URL handler, Bluetooth COM, or auto-retry.
        self.port=factory(port=self.name,baudrate=baudrate,timeout=0,write_timeout=WRITE_TIMEOUT,rtscts=False,dsrdtr=False)
        if self.event_sink:self.event_sink('usb_open',route=0,port=self.name,baudrate=baudrate,timeout=0,write_timeout=WRITE_TIMEOUT)
    def _check(self):
        if self.error:raise OSError(self.error)
        if self.closed:raise OSError('USB串口已关闭')
    @property
    def in_waiting(self):
        self._check()
        return int(self.port.in_waiting)
    def read(self,n):
        self._check()
        if n<0:raise ValueError('读取长度不能为负')
        try:data=bytes(self.port.read(n))
        except Exception as exc:
            self.error='USB读取失败：'+repr(exc)
            if self.event_sink:self.event_sink('usb_read_error',route=0,reason=self.error)
            raise OSError(self.error) from exc
        if data and self.io_sink:self.io_sink(0,0,data)
        return data
    def write(self,raw):
        self._check();raw=bytes(raw)
        if len(raw)!=48 or raw[:4]!=b'FD2C' or raw[44]!=0:
            raise ValueError('USB只能发送完整48字节route0 FD2C请求')
        try:count=self.port.write(raw)
        except Exception as exc:
            self.error='USB写入结果未知，不会重发：'+repr(exc)
            if self.event_sink:self.event_sink('write_unknown',route=0,confirmed_bytes=None,reason=self.error)
            raise OSError(self.error) from exc
        if count!=len(raw):
            confirmed=count if isinstance(count,int) and 0<=count<=len(raw) else None
            if confirmed and self.io_sink:self.io_sink(0,1,raw[:confirmed])
            self.error='USB写入不完整，结果未知且不会重发'
            if self.event_sink:self.event_sink('write_unknown',route=0,confirmed_bytes=confirmed,reason=self.error)
            raise OSError(self.error)
        if self.io_sink:self.io_sink(0,1,raw)
        if self.event_sink:self.event_sink('physical_write',route=0,bytes=count,port=self.name)
        return count
    def close(self):
        if self.closed:return
        self.closed=True
        self.port.close()
        if self.event_sink:self.event_sink('usb_closed',route=0,port=self.name)
    def __enter__(self):return self
    def __exit__(self,*unused):self.close()
