from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from tests.test_learning import ASSIGNMENT, QUESTION, ANSWER
from yuketang_helper.learning import LearningService, LearningStore
from yuketang_helper.learning_gui import LearningPanel
from yuketang_helper.homework_gui import HomeworkPanel
from yuketang_helper.llm import ModelClient, ModelSettings


class LearningGuiTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
        self.temp=tempfile.TemporaryDirectory()
        self.service=LearningService(LearningStore(Path(self.temp.name)/'records.db'))

    def tearDown(self):
        self.service.close();self.root.destroy();self.temp.cleanup()

    def test_records_sources_and_personal_notes_can_be_viewed_and_revised(self):
        key=self.service.store.record('我的课程',ASSIGNMENT,QUESTION,ANSWER,model='mock',status='submitted')
        rows=self.service.store.records()
        self.service.store.save_experiences(rows,[{'topic':'知识点','summary':'总结','techniques':[], 'record_ids':[key]}],'mock','sig')
        panel=LearningPanel(self.root,self.service,lambda:None)
        panel.refresh();panel.records.selection_set(key);panel.show_record()
        self.assertIn('待确认',panel.detail.get('1.0','end'))
        self.assertIn('已提交',panel.detail.get('1.0','end'))
        exp=self.service.store.experiences()[0]['id']
        panel.experiences.selection_set(exp);panel.show_experience()
        self.assertIn(QUESTION['stem'],panel.material.get('1.0','end'))
        panel.notes.insert('1.0','个人修订')
        panel.render({'message':'新记录已保存'})
        panel.show_experience()
        self.assertEqual(panel.notes.get('1.0','end-1c'),'个人修订')
        panel.save_notes()
        self.assertEqual(self.service.store.experiences()[0]['notes'],'个人修订')

    def test_saving_review_options_does_not_trigger_api(self):
        with patch('yuketang_helper.homework_gui.ModelSettings.load',return_value=ModelSettings()), \
             patch('yuketang_helper.homework_gui.ModelSettings.save'), \
             patch.object(ModelClient,'review') as api:
            panel=HomeworkPanel(self.root,lambda url:None,lambda *_:None)
            try:
                panel.vars['review_summary'].set(True)
                self.assertTrue(panel.save())
                self.assertTrue(panel.settings.review_summary)
                self.assertFalse(panel.settings.review_techniques)
                api.assert_not_called()
            finally:panel.close()
