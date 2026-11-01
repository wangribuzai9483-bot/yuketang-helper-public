import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from yuketang_helper.config import Config, ROOT, data_root
from yuketang_helper.llm import ModelSettings


class DistributionTests(unittest.TestCase):
    def test_frozen_data_root_does_not_follow_bundle_or_working_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('sys.frozen', True, create=True), patch.dict(os.environ, {'LOCALAPPDATA': folder}):
                self.assertEqual(data_root(), Path(folder) / 'YuketangHelper')
        with patch('sys.frozen', False, create=True):
            self.assertEqual(data_root(), ROOT)

    def test_fresh_user_directory_can_save_both_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / 'new-user' / 'config.json'
            Config().save(config_path)
            settings_path = Path(folder) / 'other-user' / 'model-settings.json'
            ModelSettings(model='example-model').save(settings_path)
            self.assertEqual(Config.load(config_path).course_name, '我的课程')
            self.assertEqual(ModelSettings.load(settings_path).model, 'example-model')


if __name__ == '__main__':
    unittest.main()
