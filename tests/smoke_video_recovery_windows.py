"""Real media and browser lifecycle, with synthetic courses in a fresh profile."""
from dataclasses import replace
from pathlib import Path
import queue
import tempfile
import threading
import time
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.test_player import FIXTURE, media_url
from yuketang_helper.config import Config
from yuketang_helper.engine import Engine


def wait_event(engine, predicate, timeout=20):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        try:event=engine.events.get(timeout=.2)
        except queue.Empty:continue
        if event.kind=='error':raise AssertionError(event.data)
        if predicate(event):return event
    raise AssertionError('Expected browser event did not arrive')


def main():
    with tempfile.TemporaryDirectory(prefix='ykt-video-recovery-') as folder, patch('yuketang_helper.engine.RUNTIME',Path(folder)):
        first='https://school.yuketang.cn/ai-workspace/lms-graph/100/video/10'
        second=first.replace('/100/','/200/')
        config=Config(course_name='旧的显示名称',course_url=first,verification_bypass='off',stealth=False)
        engine=Engine(config,queue.Queue())
        def entry():
            def route(request):
                title='演示课程甲' if '/100/' in request.request.url else '演示课程乙'
                body=FIXTURE.replace('我的课程',title)+f'<script>document.querySelector("video").src={__import__("json").dumps(media_url(24))}</script>'
                request.fulfill(body=body,content_type='text/html; charset=utf-8')
            engine.context.route('**/*',route)
            engine.owned_page.goto(first)
        engine._open_entry=entry
        engine.start()
        try:
            sample=wait_event(engine,lambda e:e.kind=='status' and e.data.get('state')=='playing' and e.data.get('time',0)>.5)
            before=sample.data['time'];engine.command('minimize')
            wait_event(engine,lambda e:e.kind=='window' and e.data=='minimize')
            after=wait_event(engine,lambda e:e.kind=='status' and e.data.get('time',0)>before+1)
            assert after.data['rate']==2 and after.data['duration']>0
            engine.command('options',replace(engine.config,course_url=second))
            wait_event(engine,lambda e:e.kind=='course_context' and e.data['course_name']=='演示课程乙')
            sample=wait_event(engine,lambda e:e.kind=='status' and e.data.get('state')=='playing' and e.data.get('time',0)>.5)
            assert sample.data['rate']==2
            engine.command('stop');engine.thread.join(timeout=12)
            assert not engine.thread.is_alive() and engine.finished.is_set()
            print('Two synthetic courses: 2x media time advanced while minimized; live course switch and stop OK.',flush=True)
        finally:
            engine.command('stop');engine.thread.join(timeout=12)
        blocked=Engine(config,queue.Queue());blocked.STOP_GRACE_SECONDS=.3
        entered=threading.Event()
        release=threading.Event()
        class PendingPage(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def handle(self):
                try:super().handle()
                except (ConnectionResetError,ConnectionAbortedError):pass
            def do_GET(self):
                entered.set();release.wait(15)
                try:self.send_response(200);self.end_headers();self.wfile.write(b'local test')
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),PendingPage)
        server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
        def blocked_entry():
            blocked.owned_page.goto(f'http://127.0.0.1:{server.server_port}/pending',timeout=45000)
        blocked._open_entry=blocked_entry
        blocked.start()
        try:
            assert entered.wait(15)
            assert blocked.browser_process and blocked.browser_process.handle
            blocked.command('stop');blocked.thread.join(timeout=12)
            assert not blocked.thread.is_alive() and blocked.finished.is_set()
            print('Unresponsive temporary browser: owned-process shutdown released worker and profile.',flush=True)
        finally:
            blocked.command('stop');blocked.thread.join(timeout=12)
            release.set();server.shutdown();server.server_close();server_thread.join(timeout=2)


if __name__=='__main__':main()
