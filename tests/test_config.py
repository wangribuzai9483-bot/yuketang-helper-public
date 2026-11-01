import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

from yuketang_helper.config import Config, allowed_url


class ConfigTests(unittest.TestCase):
    def test_domain_boundary(self):
        for url in ("https://www.yuketang.cn/web", "https://sso.yuketang.cn/login", "https://yuketang.cn"):
            self.assertTrue(allowed_url(url))
        for url in ("http://www.yuketang.cn", "https://yuketang.cn.evil.example", "file:///x", "https://evil-yuketang.cn", "https://user:secret@yuketang.cn"):
            self.assertFalse(allowed_url(url))

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            Config(speed=1.5, muted=False).save(path)
            self.assertEqual(Config.load(path).speed, 1.5)
            self.assertFalse(Config.load(path).muted)
            self.assertNotIn("api_key", json.loads(path.read_text(encoding="utf-8")))

    def test_invalid_options(self):
        for kwargs in ({"speed": 20}, {"speed": float("nan")}, {"muted": "false"}, {"course_name": ""}, {"stall_seconds": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Config(**kwargs).validate()

    def test_school_login_preserves_full_video_return_path(self):
        url = "https://school.yuketang.cn/ai-workspace/lms-graph/100001/video/200001?is_chapter=1&node_id=300002"
        config = Config(course_url=url)
        entry = urlparse(config.entry_url())
        self.assertEqual(entry.hostname, "school.yuketang.cn")
        self.assertEqual(entry.path, "/web/")
        self.assertEqual(parse_qs(entry.query)["type"], ["1"])
        self.assertEqual(parse_qs(entry.query)["next"], [urlparse(url).path + "?" + urlparse(url).query])

    def test_no_direct_url_preserves_existing_login(self):
        self.assertEqual(Config().entry_url(), Config().login_url)

    def test_external_direct_url_is_rejected(self):
        with self.assertRaises(ValueError):
            Config(course_url="https://example.com/video").validate()
