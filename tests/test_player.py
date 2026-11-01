import base64
from html import escape
from html.parser import HTMLParser
import io
from pathlib import Path
import queue
import unittest
import wave

from playwright.sync_api import sync_playwright

from yuketang_helper.config import Config
from yuketang_helper.engine import Engine, PLAYER_SCRIPT, NAVIGATION_SCRIPT

ROOT = Path(__file__).resolve().parent.parent


def media_url(seconds=4):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 8000 * seconds)
    return "data:audio/wav;base64," + base64.b64encode(buffer.getvalue()).decode()


MEDIA = media_url()
FIXTURE = '''<!doctype html><meta charset="utf-8">
<style>video { width: 320px; height: 180px } .hidden {display:none}</style>
<div class="learning-space-header__primary">我的课程</div>
<div class="leaf-item is-active"><span class="leaf-item-tag">视频</span><span class="leaf-item-title">视频一</span></div>
<div class="leaf-item" onclick="window.homeworkClicked = true"><span class="leaf-item-tag">作业</span><span class="leaf-item-title">作业一</span></div>
<div class="leaf-item" onclick="window.nextClicked = (window.nextClicked || 0) + 1"><span class="leaf-item-tag">视频</span><span class="leaf-item-title">视频二</span></div>
<div class="xt_video_player_container"><video class="xt_video_player"></video>
<li data-speed="2" onclick="window.speedClicked = true">2.00X</li></div>
<div class="el-dialog__wrapper hidden"><div role="dialog">请回答随堂问题</div></div>
'''


class LeafParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.items = []
        self.item = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "div":
            self.stack.append(classes)
            if "leaf-item" in classes:
                self.item = {"tag": "", "title": "", "active": "is-active" in classes}
                self.items.append(self.item)

    def handle_endtag(self, tag):
        if tag == "div" and self.stack:
            classes = self.stack.pop()
            if "leaf-item" in classes:
                self.item = None

    def handle_data(self, data):
        if self.item and self.stack:
            for field, classname in (("tag", "leaf-item-tag"), ("title", "leaf-item-title")):
                if classname in self.stack[-1]:
                    self.item[field] += data.strip()


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.set_content(FIXTURE)
        self.page.evaluate(PLAYER_SCRIPT)
        self.options = Config().player_options()

    def tearDown(self):
        self.context.close()

    def read(self):
        return self.page.evaluate("o => window.__yktHelper.read(o)", self.options)

    def set_ended(self):
        self.page.evaluate("Object.defineProperty(document.querySelector('video'), 'ended', {get: () => true, configurable: true})")

    def test_real_media_plays_at_twice_speed(self):
        self.page.locator("video").evaluate("(v, src) => v.src = src", MEDIA)
        self.page.wait_for_function("document.querySelector('video').readyState >= 3")
        self.read()
        self.page.wait_for_function("!document.querySelector('video').paused")
        first = self.read()
        self.page.wait_for_timeout(400)
        second = self.read()
        self.assertEqual(second["state"], "playing")
        self.assertEqual(second["rate"], 2)
        self.assertTrue(second["muted"])
        self.assertGreater(second["time"] - first["time"], 0.45)
        self.assertTrue(self.page.evaluate("window.speedClicked"))

    def test_modal_pauses_and_then_resumes(self):
        self.page.locator("video").evaluate("(v, src) => v.src = src", MEDIA)
        self.read()
        self.page.wait_for_function("!document.querySelector('video').paused")
        self.page.locator(".el-dialog__wrapper").evaluate("el => el.classList.remove('hidden')")
        self.assertEqual(self.read()["state"], "blocked")
        self.assertTrue(self.page.locator("video").evaluate("v => v.paused"))
        self.page.locator(".el-dialog__wrapper").evaluate("el => el.classList.add('hidden')")
        self.page.wait_for_timeout(3100)
        self.read()
        self.page.wait_for_function("!document.querySelector('video').paused")

    def test_next_skips_homework_and_clicks_once(self):
        self.set_ended()
        self.assertEqual(self.page.evaluate("window.__yktHelper.nextVideo(0)")["state"], "next-wait")
        self.assertEqual(self.page.evaluate("window.__yktHelper.nextVideo(0)")["state"], "next-clicked")
        self.assertEqual(self.page.evaluate("window.__yktHelper.nextVideo(0)")["state"], "next-pending")
        self.assertEqual(self.page.evaluate("window.nextClicked"), 1)
        self.assertFalse(self.page.evaluate("!!window.homeworkClicked"))
        # Vue may update the active row before the new media finishes loading.
        self.page.evaluate("document.querySelector('.is-active').classList.remove('is-active'); document.querySelectorAll('.leaf-item')[2].classList.add('is-active')")
        self.assertEqual(self.page.evaluate("window.__yktHelper.nextVideo(0)")["state"], "next-pending")

    def test_hidden_modal_does_not_block_and_visible_modal_prevents_next(self):
        self.set_ended()
        self.assertEqual(self.read()["state"], "ended")
        self.page.locator(".el-dialog__wrapper").evaluate("el => el.classList.remove('hidden')")
        self.assertEqual(self.page.evaluate("window.__yktHelper.nextVideo(0)")["state"], "not-ended")

    def test_no_early_next_and_directory_end(self):
        self.assertEqual(self.page.evaluate("window.__yktHelper.nextVideo(0)")["state"], "not-ended")
        self.set_ended()
        self.page.evaluate("document.querySelector('.is-active').classList.remove('is-active'); document.querySelectorAll('.leaf-item')[2].classList.add('is-active')")
        self.assertEqual(self.page.evaluate("window.__yktHelper.nextVideo(0)")["state"], "complete")

    def test_pause_resume_and_reinjection(self):
        self.page.evaluate("window.__yktHelper.pause()")
        self.page.evaluate(PLAYER_SCRIPT)
        self.assertEqual(self.read()["state"], "disabled")
        self.page.evaluate("window.__yktHelper.resume()")
        self.assertNotEqual(self.read()["state"], "disabled")

    def test_disabled_pause_recovery_stays_paused(self):
        self.options["resumePaused"] = False
        self.page.locator("video").evaluate("(v, src) => v.src = src", MEDIA)
        self.read()
        self.page.wait_for_function("!document.querySelector('video').paused")
        self.read()  # Register a genuine playing state before the manual pause.
        self.page.locator("video").evaluate("v => v.pause()")
        self.assertEqual(self.read()["state"], "paused")
        self.page.wait_for_timeout(100)
        self.assertTrue(self.page.locator("video").evaluate("v => v.paused"))
        self.page.evaluate("window.__yktHelper.pause(); window.__yktHelper.resume()")
        self.read()
        self.page.wait_for_function("!document.querySelector('video').paused")

    def test_playback_interruption_is_detected(self):
        self.page.evaluate("""() => {
          const v = document.querySelector('video');
          Object.defineProperty(v, 'paused', {get: () => false});
          Object.defineProperty(v, 'readyState', {get: () => 4});
        }""")
        self.read()
        self.options["stallSeconds"] = 0
        self.assertEqual(self.read()["state"], "stalled")

    def test_autoplay_failure_is_reported(self):
        self.page.evaluate("() => { document.querySelector('video').play = () => Promise.reject(new DOMException('blocked', 'NotAllowedError')); }")
        self.read()
        self.page.wait_for_timeout(20)
        self.assertEqual(self.read()["state"], "autoplay-blocked")

    def test_repeated_speed_reset_is_detected(self):
        self.page.evaluate("""() => {
          const v = document.querySelector('video');
          Object.defineProperty(v, 'playbackRate', {get: () => 1, set: () => {}});
          Object.defineProperty(v, 'paused', {get: () => false});
          Object.defineProperty(v, 'readyState', {get: () => 4});
          window.testNow = 0;
          performance.now = () => window.testNow;
        }""")
        for _ in range(5):
            self.page.evaluate("window.testNow += 1300")
            self.read()
        self.assertEqual(self.read()["state"], "speed-rejected")
        self.assertIn("当前实际 1 倍速", self.read()["detail"])

    def test_official_player_mouse_movement_gate_keeps_internal_speed(self):
        self.page.locator(".xt_video_player_container").evaluate("""el => {
          el.querySelector('li').remove();
          el.insertAdjacentHTML('beforeend', `<xt-speedbutton class="xt_video_player_speed">
            <xt-speedvalue>1.00X</xt-speedvalue><ul><li data-speed="2">2.00X</li></ul></xt-speedbutton>`);
          const control = el.querySelector('xt-speedbutton');
          const v = el.querySelector('video');
          let moved = false, x = 0, y = 0;
          window.platformSpeed = 1;
          control.addEventListener('mouseover', e => { x=e.clientX; y=e.clientY; });
          control.addEventListener('mousemove', e => { moved=Math.hypot(e.clientX-x, e.clientY-y)>0; });
          control.addEventListener('mouseout', () => { moved=false; });
          control.querySelector('li').addEventListener('click', e => {
            if (!moved) return;
            window.platformSpeed=Number(e.target.dataset.speed);
            control.querySelector('xt-speedvalue').textContent='2.00X';
            v.playbackRate=window.platformSpeed;
          });
          v.addEventListener('timeupdate', () => {
            if(v.playbackRate!==window.platformSpeed) v.playbackRate=window.platformSpeed;
          });
        }""")
        self.page.locator("video").evaluate("(v, src) => v.src = src", media_url(10))
        self.page.wait_for_function("document.querySelector('video').readyState >= 3")
        self.read()
        self.assertEqual(self.page.evaluate("window.platformSpeed"), 2)
        self.page.wait_for_timeout(1400)
        self.assertEqual(self.page.locator("video").evaluate("v => v.playbackRate"), 2)
        self.assertEqual(self.read()["state"], "playing")

    def test_speed_failure_does_not_prevent_autoplay(self):
        self.page.evaluate("""() => {
          const v = document.querySelector('video');
          Object.defineProperty(v, 'playbackRate', {get: () => 1, set: () => {}});
          window.playAttempts=0;
          v.play = () => { window.playAttempts++; return Promise.resolve(); };
          window.testNow=0; performance.now=() => window.testNow;
        }""")
        for _ in range(6):
            self.page.evaluate("window.testNow += 1300")
            self.read()
        self.assertGreater(self.page.evaluate("window.playAttempts"), 0)

    def test_next_is_not_clicked_until_delay(self):
        self.set_ended()
        self.page.evaluate("window.__yktHelper.nextVideo(5)")
        self.assertEqual(self.page.evaluate("window.__yktHelper.nextVideo(5)")["state"], "next-wait")
        self.assertFalse(self.page.evaluate("!!window.nextClicked"))

    def test_new_source_reuses_controller(self):
        self.page.locator("video").evaluate("(v, src) => v.src = src", MEDIA)
        self.read()
        self.page.wait_for_function("!document.querySelector('video').paused")
        self.page.evaluate("document.querySelector('video').src = ''")
        self.page.locator("video").evaluate("(v, src) => v.src = src", media_url(3))
        self.read()
        self.page.wait_for_function("!document.querySelector('video').paused")
        self.assertEqual(self.read()["rate"], 2)

    def test_loaded_real_source_directory(self):
        parser = LeafParser()
        parser.feed((ROOT / "源码.txt").read_text(encoding="utf-8"))
        videos = [item for item in parser.items if item["tag"] == "视频"]
        self.assertEqual(len(videos), 33)
        self.assertEqual(len(parser.items), 65)
        self.page.locator(".leaf-item").evaluate_all("els => els.forEach(el => el.remove())")
        for i, item in enumerate(parser.items):
            markup = f'<div class="leaf-item {"is-active" if item["active"] else ""}" data-index="{i}" onclick="window.selectedIndex = {i}"><div class="leaf-item-tag">{escape(item["tag"])}</div><div class="leaf-item-title">{escape(item["title"])}</div></div>'
            self.page.locator("body").evaluate("(el, html) => el.insertAdjacentHTML('beforeend', html)", markup)
        self.set_ended()
        self.assertEqual(self.read()["videoCount"], 33)
        self.page.evaluate("window.__yktHelper.nextVideo(0)")
        next_result = self.page.evaluate("window.__yktHelper.nextVideo(0)")
        self.assertEqual(next_result["state"], "next-clicked")
        selected = parser.items[self.page.evaluate("window.selectedIndex")]
        self.assertEqual(selected["tag"], "视频")
        self.assertEqual(selected["title"], videos[1]["title"])

    def test_respect_manual_homework_selection(self):
        self.page.locator("video").evaluate("v => v.remove()")
        self.page.evaluate("document.querySelector('.is-active').classList.remove('is-active'); document.querySelectorAll('.leaf-item')[1].classList.add('is-active')")
        self.assertFalse(self.page.evaluate("window.__yktHelper.firstVideo()"))

    def test_engine_does_not_operate_on_other_course_or_domain(self):
        self.context.route("**/*", lambda route: route.fulfill(body=FIXTURE.replace("我的课程", "其他课程"), content_type="text/html"))
        self.page.goto("https://www.yuketang.cn/test")
        engine = Engine(Config(), queue.Queue())
        engine.context = self.context
        engine._poll()
        self.assertFalse(self.page.evaluate("!!window.__yktHelper"))
        self.page.goto("https://example.com/test")
        engine._poll()
        self.assertFalse(self.page.evaluate("!!window.__yktHelper"))

    def test_engine_operates_on_selected_course(self):
        self.context.route("**/*", lambda route: route.fulfill(body=FIXTURE, content_type="text/html"))
        self.page.goto("https://www.yuketang.cn/test")
        engine = Engine(Config(), queue.Queue())
        engine.context = self.context
        engine._poll()
        self.assertTrue(self.page.evaluate("!!window.__yktHelper"))
        engine._set_paused(True)
        self.assertEqual(self.page.evaluate("window.__yktHelper.read({}).state"), "disabled")

    def test_engine_controls_only_one_course_tab(self):
        self.context.route("**/*", lambda route: route.fulfill(body=FIXTURE, content_type="text/html"))
        self.page.goto("https://www.yuketang.cn/first")
        other = self.context.new_page()
        other.goto("https://www.yuketang.cn/second")
        engine = Engine(Config(), queue.Queue())
        engine.context = self.context
        engine.active_page = self.page
        engine._poll()
        self.assertTrue(self.page.evaluate("!!window.__yktHelper"))
        self.assertFalse(other.evaluate("!!window.__yktHelper"))

    def test_course_card_with_split_whitespace_and_duplicate_title(self):
        self.page.set_content('''<div class="course-card" onclick="window.courseOpened=true">
          <div title="我的课程"><span>我的</span> <span>课程</span></div><p>2026 秋季</p></div>''')
        result = self.page.evaluate(NAVIGATION_SCRIPT, {"courseName": "我的课程"})
        self.assertEqual(result["state"], "course-clicked")
        self.assertTrue(self.page.evaluate("window.courseOpened"))

    def test_engine_redirects_dashboard_and_outline_to_video(self):
        target = "https://school.yuketang.cn/ai-workspace/lms-graph/100001/video/200001?is_chapter=1&node_id=300002"
        self.context.route("**/*", lambda route: route.fulfill(body="<p>已登录的课程门户</p>", content_type="text/html"))
        for initial in ("https://www.yuketang.cn/v2/web/index", "https://school.yuketang.cn/v2/web/studentLog/100001?university_id=100"):
            with self.subTest(initial=initial):
                self.page.goto(initial)
                engine = Engine(Config(course_url=target), queue.Queue())
                engine.context = self.context
                engine._poll()
                self.assertEqual(self.page.url, target)

    def test_direct_navigation_does_not_interrupt_login(self):
        self.context.route("**/*", lambda route: route.fulfill(body="<p>扫码登录</p>", content_type="text/html"))
        self.page.goto("https://school.yuketang.cn/web/")
        engine = Engine(Config(course_url="https://school.yuketang.cn/ai-workspace/lms-graph/1/video/2"), queue.Queue())
        engine.context = self.context
        engine._poll()
        self.assertEqual(self.page.url, "https://school.yuketang.cn/web/")

    def test_engine_finds_course_on_an_earlier_tab(self):
        body = '<div class="course-card" onclick="window.courseOpened=true"><span>我的课程</span></div>'
        self.context.route("**/*", lambda route: route.fulfill(body=body if 'index' in route.request.url else '<p>其他页</p>', content_type="text/html; charset=utf-8"))
        self.page.goto("https://school.yuketang.cn/v2/web/index")
        other = self.context.new_page()
        other.goto("https://school.yuketang.cn/web/")
        engine = Engine(Config(), queue.Queue())
        engine.context = self.context
        engine._poll()
        self.assertTrue(self.page.evaluate("window.courseOpened"))


if __name__ == "__main__":
    unittest.main()
