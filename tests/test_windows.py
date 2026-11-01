from types import SimpleNamespace
import queue
import unittest
from unittest.mock import Mock, patch

from yuketang_helper.config import Config
from yuketang_helper.engine import Engine
from yuketang_helper.windows import Windows, WindowTarget


class WindowMatchTests(unittest.TestCase):
    def setUp(self):
        self.windows=Windows()
        self.target=WindowTarget(123,456)

    def test_ambiguous_windows_are_not_guessed(self):
        candidates=[(self.target,(10,20,800,600),None),(WindowTarget(124,456),(10,20,800,600),None)]
        with patch.object(self.windows,'candidates',return_value=candidates):
            self.assertIsNone(self.windows.locate(456,{'left':10,'top':20,'width':800,'height':600}))

    def test_geometry_selects_only_matching_owned_window(self):
        candidates=[(self.target,(10,20,800,600),None),(WindowTarget(124,456),(1000,20,800,600),None)]
        with patch.object(self.windows,'candidates',return_value=candidates):
            self.assertEqual(self.windows.locate(456,{'left':10,'top':20,'width':800,'height':600}),self.target)

    def test_cached_window_from_wrong_process_is_not_reused(self):
        with patch.object(self.windows,'candidates',return_value=[]),patch.object(self.windows,'valid',return_value=True):
            self.assertIsNone(self.windows.locate(999,{},self.target))

    def test_invalid_target_is_not_activated(self):
        with patch.object(self.windows,'valid',return_value=False),patch.object(self.windows,'api') as api:
            self.assertFalse(self.windows.restore(self.target)['restored'])
            api.SetForegroundWindow.assert_not_called()


class WindowCommandTests(unittest.TestCase):
    def test_native_restore_relocates_stale_handle_without_browser_worker(self):
        target=WindowTarget(123,456)
        self.engine.browser_pid=456
        with patch.object(self.engine.windows,'valid',return_value=False),patch.object(self.engine.windows,'locate',return_value=target) as locate,patch.object(self.engine.windows,'restore',return_value={'restored':True,'foreground':True}) as restore:
            self.assertTrue(self.engine.restore_window_now()['restored'])
            locate.assert_called_once_with(456,self.engine.window_bounds)
            restore.assert_called_once_with(target)

    def setUp(self):
        self.engine=Engine(Config(),queue.Queue())
        self.page=Mock()
        self.page.is_closed.return_value=False
        self.page.url='https://school.yuketang.cn/ai-workspace/lms-graph/100001/video/1'
        self.engine.context=SimpleNamespace(pages=[self.page])
        self.engine.owned_page=self.page

    def test_closed_homework_target_falls_back_to_live_course_page(self):
        closed=Mock();closed.is_closed.return_value=True
        self.engine.mode='homework';self.engine.homework=SimpleNamespace(page=closed)
        self.engine.active_page=self.page
        with patch.object(self.engine,'window_state') as restore,patch.object(self.engine,'restore_window_now',return_value={'foreground':True}):
            self.engine.command('restore');self.engine._handle_commands()
            restore.assert_called_once_with(self.page,'normal')
            self.page.bring_to_front.assert_called_once()

    def test_active_verification_page_takes_precedence(self):
        other=Mock();other.is_closed.return_value=False
        self.engine.context.pages.append(other)
        self.engine.course_flow.active=True;self.engine.course_flow.page=other
        self.engine.verification_page=self.page
        with patch.object(self.engine,'window_state') as restore,patch.object(self.engine,'restore_window_now',return_value={'foreground':True}):
            self.engine.command('restore');self.engine._handle_commands()
            restore.assert_called_once_with(self.page,'normal')

    def test_repeated_clicks_coalesce_and_run_before_regular_commands(self):
        events=[]
        self.engine.command('options',Config())
        for _ in range(10):self.engine.command('restore')
        with patch.object(self.engine,'window_state',side_effect=lambda *_:events.append('restore')) as restore,patch.object(self.engine,'restore_window_now',return_value={'foreground':True}):
            self.engine._handle_commands()
            restore.assert_called_once()
        self.assertEqual(events,['restore'])
        self.assertIsNone(self.engine.pending_window_action)

    def test_no_browser_emits_visible_failure_result(self):
        self.engine.context.pages=[];self.engine.owned_page=None
        self.engine.command('restore');self.engine._handle_commands()
        result=self.engine.events.get_nowait()
        self.assertEqual(result.kind,'window_result')
        self.assertFalse(result.data['success'])
