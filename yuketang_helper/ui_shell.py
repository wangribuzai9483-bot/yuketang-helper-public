"""Warm, responsive Tk shell. Navigation changes views, never task state."""

import tkinter as tk
import time
from tkinter import ttk
from pathlib import Path
from PIL import Image, ImageTk


BG = "#FFFFFF"
SURFACE = "#FFFFFF"
SOFT = "#F1F0EC"
LINE = "#E9E7E2"
INK = "#252525"
MUTED = "#777570"
ASSETS = Path(__file__).with_name("assets") / "ui-v2"


def style_native_caption(root):
    """Tint the system caption while retaining its resize, snap and taskbar behavior."""
    import sys
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        from ctypes import wintypes
        ancestor = ctypes.windll.user32.GetAncestor
        ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
        ancestor.restype = wintypes.HWND
        hwnd = ancestor(root.winfo_id(), 2)
        setter = ctypes.windll.dwmapi.DwmSetWindowAttribute
        setter.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        results = []
        for attribute, rgb in ((35, 0xF7F9FA), (36, 0x252525), (34, 0xE2E7E9)):
            value = wintypes.DWORD(rgb)
            results.append(setter(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)))
        return all(result == 0 for result in results)
    except (OSError, AttributeError, tk.TclError):
        return False


def rounded_rect(canvas, x1, y1, x2, y2, radius, **options):
    radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
              x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
              x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
    return canvas.create_polygon(points, smooth=True, splinesteps=18, **options)


class DocumentMark(tk.Canvas):
    def __init__(self, parent, variant=None):
        super().__init__(parent, width=132, height=132, bg=BG, highlightthickness=0)
        if variant:
            variants = ['course', 'video', 'model', 'logs', 'learning', 'exam']
            cell = variants.index(variant)
            with Image.open(ASSETS.parent / 'ui-v4' / 'emblems.png') as source:
                width, height = source.width//3, source.height//2
                x, y = cell%3 * width, cell//3 * height
                self.source = source.crop((x, y, x+width, y+height))
        else:
            with Image.open(ASSETS / "document.png") as source:
                self.source = source.copy()
        self.size = None
        self.animation_job = None
        self.active = False
        self.angle = 0
        self.set_size(132)

    def set_size(self, size):
        if self.size == size:
            return
        self.size = size
        self.configure(width=size, height=size)
        self.image = ImageTk.PhotoImage(self.source.resize((size, size), Image.Resampling.LANCZOS), master=self)
        self.delete("all")
        self.create_image(size / 2, size / 2, image=self.image)

    def set_active(self, active):
        if self.active == active:
            return
        self.active = active
        if self.animation_job is not None:
            self.after_cancel(self.animation_job)
            self.animation_job = None
        self.delete("activity")
        if active:
            self._tick()

    def _tick(self):
        self.delete("activity")
        self.angle = (self.angle + 12) % 360
        self.create_arc(self.size - 27, 6, self.size - 5, 28, start=self.angle, extent=100,
                        style="arc", outline="#D9AF66", width=2, tags="activity")
        self.animation_job = self.after(100, self._tick)


def redraw_widget_tree(widget):
    """Invalidate Tk's complete visible regions once, without synchronous Win32 paints."""
    def expose(child):
        if child.winfo_ismapped():
            if isinstance(child, Backdrop):
                child.refresh()
            descendants = child.winfo_children()
            # Frame backgrounds and the workspace image already invalidate
            # themselves through geometry/item changes. Only repaint the
            # moving foreground surfaces that can retain copied fragments.
            if not isinstance(child, (tk.Frame, ttk.Frame, tk.Tk, tk.Toplevel)) and not hasattr(child, 'background_scaled'):
                child.event_generate('<Expose>', x=0, y=0,
                                     width=child.winfo_width(), height=child.winfo_height())
            for descendant in descendants:
                expose(descendant)
    expose(widget)


