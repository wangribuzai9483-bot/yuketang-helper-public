import json
from pathlib import Path
import queue
import tempfile
import time
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from tests.test_llm import MockServer
from tests.test_homework import SECURITY
from yuketang_helper.config import Config
from yuketang_helper.course import COURSE_SCRIPT, CourseProgress
from yuketang_helper.engine import Engine
from yuketang_helper.homework import HomeworkSession
from yuketang_helper.llm import ModelSettings

ORIGIN = 'https://school.yuketang.cn'
BASE = ORIGIN + '/ai-workspace/lms-graph/100001/'
URL = BASE + 'video/100'

FIXTURE = '''<!doctype html><meta charset="utf-8">
<div class="learning-space-header__primary">我的课程</div>
<div id="directory"></div><main id="content"></main><div id="dialogs"></div>
<script>
window.catalogue=[['video','视频一',100],['homework','作业一',200],['video','视频二',101],
  ['homework','作业二',201],['video','视频三',102]];
window.currentLeaf=0;window.questionIndex=1;window.jobs={};window.navClicks=[];
window.submits=[];window.drafts={};window.deferNavigation=false;window.intro=false;
function label(kind){return {video:'视频',homework:'作业',discussion:'讨论',exam:'考试'}[kind]||'其他';}
function renderDirectory(){
 document.querySelector('#directory').innerHTML=catalogue.map((item,i)=>
  `<div class="leaf-item ${i===currentLeaf?'is-active':''} ${item[3]?'is-locked':''}" onclick="goLeaf(${i})">
  <span class="leaf-item-tag">${label(item[0])}</span><span class="leaf-item-title">${item[1]}</span></div>`).join('');
}
function goLeaf(i){
 navClicks.push(i);
 if(deferNavigation){currentLeaf=i;renderDirectory();return;}
 const item=catalogue[i];currentLeaf=i;questionIndex=window.resumeIndex||1;
 history.pushState({},'',`/ai-workspace/lms-graph/100001/${item[0]==='homework'?'exercise':item[0]}/${item[2]}?node_id=${item[2]}`);
 render();
}
function render(){
 renderDirectory();document.querySelector('#dialogs').innerHTML='';
 const item=catalogue[currentLeaf];
 if(item[0]==='video'){
  document.querySelector('#content').innerHTML='<video style="width:320px;height:180px"></video>';
  const video=document.querySelector('video');
  Object.defineProperty(video,'ended',{get:()=>!!window.forceEnded,configurable:true});
  window.forceEnded=false;return;
 }
 if(item[0]!=='homework'){document.querySelector('#content').innerHTML='<p>其他内容</p>';return;}
 if(intro){
  document.querySelector('#content').innerHTML='<button type="button" onclick="window.intro=false;renderQuestion()">开始作答</button>';
 }else renderQuestion();
}
function renderQuestion(){
 const id=catalogue[currentLeaf][2], index=questionIndex, key=id+'-'+index;
 const stored=jobs[key]||{};
 document.querySelector('#content').innerHTML=`
 <div class="problems-aside"><div class="subject-item ${index===1?'active':''} ${jobs[id+'-1']?.count?'primary':''}" data-order="1" onclick="selectQuestion(1)">1</div>
 <div class="subject-item ${index===2?'active':''} ${jobs[id+'-2']?.count?'primary':''}" data-order="2" onclick="selectQuestion(2)">2</div></div>
 <div class="container-problem" data-question-id="${key}" data-question-type="single" data-question-index="${index}"
 data-submission-count="${stored.count||0}" data-submit-time="${stored.time||''}">
 <div class="problem-body">${id}的第${index}题</div>
 <label><input type="radio" name="answer" value="A" ${stored.answer==='A'?'checked':''}>A.甲</label>
 <label><input type="radio" name="answer" value="B" ${stored.answer==='B'?'checked':''}>B.乙</label>
 <div class="problem-fixedbar"><button type="button" onclick="submitQuestion()">提交</button>
 <button type="button" id="next" onclick="selectQuestion(2)" ${index===2?'disabled':''}>下一题</button></div></div>`;
}
function submitQuestion(){
 const root=document.querySelector('.container-problem'), key=root.dataset.questionId;
 const answer=document.querySelector('input:checked')?.value;
 submits.push(key);jobs[key]={answer,count:Number(root.dataset.submissionCount)+1,time:String(Date.now())};
 root.dataset.submissionCount=String(jobs[key].count);root.dataset.submitTime=jobs[key].time;
}
function selectQuestion(index){
 const root=document.querySelector('.container-problem');
 const answer=document.querySelector('input:checked')?.value;
 if(answer && !Number(root.dataset.submissionCount)){
  window.pendingQuestion=index;
  document.querySelector('#dialogs').innerHTML=`<div role="dialog" class="homework-problem-change-guard-dialog">
   刚刚答的习题还没有提交，需要提交吗？<button type="button" onclick="declineQuestion()">先不用了</button>
   <button type="button" onclick="window.badSubmit=true">帮我提交</button></div>`;
 }else {questionIndex=index;renderQuestion();}
}
function declineQuestion(){
 const root=document.querySelector('.container-problem');
 jobs[root.dataset.questionId]={answer:document.querySelector('input:checked')?.value,count:0};
 document.querySelector('#dialogs').innerHTML='';questionIndex=pendingQuestion;renderQuestion();
}
render();
</script>'''


