import tempfile
import time
import tkinter as tk
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from types import SimpleNamespace
from yuketang_helper.engine import Event
from yuketang_helper.gui import App
from yuketang_helper.config import Config
from yuketang_helper.llm import ModelSettings
from yuketang_helper.learning import LearningService, LearningStore
from yuketang_helper.page_components import Disclosure


class PageTransitionTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = LearningService(LearningStore(Path(self.temp.name)/'qa.db'))
        self.root = tk.Tk()
        self.root.withdraw()
        with patch('yuketang_helper.gui.Config.load', return_value=Config()), patch('yuketang_helper.homework_gui.ModelSettings.load', return_value=ModelSettings()), patch('yuketang_helper.gui.LearningService', return_value=self.service):
            self.app = App(self.root)

    def tearDown(self):
        self.app.homework_panel.close()
        self.service.close()
        self.root.destroy()
        self.temp.cleanup()

    def settle(self):
        end = time.monotonic()+.4
        while time.monotonic()<end:
            self.root.update()
            time.sleep(.01)
        self.root.update()

    def test_all_detail_pages_cancel_old_animation_and_preserve_settings(self):
        features = [self.app.course_page, self.app.video_page, self.app.logs_page,
                    self.app.homework_panel.model_feature, self.app.exam_panel.feature]
        self.app.vars['course_name'].set('保留的课程配置')
        for feature in features:
            feature.open_details()
            feature.close_details()
            feature.open_details()
        self.settle()
        for feature in features:
            self.assertEqual(feature.deck.select(), str(feature.detail))
            self.assertIsNone(feature.deck.transition_job)
            self.assertFalse(feature.front.place_info())
        self.assertEqual(self.app.vars['course_name'].get(), '保留的课程配置')
        self.assertIsNone(self.app.engine)

    def test_homework_details_and_disclosure_share_nonblocking_behavior(self):
        panel = self.app.homework_panel
        for _ in range(3):
            panel.toggle_details()
        fold = Disclosure(self.root, '测试详情', height=120)
        fold.set_open(True)
        fold.set_open(False)
        fold.set_open(True)
        self.settle()
        self.assertEqual(panel.workspace_switch.select(), str(panel.details))
        self.assertIsNone(panel.workspace_switch.transition_job)
        self.assertEqual(fold.progress, 120)
        self.assertIsNone(fold.job)
        self.assertIsNone(self.app.engine)

    def test_video_events_update_real_progress_and_preserve_configuration(self):
        self.app.vars['course_url'].set('https://example.test/course')
        self.app.engine = SimpleNamespace(mode='video', thread=SimpleNamespace(is_alive=lambda: True))
        self.app.events.put(Event('running', True))
        self.app.events.put(Event('status', {'state':'playing','time':30,'duration':120,'title':'模拟视频'}))
        self.app.poll()
        self.assertEqual(self.app.progress['value'], 25)
        self.assertFalse(self.app.video_page.content.winfo_manager())
        self.assertEqual(self.app.vars['course_url'].get(), 'https://example.test/course')
        self.app.events.put(Event('running', False))
        self.app.poll()
        self.assertEqual(self.app.video_page.content.winfo_manager(), 'pack')
        self.assertEqual(str(self.app.start_button.cget('state')), 'normal')
