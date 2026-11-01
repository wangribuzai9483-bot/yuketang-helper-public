"""Restore only a verified window of this helper's own browser process."""
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os


@dataclass(frozen=True)
class WindowTarget:
    hwnd: int
    pid: int


class WindowPlacement(ctypes.Structure):
    _fields_ = [('length', wintypes.UINT), ('flags', wintypes.UINT), ('showCmd', wintypes.UINT),
                ('ptMinPosition', wintypes.POINT), ('ptMaxPosition', wintypes.POINT),
                ('rcNormalPosition', wintypes.RECT)]


class Windows:
    def __init__(self):
        self.api = ctypes.WinDLL('user32', use_last_error=True) if os.name == 'nt' else None
        if not self.api:
            return
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = {
            'EnumWindows': ([self.callback_type, wintypes.LPARAM], wintypes.BOOL),
            'GetWindowThreadProcessId': ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            'GetClassNameW': ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            'GetWindowRect': ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            'GetWindowPlacement': ([wintypes.HWND, ctypes.POINTER(WindowPlacement)], wintypes.BOOL),
            'GetForegroundWindow': ([], wintypes.HWND),
            'GetAncestor': ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            'IsWindow': ([wintypes.HWND], wintypes.BOOL),
            'IsWindowVisible': ([wintypes.HWND], wintypes.BOOL),
            'IsIconic': ([wintypes.HWND], wintypes.BOOL),
            'ShowWindow': ([wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            'BringWindowToTop': ([wintypes.HWND], wintypes.BOOL),
            'SetForegroundWindow': ([wintypes.HWND], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result

    def valid(self, target):
        if not self.api or not target or not self.api.IsWindow(target.hwnd):
            return False
        pid = wintypes.DWORD()
        self.api.GetWindowThreadProcessId(target.hwnd, ctypes.byref(pid))
        return pid.value == target.pid

    def candidates(self, pid):
        if not self.api:
            return []
        result = []

        @self.callback_type
        def collect(hwnd, _):
            target = WindowTarget(int(hwnd), pid)
            if not self.valid(target) or not self.api.IsWindowVisible(hwnd):
                return True
            name = ctypes.create_unicode_buffer(256)
            self.api.GetClassNameW(hwnd, name, len(name))
            if name.value != 'Chrome_WidgetWin_1':
                return True
            rect = wintypes.RECT()
            self.api.GetWindowRect(hwnd, ctypes.byref(rect))
            placement = WindowPlacement(length=ctypes.sizeof(WindowPlacement))
            normal = None
            if self.api.GetWindowPlacement(hwnd, ctypes.byref(placement)):
                p = placement.rcNormalPosition
                normal = (p.left, p.top, p.right-p.left, p.bottom-p.top)
            result.append((target, (rect.left, rect.top, rect.right-rect.left, rect.bottom-rect.top), normal))
            return True

        self.api.EnumWindows(collect, 0)
        return result

    def locate(self, pid, bounds, cached=None):
        if cached and cached.pid == pid and self.valid(cached):
            return cached
        candidates = self.candidates(pid)
        if len(candidates) == 1:
            return candidates[0][0]
        expected = tuple(bounds.get(key) for key in ('left', 'top', 'width', 'height'))
        if any(value is None for value in expected):
            return None
        matches = []
        for target, actual, normal in candidates:
            if any(rect and all(abs(a-b) <= 12 for a,b in zip(rect, expected)) for rect in (actual, normal)):
                matches.append(target)
        return matches[0] if len(matches) == 1 else None

    def restore(self, target):
        if not self.valid(target):
            return {'restored': False, 'foreground': False}
        self.api.ShowWindow(target.hwnd, 9 if self.api.IsIconic(target.hwnd) else 5)
        self.api.BringWindowToTop(target.hwnd)
        self.api.SetForegroundWindow(target.hwnd)
        foreground = self.api.GetForegroundWindow()
        root = self.api.GetAncestor(foreground, 2) if foreground else None
        valid = self.valid(target)
        return {'restored': valid and not bool(self.api.IsIconic(target.hwnd)),
                'foreground': valid and (foreground == target.hwnd or root == target.hwnd)}
