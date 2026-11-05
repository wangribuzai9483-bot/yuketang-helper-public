import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest

from yuketang_helper.page_components import ScrollableForm
from yuketang_helper.scroll_widgets import OverlayScrollbar, ScrolledText


class ScrollWidgetTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('620x320+20+20')
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        self.root.destroy()
        pending = self.root.tk.call('after', 'info')
        self.assertFalse(pending, f'Scroll callbacks survived root destruction: {pending}')
        self.assertFalse(self.errors)

    def settle(self, seconds=.12):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(.005)

    def text(self, long=True):
        text = ScrolledText(self.root, wrap='word')
        text.pack(fill='both', expand=True)
        text.insert('1.0', '\n'.join(f'示例内容 {i}' for i in range(160)) if long else '只有一行')
        self.settle()
        return text

    def test_native_wheel_reveals_then_hides_without_changing_layout(self):
        text = self.text()
        bar = text.scrollbar
        width = text.winfo_width()
        self.assertFalse(bar.visible)
        text.configure(state='disabled')
        text.event_generate('<MouseWheel>', delta=-120)
        self.settle()
        self.assertGreater(text.yview()[0], 0)
        self.assertTrue(bar.visible)
        self.assertEqual(bar.winfo_manager(), 'place')
        self.settle(1.15)
        self.assertFalse(bar.visible)
        self.assertEqual(text.winfo_width(), width)
        self.assertEqual(text.cget('state'), 'disabled')

    def test_short_content_never_displays_a_thumb(self):
        text = self.text(long=False)
        text.event_generate('<MouseWheel>', delta=-120)
        self.settle()
        self.assertFalse(text.scrollbar.visible)
        self.assertFalse(text.scrollbar.winfo_manager())

    def test_drag_reaches_the_bottom_and_preserves_text(self):
        text = self.text()
        original = text.get('1.0', 'end-1c')
        bar = text.scrollbar
        bar.activity()
        self.settle()
        top, length, _ = bar.thumb_geometry()
        bar.event_generate('<ButtonPress-1>', x=7, y=round(top+length/2))
        bar.event_generate('<B1-Motion>', x=7, y=bar.winfo_height()-2)
        bar.event_generate('<ButtonRelease-1>', x=7, y=bar.winfo_height()-2)
        self.settle()
        self.assertGreater(text.yview()[0], .7)
        self.assertAlmostEqual(text.yview()[1], 1, places=2)
        self.assertEqual(text.get('1.0', 'end-1c'), original)
        self.assertFalse(bar.dragging)

    def test_new_scroll_cancels_an_old_fade_and_hover_holds_visibility(self):
        bar = self.text().scrollbar
        bar.activity()
        self.settle()
        bar._hide()
        self.settle(.05)
        bar.activity()
        self.settle()
        self.assertTrue(bar.visible)
        self.assertAlmostEqual(bar.opacity, 1)
        bar.HIDE_DELAY = 30
        bar.event_generate('<Enter>')
        self.settle(.25)
        self.assertTrue(bar.visible)
        bar.event_generate('<Leave>')
        self.settle(.25)
        self.assertFalse(bar.visible)

    def test_form_wheel_over_entry_scrolls_only_its_own_canvas(self):
        form = ScrollableForm(self.root)
        entry = ttk.Entry(form.body)
        entry.pack(fill='x')
        for i in range(40):
            ttk.Label(form.body, text=f'设置项 {i}').pack(pady=6)
        outside = ttk.Entry(self.root)
        outside.pack()
        self.settle()
        self.assertFalse(form.scrollbar.visible)
        entry.event_generate('<MouseWheel>', delta=-120)
        self.settle()
        self.assertGreater(form.canvas.yview()[0], 0)
        self.assertTrue(form.scrollbar.visible)
        position = form.canvas.yview()
        outside.event_generate('<MouseWheel>', delta=-120)
        self.settle()
        self.assertEqual(form.canvas.yview(), position)

    def test_inner_text_scroll_does_not_also_scroll_the_form(self):
        form = ScrollableForm(self.root)
        inner = ScrolledText(form.body, height=4)
        inner.pack(fill='x')
        inner.insert('1.0', '\n'.join(map(str, range(100))))
        for i in range(30):
            ttk.Label(form.body, text=str(i)).pack(pady=6)
        self.settle()
        position = form.canvas.yview()
        inner.event_generate('<MouseWheel>', delta=-120)
        self.settle()
        self.assertGreater(inner.yview()[0], 0)
        self.assertEqual(form.canvas.yview(), position)

    def test_high_resolution_wheel_accumulates_and_treeview_uses_same_thumb(self):
        tree = ttk.Treeview(self.root)
        tree.pack(fill='both', expand=True)
        bar = OverlayScrollbar(tree)
        for i in range(60):
            tree.insert('', 'end', text=str(i))
        self.settle()
        self.assertFalse(bar.visible)
        event = SimpleNamespace(delta=-30)
        for _ in range(4):
            bar.scroll_wheel(event)
        self.settle()
        self.assertGreater(tree.yview()[0], 0)
        self.assertTrue(bar.visible)

    def test_record_table_thumb_stays_below_heading_and_expands_on_hover(self):
        tree = ttk.Treeview(self.root, columns=('name',), show='headings', height=5)
        tree.heading('name', text='课程')
        tree.pack(fill='both', expand=True)
        bar = OverlayScrollbar(tree, below_header=True)
        for i in range(60):
            tree.insert('', 'end', values=(f'模拟课程 {i}',))
        self.settle()
        bar.activity()
        self.settle()
        first_row = next(y for y in range(tree.winfo_height())
                         if tree.identify_region(10, y) == 'cell')
        self.assertGreaterEqual(bar.winfo_y(), first_row)
        bar.event_generate('<Enter>')
        self.settle(.28)
        self.assertGreater(bar.thumb_width, 9)
        bar.event_generate('<Leave>')
        self.settle(.35)
        self.assertAlmostEqual(bar.thumb_width, 6, delta=.25)
        top, length, _ = bar.thumb_geometry()
        bar.event_generate('<ButtonPress-1>', x=7, y=round(top + length / 2))
        bar.event_generate('<B1-Motion>', x=7, y=bar.winfo_height() - 2)
        bar.event_generate('<ButtonRelease-1>', x=7, y=bar.winfo_height() - 2)
        self.settle()
        self.assertGreater(tree.yview()[0], .7)

    def test_destroy_cancels_timers_and_keeps_other_root_wheel_bindings(self):
        other = self.root.bind('<MouseWheel>', lambda _: None, add='+')
        form = ScrollableForm(self.root)
        for i in range(30):
            ttk.Label(form.body, text=str(i)).pack(pady=6)
        self.settle()
        form.scrollbar.activity()
        self.settle(.025)
        jobs = [job for job in (form.scrollbar.hide_job, form.scrollbar.fade_job) if job]
        bindings = list(form.wheel_bindings)
        form.destroy()
        self.settle()
        current_jobs = self.root.tk.call('after', 'info')
        self.assertTrue(all(job not in current_jobs for job in jobs))
        script = self.root.bind('<MouseWheel>')
        self.assertIn(other, script)
        self.assertTrue(all(ident not in self.root.bind(sequence) for sequence, ident in bindings))


if __name__ == '__main__':
    unittest.main()
