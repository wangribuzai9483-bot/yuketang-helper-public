"""Shared warm-white pages and nonblocking 180ms detail transitions."""
import tkinter as tk
import time
from tkinter import ttk
from PIL import Image, ImageTk
from .ui_shell import ASSETS, BG, MUTED, Backdrop, DocumentMark, PageStack, ActionButton
from .scroll_widgets import OverlayScrollbar


class FeaturePage(ttk.Frame):
    def __init__(self, parent, title, subtitle, detail_title, detail_label='更多操作与详情', icon=None):
        super().__init__(parent)
        self.pack(fill='both', expand=True)
        self.deck = PageStack(self)
        self.deck.pack(fill='both', expand=True)
        self.front = tk.Canvas(self.deck, bg=BG, highlightthickness=0)
        self.detail = ttk.Frame(self.deck, padding=(28, 22))
        self.deck.add(self.front)
        self.deck.add(self.detail)
        self.source = Image.open(ASSETS / 'workspace.png').convert('RGB')
        self.resize_job = None
        self.body = Backdrop(self.front, self.front)
        self.body.place(relx=.5, rely=.47, anchor='center', relwidth=.84)
        self.mark = DocumentMark(self.body, icon)
        self.mark.set_size(100)
        self.mark.pack(pady=(0, 14))
        self.title = tk.StringVar(value=title) if isinstance(title, str) else title
        self.title_label = ttk.Label(self.body, textvariable=self.title, font=('Microsoft YaHei UI', 28, 'bold'), justify='center')
        self.title_label.pack()
        self.subtitle = ttk.Label(self.body, text=subtitle, foreground=MUTED, justify='center')
        self.subtitle.pack(pady=(10, 22))
        self.content = Backdrop(self.body, self.front)
        self.content.pack(fill='x')
        self.actions = Backdrop(self.body, self.front)
        self.actions.pack(pady=(18, 4))
        self.extra = Backdrop(self.body, self.front)
        self.extra.pack(fill='x', pady=(10, 0))
        link = tk.Label(self.front, text=detail_label+'  ⌄', bg=BG, fg=MUTED,
                        font=('Microsoft YaHei UI', 9), cursor='hand2', takefocus=1)
        link.place(relx=.5, rely=1, y=-24, anchor='s')
        link.bind('<Button-1>', lambda _: self.open_details())
        link.bind('<Return>', lambda _: self.open_details())
        header = ttk.Frame(self.detail)
        header.pack(fill='x', pady=(0, 20))
        ActionButton(header, '‹ 返回', self.close_details, primary=True, width=110).pack(side='right')
        ttk.Label(header, text=detail_title, font=('Microsoft YaHei UI', 23, 'bold')).pack(anchor='w')
        self.detail_body = ttk.Frame(self.detail)
        self.detail_body.pack(fill='both', expand=True)
        self.front.bind('<Configure>', self._resize)
        self.bind('<Destroy>', self._cleanup, add='+')

    def _resize(self, event):
        self.body.place_configure(relwidth=0, width=min(int(event.width*.84), 930))
        self.subtitle.configure(wraplength=max(260, event.width-100))
        self.title_label.configure(wraplength=max(260, event.width-110))
        for widget in self.extra.winfo_children():
            if isinstance(widget, ttk.Label):
                widget.configure(wraplength=max(250, min(event.width-100, 650)))
        self._paint()

    def _paint(self):
        self.resize_job = None
        # Keep enough artwork for either sidebar width. During a toggle only
        # its centered crop moves; neither pixels nor Tk images are rebuilt.
        size = (max(2, self.front.winfo_width(), self.winfo_toplevel().winfo_width()-76),
                max(2, self.front.winfo_height()))
        if getattr(self.front, 'background_scaled', None) is None or self.front.background_scaled.size != size:
            self.front.background_scaled = self.source.resize(size, Image.Resampling.BILINEAR)
            self.photo = ImageTk.PhotoImage(self.front.background_scaled, master=self.front)
            self.front.background_photo = self.photo
            self.front._backdrop_source = self.front.background_scaled
        if not hasattr(self, 'background_item'):
            self.background_item = self.front.create_image(0, 0, anchor='nw', tags='background')
        self.front.itemconfigure(self.background_item, image=self.photo)
        offset = (self.front.winfo_width()-size[0])//2
        self.front.background_offset = (offset, 0)
        self.front.coords(self.background_item, offset, 0)
        self.front.tag_lower(self.background_item)
        self.body.refresh()
        self.content.refresh()
        self.actions.refresh()
        self.extra.refresh()

    def open_details(self):
        self.deck.select(self.detail)

    def close_details(self):
        self.deck.select(self.front)

    def _cleanup(self, event):
        if event.widget is self and self.resize_job is not None:
            self.after_cancel(self.resize_job)
            self.resize_job = None


