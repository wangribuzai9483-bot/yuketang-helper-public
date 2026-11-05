"""Small, transient vertical scroll thumbs shared by Text, Treeview and forms."""
import time
import tkinter as tk
from tkinter import scrolledtext, ttk

from .ui_shell import rounded_rect


class OverlayScrollbar(tk.Canvas):
    """A real draggable scrollbar over the viewport, without a reserved gutter."""
    HIDE_DELAY = 900
    FADE_IN = .09
    FADE_OUT = .18

    def __init__(self, viewport, *, below_header=False):
        super().__init__(viewport, width=14, highlightthickness=0, bd=0,
                         takefocus=False, cursor='arrow')
        self.viewport = viewport
        self.below_header = below_header
        self.first, self.last = 0.0, 1.0
        self.initialized = False
        self.opacity = 0.0
        self.visible = False
        self.hovered = False
        self.dragging = False
        self.drag_offset = 0
        self.hide_job = None
        self.fade_job = None
        self.fade_target = None
        self.spring_job = None
        self.thumb_width = 6.0
        self.thumb_velocity = 0.0
        self.wheel_remainder = 0.0
        self.closed = False
        viewport._overlay_scrollbar = self
        viewport.configure(yscrollcommand=self.set)
        self.bind('<Configure>', lambda _: self._draw())
        self.bind('<Enter>', self._enter)
        self.bind('<Leave>', self._leave)
        self.bind('<ButtonPress-1>', self._press)
        self.bind('<B1-Motion>', self._drag)
        self.bind('<ButtonRelease-1>', self._release)
        for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            self.bind(sequence, self.scroll_wheel)
        self.viewport_bindings = []
        for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>', '<Next>', '<Prior>'):
            ident = viewport.bind(sequence, lambda _: self.activity(), add='+')
            self.viewport_bindings.append((sequence, ident))
        ident = viewport.bind('<Configure>', lambda _: self._place() if self.visible else None, add='+')
        self.viewport_bindings.append(('<Configure>', ident))
        self.bind('<Destroy>', self._cleanup, add='+')

    @property
    def scrollable(self):
        return self.last - self.first < .999999

    def set(self, first, last):
        first, last = max(0.0, float(first)), min(1.0, float(last))
        moved = self.initialized and abs(first - self.first) > .000001
        self.first, self.last = first, last
        self.initialized = True
        if not self.scrollable:
            self._conceal()
        elif moved:
            self.activity()
        if self.visible:
            self._draw()

    def activity(self):
        if self.closed or not self.scrollable or not self.viewport.winfo_ismapped():
            return
        if not self.visible:
            self.visible = True
        self._place()
        self._animate(1.0, self.FADE_IN)
        self._schedule_hide()

    def _place(self):
        top = self._top_inset()
        self.place(relx=1, x=-3, y=top, relheight=1,
                   height=-(top + 5), width=14, anchor='ne')
        self.tk.call('raise', self._w)

    def _top_inset(self):
        if not self.below_header:
            return 5
        width = self.viewport.winfo_width()
        height = self.viewport.winfo_height()
        probe_x = min(10, max(1, width - 2))
        for y in range(2, min(height - 5, 100)):
            if self.viewport.identify_region(probe_x, y) in {'cell', 'tree'}:
                return y + 2
        return 5

    def _spring_thumb(self):
        if self.spring_job is not None:
            return

        def frame():
            self.spring_job = None
            if self.closed or not self.visible:
                return
            target = 12.0 if self.dragging else 10.0 if self.hovered else 6.0
            self.thumb_velocity = (self.thumb_velocity + (target - self.thumb_width) * .28) * .70
            self.thumb_width += self.thumb_velocity
            if abs(target - self.thumb_width) < .08 and abs(self.thumb_velocity) < .08:
                self.thumb_width, self.thumb_velocity = target, 0.0
            else:
                self.spring_job = self.after(16, frame)
            self._draw()

        frame()

    def _cancel(self, name):
        job = getattr(self, name)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
            setattr(self, name, None)

    def _schedule_hide(self):
        self._cancel('hide_job')
        if not self.closed and self.visible and not self.dragging and not self.hovered:
            self.hide_job = self.after(self.HIDE_DELAY, self._hide)

    def _hide(self):
        self._cancel('hide_job')
        if not self.dragging and not self.hovered:
            self._animate(0.0, self.FADE_OUT)

    def _animate(self, target, duration):
        if self.fade_target == target and self.fade_job is not None:
            return
        self._cancel('fade_job')
        self.fade_target = target
        if abs(target - self.opacity) < .001:
            self.opacity = target
            self._draw()
            return
        start, started = self.opacity, time.monotonic()

        def frame():
            self.fade_job = None
            if self.closed:
                return
            progress = min(1.0, (time.monotonic() - started) / duration)
            self.opacity = start + (target - start) * (1 - (1 - progress) ** 3)
            self._draw()
            if progress < 1:
                self.fade_job = self.after(16, frame)
            elif target == 0:
                self.place_forget()
                self.visible = False
        frame()

    def _conceal(self):
        self._cancel('hide_job')
        self._cancel('fade_job')
        self._cancel('spring_job')
        self.thumb_width, self.thumb_velocity = 6.0, 0.0
        self.opacity = 0.0
        self.fade_target = 0.0
        self.visible = False
        self.place_forget()

    def _background(self):
        try:
            return self.viewport.cget('background')
        except tk.TclError:
            return ttk.Style(self).lookup(self.viewport.cget('style') or 'Treeview', 'background') or '#FFFFFF'

    def _blend(self, color, background):
        foreground = self.winfo_rgb(color)
        back = self.winfo_rgb(background)
        return '#' + ''.join(f'{round((b + (f-b)*self.opacity)/257):02x}' for f, b in zip(foreground, back))

    def thumb_geometry(self):
        track = max(1, self.winfo_height() - 4)
        span = min(1.0, max(0.0, self.last - self.first))
        length = min(track, max(32, min(104, track * span)))
        travel = max(0, track - length)
        top = 2 + travel * self.first / max(.000001, 1 - span)
        return top, length, travel

    def _draw(self):
        if self.closed:
            return
        background = self._background()
        self.configure(background=background)
        self.delete('all')
        if not self.visible or self.winfo_height() < 3:
            return
        top, length, _ = self.thumb_geometry()
        width = self.thumb_width
        center = self.winfo_width() / 2
        rounded_rect(self, center-width/2, top, center+width/2, top+length, width/2,
                     fill=self._blend('#FFFFFF', background),
                     outline=self._blend('#C8C3B9' if self.dragging else '#D8D4CC', background), width=1)

    def _enter(self, _event):
        if self.closed or not self.visible:
            return
        self.hovered = True
        self._cancel('hide_job')
        self._animate(1.0, self.FADE_IN)
        self._spring_thumb()

    def _leave(self, _event):
        if self.closed:
            return
        self.hovered = False
        self._spring_thumb()
        self._schedule_hide()

    def _press(self, event):
        self.dragging = True
        self._cancel('hide_job')
        self._spring_thumb()
        top, length, _ = self.thumb_geometry()
        self.drag_offset = event.y - top if top <= event.y <= top + length else length / 2
        self._drag(event)
        return 'break'

    def _drag(self, event):
        if not self.dragging:
            return
        _, _, travel = self.thumb_geometry()
        fraction = (event.y - 2 - self.drag_offset) / max(1, travel)
        fraction = max(0.0, min(1.0, fraction)) * max(0.0, 1 - (self.last - self.first))
        self.viewport.yview('moveto', fraction)
        self.activity()
        return 'break'

    def _release(self, _event):
        self.dragging = False
        self._spring_thumb()
        self._schedule_hide()
        return 'break'

    def scroll_wheel(self, event):
        if not self.scrollable:
            return
        number = getattr(event, 'num', None)
        delta = -1 if number == 4 else 1 if number == 5 else -getattr(event, 'delta', 0) / 120
        self.wheel_remainder += delta
        units = int(self.wheel_remainder)
        if units:
            self.wheel_remainder -= units
            self.viewport.yview_scroll(units, 'units')
        self.activity()
        return 'break'

    def _cleanup(self, event):
        if event.widget is not self:
            return
        self.closed = True
        self._cancel('hide_job')
        self._cancel('fade_job')
        self._cancel('spring_job')
        for sequence, ident in self.viewport_bindings:
            try:
                self.viewport.unbind(sequence, ident)
            except tk.TclError:
                pass


class ScrolledText(scrolledtext.ScrolledText):
    """Keep the standard Text API and geometry wrapper, replacing its gutter."""
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.vbar.destroy()
        self.vbar = OverlayScrollbar(self)
        self.scrollbar = self.vbar
