"""Keep a handle to the CDP-identified browser, never terminate by process name."""
import ctypes
from ctypes import wintypes
import os
import threading


class OwnedBrowserProcess:
    def __init__(self, pid):
        self.lock=threading.Lock();self.handle=None;self.api=None
        if os.name!='nt':return
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        signatures={
            'OpenProcess':([wintypes.DWORD,wintypes.BOOL,wintypes.DWORD],wintypes.HANDLE),
            'QueryFullProcessImageNameW':([wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)],wintypes.BOOL),
            'GetExitCodeProcess':([wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)],wintypes.BOOL),
            'TerminateProcess':([wintypes.HANDLE,wintypes.UINT],wintypes.BOOL),
            'CloseHandle':([wintypes.HANDLE],wintypes.BOOL),
        }
        for name,(args,result) in signatures.items():
            method=getattr(api,name);method.argtypes=args;method.restype=result
        handle=api.OpenProcess(0x1001,False,pid)  # query limited information + terminate
        if not handle:return
        path=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(len(path))
        if not api.QueryFullProcessImageNameW(handle,0,path,ctypes.byref(size)) or os.path.basename(path.value).lower() not in {'msedge.exe','chrome.exe','chromium.exe'}:
            api.CloseHandle(handle);return
        self.api=api;self.handle=handle

    def terminate(self):
        with self.lock:
            if not self.handle:return False
            code=wintypes.DWORD()
            if not self.api.GetExitCodeProcess(self.handle,ctypes.byref(code)) or code.value!=259:return False
            # This original kernel handle remains bound to the same process even
            # after exit/PID reuse. No unrelated browser or user profile is used.
            return bool(self.api.TerminateProcess(self.handle,1))

    def close(self):
        with self.lock:
            if self.handle:
                self.api.CloseHandle(self.handle);self.handle=None

    def __del__(self):
        try:self.close()
        except Exception:pass