class Disclosure(ttk.Frame):
    def __init__(self, parent, title, height=230):
        super().__init__(parent)
        self.target_height = height
        self.opened = False
        self.job = None
        self.progress = 0
        self.label = tk.Label(self, text=title+'  ⌄', bg=BG, fg=MUTED,
                              font=('Microsoft YaHei UI', 10), cursor='hand2', anchor='w', pady=10, takefocus=1)
        self.label.pack(fill='x')
        self.label.bind('<Button-1>', lambda _: self.set_open(not self.opened))
        self.label.bind('<Return>', lambda _: self.set_open(not self.opened))
        self.clip = ttk.Frame(self, height=0)
        self.clip.pack_propagate(False)
        self.body = ttk.Frame(self.clip)
        self.body.pack(fill='both', expand=True)
        self.bind('<Destroy>', self._cleanup, add='+')

    def set_open(self, value):
        if self.opened == value:
            return
        self.opened = value
        if self.job is not None:
            self.after_cancel(self.job)
        self.clip.pack(fill='x')
        start, end = self.progress, self.target_height if value else 0
        started = time.monotonic()
        def frame():
            progress = min(1, (time.monotonic()-started)/.18)
            self.progress = round(start+(end-start)*(1-(1-progress)**3))
            self.clip.configure(height=self.progress)
            if progress < 1:
                self.job = self.after(16, frame)
            else:
                self.job = None
                if not value:
                    self.clip.pack_forget()
        frame()

    def _cleanup(self, event):
        if event.widget is self and self.job is not None:
            self.after_cancel(self.job)
            self.job = None


class ScrollableForm(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.pack(fill='both', expand=True)
        canvas = tk.Canvas(self, bg=BG, highlightthickness=0, yscrollincrement=22)
        self.canvas = canvas
        self.scrollbar = OverlayScrollbar(canvas)
        canvas.pack(side='left', fill='both', expand=True)
        self.body = ttk.Frame(canvas)
        window = canvas.create_window(0, 0, anchor='nw', window=self.body)
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window, width=event.width))
        self.body.bind('<Configure>', lambda _: canvas.configure(scrollregion=canvas.bbox('all')))
        self.root = self.winfo_toplevel()
        self.wheel_bindings = []
        for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>', '<Next>', '<Prior>'):
            ident = self.root.bind(sequence, self._scroll, add='+')
            self.wheel_bindings.append((sequence, ident))
        self.bind('<Destroy>', self._cleanup, add='+')

    def _scroll(self, event):
        widget = event.widget
        while widget is not None and widget is not self.canvas:
            # Inner text/record widgets keep their own native wheel behavior.
            if isinstance(widget, (tk.Text, ttk.Treeview, ttk.Combobox, ttk.Spinbox)):
                return
            widget = getattr(widget, 'master', None)
        if widget is not self.canvas or not self.scrollbar.scrollable:
            return
        if getattr(event, 'keysym', '') in ('Next', 'Prior'):
            self.canvas.yview_scroll(1 if event.keysym == 'Next' else -1, 'pages')
            self.scrollbar.activity()
            return 'break'
        return self.scrollbar.scroll_wheel(event)

    def _cleanup(self, event):
        if event.widget is self:
            for sequence, ident in self.wheel_bindings:
                self.root.unbind(sequence, ident)
