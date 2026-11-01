"""Sequential course workflow, using the existing video and homework controllers."""
from dataclasses import replace
import json
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, urlencode, urlparse

from playwright.sync_api import Error as BrowserError

from .config import RUNTIME, allowed_url
from .homework import HOMEWORK_SCRIPT
from .llm import is_homework_url
from .course_identity import matches_course

COURSE_SCRIPT = Path(__file__).with_name('course.js').read_text(encoding='utf-8')


def scope(config):
    url = urlparse(config.course_url or config.login_url)
    match = re.search(r'/(?:lms-graph|studentLog)/(\d+)(?:/|$)', url.path)
    return (url.netloc, match[1] if match else '', config.course_name)


class CourseProgress:
    def __init__(self, path=None):
        self.path = path or RUNTIME / 'course-progress.json'

    def save(self, config, url, item):
        if not allowed_url(url):
            return
        parsed = urlparse(url)
        query = urlencode([(k, v) for k, v in parse_qsl(parsed.query) if k in {'node_id', 'is_chapter', 'newWeb'}])
        clean = parsed._replace(query=query, fragment='').geturl()
        data = {'scope': list(scope(config)), 'url': clean, 'kind': item['kind'], 'title': item['title']}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def load_url(self, config):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            url = data['url']
            if data['scope'] != list(scope(config)) or not allowed_url(url):
                return ''
            parsed = urlparse(url)
            expected_host, classroom, _ = scope(config)
            match = re.search(r'/lms-graph/(\d+)/(video|exercise|homework)/', parsed.path)
            if parsed.netloc != expected_host or not match or (classroom and match[1] != classroom):
                return ''
            return url
        except (OSError, ValueError, KeyError, TypeError):
            return ''


