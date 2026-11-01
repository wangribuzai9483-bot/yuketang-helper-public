"""Explicit minimized OCR check in a temporary, unsigned-in Edge."""
import base64
import queue
import time

from playwright.sync_api import sync_playwright

from tests.test_homework import HEADER, SECURITY, SINGLE, URL
from tests.test_llm import MockServer
from tests.test_ocr import FIXTURE
from yuketang_helper.config import Config
from yuketang_helper.engine import Engine
from yuketang_helper.homework import HomeworkSession
from yuketang_helper.llm import ModelSettings


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=False, args=[
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
        ])
        try:
            context = browser.new_context()
            body = SINGLE.replace('class="problem-body"',
                'class="problem-body xuetangx-com-encrypted-font" style="font:16px Microsoft YaHei;line-height:24px"')
            context.route("**/*", lambda route: route.fulfill(body=HEADER + body, content_type="text/html; charset=utf-8"))
            page = context.new_page()
            page.goto(URL)
            engine = Engine(Config(verification_bypass='off', stealth=False), queue.Queue())
            engine.context = context
            engine.owned_page = page
            session = HomeworkSession(engine)
            try:
                engine.window_state(page, 'minimized')
                cdp = context.new_cdp_session(page)
                assert cdp.send('Browser.getWindowForTarget')['bounds']['windowState'] == 'minimized'
                raw = session.current(resolve_text=False)
                question = session.reader.resolve(raw[1], raw[2])
                assert '史记' in question['stem'] and '作者' in question['stem'], question['stem']
                assert cdp.send('Browser.getWindowForTarget')['bounds']['windowState'] == 'minimized'
                print('Minimized Windows OCR OK; window stayed minimized.', flush=True)
                # Discard the cache so the full solve run really invokes OCR again.
                session.reader.cache.clear()
                with MockServer() as server:
                    session.start(ModelSettings(base_url=server.url, model='mock'))
                    until = time.monotonic() + 15
                    while session.pending and time.monotonic() < until:
                        session.tick()
                        page.wait_for_timeout(40)
                    assert session.completed == 1, 'Minimized solve/fill did not complete'
                    assert page.locator('input[value="B"]').is_checked()
                    assert len(server.requests) == 1
                try:
                    state = cdp.send('Browser.getWindowForTarget')['bounds']['windowState']
                    assert state == 'minimized', f'OCR restored the window: {state}'
                finally:
                    cdp.detach()
                print('Minimized Windows OCR + mock-model + browser fill OK; window stayed minimized.')
                context.route('https://verify.example.test/human-check', lambda route: route.fulfill(
                    body='''<meta charset="utf-8"><section id="host"></section><script>
                      document.querySelector('#host').attachShadow({mode:'closed'}).innerHTML='<strong>请完成安全校验</strong>';
                      window.fixtureReady=true;</script>''', content_type='text/html; charset=utf-8'))
                page.evaluate('''() => {
                  document.querySelectorAll('input').forEach(el=>el.checked=false);
                  const frame=document.createElement('iframe');frame.id='security-host';
                  frame.src='https://verify.example.test/human-check';document.body.append(frame);
                }''')
                until=time.monotonic()+5
                while len(page.frames)<2 and time.monotonic()<until:
                    page.wait_for_timeout(20)
                page.frames[-1].wait_for_function('window.fixtureReady')
                engine.window_state(page, 'minimized')
                with MockServer() as server:
                    session.start(ModelSettings(base_url=server.url, model='mock'))
                    cdp = context.new_cdp_session(page)
                    try:
                        assert cdp.send('Browser.getWindowForTarget')['bounds']['windowState'] == 'normal'
                    finally:
                        cdp.detach()
                    assert session.verification_wait
                    assert len(server.requests) == 0
                    assert page.evaluate('window.challengeTouches||0') == 0
                    # Simulate manual completion of this local fixture only.
                    page.locator('#security-host').evaluate('el=>el.remove()')
                    until = time.monotonic() + 15
                    while (session.verification_wait or session.deferred_solve or session.pending) and time.monotonic() < until:
                        session.tick()
                        page.wait_for_timeout(40)
                    assert session.completed == 1
                    assert page.locator('input[value="B"]').is_checked()
                    assert len(server.requests) == 1
                print('Verification restored minimized Edge; manual fixture completion resumed the original task.')
                page.set_content(HEADER+'''<style>.option-item{display:flex;width:1500px;margin:12px;font:16px/24px "Microsoft YaHei"}
                  .option-item__text{display:inline-block;width:1400px}.option-item input{opacity:0;position:absolute}</style>
                  <div class="container-problem" data-question-id="multi" data-question-type="multiple"><div class="problem-body">世界文化类型可以分为哪些类型？</div>
                  <label class="option-item"><input type="checkbox" value="A"><b>A</b><span class="option-item__text"><span class="xuetangx-com-encrypted-font">游牧与森林文化</span></span></label>
                  <label class="option-item"><input type="checkbox" value="B"><b>B</b><span class="option-item__text"><span class="xuetangx-com-encrypted-font">宗教文化</span></span></label>
                  <label class="option-item"><input type="checkbox" value="C"><b>C</b><span class="option-item__text"><span class="xuetangx-com-encrypted-font">海洋文化</span></span></label>
                  <label class="option-item"><input type="checkbox" value="D"><b>D</b><span class="option-item__text"><span class="xuetangx-com-encrypted-font">农耕文化</span></span></label></div>''')
                engine.window_state(page,'minimized')
                with MockServer() as server:
                    server.content='{"option_ids":["A","D"],"explanation":"local fixture"}'
                    session.start(ModelSettings(base_url=server.url,model='mock'))
                    until=time.monotonic()+15
                    while session.pending and time.monotonic()<until:
                        session.tick();page.wait_for_timeout(40)
                    assert session.completed==1
                    assert page.locator('input:checked').evaluate_all('els=>els.map(el=>el.value)')==['A','D']
                cdp=context.new_cdp_session(page)
                try:
                    assert cdp.send('Browser.getWindowForTarget')['bounds']['windowState']=='minimized'
                finally:
                    cdp.detach()
                print('Minimized short multiple-choice Windows OCR + A/D fill OK.')
                page.set_content(HEADER+'''<div class="container-problem" data-question-id="short-option" data-question-type="single">
                  <div class="problem-body">请选择选项</div><label><input type="radio" name="a" value="A">A. test</label>
                  <label><input type="radio" name="a" value="C"><span class="option-item__text xuetangx-com-encrypted-font"
                  style="display:inline-block;width:948px;height:75px"></span></label></div>''')
                page.evaluate('''data=>{
                  const span=document.querySelector('.option-item__text');
                  span.style.backgroundImage='url(data:image/png;base64,'+data+')';span.style.backgroundSize='100% 100%';
                  document.querySelector('.container-problem').__vue__={problem:{problem_id:'short-option',
                    content:{ProblemType:1,Body:'test',Options:[{key:'A',value:'test'},{key:'C',value:'encoded'}]},user:{my_count:0}}};
                }''',base64.b64encode(FIXTURE.read_bytes()).decode())
                engine.window_state(page,'minimized')
                with MockServer() as server:
                    server.content='{"option_ids":["A"]}'
                    session.start(ModelSettings(base_url=server.url,model='mock'))
                    until=time.monotonic()+15
                    while session.pending and time.monotonic()<until:
                        session.tick();page.wait_for_timeout(40)
                    assert session.completed==1
                    assert page.locator('input[value="A"]').is_checked()
                    assert len(server.requests)==1
                    assert '柳宗元' in server.requests[0][1]['messages'][-1]['content']
                cdp=context.new_cdp_session(page)
                try:
                    assert cdp.send('Browser.getWindowForTarget')['bounds']['windowState']=='minimized'
                finally:
                    cdp.detach()
                print('Actual failed short-option image: minimized OCR + model payload + fill OK.')
            finally:
                session.close()
                context.close()
        finally:
            browser.close()


if __name__ == '__main__':
    main()
