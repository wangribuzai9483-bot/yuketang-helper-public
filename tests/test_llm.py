import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from yuketang_helper.llm import ModelClient, ModelError, ModelSettings, is_homework_url, validate_answer
from yuketang_helper.secrets import KeyStore


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.path, body, self.headers.get("Authorization")))
        self.send_response(self.server.status)
        if self.server.status == 302:
            self.send_header("Location", self.server.url + "/redirect-target")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        data = {"choices": [{"message": {"content": self.server.content}, "finish_reason": "stop"}]}
        self.wfile.write(json.dumps(data).encode())


class MockServer:
    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.url = f"http://127.0.0.1:{self.server.server_port}"
        self.server.requests = []
        self.server.status = 200
        self.server.content = '{"option_ids":["B"],"explanation":"示例"}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.server

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


QUESTION = {"type": "single", "stem": "题干", "options": [{"id": "A", "text": "甲"}, {"id": "B", "text": "乙"}], "blank_count": 0}


class ModelTests(unittest.TestCase):
    def test_endpoint_and_settings(self):
        self.assertEqual(ModelSettings(base_url="https://provider.example/v1/").endpoint, "https://provider.example/v1/chat/completions")
        self.assertEqual(ModelSettings(base_url="https://provider.example/chat/completions").endpoint, "https://provider.example/chat/completions")
        for url in ("http://provider.example", "https://user:key@provider.example", "https://provider.example?key=secret"):
            with self.assertRaises(ValueError):
                ModelSettings(base_url=url).validate()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            ModelSettings(model="my-model",auto_submit=True).save(path)
            self.assertEqual(ModelSettings.load(path).model, "my-model")
            self.assertTrue(ModelSettings.load(path).auto_submit)
            self.assertNotIn("key", json.loads(path.read_text(encoding="utf-8")))
        with self.assertRaises(ValueError):
            ModelSettings(auto_submit="true").validate()

    def test_homework_routes_exclude_exam(self):
        self.assertTrue(is_homework_url("https://school.yuketang.cn/ai-workspace/lms-graph/1/exercise/2"))
        self.assertFalse(is_homework_url("https://school.yuketang.cn/ai-workspace/lms-graph/1/exam/2"))
        self.assertFalse(is_homework_url("https://school.yuketang.cn/exercise/2/quiz/3"))

    def test_choice_validation(self):
        for answer in ({"option_ids": ["C"]}, {"option_ids": ["A", "B"]}, {"option_ids": ["A", "A"]}, {"option_ids": "A"}, {"uncertain": True}):
            with self.subTest(answer=answer), self.assertRaises(ModelError):
                validate_answer(QUESTION, answer)
        self.assertEqual(validate_answer(QUESTION, {"option_ids": ["B"]})["option_ids"], ["B"])

    def test_blank_and_text_validation(self):
        with self.assertRaises(ModelError):
            validate_answer({"type": "blank", "blank_count": 2}, {"blank_answers": ["一"]})
        with self.assertRaises(ModelError):
            validate_answer({"type": "text"}, {"text": "  "})
        self.assertEqual(validate_answer({"type": "text"}, {"text": "答案"})["text"], "答案")

    def test_actual_http_payload_is_limited_to_question_data(self):
        with MockServer() as server:
            settings = ModelSettings(base_url=server.url + "/v1", model="mock-model")
            answer = ModelClient(settings, "dummy-test-key").solve({**QUESTION, "cookie": "test-private-session", "user_name": "test-user", "url": "signed-url"})
            self.assertEqual(answer["option_ids"], ["B"])
            path, payload, auth = server.requests[0]
            self.assertEqual(path, "/v1/chat/completions")
            self.assertEqual(auth, "Bearer dummy-test-key")
            self.assertEqual(payload["model"], "mock-model")
            user = json.loads(payload["messages"][1]["content"])
            self.assertEqual(set(user), {"type", "stem", "options", "blank_count"})

    def test_provider_error_and_redirect_do_not_leak_or_resend(self):
        with MockServer() as server:
            client = ModelClient(ModelSettings(base_url=server.url, model="mock"), "dummy-test-key")
            server.status = 401
            with self.assertRaisesRegex(ModelError, "HTTP 401") as caught:
                client.test()
            self.assertNotIn("dummy-test-key", str(caught.exception))
            server.status = 302
            with self.assertRaisesRegex(ModelError, "HTTP 302"):
                client.test()
            self.assertEqual(len(server.requests), 2)

    def test_invalid_json_and_images_stop_without_filling(self):
        with MockServer() as server:
            client = ModelClient(ModelSettings(base_url=server.url, model="mock"), "")
            server.content = "not JSON"
            with self.assertRaises(ModelError):
                client.solve(QUESTION)
            before = len(server.requests)
            with self.assertRaises(ModelError):
                client.solve({**QUESTION, "has_images": True})
            self.assertEqual(len(server.requests), before)

    def test_dpapi_keys_are_encrypted_and_bound_to_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            store = KeyStore(Path(directory))
            endpoint = "https://provider.example/v1/chat/completions"
            store.save(endpoint, "dummy-private-key-for-test")
            self.assertEqual(store.load(endpoint), "dummy-private-key-for-test")
            self.assertEqual(store.load("https://other.example/v1/chat/completions"), "")
            self.assertNotIn(b"dummy-private-key-for-test", next(Path(directory).glob("*.bin")).read_bytes())

    def test_judgment_accepts_explicit_json_boolean_option(self):
        q = {'type':'judgment','options':[{'id':'true'},{'id':'false'}]}
        self.assertEqual(validate_answer(q,{'option_ids':[False]})['option_ids'],['false'])

    def test_unresolved_font_is_not_sent_to_model(self):
        with MockServer() as server:
            client=ModelClient(ModelSettings(base_url=server.url,model='mock'),'')
            with self.assertRaisesRegex(ModelError,'特殊字体'):
                client.solve({**QUESTION,'needs_ocr':True})
            self.assertEqual(len(server.requests),0)

    def test_deepseek_uses_non_thinking_mode_for_structured_answers(self):
        opener=MagicMock()
        response=opener.open.return_value.__enter__.return_value
        response.read.return_value=json.dumps({'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}]}).encode()
        with patch('yuketang_helper.llm.request.build_opener',return_value=opener):
            ModelClient(ModelSettings(base_url='https://api.deepseek.com',model='deepseek-flash'),'dummy-test-key').test()
        payload=json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload['thinking'],{'type':'disabled'})
