"""Current-assignment workflow; browser actions stay on the Engine thread."""
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Error as BrowserError

from .config import RUNTIME, allowed_url
from .llm import ModelClient, ModelSettings, is_homework_url
from .secrets import KeyStore
from .ocr import RenderedTextReader
from .verification import VERIFICATION_SCRIPT, verification_state
from .submission import SubmissionMonitor
from .course_identity import matches_course

HOMEWORK_SCRIPT = VERIFICATION_SCRIPT + '\n' + Path(__file__).with_name("homework.js").read_text(encoding="utf-8")


class HomeworkSession:
    def __init__(self, owner):
        self.owner = owner
        self.page = None
        self.pinned_page = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="homework-model")
        self.pending = None
        self.fill_interrupted = False
        self.discard = False
        self.batch = False
        self.settings = None
        self.overwrite = False
        self.seen = set()
        self.completed = 0
        self.advance_at = None
        self.waiting_for = None
        self.wait_started = 0
        self.scan_until = 0
        self.last_scan = 0
        self.last_intro_click = 0
        self.assignment = None
        self.reader = RenderedTextReader(notify=lambda: self.emit("recognizing", "检测到特殊字体，正在本机识别网页实际显示的中文…"),
                                         blocked=lambda: self._gate_verification())
        self.pending_started = 0
        self.last_progress = 0
        self.guard_handled = False
        self.verification_wait = None
        self.deferred_solve = False
        self.auto_submit = False
        self.submission = None
        self.submitted_count = 0
        self.attempted_submissions = set()
        self.advance_question = None
        self.waiting_question = None
        self.next_candidate = None
        self.navigation_retry_at = 0
        self.navigation_retry_used = False
        self.last_confirmed = None
        self.advance_ready_deadline = None
        self.submission_monitor = SubmissionMonitor(owner.context)
        self.submission_history = {}

    def emit(self, state, message, **extra):
        self.owner.emit("homework", {"state": state, "message": message, **extra})
        if state == 'complete':
            self._finish_learning()

    def _learn(self, question, answer, status='filled'):
        service=getattr(self.owner,'learning',None)
        if not service or not self.assignment:
            return
        try:
            flow=getattr(self.owner,'course_flow',None)
            item=getattr(flow,'item',None) or {}
            service.record(self.owner.config.course_name,self.assignment,question,answer,
                           model=self.settings.model if self.settings else '',status=status,
                           assignment_title=item.get('title','') if item.get('kind')=='homework' else '')
        except Exception:
            service.emit('本地做题记录暂未保存，请在“记录与经验”中检查存储目录。','error')

    def _finish_learning(self):
        service=getattr(self.owner,'learning',None)
        if not service or not self.assignment or not self.settings:
            return
        try:
            service.request_review(''.join(self.assignment),self.settings)
        except Exception as exc:
            service.emit(str(exc) if isinstance(exc,ValueError) else '复盘未启动；课程流程继续，可稍后重试。','error')

    def _eligible(self, page):
        if page.is_closed() or not allowed_url(page.url) or not is_homework_url(page.url):
            return False
        target = re.search(r"/lms-graph/(\d+)", self.owner.config.course_url)
        actual = re.search(r"/lms-graph/(\d+)", page.url)
        if target and actual and target[1] != actual[1]:
            return False
        header = page.locator(".learning-space-header__primary").first
        return not header.count() or matches_course(self.owner.config,page.url,header.inner_text())

    def current(self, resolve_text=True):
        pages = [p for p in self.owner.context.pages if self.pinned_page is None or p == self.pinned_page]
        if self.page in pages:
            pages.remove(self.page)
            pages.append(self.page)
        for page in reversed(pages):
            if not self._eligible(page):
                continue
            for frame in page.frames:
                if not allowed_url(frame.url):
                    continue
                frame.evaluate(HOMEWORK_SCRIPT)
                question = frame.evaluate("() => window.__yktHomework.read()")
                if question:
                    self.page = page
                    if hasattr(self.owner, 'remember_window'):
                        self.owner.remember_window(page)
                    if resolve_text:
                        question = self.reader.resolve(frame, question)
                    return page, frame, question
        return None

    def enter(self):
        if self.owner.mode != "homework":
            self.owner._set_paused(True)
            self.owner.emit("mode", "homework")
        self.owner.mode = "homework"

    def _verification_state(self):
        pages = [p for p in self.owner.context.pages if (self.pinned_page is None or p == self.pinned_page) and self._eligible(p)]
        page = self.page if self.page in pages else pages[-1] if pages else None
        if not page:
            return None
        state = verification_state(page)
        if state['required'] or (state['uncertain'] and self.verification_wait):
            return page
        return None

    def _show_verification(self, page, now):
        wait = self.verification_wait
        if (wait.get("window_restored") and wait.get("tab_activated")) or wait.get("foreground_attempts", 0) >= 3:
            return
        if now < wait.get("foreground_retry_at", 0):
            return
        wait["foreground_attempts"] = wait.get("foreground_attempts", 0) + 1
        wait["foreground_retry_at"] = now + 2
        # Restore and activate separately: one failed operation must not skip the other.
        if not wait.get("window_restored"):
            try:
                if hasattr(self.owner, "window_state"):
                    self.owner.window_state(page, "normal")
                wait["window_restored"] = True
            except Exception:
                pass
        if not wait.get("tab_activated"):
            try:
                page.bring_to_front()
                wait["tab_activated"] = True
            except Exception:
                pass
        if wait.get("window_restored") and wait.get("tab_activated"):
            self.owner.emit("window", "restore")
            self.emit("verification", "检测到安全核验，已恢复浏览器并切到作业标签页。请手动完成，之后自动继续答题。")
        elif wait["foreground_attempts"] >= 3:
            self.emit("verification", "作业已暂停等待安全核验；自动切回浏览器未成功，请点击“恢复浏览器”后手动完成核验。")

    def _attempt_bypass(self, page):
        """Run one automatic security-check attempt before asking the user."""
        bypass = getattr(self.owner, "bypass", None)
        if bypass is None:
            return
        wait = self.verification_wait or {}
        try:
            outcome = bypass.handle(page, wait)
        except Exception:
            return
        state = outcome.get("state")
        message = outcome.get("message") or ""
        if state == "cleared":
            if not wait.get("bypassed"):
                wait["bypassed"] = True
                self.emit("verification", message or "安全核验已自动通过。")
        elif state in {"unsupported", "exhausted"} and message and wait.get("bypass_message") != message:
            wait["bypass_message"] = message
            self.emit("verification", message)

    def _gate_verification(self, force_resume=False, observed=False):
        if self.owner.mode != "homework":
            return False
        now = time.monotonic()
        page = self.page if observed else self._verification_state()
        if page:
            self.page = page
            if hasattr(self.owner, 'set_verification_page'):
                self.owner.set_verification_page(page)
            if self.submission and self.submission['clicked']:
                self.submission['verification_seen'] = True
            if not self.verification_wait:
                self.verification_wait = {"since": now, "clear_since": None}
                self.emit("verification", "检测到安全核验，先尝试自动通过；失败后会暂停并等待你手动完成。")
                self.owner.emit('attention', {'state':'verification', 'message':'检测到安全核验，正在自动处理。'})
            else:
                self.verification_wait["clear_since"] = None
            self._attempt_bypass(page)
            bypass = getattr(self.owner, "bypass", None)
            if not (bypass and bypass.holds_foreground(page)):
                self._show_verification(page, now)
            return True
        if self.verification_wait:
            wait = self.verification_wait
            try:
                current = self.current(resolve_text=False)
            except BrowserError:
                current = None
            if not current:
                wait["clear_since"] = None
                if not wait.get("recovering"):
                    wait["recovering"] = True
                    self.emit("verification", "核验窗口已关闭，正在等待原作业题面恢复；无需重新启动任务。")
                return True
            if wait["clear_since"] is None:
                wait["clear_since"] = now
            if not force_resume and now - wait["clear_since"] < 1:
                return True
            elapsed = now - wait["since"]
            self.extend_wait(elapsed)
            if self.submission and self.submission.get('verification_seen'):
                self.submission['verification_completed_at'] = now
                self.submission['deadline'] = max(self.submission['deadline'], now + 35)
            if self.waiting_for and not self.navigation_retry_used:
                self.navigation_retry_at = now + 2
            self.verification_wait = None
            bypass = getattr(self.owner, "bypass", None)
            if bypass is not None:
                bypass.reset(current[0])
            if hasattr(self.owner, 'clear_verification_page'):
                self.owner.clear_verification_page()
            self.owner.emit('attention', {'state':'verification-cleared'})
            self.emit("resumed", "安全核验窗口已消失，正在继续之前的作业任务。")
        return False

    def extend_wait(self, elapsed):
        if self.advance_ready_deadline is not None:
            self.advance_ready_deadline += elapsed
        if self.waiting_for:
            self.wait_started += elapsed
        if self.advance_at:
            self.advance_at = (self.advance_at[0] + elapsed, self.advance_at[1])
        if self.submission:
            self.submission["deadline"] += elapsed
        if self.scan_until:
            self.scan_until += elapsed

    def resume_after_verification(self):
        if self._gate_verification(force_resume=True):
            if not self.verification_wait.get("recovering"):
                self.emit("verification", "安全核验仍在显示，请先在浏览器手动完成。")

    def open(self, url):
        if not allowed_url(url) or not is_homework_url(url):
            raise ValueError("请填写雨课堂普通作业详情页地址。")
        target = re.search(r"/lms-graph/(\d+)", self.owner.config.course_url)
        actual = re.search(r"/lms-graph/(\d+)", url)
        if target and actual and target[1] != actual[1]:
            raise ValueError("作业地址不属于当前设置的课程，请先修改目标课程。")
        self.cancel(quiet=True)
        self.enter()
        path = urlparse(url).path
        self.page = next((p for p in self.owner.context.pages if not p.is_closed() and
                          urlparse(p.url).netloc == urlparse(url).netloc and urlparse(p.url).path == path), None)
        if not self.page:
            # Reuse the startup login tab when the session has not logged in yet.
            login = self.owner.owned_page
            if login and not login.is_closed() and urlparse(login.url).path.rstrip("/") == "/web":
                self.page = login
                target = urlparse(url)
                requested_next = target.path + ("?" + target.query if target.query else "")
                if parse_qs(urlparse(login.url).query).get("next") != [requested_next]:
                    self.page.goto(replace(self.owner.config, course_url=url).entry_url(), wait_until="commit", timeout=15000)
            else:
                self.page = self.owner.context.new_page()
                self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self.page.bring_to_front()
        if hasattr(self.owner, 'remember_window'):
            self.owner.remember_window(self.page)
        self.scan_until = time.monotonic() + 300
        self.emit("opening", "已打开作业；正在识别题面。如需登录，请在浏览器自行完成。")

    def inspect(self):
        self.enter()
        if self._gate_verification():
            return
        current = self.current(resolve_text=False)
        if not current:
            self.emit("waiting", "尚未识别题面；请在网页点击“开始作答”，或导出题面样本。")
            return
        state = current[1].evaluate("() => window.__yktHomework.navigationState()")
        if state["state"] != "ready":
            message = "网页正在提示本题尚未提交；连续填写会选择“先不用了”并切到下一题。" if state["state"] == "unsubmitted-prompt" else "网页有其他弹窗，请先处理后再识别题目。"
            self.emit("blocked", message)
            return
        question = self.reader.resolve(current[1], current[2])
        reason = "（已有答案）" if question["answered"] else ""
        self.emit("ready", f"已识别 {question['type']} 题 {reason}", question=question)

    def start(self, settings: ModelSettings, batch=False, overwrite=False, auto_submit=False):
        if self.pending or self.submission:
            raise ValueError("上一条模型请求或提交任务尚未结束，请等待或先停止任务。")
        self.enter()
        self.settings = settings.validate(require_model=True)
        self.overwrite = overwrite
        self.auto_submit = auto_submit
        self.batch = batch
        self.discard = False
        self.seen.clear()
        self.completed = 0
        self.submitted_count = 0
        self.advance_at = None
        self.waiting_for = None
        self.assignment = None
        self.guard_handled = False
        self.advance_question = None
        self.advance_ready_deadline = None
        self.waiting_question = None
        self.next_candidate = None
        self.navigation_retry_at = 0
        self.navigation_retry_used = False
        self.last_confirmed = None
        self._solve_current()

    def _same_assignment(self, page):
        parsed = urlparse(page.url)
        return self.assignment is None or self.assignment == (parsed.netloc, parsed.path)

    def _answer_source(self):
        return str(getattr(getattr(self.owner, "config", None), "answer_source", "auto") or "auto").lower()

    @staticmethod
    def _plain(value):
        return re.sub(r"\s+", "", str(value or "")).strip().lower()

    def _map_embedded(self, question, values):
        """Translate the platform's key format into the option ids the page uses."""
        kind = question.get("type")
        if kind not in {"single", "multiple", "judgment"}:
            return None
        options = question.get("options") or []
        ids = [str(option.get("id")) for option in options]
        if not ids:
            return None
        by_text = {str(option.get("id")): self._plain(option.get("text")) for option in options}
        truthy = {"true", "t", "1", "正确", "对", "是", "√", "v"}
        falsy = {"false", "f", "0", "错误", "错", "否", "×", "x"}
        mapped = []
        for value in values:
            item = str(value).strip()
            if not item:
                continue
            found = next((oid for oid in ids if oid.upper() == item.upper()), None)
            if found is None and item.isdigit() and len(item) <= 2:
                index = int(item)
                if 0 <= index < len(ids):
                    found = ids[index]
                elif 1 <= index <= len(ids):
                    found = ids[index - 1]
            if found is None:
                lowered = item.lower()
                if lowered in truthy:
                    found = next((oid for oid in ids if "正确" in by_text[oid] or "对" in by_text[oid]), None)
                    if found is None:
                        found = "true" if "true" in ids else ids[0]
                elif lowered in falsy:
                    found = next((oid for oid in ids if "错误" in by_text[oid] or "错" in by_text[oid]), None)
                    if found is None:
                        found = "false" if "false" in ids else ids[-1]
            if found is None:
                found = next((oid for oid in ids if by_text[oid] == self._plain(item)), None)
            if found is not None and found not in mapped:
                mapped.append(found)
        if not mapped:
            return None
        if kind in {"single", "judgment"}:
            mapped = mapped[:1]
        return {"option_ids": mapped}

    def _embedded_answer(self, question):
        """Return the platform-provided key for objective questions, if allowed."""
        source = self._answer_source()
        if source == "model":
            return None
        raw = question.get("embedded_answer")
        if not raw:
            if source == "embedded":
                raise ValueError("本题没有题目自带的答案，已按设置停止；可在设置中改为“自动”以调用模型。")
            return None
        answer = self._map_embedded(question, raw)
        if answer is None:
            if source == "embedded":
                raise ValueError("题目自带答案无法对应到本题选项，已停止；可改为“自动”以调用模型。")
            return None
        return answer

    def _solve_current(self):
        if self._gate_verification():
            self.deferred_solve = True
            return
        self.deferred_solve = False
        current = self.current(resolve_text=False)
        if not current:
            self.batch = False
            self.emit("waiting", "没有找到可识别的作业题，请先打开作业并开始作答。")
            return
        page, frame, question = current
        if not self._same_assignment(page):
            self.batch = False
            self.emit("stopped", "已切换到另一份作业，连续填写已停止。")
            return
        parsed = urlparse(page.url)
        self.assignment = (parsed.netloc, parsed.path)
        state = frame.evaluate("() => window.__yktHomework.navigationState()")
        if state["state"] == "security-verification":
            self.deferred_solve = True
            self._gate_verification(observed=True)
            return
        if state["state"] == "unsubmitted-prompt" and self.batch:
            self._advance(frame, question["fingerprint"])
            return
        if state["state"] != "ready":
            self.batch = False
            self.emit("blocked", "网页有待处理弹窗，尚未调用模型或填写答案。")
            return
        try:
            question = self.reader.resolve(frame, question)
        except ValueError:
            if self._gate_verification():
                self.deferred_solve = True
                return
            raise
        if self._gate_verification():
            self.deferred_solve = True
            return
        fingerprint = question["fingerprint"]
        if fingerprint in self.seen:
            self.batch = False
            self.emit("stopped", "检测到重复题目，连续填写已停止。")
            return
        self.seen.add(fingerprint)
        if question["read_only"] or (question["answered"] and not self.overwrite):
            if self.auto_submit and question["answered"] and not question["read_only"] and not question["submitted"]:
                self._queue_submission(page, frame, question)
                return
            if getattr(self.owner,'learning',None):
                try:
                    saved_answer=frame.evaluate('fp=>window.__yktHomework.readAnswer(fp)',question['fingerprint'])
                    self._learn(question,saved_answer,'submitted' if question.get('submitted') else 'filled')
                except Exception:
                    self.owner.learning.emit('已有答案记录未读取完成，课程流程继续。','error')
            self.emit("skipped", "已保留本题已有答案或只读状态。", question=question)
            if self.batch:
                self._schedule_advance(question, 0.4)
            else:
                self._finish_learning()
            return
        if question["unsupported"] or question["has_images"]:
            self.batch = False
            self.emit("unsupported", "本题含图片/附件、未知题型或无法定位的控件，请手动处理或导出样本。", question=question)
            return
        embedded = self._embedded_answer(question)
        if embedded is not None:
            question={**question,'answer_source':'embedded'}
            # Same pipeline as a model answer (verify, fill, submit), but the key
            # came with the page data, so no API call is made.
            ready = Future()
            ready.set_result(embedded)
            self.pending = (ready, page, frame, question, page.url)
            self.fill_interrupted = False
            self.pending_started = time.monotonic()
            self.last_progress = self.pending_started
            self.emit("thinking", "本题使用题目自带的答案（未调用模型），正在填写并核对。", question=question)
            return
        question={**question,'answer_source':'model'}
        try:
            client = ModelClient(self.settings, KeyStore().load(self.settings.endpoint))
        except ValueError:
            self.batch = False
            raise
        self.pending = (self.executor.submit(client.solve, question), page, frame, question, page.url)
        self.fill_interrupted = False
        self.pending_started = time.monotonic()
        self.last_progress = self.pending_started
        self.emit("thinking", "正在调用模型解答，返回后校验并填写当前题。", question=question)

    def _queue_submission(self, page, frame, question, answer=None):
        key = (self.assignment, question["id"])
        live = frame.evaluate("() => window.__yktHomework.read()")
        if not live or live["fingerprint"] != question["fingerprint"]:
            raise ValueError("题目已切换，未提交旧答案。")
        learning_answer=answer
        if answer is None and getattr(self.owner,'learning',None):
            try:
                learning_answer=frame.evaluate('fp=>window.__yktHomework.readAnswer(fp)',live['fingerprint'])
                self._learn(question,learning_answer)
            except Exception:
                self.owner.learning.emit('已有答案记录未读取完成，课程流程继续。','error')
        if answer is not None and not frame.evaluate('p=>window.__yktHomework.verify(p)',
            {'fingerprint':live['fingerprint'],'answer':answer}):
            raise ValueError('网页完整答案与模型返回不一致，未提交；请核对所有多选项。')
        if key in self.attempted_submissions:
            previous = self.submission_history.get(key)
            outcome = previous['probe'].outcome if previous else 'uncertain'
            if previous and outcome in {'accepted','not-sent','rejected'}:
                if outcome != 'accepted' and live['draft_fingerprint'] != previous['draft_fingerprint']:
                    self.batch = False
                    self.emit('blocked', '上次提交后答案已改变，已保留当前内容；请在网页核对后提交。')
                    return
                previous.update(page=page, frame=frame, deadline=time.monotonic()+35, manual_resume=True)
                previous['probe'].page = page
                previous['retry_count'] = 0
                self.submission = previous
                self.emit('submitting', '正在核对上次提交状态；仅在确认未发出或被拒绝时重新尝试。')
                return
            self.batch = False
            self.emit("blocked", "上次提交结果仍不明确，未再次提交；请在网页核对结果或导出题面样本。")
            return
        classroom = re.search(r'/lms-graph/(\d+)', page.url)
        probe = self.submission_monitor.begin(page, question['id'], classroom[1] if classroom else '', question.get('server_identity', True))
        self.submission = {"page": page, "frame": frame, "question": question, "answer": answer,
                           "fingerprint": question["fingerprint"], "draft_fingerprint": live["draft_fingerprint"],
                           "key": key, "clicked": False, "deadline": time.monotonic() + 30,
                           "tracking": frame.evaluate('fp=>window.__yktHomework.submissionBaseline(fp)', live['fingerprint']),
                           'probe':probe, 'retry_count':0, 'clicked_at':0,
                           'verification_seen':False, 'verification_completed_at':0, 'confirmation_clicked':False}
        self.submission['learning_answer']=learning_answer
        self.submission_history[key] = self.submission
        self.emit("submitting", "已核对本题答案，准备自动提交本题。", question=question,
                  **({"answer": answer} if answer is not None else {}))

    def _submission_expired(self, job):
        if time.monotonic() <= job['deadline'] or self._gate_verification():
            return False
        self.submission = None
        self.batch = False
        outcome = job['probe'].outcome
        self._learn(job['question'],job.get('learning_answer'), 'rejected' if outcome=='rejected' else 'submission_unknown')
        if outcome == 'not-sent':
            message = '未观察到作业提交请求，原答案已保留；请检查网页，之后可点击继续重试。'
        elif outcome == 'rejected':
            message = '网站尚未接受本题提交，原答案已保留；请在网页检查原因后继续。'
        else:
            message = '本题提交结果尚未确认，未再次提交；请在网页检查。'
        self.emit('error', message)
        return True

    def _confirm_submission(self, job):
        self.submission = None
        job['confirmed'] = True
        self.submitted_count += 1
        question = {**job['question'], 'index': (job['tracking'] or {}).get('index') or job['question'].get('index', 0)}
        self.last_confirmed = question
        learning_question=job['question']
        if getattr(self.owner,'learning',None):
            try:
                live=job['frame'].evaluate('() => window.__yktHomework.read()')
                if live and live['id']==learning_question['id']:
                    learning_question={**learning_question,'correctness':live.get('correctness','unknown')}
            except Exception:
                pass
        self._learn(learning_question,job.get('learning_answer'),'submitted')
        self.emit('submitted', f'本题提交已确认，本次已确认提交 {self.submitted_count} 题。')
        if not self.batch:
            self._finish_learning()
        if self.batch:
            try:
                current = self.current(resolve_text=False)
            except BrowserError:
                current = None
            if current and current[2]['id'] == question['id']:
                self._schedule_advance(current[2], 1.5)
            else:
                self._wait_next(job['fingerprint'], question)

    def _resume_unsubmitted(self, job, state):
        outcome = job['probe'].outcome
        if outcome not in {'not-sent','rejected'} or job['retry_count'] >= 1:
            return False
        now = time.monotonic()
        verified = job.get('verification_completed_at', 0)
        if not job.get('manual_resume'):
            if verified:
                if now - verified < 2:
                    return False
            elif outcome != 'not-sent' or now - job['clicked_at'] < 5:
                return False
        if state not in {'pending','current-submit-confirm'}:
            return False
        result = job['frame'].evaluate('p=>window.__yktHomework.resumeVerifiedSubmission(p)', {
            'id':job['question']['id'], 'draft_fingerprint':job['draft_fingerprint'],
            'confirm':job.get('confirmation_clicked',False),
            'expected_answer':job['answer'],
        })
        if result['state'] not in {'retry-submit','retry-confirm'}:
            return False
        job['retry_count'] += 1
        job['manual_resume'] = False
        job['deadline'] = now + 30
        if result['state'] == 'retry-submit':
            job['clicked'] = False
            job['fingerprint'] = result['fingerprint']
            job['tracking'] = result['tracking']
            job['confirmation_clicked'] = False
        else:
            job['confirmation_clicked'] = False
        self.emit('submitting', '已确认上次提交未发出或未被接受，正在重新尝试一次；原答案不变。')
        return True

    def _tick_submission(self):
        job = self.submission
        if not job:
            return
        page, frame, fingerprint = job["page"], job["frame"], job["fingerprint"]
        if page.is_closed() or not self._eligible(page) or not self._same_assignment(page):
            self.submission = None
            self.batch = False
            self.emit("error", "页面已切换，已停止提交任务。")
            return
        if job['probe'].outcome == 'accepted':
            self._confirm_submission(job)
            return
        if frame.is_detached():
            current = self.current(resolve_text=False)
            if not current or current[0] != page:
                self._submission_expired(job)
                return
            frame = job['frame'] = current[1]
        try:
            frame.evaluate(HOMEWORK_SCRIPT)
        except BrowserError:
            self._submission_expired(job)
            return
        if not job["clicked"]:
            if self._submission_expired(job):
                return
            try:
                result = frame.evaluate("payload => window.__yktHomework.submitCurrent(payload)", {
                    "fingerprint": fingerprint, "draft_fingerprint": job["draft_fingerprint"],
                    'expected_answer':job['answer'],
                })
            except BrowserError:
                # The click may have succeeded before its document was replaced.
                # Reconcile its saved baseline, without sending another click.
                job['clicked'] = True
                job['clicked_at'] = time.monotonic()
                self.attempted_submissions.add(job['key'])
                return
            state = result["state"]
            if state in {"submit-clicked", "already-requested"}:
                job["clicked"] = True
                job['clicked_at'] = time.monotonic()
                job['tracking'] = result.get('tracking') or job['tracking']
                self.attempted_submissions.add(job["key"])
                self.emit("submitting", "已点击本题提交，正在等待网页确认结果。")
            elif state == "security-verification":
                self._gate_verification(observed=True)
                return
            elif state == "submit-not-ready":
                return
            else:
                self.submission = None
                self.batch = False
                message='多选项或页面答案与待提交答案不一致，已停止提交；请核对完整选项。' if state=='answer-mismatch' else '本题答案、提交按钮或弹窗状态不符合提交条件，已停止提交。'
                self.emit('blocked',message)
            return
        state = frame.evaluate("p => window.__yktHomework.submissionStatus(p)",
                               {'fingerprint': fingerprint, 'tracking': job['tracking']})["state"]
        if state == "submitted":
            self._confirm_submission(job)
        elif self._resume_unsubmitted(job, state):
            return
        elif state == "current-submit-confirm":
            result = frame.evaluate("p => window.__yktHomework.confirmCurrentSubmission(p)",
                                    {'fingerprint':fingerprint,'expected_answer':job['answer']})
            if result["state"] == "security-verification":
                self._gate_verification(observed=True)
                return
            if result['state'] == 'confirmed-current':
                job['confirmation_clicked'] = True
            if result["state"] not in {"confirmed-current", "already-confirmed", "security-verification"}:
                self.submission = None
                self.batch = False
                self.emit("blocked", "未能确认仅提交本题，已停止；没有选择批量提交。")
        elif state == "security-verification":
            self._gate_verification(observed=True)
        elif state != "pending":
            if self._gate_verification():
                return
            self.submission = None
            self.batch = False
            self.emit("error", "本题提交失败或页面状态变化，已停止且不会重复提交，请在网页检查。")
        if self.submission is job:
            self._submission_expired(job)

    def _schedule_advance(self, question, delay):
        self.advance_question = question
        self.advance_at = (time.monotonic() + delay, question['fingerprint'])
        self.advance_ready_deadline = time.monotonic() + 20

    def _wait_next(self, fingerprint, question=None, guard=False):
        self.waiting_for = fingerprint
        self.waiting_question = question
        self.next_candidate = None
        self.wait_started = time.monotonic()
        self.guard_handled = guard
        self.navigation_retry_at = 0
        self.navigation_retry_used = False

    def _advance(self, frame, fingerprint):
        question = frame.evaluate('window.__yktHomework.read()')
        result = frame.evaluate("fingerprint => window.__yktHomework.navigateNext(fingerprint)", fingerprint)
        state = result["state"]
        if state in {"clicked-next", "continued-without-submit"}:
            self._wait_next(fingerprint, question, state == 'continued-without-submit')
            message = "已选择“先不用了”，正在等待下一题；没有提交答案。" if self.guard_handled else "已点击“下一题”，正在等待网页切换。"
            self.emit("switching", message)
        elif state == "end":
            self.batch = False
            suffix = f"已确认提交 {self.submitted_count} 题。" if self.auto_submit else "尚未提交。"
            self.emit("complete", f"下一题按钮已禁用，已到当前作业末尾；本次填写 {self.completed} 题，{suffix}")
        elif state == "security-verification":
            self.advance_at = (time.monotonic(), fingerprint)
            self._gate_verification(observed=True)
        elif state == 'next-not-ready':
            now = time.monotonic()
            if self.advance_ready_deadline is None:
                self.advance_ready_deadline = now + 20
            if now <= self.advance_ready_deadline:
                self.advance_at = (now + 0.5, fingerprint)
                self.emit('switching', '提交已确认，正在等待网页启用下一题；尚未到作业末尾。')
            else:
                self.batch = False
                self.emit('error', '目录仍有后续题目，但下一题尚不可用，已暂停；没有把本作业标为完成。')
        else:
            self.batch = False
            messages = {
                "missing-next": "未定位到下一题按钮，已停止；不会改点提交按钮。",
                "ambiguous-next": "找到多个下一题入口，已停止，请检查页面。",
                "blocked-dialog": "网页有待处理弹窗，已停止切题；没有提交答案。",
                "question-changed": "页面题目已切换，连续填写已停止。",
            }
            self.emit("blocked", messages.get(state, "网页尚未准备好切题，已停止。"))

    def cancel(self, quiet=False):
        if hasattr(self.owner, 'clear_verification_page'):
            self.owner.clear_verification_page()
        if self.verification_wait:
            self.owner.emit('attention', {'state':'verification-cleared'})
        self.pinned_page = None
        self.batch = False
        self.advance_at = None
        self.waiting_for = None
        self.guard_handled = False
        self.scan_until = 0
        self.discard = True
        self.deferred_solve = False
        self.verification_wait = None
        self.submission = None
        self.advance_question = None
        self.advance_ready_deadline = None
        self.waiting_question = None
        self.next_candidate = None
        self.navigation_retry_at = 0
        if self.pending:
            self.pending[0].cancel()
        if not quiet:
            self.emit("stopped", "已停止作业填写；已发出的模型请求返回后会被丢弃。")

    def tick(self):
        if self._gate_verification():
            return
        if self.deferred_solve and self.owner.mode == "homework":
            self.deferred_solve = False
            self._solve_current()
            return
        if self.pending:
            future, page, frame, question, url = self.pending
            if not future.done():
                now = time.monotonic()
                if now - self.last_progress >= 5:
                    self.last_progress = now
                    elapsed = int(now - self.pending_started)
                    self.emit("thinking", f"模型处理中，已等待 {elapsed} 秒（超时设置 {self.settings.timeout} 秒）；返回后将填写本题。")
                return
            if self.discard or self.owner.mode != "homework":
                self.pending = None
                self.emit("stopped", "已丢弃停止任务前的模型结果。")
                return
            answer = None
            try:
                answer = future.result()
                if page.is_closed() or urlparse(page.url)[:3] != urlparse(url)[:3] or not self._eligible(page):
                    raise ValueError("页面已切换，未填写旧题答案。")
                if self._gate_verification():
                    return
                if frame.is_detached():
                    current = self.current(resolve_text=False)
                    if not current or current[0] != page:
                        raise ValueError('原题所在页面尚未恢复，未填写旧答案。')
                    frame = current[1]
                    self.pending = (future, page, frame, question, url)
                frame.evaluate(HOMEWORK_SCRIPT)
                # If a verification appeared during a click, first check whether
                # the original fill completed. Never ask the model a second time.
                verified = self.fill_interrupted and frame.evaluate(
                    "payload => window.__yktHomework.verify(payload)",
                    {"fingerprint": question["fingerprint"], "answer": answer})
                if not verified:
                    self.fill_interrupted = True
                    verified = frame.evaluate("payload => window.__yktHomework.fill(payload)", {
                        "fingerprint": question["fingerprint"], "draft_fingerprint": question["draft_fingerprint"],
                        "answer": answer, "overwrite": self.overwrite,
                    })
                if self._gate_verification():
                    return
                if not verified:
                    raise ValueError("填写后读取结果不一致，已停止，请检查网页。")
                if self.auto_submit:
                    self._queue_submission(page, frame, question, answer)
                elif self.batch:
                    self._schedule_advance(question, 1.5)
                self.pending = None
                self.completed += 1
                self._learn(question,answer)
                self.emit("filled", f"已填写并核对第 {self.completed} 题。", question=question, answer=answer)
                if not self.batch and not self.auto_submit:
                    self._finish_learning()
            except Exception as exc:
                if self._gate_verification():
                    return
                self.pending = None
                self.batch = False
                message = str(exc) if isinstance(exc, ValueError) else "网页题面或控件已变化，请重新识别当前题。"
                # Show a valid answer even when a changed page prevented filling.
                extra = {"answer": answer} if answer is not None else {}
                self.emit("error", message, **extra)
            return
        if self.owner.mode != "homework":
            return
        if self.submission:
            self._tick_submission()
            return
        now = time.monotonic()
        if self.advance_at and now >= self.advance_at[0]:
            _, fingerprint = self.advance_at
            self.advance_at = None
            current = self.current(resolve_text=False)
            same_question = current and (current[2]['id'] == self.advance_question['id'] if self.advance_question else current[2]['fingerprint'] == fingerprint)
            if current and self._same_assignment(current[0]) and same_question:
                self._advance(current[1], current[2]['fingerprint'])
            elif self.auto_submit and self.last_confirmed and (not current or self._same_assignment(current[0])):
                self._wait_next(fingerprint, self.last_confirmed)
            elif self._gate_verification():
                self.advance_at = (now, fingerprint)
            else:
                self.batch = False
                self.emit("stopped", "页面题目已由其他操作切换，连续填写已停止。")
            return
        if self.waiting_for:
            current = self.current(resolve_text=False)
            if current and not self._same_assignment(current[0]):
                self.waiting_for = None
                self.batch = False
                self.emit("stopped", "已切换到另一份作业，连续填写已停止。")
            elif current:
                state = current[1].evaluate("() => window.__yktHomework.navigationState()")
                if state["state"] == "security-verification":
                    self._gate_verification(observed=True)
                    return
                if state["state"] == "unsubmitted-prompt":
                    if not self.guard_handled:
                        guard_fp = current[2]['fingerprint'] if self.waiting_question and current[2]['id'] == self.waiting_question['id'] else self.waiting_for
                        result = current[1].evaluate("fingerprint => window.__yktHomework.continueWithoutSubmit(fingerprint)", guard_fp)
                        if result["state"] == "continued-without-submit":
                            self.guard_handled = True
                            self.wait_started = now
                            self.emit("switching", "网页提示本题尚未提交，已选择“先不用了”继续下一题；没有提交答案。")
                        elif result["state"] == "security-verification":
                            self._gate_verification(observed=True)
                        else:
                            self.waiting_for = None
                            self.batch = False
                            self.emit("blocked", "未能关闭切题提醒，已停止；没有提交答案。")
                    elif now - self.wait_started > 20:
                        self.waiting_for = None
                        self.batch = False
                        self.emit("blocked", "切题提醒没有关闭，已停止；没有提交答案。")
                elif state["state"] != "ready":
                    self.waiting_for = None
                    self.batch = False
                    self.emit("blocked", "网页出现其他弹窗，已停止切题；没有提交答案。")
                elif (current[2]['id'] != self.waiting_question['id'] if self.waiting_question else current[2]['fingerprint'] != self.waiting_for):
                    question = current[2]
                    expected = (self.waiting_question or {}).get('index', 0)
                    if expected and question.get('index') != expected + 1:
                        if now - self.wait_started > 20:
                            self.waiting_for = None
                            self.batch = False
                            self.emit('stopped', '题号未按预期进入下一题，已暂停；未向其他题填写旧答案。')
                        return
                    key = (question['id'], question['fingerprint'])
                    loading_controls = ((question['read_only'] and not question['submitted'] and not question['answered']) or
                                        (question['unsupported'] and question['type'] != 'unsupported'))
                    if loading_controls and now - self.wait_started < 20:
                        # A new question can mount before its controls are enabled.
                        # Do not treat this transient disabled state as a finished or
                        # permanently read-only question.
                        return
                    if not self.next_candidate or self.next_candidate[0] != key:
                        self.next_candidate = (key, now)
                    elif now - self.next_candidate[1] >= 0.15:
                        self.waiting_for = None
                        self.waiting_question = None
                        self.next_candidate = None
                        self._solve_current()
                elif self.navigation_retry_at and now >= self.navigation_retry_at and not self.navigation_retry_used:
                    self.navigation_retry_used = True
                    self.navigation_retry_at = 0
                    result = current[1].evaluate('fp=>window.__yktHomework.navigateNext(fp)', current[2]['fingerprint'])
                    if result['state'] in {'clicked-next','continued-without-submit'}:
                        self.guard_handled = result['state'] == 'continued-without-submit'
                        self.wait_started = now
                        self.emit('switching', '核验后仍停留在原题，已重新尝试一次“下一题”，不会重复提交。')
                    elif result['state'] == 'security-verification':
                        self._gate_verification(observed=True)
                elif now - self.wait_started > 20:
                    if self._gate_verification():
                        return
                    self.waiting_for = None
                    self.batch = False
                    self.emit("error", "下一题没有完成切换，连续填写已停止；没有提交答案。")
            elif now - self.wait_started > 20:
                self.waiting_for = None
                self.batch = False
                self.emit("error", "下一题没有加载，连续填写已停止。")
            return
        if now < self.scan_until and now - self.last_scan > 2:
            self.last_scan = now
            if self.current(resolve_text=False):
                self.scan_until = 0
                self.inspect()
            elif self.page and self._eligible(self.page) and now - self.last_intro_click > 8:
                self.last_intro_click = now
                self.page.main_frame.evaluate(HOMEWORK_SCRIPT)
                self.page.main_frame.evaluate("() => window.__yktHomework.openQuestions()")

    def export(self):
        pages = [p for p in self.owner.context.pages if self._eligible(p)]
        if not pages:
            raise ValueError("请先打开作业详情页。")
        page = self.page if self.page in pages else pages[-1]
        samples = []
        for frame in page.frames:
            if allowed_url(frame.url):
                frame.evaluate(HOMEWORK_SCRIPT)
                sample = frame.evaluate("() => window.__yktHomework.sample()")
                if sample.get("question"):
                    try:
                        sample["rendered_question"] = self.reader.resolve(frame, sample["question"])
                    except ValueError as exc:
                        sample["ocr_error"] = str(exc)
                samples.append(sample)
        job = self.submission or next((value for value in reversed(self.submission_history.values()) if value['page'] == page), None)
        summary = {'question_id': job['question']['id'], 'clicked': job['clicked'], 'network': job['probe'].outcome,
                   'verification_seen': job['verification_seen'], 'retry_count': job['retry_count']} if job else None
        sample = {"frames": samples, "verification": verification_state(page), 'submission':summary}
        folder = RUNTIME / "homework-samples"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (time.strftime("%Y%m%d-%H%M%S") + ".json")
        path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
        self.emit("exported", f"题面样本已保存：{path}", path=str(path))

    def close(self):
        self.cancel(quiet=True)
        self.submission_monitor.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
