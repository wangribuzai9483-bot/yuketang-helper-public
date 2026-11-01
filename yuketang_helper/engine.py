"""All Playwright objects stay on one worker thread; Tk uses message queues."""
from dataclasses import dataclass, replace
from pathlib import Path
import queue
import re
import threading
import time
from urllib.parse import urlparse

from .config import Config, RUNTIME, allowed_url
from .bypass import VerificationBypass
from .verification import verification_state
from .windows import Windows
from .browser_process import OwnedBrowserProcess
from .course_identity import course_identity, matches_course

PLAYER_SCRIPT = Path(__file__).with_name("player.js").read_text(encoding="utf-8")
NAVIGATION_SCRIPT = Path(__file__).with_name("navigation.js").read_text(encoding="utf-8")
ATTENTION = {"blocked", "error", "stalled", "speed-rejected", "autoplay-blocked"}


@dataclass
class Event:
    kind: str
    data: object
    source: object = None


class Engine:
    STOP_GRACE_SECONDS = 7
    def __init__(self, config: Config, events: queue.Queue, initial_homework_url="", initial_course_url="", initial_exam_url=""):
        self.config = config.validate()
        self.events = events
        self.commands = queue.Queue()
        self.stop = threading.Event()
        self.finished = threading.Event()
        self.stop_watchdog_started = False
        self.browser_process = None
        self.thread = threading.Thread(target=self._run, name="yuketang-browser", daemon=True)
        self.context = None
        self.paused = False
        self.active_page = None
        self.owned_page = None
        self.minimized = set()
        self.last_notice = None
        self.last_bootstrap = 0.0
        self.next_clicked_at = None
        self.profile_lock = None
        self.navigation_attempts = {}
        self.initial_homework_url = initial_homework_url
        self.mode = "homework" if initial_homework_url else "video"
        self.homework = None
        self.exam = None
        self.initial_exam_url = initial_exam_url
        if initial_exam_url:self.mode='exam'
        self.initial_course_url = initial_course_url
        from .course import CourseFlow
        self.course_flow = CourseFlow(self)
        self.bypass = VerificationBypass(self)
        self.windows = Windows()
        self.browser_pid = None
        self.window_target = None
        self.window_page = None
        self.window_bounds = {}
        self.native_windows = {}
        self.window_action_lock = threading.Lock()
        self.pending_window_action = None
        self.verification_page = None
        self.last_window_lookup = -float('inf')
        self.verification_scan_at = 0.0

    def start(self):
        self.thread.start()

    def command(self, name, value=None):
        if name=='stop':
            self.stop.set()
            with self.window_action_lock:
                if not self.stop_watchdog_started:
                    self.stop_watchdog_started=True
                    threading.Thread(target=self._stop_watchdog,name='browser-stop-watchdog',daemon=True).start()
            return
        if name in {'restore','minimize'}:
            with self.window_action_lock:
                self.pending_window_action = name
            return
        self.commands.put((name, value))

    def _stop_watchdog(self):
        if not self.finished.wait(self.STOP_GRACE_SECONDS):
            process=self.browser_process
            if process and process.terminate():
                self.notice('close-timeout','浏览器关闭未响应，已结束本助手启动的浏览器进程，正在释放会话。')
            elif not self.finished.is_set():
                self.notice('close-wait','浏览器仍在结束当前操作，尚未确认关闭；不会影响其他浏览器窗口。')

    def restore_window_now(self):
        # UI thread: no Playwright access or browser-worker wait here.
        target = self.window_target
        if not self.windows.valid(target) and self.browser_pid:
            target = self.windows.locate(self.browser_pid, self.window_bounds)
        return self.windows.restore(target)

    def _cache_window(self, page, window):
        self.last_window_lookup = time.monotonic()
        self.window_bounds = dict(window['bounds'])
        try:
            if not self.browser_pid:
                session = self.context.browser.new_browser_cdp_session()
                try:
                    info = session.send('SystemInfo.getProcessInfo')['processInfo']
                    self.browser_pid = next(int(item['id']) for item in info if item['type'] == 'browser')
                    self.browser_process = OwnedBrowserProcess(self.browser_pid)
                finally:
                    session.detach()
            target = self.windows.locate(self.browser_pid, window['bounds'], self.native_windows.get(window['windowId']))
            self.window_page = page
            self.window_target = target
            if target:
                self.native_windows[window['windowId']] = target
        except Exception:
            self.window_target = None

    def remember_window(self, page, force=False):
        if not page or page.is_closed():
            return
        if not force and page == self.window_page and (self.windows.valid(self.window_target) or time.monotonic()-self.last_window_lookup<1):
            return
        session = None
        try:
            session = self.context.new_cdp_session(page)
            self._cache_window(page, session.send('Browser.getWindowForTarget'))
        except Exception:
            self.window_target = None
        finally:
            if session:
                try:
                    session.detach()
                except Exception:
                    pass

    def set_verification_page(self, page):
        if self.verification_page != page:
            self.verification_page = page
            self.remember_window(page, force=True)

    def clear_verification_page(self):
        self.verification_page = None

    def _watch_verification(self):
        """Video and course pages also ask for a check; clear it without pausing."""
        if self.mode=='exam' or self.bypass.mode() == "off" or self.paused:
            return
        now = time.monotonic()
        if now - self.verification_scan_at < 3:
            return
        self.verification_scan_at = now
        if self.verification_page or (self.homework and self.homework.verification_wait):
            return
        page = self._challenge_page()
        if page is None:
            return
        try:
            if not verification_state(page)["required"]:
                self.bypass.reset(page)
                return
        except Exception:
            return
        self.set_verification_page(page)
        try:
            outcome = self.bypass.handle(page)
        except Exception:
            outcome = {"state": "failed", "message": ""}
        if outcome.get("state") == "cleared":
            self.notice("verification-cleared", outcome.get("message") or "安全核验已自动通过。")
            self.clear_verification_page()
        elif outcome.get("state") in {"unsupported", "exhausted"}:
            self.notice("verification-manual", outcome.get("message") or "安全核验需要手动完成。")

    def _challenge_page(self):
        """Pick a non-homework page that can carry the check (active first)."""
        candidates = [self.active_page, getattr(self.course_flow, "page", None)]
        pages = [p for p in self.context.pages if not p.is_closed()] if self.context else []
        candidates.extend(reversed(pages))
        for candidate in candidates:
            if not candidate or candidate.is_closed() or not allowed_url(candidate.url):
                continue
            if any(word in candidate.url for word in ("/exercise/", "/homework/", "/studycontenthomework/")):
                continue
            return candidate
        return None

    def emit(self, kind, data):
        self.events.put(Event(kind, data, self))
        if kind == "homework" and getattr(self, "course_flow", None):
            self.course_flow.on_homework(data)

    def notice(self, state, message):
        key = (state, message)
        if key != self.last_notice:
            self.last_notice = key
            self.emit("log", message)

    def window_state(self, page, state):
        session = self.context.new_cdp_session(page)
        try:
            window = session.send("Browser.getWindowForTarget")
            self._cache_window(page, window)
            session.send("Browser.setWindowBounds", {
                "windowId": window["windowId"], "bounds": {"windowState": state},
            })
        finally:
            session.detach()

    def _frames(self):
        for page in self.context.pages:
            if page.is_closed() or not allowed_url(page.url):
                continue
            for frame in page.frames:
                if allowed_url(frame.url):
                    yield page, frame

    def _set_paused(self, paused):
        self.paused = paused
        for _, frame in list(self._frames()):
            try:
                frame.evaluate("() => window.__yktHelper?." + ("pause()" if paused else "resume()"))
            except Exception:
                pass  # Navigation destroys a frame; the next poll installs it again.
        self.emit("paused", paused)
        self.notice("paused" if paused else "resumed", "已暂停自动播放" if paused else "已恢复自动播放")

    def _maintain_paused(self):
        # A route change can create a new autoplaying element after Pause was pressed.
        page = self.active_page
        if not page or page.is_closed() or not allowed_url(page.url):
            return
        try:
            header = page.locator(".learning-space-header__primary").first
            if not header.count() or not matches_course(self.config,page.url,header.inner_text()):
                return
            for frame in page.frames:
                if allowed_url(frame.url):
                    frame.evaluate("() => document.querySelectorAll('video').forEach(v => { if (!v.paused) v.pause(); })")
        except Exception:
            pass

    def _handle_commands(self):
        with self.window_action_lock:
            action, self.pending_window_action = self.pending_window_action, None
        if action:
            self._window_command(action)
        while True:
            try:
                name, value = self.commands.get_nowait()
            except queue.Empty:
                return
            if name == "stop":
                self.stop.set()
            elif name == "pause":
                if self.course_flow.active:
                    self.course_flow.toggle_pause()
                else:
                    self._set_paused(not self.paused)
            elif name == "options":
                updated=value.validate()
                if (updated.course_url,updated.course_name)!=(self.config.course_url,self.config.course_name):
                    self._switch_course(updated)
                else:self.config=updated
            elif name == "verification_probe":
                self.bypass.probe_requested = True
                self.notice("verification-probe", "已登记导出请求；下次出现安全核验时会把页面结构写入 .runtime。")
            elif name in {"course_start", "course_stop", "course_pause"}:
                try:
                    if name == "course_start":
                        if self.exam:self.exam.cancel(quiet=True)
                        self.course_flow.start(value)
                    elif name == "course_pause":
                        if self.course_flow.active:
                            self.course_flow.toggle_pause()
                        else:
                            self.emit("course", {"state": "idle", "message": "请先点击“开始 / 继续课程流程”。"})
                    else:
                        self.course_flow.stop()
                except Exception as exc:
                    self.course_flow.stop()
                    self.emit("course", {"state": "blocked", "message": str(exc) if isinstance(exc, ValueError) else "无法启动统一课程流程，请检查模型设置和课程页面。"})
            elif name.startswith('exam_'):
                try:
                    self.course_flow.stop('已切换到考试填写，统一课程流程已停止。')
                    if self.homework:self.homework.cancel(quiet=True)
                    self._set_paused(True);self.mode='exam';self.emit('mode','exam')
                    if name=='exam_open':self.exam.open(value)
                    elif name=='exam_read':self.exam.inspect(**value)
                    elif name=='exam_solve':self.exam.start(**value)
                    elif name=='exam_fill':self.exam.fill(**value)
                    elif name=='exam_cancel':self.exam.cancel()
                    elif name=='exam_export':self.exam.export()
                    elif name=='exam_resume':self.exam.tick()
                except Exception as exc:
                    self.emit('exam',{'state':'error','message':str(exc) if isinstance(exc,ValueError) else '考试页尚未就绪，请检查窗口或导出结构。'})
            elif name.startswith("homework_") or name == "video_mode":
                try:
                    if name in {"homework_open", "homework_solve", "homework_read", "video_mode"}:
                        if self.exam:self.exam.cancel(quiet=True)
                        self.course_flow.stop("已切换为手动视频或作业操作，统一课程流程已停止。")
                    if name == "homework_open":
                        self.homework.open(value)
                    elif name == "homework_read":
                        self.homework.inspect()
                    elif name == "homework_solve":
                        self.homework.start(**value)
                    elif name == "homework_cancel":
                        self.homework.cancel()
                    elif name == "homework_resume":
                        self.homework.resume_after_verification()
                    elif name == "homework_export":
                        self.homework.export()
                    elif name == "video_mode":
                        self.homework.cancel(quiet=True)
                        self.mode = "video"
                        if self.config.course_url:
                            path = urlparse(self.config.course_url).path
                            page = next((p for p in self.context.pages if not p.is_closed() and
                                         urlparse(p.url).netloc == urlparse(self.config.course_url).netloc and
                                         urlparse(p.url).path == path), None)
                            if not page:
                                page = self.context.new_page()
                                page.goto(self.config.course_url, wait_until="commit", timeout=15000)
                            self.active_page = page
                        self._set_paused(False)
                        self.emit("mode", "video")
                except Exception as exc:
                    message = str(exc) if isinstance(exc, ValueError) else "作业页面尚未就绪，请检查浏览器后重试。"
                    self.emit("homework", {"state": "error", "message": message})

    def _window_command(self, name):
        preferred = [self.verification_page, self.course_flow.page if self.course_flow.active else None,
                     self.exam.page if self.mode=='exam' and self.exam else None,
                     self.homework.page if self.mode == 'homework' and self.homework else None,
                     self.active_page, self.owned_page]
        pages = list(self.context.pages) if self.context else []
        page = next((p for p in preferred if p in pages and not p.is_closed()), None)
        if not page:
            page = next((p for p in reversed(pages) if not p.is_closed() and allowed_url(p.url)), None)
        if not page:
            self.emit('window_result', {'action':name,'success':False,'message':'未找到助手控制的浏览器页面；浏览器可能已经关闭。'})
            return
        try:
            self.window_state(page, 'minimized' if name == 'minimize' else 'normal')
            if name == 'restore':
                page.bring_to_front()
                native = self.restore_window_now()
                message = '浏览器窗口已恢复，已选择当前学习标签页。' if native['foreground'] else '窗口已恢复并选中学习标签页；若仍被遮挡，请点击浏览器任务栏图标。'
            else:
                message = '浏览器已最小化。'
            self.emit('window', name)
            self.emit('window_result', {'action':name,'success':True,'message':message})
        except Exception as exc:
            self.emit('window_result', {'action':name,'success':False,'message':f'恢复浏览器未完成（{type(exc).__name__}），请再次点击或使用任务栏切回。'})

    def _switch_course(self, updated):
        self.course_flow.stop('目标课程已更换，原课程流程已停止。')
        for task in (self.homework,self.exam):
            if task:task.cancel(quiet=True)
        self._set_paused(True)
        self.config=updated
        self.initial_course_url='';self.initial_homework_url='';self.initial_exam_url=''
        self.active_page=None;self.navigation_attempts.clear();self.minimized.clear()
        self.next_clicked_at=None;self.last_bootstrap=0;self.mode='video'
        page=self.owned_page
        if not page or page.is_closed():page=self.context.new_page()
        self.owned_page=page
        self.emit('mode','video')
        self.emit('status',{'state':'navigating','title':'正在切换并识别目标课程'})
        try:
            page.goto(updated.course_url or updated.entry_url(),wait_until='commit',timeout=15000)
            self.notice('course-switch','课程入口已切换，将按链接识别当前课程。')
        except Exception:
            if not self.stop.is_set():self.notice('course-switch-wait','课程入口正在加载，请在浏览器检查登录或网络。')
        self._set_paused(False)

    def _navigate_landing(self, pages):
        now = time.monotonic()
        for page in reversed(pages):
            if now - self.navigation_attempts.get(page, -float("inf")) < 12:
                continue
            current = urlparse(page.url)
            target_url = self.initial_course_url or self.config.course_url
            target = urlparse(target_url) if target_url else None
            # Do not interrupt QR/SSO login. The official login's `next` parameter
            # handles its first redirect; this repairs redirects to the dashboard.
            authenticated_route = current.path.rstrip("/") == "/v2/web" or current.path.startswith((
                "/v2/web/index", "/v2/web/studentLog/",
            ))
            if target and authenticated_route and (current.netloc, current.path) != (target.netloc, target.path):
                self.navigation_attempts[page] = now
                self.notice("course-direct", f"登录后正在直达课程视频：{self.config.course_name}")
                self.emit("status", {"state": "navigating", "title": "正在打开目标课程视频"})
                page.goto(target_url, wait_until="commit", timeout=15000)
                return True
            if not target and authenticated_route:
                result = page.evaluate(NAVIGATION_SCRIPT, {"courseName": self.config.course_name})
                if result["state"] in {"course-clicked", "learning-clicked"}:
                    self.navigation_attempts[page] = now
                    self.notice("course", f"正在进入课程：{self.config.course_name}")
                    return True
                if result["state"] == "ambiguous-course":
                    self.notice("ambiguous-course", "发现多个同名课程，请填写课程直达地址。")
        return False

    def _poll(self):
        course_pages = []
        landing_pages = []
        for page in list(self.context.pages):
            if page.is_closed() or not allowed_url(page.url):
                continue
            try:
                header = page.locator(".learning-space-header__primary").first
                course = header.inner_text().strip() if header.count() else ""
                outline=not course and '/lms-graph/' not in urlparse(page.url).path
                if not outline and matches_course(self.config,page.url,course):
                    course_pages.append(page)
                elif not course:
                    landing_pages.append(page)
            except Exception:
                continue
        candidates = []
        if course_pages:
            page = self.active_page if self.active_page in course_pages else course_pages[-1]
            self.active_page = page
            self._adopt_course_name(page)
            self.remember_window(page)
            for frame in page.frames:
                if not allowed_url(frame.url):
                    continue
                try:
                    frame.evaluate(PLAYER_SCRIPT)
                    result = frame.evaluate("o => window.__yktHelper.read(o)", self.config.player_options())
                    candidates.append((page, frame, result))
                    if result["state"] == "blocked":
                        for child in page.frames:
                            if child != frame and allowed_url(child.url):
                                child.evaluate("() => document.querySelectorAll('video').forEach(v => v.pause())")
                        break
                except Exception:
                    continue
        elif landing_pages:
            self.active_page = None
            if self._navigate_landing(landing_pages):
                return
            page = landing_pages[-1]
            candidates.append((page, page.main_frame, {"state": "waiting", "title": "等待登录并进入课程"}))
        if not candidates:
            self.emit("status", {"state": "waiting", "title": "等待登录并进入课程"})
            return
        ranked = sorted(candidates, key=lambda c: (
            c[2]["state"] == "blocked", "time" in c[2], bool(c[2].get("course"))
        ), reverse=True)
        page, frame, result = ranked[0]
        if "time" in result or result.get("course"):
            self.active_page = page
        state = result["state"]
        if state in ATTENTION or state == "paused":
            self.notice(state, result.get("detail", state))
        elif state == "playing":
            self.notice("playing", f"正在播放：{result['title']}（{result['rate']:g} 倍速）")
        self.emit("status", result)
        if self.course_flow.active:
            self.course_flow.observe_video(page, result)
        if state == "waiting":
            now = time.monotonic()
            if not self.course_flow.active and matches_course(self.config,page.url,result.get('course')):
                if now - self.last_bootstrap > 10:
                    self.last_bootstrap = now
                    frame.evaluate("() => window.__yktHelper.firstVideo()")
        if state == "playing":
            self.next_clicked_at = None
            if self.config.auto_minimize and page not in self.minimized and result.get("time", 0) > 0.5 and result.get("readyState", 0) >= 3:
                try:
                    self.window_state(page, "minimized")
                    self.minimized.add(page)
                    self.emit("window", "minimize")
                except Exception as exc:
                    self.minimized.add(page)
                    self.notice("minimize-error", f"自动最小化失败：{type(exc).__name__}")
        if state == "ended" and self.config.auto_next and not self.course_flow.active:
            next_result = frame.evaluate("delay => window.__yktHelper.nextVideo(delay)", self.config.next_delay_seconds)
            next_state = next_result["state"]
            if next_state == "next-clicked":
                self.next_clicked_at = time.monotonic()
                self.notice("next", f"切换到下一视频：{next_result['title']}")
            elif next_state == "complete":
                self.notice("complete", "目录中的视频已播放到末尾；请在网页查看平台记录的完成度。")
            elif next_state == "no-directory":
                self.notice("no-directory", "当前目录结构未识别，请手动选择下一视频。")
            elif next_state == "next-pending" and self.next_clicked_at and time.monotonic() - self.next_clicked_at > 20:
                self.notice("next-timeout", "下一视频没有加载，请恢复窗口检查。")

    def _adopt_course_name(self, page):
        if not course_identity(self.config.course_url):return
        header=page.locator('.learning-space-header__primary').first
        name=header.inner_text().strip() if header.count() else ''
        if name and name!=self.config.course_name:
            self.config=replace(self.config,course_name=name)
            self.emit('course_context',{'course_name':name,'course_url':self.config.course_url})

    def _acquire_lock(self):
        RUNTIME.mkdir(exist_ok=True)
        self.profile_lock = (RUNTIME / "profile.lock").open("a+b")
        if self.profile_lock.tell() == 0:
            self.profile_lock.write(b"0")
            self.profile_lock.flush()
        self.profile_lock.seek(0)
        import msvcrt
        try:
            msvcrt.locking(self.profile_lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            self.profile_lock.close()
            self.profile_lock = None
            raise RuntimeError("已有启动器正在使用登录目录，请先关闭另一个启动器。") from exc

    def _open_entry(self):
        initial_url = self.initial_exam_url or self.initial_homework_url or self.initial_course_url
        entry_config = replace(self.config, course_url=initial_url) if initial_url else self.config
        entry = entry_config.entry_url()
        self.notice('navigation-start', f'正在连接登录入口：{urlparse(entry).hostname}')
        self.emit('status', {'state':'navigating', 'title':'正在打开学校登录入口'})
        try:
            self.owned_page.goto(entry, wait_until='domcontentloaded', timeout=45000)
            return True
        except Exception as exc:
            code = re.search(r'net::ERR_[A-Z0-9_]+', str(exc))
            reason = code.group() if code else ('等待页面超时' if type(exc).__name__ == 'TimeoutError' else type(exc).__name__)
            self.notice('navigation', f'登录入口打开失败（{reason}）；可停止后重试，或在浏览器手动打开学校登录页。')
            return False

    def _run(self):
        try:
            if self.stop.is_set():return
            from playwright.sync_api import sync_playwright
            self._acquire_lock()
            with sync_playwright() as playwright:
                options = {
                    "user_data_dir": str(RUNTIME / "browser-profile"),
                    "headless": False, "no_viewport": True,
                    "args": [
                        "--disable-background-timer-throttling",
                        "--disable-renderer-backgrounding",
                        "--disable-backgrounding-occluded-windows",
                        "--disable-blink-features=AutomationControlled",
                    ],
                }
                if self.config.browser != "chromium":
                    options["channel"] = self.config.browser
                self.context = playwright.chromium.launch_persistent_context(**options)
                self.context.on('close',lambda:self.stop.set())
                self.bypass.install(self.context, stealth=self.config.stealth)
                from .homework import HomeworkSession
                self.homework = HomeworkSession(self)
                from .exam import ExamSession
                self.exam = ExamSession(self)
                self.owned_page = self.context.pages[0] if self.context.pages else self.context.new_page()
                self.remember_window(self.owned_page)
                if self.stop.is_set():
                    self.context.close();return
                self.emit("running", True)
                self.notice("login", "浏览器已打开，请自行登录雨课堂；登录信息仅保存在本地专用浏览器目录。")
                self._open_entry()
                errors = 0
                while not self.stop.is_set() and self.context.pages:
                    self._handle_commands()
                    if self.stop.is_set():
                        break
                    if self.homework:
                        try:
                            if self.course_flow.active and self.course_flow.paused:
                                self.homework._gate_verification()
                            else:
                                self.homework.tick()
                        except Exception as exc:
                            self.homework.cancel(quiet=True)
                            message = str(exc) if isinstance(exc, ValueError) else "作业页面已变化，请重新识别当前题。"
                            self.emit("homework", {"state": "error", "message": message})
                    if self.exam and self.mode=='exam':
                        try:self.exam.tick()
                        except Exception as exc:
                            self.exam.cancel(quiet=True)
                            self.emit('exam',{'state':'error','message':str(exc) if isinstance(exc,ValueError) else '考试页面已变化，请重新识别。'})
                    if self.course_flow.active:
                        try:
                            self.course_flow.tick()
                        except Exception as exc:
                            message = str(exc) if isinstance(exc, ValueError) else "课程页面尚未就绪或已经变化，请检查页面后继续。"
                            self.course_flow._hold(message)
                    if self.mode == "video" and not self.paused:
                        try:
                            self._poll()
                            errors = 0
                        except Exception as exc:
                            errors += 1
                            if errors >= 5:
                                self.notice("poll-error", f"页面暂时无法读取：{type(exc).__name__}，请恢复窗口检查。")
                    elif self.paused and self.mode!='exam':
                        self._maintain_paused()
                    try:
                        self._watch_verification()
                    except Exception:
                        pass
                    # Pump Playwright events; sleep() alone would postpone page-close callbacks.
                    open_pages = self.context.pages
                    if not open_pages:
                        break
                    try:
                        open_pages[0].wait_for_timeout(800)
                    except Exception:
                        pass
                self.context.close()
        except Exception as exc:
            # Keep messages free of page URLs/cookies and signed media links.
            if isinstance(exc, RuntimeError):
                message = str(exc)
            else:
                message = (f"启动或运行失败（{type(exc).__name__}）。请确认所选浏览器已安装；"
                           "Chromium 可通过 .venv\\Scripts\\python.exe -m playwright install chromium 安装。")
            if not self.stop.is_set():self.emit("error", message)
        finally:
            self.stop.set()
            for task in (self.course_flow,self.exam,self.homework):
                if task:
                    try:
                        if task is self.course_flow:task.stop('浏览器已关闭，课程流程已停止，可重新启动。')
                        else:task.close()
                    except Exception:
                        self.notice('cleanup-error','浏览器任务清理遇到问题，已恢复启动入口。')
            self.context = None
            for resource in (self.browser_process,self.profile_lock):
                if resource:
                    try:resource.close()
                    except Exception:pass
            self.emit("running", False)
            self.finished.set()