class Backdrop(tk.Canvas):
    """Render the aligned workspace asset behind child widgets without a solid panel."""
    def __init__(self, parent, workspace):
        super().__init__(parent, bg=BG, highlightthickness=0, height=1)
        self.workspace = workspace
        self.redraw_job = None
        self.image_item = self.create_image(0, 0, anchor='nw')
        self.image_signature = None
        self.bind("<Configure>", lambda _: self.refresh())
        self.bind('<Destroy>', self._cleanup_redraw, add='+')

    def refresh(self):
        source = getattr(self.workspace, "background_scaled", None)
        if source is None or self.winfo_width() < 2:
            return
        x = self.winfo_rootx() - self.workspace.winfo_rootx()
        y = self.winfo_rooty() - self.workspace.winfo_rooty()
        # All nested containers share the workspace's one Tk image. Canvas
        # clipping handles the crop; uncovered pixels use its normal BG color.
        if getattr(self.workspace, '_backdrop_source', None) is not source:
            self.workspace.background_photo = ImageTk.PhotoImage(source, master=self.workspace)
            self.workspace._backdrop_source = source
        self.photo = self.workspace.background_photo
        offset_x, offset_y = getattr(self.workspace, 'background_offset', (0, 0))
        signature = (str(self.photo), offset_x-x, offset_y-y)
        if signature == self.image_signature:
            return
        self.image_signature = signature
        self.coords(self.image_item, offset_x-x, offset_y-y)
        self.itemconfigure(self.image_item, image=self.photo)
        self.tag_lower(self.image_item)

    def _redraw_children(self):
        self.redraw_job = None
        # Moving nested Canvas windows can copy stale child pixels on Windows.
        # Invalidate the complete subtree after geometry and image updates;
        # repaint the actual labels/buttons instead of keeping copied fragments.
        redraw_widget_tree(self)

    def _cleanup_redraw(self, event):
        if event.widget is self and self.redraw_job is not None:
            self.after_cancel(self.redraw_job)
            self.redraw_job = None


class FlowConnector(tk.Canvas):
    def __init__(self, parent, active=False, completed=False):
        super().__init__(parent, width=48, height=35, bg=BG, highlightthickness=0)
        self.job = None
        self.phase = 0
        self.create_line(0, 18, 48, 18, fill='#D8B77D' if completed else LINE, width=2)
        if active:
            self.create_line(0, 18, 48, 18, fill='#D8B77D', width=2, dash=(9, 14), tags='pulse')
            self._tick()

    def _tick(self):
        self.phase = (self.phase - 2) % 23
        self.itemconfigure('pulse', dashoffset=self.phase)
        self.job = self.after(35, self._tick)

    def destroy(self):
        if self.job is not None:
            self.after_cancel(self.job)
            self.job = None
        super().destroy()


class FlowSteps(ttk.Frame):
    """Small event-driven overview; only observed steps receive a completion mark."""
    def __init__(self, parent):
        super().__init__(parent)
        self.observed = set()
        self.use_model = False
        self.last_signature = None
        self.current = None

    def update_state(self, state, embedded=False):
        mapping = {"opening": "获取题目", "waiting": "获取题目", "recognizing": "识别解析",
                   "ready": "识别解析", "thinking": "填写提交" if embedded else "模型求解",
                   "filled": "填写提交", "submitting": "填写提交", "submitted": "填写提交"}
        if state == "switching":
            self.observed.clear()
            self.use_model = False
        if state == "thinking" and not embedded:
            self.use_model = True
        current = mapping.get(state)
        if state in {'skipped', 'complete'}:
            current = '填写提交'
        elif state == 'switching':
            current = '获取题目'
        elif state in {'stopped', 'error', 'blocked', 'verification', 'unsupported'}:
            current = self.current
        self.current = current
        busy = state in {'opening', 'recognizing', 'thinking', 'submitting', 'switching', 'resumed'}
        steps = ["获取题目", "识别解析"] + (["模型求解"] if self.use_model else []) + ["填写提交"]
        signature = (current, tuple(steps), frozenset(self.observed), busy)
        if signature == self.last_signature:
            return
        self.last_signature = signature
        for widget in self.winfo_children():
            widget.destroy()
        for index, name in enumerate(steps):
            if index:
                active_index = max(1, steps.index(current)) if current in steps else -1
                connector = FlowConnector(self, active=busy and index == active_index,
                                          completed=steps[index] in self.observed and steps[index-1] in self.observed)
                connector.pack(side="left", anchor="n")
            color = "#C49A53" if name == current else MUTED
            mark = "●" if name == current else "✓" if name in self.observed else "○"
            cell = ttk.Frame(self)
            cell.pack(side="left", padx=12)
            ttk.Label(cell, text=mark, foreground=color, font=("Segoe UI", 17)).pack(pady=(0, 7))
            ttk.Label(cell, text=name, foreground=INK if name == current else MUTED).pack()
        if current:
            self.observed.add(current)

    def stop(self):
        for widget in self.winfo_children():
            if isinstance(widget, FlowConnector) and widget.job is not None:
                widget.after_cancel(widget.job)
                widget.job = None


