"""Sanitized studentLog DOM shapes; no saved pages or real user sessions."""
import queue
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

from tests.test_player import FIXTURE, media_url
from yuketang_helper.config import Config
from yuketang_helper.engine import Engine, OVERVIEW_SCRIPT

HOST = 'https://school.yuketang.cn'
OVERVIEW = HOST + '/v2/web/studentLog/200?classroom_id=200'
VIDEO = HOST + '/ai-workspace/lms-graph/200/video/20'


def leaf(label='视频', action='window.videoClicked=(window.videoClicked||0)+1'):
    icon = 'shipin' if label == '视频' else 'kaoshi'
    return f'<section class="activity__wrap" onclick="{action}"><span class="tag"><svg><use href="#icon-{icon}"></use></svg></span><h2>示例{label}</h2></section>'


def chapter(completed=False, action='openChapter(this)', extra=''):
    return f'''<div class="content-box {extra}"><section class="activity__wrap">
      <div class="activity-info"><h2>示例章节</h2><div class="sub-info">
      <span class="gray">共计2个学习单元 <span class="blue ml20" onclick="{action}">展开</span></span></div></div>
      <div class="statistics-box">{'已完成' if completed else '未完成'}</div></section>
      <section class="leaf_list__wrap" style="display:none"><div class="loading-wrapper">加载中</div></section></div>'''


CHAPTER_SCRIPT = '''<script>function openChapter(el) {
  window.expanded=(window.expanded||0)+1;
  const region=el.closest('.content-box').querySelector('.leaf_list__wrap');
  region.style.display='block';el.textContent='收起';region.innerHTML=window.leafMarkup;
}</script>'''


class OverviewEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.p.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.context.route('**/*', lambda route: route.fulfill(body='<meta charset="utf-8">', content_type='text/html; charset=utf-8'))
        self.page = self.context.new_page(); self.page.goto(OVERVIEW)
        self.engine = Engine(Config(course_name='之前的课程名称', course_url=OVERVIEW), queue.Queue())
        self.engine.context = self.context

    def tearDown(self):
        self.context.close()

    def load(self, body, leaves=None):
        self.page.set_content('<meta charset="utf-8">' + body + CHAPTER_SCRIPT)
        self.page.evaluate('(markup)=>window.leafMarkup=markup', leaves or leaf())

    def read(self):
        return self.page.evaluate(OVERVIEW_SCRIPT)

    def test_configured_overview_expands_then_opens_video_popup_and_plays(self):
        self.load(chapter(), leaf('考试', 'window.examOpened=true') + leaf(action=f"window.open('{VIDEO}')"))
        self.context.route('**/video/20', lambda route: route.fulfill(
            body=FIXTURE, content_type='text/html; charset=utf-8'))
        self.engine._poll()
        self.assertEqual(self.page.evaluate('window.expanded'), 1)
        with self.context.expect_page() as opened:
            self.engine._poll()
        video = opened.value; video.wait_for_load_state()
        self.assertEqual(video.url, VIDEO)
        video.locator('video').evaluate('(v,src)=>v.src=src', media_url(8))
        self.engine._poll()
        video.wait_for_function('!document.querySelector("video").paused')
        self.assertEqual(video.locator('video').evaluate('v=>v.playbackRate'), 2)
        self.assertFalse(self.page.evaluate('!!window.examOpened'))

    def test_pending_chapter_does_not_expand_other_chapters(self):
        self.load(chapter(action="window.started=true;this.closest('.content-box').querySelector('.leaf_list__wrap').style.display='block'") + chapter())
        self.assertEqual(self.read()['state'], 'overview-expanded')
        self.assertEqual(self.read()['state'], 'overview-loading')
        self.assertFalse(self.page.evaluate('!!window.expanded'))
        self.page.locator('.leaf_list__wrap').first.evaluate('(el,html)=>el.innerHTML=html', leaf())
        self.assertEqual(self.read()['state'], 'overview-video-clicked')

    def test_unfinished_chapter_is_preferred(self):
        self.load(chapter(completed=True) + chapter())
        self.read()
        self.assertEqual(self.page.locator('.blue').nth(0).inner_text(), '展开')
        self.assertEqual(self.page.locator('.blue').nth(1).inner_text(), '收起')

    def test_hidden_and_disabled_video_rows_are_not_clicked(self):
        self.load('<div style="display:none" class="leaf_list__wrap">' + leaf(action='window.bad=true') + '</div>'
                  + '<div class="content-box-disable"><div class="leaf_list__wrap">' + leaf(action='window.bad=true') + '</div></div>'
                  + chapter())
        self.read(); self.read()
        self.assertFalse(self.page.evaluate('!!window.bad'))
        self.assertEqual(self.page.evaluate('window.videoClicked'), 1)

    def test_all_visible_videos_disabled_reports_course_state(self):
        disabled = '<div class="leaf_list__wrap"><section class="activity__wrap activity__wrap-disable">' \
            '<span class="tag"><svg><use href="#icon-shipin"></use></svg></span>' \
            '<div class="sub-info">考核截止时间：2026-06-28/23:59</div></section></div>'
        self.load(disabled)
        self.assertEqual(self.read()['state'], 'overview-locked')
        self.assertIn('2026-06-28', self.read()['title'])
        self.engine._poll()
        statuses = [e.data for e in list(self.engine.events.queue) if e.kind == 'status']
        self.assertEqual(statuses[-1]['state'], 'blocked')

    def test_saved_overview_dom_reports_disabled_videos_when_available(self):
        source = Path(__file__).resolve().parents[1] / '可能是.txt'
        if not source.exists():self.skipTest('local saved page was not provided')
        self.page.set_content(source.read_text(encoding='utf-8-sig'))
        # The saved outer HTML keeps the learning-log list under display:none.
        # The visible learning-content iframe is a separate document.
        self.assertEqual(self.read()['state'], 'overview-frame')
        self.page.evaluate('window.__yktOverviewEntry.waitingSince-=21000')
        self.assertEqual(self.read()['state'], 'overview-attention')

    def test_video_popup_is_not_reopened_while_waiting(self):
        self.load('<div class="leaf_list__wrap">' + leaf() + '</div>')
        self.read(); self.read(); self.read()
        self.assertEqual(self.page.evaluate('window.videoClicked'), 1)
        self.page.evaluate('window.__yktOverviewEntry.pending.at-=21000')
        self.assertEqual(self.read()['state'], 'overview-attention')
        self.assertEqual(self.page.evaluate('window.videoClicked'), 1)

    def test_learning_content_tab_and_visible_same_course_iframe(self):
        self.context.route('**/studycontent?*', lambda route: route.fulfill(
            body='<div class="leaf-item" onclick="window.videoClicked=true"><span class="leaf-item-tag">视频</span></div>',
            content_type='text/html; charset=utf-8'))
        self.load('''<div class="rain-tabs__nav-item" onclick="this.classList.add('rain-tabs__nav-item--active');document.querySelector('iframe').style.display='block'">
            <span class="rain-tabs__nav-item-text">学习内容</span></div>
            <iframe style="display:none" src="/pro/lms/demo/200/studycontent?classroom_id=200"></iframe>''')
        self.page.frames[1].wait_for_load_state()
        self.engine._poll(); self.engine._poll()
        self.assertTrue(self.page.frames[1].evaluate('!!window.videoClicked'))

    def test_pro_learning_content_video_icon_opens_player(self):
        self.context.route('**/studycontent?*', lambda route: route.fulfill(
            body=f'''<div class="chapter-list"><div class="leaf-detail" onclick="window.examClicked=true">
              <div class="leaf-title"><i class="iconfont icon--kaoshi"></i></div></div>
              <div class="leaf-detail" onclick="window.open('{VIDEO}')">
              <div class="leaf-title"><i class="iconfont icon--shipin"></i><span class="title">演示视频</span></div></div></div>''',
            content_type='text/html; charset=utf-8'))
        self.context.route('**/video/20', lambda route: route.fulfill(body=FIXTURE, content_type='text/html; charset=utf-8'))
        self.load('''<div class="rain-tabs__nav-item rain-tabs__nav-item--active"><span class="rain-tabs__nav-item-text">学习内容</span></div>
            <iframe src="/pro/lms/demo/200/studycontent?classroom_id=200"></iframe>''')
        self.page.frames[1].wait_for_load_state()
        with self.context.expect_page() as opened:self.engine._poll()
        self.assertEqual(opened.value.url, VIDEO)
        self.assertFalse(self.page.frames[1].evaluate('!!window.examClicked'))

    def test_pro_learning_content_disabled_videos_report_blocked(self):
        self.context.route('**/studycontent?*', lambda route: route.fulfill(
            body='''<div class="leaf-detail leaf-detail-disable" onclick="window.bad=true">
              <div class="leaf-title"><i class="iconfont icon--shipin"></i></div></div>''',
            content_type='text/html; charset=utf-8'))
        self.load('''<div class="rain-tabs__nav-item rain-tabs__nav-item--active"><span class="rain-tabs__nav-item-text">学习内容</span></div>
            <iframe src="/pro/lms/demo/200/studycontent?classroom_id=200"></iframe>''')
        self.page.frames[1].wait_for_load_state()
        self.engine._poll()
        statuses=[e.data for e in list(self.engine.events.queue) if e.kind=='status']
        self.assertEqual(statuses[-1]['state'],'blocked')
        self.assertFalse(self.page.frames[1].evaluate('!!window.bad'))

    def test_hidden_or_other_course_iframe_is_ignored(self):
        self.context.route('**/studycontent?*', lambda route: route.fulfill(
            body='<div class="leaf-item" onclick="window.bad=true"><span class="leaf-item-tag">视频</span></div>',
            content_type='text/html; charset=utf-8'))
        self.load('''<iframe src="/pro/lms/demo/300/studycontent?classroom_id=300"></iframe>
            <iframe style="display:none" src="/pro/lms/demo/200/studycontent?classroom_id=200"></iframe>''')
        for frame in self.page.frames[1:]:frame.wait_for_load_state()
        self.engine._poll()
        for frame in self.page.frames[1:]:self.assertFalse(frame.evaluate('!!window.bad'))

    def test_modal_pauses_navigation_and_removal_resumes(self):
        self.load('<div role="dialog">请手动处理</div>' + chapter())
        self.assertEqual(self.read()['state'], 'overview-blocked')
        self.assertFalse(self.page.evaluate('!!window.expanded'))
        self.page.locator('[role="dialog"]').evaluate('el=>el.remove()')
        self.assertEqual(self.read()['state'], 'overview-expanded')

    def test_no_video_in_loaded_chapter_tries_next_without_opening_quiz(self):
        self.load(chapter() + chapter(), leaf('考试', 'window.bad=true'))
        self.read(); self.read()
        self.assertEqual(self.page.evaluate('window.expanded'), 2)
        self.assertFalse(self.page.evaluate('!!window.bad'))

    def test_other_classroom_redirects_before_interacting(self):
        self.load(chapter())
        self.engine.config.course_url = OVERVIEW.replace('/200?', '/300?').replace('id=200', 'id=300')
        self.engine._poll()
        self.assertEqual(self.page.url, self.engine.config.course_url)
        self.assertFalse(self.page.evaluate('!!window.expanded'))

    def test_name_only_mode_does_not_enter_unrelated_overview(self):
        self.load('''<div class="header-main"><h1 class="title-line"><div class="classroom-ellipsis">
          <div class="title-inner-wrapper">另一门课</div></div></h1></div>''' + chapter())
        self.engine.config.course_url = ''
        self.engine._poll()
        self.assertFalse(self.page.evaluate('!!window.expanded'))
        self.engine.config.course_name = '另一门课'
        self.engine._poll()
        self.assertEqual(self.page.evaluate('window.expanded'), 1)

    def test_video_links_must_belong_to_current_classroom(self):
        self.load('''<a href="/ai-workspace/lms-graph/300/video/30" onclick="event.preventDefault();window.bad=true">其他课程视频</a>
          <a href="https://other.yuketang.cn/ai-workspace/lms-graph/200/video/20" onclick="event.preventDefault();window.bad=true">其他学校视频</a>
          <a href="/ai-workspace/lms-graph/200/video/20" onclick="event.preventDefault();window.good=true">本课程视频</a>''')
        self.assertEqual(self.read()['state'], 'overview-video-clicked')
        self.assertTrue(self.page.evaluate('!!window.good'))
        self.assertFalse(self.page.evaluate('!!window.bad'))


if __name__ == '__main__':
    unittest.main()
