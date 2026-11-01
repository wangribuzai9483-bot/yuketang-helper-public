import queue
from dataclasses import replace
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import sync_playwright

from tests.test_player import FIXTURE
from yuketang_helper.config import Config
from yuketang_helper.engine import Engine, Event
from yuketang_helper.gui import App
from yuketang_helper.llm import ModelSettings


class BrowserRecoveryUiTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
        with patch('yuketang_helper.gui.Config.load',return_value=Config()), \
             patch('yuketang_helper.homework_gui.ModelSettings.load',return_value=ModelSettings()):
            self.app=App(self.root)

    def tearDown(self):
        self.app.homework_panel.close();self.app.learning.close();self.root.destroy()

    def test_stop_without_live_browser_restores_start_controls(self):
        self.app.stop();self.app.poll()
        self.assertNotIn('正在关闭',self.app.status.get())
        self.assertEqual(str(self.app.start_button.cget('state')),'normal')
        self.assertTrue(self.app.start_button.winfo_manager())

    def test_missing_final_event_is_reconciled_from_finished_worker(self):
        from yuketang_helper.application_pages import set_video_active
        self.app.engine=SimpleNamespace(thread=SimpleNamespace(is_alive=lambda:False),mode='video',command=lambda *_:None)
        self.app.starting=True
        self.app.status.set('正在关闭浏览器…');set_video_active(self.app,True)
        self.app.poll()
        self.assertNotIn('正在关闭',self.app.status.get())
        self.assertTrue(self.app.start_button.winfo_manager())

    def test_previous_worker_event_cannot_stop_new_browser(self):
        old=object()
        self.app.engine=SimpleNamespace(thread=SimpleNamespace(is_alive=lambda:True),mode='video')
        self.app.browser_running=True
        self.app.events.put(Event('running',False,old))
        self.app.poll()
        self.assertTrue(self.app.browser_running)

    def test_link_change_is_applied_without_restarting_browser(self):
        old=Config(course_url='https://school.yuketang.cn/ai-workspace/lms-graph/100/video/10')
        self.app.config=old
        for key in ('login_url','course_url','course_name','browser'):self.app.vars[key].set(getattr(old,key))
        self.app.engine=SimpleNamespace(thread=SimpleNamespace(is_alive=lambda:True),command=Mock())
        self.app.vars['course_url'].set(old.course_url.replace('/100/','/200/'))
        with patch.object(Config,'save'):
            self.assertTrue(self.app.apply())
        self.assertEqual(self.app.engine.command.call_args.args[0],'options')

    def test_stop_signal_does_not_wait_for_browser_command_queue(self):
        engine=Engine(Config(),queue.Queue())
        with patch.object(engine.finished,'wait',return_value=True):
            engine.command('stop')
            self.assertTrue(engine.stop.is_set())

    def test_owned_browser_watchdog_never_targets_other_processes(self):
        engine=Engine(Config(),queue.Queue())
        engine.browser_process=Mock()
        with patch.object(engine.finished,'wait',return_value=False):engine._stop_watchdog()
        engine.browser_process.terminate.assert_called_once_with()

    def test_session_cleanup_error_still_reports_worker_finished(self):
        engine=Engine(Config(),queue.Queue())
        context=Mock();context.pages=[Mock()]
        playwright=Mock();playwright.chromium.launch_persistent_context.return_value=context
        manager=Mock();manager.__enter__=Mock(return_value=playwright);manager.__exit__=Mock(return_value=False)
        exam=Mock();exam.close.side_effect=OSError('cleanup error')
        engine.profile_lock=Mock()
        with patch('playwright.sync_api.sync_playwright',return_value=manager), \
             patch('yuketang_helper.homework.HomeworkSession',return_value=Mock()), \
             patch('yuketang_helper.exam.ExamSession',return_value=exam), \
             patch.object(engine,'_acquire_lock'),patch.object(engine,'remember_window'), \
             patch.object(engine,'_open_entry',side_effect=engine.stop.set):
            engine._run()
        events=[]
        while not engine.events.empty():events.append(engine.events.get())
        self.assertTrue(engine.finished.is_set())
        self.assertEqual([event.data for event in events if event.kind=='running'][-1],False)
        engine.profile_lock.close.assert_called_once()


class CourseIdentificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=sync_playwright().start();cls.browser=cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.p.stop()

    def setUp(self):
        self.context=self.browser.new_context();self.page=self.context.new_page()
        self.context.route('**/*',lambda route:route.fulfill(body=FIXTURE.replace('我的课程','课程乙'),content_type='text/html; charset=utf-8'))
        self.page.goto('https://school.yuketang.cn/ai-workspace/lms-graph/200/video/20')

    def tearDown(self):self.context.close()

    def test_link_identity_accepts_new_course_with_old_display_name(self):
        engine=Engine(Config(course_name='课程甲',course_url=self.page.url),queue.Queue())
        engine.context=self.context;engine._poll()
        self.assertTrue(self.page.evaluate('!!window.__yktHelper'))
        self.assertEqual(engine.config.course_name,'课程乙')

    def test_different_classroom_is_not_controlled_even_with_same_title(self):
        engine=Engine(Config(course_name='课程乙',course_url=self.page.url.replace('/200/','/100/')),queue.Queue())
        engine.context=self.context;engine._poll()
        self.assertFalse(self.page.evaluate('!!window.__yktHelper'))

    def test_current_course_can_be_switched_inside_existing_browser(self):
        engine=Engine(Config(course_name='课程甲',course_url=self.page.url.replace('/200/','/100/')),queue.Queue())
        engine.context=self.context;engine.owned_page=self.page
        engine.command('options',replace(engine.config,course_url=self.page.url))
        engine._handle_commands();engine._poll()
        self.assertEqual(engine.config.course_name,'课程乙')
        self.assertTrue(self.page.evaluate('!!window.__yktHelper'))
        self.assertFalse(engine.paused)


class OwnedBrowserProcessTests(unittest.TestCase):
    def kernel(self, executable):
        api=Mock()
        api.OpenProcess.return_value=55
        def path(handle,flags,buffer,size):buffer.value=executable;return True
        def exit_code(handle,code):code._obj.value=259;return True
        api.QueryFullProcessImageNameW.side_effect=path
        api.GetExitCodeProcess.side_effect=exit_code
        api.TerminateProcess.return_value=True
        return api

    def test_handle_validation_refuses_non_browser_and_releases_handle(self):
        from yuketang_helper.browser_process import OwnedBrowserProcess
        api=self.kernel(r'C:\Windows\notepad.exe')
        with patch('yuketang_helper.browser_process.ctypes.WinDLL',return_value=api):process=OwnedBrowserProcess(123)
        self.assertFalse(process.terminate())
        api.TerminateProcess.assert_not_called();api.CloseHandle.assert_called_once_with(55)

    def test_termination_uses_original_handle_and_is_disabled_after_close(self):
        from yuketang_helper.browser_process import OwnedBrowserProcess
        api=self.kernel(r'C:\Browser\msedge.exe')
        with patch('yuketang_helper.browser_process.ctypes.WinDLL',return_value=api):process=OwnedBrowserProcess(123)
        self.assertTrue(process.terminate())
        api.TerminateProcess.assert_called_once_with(55,1)
        process.close();self.assertFalse(process.terminate())
