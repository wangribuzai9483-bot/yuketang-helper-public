from dataclasses import replace
from datetime import datetime
import json
import queue
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from .config import Config, RUNTIME
from .engine import Engine, Event
from .homework_gui import HomeworkPanel
from .learning import LearningService
from .learning_gui import LearningPanel
from .exam_gui import ExamPanel
from .application_pages import build_video, build_course, build_logs, set_video_active
from .ui_shell import ASSETS, PageStack, Sidebar, apply_theme, style_native_caption
from .window_chrome import install_caption
from PIL import Image, ImageTk

STATE_LABELS = {
    "waiting": "等待登录 / 打开课程", "navigating": "正在进入课程", "playing": "正在播放", "paused": "视频暂停",
    "buffering": "正在缓冲", "ended": "本节播放结束", "blocked": "弹窗待处理",
    "stalled": "播放中断", "error": "视频出错", "speed-rejected": "倍速设置受阻",
    "autoplay-blocked": "需要点击播放", "disabled": "自动播放已暂停",
}
ANSWER_SOURCE_LABELS = {"auto": "自动（自带答案优先）", "model": "仅用模型", "embedded": "仅用题目答案"}
ANSWER_SOURCE_VALUES = {label: key for key, label in ANSWER_SOURCE_LABELS.items()}


