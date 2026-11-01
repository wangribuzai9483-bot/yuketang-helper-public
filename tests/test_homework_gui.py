import tkinter as tk
from tkinter import ttk
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from yuketang_helper.gui import App
from yuketang_helper.homework_gui import HomeworkPanel, KEY_MASK
from yuketang_helper.llm import ModelSettings
from yuketang_helper.secrets import KeyStore


class HomeworkGuiTests(unittest.TestCase):
    def test_manual_restore_still_works_if_browser_worker_stopped(self):
        root=tk.Tk();root.withdraw();app=App(root)
        commands=[]
        app.engine=SimpleNamespace(thread=SimpleNamespace(is_alive=lambda:False),
            restore_window_now=lambda:{'restored':True,'foreground':True},
            command=lambda name,value=None:commands.append(name))
        try:
            app.send('restore')
            self.assertEqual(commands,[])
            self.assertIn('窗口已恢复',app.course_status.get())
            self.assertFalse(app.restore_pending)
        finally:
            app.close_verification_notice();app.homework_panel.close();root.destroy()

    def test_restore_button_uses_native_path_before_waiting_for_worker(self):
        root=tk.Tk();root.withdraw();app=App(root)
        calls=[]
        app.engine=SimpleNamespace(thread=SimpleNamespace(is_alive=lambda:True),
            restore_window_now=lambda:(calls.append('native') or {'restored':True,'foreground':True}),
            command=lambda name,value=None:calls.append(name))
        try:
            app.show_verification_notice('请手动完成核验。')
            app.send('restore')
            self.assertEqual(calls,['native','restore'])
            self.assertTrue(app.restore_pending)
            self.assertFalse(app.verification_notice.attributes('-topmost'))
            app.restore_timeout(app.restore_request)
            self.assertIn('尚未收到',app.verification_notice_status.get())
            app.last_restore_click=-float('inf')
            app.send('restore')
            self.assertEqual(calls,['native','restore','native','restore'])
        finally:
            app.close_verification_notice();app.homework_panel.close();root.destroy()

    def test_each_new_verification_notice_can_restore_the_browser(self):
        root=tk.Tk();root.withdraw();app=App(root)
        commands=[]
        app.engine=SimpleNamespace(thread=SimpleNamespace(is_alive=lambda:True),
            restore_window_now=lambda:{'restored':True,'foreground':True},
            command=lambda name,value=None:commands.append(name))
        try:
            for _ in range(5):
                app.show_verification_notice('请手动完成核验。')
                app.last_restore_click=-float('inf')
                app.send('restore')
                app.restore_result({'action':'restore','success':True,'message':'已恢复浏览器'})
                self.assertFalse(app.restore_pending)
                app.close_verification_notice()
            self.assertEqual(commands,['restore']*5)
        finally:
            app.close_verification_notice();app.homework_panel.close();root.destroy()

    def test_verification_notice_is_non_modal_and_can_be_cleared(self):
        root=tk.Tk();root.withdraw();app=App(root)
        try:
            app.show_verification_notice('请手动完成安全核验，完成后自动继续。')
            notice=app.verification_notice
            self.assertIsNotNone(notice)
            root.update_idletasks()
            self.assertEqual(notice.state(),'normal')
            self.assertIsNone(root.grab_current())
            app.show_verification_notice('same verification')
            self.assertIs(app.verification_notice,notice)
            app.close_verification_notice()
            self.assertIsNone(app.verification_notice)
        finally:
            app.close_verification_notice()
            app.homework_panel.close()
            root.destroy()

    def test_course_start_uses_saved_model_settings_without_manual_homework_url(self):
        root = tk.Tk()
        root.withdraw()
        app = App(root)
        commands = []
        app.engine = SimpleNamespace(thread=SimpleNamespace(is_alive=lambda: True), command=lambda name,value=None: commands.append((name,value)))
        app.homework_panel.settings = ModelSettings(base_url='http://localhost:1234',model='mock',auto_submit=True)
        try:
            with patch.object(app.homework_panel, 'save', return_value=True), patch.object(app, 'apply', return_value=True), patch('yuketang_helper.course.CourseProgress.load_url', return_value=''):
                app.start_course()
            self.assertEqual(commands[0][0], 'course_start')
            self.assertTrue(commands[0][1].auto_submit)
            self.assertEqual(commands[0][1].homework_url, '')
        finally:
            app.homework_panel.close()
            root.destroy()

    def test_solve_button_passes_settings_to_browser_worker(self):
        root = tk.Tk()
        root.withdraw()
        app = App(root)
        commands = []
        app.engine = SimpleNamespace(thread=SimpleNamespace(is_alive=lambda: True), command=lambda name,value=None: commands.append((name,value)))
        try:
            with patch.object(app.homework_panel, "save", return_value=True):
                app.homework_panel.solve(True)
            self.assertEqual(commands[0][0], "homework_solve")
            self.assertTrue(commands[0][1]["batch"])
            self.assertFalse(commands[0][1]["overwrite"])
            app.homework_panel.auto_submit.set(True)
            with patch.object(app.homework_panel,"save",return_value=True):
                app.homework_panel.solve(True)
            self.assertTrue(commands[-1][1]['auto_submit'])
        finally:
            app.homework_panel.close()
            root.destroy()


class SavedKeyGuiTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk()
        self.root.withdraw()
        self.directory=tempfile.TemporaryDirectory()
        self.store=KeyStore(Path(self.directory.name))
        self.settings=ModelSettings(base_url='https://provider.example/v1',model='mock')
        self.patches=[patch('yuketang_helper.homework_gui.KeyStore',return_value=self.store),
                      patch('yuketang_helper.homework_gui.ModelSettings.load',return_value=self.settings),
                      patch.object(ModelSettings,'save',return_value=None)]
        for item in self.patches:
            item.start()
        self.panels=[]

    def tearDown(self):
        for panel in self.panels:
            panel.close()
        self.root.destroy()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def panel(self):
        panel=HomeworkPanel(ttk.Frame(self.root),lambda url:None,lambda *args:None)
        self.panels.append(panel)
        return panel

    def test_restored_mask_and_empty_field_do_not_overwrite_saved_key(self):
        self.store.save(self.settings.endpoint,'dummy-persistent-key')
        panel=self.panel()
        self.assertEqual(panel.key.get(),KEY_MASK)
        self.assertIn('已保存 API Key',panel.key_status.get())
        self.assertTrue(panel.save())
        self.assertEqual(self.store.load(self.settings.endpoint),'dummy-persistent-key')
        panel.key.set('')
        self.assertTrue(panel.save())
        self.assertEqual(panel.key.get(),KEY_MASK)
        self.assertEqual(self.store.load(self.settings.endpoint),'dummy-persistent-key')

    def test_new_key_is_retained_and_restored_when_settings_reopen(self):
        panel=self.panel()
        panel.key.set('dummy-new-key')
        self.assertTrue(panel.save())
        self.assertEqual(panel.key.get(),KEY_MASK)
        reopened=self.panel()
        self.assertEqual(reopened.key.get(),KEY_MASK)
        self.assertEqual(self.store.load(self.settings.endpoint),'dummy-new-key')

    def test_endpoint_change_never_copies_old_key_to_another_provider(self):
        self.store.save(self.settings.endpoint,'dummy-original-key')
        panel=self.panel()
        panel.vars['base_url'].set('https://other.example/v1')
        self.assertEqual(panel.key.get(),'')
        self.assertIn('尚未保存',panel.key_status.get())
        self.assertTrue(panel.save())
        self.assertEqual(self.store.load('https://other.example/v1/chat/completions'),'')
        panel.vars['base_url'].set(self.settings.base_url)
        self.assertEqual(panel.key.get(),KEY_MASK)
        self.assertEqual(self.store.load(self.settings.endpoint),'dummy-original-key')

    def test_key_entered_before_url_survives_url_typing_and_is_saved(self):
        self.settings.base_url=''
        panel=self.panel()
        panel.key.set('dummy-first-key')
        panel.vars['base_url'].set('https://p')
        panel.vars['base_url'].set('https://provider.example/v1')
        self.assertEqual(panel.key.get(),'dummy-first-key')
        self.assertTrue(panel.save())
        self.assertEqual(self.store.load('https://provider.example/v1/chat/completions'),'dummy-first-key')