class CourseProgressTests(unittest.TestCase):
    def test_outline_course_ids_are_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            progress = CourseProgress(Path(folder) / 'progress.json')
            config = Config(course_url=ORIGIN+'/v2/web/studentLog/100001?classroom_id=100001')
            progress.save(config, BASE+'exercise/200', {'kind':'homework','title':'作业'})
            self.assertEqual(progress.load_url(config), BASE+'exercise/200')
            self.assertEqual(progress.load_url(Config(course_url=ORIGIN+'/v2/web/studentLog/999')), '')

    def test_checkpoint_is_scoped_and_omits_volatile_query(self):
        with tempfile.TemporaryDirectory() as folder:
            progress = CourseProgress(Path(folder) / 'progress.json')
            config = Config(course_url=URL)
            progress.save(config, BASE+'exercise/200?node_id=77&date=123&token=secret', {'kind':'homework','title':'作业'})
            self.assertEqual(progress.load_url(config), BASE+'exercise/200?node_id=77')
            self.assertNotIn('secret', progress.path.read_text(encoding='utf-8'))
            self.assertEqual(progress.load_url(Config(course_url=BASE.replace('100001','999')+'video/100')), '')

    def test_corrupt_foreign_and_exam_checkpoints_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            progress = CourseProgress(Path(folder) / 'progress.json')
            config = Config(course_url=URL)
            for url in ('https://example.com/video/100', BASE+'exam/500', BASE.replace('100001','999')+'video/100'):
                progress.save(config, url, {'kind':'video','title':'test'})
                self.assertEqual(progress.load_url(config), '')
            progress.path.write_text('{broken', encoding='utf-8')
            self.assertEqual(progress.load_url(config), '')