class CourseFlow:
    def __init__(self, owner, progress=None):
        self.owner = owner
        self.progress = progress or CourseProgress()
        self.active = False
        self.paused = False
        self.stage = 'idle'
        self.settings = None
        self.page = None
        self.item = None
        self.transition = None
        self.deadline = 0
        self.due = 0
        self.last_tick = 0
        self.paused_at = 0
        self.terminal = None
        self.first_wait = None
        self.first_guard_handled = False
        self.intro_at = 0
        self.last_message = None
        self.transient_since = None

    def emit(self, message):
        key = (self.stage, message)
        if key != self.last_message:
            self.last_message = key
            self.owner.emit('course', {'state': self.stage, 'message': message,
                                       'title': self.item['title'] if self.item else '',
                                       'kind': self.item['kind'] if self.item else ''})

    def on_homework(self, data):
        if self.terminal and self.terminal['state'] != 'complete':
            return
        if self.active and self.stage == 'homework' and self.settings.auto_submit and data['state'] == 'skipped':
            question = data.get('question', {})
            if question.get('read_only') and not question.get('submitted'):
                self.terminal = {'state': 'blocked', 'message': '本题只读但没有提交记录，无法确认作业完成，请手动检查。'}
                return
        if self.active and self.stage == 'homework' and data['state'] in {
            'complete', 'error', 'blocked', 'unsupported', 'stopped', 'waiting'
        }:
            self.terminal = data

    def start(self, settings):
        settings.validate(require_model=True)
        self.settings = replace(settings)
        if self.active and self.stage not in {'blocked', 'review', 'complete'}:
            if self.paused:
                self.toggle_pause()
            self.emit('统一课程流程已在运行，将沿用更新后的设置处理下一份作业。')
            return
        if self.active and self.stage == 'review':
            snap = self._snapshot()
            if snap and snap['current'] and snap['current']['key'] == self.item['key']:
                self.paused = False
                if self.settings.auto_submit:
                    self._arrive(self.item)
                else:
                    self._schedule_next()
                return
        self.active = True
        self.paused = False
        self.stage = 'waiting-course'
        self.page = None
        self.item = None
        self.transition = None
        self.terminal = None
        self.transient_since = None
        self.deadline = time.monotonic() + 300
        self.last_tick = time.monotonic()
        if self.owner.homework:
            self.owner.homework.cancel(quiet=True)
        self.owner.mode = 'video'
        self.owner._set_paused(False)
        self.emit('统一课程流程已启动；从当前目录项继续，等待登录或课程目录加载。')

    def stop(self, message='统一课程流程已停止，保留已完成的播放和填写结果。'):
        if not self.active:
            return
        self.active = False
        self.paused = False
        self.stage = 'stopped'
        self.terminal = None
        if self.owner.homework:
            self.owner.homework.cancel(quiet=True)
        self.owner._set_paused(True)
        self.emit(message)

    def _hold(self, message, stage='blocked'):
        self.stage = stage
        self.paused = True
        self.paused_at = time.monotonic()
        self.owner._set_paused(True)
        if self.owner.homework:
            self.owner.homework.cancel(quiet=True)
        self.emit(message)

    def toggle_pause(self):
        if self.stage in {'blocked', 'review', 'complete'}:
            self.emit('请点击“开始 / 继续课程流程”，从当前页面继续。')
            return
        now = time.monotonic()
        self.paused = not self.paused
        if self.paused:
            self.paused_at = now
        else:
            elapsed = now - self.paused_at
            self.deadline += elapsed
            self.due += elapsed
            self.owner.homework.extend_wait(elapsed)
        if self.owner.mode == 'video':
            self.owner._set_paused(self.paused)
        else:
            self.owner.emit('paused', self.paused)
        self.emit('统一课程流程已暂停。' if self.paused else '统一课程流程已继续。')

    def _eligible(self, page):
        if page.is_closed() or not allowed_url(page.url):
            return False
        host, classroom, _ = scope(self.owner.config)
        parsed = urlparse(page.url)
        if parsed.netloc != host:
            return False
        match = re.search(r'/lms-graph/(\d+)/', parsed.path)
        return bool(match and (not classroom or match[1] == classroom))

    def _snapshot(self):
        if self.page:
            if not self._eligible(self.page):
                self._hold('课程页面已关闭或离开目标课程，统一流程已暂停。')
                return None
            pages = [self.page]
        else:
            pages = [p for p in self.owner.context.pages if self._eligible(p)]
            for preferred in (self.owner.active_page, self.owner.owned_page):
                if preferred in pages:
                    pages.remove(preferred)
                    pages.insert(0, preferred)
        for page in pages:
            page.main_frame.evaluate(COURSE_SCRIPT)
            snap = page.main_frame.evaluate('() => window.__yktCourse.snapshot()')
            if matches_course(self.owner.config,page.url,snap['course']):
                if hasattr(self.owner,'_adopt_course_name'):self.owner._adopt_course_name(page)
                self.page = page
                if page.main_frame.evaluate('() => window.__yktCourse.expandOne()'):
                    return None
                if snap['ambiguous']:
                    self._hold('发现多个选中的目录项，无法确定课程顺序，请检查页面。')
                    return None
                return snap
        return None

    def _arrive(self, item):
        if item['kind'] == 'unsupported' or item['locked']:
            self._hold(f'当前项目“{item["title"]}”为讨论、考试、其他内容或尚未解锁，请手动处理后继续。')
            return
        self.item = item
        self.transition = None
        if item['kind'] == 'video':
            if '/video/' not in urlparse(self.page.url).path:
                return self._hold('目录选中了视频，但视频地址尚未匹配，请检查后继续。')
            self.stage = 'video'
            self.owner.mode = 'video'
            self.owner.active_page = self.page
            self.owner._set_paused(False)
            self.owner.emit('mode', 'video')
            self.emit(f'自动播放视频：{item["title"]}')
        else:
            if not is_homework_url(self.page.url):
                return self._hold('目录选中了作业，但作业地址尚未匹配，请检查后继续。')
            homework = self.owner.homework
            homework.cancel(quiet=True)
            homework.page = self.page
            homework.pinned_page = self.page
            homework.enter()
            self.stage = 'opening-homework'
            self.first_wait = None
            self.first_guard_handled = False
            self.intro_at = 0
            self.deadline = time.monotonic() + 90
            self.emit(f'正在进入作业并定位第一题：{item["title"]}')
        self.progress.save(self.owner.config, self.page.url, item)

    def observe_video(self, page, result):
        if not self.active or self.paused or self.stage != 'video' or page != self.page:
            return
        if result['state'] == 'ended':
            self._schedule_next()

    def _schedule_next(self):
        self.stage = 'advance-wait'
        self.due = time.monotonic() + self.owner.config.next_delay_seconds
        self.owner._set_paused(True)
        self.emit('当前项目处理完毕，准备按目录进入下一项。')

    def _navigate(self, result, before):
        state = result['state']
        if state == 'clicked':
            self.transition = {'target': result['item'], 'before': before}
            self.stage = 'navigating'
            self.deadline = time.monotonic() + 60
            self.emit(f'正在进入下一项：{result["item"]["title"]}')
        elif state == 'complete':
            self._hold('已到课程目录末尾；请在雨课堂查看实际完成记录。', 'complete')
        else:
            title = result.get('item', {}).get('title', '')
            messages = {'unsupported': f'下一项“{title}”为讨论、考试或不支持的内容，流程已暂停。',
                        'locked': f'下一项“{title}”尚未解锁，流程已暂停。',
                        'blocked-dialog': '网页有待处理弹窗，未切换目录；请处理后继续。'}
            self._hold(messages.get(state, '目录或导航状态发生变化，未重复点击；请检查后继续。'))

    def _prepare_homework(self, now):
        homework = self.owner.homework
        if homework._gate_verification():
            return
        if now > self.deadline:
            return self._hold('作业题面或第一题未能加载，流程已暂停；请检查后继续。')
        current = homework.current(resolve_text=False)
        if not current:
            if now >= self.intro_at:
                self.intro_at = now + 5
                for frame in self.page.frames:
                    if allowed_url(frame.url):
                        frame.evaluate(HOMEWORK_SCRIPT)
                        if frame.evaluate('() => window.__yktHomework.openQuestions()'):
                            break
            return
        _, frame, question = current
        frame.evaluate(COURSE_SCRIPT)
        state = frame.evaluate('() => window.__yktHomework.navigationState()')['state']
        if state == 'security-verification':
            homework._gate_verification(observed=True)
            return
        if state == 'unsubmitted-prompt' and self.first_wait and not self.first_guard_handled:
            result = frame.evaluate('fp => window.__yktHomework.continueWithoutSubmit(fp)', self.first_wait)
            if result['state'] == 'continued-without-submit':
                self.first_guard_handled = True
                return
            if result['state'] == 'security-verification':
                homework._gate_verification(observed=True)
                return
        if state != 'ready':
            if state == 'unsubmitted-prompt' and self.first_guard_handled:
                return
            return self._hold('作业页面有待处理弹窗，流程已暂停；请处理后继续。')
        if self.first_wait and question['fingerprint'] == self.first_wait:
            return
        result = frame.evaluate('() => window.__yktCourse.firstQuestion()')
        if result['state'] == 'clicked-first':
            self.first_wait = result['fingerprint']
            return
        if result['state'] != 'ready':
            return self._hold('无法可靠定位作业第一题，流程已暂停；请在网页选择第一题后继续。')
        if homework.pending:
            return
        self.stage = 'homework'
        self.terminal = None
        self.emit(f'正在逐题处理作业：{self.item["title"]}')
        homework.start(self.settings, batch=True, overwrite=False, auto_submit=self.settings.auto_submit)

    def tick(self):
        if not self.active or self.paused:
            return
        try:
            self._tick()
            self.transient_since = None
        except BrowserError:
            now = time.monotonic()
            if self.transient_since is None:
                self.transient_since = now
            if now - self.transient_since > 30:
                self._hold('课程页面持续无法读取，请检查页面后继续。')

    def _tick(self):
        now = time.monotonic()
        elapsed = max(0, now - self.last_tick) if self.last_tick else 0
        self.last_tick = now
        if not self.active or self.paused:
            return
        if self.page and not self._eligible(self.page):
            self._hold('课程页面已关闭或离开目标课程，统一流程已暂停。')
            return
        if self.owner.homework.verification_wait:
            self.deadline += elapsed
            self.due += elapsed
            return
        snap = self._snapshot()
        if not snap or self.paused:
            if not self.paused and now > self.deadline and self.stage in {'waiting-course', 'opening-homework', 'navigating'}:
                self._hold('等待登录、目录或下一项加载超时，请检查课程页面后继续。')
            return
        current = snap['current']
        if self.stage == 'waiting-course':
            if not current:
                if snap['items']:
                    before = self.page.url
                    self._navigate(self.page.main_frame.evaluate('() => window.__yktCourse.first()'), before)
                return
            self.page.main_frame.evaluate('() => window.__yktCourse.resetNavigation()')
            self._arrive(current)
        elif self.stage == 'navigating':
            target = self.transition['target']
            if current and current['key'] == target['key'] and self.page.url != self.transition['before']:
                self._arrive(current)
            elif now > self.deadline:
                self._hold('下一项未完成加载，已停止且不会重复点击目录；请检查后继续。')
        elif not current or current['key'] != self.item['key']:
            self._hold('目录选中项已被其他操作改变，统一流程已暂停；点击继续可从新位置开始。')
        elif self.stage == 'opening-homework':
            self._prepare_homework(now)
        elif self.stage == 'homework' and self.terminal:
            result, self.terminal = self.terminal, None
            if result['state'] == 'complete':
                if self.settings.auto_submit:
                    self._schedule_next()
                else:
                    self._hold('本作业已填写；未开启自动提交。请手动核对并提交，再点击“开始 / 继续课程流程”。', 'review')
            else:
                self._hold(f'作业流程已暂停：{result["message"]}')
        elif self.stage == 'advance-wait' and now >= self.due:
            before = self.page.url
            self._navigate(self.page.main_frame.evaluate('key => window.__yktCourse.advance(key)', self.item['key']), before)
