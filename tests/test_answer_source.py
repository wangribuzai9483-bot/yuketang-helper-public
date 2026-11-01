"""Choice questions may use the platform-provided key instead of the model."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from tests.test_homework import HEADER, SINGLE
from yuketang_helper.config import Config
from yuketang_helper.homework import HOMEWORK_SCRIPT, HomeworkSession
from yuketang_helper.llm import ModelSettings

URL = 'https://school.yuketang.cn/ai-workspace/lms-graph/100001/exercise/100'


class AnswerSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.context.route("**/*", lambda route: route.fulfill(
            body=HEADER + SINGLE, content_type="text/html; charset=utf-8"))
        self.page.goto(URL)
        self.page.evaluate(HOMEWORK_SCRIPT)
        self.events = []
        self.addCleanup(self.context.close)

    def embed_answer(self, values, option_type=1, options=None):
        options = options or [{"key": "A", "value": "甲"}, {"key": "B", "value": "乙"}]
        self.page.evaluate('''payload => {
          const root = document.querySelector('.container-problem');
          root.__vue__ = {problem: {problem_id: 42, index: 1,
            content: {ProblemType: payload.type, Body: '《史记》的作者是谁？',
                      Options: payload.options, Answer: payload.values},
            user: {my_count: 0}}};
        }''', {"type": option_type, "options": options, "values": values})

    def owner(self, answer_source="auto"):
        owner = SimpleNamespace(
            context=self.context,
            config=Config(answer_source=answer_source,
                          course_url='https://school.yuketang.cn/ai-workspace/lms-graph/100001/video/1'),
            mode="homework", owned_page=self.page, events=self.events)
        owner.emit = lambda kind, data: self.events.append((kind, data))
        owner.set_verification_page = lambda page: None
        owner.clear_verification_page = lambda: None
        owner.window_state = lambda page, state: None
        owner._set_paused = lambda paused: None
        return owner

    def finish(self, session, deadline=8):
        until = time.monotonic() + deadline
        while time.monotonic() < until:
            session.tick()
            if session.completed:
                return
            self.page.wait_for_timeout(30)
        self.fail(f"did not fill the question: {self.events}")

    def states(self):
        return [data.get('state') for kind, data in self.events if kind == 'homework']

    def test_embedded_answer_is_filled_without_calling_the_model(self):
        self.embed_answer(["B"])
        session = HomeworkSession(self.owner("auto"))
        try:
            with patch("yuketang_helper.homework.ModelClient.solve",
                       side_effect=AssertionError("model must not be called")):
                session.start(ModelSettings(base_url="http://localhost:1", model="mock"))
                self.finish(session)
            self.assertTrue(self.page.locator('input[value="B"]').is_checked())
            self.assertEqual(self.page.locator('input[value="A"]').is_checked(), False)
            self.assertNotIn("error", self.states(), self.events)
            self.assertIn("thinking", self.states())
        finally:
            session.close()

    def test_model_source_ignores_the_embedded_key(self):
        self.embed_answer(["B"])
        session = HomeworkSession(self.owner("model"))
        try:
            with patch("yuketang_helper.homework.ModelClient.solve",
                       return_value={"option_ids": ["A"]}) as solver:
                session.start(ModelSettings(base_url="http://localhost:1", model="mock"))
                self.finish(session)
            self.assertEqual(solver.call_count, 1)
            self.assertTrue(self.page.locator('input[value="A"]').is_checked())
        finally:
            session.close()

    def test_embedded_only_mode_reports_a_question_without_a_key(self):
        session = HomeworkSession(self.owner("embedded"))
        try:
            with self.assertRaisesRegex(ValueError, "答案"):
                session.start(ModelSettings(base_url="http://localhost:1", model="mock"))
        finally:
            session.close()

    def test_key_formats_are_normalised(self):
        session = HomeworkSession(self.owner("auto"))
        try:
            judgment = {"type": "judgment", "options": [{"id": "true", "text": "正确"},
                                                        {"id": "false", "text": "错误"}]}
            self.assertEqual(session._map_embedded(judgment, ["0"]), {"option_ids": ["true"]})
            self.assertEqual(session._map_embedded(judgment, ["错误"]), {"option_ids": ["false"]})
            single = {"type": "single", "options": [{"id": "A", "text": "甲"}, {"id": "B", "text": "乙"}]}
            self.assertEqual(session._map_embedded(single, ["B"]), {"option_ids": ["B"]})
            self.assertEqual(session._map_embedded(single, ["1"]), {"option_ids": ["B"]})
            self.assertEqual(session._map_embedded(single, ["乙"]), {"option_ids": ["B"]})
            multiple = {"type": "multiple", "options": [{"id": "A", "text": "甲"}, {"id": "B", "text": "乙"},
                                                        {"id": "C", "text": "丙"}]}
            self.assertEqual(session._map_embedded(multiple, ["A", "C", "A"]), {"option_ids": ["A", "C"]})
            self.assertIsNone(session._map_embedded(multiple, ["Z"]))
            self.assertIsNone(session._map_embedded({"type": "text", "options": []}, ["B"]))
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
