"""Explicit course pipeline check in a temporary minimized, unsigned-in Edge."""
from pathlib import Path
import queue
import tempfile
import time

from playwright.sync_api import sync_playwright

from tests.test_course import FIXTURE, URL
from tests.test_llm import MockServer
from yuketang_helper.config import Config
from yuketang_helper.course import CourseProgress
from yuketang_helper.engine import Engine
from yuketang_helper.homework import HomeworkSession
from yuketang_helper.llm import ModelSettings


def main():
    with sync_playwright() as playwright, tempfile.TemporaryDirectory() as folder:
        browser = playwright.chromium.launch(channel='msedge', headless=False, args=[
            '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
            '--disable-backgrounding-occluded-windows',
        ])
        try:
            context = browser.new_context()
            fixture = FIXTURE.replace('<div class="problem-body">',
                '<div class="problem-body xuetangx-com-encrypted-font" style="font:16px Microsoft YaHei;line-height:24px">')
            fixture = fixture.replace('${id}的第${index}题', '中国古典文学中的抒情传统与叙事传统有什么区别？第${index}题')
            context.route('**/*', lambda route: route.fulfill(body=fixture, content_type='text/html; charset=utf-8'))
            page = context.new_page()
            page.goto(URL)
            page.evaluate('window.intro=true')
            engine = Engine(Config(course_url=URL), queue.Queue())
            engine.context = context
            engine.owned_page = page
            engine.active_page = page
            engine.homework = HomeworkSession(engine)
            flow = engine.course_flow
            flow.progress = CourseProgress(Path(folder) / 'progress.json')
            try:
                engine.window_state(page, 'minimized')
                with MockServer() as server:
                    flow.start(ModelSettings(base_url=server.url, model='mock', auto_submit=True))
                    until = time.monotonic() + 40
                    while time.monotonic() < until and flow.stage not in {'complete','blocked'}:
                        engine.homework.tick()
                        flow.tick()
                        if engine.mode == 'video' and not engine.paused:
                            page.evaluate('window.forceEnded=true')
                            engine._poll()
                        if flow.stage == 'advance-wait':
                            flow.due = 0
                        if engine.homework.advance_at:
                            engine.homework.advance_at = (0, engine.homework.advance_at[1])
                        page.wait_for_timeout(30)
                    assert flow.stage == 'complete', flow.last_message
                    assert page.evaluate('window.navClicks') == [1,2,3,4]
                    assert page.evaluate('window.submits') == ['200-1','200-2','201-1','201-2']
                    assert len(server.requests) == 4
                cdp = context.new_cdp_session(page)
                try:
                    assert cdp.send('Browser.getWindowForTarget')['bounds']['windowState'] == 'minimized'
                finally:
                    cdp.detach()
                print('Minimized Edge course pipeline OK: 3 videos, 2 assignments, 4 Windows OCR/model/fill/submit cycles.')
            finally:
                engine.homework.close()
                context.close()
        finally:
            browser.close()


if __name__ == '__main__':
    main()
