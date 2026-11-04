"""Minimal 115200/8N1 backend. Windows uses ctypes, no pip dependency.

Opening/reading never sends commands. Non-Windows systems use optional
pyserial. Windows API execution must be verified on the destination PC.
"""
from __future__ import annotations
import os
import re
from typing import Any


class SerialPort:
    def __init__(self, port: str, baud: int = 115200, read_timeout_ms: int = 10) -> None:
        self.handle: Any = None
        self.serial: Any = None
        if baud <= 0:
            raise ValueError('baud must be positive')
        if not 1 <= read_timeout_ms <= 1000:
            raise ValueError('read_timeout_ms must be1..1000')
        if os.name != 'nt':
            try:
                import serial
            except ImportError as exc:
                raise RuntimeError('Non-Windows serial access requires pyserial; offline decoding does not.') from exc
            self.serial = serial.Serial(port, baud, timeout=read_timeout_ms/1000, write_timeout=2)
            return
        normalized=port.removeprefix('\\\\.\\').upper()
        if not re.fullmatch(r'COM[1-9][0-9]*',normalized):
            raise ValueError('Windows port must be COM1, COM2, ...; arbitrary device paths are rejected.')
        port=normalized
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        self.api = c.WinDLL('kernel32', use_last_error=True)
        api = self.api
        api.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
        api.CreateFileW.restype=w.HANDLE
        api.CloseHandle.argtypes=[w.HANDLE];api.CloseHandle.restype=w.BOOL
        for name in ('GetCommState','SetCommState','SetCommTimeouts'):
            getattr(api,name).argtypes=[w.HANDLE,c.c_void_p];getattr(api,name).restype=w.BOOL
        api.ReadFile.argtypes=[w.HANDLE,c.c_void_p,w.DWORD,c.POINTER(w.DWORD),c.c_void_p]
        api.ReadFile.restype=w.BOOL
        api.WriteFile.argtypes=api.ReadFile.argtypes;api.WriteFile.restype=w.BOOL
        class DCB(c.Structure):
            _fields_=[('DCBlength',w.DWORD),('BaudRate',w.DWORD),('flags',w.DWORD),
                      ('reserved',w.WORD),('XonLim',w.WORD),('XoffLim',w.WORD),
                      ('ByteSize',w.BYTE),('Parity',w.BYTE),('StopBits',w.BYTE),
                      ('XonChar',c.c_char),('XoffChar',c.c_char),('ErrorChar',c.c_char),
                      ('EofChar',c.c_char),('EvtChar',c.c_char),('reserved1',w.WORD)]
        class Timeouts(c.Structure):
            _fields_=[('interval',w.DWORD),('read_multi',w.DWORD),('read_const',w.DWORD),
                      ('write_multi',w.DWORD),('write_const',w.DWORD)]
        name = port if port.startswith('\\\\.\\') else '\\\\.\\' + port
        self.handle=api.CreateFileW(name,0xC0000000,0,None,3,0x80,None)
        if self.handle == c.c_void_p(-1).value:
            self.handle=None;raise c.WinError(c.get_last_error())
        try:
            dcb=DCB();dcb.DCBlength=c.sizeof(DCB)
            self._check(api.GetCommState(self.handle,c.byref(dcb)))
            dcb.BaudRate=baud;dcb.flags=1;dcb.ByteSize=8;dcb.Parity=0;dcb.StopBits=0
            self._check(api.SetCommState(self.handle,c.byref(dcb)))
            timeouts=Timeouts(0xFFFFFFFF,0,read_timeout_ms,0,2000)
            self._check(api.SetCommTimeouts(self.handle,c.byref(timeouts)))
        except BaseException:
            self.close();raise

    def _check(self, ok: Any) -> None:
        if not ok:
            raise self.c.WinError(self.c.get_last_error())

    def read(self, count: int = 4096) -> bytes:
        if self.serial is not None:
            return self.serial.read(count)
        if self.handle is None:
            raise RuntimeError('serial port is closed')
        buffer=self.c.create_string_buffer(count);n=self.w.DWORD()
        self._check(self.api.ReadFile(self.handle,buffer,count,self.c.byref(n),None))
        return buffer.raw[:n.value]

    def write(self, data: bytes) -> None:
        if self.serial is not None:
            sent=self.serial.write(data)
            if sent != len(data):
                raise IOError('partial serial write')
            return
        while data:
            buffer=self.c.create_string_buffer(data);n=self.w.DWORD()
            self._check(self.api.WriteFile(self.handle,buffer,len(data),self.c.byref(n),None))
            if not n.value:
                raise IOError('zero-length serial write')
            data=data[n.value:]

    def close(self) -> None:
        if self.serial is not None:
            self.serial.close();self.serial=None
        if self.handle is not None:
            self.api.CloseHandle(self.handle);self.handle=None

    def __enter__(self) -> 'SerialPort':
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
