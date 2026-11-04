"""Bounded read-only JSON access that does not deny atomic replacement on Windows.

Only JSON beneath the explicitly supplied metadata root is accepted. No device paths,
COM/bridge/control calls or file mutation. A held handle permits FILE_SHARE_DELETE
so a concurrent writer can atomically replace its complete destination snapshot.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 4 * 1024 * 1024


def _path(path, root=ROOT):
    p = Path(path).resolve()
    if not p.is_relative_to(Path(root).resolve()) or p.suffix.lower() != '.json':
        raise ValueError('Only JSON files beneath the selected root are permitted')
    return p


def read_bytes(path, max_bytes=MAX_BYTES, *, root=ROOT):
    p = _path(path, root=root)
    if not isinstance(max_bytes, int) or not 0 < max_bytes <= MAX_BYTES:
        raise ValueError('Bounded metadata read required')
    if os.name != 'nt':
        with p.open('rb') as stream:
            value = stream.read(max_bytes + 1)
        if len(value) > max_bytes:
            raise ValueError('Metadata exceeds bounded read')
        return value
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileSizeEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
    kernel.GetFileSizeEx.restype = wintypes.BOOL
    kernel.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel.ReadFile.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(p), 0x80000000, 0x1 | 0x2 | 0x4,
                                None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = ctypes.c_longlong()
        if not kernel.GetFileSizeEx(handle, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not 0 <= size.value <= max_bytes:
            raise ValueError('Metadata exceeds bounded read')
        output = []
        remaining = size.value
        while remaining:
            amount = min(65536, remaining)
            buffer = ctypes.create_string_buffer(amount)
            got = wintypes.DWORD()
            if not kernel.ReadFile(handle, buffer, amount, ctypes.byref(got), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if got.value == 0:
                raise OSError('Snapshot truncated before bounded read completed')
            output.append(buffer.raw[:got.value])
            remaining -= got.value
        return b''.join(output)
    finally:
        kernel.CloseHandle(handle)


def read_json(path, *, root=ROOT):
    return json.loads(read_bytes(path, root=root).decode('utf-8'))
