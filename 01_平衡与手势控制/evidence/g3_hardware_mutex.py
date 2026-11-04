"""Shared J280 Windows hardware lease; import/construction never acquire it.

Semantics match expr_sram_coordinator.SharedMutex: nonblocking, no stealing,
no process termination and abandoned ownership rejected. Acquired/released in
the same SerialWorker thread around the entire explicit COM connection.
"""
import os

MUTEX = 'Global\\J280_HW_LOCK'

class SharedMutex:
    def __init__(self, *, api=None):
        self.api = api; self.handle = None; self.acquired = False

    def __enter__(self):
        if self.api is None:
            if os.name != 'nt':
                raise RuntimeError('Windows shared hardware mutex is required')
            import ctypes
            from ctypes import wintypes
            self.api = ctypes.WinDLL('kernel32', use_last_error=True)
            self.api.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
            self.api.CreateMutexW.restype = wintypes.HANDLE
            self.api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            self.api.WaitForSingleObject.restype = wintypes.DWORD
            self.api.ReleaseMutex.argtypes = (wintypes.HANDLE,)
            self.api.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.handle = self.api.CreateMutexW(None, False, MUTEX)
        if not self.handle:
            raise RuntimeError('Cannot create shared hardware mutex; no device access')
        status = self.api.WaitForSingleObject(self.handle, 0)
        if status != 0:
            if status == 0x80:
                self.api.ReleaseMutex(self.handle)
            self.api.CloseHandle(self.handle); self.handle = None
            raise RuntimeError('Shared lock held/abandoned/error; no stealing or process termination')
        self.acquired = True
        return self

    def __exit__(self, *args):
        if self.acquired:
            try:
                self.api.ReleaseMutex(self.handle)
            finally:
                self.api.CloseHandle(self.handle)
                self.handle = None; self.acquired = False
