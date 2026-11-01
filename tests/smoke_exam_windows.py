"""A temporary unsigned-in Edge; all examination pages and model calls are mocks."""
import queue
import time
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from tests.test_exam import INTRO, PAPER_HTML, PORTAL, PAPER
from yuketang_helper.config import Config
from yuketang_helper.engine import Engine
from yuketang_helper.exam import ExamSession
from yuketang_helper.llm import ModelSettings


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=False)
        try:
            context=browser.new_context()
            context.route('**/*',lambda route:route.fulfill(body=PAPER_HTML if '/exam/123' in route.request.url else INTRO,
                                                          content_type='text/html; charset=utf-8'))
            portal=context.new_page();portal.goto(PORTAL)
            engine=Engine(Config(verification_bypass='off',stealth=False),queue.Queue())
            engine.context=context;engine.owned_page=portal
            exam=ExamSession(engine)
            try:
                exam.open(PORTAL)
                # Simulate the user's manual opening of this local fixture only.
                with context.expect_page() as created:portal.evaluate('url=>window.open(url)',PAPER)
                page=created.value;page.wait_for_load_state()
                def solve(q):
                    if q['type']=='judgment':return {'option_ids':['true']}
                    if q['type']=='single':return {'option_ids':['A']}
                    if q['type']=='multiple':return {'option_ids':['A','C']}
                    return {'text':'模拟简答或论述答案'}
                with patch('yuketang_helper.exam.ModelClient.solve',side_effect=solve),patch('yuketang_helper.exam.KeyStore.load',return_value=''):
                    exam.start(ModelSettings(base_url='http://localhost:1234',model='mock'),batch=True)
                    until=time.monotonic()+15
                    while (exam.pending or exam.batch) and time.monotonic()<until:
                        exam.tick();page.wait_for_timeout(25)
                assert exam.completed==5
                assert sorted(page.evaluate('items[2].result'))==['A','C']
                assert page.evaluate('window.submits')==0
                assert portal.evaluate('window.starts')==0
                print('Independent exam window: five question types filled; intro start and paper submission untouched.')
            finally:exam.close()
        finally:browser.close()


if __name__=='__main__':main()
