"""Tk caption with a native Windows frame, taskbar identity and sizing loop.

Only the caption is replaced. No overrideredirect window, browser commands or
worker-thread objects are used here. Set YUKETANG_NATIVE_TITLEBAR=1 to opt out.
"""
import ctypes
from ctypes import wintypes as wt
import os
import sys
import tkinter as tk

from .ui_shell import DocumentMark, style_native_caption


def resize_hit(x, y, width, height, border=6):
    """Win32 HT* value for an edge; zero for content/outside the window."""
    if not (0 <= x < width and 0 <= y < height):
        return 0
    left, right = x < border, x >= width - border
    top, bottom = y < border, y >= height - border
    if top:
        return 13 if left else 14 if right else 12
    if bottom:
        return 16 if left else 17 if right else 15
    return 10 if left else 11 if right else 0


class _MonitorInfo(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('monitor', wt.RECT),
                ('work', wt.RECT), ('flags', wt.DWORD)]


class _MinMaxInfo(ctypes.Structure):
    _fields_ = [('reserved', wt.POINT), ('max_size', wt.POINT),
                ('max_position', wt.POINT), ('min_track', wt.POINT),
                ('max_track', wt.POINT)]


class WindowsCaption:
    HEIGHT = 64
    BUTTON_WIDTH = 64
    COLOR = '#FAF9F7'

    def __init__(self, root, on_close):
        self.root, self.on_close = root, on_close
        self.hwnd = 0
        self.enabled = False
        self.bindings = []
        self._cursor_widget = None
        self._cursor_before = ''
        self._api()
        root.update_idletasks()
        geometry = root.geometry()
        self.hwnd = self.user.GetAncestor(root.winfo_id(), 2)
        if not self.hwnd:
            raise OSError('Tk wrapper window is unavailable')
        self.callback = self.proc_type(self._window_proc)
        self.subclass_id = id(self)
        if not self.comctl.SetWindowSubclass(self.hwnd, self.callback, self.subclass_id, 0):
            raise OSError('Cannot attach the custom caption')
        self.enabled = True
        try:
            self._build()
            # Keep WS_CAPTION / THICKFRAME / SYSMENU and the real taskbar window.
            # WM_NCCALCSIZE removes their painting, not their native capabilities.
            self._frame_changed()
            root.update_idletasks()
            root.geometry(geometry)
            for sequence, handler in [('<Configure>', self._configure),
                                      ('<Motion>', self._edge_cursor),
                                      ('<ButtonPress-1>', self._edge_press),
                                      ('<Destroy>', self._destroy)]:
                self.bindings.append((sequence, root.bind(sequence, handler, add='+')))
        except Exception:
            self.restore_native()
            raise

    def _api(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.comctl = ctypes.WinDLL('comctl32', use_last_error=True)
        result = ctypes.c_ssize_t
        self.proc_type = ctypes.WINFUNCTYPE(result, wt.HWND, wt.UINT, wt.WPARAM,
                                            wt.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
        signatures = [
            (self.user.GetAncestor, [wt.HWND, wt.UINT], wt.HWND),
            (self.user.GetWindowRect, [wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
            (self.user.IsZoomed, [wt.HWND], wt.BOOL),
            (self.user.MonitorFromWindow, [wt.HWND, wt.DWORD], wt.HANDLE),
            (self.user.GetMonitorInfoW, [wt.HANDLE, ctypes.POINTER(_MonitorInfo)], wt.BOOL),
            (self.user.SetWindowPos, [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wt.UINT], wt.BOOL),
            (self.user.PostMessageW, [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], wt.BOOL),
            (self.user.ReleaseCapture, [], wt.BOOL),
            (self.comctl.SetWindowSubclass, [wt.HWND, self.proc_type, ctypes.c_size_t,
                                             ctypes.c_size_t], wt.BOOL),
            (self.comctl.RemoveWindowSubclass, [wt.HWND, self.proc_type, ctypes.c_size_t], wt.BOOL),
            (self.comctl.DefSubclassProc, [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], result),
        ]
        for function, args, restype in signatures:
            function.argtypes, function.restype = args, restype

    def _build(self):
        children = self.root.winfo_children()
        self.header = tk.Frame(self.root, height=self.HEIGHT, bg=self.COLOR)
        self.header.pack(side='top', fill='x', **({'before': children[0]} if children else {}))
        self.header.pack_propagate(False)
        self.rule = tk.Frame(self.root, height=1, bg='#E9E7E2')
        self.rule.pack(side='top', fill='x', after=self.header)
        mark = DocumentMark(self.header)
        mark.source = mark.source.crop(mark.source.getbbox())
        mark.size = None
        mark.set_size(42)
        mark.configure(bg=self.COLOR)
        mark.pack(side='left', padx=(20, 10))
        self.title = tk.Label(self.header, text=self.root.title(), bg=self.COLOR,
                              fg='#252525', font=('Microsoft YaHei UI', -20))
        self.title.pack(side='left')
        for widget in (self.header, mark, self.title):
            widget.bind('<ButtonPress-1>', self._drag)
            widget.bind('<Double-Button-1>', self._toggle)
            widget.bind('<ButtonRelease-3>', self._system_menu)
        self.close_button = self._button('\ue8bb', self.close, '关闭', close=True)
        self.max_button = self._button('\ue922', self.toggle_maximize, '最大化 / 还原')
        self.min_button = self._button('\ue921', self.minimize, '最小化')

    def _button(self, glyph, command, name, close=False):
        slot = tk.Frame(self.header, width=self.BUTTON_WIDTH, bg=self.COLOR)
        slot.pack(side='right', fill='y')
        slot.pack_propagate(False)
        button = tk.Button(slot, text=glyph, command=command, bg=self.COLOR,
                           fg='#252525', activebackground='#C94242' if close else '#E7E5DF',
                           activeforeground='#FFFFFF' if close else '#252525',
                           font=('Segoe MDL2 Assets', -18), bd=0, relief='flat',
                           highlightthickness=0, takefocus=True)
        button.pack(fill='both', expand=True)
        button.bind('<Enter>', lambda _: button.configure(
            bg='#C94242' if close else '#ECEAE5', fg='#FFFFFF' if close else '#252525'))
        button.bind('<Leave>', lambda _: button.configure(bg=self.COLOR, fg='#252525'))
        button.bind('<FocusIn>', lambda _: button.configure(bg='#ECEAE5'))
        button.bind('<FocusOut>', lambda _: button.configure(bg=self.COLOR, fg='#252525'))
        button.bind('<Return>', lambda _: button.invoke())
        # A tooltip is intentionally unnecessary: these retain standard symbols,
        # and keyboard users reach native window operations with Alt+Space.
        button.window_action_name = name
        return button

    def _frame_changed(self):
        if not self.user.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, 0x0037):
            raise ctypes.WinError(ctypes.get_last_error())

    def _monitor(self, hwnd):
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        monitor = self.user.MonitorFromWindow(hwnd, 2)
        return info if self.user.GetMonitorInfoW(monitor, ctypes.byref(info)) else None

    def _window_proc(self, hwnd, message, wparam, lparam, subclass_id, reference):
        # No Tcl calls inside the native callback: resizing can reenter Tk.
        try:
            if self.enabled and message == 0x0083 and wparam:  # WM_NCCALCSIZE
                rect = ctypes.cast(lparam, ctypes.POINTER(wt.RECT)).contents
                top = rect.top
                result = self.comctl.DefSubclassProc(hwnd, message, wparam, lparam)
                if self.user.IsZoomed(hwnd):
                    info = self._monitor(hwnd)
                    if info:
                        rect.left, rect.top = info.work.left, info.work.top
                        rect.right, rect.bottom = info.work.right, info.work.bottom
                else:
                    # Preserve native side/bottom resize borders. Tk uses these
                    # widths when translating wm geometry into outer dimensions.
                    rect.top = top
                return result
            if self.enabled and message == 0x0084:  # WM_NCHITTEST
                rect = wt.RECT()
                self.user.GetWindowRect(hwnd, ctypes.byref(rect))
                x = ctypes.c_short(lparam & 0xffff).value - rect.left
                y = ctypes.c_short((lparam >> 16) & 0xffff).value - rect.top
                width, height = rect.right - rect.left, rect.bottom - rect.top
                hit = 0 if self.user.IsZoomed(hwnd) else resize_hit(x, y, width, height)
                if hit:
                    return hit
                if 0 <= y < self.HEIGHT and 0 <= x < width - 3 * self.BUTTON_WIDTH:
                    return 2  # HTCAPTION: native drag, double-click and snap
                return 1  # HTCLIENT: Tk owns our actual caption buttons
            if self.enabled and message == 0x0024:  # WM_GETMINMAXINFO
                result = self.comctl.DefSubclassProc(hwnd, message, wparam, lparam)
                info = self._monitor(hwnd)
                if info:
                    limits = ctypes.cast(lparam, ctypes.POINTER(_MinMaxInfo)).contents
                    limits.max_position.x = info.work.left - info.monitor.left
                    limits.max_position.y = info.work.top - info.monitor.top
                    limits.max_size.x = info.work.right - info.work.left
                    limits.max_size.y = info.work.bottom - info.work.top
                return result
            if message == 0x0082:  # WM_NCDESTROY (also handles external destruction)
                self.comctl.RemoveWindowSubclass(hwnd, self.callback, subclass_id)
                self.enabled = False
        except Exception:
            # Never let a Python exception escape a Win32 procedure.
            pass
        return self.comctl.DefSubclassProc(hwnd, message, wparam, lparam)

    def _post(self, message, wparam, lparam=0):
        if self.enabled:
            self.user.PostMessageW(self.hwnd, message, wparam, lparam)

    @staticmethod
    def _point(event):
        return (event.x_root & 0xffff) | ((event.y_root & 0xffff) << 16)

    def _drag(self, event):
        hit = self._edge_hit(event)
        self.user.ReleaseCapture()
        self._post(0x00A1, hit or 2, self._point(event))  # native modal move/resize
        return 'break'

    def _toggle(self, event):
        self.toggle_maximize()
        return 'break'

    def _system_menu(self, event):
        self._post(0x00A5, 2, self._point(event))  # WM_NCRBUTTONUP / HTCAPTION
        return 'break'

    def minimize(self):
        self._post(0x0112, 0xF020)  # SC_MINIMIZE, retains Tk/taskbar state

    def toggle_maximize(self):
        self._post(0x0112, 0xF120 if self.user.IsZoomed(self.hwnd) else 0xF030)

    def close(self):
        self.on_close()  # Existing shutdown/stop confirmation path, never force-kill

    def _configure(self, event):
        if event.widget is self.root and self.enabled:
            self.max_button.configure(text='\ue923' if self.user.IsZoomed(self.hwnd) else '\ue922')
            self.title.configure(text=self.root.title())

    def _edge_hit(self, event):
        if not self.enabled or self.user.IsZoomed(self.hwnd):
            return 0
        return resize_hit(event.x_root - self.root.winfo_rootx(),
                          event.y_root - self.root.winfo_rooty(),
                          self.root.winfo_width(), self.root.winfo_height())

    def _edge_press(self, event):
        hit = self._edge_hit(event)
        if hit:
            self.user.ReleaseCapture()
            self._post(0x00A1, hit, self._point(event))
            return 'break'

    def _edge_cursor(self, event):
        hit = self._edge_hit(event)
        if self._cursor_widget is not event.widget or not hit:
            if self._cursor_widget is not None:
                try:
                    self._cursor_widget.configure(cursor=self._cursor_before)
                except tk.TclError:
                    pass
                self._cursor_widget = None
        if hit:
            if self._cursor_widget is None:
                self._cursor_widget = event.widget
                self._cursor_before = event.widget.cget('cursor')
            cursor = {10: 'size_we', 11: 'size_we', 12: 'size_ns', 15: 'size_ns',
                      13: 'size_nw_se', 17: 'size_nw_se', 14: 'size_ne_sw', 16: 'size_ne_sw'}[hit]
            event.widget.configure(cursor=cursor)

    def _destroy(self, event):
        if event.widget is self.root:
            self._detach()

    def _detach(self):
        if self.enabled:
            self.comctl.RemoveWindowSubclass(self.hwnd, self.callback, self.subclass_id)
            self.enabled = False

    def restore_native(self):
        """Restore the existing caption without rebuilding any task or page."""
        self.root.update_idletasks()
        geometry = self.root.geometry()
        self._detach()
        if self._cursor_widget is not None:
            try:
                self._cursor_widget.configure(cursor=self._cursor_before)
            except tk.TclError:
                pass
            self._cursor_widget = None
        for sequence, binding in self.bindings:
            self.root.unbind(sequence, binding)
        self.bindings.clear()
        for name in ('header', 'rule'):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.destroy()
        if self.hwnd:
            self._frame_changed()
            self.root.update_idletasks()
            self.root.geometry(geometry)
        style_native_caption(self.root)


def install_caption(root, on_close):
    if sys.platform != 'win32' or os.environ.get('YUKETANG_NATIVE_TITLEBAR', '').lower() in ('1', 'true', 'yes'):
        return None
    try:
        return WindowsCaption(root, on_close)
    except (OSError, AttributeError, tk.TclError):
        # Unsupported Windows/Tk builds retain the fully working native frame.
        return None
