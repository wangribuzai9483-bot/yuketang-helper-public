from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from yuketang_helper.exam_gui import ExamPanel
from yuketang_helper.homework_gui import HomeworkPanel
from yuketang_helper.llm import ModelSettings

EXAM_URL = 'https://school.yuketang.cn/quiz/example?is_chapter=1&newWeb=1'


class ExamGuiTests(unittest.TestCase):
    def test_model_settings_preserve_exam_url_and_commands_never_submit(self):
        root=tk.Tk();root.withdraw()
        commands=[]
        with patch('yuketang_helper.homework_gui.ModelSettings.load',return_value=ModelSettings(exam_url=EXAM_URL)), \
             patch.object(ModelSettings,'save'):
            model=HomeworkPanel(root,lambda _:None,lambda *_:None)
            exam=ExamPanel(root,model,lambda url:None,lambda *args:commands.append(args))
            try:
                self.assertEqual(model.read_settings().exam_url,EXAM_URL)
                exam.answer.insert('1.0','{"option_ids":["A","C"]}')
                exam.index.set(3);exam.fill()
                self.assertEqual(commands[0],('exam_fill',{'answer':{'option_ids':['A','C']},'index':2,'overwrite':False}))
                with patch.object(model,'save',return_value=True):exam.solve(True)
                self.assertEqual(commands[-1][0],'exam_solve')
                self.assertNotIn('auto_submit',commands[-1][1])
            finally:model.close();root.destroy()

    def test_exam_url_persists_and_repeated_navigation_parameters_are_cleaned(self):
        root=tk.Tk();root.withdraw()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json'
            with patch('yuketang_helper.homework_gui.ModelSettings.load',return_value=ModelSettings()), \
                 patch.object(ModelSettings,'save',lambda settings: path.write_text(__import__('json').dumps(settings.__dict__),encoding='utf-8')):
                model=HomeworkPanel(root,lambda _:None,lambda *_:None)
                engine=Mock()
                panel=ExamPanel(root,model,lambda url:engine,lambda *_:None)
                try:
                    self.assertEqual(panel.url.get(), '')
                    panel.url.set(EXAM_URL+'&newWeb=1&date=1793453325387')
                    panel.open()
                    saved=ModelSettings(**__import__('json').loads(path.read_text(encoding='utf-8')))
                    self.assertEqual(saved.exam_url.count('newWeb='),1)
                    self.assertNotIn('date=',saved.exam_url)
                    engine.command.assert_called_once_with('exam_open',saved.exam_url)
                finally:model.close();root.destroy()