class InputCard(tk.Canvas):
    def __init__(self, parent, textvariable, placeholder='粘贴雨课堂作业链接…'):
        super().__init__(parent, height=80, bg=BG, highlightthickness=0)
        self.value = textvariable
        self.hint_text = placeholder
        self.display = tk.StringVar()
        self.syncing = False
        self.placeholder_active = False
        self.entry = tk.Entry(self, textvariable=self.display, relief="flat", bd=0,
                              highlightthickness=0, bg=SURFACE, fg=INK,
                              insertbackground=INK, font=("Microsoft YaHei UI", 11))
        self.entry_window = self.create_window(62, 40, window=self.entry, anchor="w")
        self.utilities = []
        for symbol, action in (("\ue77f", self.paste), ("\ue711", self.clear)):
            button = tk.Label(self, text=symbol, bg=SURFACE, fg=MUTED,
                              font=("Segoe MDL2 Assets", 13), cursor="hand2", takefocus=1)
            button.bind("<Button-1>", lambda _, fn=action: fn())
            button.bind("<Return>", lambda _, fn=action: fn())
            self.utilities.append(self.create_window(0, 39, window=button, width=30, height=30))
        self.value.trace_add("write", lambda *_: self._sync_from_value())
        self.display.trace_add("write", lambda *_: self._sync_to_value())
        self.bind("<Configure>", self._redraw)
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        self._sync_from_value()

    def paste(self):
        try:
            self.value.set(self.clipboard_get().strip())
            self.entry.focus_set()
        except tk.TclError:
            pass

    def clear(self):
        self.value.set("")
        self.entry.focus_set()

    def _sync_from_value(self):
        if self.syncing:
            return
        self.syncing = True
        value = self.value.get()
        if value:
            self.placeholder_active = False
            self.display.set(value)
            self.entry.configure(fg=INK)
        elif self.entry.focus_get() is self.entry:
            self.placeholder_active = False
            self.display.set("")
            self.entry.configure(fg=INK)
        else:
            self.placeholder_active = True
            self.display.set(self.hint_text)
            self.entry.configure(fg=MUTED)
        self.syncing = False

    def _sync_to_value(self):
        if not self.syncing and not self.placeholder_active:
            self.syncing = True
            self.value.set(self.display.get())
            self.syncing = False

    def _focus_in(self, _event):
        if self.placeholder_active:
            self.placeholder_active = False
            self.display.set("")
        self.entry.configure(fg=INK)
        self._redraw()

    def _focus_out(self, _event):
        if not self.display.get():
            self._sync_from_value()
        self._redraw()

    def _redraw(self, _event=None):
        width = max(self.winfo_width(), 300)
        self.delete("surface")
        rounded_rect(self, 3, 5, width - 3, 78, 23, fill="#F0EEEA", outline="", tags="surface")
        rounded_rect(self, 1, 1, width - 5, 74, 23, fill=SURFACE,
                     outline="#D7D5D0" if self.entry.focus_get() is self.entry else LINE,
                     width=1, tags="surface")
        self.create_text(32, 38, text="\ue71b", fill=MUTED, font=("Segoe MDL2 Assets", 18), tags="surface")
        self.tag_lower("surface")
        self.itemconfigure(self.entry_window, width=max(width - 160, 100))
        for item, offset in zip(self.utilities, (78, 38)):
            self.coords(item, width - offset, 39)