def clock(seconds):
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("雨课堂 · 学习助手")
        self.root.geometry(self._saved_geometry())
        self.root.minsize(760, 760)
        self.engine = None
        self.events = queue.Queue()
        self.learning = LearningService(notify=lambda data:self.events.put(Event('learning',data)))
        self.closing = False
        self.starting = False
        self.verification_notice = None
        self.verification_notice_status = tk.StringVar()
        self.restore_request = 0
        self.restore_pending = False
        self.restore_native_done = False
        self.last_restore_click = -float('inf')
        load_error = ""
        try:
            self.config = Config.load()
        except (ValueError, OSError) as exc:
            self.config = Config()
            load_error = f"配置无法读取，已使用默认值：{exc}"
        self.vars = {}
        self.style = ttk.Style()
        apply_theme(self.root, self.style)
        self._build()
        style_native_caption(self.root)
        self.root.bind('<Map>', lambda event: style_native_caption(self.root) if event.widget is self.root else None, add='+')
        self.root.bind("<Configure>", self._resize_navigation)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.window_chrome = install_caption(self.root, self.close)
        self.root.bind('<Destroy>', self._cancel_ui_callbacks, add='+')
        self.root.after(150, self.poll)
        self.log("点击“启动并登录”，在学校门户自行登录；已填写课程直达地址时，登录后自动进入课程。")
        if load_error:
            self.log(load_error)

    def _build(self):
        with Image.open(ASSETS / "document.png") as source:
            self.brand_image = ImageTk.PhotoImage(source.resize((32, 32), Image.Resampling.LANCZOS), master=self.root)
        self.root.iconphoto(True, self.brand_image)
        shell = ttk.Frame(self.root)
        shell.pack(fill="both", expand=True)
        self.sidebar = Sidebar(shell, self.show_page)
        self.sidebar.pack(side="left", fill="y")
        tabs = PageStack(shell)
        self.tabs = tabs
        tabs.pack(side="left", fill="both", expand=True)
        outer = ttk.Frame(tabs)
        homework = ttk.Frame(tabs)
        course = ttk.Frame(tabs)
        learning = ttk.Frame(tabs)
        exam = ttk.Frame(tabs)
        logs_page = ttk.Frame(tabs)
        tabs.add(course, text="课程流程")
        tabs.add(outer, text="视频播放")
        tabs.add(homework, text="作业")
        tabs.add(learning,text='记录与经验')
        tabs.add(exam,text='期末考试')
        tabs.add(logs_page, text="运行日志")
        self.pages = {"course": course, "video": outer, "homework": homework,
                      "model": homework, "learning": learning, "exam": exam, "logs": logs_page}
        self.homework_panel = HomeworkPanel(homework, lambda url: self.start(initial_homework_url=url), self.send, hide_tabs=True)
        self.learning_panel = LearningPanel(learning,self.learning,self.prepare_learning_settings)
        self.exam_panel = ExamPanel(exam,self.homework_panel,lambda url:self.start(initial_exam_url=url),self.send)
        tabs.bind('<<NotebookTabChanged>>',lambda _:self.learning_panel.refresh() if tabs.select()==str(learning) else None)
        build_video(self, outer, ANSWER_SOURCE_LABELS)
        build_course(self, course)
        build_logs(self, logs_page)
        self.show_page("homework")

    def show_page(self, key):
        self.tabs.select(self.pages[key])
        self.sidebar.mark(key)
        if key in {"homework", "model"}:
            self.homework_panel.tabs.select(self.homework_panel.model_tab if key == "model" else self.homework_panel.work_tab)
        elif key == "learning":
            self.learning_panel.refresh()

    def _resize_navigation(self, event):
        if event.widget is self.root:
            self.sidebar.set_auto(event.width < 1000)

    def _cancel_ui_callbacks(self, event):
        if event.widget is self.root:
            for job in self.root.tk.call('after', 'info'):
                self.root.after_cancel(job)

    @staticmethod
    def _saved_geometry():
        try:
            size = json.loads((RUNTIME / "ui-window.json").read_text(encoding="utf-8"))
            width, height = int(size["width"]), int(size["height"])
            if 760 <= width <= 4000 and 760 <= height <= 3000:
                return f"{width}x{height}"
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return "1180x820"

    def _remember_geometry(self):
        if self.root.state() != "normal":
            return
        try:
            RUNTIME.mkdir(parents=True, exist_ok=True)
            path = RUNTIME / "ui-window.json"
            path.write_text(json.dumps({"width": self.root.winfo_width(),
                                        "height": self.root.winfo_height()}), encoding="utf-8")
        except OSError:
            pass

    def read_config(self):
        return replace(self.config,
            login_url=self.vars["login_url"].get().strip(),
            course_name=self.vars["course_name"].get().strip(),
            course_url=self.vars["course_url"].get().strip(),
            browser=self.vars["browser"].get(), speed=float(self.vars["speed"].get()),
            verification_bypass=("solve" if self.vars["bypass"].get() else "off"),
            answer_source=ANSWER_SOURCE_VALUES.get(self.vars["answer_source"].get(), "auto"),
            **{key: self.vars[key].get() for key in ("muted", "auto_next", "auto_minimize", "resume_paused")},
        ).validate()

    def apply(self):
        try:
            updated = self.read_config()
            if self.engine and self.engine.thread.is_alive():
                if any(getattr(updated, key) != getattr(self.config, key) for key in ("browser", "login_url", "course_name", "course_url")):
                    messagebox.showinfo("需要重启", "修改浏览器、入口、目标课程或直达地址后，请先停止，再重新启动。")
                    return False
                self.engine.command("options", updated)
            updated.save()
            self.config = updated
            self.log("设置已保存。")
            return True
        except (ValueError, OSError) as exc:
            messagebox.showerror("设置错误", str(exc))
            return False

    def start(self, initial_homework_url="", initial_course_url="", initial_exam_url=""):
        if self.engine and self.engine.thread.is_alive():
            return self.engine
        if not self.apply():
            return
        self.engine = Engine(self.config, self.events, initial_homework_url=initial_homework_url, initial_course_url=initial_course_url,initial_exam_url=initial_exam_url)
        self.engine.learning = self.learning
        self.start_button.configure(state="disabled")
        self.status.set("正在打开浏览器…")
        self.starting = True
        self.engine.start()
        return self.engine

    def prepare_learning_settings(self):
        return self.homework_panel.settings if self.homework_panel.save(require_model=True) else None

    def start_course(self):
        if not self.homework_panel.save(require_model=True) or not self.apply():
            return
        from .course import CourseProgress
        resume_url = CourseProgress().load_url(self.config)
        engine = self.start(initial_course_url=resume_url)
        if engine:
            engine.command("course_start", self.homework_panel.settings)

    def send(self, command, value=None):
        if command == 'restore':
            self.restore_browser()
            return
        if self.engine and self.engine.thread.is_alive():
            self.engine.command(command, value)
        elif command.startswith("homework_"):
            self.homework_panel.status.set("请先点击“打开作业并登录”。")
        elif command.startswith('exam_'):
            self.exam_panel.status.set('请先点击“打开考试入口”。')

    def restore_browser(self):
        now = time.monotonic()
        if now - self.last_restore_click < 0.5:
            return
        self.last_restore_click = now
        if not self.engine:
            self.verification_notice_status.set('浏览器任务未运行，请重新启动助手任务。')
            self.course_status.set(self.verification_notice_status.get())
            return
        self.restore_request += 1
        request = self.restore_request
        self.restore_pending = True
        self.verification_notice_status.set('正在恢复浏览器并定位当前学习标签页…')
        # User-click path runs independently of the Playwright worker. A busy
        # OCR/browser call cannot prevent restoring its already-verified window.
        try:
            result = self.engine.restore_window_now() if hasattr(self.engine, 'restore_window_now') else {}
        except Exception:
            result = {}
        self.restore_native_done = bool(result.get('foreground'))
        if result.get('restored') and self.verification_notice is not None:
            self.lower_verification_notice()
        if not self.engine.thread.is_alive():
            self.restore_pending = False
            message = '浏览器窗口已恢复，但学习任务已停止；请重新开始任务。' if result.get('restored') else '浏览器任务已停止，未找到可恢复的窗口。'
            self.verification_notice_status.set(message)
            self.course_status.set(message)
            return
        self.engine.command('restore')
        self.root.after(5000, lambda: self.restore_timeout(request))

    def restore_timeout(self, request):
        if self.closing or request != self.restore_request or not self.restore_pending:
            return
        self.restore_pending = False
        message = ('浏览器窗口已恢复，尚未收到标签页定位反馈；可在浏览器手动完成核验。'
                   if self.restore_native_done else '尚未收到窗口恢复的完成反馈；可再次点击恢复，或从任务栏切回浏览器。')
        self.verification_notice_status.set(message)
        self.course_status.set(message)

    def restore_result(self, data):
        if data['action'] != 'restore':
            return
        self.restore_pending = False
        self.verification_notice_status.set(data['message'])
        if data['success'] and self.verification_notice is not None:
            self.lower_verification_notice()
        elif not data['success']:
            self.course_status.set(data['message'])
            if self.verification_notice is not None:
                self.verification_notice.attributes('-topmost', True)
                self.verification_notice.lift()

    def lower_verification_notice(self):
        if self.verification_notice is not None:
            try:
                self.verification_notice.attributes('-topmost', False)
                self.verification_notice.lower()
            except tk.TclError:
                self.verification_notice = None

    def stop(self):
        self.send("stop")
        self.stop_button.configure(state="disabled")
        self.pause_button.configure(state="disabled")
        self.status.set("正在关闭浏览器…")

    def log(self, message):
        line = f"[{datetime.now():%H:%M:%S}] {message}\n"
        views = [self.logs, self.log_view]
        if any(word in str(message) for word in ('错误', '失败', '完成', '停止', '暂停', '恢复', '核验', '登录', '启动', '已进入')):
            views.append(self.important_logs)
        for view in views:
            view.configure(state="normal")
            view.insert("end", line)
            if int(view.index("end-1c").split(".")[0]) > 300:
                view.delete("1.0", "30.0")
            view.see("end")
            view.configure(state="disabled")

    def close_verification_notice(self):
        self.restore_request += 1
        self.restore_pending = False
        if self.verification_notice is not None:
            try:
                self.verification_notice.destroy()
            except tk.TclError:
                pass
            self.verification_notice = None
        self.verification_notice_status.set('')

    def show_verification_notice(self, message):
        if self.closing or self.verification_notice is not None:
            return
        notice = self.verification_notice = tk.Toplevel(self.root)
        notice.title("需要手动完成安全核验")
        notice.attributes('-topmost', True)
        notice.resizable(False, False)
        x = max(0, self.root.winfo_screenwidth()-430)
        notice.geometry(f'410x210+{x}+35')
        content = ttk.Frame(notice, padding=14)
        content.pack(fill='both', expand=True)
        ttk.Label(content, text=message, wraplength=380).pack(anchor='w', pady=(0, 14))
        self.verification_notice_status.set('请手动完成核验，完成后程序会自动继续。')
        ttk.Label(content, textvariable=self.verification_notice_status, wraplength=380).pack(anchor='w', pady=(0, 12))
        controls = ttk.Frame(content)
        controls.pack(fill='x')
        ttk.Button(controls, text='恢复浏览器', command=lambda: self.send('restore')).pack(side='left')
        ttk.Button(controls, text='关闭提示', command=self.close_verification_notice).pack(side='left', padx=8)
        notice.protocol('WM_DELETE_WINDOW', self.close_verification_notice)
        notice.lift()
        self.root.bell()

    def poll(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event.kind == "log":
                self.log(event.data)
            elif event.kind == "status":
                data = event.data
                self.status.set(STATE_LABELS.get(data["state"], data["state"]))
                self.video.set(data.get("title", "") + (f" · {data['detail']}" if data.get("detail") else ""))
                current, duration = data.get("time", 0), data.get("duration", 0)
                self.progress["value"] = current / duration * 100 if duration else 0
                self.progress_text.set(f"{clock(current)} / {clock(duration)}" + (f" · 实际 {data['rate']:g} 倍速" if "rate" in data else ""))
                if self.engine and getattr(self.engine, "course_flow", None) and self.engine.course_flow.active and self.engine.mode == "video":
                    self.course_progress.set(self.progress_text.get())
            elif event.kind == "running":
                self.starting = False
                set_video_active(self, bool(event.data and self.engine and self.engine.mode == 'video'))
                self.sidebar.set_running("video", bool(event.data and self.engine and self.engine.mode == "video"))
                self.start_button.configure(state="disabled" if event.data else "normal")
                for button in (self.pause_button, self.stop_button):
                    button.configure(state="normal" if event.data else "disabled")
                if not event.data:
                    self.close_verification_notice()
                    self.status.set("已停止")
                    self.pause_button.configure(text="暂停自动播放")
            elif event.kind == "paused":
                self.pause_button.configure(text="恢复自动播放" if event.data else "暂停自动播放")
                if event.data:
                    self.status.set("自动播放已暂停")
            elif event.kind == "error":
                self.log(event.data)
                if not self.closing:
                    messagebox.showerror("运行提示", event.data)
            elif event.kind == "window":
                self.log("浏览器已最小化。" if event.data == "minimize" else "浏览器已恢复。")
            elif event.kind == 'window_result':
                self.restore_result(event.data)
                self.log(event.data['message'])
            elif event.kind == 'attention':
                if event.data['state'] == 'verification':
                    self.show_verification_notice(event.data['message'])
                else:
                    self.close_verification_notice()
            elif event.kind == "homework":
                self.homework_panel.render(event.data)
                self.sidebar.set_running("homework", event.data.get("state") not in
                                         {"complete", "stopped", "error", "blocked", "unsupported", "waiting", "ready", "exported"})
                if self.engine and getattr(self.engine, "course_flow", None) and self.engine.course_flow.active:
                    self.course_status.set(event.data["message"])
            elif event.kind == 'learning':
                self.learning_panel.render(event.data)
            elif event.kind == 'exam':
                self.exam_panel.render(event.data)
            elif event.kind == "course":
                self.course_sequence.render(event.data)
                course_state = event.data.get('state')
                headline = {'paused':'课程已暂停', 'complete':'课程已完成', 'stopped':'流程已停止', 'blocked':'等待处理', 'error':'流程遇到问题'}.get(course_state)
                self.course_heading.set(headline or {'video':'视频学习', 'homework':'作业处理'}.get(event.data.get('kind'), '继续你的课程'))
                self.sidebar.set_running("course", event.data.get("state") not in {"idle", "complete", "stopped", "blocked", "error"})
                self.course_status.set(event.data["message"])
                self.course_item.set(event.data.get("title") or "等待进入课程目录")
                if event.data.get("kind") != "video":
                    self.course_progress.set("")
                self.log(event.data["message"])
            elif event.kind == "mode":
                set_video_active(self, event.data == 'video' and bool(self.engine and self.engine.thread.is_alive()))
                self.sidebar.set_running("video", event.data == "video" and bool(self.engine and self.engine.thread.is_alive()))
                self.log('已进入考试填写模式，交卷请在网页手动操作。' if event.data=='exam' else
                         "已进入作业模式，视频自动播放已暂停。" if event.data == "homework" else "已返回视频模式。")
        if self.closing and (not self.engine or not self.engine.thread.is_alive()):
            self.root.destroy()
            return
        self.root.after(150, self.poll)

    def close(self):
        self._remember_geometry()
        self.closing = True
        self.close_verification_notice()
        self.homework_panel.close()
        self.learning.close()
        if self.engine and self.engine.thread.is_alive():
            self.stop()
            self.root.title("正在关闭，请稍候…")
            if self.window_chrome and self.window_chrome.enabled:
                self.window_chrome.title.configure(text=self.root.title())
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
