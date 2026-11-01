import tkinter as tk
import unittest
from unittest.mock import patch

from yuketang_helper.gui import App


class UiShellTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)

    def tearDown(self):
        self.app.homework_panel.close()
        self.app.learning.close()
        self.root.destroy()

    def test_navigation_keeps_existing_pages_reachable(self):
        for key in ("course", "video", "homework", "model", "learning", "exam", "logs"):
            self.app.show_page(key)
            self.assertEqual(self.app.sidebar.selected, key)
            self.assertEqual(self.app.tabs.select(), str(self.app.pages[key]))
        self.assertEqual(self.app.homework_panel.tabs.select(), str(self.app.homework_panel.model_tab))

    def test_task_events_update_hero_and_bottom_detail_without_commands(self):
        panel = self.app.homework_panel
        commands = []
        panel.send = lambda *args: commands.append(args)
        panel.render({"state": "thinking", "message": "本题使用题目自带的答案（未调用模型）",
                      "question": {"type": "single", "stem": "测试题", "options": [{"id": "A", "text": "甲"}]},
                      "answer": {"option_ids": ["A"], "explanation": "说明"}})
        self.assertEqual(panel.hero_title.get(), "使用题目答案")
        panel.show_detail("识别结果")
        self.assertIn("答案：A", panel.detail_text.get("1.0", "end"))
        panel.show_detail("模型说明")
        self.assertIn("说明", panel.detail_text.get("1.0", "end"))
        panel.show_detail("模型说明")
        self.assertIsNone(panel.detail_kind)
        self.assertEqual(commands, [])

    def test_subtitles_start_hidden_and_narrow_navigation_collapses(self):
        item, glyph, title, subtitle = self.app.sidebar.entries["homework"]
        self.assertEqual(subtitle.winfo_manager(), "")
        self.app.sidebar._hover("homework", True)
        self.assertEqual(subtitle.winfo_manager(), "place")
        self.app.sidebar.set_auto(True)
        self.assertTrue(self.app.sidebar.collapsed)
        self.assertEqual(title.winfo_manager(), "")
        self.assertEqual(subtitle.winfo_manager(), "")

    def test_input_hint_does_not_become_homework_url(self):
        panel = self.app.homework_panel
        panel.vars["homework_url"].set("")
        self.assertEqual(panel.vars["homework_url"].get(), "")
        self.assertTrue(panel.input_card.placeholder_active)
        panel.input_card._focus_in(None)
        panel.input_card.display.set("https://example.test/task")
        self.assertEqual(panel.vars["homework_url"].get(), "https://example.test/task")

    def test_input_utilities_update_the_real_value_without_starting_task(self):
        panel = self.app.homework_panel
        with patch.object(panel.input_card, 'clipboard_get', return_value=' https://example.test/task '):
            panel.input_card.paste()
        self.assertEqual(panel.vars['homework_url'].get(), 'https://example.test/task')
        panel.input_card.clear()
        self.assertEqual(panel.vars['homework_url'].get(), '')
        self.assertIsNone(self.app.engine)

    def test_flow_uses_model_step_only_after_model_event_and_stops_activity(self):
        panel = self.app.homework_panel
        panel.render({'state':'recognizing','message':'识别中'})
        self.assertFalse(panel.steps.use_model)
        panel.render({'state':'thinking','message':'正在调用模型'})
        self.assertTrue(panel.steps.use_model)
        self.assertTrue(panel.hero_icon.active)
        panel.render({'state':'stopped','message':'已停止'})
        self.assertFalse(panel.hero_icon.active)
        self.assertIsNone(panel.hero_icon.animation_job)

    def test_workbench_routes_existing_read_and_solve_commands(self):
        panel = self.app.homework_panel
        commands = []
        panel.send = lambda *args: commands.append(args)
        panel.workbench.buttons[1].invoke()
        with patch.object(panel, 'solve') as solve:
            panel.workbench.buttons[2].invoke()
            panel.workbench.buttons[3].invoke()
            self.assertEqual([call.args for call in solve.call_args_list], [(False,), (True,)])
        self.assertEqual(commands, [('homework_read',)])
        panel.workbench.select('settings')
        self.assertEqual(panel.workbench.stack.select(), str(panel.workbench.pages['settings']))

    def test_hover_does_not_restart_when_moving_between_item_children(self):
        sidebar = self.app.sidebar
        sidebar._hover('homework', True)
        generation = sidebar.animation_generation['homework']
        sidebar._hover('homework', True)
        self.assertEqual(sidebar.animation_generation['homework'], generation)


if __name__ == "__main__":
    unittest.main()