class PrimaryButton(tk.Canvas):
    def __init__(self, parent, text, command):
        super().__init__(parent, width=278, height=62, bg=BG, highlightthickness=0,
                         cursor="hand2", takefocus=1)
        self.text = text
        self.command = command
        self.hover = False
        self._draw()
        self.bind("<Enter>", lambda _: self._set_hover(True))
        self.bind("<Leave>", lambda _: self._set_hover(False))
        self.bind("<Button-1>", lambda _: self.invoke())
        self.bind("<Return>", lambda _: self.invoke())
        self.bind("<space>", lambda _: self.invoke())

    def invoke(self):
        if self.cget('state') != 'disabled':
            return self.command()

    def _set_hover(self, value):
        self.hover = value
        self._draw()

    def _draw(self):
        self.delete("all")
        rounded_rect(self, 3, 4, 275, 61, 23, fill="#ECEAE6", outline="")
        rounded_rect(self, 1, 1, 273, 57, 23,
                     fill="#3B3B3B" if self.hover else INK, outline="")
        self.create_text(48, 29, text="▶", fill=SURFACE, font=("Segoe UI Symbol", 13))
        self.create_line(75, 18, 75, 40, fill="#737373", width=1)
        self.create_text(174, 29, text=self.text, fill=SURFACE,
                         font=("Microsoft YaHei UI", 11, "bold"))


class ActionButton(tk.Canvas):
    """A keyboard-accessible compact rounded action, with live GUI text."""
    def __init__(self, parent, text, command, primary=False, width=150):
        super().__init__(parent, width=width, height=44, bg=BG, highlightthickness=0,
                         takefocus=1, cursor='hand2')
        self.text, self.command, self.primary = text, command, primary
        self.hover = False
        self.bind('<Configure>', lambda _: self._draw())
        self.bind('<Enter>', lambda _: self._hover(True))
        self.bind('<Leave>', lambda _: self._hover(False))
        self.bind('<Button-1>', lambda _: self.invoke())
        self.bind('<Return>', lambda _: self.invoke())
        self.bind('<space>', lambda _: self.invoke())
        self.bind('<FocusIn>', lambda _: self._draw())
        self.bind('<FocusOut>', lambda _: self._draw())

    def invoke(self):
        return self.command()

    def _hover(self, active):
        self.hover = active
        self._draw()

    def _draw(self):
        self.delete('all')
        width = max(self.winfo_width(), 30)
        color = INK if self.primary else SOFT if self.hover else SURFACE
        rounded_rect(self, 1, 1, width-1, 43, 11, fill=color,
                     outline=INK if self.focus_get() is self else LINE, width=1)
        self.create_text(width/2, 22, text=self.text, fill=SURFACE if self.primary else INK,
                         font=('Microsoft YaHei UI', 10, 'bold' if self.primary else 'normal'))


class PlaybackProgress(tk.Canvas):
    """Display backend progress with a rounded track; no seeking or synthetic progress."""
    def __init__(self, parent):
        super().__init__(parent, height=22, bg='#FCFBFA', highlightthickness=0)
        self.value = 0.0
        self.bind('<Configure>', lambda _: self._draw())

    def __setitem__(self, key, value):
        if key == 'value':
            self.value = min(100.0, max(0.0, float(value)))
            self._draw()
        else:
            super().__setitem__(key, value)

    def __getitem__(self, key):
        return self.value if key == 'value' else super().__getitem__(key)

    def _draw(self):
        self.delete('all')
        width = self.winfo_width()
        if width < 12:
            return
        rounded_rect(self, 4, 8, width-4, 14, 3, fill='#E9E6E0', outline='')
        end = 4+(width-8)*self.value/100
        if self.value > 0:
            rounded_rect(self, 4, 8, max(5,end), 14, 3, fill='#353431', outline='')
            self.create_oval(end-4, 7, end+4, 15, fill='#353431', outline='#FCFBFA', width=1)