class CourseFlowTests(unittest.TestCase):
    def test_submission_blocked_by_verification_resumes_entire_course_without_restart(self):
        posts=[]
        self.context.route('**/mooc-api/v1/lms/exercise/problem_apply/',lambda route:(
            posts.append(route.request.post_data_json),
            route.fulfill(body='{"success":true,"data":{"my_count":1}}',content_type='application/json')))
        self.page.evaluate('''html=>{
          const submit=window.submitQuestion;window.uiSubmitClicks=0;window.blockedOnce=false;
          window.submitQuestion=()=>{window.uiSubmitClicks++;
            if(!window.blockedOnce){window.blockedOnce=true;document.body.insertAdjacentHTML('beforeend',html);return;}
            fetch('/mooc-api/v1/lms/exercise/problem_apply/',{method:'POST',headers:{'Content-Type':'application/json'},
              body:JSON.stringify({classroom_id:100001,problem_id:document.querySelector('.container-problem').dataset.questionId})
            }).then(r=>r.json()).then(data=>{if(data.success)submit();});
          };
        }''',SECURITY)
        with MockServer() as server:
            self.flow.start(ModelSettings(base_url=server.url,model='mock',auto_submit=True))
            self.run_until(lambda:bool(self.engine.homework.verification_wait))
            self.assertFalse(self.flow.paused)
            self.assertEqual(posts,[])
            self.page.locator('#security').evaluate('el=>el.remove()')
            self.engine.homework.tick()
            self.engine.homework.verification_wait['clear_since']-=2
            self.engine.homework.tick()
            self.engine.homework.submission['verification_completed_at']-=3
            self.run_until(lambda:self.flow.stage=='complete',timeout=15)
            self.assertEqual([p['problem_id'] for p in posts],['200-1','200-2','201-1','201-2'])
            self.assertEqual(len(server.requests),4)
            self.assertEqual(self.page.evaluate('window.uiSubmitClicks'),5)

    def test_website_auto_next_after_each_submission_keeps_course_running(self):
        self.page.evaluate('''() => {const submit=window.submitQuestion;window.submitQuestion=()=>{
          const index=questionIndex;submit();if(index===1)selectQuestion(2);
        };}''')
        with MockServer() as server:
            self.flow.start(ModelSettings(base_url=server.url,model='mock',auto_submit=True))
            self.run_until(lambda: self.flow.stage=='complete',timeout=15)
            self.assertEqual(self.page.evaluate('window.navClicks'),[1,2,3,4])
            self.assertEqual(self.page.evaluate('window.submits'),['200-1','200-2','201-1','201-2'])
            self.assertEqual(len(server.requests),4)

    def test_closed_shadow_verification_resumes_course_without_start_button(self):
        self.page.evaluate('goLeaf(1)')
        self.page.evaluate('''html=>{const host=document.createElement('section');host.id='security-host';document.body.append(host);
          host.attachShadow({mode:'closed'}).innerHTML=html;}''',SECURITY.replace('h3','strong'))
        with MockServer() as server:
            self.flow.start(ModelSettings(base_url=server.url,model='mock',auto_submit=True))
            self.run_until(lambda: bool(self.engine.homework.verification_wait))
            self.assertEqual(len(server.requests),0)
            self.page.locator('#security-host').evaluate('el=>el.remove()')
            self.run_until(lambda: self.flow.stage=='video',end_video=False,timeout=15)
            self.assertEqual(self.page.evaluate('window.submits'),['200-1','200-2'])

    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.context.route('**/*', lambda route: route.fulfill(body=FIXTURE, content_type='text/html; charset=utf-8'))
        self.page = self.context.new_page()
        self.page.goto(URL)
        self.directory = tempfile.TemporaryDirectory()
        self.engine = Engine(Config(course_url=URL, next_delay_seconds=1), queue.Queue())
        self.engine.context = self.context
        self.engine.owned_page = self.page
        self.engine.active_page = self.page
        self.engine.homework = HomeworkSession(self.engine)
        self.flow = self.engine.course_flow
        self.flow.progress = CourseProgress(Path(self.directory.name) / 'progress.json')

    def tearDown(self):
        self.engine.homework.close()
        self.context.close()
        self.directory.cleanup()

    def step(self, accelerate=True, end_video=True):
        homework = self.engine.homework
        if self.flow.active and self.flow.paused:
            homework._gate_verification()
        else:
            homework.tick()
        self.flow.tick()
        if self.engine.mode == 'video' and not self.engine.paused:
            if end_video:
                self.page.evaluate('window.forceEnded=true')
            self.engine._poll()
        if accelerate:
            if self.flow.stage == 'advance-wait':
                self.flow.due = 0
            if homework.advance_at:
                homework.advance_at = (0, homework.advance_at[1])
        self.page.wait_for_timeout(20)

    def run_until(self, predicate, timeout=10, **kwargs):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            self.step(**kwargs)
            if predicate():
                return
        self.fail(f'Course flow did not reach expected state; stage={self.flow.stage}')

    def test_video_homework_video_homework_video_pipeline(self):
        self.page.evaluate('window.intro=true')
        with MockServer() as server:
            self.flow.start(ModelSettings(base_url=server.url, model='mock', auto_submit=True))
            self.run_until(lambda: self.flow.stage == 'complete')
            self.assertEqual(self.page.evaluate('window.navClicks'), [1,2,3,4])
            self.assertEqual(self.page.evaluate('window.submits'), ['200-1','200-2','201-1','201-2'])
            self.assertEqual(len(server.requests), 4)
            self.assertFalse(self.page.evaluate('!!window.badSubmit'))
            self.assertEqual(self.flow.progress.load_url(self.engine.config), BASE+'video/102?node_id=102')

    def test_starting_at_homework_returns_to_first_question(self):
        self.page.evaluate('window.resumeIndex=2;goLeaf(1)')
        with MockServer() as server:
            self.flow.start(ModelSettings(base_url=server.url, model='mock', auto_submit=True))
            self.run_until(lambda: self.flow.stage == 'video' and self.page.url.startswith(BASE+'video/101'), end_video=False)
            self.assertEqual(self.page.evaluate('window.submits'), ['200-1','200-2'])
            self.assertEqual(len(server.requests), 2)

    def test_fill_only_holds_for_manual_review_before_leaving_homework(self):
        with MockServer() as server:
            settings = ModelSettings(base_url=server.url, model='mock')
            self.flow.start(settings)
            self.run_until(lambda: self.flow.stage == 'review')
            for _ in range(3):
                self.step()
            self.assertEqual(self.page.evaluate('window.navClicks'), [1])
            self.assertEqual(self.page.evaluate('window.submits'), [])
            self.assertEqual(len(server.requests), 2)
            # Explicit continue after the user has reviewed their draft.
            self.flow.start(settings)
            self.run_until(lambda: self.flow.stage == 'video', end_video=False)
            self.assertEqual(self.page.evaluate('window.navClicks'), [1,2])

    def test_discussion_and_exam_are_not_silently_skipped(self):
        for kind in ('discussion','exam'):
            with self.subTest(kind=kind):
                self.page.evaluate('kind=>{catalogue[1][0]=kind;renderDirectory()}', kind)
                self.flow.start(ModelSettings(base_url='http://localhost:1', model='unused', auto_submit=True))
                self.run_until(lambda: self.flow.stage == 'blocked')
                self.assertEqual(self.page.evaluate('window.navClicks'), [])

    def test_locked_homework_is_not_clicked(self):
        self.page.evaluate('catalogue[1][3]=true;renderDirectory()')
        self.flow.start(ModelSettings(base_url='http://localhost:1', model='unused'))
        self.run_until(lambda: self.flow.stage == 'blocked')
        self.assertEqual(self.page.evaluate('window.navClicks'), [])

    def test_failed_route_is_not_clicked_again_or_marked_arrived(self):
        self.page.evaluate('window.deferNavigation=true')
        self.flow.start(ModelSettings(base_url='http://localhost:1', model='unused'))
        self.run_until(lambda: self.flow.stage == 'navigating')
        for _ in range(4):
            self.step()
        self.assertEqual(self.page.evaluate('window.navClicks'), [1])
        self.assertEqual(self.flow.stage, 'navigating')
        self.flow.deadline = 0
        self.step()
        self.assertEqual(self.flow.stage, 'blocked')
        self.assertEqual(self.flow.progress.load_url(self.engine.config), URL)

    def test_manual_directory_change_holds_before_filling_new_assignment(self):
        self.flow.start(ModelSettings(base_url='http://localhost:1', model='unused'))
        self.step(end_video=False)
        self.page.evaluate('goLeaf(3)')
        self.step(end_video=False)
        self.assertEqual(self.flow.stage, 'blocked')
        self.assertEqual(self.page.evaluate('window.submits'), [])

    def test_stop_cancels_future_directory_actions(self):
        self.flow.start(ModelSettings(base_url='http://localhost:1', model='unused'))
        self.step()
        self.flow.stop()
        for _ in range(4):
            self.step()
        self.assertEqual(self.page.evaluate('window.navClicks'), [])
        self.assertFalse(self.flow.active)

    def test_legacy_video_only_mode_keeps_skipping_homework(self):
        self.page.evaluate('window.forceEnded=true')
        self.engine._poll()
        self.page.wait_for_timeout(1050)
        self.engine._poll()
        self.assertEqual(self.page.evaluate('window.navClicks'), [2])

    def test_unified_mode_does_not_use_legacy_next_video(self):
        self.flow.start(ModelSettings(base_url='http://localhost:1', model='unused'))
        self.step()
        self.assertEqual(self.flow.stage, 'advance-wait')
        self.assertEqual(self.page.evaluate('window.navClicks'), [])

    def test_unsent_draft_at_second_question_can_return_to_first(self):
        self.page.evaluate("window.resumeIndex=2;window.jobs['200-2']={answer:'B',count:0};goLeaf(1)")
        with MockServer() as server:
            self.flow.start(ModelSettings(base_url=server.url, model='mock', auto_submit=True))
            self.run_until(lambda: self.flow.stage == 'video', end_video=False)
            self.assertEqual(self.page.evaluate('window.submits'), ['200-1','200-2'])
            self.assertEqual(len(server.requests), 1)
            self.assertFalse(self.page.evaluate('!!window.badSubmit'))

    def test_saved_submissions_do_not_call_model_or_submit_again(self):
        self.page.evaluate("jobs['200-1']={answer:'B',count:1,time:'1'};jobs['200-2']={answer:'B',count:1,time:'2'};goLeaf(1)")
        with MockServer() as server:
            self.flow.start(ModelSettings(base_url=server.url, model='mock', auto_submit=True))
            self.run_until(lambda: self.flow.stage == 'video', end_video=False)
            self.assertEqual(self.page.evaluate('window.submits'), [])
            self.assertEqual(len(server.requests), 0)

    def test_enabling_auto_submit_at_review_submits_drafts_before_leaving(self):
        self.page.evaluate('goLeaf(1)')
        with MockServer() as server:
            settings = ModelSettings(base_url=server.url, model='mock')
            self.flow.start(settings)
            self.run_until(lambda: self.flow.stage == 'review')
            settings.auto_submit = True
            self.flow.start(settings)
            self.run_until(lambda: self.flow.stage == 'video', end_video=False)
            self.assertEqual(self.page.evaluate('window.submits'), ['200-1','200-2'])
            self.assertEqual(len(server.requests), 2)

    def test_verification_during_homework_entry_holds_then_continues_course(self):
        self.page.evaluate('goLeaf(1)')
        self.page.evaluate('html=>document.body.insertAdjacentHTML("beforeend",html)', SECURITY)
        with MockServer() as server:
            self.flow.start(ModelSettings(base_url=server.url, model='mock', auto_submit=True))
            self.run_until(lambda: bool(self.engine.homework.verification_wait))
            self.assertEqual(len(server.requests), 0)
            old_deadline = self.flow.deadline
            self.flow.last_tick -= 120
            self.step()
            self.assertGreater(self.flow.deadline, old_deadline+100)
            self.page.locator('#security').evaluate('el=>el.remove()')
            self.run_until(lambda: self.flow.stage == 'video', end_video=False)
            self.assertEqual(self.page.evaluate('window.submits'), ['200-1','200-2'])

    def test_pause_keeps_completed_model_result_until_resume(self):
        self.page.evaluate('goLeaf(1)')
        with MockServer() as server:
            self.flow.start(ModelSettings(base_url=server.url, model='mock', auto_submit=True))
            self.run_until(lambda: self.engine.homework.pending is not None)
            self.flow.toggle_pause()
            for _ in range(4):
                self.step()
            self.assertEqual(self.page.locator('input:checked').count(), 0)
            self.assertEqual(self.page.evaluate('window.navClicks'), [1])
            self.flow.toggle_pause()
            self.run_until(lambda: self.flow.stage == 'video', end_video=False)
            self.assertEqual(len(server.requests), 2)
            self.assertEqual(self.page.evaluate('window.submits'), ['200-1','200-2'])

    def test_read_only_unsubmitted_question_does_not_advance_to_video(self):
        self.page.evaluate('goLeaf(1);document.querySelectorAll("input").forEach(el=>el.disabled=true)')
        self.flow.start(ModelSettings(base_url='http://localhost:1', model='unused', auto_submit=True))
        self.run_until(lambda: self.flow.stage == 'blocked')
        self.assertEqual(self.page.evaluate('window.navClicks'), [1])

    def test_transient_route_read_error_can_recover(self):
        from playwright.sync_api import Error as BrowserError
        self.flow.start(ModelSettings(base_url='http://localhost:1', model='unused'))
        with patch.object(self.flow, '_snapshot', side_effect=BrowserError('context replaced')):
            self.flow.tick()
        self.assertFalse(self.flow.paused)
        self.step(end_video=False)
        self.assertEqual(self.flow.stage, 'video')

    def test_original_directory_preserves_homework_order(self):
        from html import escape
        from tests.test_player import LeafParser, ROOT
        parser = LeafParser()
        parser.feed((ROOT / '源码.txt').read_text(encoding='utf-8-sig'))
        html = ''.join('<div class="leaf-item'+(' is-active' if item['active'] else '')+'"><span class="leaf-item-tag">'+
                       escape(item['tag'])+'</span><span class="leaf-item-title">'+escape(item['title'])+'</span></div>' for item in parser.items)
        self.page.set_content(html)
        self.page.evaluate(COURSE_SCRIPT)
        snap = self.page.evaluate('window.__yktCourse.snapshot()')
        self.assertEqual(len(snap['items']), 65)
        self.assertEqual([item['kind'] for item in snap['items'][:3]], ['video','homework','video'])

    def test_other_homework_tab_is_not_used_while_selected_assignment_loads(self):
        self.page.evaluate('window.intro=true;goLeaf(1)')
        other = self.context.new_page()
        other.goto(BASE+'exercise/201')
        other.evaluate('goLeaf(3)')
        with MockServer() as server:
            self.flow.start(ModelSettings(base_url=server.url, model='mock', auto_submit=True))
            self.run_until(lambda: self.flow.stage == 'video', end_video=False)
            self.assertEqual(self.page.evaluate('window.submits'), ['200-1','200-2'])
            self.assertEqual(other.evaluate('window.submits'), [])

    def test_homework_failure_cannot_be_overridden_by_later_completion_event(self):
        self.flow.active = True
        self.flow.stage = 'homework'
        self.flow.settings = ModelSettings(auto_submit=True)
        self.flow.on_homework({'state':'skipped','question':{'read_only':True,'submitted':False}})
        self.flow.on_homework({'state':'complete','message':'end'})
        self.assertEqual(self.flow.terminal['state'], 'blocked')

    def test_review_continue_uses_manually_selected_video_without_second_continue(self):
        self.page.evaluate('goLeaf(1)')
        with MockServer() as server:
            settings = ModelSettings(base_url=server.url,model='mock')
            self.flow.start(settings)
            self.run_until(lambda: self.flow.stage == 'review')
            self.page.evaluate('goLeaf(2)')
            self.flow.start(settings)
            self.run_until(lambda: self.flow.stage == 'video', end_video=False)
            self.assertEqual(self.page.evaluate('window.navClicks'), [1,2])

    def test_course_pause_command_does_not_change_standalone_video_mode(self):
        self.assertFalse(self.engine.paused)
        self.engine.command('course_pause')
        self.engine._handle_commands()
        self.assertFalse(self.engine.paused)

    def test_manual_question_inspection_exits_unified_mode(self):
        self.flow.start(ModelSettings(base_url='http://localhost:1',model='unused'))
        self.step(end_video=False)
        self.engine.command('homework_read')
        self.engine._handle_commands()
        self.assertFalse(self.flow.active)

    def test_login_dashboard_resumes_homework_checkpoint_instead_of_initial_video(self):
        target = BASE+'exercise/200?node_id=200'
        self.engine.initial_course_url = target
        self.page.goto(ORIGIN+'/v2/web/index')
        self.assertTrue(self.engine._navigate_landing([self.page]))
        self.assertEqual(self.page.url, target)

    def test_closing_course_page_during_verification_does_not_wait_forever(self):
        self.page.evaluate('goLeaf(1)')
        self.page.evaluate('html=>document.body.insertAdjacentHTML("beforeend",html)', SECURITY)
        self.flow.start(ModelSettings(base_url='http://localhost:1',model='unused'))
        self.run_until(lambda: bool(self.engine.homework.verification_wait))
        self.page.close()
        self.flow.tick()
        self.assertEqual(self.flow.stage, 'blocked')
