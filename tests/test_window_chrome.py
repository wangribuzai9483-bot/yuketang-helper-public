import ctypes
from ctypes import wintypes as wt
import os
import sys
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from yuketang_helper.window_chrome import install_caption, resize_hit


class HitTestTests(unittest.TestCase):
    def test_edges_corners_content_and_signed_screen_coordinates(self):
        for point, expected in [((0, 0), 13), ((999, 0), 14),
                                ((0, 759), 16), ((999, 759), 17),
                                ((0, 40), 10), ((999, 40), 11),
                                ((500, 0), 12), ((500, 759), 15),
                                ((500, 400), 0), ((-1, 20), 0)]:
            self.assertEqual(resize_hit(*point, 1000, 760), expected)


@unittest.skipUnless(sys.platform == 'win32', 'Windows native caption integration')
class NativeCaptionTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('1000x760+30+30')
        self.root.minsize(760, 600)
        self.close = Mock()
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.update()
        with patch.dict(os.environ, {'YUKETANG_NATIVE_TITLEBAR': '0'}):
            self.chrome = install_caption(self.root, self.close)
        self.assertIsNotNone(self.chrome)
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def settle(self):
        end = time.monotonic() + .18
        while time.monotonic() < end:
            self.root.update()
            time.sleep(.005)

    def test_retains_real_taskbar_window_and_client_geometry(self):
        chrome = self.chrome
        get_style = chrome.user.GetWindowLongW
        get_style.argtypes, get_style.restype = [wt.HWND, ctypes.c_int], wt.LONG
        style = get_style(chrome.hwnd, -16)
        self.assertFalse(self.root.overrideredirect())
        # Native sizing, system menu, minimize and maximize styles are retained.
        self.assertEqual(style & 0x000F0000, 0x000F0000)
        self.assertFalse(get_style(chrome.hwnd, -20) & 0x80)  # not WS_EX_TOOLWINDOW
        self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), (1000, 760))
        chrome.restore_native()
        self.root.update()
        self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), (1000, 760))
        self.assertFalse(chrome.enabled)
        self.assertFalse(chrome.bindings)

    def test_native_minimize_maximize_restore_and_work_area(self):
        chrome = self.chrome
        chrome.max_button.invoke()
        self.settle()
        self.assertEqual(self.root.state(), 'zoomed')
        # Maximized outer frames extend beyond the work area by resize borders.
        # It is the usable client rectangle that must avoid covering the taskbar.
        bounds = wt.RECT()
        origin = wt.POINT()
        chrome.user.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        chrome.user.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
        chrome.user.GetClientRect(chrome.hwnd, ctypes.byref(bounds))
        chrome.user.ClientToScreen(chrome.hwnd, ctypes.byref(origin))
        info = chrome._monitor(chrome.hwnd)
        self.assertEqual((origin.x, origin.y, origin.x + bounds.right, origin.y + bounds.bottom),
                         (info.work.left, info.work.top, info.work.right, info.work.bottom))
        self.assertEqual(chrome.max_button.cget('text'), '\ue923')
        chrome.max_button.invoke()
        self.settle()
        self.assertEqual(self.root.state(), 'normal')
        self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), (1000, 760))
        chrome.min_button.invoke()
        self.settle()
        self.assertEqual(self.root.state(), 'iconic')
        self.root.deiconify()
        self.settle()
        self.assertEqual(self.root.state(), 'normal')
        self.assertTrue(chrome.enabled)

    def test_close_routes_existing_callback_and_destroy_detaches_hook(self):
        self.chrome.close_button.invoke()
        self.close.assert_called_once_with()
        self.assertTrue(self.root.winfo_exists())
        # Alt+F4 and the system menu also go through the existing WM_DELETE path.
        self.chrome._post(0x0112, 0xF060)
        self.settle()
        self.assertEqual(self.close.call_count, 2)
        self.chrome._destroy(type('Event', (), {'widget': self.root})())
        self.assertFalse(self.chrome.enabled)

    def test_native_hit_testing_keeps_caption_buttons_and_content_separate(self):
        chrome = self.chrome
        send = chrome.user.SendMessageW
        send.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        send.restype = ctypes.c_ssize_t
        rect = wt.RECT()
        chrome.user.GetWindowRect(chrome.hwnd, ctypes.byref(rect))
        for x, y, expected in [(rect.left, rect.top, 13),
                                (rect.left + 400, rect.top + 20, 2),
                                (rect.right - 30, rect.top + 20, 1),
                                (rect.left + 400, rect.top + 300, 1)]:
            point = (x & 0xffff) | ((y & 0xffff) << 16)
            self.assertEqual(send(chrome.hwnd, 0x0084, 0, point), expected)
        event = type('Event', (), {'x_root': -1400, 'y_root': -100})()
        self.assertEqual(ctypes.c_short(chrome._point(event) & 0xffff).value, -1400)
        self.assertEqual(ctypes.c_short(chrome._point(event) >> 16).value, -100)

    def test_fallback_switch_and_install_failure_leave_native_frame(self):
        self.chrome.restore_native()
        with patch.dict(os.environ, {'YUKETANG_NATIVE_TITLEBAR': '1'}):
            self.assertIsNone(install_caption(self.root, self.close))
        with patch('yuketang_helper.window_chrome.WindowsCaption._build', side_effect=tk.TclError('test')):
            self.assertIsNone(install_caption(self.root, self.close))
        self.assertFalse(self.root.overrideredirect())
        self.assertFalse(self.root.winfo_children())


if __name__ == '__main__':
    unittest.main()
