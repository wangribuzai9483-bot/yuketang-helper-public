"""Local fixture: a verification-intercepted click, manual handoff, one real app POST per question."""
import json
import queue
import time

from playwright.sync_api import sync_playwright

from tests.test_homework import HEADER, SECURITY, SUBMITTABLE, URL
from tests.test_llm import MockServer
from yuketang_helper.config import Config
from yuketang_helper.engine import Engine
from yuketang_helper.homework import HomeworkSession
from yuketang_helper.llm import ModelSettings


def main():
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel='msedge',headless=False,args=[
            '--disable-background-timer-throttling','--disable-renderer-backgrounding',
            '--disable-backgrounding-occluded-windows'])
        try:
            context=browser.new_context()
            context.route('**/*',lambda route:route.fulfill(body=HEADER+SUBMITTABLE,content_type='text/html; charset=utf-8'))
            posts=[]
            def accept(route):
                posts.append(route.request.post_data_json)
                route.fulfill(body=json.dumps({'success':True,'data':{'my_count':1}}),content_type='application/json')
            context.route('**/mooc-api/v1/lms/exercise/problem_apply/',accept)
            page=context.new_page();page.goto(URL)
            page.evaluate('''html=>{
              window.blockedOnce=false;
              window.submitAction=()=>{window.submitClicks++;
                if(!window.blockedOnce){window.blockedOnce=true;
                  const host=document.createElement('section');host.id='mock-verification';document.body.append(host);
                  host.attachShadow({mode:'closed'}).innerHTML=html;return;
                }
                fetch('/mooc-api/v1/lms/exercise/problem_apply/',{method:'POST',headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({classroom_id:100001,problem_id:document.querySelector('.container-problem').dataset.questionId})
                }).then(r=>r.json()).then(data=>{if(data.success)window.confirmSubmission();});
              };
            }''',SECURITY)
            engine=Engine(Config(),queue.Queue());engine.context=context;engine.owned_page=page
            session=HomeworkSession(engine)
            try:
                engine.window_state(page,'minimized')
                with MockServer() as server:
                    session.start(ModelSettings(base_url=server.url,model='mock'),auto_submit=True,batch=True)
                    until=time.monotonic()+10
                    while time.monotonic()<until and not session.verification_wait:
                        session.tick();page.wait_for_timeout(30)
                    assert session.verification_wait
                    assert session.submission['probe'].outcome=='not-sent'
                    assert posts==[]
                    cdp=context.new_cdp_session(page)
                    assert cdp.send('Browser.getWindowForTarget')['bounds']['windowState']=='normal'
                    # Simulate manual completion of this local fixture only.
                    page.locator('#mock-verification').evaluate('el=>el.remove()')
                    session.tick();session.verification_wait['clear_since']-=2;session.tick()
                    session.submission['verification_completed_at']-=3
                    engine.window_state(page,'minimized')
                    until=time.monotonic()+15
                    while time.monotonic()<until:
                        session.tick()
                        if session.advance_at:session.advance_at=(0,session.advance_at[1])
                        if not(session.pending or session.submission or session.advance_at or session.waiting_for):break
                        page.wait_for_timeout(30)
                    assert session.submitted_count==3
                    assert [str(p['problem_id']) for p in posts]==['1','2','3']
                    assert len(server.requests)==3
                    assert page.evaluate('window.submitClicks')==4
                    assert cdp.send('Browser.getWindowForTarget')['bounds']['windowState']=='minimized'
                    cdp.detach()
                    print('Edge submission handoff OK: verification blocked one click; manual fixture completion resumed 3 questions; exactly 3 accepted assignment POSTs.')
            finally:
                session.close();context.close()
        finally:
            browser.close()


if __name__=='__main__':main()