def apply_theme(root, style):
    root.configure(background=BG)
    style.theme_use("clam")
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=INK, font=("Microsoft YaHei UI", 10))
    style.configure("TButton", padding=(13, 8), font=("Microsoft YaHei UI", 10),
                    background=SOFT, foreground=INK, borderwidth=0, relief="flat")
    style.map("TButton", background=[("active", "#E5E3DE")])
    style.configure("TEntry", fieldbackground=SURFACE, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, relief="flat", padding=7)
    style.configure("Polished.TCombobox", font=("Microsoft YaHei UI", 10),
                    foreground=INK, fieldbackground=SURFACE, background=SURFACE,
                    arrowcolor=INK, arrowsize='12p', padding=(10, 7, 8, 7),
                    borderwidth=1, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                    relief="flat")
    style.map("Polished.TCombobox",
              foreground=[("disabled", MUTED), ("readonly", INK)],
              fieldbackground=[("readonly", SURFACE), ("disabled", SOFT)],
              background=[("active", SOFT), ("readonly", SURFACE)],
              arrowcolor=[("disabled", MUTED), ("active", INK)],
              bordercolor=[("focus", "#8F8A80"), ("active", "#C9C4BB")],
              lightcolor=[("focus", "#8F8A80"), ("active", "#C9C4BB")],
              darkcolor=[("focus", "#8F8A80"), ("active", "#C9C4BB")])
    root.option_add("*TCombobox*Listbox.background", SURFACE)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", SOFT)
    root.option_add("*TCombobox*Listbox.selectForeground", INK)
    root.option_add("*TCombobox*Listbox.font", "{Microsoft YaHei UI} 10")
    style.configure("TCheckbutton", background=BG, foreground=INK)
    style.configure('Workbench.TMenubutton', background=BG, foreground=INK, padding=(12, 9),
                    bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, relief='flat')
    style.configure("TRadiobutton", background=BG, foreground=INK)
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(15, 9))
    style.map('TNotebook.Tab', background=[('selected', SOFT)], foreground=[('selected', INK)])
    style.configure('Learning.TNotebook', background=BG, borderwidth=0, relief='flat')
    style.configure('Learning.TNotebook.Tab', font=('Microsoft YaHei UI', 10),
                    padding=(18, 10), background=SOFT, foreground=MUTED,
                    borderwidth=1, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
    style.map('Learning.TNotebook.Tab',
              background=[('selected', SURFACE), ('active', '#F8F7F4')],
              foreground=[('selected', INK), ('active', INK)],
              bordercolor=[('selected', LINE), ('active', '#C9C4BB')],
              lightcolor=[('selected', LINE)], darkcolor=[('selected', LINE)])
    style.configure('Treeview', rowheight=32, background='#FCFBFA', fieldbackground='#FCFBFA',
                    foreground=INK, borderwidth=0, font=('Microsoft YaHei UI', 10))
    style.configure('Treeview.Heading', background=SOFT, foreground=INK, padding=(10, 8),
                    font=('Microsoft YaHei UI', 10, 'bold'), relief='flat')
    style.map('Treeview', background=[('selected', '#EAE5DB')], foreground=[('selected', INK)])
    style.configure('Learning.Treeview', background=SURFACE, fieldbackground=SURFACE,
                    foreground=INK, borderwidth=0, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, rowheight=34,
                    font=('Microsoft YaHei UI', 10))
    style.configure('Learning.Treeview.Heading', background=SOFT, foreground=INK,
                    borderwidth=0, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                    relief='flat', padding=(12, 10), font=('Microsoft YaHei UI', 10, 'bold'))
    style.map('Learning.Treeview', background=[('selected', '#EAE5DB')],
              foreground=[('selected', INK)])
    style.configure('Learning.Horizontal.TScrollbar', background='#D4D0C8',
                    troughcolor=SOFT, bordercolor=LINE, lightcolor=LINE,
                    darkcolor=LINE, arrowcolor=MUTED, arrowsize=9, width=10)
    style.configure("Title.TLabel", font=("Microsoft YaHei UI", 19, "bold"))
    style.configure("Status.TLabel", font=("Microsoft YaHei UI", 12, "bold"))
    style.configure('Horizontal.TProgressbar', troughcolor=SOFT, background='#C4A46A',
                    lightcolor='#C4A46A', darkcolor='#C4A46A', borderwidth=0, thickness=5)
    style.configure("Hero.TLabel", background=BG, foreground=INK,
                    font=("Microsoft YaHei UI", 31, "bold"))
    style.configure("HeroHint.TLabel", background=BG, foreground=MUTED,
                    font=("Microsoft YaHei UI", 11))
    style.configure("Primary.TButton", background=INK, foreground=SURFACE,
                    font=("Microsoft YaHei UI", 12, "bold"), padding=(28, 13), borderwidth=0)
    style.map("Primary.TButton", background=[("active", "#444444")], foreground=[("active", SURFACE)])
    # Only the outer navigation notebook loses its tabs. Nested existing tabs remain.
    style.layout("Shell.TNotebook.Tab", [])
    style.layout("Shell.TNotebook", [("Notebook.client", {"sticky": "nswe"})])
    style.configure("Shell.TNotebook", background=BG, borderwidth=0)


class PageStack(tk.Frame):
    """Borderless page switcher with the small Notebook API used by the UI."""

    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.active_page = None
        self.pages = []
        self.transition_job = None
        self.transition_generation = 0
        self.bind('<Destroy>', self._cleanup_transition, add='+')

    def add(self, page, text=None):
        self.pages.append(page)
        if self.active_page is None:
            self.select(page)

    def select(self, page=None):
        if page is None:
            return str(self.active_page)
        if page is self.active_page:
            return
        previous = self.active_page
        self.active_page = page
        self.transition_generation += 1
        generation = self.transition_generation
        if self.transition_job is not None:
            self.after_cancel(self.transition_job)
            self.transition_job = None
        page.place(x=0, y=18 if previous is not None else 0, relwidth=1, relheight=1)
        self.tk.call('raise', page._w)
        self.event_generate("<<NotebookTabChanged>>")
        started = time.monotonic()
        def frame():
            if generation != self.transition_generation:
                return
            progress = min(1, (time.monotonic()-started)/.18)
            offset = round(18 * (1-progress) ** 3)
            page.place_configure(y=offset)
            if progress < 1:
                self.transition_job = self.after(16, frame)
            else:
                self.transition_job = None
                for other in self.pages:
                    if other is not page:
                        other.place_forget()
        if previous is not None:
            frame()

    def _cleanup_transition(self, event):
        if event.widget is self and self.transition_job is not None:
            self.after_cancel(self.transition_job)
            self.transition_job = None


class Sidebar(tk.Frame):
    """Fixed-height items keep their neighbors still during hover animation."""

    ITEMS = (
        ("course", "◇", "课程流程", "视频与作业统一调度"),
        ("homework", "▤", "作业填写", "自动识别并填写"),
        ("video", "▷", "视频播放", "观看与续播"),
        ("model", "⬡", "模型设置", "连接与配置"),
        ("logs", "☰", "运行日志", "查看运行记录"),
        ("learning", "▦", "记录与经验", "查看学习记录"),
        ("exam", "□", "期末考试", "独立填写入口"),
    )

    def __init__(self, parent, on_select):
        super().__init__(parent, bg=SURFACE, highlightbackground=LINE, highlightthickness=1)
        self.on_select = on_select
        self.manual_collapsed = False
        self.auto_collapsed = False
        self.selected = "homework"
        self.entries = {}
        self.status_dots = {}
        self.animation_generation = {}
        self.hover_targets = {}
        self.hover_progress = {}
        self.tip = None
        self.tip_after = None
        self.width_job = None
        self.width_target = 210
        self.bind('<Destroy>', self._cleanup_width, add='+')
        self.configure(width=210)
        self.pack_propagate(False)
        head = tk.Frame(self, bg=SURFACE, height=66)
        head.pack(fill="x")
        head.pack_propagate(False)
        self.toggle = tk.Label(head, text="≡", bg=SURFACE, fg=INK, font=("Segoe UI", 18), cursor="hand2")
        self.toggle.pack(side="left", padx=25)
        self.toggle.bind("<Button-1>", lambda _: self.set_manual(not self.manual_collapsed))
        self.brand = tk.Label(head, text="雨课堂", bg=SURFACE, fg=INK,
                              font=("Microsoft YaHei UI", 11, "bold"))
        self.brand.pack(side="left")
        for key, icon, title, subtitle in self.ITEMS:
            if key == "learning":
                tk.Frame(self, bg=LINE, height=1).pack(fill="x", padx=22, pady=14)
            item = tk.Canvas(self, bg=SURFACE, height=62, highlightthickness=0, cursor="hand2")
            item.pack(fill="x", padx=10, pady=2)
            item.bind("<Configure>", lambda _, k=key: self._paint_item(k))
            symbols = {"homework": "\ue8a5", "video": "\ue768", "model": "\ue7b8", "logs": "\ue9d9",
                       "course": "\ue8f1", "learning": "\ue81c", "exam": "\ue7c3"}
            glyph = tk.Label(item, text=symbols[key], bg=SURFACE, fg=INK,
                             font=("Segoe MDL2 Assets", 19))
            glyph.place(x=16, y=13, width=31, height=35)
            name = tk.Label(item, text=title, bg=SURFACE, fg=INK, anchor="w",
                            font=("Microsoft YaHei UI", 10))
            name.place(x=57, y=19, width=135, height=25)
            hint = tk.Label(item, text=subtitle, bg=SURFACE, fg=MUTED, anchor="w",
                            font=("Microsoft YaHei UI", 8))
            hint.place(x=57, y=38, width=140, height=18)
            hint.place_forget()
            self.entries[key] = (item, glyph, name, hint)
            dot = tk.Label(item, text="●", bg=SURFACE, fg="#38A565", font=("Segoe UI", 7))
            self.status_dots[key] = dot
            for widget in (item, glyph, name, hint, dot):
                widget.bind("<Button-1>", lambda _, k=key: self.on_select(k))
                widget.bind("<Enter>", lambda _, k=key: self._hover(k, True))
                widget.bind("<Leave>", lambda _, k=key: self.after(1, lambda: self._leave(k)))
        self.mark("homework")

    @property
    def collapsed(self):
        return self.manual_collapsed or self.auto_collapsed

    def set_manual(self, value):
        self.manual_collapsed = value
        self._layout()

    def set_auto(self, value):
        if self.auto_collapsed != value:
            self.auto_collapsed = value
            self._layout()

    def _layout(self):
        self._clear_tip()
        self.hover_targets.clear()
        self.hover_progress.clear()
        for key in self.animation_generation:
            self.animation_generation[key] += 1
        collapsed = self.collapsed
        self._animate_width(76 if collapsed else 210)
        if collapsed:
            self.brand.pack_forget()
        else:
            self.brand.pack(side="left")
        for item, glyph, name, hint in self.entries.values():
            hint.place_forget()
            name.place_forget() if collapsed else name.place(x=57, y=19, width=135, height=25)
        self.mark(self.selected)

    def _animate_width(self, target):
        if target == self.width_target:
            return
        self.width_target = target
        if self.width_job is not None:
            self.after_cancel(self.width_job)
            self.width_job = None
        start = int(self.cget('width'))
        if not self.winfo_ismapped():
            self.configure(width=target)
            return
        started = time.monotonic()

        def frame():
            frame_started = time.monotonic()
            progress = min(1, (time.monotonic()-started)/.26)
            eased = progress*progress*(3-2*progress)
            self.configure(width=round(start+(target-start)*eased))
            # Complete this frame's geometry, background and child invalidation
            # before scheduling the next width. Timer callbacks must not race
            # ahead of Tk's idle geometry/painting work on a busy Windows UI.
            self.update_idletasks()
            redraw_widget_tree(self.winfo_toplevel())
            self.update_idletasks()
            if progress < 1:
                delay = max(1, round(16-(time.monotonic()-frame_started)*1000))
                self.width_job = self.after(delay, frame)
            else:
                self.width_job = None
        frame()

    def _cleanup_width(self, event):
        if event.widget is self:
            if self.width_job is not None:
                self.after_cancel(self.width_job)
                self.width_job = None

    def mark(self, key):
        self.selected = key
        for item_key, (item, glyph, name, hint) in self.entries.items():
            self._paint_item(item_key)
            name.configure(font=("Microsoft YaHei UI", 10, "bold" if item_key == key else "normal"))

    def _item_color(self, key):
        if self.hover_targets.get(key, False):
            return '#E7E5DF'
        return SOFT if key == self.selected else SURFACE

    def _paint_item(self, key):
        item, glyph, name, hint = self.entries[key]
        color = self._item_color(key)
        item.delete("tile")
        for widget in (glyph, name, hint, self.status_dots[key]):
            widget.configure(bg=color)
        if color != SURFACE:
            rounded_rect(item, 1, 1, max(item.winfo_width() - 1, 188), 61, 12,
                         fill=color, outline="", tags="tile")
            item.tag_lower("tile")

    def set_running(self, key, active):
        dot = self.status_dots[key]
        if active:
            dot.place(x=44, y=7, width=12, height=12)
        else:
            dot.place_forget()

    def _hover(self, key, active):
        if self.hover_targets.get(key, False) == active:
            return
        self.hover_targets[key] = active
        self._paint_item(key)
        if self.collapsed:
            self._clear_tip()
            if active:
                self.tip_after = self.after(450, lambda: self._show_tip(key))
            return
        item, glyph, name, hint = self.entries[key]
        self.animation_generation[key] = self.animation_generation.get(key, 0) + 1
        generation = self.animation_generation[key]
        start = self.hover_progress.get(key, 0.0)
        end = 1.0 if active else 0.0
        if active:
            hint.place(x=57, y=34, width=140, height=18)
        def frame(step):
            if self.animation_generation.get(key) != generation or self.collapsed:
                return
            fraction = step / 9
            eased = 1 - (1 - fraction) ** 3
            opacity = start + (end - start) * eased
            self.hover_progress[key] = opacity
            name.place_configure(y=round(19 - 9 * opacity))
            name.configure(font=("Microsoft YaHei UI", 9 if opacity >= .5 else 10,
                                 "bold" if key == self.selected else "normal"))
            base = self._item_color(key)
            blend = tuple(round(int(base[n:n+2], 16) * (1-opacity) + int(MUTED[n:n+2], 16) * opacity) for n in (1, 3, 5))
            hint.configure(foreground="#%02x%02x%02x" % blend)
            if step < 9:
                self.after(15, lambda: frame(step + 1))
            elif not active:
                hint.place_forget()
        frame(0)

    def _leave(self, key):
        item = self.entries[key][0]
        x, y = item.winfo_pointerxy()
        widget = item.winfo_containing(x, y)
        if widget not in (*self.entries[key], self.status_dots[key]):
            self._hover(key, False)

    def _show_tip(self, key):
        if not self.collapsed:
            return
        self.tip = tk.Toplevel(self)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        x, y = self.entries[key][0].winfo_pointerxy()
        self.tip.geometry(f"+{x + 14}+{y + 12}")
        tk.Label(self.tip, text=self.entries[key][2].cget("text"), bg=INK, fg=SURFACE,
                 font=("Microsoft YaHei UI", 9), padx=10, pady=6).pack()

    def _clear_tip(self):
        if self.tip_after is not None:
            self.after_cancel(self.tip_after)
            self.tip_after = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None
