from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from playwright.sync_api import sync_playwright

from yuketang_helper.bypass import VerificationBypass
from yuketang_helper.config import Config
from yuketang_helper.engine import Engine


class LoginHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == '/login':
            body = b'''<!doctype html><title>Login fixture</title><link rel="stylesheet" href="/style.css">
              <main>Login ready</main><script>fetch('/data').then(r=>r.json()).then(r=>{window.ready=r.ok})</script>'''
            kind = 'text/html'
        elif self.path == '/data':
            body, kind = b'{"ok":true}', 'application/json'
        else:
            body, kind = b'main{color:rgb(1,2,3)}', 'text/css'
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StartupNavigationTests(unittest.TestCase):
    def test_navigation_failure_reports_only_error_code(self):
        events = queue.Queue()
        engine = Engine(Config(verification_bypass='off', stealth=False), events)
        engine.owned_page = Mock()
        engine.owned_page.goto.side_effect = Exception('Page.goto net::ERR_PROXY_CONNECTION_FAILED https://example.test/?token=private')
        self.assertFalse(engine._open_entry())
        messages = []
        while not events.empty():
            messages.append(str(events.get().data))
        self.assertTrue(any('net::ERR_PROXY_CONNECTION_FAILED' in message for message in messages))
        self.assertFalse(any('token=private' in message for message in messages))

    def test_initial_homework_keeps_its_login_return_path(self):
        engine = Engine(Config(verification_bypass='off', stealth=False), queue.Queue(),
                        initial_homework_url='https://school.yuketang.cn/ai-workspace/lms-graph/100001/exercise/200004?node_id=1')
        engine.owned_page = Mock()
        self.assertTrue(engine._open_entry())
        entry = engine.owned_page.goto.call_args.args[0]
        self.assertIn('school.yuketang.cn/web/', entry)
        self.assertIn('%2Fexercise%2F200004', entry)
        self.assertIn('node_id%3D1', entry)

    def test_installed_helpers_do_not_block_login_or_its_resources(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), LoginHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context()
                    helper = VerificationBypass(SimpleNamespace(config=Config(verification_bypass='off', stealth=False)))
                    helper.install(context, stealth=False)
                    page = context.new_page()
                    response = page.goto(f'http://127.0.0.1:{server.server_port}/login', timeout=2000)
                    self.assertEqual(response.status, 200)
                    page.wait_for_function('window.ready === true', timeout=2000)
                    self.assertEqual(page.locator('main').inner_text(), 'Login ready')
                    self.assertEqual(page.locator('main').evaluate('el=>getComputedStyle(el).color'), 'rgb(1, 2, 3)')
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
