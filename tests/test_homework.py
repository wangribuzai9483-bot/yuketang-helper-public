from concurrent.futures import Future
import json
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from yuketang_helper.config import Config
from yuketang_helper.homework import HomeworkSession, HOMEWORK_SCRIPT
from yuketang_helper.llm import ModelSettings
from yuketang_helper.ocr import RenderedTextReader, OcrError
from yuketang_helper.verification import verification_state
from tests.test_llm import MockServer

URL = "https://school.yuketang.cn/ai-workspace/lms-graph/100001/exercise/200002"
HEADER = '<meta charset="utf-8"><div class="learning-space-header__primary">我的课程</div>'
SINGLE = '''<div class="container-problem" data-question-id="1" data-question-type="single">
<div class="problem-body">《史记》的作者是谁？</div>
<label><input type="radio" name="answer" value="A">A. 班固</label>
<label><input type="radio" name="answer" value="B">B. 司马迁</label></div>'''

GUARDED = SINGLE.replace('</label></div>', '''</label><div class="problem-fixedbar">
  <div class="text-center"><button type="button" id="submit" onclick="window.submits++">提交</button></div>
  <div class="text-right"><button type="button" id="next" onclick="window.nextClicks++;document.querySelector('#guard').style.display='block'">下一题</button></div>
  </div></div>''') + '''
  <div id="guard" class="el-dialog homework-problem-change-guard-dialog" role="dialog" style="display:none">
    <div>刚刚答的习题还没有提交，需要提交吗？</div>
    <div class="rain-btn" onclick="window.declineClicks++;window.moveQuestion()"><span>先不用了</span></div>
    <button type="button" data-risk-action="submit" onclick="window.submits++">帮我提交</button>
  </div>
  <button type="button" id="unrelated-next" onclick="window.unrelatedClicks++">下一题</button>
  <script>
  window.submits=0;window.nextClicks=0;window.declineClicks=0;window.unrelatedClicks=0;window.drafts={};
  window.moveQuestion=()=>{
    const root=document.querySelector('.container-problem');
    const old=Number(root.dataset.questionId);
    window.drafts[old]=[...root.querySelectorAll('input:checked')].map(el=>el.value);
    root.dataset.questionId=String(old+1);
    root.querySelector('.problem-body').textContent='第'+(old+1)+'题';
    root.querySelectorAll('input').forEach(el=>el.checked=false);
    document.querySelector('#next').disabled=old+1>=3;
    document.querySelector('#guard').style.display='none';
  };
  </script>'''

SECURITY = '''<div id="security" role="dialog"><h3>请完成安全核验</h3>
  <button type="button" onclick="window.challengeTouches=(window.challengeTouches||0)+1">人工核验控件</button></div>'''
SUBMITTABLE = SINGLE.replace('data-question-id="1"','data-question-id="1" data-submission-count="0"').replace('</label></div>', '''</label>
  <div class="problem-fixedbar"><div class="text-center"><button type="button" id="submit" onclick="window.submitAction()">提交</button></div>
  <div class="text-right"><button type="button" id="next" onclick="window.moveSubmittedQuestion()">下一题</button></div></div></div>''') + '''<script>
  window.submitClicks=0;window.nextClicks=0;window.nextWithoutAck=0;window.submittedAnswers={};
  window.confirmSubmission=()=>{
    const root=document.querySelector('.container-problem');
    root.dataset.submissionCount=String(Number(root.dataset.submissionCount)+1);
    root.dataset.submitTime=String(Date.now());
    window.submittedAnswers[root.dataset.questionId]=[...root.querySelectorAll('input:checked')].map(el=>el.value);
  };
  window.submitAction=()=>{window.submitClicks++;setTimeout(window.confirmSubmission,40);};
  window.moveSubmittedQuestion=()=>{
    window.nextClicks++;
    const root=document.querySelector('.container-problem');
    if(!Number(root.dataset.submissionCount)) window.nextWithoutAck++;
    const id=Number(root.dataset.questionId)+1;
    root.dataset.questionId=String(id);root.dataset.submissionCount='0';root.dataset.submitTime='';
    root.querySelector('.problem-body').textContent='第'+id+'题';
    root.querySelectorAll('input').forEach(el=>el.checked=false);
    document.querySelector('#next').disabled=id>=3;
  };
  </script>'''


class HomeworkTests(unittest.TestCase):
    def test_learning_reads_only_visible_result_labels_as_correctness(self):
        self.content(SUBMITTABLE)
        self.assertEqual(self.read()['correctness'],'unknown')
        self.page.locator('.container-problem').evaluate("el=>el.insertAdjacentHTML('beforeend','<div class=problem-answer-result hidden>回答正确</div>')")
        self.assertEqual(self.read()['correctness'],'unknown')
        self.page.locator('.problem-answer-result').evaluate('el=>el.hidden=false')
        self.assertEqual(self.read()['correctness'],'correct')
        self.page.locator('.problem-answer-result').evaluate("el=>el.textContent='回答半对'")
        self.assertEqual(self.read()['correctness'],'partial')

    def test_completed_assignment_records_every_question_then_reviews_once(self):
        import tempfile
        from yuketang_helper.learning import LearningService, LearningStore
        self.online_fixture(SUBMITTABLE)
        owner=self.owner()
        with tempfile.TemporaryDirectory() as folder:
            service=LearningService(LearningStore(Path(folder)/'learning.db'),key_loader=lambda endpoint:'')
            owner.learning=service
            session=HomeworkSession(owner)
            def review(rows):
                return [{'topic':'课程知识','summary':'复习材料','techniques':[], 'record_ids':[r['id'] for r in rows]}]
            try:
                with MockServer() as server, patch('yuketang_helper.learning.ModelClient.review',side_effect=review) as model:
                    settings=ModelSettings(base_url=server.url,model='mock',review_summary=True)
                    session.start(settings,batch=True,auto_submit=True)
                    self.run_until_idle(session,deadline=15)
                    service.executor.shutdown(wait=True)
                    rows=service.store.records()
                    self.assertEqual(len(rows),3)
                    self.assertTrue(all(r['status']=='submitted' and r['correctness']=='unknown' for r in rows))
                    self.assertTrue(all(r['answer']['option_ids']==['B'] and r['answer_source']=='model' for r in rows))
                    self.assertEqual(model.call_count,1)
                    self.assertEqual(len(model.call_args.args[0]),3)
                    self.assertEqual(len(server.requests),3)
                    self.assertEqual(len(service.store.experiences()[0]['record_ids']),3)
            finally:session.close();service.close()

    def test_learning_failures_do_not_interrupt_question_submission(self):
        self.online_fixture(SUBMITTABLE)
        owner=self.owner()
        from unittest.mock import Mock
        owner.learning=Mock()
        owner.learning.record.side_effect=OSError('local storage failure')
        owner.learning.request_review.side_effect=ValueError('review unavailable')
        session=HomeworkSession(owner)
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock',review_summary=True),batch=True,auto_submit=True)
                self.run_until_idle(session,deadline=15)
                self.assertEqual(session.submitted_count,3)
                self.assertTrue(any(data.get('state')=='complete' for kind,data in owner.events if kind=='homework'))
                owner.learning.request_review.assert_called_once()
        finally:session.close()

    def reactive_multiple_fixture(self):
        self.online_fixture('''<div class="container-problem" data-question-id="reactive-multi" data-question-type="multiple">
          <div class="problem-body">请选择所有符合题意的选项</div><div id="choices"></div>
          <div class="problem-fixedbar"><button type="button" onclick="window.submitReactive()">提交</button>
          <button type="button" disabled>下一题</button></div></div><script>
          window.problem={problem_id:'reactive-multi',index:1,content:{ProblemType:2,Body:'请选择所有符合题意的选项',
            Options:[{key:'A',value:'甲'},{key:'B',value:'乙'},{key:'C',value:'丙'},{key:'D',value:'丁'}]},user:{my_count:0},_answer:[]};
          document.querySelector('.container-problem').__vue__={problem:window.problem};
          window.checkboxProps=[];window.submitClicks=0;
          window.renderChoices=()=>{
            document.querySelector('#choices').innerHTML=problem.content.Options.map(o=>
              `<label class="option-item"><input type="checkbox" value="${o.key}" ${checkboxProps.includes(o.key)?'checked':''}>
              <span class="option-item__text">${o.value}</span></label>`).join('');
            document.querySelectorAll('input').forEach(input=>{
              input.closest('label').__vue__={$nextTick:fn=>setTimeout(fn,0)};
              input.addEventListener('change',()=>{
                const next=checkboxProps.filter(id=>id!==input.value);if(input.checked)next.push(input.value);
                problem._answer=next;
                setTimeout(()=>{window.checkboxProps=[...problem._answer];window.renderChoices();},0);
              });
            });
          };window.renderChoices();
          window.submitReactive=()=>{window.submitClicks++;fetch('/mooc-api/v1/lms/exercise/problem_apply/',{
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({classroom_id:100001,
              problem_id:problem.problem_id,answer:[...problem._answer]})
          }).then(r=>r.json()).then(r=>{if(r.success)problem.user.my_count++;});};
          </script>''')
        self.page.evaluate(HOMEWORK_SCRIPT)

    def test_reactive_multiselect_rechecks_component_model_before_submit(self):
        self.reactive_multiple_fixture()
        self.assertTrue(self.fill({'option_ids':['A','C','D']}))
        self.page.evaluate("() => {problem._answer=['D'];}")
        q=self.read()
        self.assertFalse(self.page.evaluate('p=>window.__yktHomework.verify(p)',{'fingerprint':q['fingerprint'],'answer':{'option_ids':['A','C','D']}}))
        result=self.page.evaluate('p=>window.__yktHomework.submitCurrent(p)',{'fingerprint':q['fingerprint'],
            'draft_fingerprint':q['draft_fingerprint'],'expected_answer':{'option_ids':['A','C','D']}})
        self.assertEqual(result['state'],'answer-mismatch')
        self.assertEqual(self.page.evaluate('window.submitClicks'),0)

    def test_reactive_multiselect_resumes_remaining_options_after_manual_verification(self):
        self.reactive_multiple_fixture()
        posts=[]
        self.context.route('**/mooc-api/v1/lms/exercise/problem_apply/',lambda route:(posts.append(route.request.post_data_json),
            route.fulfill(body='{"success":true,"data":{"my_count":1}}',content_type='application/json')))
        self.page.evaluate('''html=>document.querySelector('input[value="A"]').addEventListener('change',()=>{
          document.body.insertAdjacentHTML('beforeend',html);
        },{once:true})''',SECURITY)
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                server.content='{"option_ids":["A","C","D"]}'
                session.start(ModelSettings(base_url=server.url,model='mock'),auto_submit=True)
                until=time.monotonic()+5
                while time.monotonic()<until and not session.verification_wait:
                    session.tick();self.page.wait_for_timeout(25)
                self.assertIsNotNone(session.verification_wait)
                self.assertEqual(self.page.evaluate('window.problem._answer'),['A'])
                self.assertEqual(posts,[])
                self.finish_local_verification(session)
                self.run_until_idle(session)
                self.assertEqual(len(posts),1)
                self.assertEqual(sorted(posts[0]['answer']),['A','C','D'])
                self.assertEqual(len(server.requests),1)
        finally:session.close()

    def test_mixed_reactive_multiple_and_single_questions_continue_to_assignment_end(self):
        self.reactive_multiple_fixture()
        self.page.evaluate('''() => {
          const root=document.querySelector('.container-problem'), baseRender=window.renderChoices;
          window.currentQuestion=1;window.badNext=0;
          window.renderChoices=()=>{baseRender();if(problem.content.ProblemType===1){
            document.querySelectorAll('input').forEach(input=>{input.type='radio';input.name='one';});
          }};
          root.querySelector('.problem-fixedbar').insertAdjacentHTML('beforebegin','<div class="problems-aside">'+[1,2,3].map(i=>`<div class="subject-item" data-order="${i}">${i}</div>`).join('')+'</div>');
          window.showQuestion=index=>{
            window.currentQuestion=index;problem.problem_id='mixed-'+index;problem.index=index;
            problem.content.ProblemType=index===2?1:2;problem.user={my_count:0};problem._answer=[];window.checkboxProps=[];
            root.dataset.questionId=problem.problem_id;root.dataset.questionIndex=String(index);
            root.querySelector('.problem-body').textContent='第'+index+'题';
            root.querySelectorAll('.subject-item').forEach(el=>el.classList.toggle('active',Number(el.dataset.order)===index));
            root.querySelector('.problem-fixedbar button:last-child').disabled=index===3;
            window.renderChoices();
          };
          root.querySelector('.problem-fixedbar button:last-child').onclick=()=>{
            if(!problem.user.my_count){window.badNext++;return;}showQuestion(currentQuestion+1);
          };
          window.submitReactive=()=>{window.submitClicks++;const id=problem.problem_id,index=currentQuestion;
            fetch('/mooc-api/v1/lms/exercise/problem_apply/',{method:'POST',headers:{'Content-Type':'application/json'},
              body:JSON.stringify({classroom_id:100001,problem_id:id,answer:[...problem._answer]})
            }).then(r=>r.json()).then(r=>{if(r.success&&problem.problem_id===id){problem.user.my_count++;
              root.querySelector(`[data-order="${index}"]`).classList.add('primary');}});
          };
          showQuestion(1);
        }''')
        posts=[]
        self.context.route('**/mooc-api/v1/lms/exercise/problem_apply/',lambda route:(posts.append(route.request.post_data_json),
            route.fulfill(body='{"success":true,"data":{"my_count":1}}',content_type='application/json')))
        session=HomeworkSession(self.owner())
        def answer(question):return {'option_ids':['A','C','D'] if question['type']=='multiple' else ['B']}
        try:
            with patch('yuketang_helper.homework.ModelClient.solve',side_effect=answer) as model:
                session.start(ModelSettings(base_url='http://localhost:1234',model='mock'),batch=True,auto_submit=True)
                self.run_until_idle(session,deadline=15)
                self.assertEqual([p['problem_id'] for p in posts],['mixed-1','mixed-2','mixed-3'])
                self.assertEqual([sorted(p['answer']) for p in posts],[['A','C','D'],['B'],['A','C','D']])
                self.assertEqual(session.submitted_count,3)
                self.assertEqual(self.page.evaluate('window.badNext'),0)
                self.assertEqual(model.call_count,3)
        finally:session.close()

    def test_reactive_multiselect_keeps_all_options_after_component_updates(self):
        self.reactive_multiple_fixture()
        self.page.evaluate(HOMEWORK_SCRIPT)
        q=self.read()
        self.assertTrue(self.fill({'option_ids':['A','C','D']},question=q))
        self.page.wait_for_timeout(50)
        self.assertEqual(sorted(self.page.evaluate('window.problem._answer')),['A','C','D'])
        self.assertEqual(self.page.locator('input:checked').evaluate_all('els=>els.map(el=>el.value)'),['A','C','D'])

    def test_reactive_multiselect_can_fill_again_after_user_clears_draft(self):
        self.reactive_multiple_fixture()
        answer={'option_ids':['A','C','D']}
        self.assertTrue(self.fill(answer))
        for option_id in answer['option_ids']:
            self.page.locator(f'input[value="{option_id}"]').click()
            self.page.wait_for_timeout(30)
        self.assertEqual(self.page.evaluate('window.problem._answer'),[])
        self.assertTrue(self.fill(answer))
        self.assertEqual(sorted(self.page.evaluate('window.problem._answer')),['A','C','D'])

    def test_reactive_multiselect_submits_all_requested_options(self):
        self.reactive_multiple_fixture()
        posts=[]
        def accept(route):
            posts.append(route.request.post_data_json)
            route.fulfill(body='{"success":true,"data":{"my_count":1}}',content_type='application/json')
        self.context.route('**/mooc-api/v1/lms/exercise/problem_apply/',accept)
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                server.content='{"option_ids":["A","C","D"],"explanation":"fixture"}'
                session.start(ModelSettings(base_url=server.url,model='mock'),auto_submit=True)
                self.run_until_idle(session)
                self.assertEqual(len(posts),1)
                self.assertEqual(sorted(posts[0]['answer']),['A','C','D'])
        finally:session.close()

    def network_submit_fixture(self, intercept=False, confirm_dom=True):
        self.online_fixture(SUBMITTABLE)
        posts=[]
        def accept(route):
            posts.append(route.request.post_data_json)
            route.fulfill(body=json.dumps({'success':True,'data':{'my_count':1}}),content_type='application/json')
        self.context.route('**/mooc-api/v1/lms/exercise/problem_apply/',accept)
        self.page.evaluate('''p=>{
          window.blockFirst=p.intercept;
          window.sendNormalSubmission=()=>fetch('/mooc-api/v1/lms/exercise/problem_apply/',{
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
              classroom_id:100001,problem_id:document.querySelector('.container-problem').dataset.questionId,
              answer:[document.querySelector('input:checked')?.value]})
          }).then(r=>r.json()).then(data=>{if(data.success&&p.confirmDom)window.confirmSubmission();}).catch(()=>{});
          window.submitAction=()=>{window.submitClicks++;
            if(window.blockFirst){window.blockFirst=false;document.body.insertAdjacentHTML('beforeend',p.security);return;}
            window.sendNormalSubmission();
          };
        }''',{'intercept':intercept,'confirmDom':confirm_dom,'security':SECURITY})
        return posts

    def finish_local_verification(self, session):
        self.page.locator('#security').evaluate('el=>el.remove()')
        session.tick()
        session.verification_wait['clear_since']-=2
        session.tick()
        if session.submission:
            session.submission['verification_completed_at']-=3

    def test_manual_verification_resends_only_the_submission_that_was_not_sent(self):
        posts=self.network_submit_fixture(intercept=True)
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),auto_submit=True,batch=True)
                until=time.monotonic()+5
                while time.monotonic()<until and not session.verification_wait:
                    session.tick();self.page.wait_for_timeout(25)
                self.assertIsNotNone(session.verification_wait)
                self.assertEqual(posts,[])
                self.assertEqual(session.submission['probe'].outcome,'not-sent')
                self.finish_local_verification(session)
                self.run_until_idle(session,deadline=15)
                self.assertEqual(session.submitted_count,3)
                self.assertEqual([str(p['problem_id']) for p in posts],['1','2','3'])
                self.assertEqual(self.page.evaluate('window.submitClicks'),4)
                self.assertEqual(len(server.requests),3)
        finally:
            session.close()

    def test_ignored_click_is_retried_once_without_repeating_a_backend_submission(self):
        posts=self.network_submit_fixture()
        self.page.evaluate('''() => {const normal=window.submitAction;window.submitAction=()=>{
          window.submitAction=normal;window.submitClicks++;
        };}''')
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),auto_submit=True)
                until=time.monotonic()+5
                while time.monotonic()<until and not(session.submission and session.submission['clicked']):
                    session.tick();self.page.wait_for_timeout(25)
                session.submission['clicked_at']-=6
                self.run_until_idle(session)
                self.assertEqual(session.submitted_count,1)
                self.assertEqual(len(posts),1)
                self.assertEqual(self.page.evaluate('window.submitClicks'),2)
                self.assertEqual(len(server.requests),1)
        finally:
            session.close()

    def test_continue_can_recover_a_not_sent_history_entry_instead_of_duplicate_lock(self):
        posts=self.network_submit_fixture()
        self.page.evaluate('''() => {window.normalSubmit=window.submitAction;window.submitAction=()=>{window.submitClicks++;};}''')
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                settings=ModelSettings(base_url=server.url,model='mock')
                session.start(settings,auto_submit=True)
                until=time.monotonic()+5
                while time.monotonic()<until and not(session.submission and session.submission['clicked']):
                    session.tick();self.page.wait_for_timeout(25)
                session.submission['deadline']=0
                session.tick()
                self.assertIsNone(session.submission)
                self.page.evaluate('() => {window.submitAction=window.normalSubmit;}')
                session.start(settings,auto_submit=True)
                self.run_until_idle(session)
                self.assertEqual(session.submitted_count,1)
                self.assertEqual(len(posts),1)
                self.assertEqual(len(server.requests),1)
        finally:
            session.close()

    def test_accepted_network_response_is_confirmed_even_if_page_count_stays_stale(self):
        posts=self.network_submit_fixture(confirm_dom=False)
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                settings=ModelSettings(base_url=server.url,model='mock')
                session.start(settings,auto_submit=True)
                self.run_until_idle(session)
                self.assertEqual(session.submitted_count,1)
                self.assertEqual(self.page.locator('.container-problem').get_attribute('data-submission-count'),'0')
                session.start(settings,auto_submit=True)
                self.run_until_idle(session)
                self.assertEqual(self.page.evaluate('window.submitClicks'),1)
                self.assertEqual(len(posts),1)
                self.assertEqual(len(server.requests),1)
        finally:
            session.close()

    def test_disabled_next_with_later_sidebar_questions_is_not_assignment_end(self):
        posts=self.network_submit_fixture(confirm_dom=False)
        self.page.evaluate('''() => {document.querySelector('.container-problem').dataset.questionIndex='1';
          document.querySelector('#next').disabled=true;
          document.body.insertAdjacentHTML('beforeend','<div class="problems-aside"><div class="subject-item active" data-order="1">1</div><div class="subject-item" data-order="2">2</div></div>');
        }''')
        owner=self.owner();session=HomeworkSession(owner)
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),auto_submit=True,batch=True)
                until=time.monotonic()+5
                while time.monotonic()<until and session.submitted_count<1:
                    session.tick();self.page.wait_for_timeout(25)
                self.assertEqual(session.submitted_count,1)
                session.advance_at=(0,session.advance_at[1]);session.tick()
                self.assertTrue(session.batch)
                self.assertIsNotNone(session.advance_at)
                session.advance_ready_deadline=0
                session.advance_at=(0,session.advance_at[1]);session.tick()
                self.assertFalse(session.batch)
                self.assertFalse(any(kind=='homework' and data['state']=='complete' for kind,data in owner.events))
                self.assertEqual(len(posts),1)
        finally:
            session.close()

    def test_inflight_submission_is_not_resent_after_manual_verification(self):
        self.network_submit_fixture()
        pending=[]
        self.context.route('**/mooc-api/v1/lms/exercise/problem_apply/',lambda route:pending.append(route))
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),auto_submit=True)
                until=time.monotonic()+5
                while time.monotonic()<until and not pending:
                    session.tick();self.page.wait_for_timeout(25)
                self.assertEqual(session.submission['probe'].outcome,'pending')
                self.page.evaluate('html=>document.body.insertAdjacentHTML("beforeend",html)',SECURITY)
                session.tick()
                self.finish_local_verification(session)
                session.submission['clicked_at']-=10
                for _ in range(4):session.tick()
                self.assertEqual(self.page.evaluate('window.submitClicks'),1)
                self.assertEqual(len(pending),1)
                pending[0].fulfill(body='{"success":true,"data":{"my_count":1}}',content_type='application/json')
                self.run_until_idle(session)
                self.assertEqual(session.submitted_count,1)
                self.assertEqual(self.page.evaluate('window.submitClicks'),1)
        finally:
            session.close()

    def test_explicitly_rejected_submission_can_resume_after_manual_verification(self):
        self.network_submit_fixture()
        posts=[]
        def reject_then_accept(route):
            posts.append(route.request.post_data_json)
            route.fulfill(body=json.dumps({'success':len(posts)>1,'data':{'my_count':1}}),content_type='application/json')
        self.context.route('**/mooc-api/v1/lms/exercise/problem_apply/',reject_then_accept)
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),auto_submit=True)
                until=time.monotonic()+5
                while time.monotonic()<until and not(session.submission and session.submission['probe'].outcome=='rejected'):
                    session.tick();self.page.wait_for_timeout(25)
                self.assertEqual(len(posts),1)
                self.page.evaluate('html=>document.body.insertAdjacentHTML("beforeend",html)',SECURITY)
                session.tick();self.finish_local_verification(session)
                self.run_until_idle(session)
                self.assertEqual(len(posts),2)
                self.assertEqual(session.submitted_count,1)
                self.assertEqual(len(server.requests),1)
        finally:
            session.close()

    def test_verified_current_only_confirmation_is_retried_without_batch_submit(self):
        posts=self.network_submit_fixture()
        self.page.evaluate('''html=>{
          window.confirmClicks=0;window.allClicks=0;
          window.confirmCurrent=()=>{window.confirmClicks++;
            if(window.confirmClicks===1){document.body.insertAdjacentHTML('beforeend',html);return;}
            window.sendNormalSubmission().then(()=>document.querySelector('#batch').remove());
          };
          window.submitAction=()=>{window.submitClicks++;document.body.insertAdjacentHTML('beforeend',
            `<div class="el-dialog" id="batch" role="dialog">3道已作答习题未提交，需要一起提交吗？
            <button type="button" onclick="window.confirmCurrent()">仅提交本题</button>
            <button type="button" onclick="window.allClicks++">帮我提交</button></div>`);};
        }''',SECURITY)
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),auto_submit=True)
                until=time.monotonic()+5
                while time.monotonic()<until and not session.verification_wait:
                    session.tick();self.page.wait_for_timeout(25)
                self.assertIsNotNone(session.verification_wait)
                self.finish_local_verification(session)
                self.run_until_idle(session)
                self.assertEqual(len(posts),1)
                self.assertEqual(session.submitted_count,1)
                self.assertEqual(self.page.evaluate('window.submitClicks'),1)
                self.assertEqual(self.page.evaluate('window.confirmClicks'),2)
                self.assertEqual(self.page.evaluate('window.allClicks'),0)
        finally:
            session.close()

    def test_changed_draft_after_verification_is_not_automatically_resubmitted(self):
        posts=self.network_submit_fixture(intercept=True)
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),auto_submit=True)
                until=time.monotonic()+5
                while time.monotonic()<until and not session.verification_wait:
                    session.tick();self.page.wait_for_timeout(25)
                self.page.locator('input[value="A"]').evaluate('el=>el.checked=true')
                self.finish_local_verification(session)
                for _ in range(3):session.tick()
                self.assertEqual(posts,[])
                self.assertEqual(self.page.evaluate('window.submitClicks'),1)
        finally:
            session.close()
    def auto_advance_fixture(self, reset_helper=False):
        self.online_fixture(SUBMITTABLE)
        self.page.evaluate('''reset => {
          const sidebar=document.createElement('div');sidebar.className='problems-aside';
          sidebar.innerHTML=[1,2,3].map(i=>`<div class="subject-item ${i===1?'active':''}" data-order="${i}">${i}</div>`).join('');
          document.body.prepend(sidebar);
          document.querySelector('.container-problem').dataset.questionIndex='1';
          const confirm=window.confirmSubmission;
          window.confirmSubmission=()=>{
            const id=Number(document.querySelector('.container-problem').dataset.questionId);
            confirm();document.querySelector(`[data-order="${id}"]`).classList.add('primary');
            if(id<3){
              window.moveSubmittedQuestion();
              document.querySelector('.container-problem').dataset.questionIndex=String(id+1);
              document.querySelectorAll('.subject-item').forEach(el=>el.classList.toggle('active',Number(el.dataset.order)===id+1));
            }
            if(reset)delete window.__yktHomework;
          };
        }''', reset_helper)

    def test_site_auto_advances_after_submit_without_extra_next_click(self):
        self.auto_advance_fixture()
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),batch=True,auto_submit=True)
                self.run_until_idle(session)
                self.assertEqual(session.completed,3)
                self.assertEqual(session.submitted_count,3)
                self.assertEqual(len(server.requests),3)
                self.assertEqual(self.page.evaluate('window.submitClicks'),3)
                self.assertEqual(self.page.evaluate('window.nextClicks'),2)
        finally:
            session.close()

    def test_submission_evidence_survives_helper_reinitialization(self):
        self.auto_advance_fixture(reset_helper=True)
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),batch=True,auto_submit=True)
                self.run_until_idle(session)
                self.assertEqual(session.submitted_count,3)
                self.assertEqual(self.page.evaluate('window.submitClicks'),3)
        finally:
            session.close()

    def test_auto_advanced_question_waits_for_temporarily_disabled_controls(self):
        self.auto_advance_fixture()
        self.page.evaluate('''() => {const confirm=window.confirmSubmission;window.confirmSubmission=()=>{
          const id=Number(document.querySelector('.container-problem').dataset.questionId);
          confirm();
          if(id<3){document.querySelectorAll('input').forEach(el=>el.disabled=true);
            setTimeout(()=>document.querySelectorAll('input').forEach(el=>el.disabled=false),500);}
        };}''')
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),batch=True,auto_submit=True)
                self.run_until_idle(session)
                self.assertEqual(session.completed,3)
                self.assertEqual(session.submitted_count,3)
                self.assertEqual(len(server.requests),3)
        finally:
            session.close()

    def test_submission_feedback_changes_fingerprint_without_breaking_batch(self):
        self.online_fixture(SUBMITTABLE)
        self.page.evaluate('''() => {const confirm=window.confirmSubmission;window.confirmSubmission=()=>{
          confirm();document.querySelector('.problem-body').append(' 已提交');
        };}''')
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),batch=True,auto_submit=True)
                self.run_until_idle(session)
                self.assertEqual(session.submitted_count,3)
                self.assertEqual(len(server.requests),3)
        finally:
            session.close()

    def test_question_change_without_submission_evidence_does_not_count_success(self):
        self.online_fixture(SUBMITTABLE)
        self.page.evaluate('() => {window.submitAction=()=>{window.submitClicks++;window.moveSubmittedQuestion();};}')
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),batch=True,auto_submit=True)
                until=time.monotonic()+5
                while time.monotonic()<until and not(session.submission and session.submission['clicked']):
                    session.tick();self.page.wait_for_timeout(30)
                for _ in range(3):session.tick()
                self.assertIsNotNone(session.submission)
                self.assertEqual(session.submitted_count,0)
                session.submission['deadline']=0
                session.tick()
                self.assertFalse(session.batch)
                self.assertEqual(len(server.requests),1)
                self.assertEqual(self.page.evaluate('window.submitClicks'),1)
        finally:
            session.close()

    def test_strong_verification_heading_pauses_notifies_and_resumes_automatically(self):
        self.online_fixture(SUBMITTABLE+SECURITY.replace('h3','strong'))
        owner=self.owner();session=HomeworkSession(owner)
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),auto_submit=True)
                self.assertIsNotNone(session.verification_wait)
                self.assertTrue(any(kind=='attention' and data['state']=='verification' for kind,data in owner.events))
                self.assertEqual(len(server.requests),0)
                self.page.locator('#security').evaluate('el=>el.remove()')
                self.run_until_idle(session)
                self.assertEqual(session.submitted_count,1)
                self.assertTrue(any(kind=='attention' and data['state']=='verification-cleared' for kind,data in owner.events))
        finally:
            session.close()

    def test_closed_shadow_verification_detected_via_accessibility(self):
        self.online_fixture()
        self.page.evaluate('''html=>{const host=document.createElement('section');host.id='verification-host';
          document.body.append(host);window.testVerification=host.attachShadow({mode:'closed'});window.testVerification.innerHTML=html;
        }''',SECURITY.replace('h3','strong'))
        state=verification_state(self.page)
        self.assertTrue(state['required'])
        self.assertEqual(state['source'],'accessible-heading')
        self.page.locator('#verification-host').evaluate("el=>el.style.opacity='0'")
        self.assertFalse(verification_state(self.page)['required'])

    def test_open_shadow_and_unusual_heading_tags_are_detected(self):
        self.online_fixture()
        self.page.evaluate('''html=>{const host=document.createElement('section');document.body.append(host);
          host.attachShadow({mode:'open'}).innerHTML=html;}''',SECURITY.replace('h3','h5'))
        self.assertTrue(verification_state(self.page)['required'])

    def test_cross_origin_closed_shadow_verification_does_not_block_after_hiding(self):
        self.online_fixture()
        self.context.route('https://verify.example.test/human-check', lambda route: route.fulfill(
            body='''<meta charset="utf-8"><section id="host"></section><script>
              document.querySelector('#host').attachShadow({mode:'closed'}).innerHTML='<strong>请完成安全校验</strong>';
              window.testReady=true;</script>''', content_type='text/html; charset=utf-8'))
        self.page.evaluate('''() => {const frame=document.createElement('iframe');frame.id='external-security';
          frame.src='https://verify.example.test/human-check#visible';document.body.append(frame);}''')
        until=time.monotonic()+5
        while time.monotonic()<until and len(self.page.frames)<2:self.page.wait_for_timeout(20)
        self.page.frames[1].wait_for_function('window.testReady')
        self.assertTrue(verification_state(self.page)['required'])
        self.page.locator('#external-security').evaluate("el=>el.style.display='none'")
        self.assertFalse(verification_state(self.page)['required'])

    def test_verification_completion_retries_intercepted_next_only_once(self):
        self.online_fixture(GUARDED)
        self.page.evaluate('''html=>{const button=document.querySelector('#next'),normal=button.onclick;
          button.onclick=()=>{button.onclick=normal;window.nextClicks++;document.body.insertAdjacentHTML('beforeend',html);};
        }''',SECURITY.replace('h3','strong'))
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),batch=True)
                until=time.monotonic()+5
                while time.monotonic()<until and not session.verification_wait:
                    session.tick();self.page.wait_for_timeout(30)
                self.assertIsNotNone(session.verification_wait)
                session.verification_wait['since']-=120
                self.page.locator('#security').evaluate('el=>el.remove()')
                session.tick()
                session.verification_wait['clear_since']-=2
                session.tick()
                self.assertGreater(session.navigation_retry_at,0)
                session.navigation_retry_at=time.monotonic()-1
                self.run_until_idle(session)
                self.assertEqual(session.completed,3)
                self.assertEqual(self.page.evaluate('window.nextClicks'),3)
                self.assertEqual(self.page.evaluate('window.submits'),0)
                self.assertEqual(len(server.requests),3)
        finally:
            session.close()

    def test_short_multiple_choice_options_are_read_by_real_windows_ocr(self):
        self.content('''<style>.option-item{display:flex;width:1500px;margin:12px;font:16px/24px "Microsoft YaHei"}
          .option-item__text{display:inline-block;width:1400px}.option-item input{position:absolute;opacity:0}</style>
          <div class="container-problem" data-question-id="multi" data-question-type="multiple">
          <div class="problem-body">世界的文化类型，可以分为哪些类型？</div>
          <label class="option-item"><input type="checkbox" value="A"><b>A</b><span class="option-item__text"><span class="xuetangx-com-encrypted-font">游牧与森林文化</span></span></label>
          <label class="option-item"><input type="checkbox" value="B"><b>B</b><span class="option-item__text"><span class="xuetangx-com-encrypted-font">宗教文化</span></span></label>
          <label class="option-item"><input type="checkbox" value="C"><b>C</b><span class="option-item__text"><span class="xuetangx-com-encrypted-font">海洋文化</span></span></label>
          <label class="option-item"><input type="checkbox" value="D"><b>D</b><span class="option-item__text"><span class="xuetangx-com-encrypted-font">农耕文化</span></span></label></div>''')
        resolved=RenderedTextReader().resolve(self.page.main_frame,self.read())
        self.assertEqual(resolved['type'],'multiple')
        text={item['id']:item['text'] for item in resolved['options']}
        self.assertIn('宗教文化',text['B'])
        self.assertIn('海洋文化',text['C'])
        self.assertIn('农耕文化',text['D'])
        self.assertTrue(self.fill({'option_ids':['A','D']},question=resolved))

    def test_real_failed_option_row_recovers_through_reader_fallback(self):
        from tests.test_ocr import FIXTURE, blank_png
        self.content('''<div class="container-problem" data-question-type="single"><div class="problem-body">北宋诗文革新运动的领袖是谁？</div>
          <label><input type="radio" name="a" value="A">欧阳修</label>
          <label><input type="radio" name="a" value="C"><span class="option-item__text"><span class="xuetangx-com-encrypted-font">柳宗元</span></span></label></div>''')
        captures=[]
        def capture(frame, selector, path, tight=True):
            captures.append(tight)
            path.write_bytes(blank_png() if tight else FIXTURE.read_bytes())
        with patch('yuketang_helper.ocr.capture_rendered',side_effect=capture):
            reader=RenderedTextReader()
            result=reader.resolve(self.page.main_frame,self.read())
            self.assertEqual(captures,[True,False])
            self.assertIn('柳宗元',result['options'][1]['text'])
            self.assertFalse(result['needs_ocr'])
            self.assertTrue(self.fill({'option_ids':['A']},question=result))
            self.assertTrue(reader.resolve(self.page.main_frame,self.read())['answered'])
            self.assertEqual(captures,[True,False])

    def test_ocr_retries_short_text_with_fallback_crop(self):
        import struct
        self.content('''<div class="container-problem" data-question-type="multiple"><div class="problem-body">测试多选题</div>
          <label><input type="checkbox" value="A"><span class="option-item__text" style="display:inline-block;width:1500px"><span class="xuetangx-com-encrypted-font">宗教文化</span></span></label></div>''')
        sizes=[]
        def recognize(path):
            sizes.append(struct.unpack('>II',path.read_bytes()[16:24]))
            if len(sizes)==1:raise OcrError('empty')
            return '宗教文化'
        result=RenderedTextReader(recognize=recognize).resolve(self.page.main_frame,self.read())
        self.assertEqual(len(sizes),2)
        self.assertLess(sizes[0][0],500)
        self.assertEqual(result['options'][0]['text'],'宗教文化')

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
        self.page = self.context.new_page()
        self.page.set_content(HEADER + SINGLE)
        self.page.evaluate(HOMEWORK_SCRIPT)

    def tearDown(self):
        self.context.close()

    def content(self, body):
        self.page.set_content(HEADER + body)
        self.page.evaluate(HOMEWORK_SCRIPT)

    def read(self):
        return self.page.evaluate("window.__yktHomework.read()")

    def fill(self, answer, overwrite=False, question=None):
        q = question or self.read()
        return self.page.evaluate("p => window.__yktHomework.fill(p)", {
            "fingerprint": q["fingerprint"], "draft_fingerprint": q["draft_fingerprint"],
            "answer": answer, "overwrite": overwrite,
        })

    def test_single_choice_updates_real_control(self):
        q = self.read()
        self.assertEqual(q["type"], "single")
        self.assertEqual(q["stem"], "《史记》的作者是谁？")
        self.assertTrue(self.fill({"option_ids": ["B"]}))
        self.assertTrue(self.page.locator('input[value="B"]').is_checked())
        self.assertTrue(self.read()["answered"])

    def test_multiple_choice_deselects_previous_option_when_overwriting(self):
        body = SINGLE.replace('data-question-type="single"', 'data-question-type="multiple"').replace('type="radio"', 'type="checkbox"')
        body = body.replace('</div>', '<label><input type="checkbox" value="C">C. 其他</label></div>', 1)
        self.content(body)
        self.page.locator('input[value="A"]').check()
        self.assertTrue(self.fill({"option_ids": ["B", "C"]}, overwrite=True))
        self.assertFalse(self.page.locator('input[value="A"]').is_checked())
        self.assertTrue(self.page.locator('input[value="C"]').is_checked())

    def test_judgment_option_ids_are_preserved(self):
        self.content(SINGLE.replace('data-question-type="single"', 'data-question-type="judgment"').replace('value="A"', 'value="true"').replace('value="B"', 'value="false"'))
        self.assertTrue(self.fill({"option_ids": ["true"]}))

    def test_blanks_dispatch_events(self):
        self.content('''<div class="container-problem" data-question-id="blank" data-question-type="blank">
        <div class="problem-body">诗仙是____，诗圣是____。</div><div class="fill-blank-body"><input><input></div></div>''')
        self.page.evaluate("document.querySelector('.container-problem').addEventListener('input', () => window.events=(window.events||0)+1)")
        self.assertEqual(self.read()["blank_count"], 2)
        self.assertTrue(self.fill({"blank_answers": ["李白", "杜甫"]}))
        self.assertEqual(self.page.evaluate("window.events"), 2)

    def test_text_is_literal_and_dispatches_change(self):
        self.content('''<div class="container-problem" data-question-type="text"><div class="problem-body">简述诗性。</div><textarea></textarea></div>''')
        self.assertTrue(self.fill({"text": "第一点\n<script>window.bad=true</script>"}))
        self.assertFalse(self.page.evaluate("!!window.bad"))
        self.assertIn("<script>", self.page.locator("textarea").input_value())

    def test_ueditor_uses_public_editor_api(self):
        self.content('''<div class="container-problem" data-question-type="text"><div class="problem-body">简述文化。</div>
        <div class="ueditor-wrapper"><div class="edui-editor-iframeholder"><iframe srcdoc="<body contenteditable='true'></body>"></iframe></div></div></div>''')
        self.page.wait_for_function("document.querySelector('iframe').contentDocument.body.isContentEditable")
        self.page.evaluate("""() => {
          const iframe=document.querySelector('iframe');
          window.UE={instants:{test:{iframe,setContent(html){iframe.contentDocument.body.innerHTML=html;},
            getContentTxt(){return iframe.contentDocument.body.innerText;},fireEvent(){window.editorChanged=true;}}}};
        }""")
        self.assertTrue(self.fill({"text": "文化的意义 <b>文本</b>"}))
        self.assertTrue(self.page.evaluate("window.editorChanged"))
        self.assertFalse(self.page.evaluate("!!document.querySelector('iframe').contentDocument.querySelector('b')"))

    def test_page_switch_and_user_edit_reject_stale_answers(self):
        q = self.read()
        self.page.locator(".problem-body").evaluate("el => el.textContent='新的题目'")
        with self.assertRaisesRegex(Exception, "题目已切换"):
            self.fill({"option_ids": ["B"]}, question=q)
        q = self.read()
        self.page.locator('input[value="A"]').check()
        with self.assertRaisesRegex(Exception, "答案已变化"):
            self.fill({"option_ids": ["B"]}, question=q, overwrite=True)

    def test_existing_answer_and_disabled_options_are_preserved(self):
        self.page.locator('input[value="A"]').check()
        with self.assertRaisesRegex(Exception, "已有答案"):
            self.fill({"option_ids": ["B"]})
        self.page.locator('input').evaluate_all("els=>els.forEach(el=>el.disabled=true)")
        self.assertTrue(self.read()["read_only"])

    def test_vue_metadata_reads_only_question_and_marks_images(self):
        self.page.locator('.container-problem').evaluate("""el => el.__vue__={problem:{problem_id:123,left_times:2,
          content:{ProblemType:1,Body:'题干<img src="https://example.com/private?auth=secret">',Options:[{key:'A',value:'甲'},{key:'B',value:'乙'}],Answer:['B']},user:{name:'private-user'}}}""")
        q = self.read()
        self.assertEqual(q["id"], "123")
        self.assertTrue(q["has_images"])
        self.assertNotIn("secret", str(q))
        self.assertNotIn("private-user", str(q))
        self.assertNotIn("Answer", q)

    def owner(self):
        events = []
        owner = SimpleNamespace(context=self.context, config=Config(course_url="https://school.yuketang.cn/ai-workspace/lms-graph/100001/video/1"),
                                owned_page=self.page, mode="video", events=events)
        owner.emit = lambda kind, data: events.append((kind, data))
        owner._set_paused = lambda paused: setattr(owner, "paused", paused)
        return owner

    def online_fixture(self, body=SINGLE):
        self.context.route("**/*", lambda route: route.fulfill(body=HEADER+body, content_type="text/html; charset=utf-8"))
        self.page.goto(URL)

    def run_until_idle(self, session, deadline=10):
        until = time.monotonic() + deadline
        while time.monotonic() < until:
            session.tick()
            if not session.pending and not session.advance_at and not session.waiting_for and not session.submission and not session.deferred_solve and not session.verification_wait:
                return
            self.page.wait_for_timeout(40)
        self.fail("Homework session did not finish")

    def test_full_model_to_browser_fill_pipeline(self):
        self.online_fixture()
        owner = self.owner()
        session = HomeworkSession(owner)
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url, model="mock"))
                self.run_until_idle(session)
                self.assertTrue(self.page.locator('input[value="B"]').is_checked())
                self.assertTrue(any(data.get("state")=="filled" for _,data in owner.events if isinstance(data,dict)))
                self.assertEqual(len(server.requests), 1)
        finally:
            session.close()

    def test_continuous_fill_moves_to_next_question(self):
        body = SINGLE + '''<button id="next" onclick="document.querySelector('.container-problem').dataset.questionId='2';
          document.querySelector('.problem-body').textContent='第二题';document.querySelectorAll('input').forEach(el=>el.checked=false);this.disabled=true;">下一题</button>'''
        self.online_fixture(body)
        owner = self.owner()
        session = HomeworkSession(owner)
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url, model="mock"), batch=True)
                self.run_until_idle(session)
                self.assertEqual(session.completed, 2)
                self.assertEqual(len(server.requests), 2)
                self.assertTrue(any(data.get("state")=="complete" for _,data in owner.events if isinstance(data,dict)))
        finally:
            session.close()

    def test_cancellation_discards_late_response(self):
        self.online_fixture()
        owner = self.owner()
        session = HomeworkSession(owner)
        try:
            session.enter()
            page, frame, q = session.current()
            future = Future()
            future.set_running_or_notify_cancel()
            session.pending = (future, page, frame, q, page.url)
            session.cancel()
            future.set_result({"option_ids": ["B"]})
            session.tick()
            self.assertFalse(self.page.locator('input[value="B"]').is_checked())
        finally:
            session.close()

    def test_other_course_and_exam_are_not_selected(self):
        self.online_fixture()
        owner = self.owner()
        session = HomeworkSession(owner)
        try:
            self.page.goto(URL.replace('100001', '999'))
            self.assertIsNone(session.current())
            self.page.goto(URL.replace('/exercise/', '/exam/'))
            self.assertIsNone(session.current())
        finally:
            session.close()

    def test_sample_can_be_exported_when_question_is_not_recognized(self):
        self.content('<main><section class="unknown-question">题面未识别<input type="password" value="private"></section></main>')
        sample = self.page.evaluate("window.__yktHomework.sample()")
        self.assertIsNone(sample["question"])
        self.assertIn("题面未识别", sample["html"])
        self.assertNotIn("private", sample["html"])

    def test_duplicate_model_request_and_stale_response_are_rejected(self):
        self.online_fixture()
        owner = self.owner()
        session = HomeworkSession(owner)
        try:
            session.enter()
            page, frame, q = session.current()
            future = Future()
            session.pending = (future, page, frame, q, page.url)
            with self.assertRaisesRegex(ValueError, "上一条模型请求"):
                session.start(ModelSettings(base_url="http://localhost:1", model="mock"))
            self.page.locator('.problem-body').evaluate("el=>el.textContent='切换后的题目'")
            future.set_result({"option_ids": ["B"]})
            session.tick()
            self.assertFalse(self.page.locator('input[value="B"]').is_checked())
        finally:
            session.close()

    def test_continuous_mode_does_not_cross_assignment_boundary(self):
        self.online_fixture()
        owner = self.owner()
        session = HomeworkSession(owner)
        try:
            session.enter()
            session.assignment = ("school.yuketang.cn", "/ai-workspace/lms-graph/100001/exercise/200002")
            session.batch = True
            session.waiting_for = session.current()[2]["fingerprint"]
            session.wait_started = time.monotonic()
            self.page.goto(URL.replace("200002", "200003"))
            session.tick()
            self.assertFalse(session.batch)
            self.assertIsNone(session.waiting_for)
            self.assertIsNone(session.pending)
        finally:
            session.close()

    def test_visible_question_takes_precedence_over_stale_component_text(self):
        self.page.locator('.container-problem').evaluate("""el=>{
          el.__vue__={problem:{problem_id:123,content:{ProblemType:1,Body:'另一个缓存题目',Options:[]}}};
        }""")
        self.assertEqual(self.read()["stem"], "《史记》的作者是谁？")

    def test_encrypted_font_sample_is_resolved_before_model_and_fill(self):
        path = Path(__file__).resolve().parents[1] / '.runtime/homework-samples/20261031-001856.json'
        if not path.exists():
            self.skipTest('Local user sample is not present')
        frame = json.loads(path.read_text(encoding='utf-8'))['frames'][1]
        self.content(frame['html'])
        self.page.locator('input[type=radio]').evaluate_all("""els=>els.forEach((el,i)=>{
          el.value=i?'false':'true'; el.name='judgment';
        })""")
        q = self.read()
        self.assertTrue(q['needs_ocr'])
        expected = '中国古典文学强调意境营造的抒情小诗，与西方文学的叙事传统有别。'
        calls = []
        reader = RenderedTextReader(recognize=lambda image: calls.append(image.exists()) or expected)
        resolved = reader.resolve(self.page.main_frame, q)
        self.assertEqual(resolved['stem'], expected)
        self.assertFalse(resolved['needs_ocr'])
        self.assertEqual(resolved['fingerprint'], q['fingerprint'])
        self.assertTrue(self.fill({'option_ids':['false']},question=resolved))
        again = reader.resolve(self.page.main_frame, self.read())
        self.assertTrue(again['answered'])
        self.assertEqual(len(calls), 1)

    def test_real_windows_ocr_reads_small_chinese_rendered_at_high_density(self):
        self.content('''<style>.problem-body{font:16px/1.8 "Microsoft YaHei";width:820px;color:#111;background:white}</style>
          <div class="container-problem" data-question-type="judgment"><div class="problem-body"><span class="xuetangx-com-encrypted-font">中国古典文学强调意境营造的抒情小诗，而西方文学长于叙事。</span></div>
          <label><input type="radio" name="a" value="true">正确</label><label><input type="radio" name="a" value="false">错误</label></div>''')
        resolved = RenderedTextReader().resolve(self.page.main_frame,self.read())
        self.assertIn('中国古典文学',resolved['stem'])
        self.assertIn('抒情小诗',resolved['stem'])
        self.assertIn('西方文学长于叙事',resolved['stem'])
        self.assertEqual(resolved['text_source'],'windows-ocr')

    def test_pending_request_reports_elapsed_wait(self):
        self.online_fixture()
        owner = self.owner()
        session = HomeworkSession(owner)
        try:
            session.enter()
            page,frame,q=session.current()
            future=Future()
            session.pending=(future,page,frame,q,page.url)
            session.settings=ModelSettings(timeout=90)
            session.pending_started=time.monotonic()-7
            session.last_progress=0
            session.tick()
            messages=[data.get('message','') for _,data in owner.events if isinstance(data,dict)]
            self.assertTrue(any('已等待 7 秒' in message for message in messages))
        finally:
            session.close()

    def test_real_windows_ocr_captures_nested_frame_after_scroll(self):
        self.page.set_content('<div style="height:1000px">页面顶部</div><iframe style="width:950px;height:300px"></iframe>')
        child=self.page.frames[1]
        child.set_content('''<meta charset="utf-8"><style>.problem-body{font:16px/1.8 "Microsoft YaHei";width:820px;color:#111;background:white}</style>
          <div class="container-problem" data-question-type="judgment"><div class="problem-body"><span class="xuetangx-com-encrypted-font">中国文学长于抒情，而西方文学长于叙事。</span></div>
          <label><input type="radio" name="a" value="true">正确</label><label><input type="radio" name="a" value="false">错误</label></div>''')
        child.evaluate(HOMEWORK_SCRIPT)
        question=child.evaluate('window.__yktHomework.read()')
        result=RenderedTextReader().resolve(child,question)
        self.assertIn('中国文学长于抒情',result['stem'])
        self.assertIn('西方文学长于叙事',result['stem'])

    def test_continuous_fill_declines_unsent_prompt_and_reads_all_three_questions(self):
        self.online_fixture(GUARDED)
        owner=self.owner()
        session=HomeworkSession(owner)
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),batch=True)
                self.run_until_idle(session)
                self.assertEqual(session.completed,3)
                self.assertEqual(len(server.requests),3)
                self.assertEqual(self.page.evaluate('window.nextClicks'),2)
                self.assertEqual(self.page.evaluate('window.declineClicks'),2)
                self.assertEqual(self.page.evaluate('window.submits'),0)
                self.assertEqual(self.page.evaluate('window.unrelatedClicks'),0)
                self.assertEqual(self.page.evaluate('window.drafts'),{'1':['B'],'2':['B']})
                prompts=[json.loads(body['messages'][1]['content'])['stem'] for _,body,_ in server.requests]
                self.assertEqual(prompts,['《史记》的作者是谁？','第2题','第3题'])
        finally:
            session.close()

    def test_can_resume_with_the_existing_unsent_prompt_open(self):
        self.online_fixture(GUARDED)
        self.page.locator('input[value="B"]').check()
        self.page.locator('#guard').evaluate("el=>el.style.display='block'")
        owner=self.owner()
        session=HomeworkSession(owner)
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),batch=True)
                self.run_until_idle(session)
                self.assertEqual(session.completed,2)
                self.assertEqual(len(server.requests),2)
                self.assertEqual(self.page.evaluate('window.submits'),0)
                self.assertEqual(self.page.evaluate('window.drafts[1]'),['B'])
        finally:
            session.close()

    def test_unknown_modal_blocks_filling_and_navigation(self):
        self.content(SINGLE+'<div class="el-dialog" role="dialog">其他确认窗口<button type="button">提交</button></div>')
        q=self.read()
        result=self.page.evaluate('fp=>window.__yktHomework.navigateNext(fp)',q['fingerprint'])
        self.assertEqual(result['state'],'blocked-dialog')
        with self.assertRaisesRegex(Exception,'待处理弹窗'):
            self.fill({'option_ids':['B']})
        self.assertFalse(self.page.locator('input[value="B"]').is_checked())

    def test_next_is_never_a_form_submit_button(self):
        self.content(SINGLE+'''<form onsubmit="window.submits=(window.submits||0)+1;return false">
        <button type="submit">下一题</button></form>''')
        result=self.page.evaluate('fp=>window.__yktHomework.navigateNext(fp)',self.read()['fingerprint'])
        self.assertEqual(result['state'],'missing-next')
        self.assertEqual(self.page.evaluate('window.submits||0'),0)

    def test_unsent_prompt_is_declined_only_once_when_it_does_not_close(self):
        self.online_fixture(GUARDED)
        self.page.evaluate("window.moveQuestion=()=>{};document.querySelector('#guard').style.display='block'")
        owner=self.owner()
        session=HomeworkSession(owner)
        try:
            session.start(ModelSettings(base_url='http://localhost:1',model='mock'),batch=True)
            for _ in range(3):
                session.tick()
            self.assertEqual(self.page.evaluate('window.declineClicks'),1)
            self.assertEqual(self.page.evaluate('window.submits'),0)
            session.wait_started=time.monotonic()-21
            session.tick()
            self.assertFalse(session.batch)
            self.assertIsNone(session.waiting_for)
        finally:
            session.close()

    def test_missing_next_is_not_reported_as_finished_or_submitted(self):
        self.online_fixture(SINGLE+'<button type="button" onclick="window.submits=(window.submits||0)+1">提交</button>')
        owner=self.owner()
        session=HomeworkSession(owner)
        try:
            session.enter()
            _,frame,q=session.current()
            session.batch=True
            session._advance(frame,q['fingerprint'])
            self.assertFalse(session.batch)
            self.assertEqual(self.page.evaluate('window.submits||0'),0)
            self.assertFalse(any(data.get('state')=='complete' for _,data in owner.events if isinstance(data,dict)))
        finally:
            session.close()

    def test_auto_submit_waits_for_acknowledgment_before_each_next(self):
        self.online_fixture(SUBMITTABLE)
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),batch=True,auto_submit=True)
                self.run_until_idle(session)
                self.assertEqual(session.completed,3)
                self.assertEqual(session.submitted_count,3)
                self.assertEqual(self.page.evaluate('window.submitClicks'),3)
                self.assertEqual(self.page.evaluate('window.nextClicks'),2)
                self.assertEqual(self.page.evaluate('window.nextWithoutAck'),0)
                self.assertEqual(self.page.evaluate('window.submittedAnswers'),{'1':['B'],'2':['B'],'3':['B']})
        finally:
            session.close()

    def test_verification_requires_manual_completion_before_model_or_submit(self):
        self.online_fixture(SUBMITTABLE+SECURITY)
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),auto_submit=True)
                for _ in range(3):
                    session.tick()
                session.resume_after_verification()
                self.assertEqual(len(server.requests),0)
                self.assertEqual(self.page.evaluate('window.submitClicks'),0)
                self.assertEqual(self.page.evaluate('window.challengeTouches||0'),0)
                self.assertIsNotNone(session.verification_wait)
                # Simulate the user completing their own local test challenge.
                self.page.locator('#security').evaluate('el=>el.remove()')
                session.tick()
                self.assertEqual(len(server.requests),0)
                session.verification_wait['clear_since']-=2
                self.run_until_idle(session)
                self.assertEqual(len(server.requests),1)
                self.assertEqual(session.submitted_count,1)
                self.assertEqual(self.page.evaluate('window.submitClicks'),1)
        finally:
            session.close()

    def test_verification_restores_and_activates_homework_only_once(self):
        self.online_fixture(SINGLE + SECURITY)
        owner = self.owner()
        owner.window_state = lambda *_: None
        session = HomeworkSession(owner)
        try:
            with patch.object(owner, 'window_state') as restore, patch.object(self.page, 'bring_to_front') as activate:
                session.enter()
                for _ in range(4):
                    session.tick()
                restore.assert_called_once_with(self.page, 'normal')
                activate.assert_called_once_with()
                self.assertIn(('window', 'restore'), owner.events)
                self.assertEqual(self.page.evaluate('window.challengeTouches||0'), 0)
        finally:
            session.close()

    def test_verification_retries_failed_restore_without_repeated_activation(self):
        self.online_fixture(SINGLE + SECURITY)
        owner = self.owner()
        owner.window_state = lambda *_: None
        session = HomeworkSession(owner)
        try:
            with patch.object(owner, 'window_state', side_effect=[RuntimeError('temporary'), None]) as restore, patch.object(self.page, 'bring_to_front') as activate:
                session.enter()
                session.tick()
                self.assertEqual(restore.call_count, 1)
                activate.assert_called_once_with()
                session.verification_wait['foreground_retry_at'] = 0
                session.tick()
                self.assertEqual(restore.call_count, 2)
                activate.assert_called_once_with()
                self.assertTrue(session.verification_wait['window_restored'])
        finally:
            session.close()

    def test_verification_limits_failed_restore_retries_and_keeps_task_paused(self):
        self.online_fixture(SINGLE + SECURITY)
        owner = self.owner()
        owner.window_state = lambda *_: None
        session = HomeworkSession(owner)
        try:
            with patch.object(owner, 'window_state', side_effect=RuntimeError('unavailable')) as restore, patch.object(self.page, 'bring_to_front') as activate:
                session.enter()
                for _ in range(6):
                    if session.verification_wait:
                        session.verification_wait['foreground_retry_at'] = 0
                    session.tick()
                self.assertEqual(restore.call_count, 3)
                activate.assert_called_once_with()
                self.assertIsNotNone(session.verification_wait)
                messages = [data['message'] for kind, data in owner.events if kind == 'homework']
                self.assertTrue(any('自动切回浏览器未成功' in message for message in messages))
        finally:
            session.close()

    def test_verification_after_next_resumes_at_the_next_question(self):
        self.online_fixture(GUARDED)
        self.page.evaluate('''security => {
          const move=window.moveQuestion;
          window.moveQuestion=()=>{move();if(document.querySelector('.container-problem').dataset.questionId==='2')document.body.insertAdjacentHTML('beforeend',security);};
        }''',SECURITY)
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),batch=True)
                until=time.monotonic()+7
                while time.monotonic()<until and not session.verification_wait:
                    session.tick();self.page.wait_for_timeout(40)
                self.assertIsNotNone(session.verification_wait)
                self.assertEqual(len(server.requests),1)
                self.assertEqual(self.page.evaluate('window.challengeTouches||0'),0)
                self.page.locator('#security').evaluate('el=>el.remove()')
                session.resume_after_verification()
                self.run_until_idle(session)
                self.assertEqual(len(server.requests),3)
                self.assertEqual(self.page.evaluate('window.submits'),0)
        finally:
            session.close()

    def test_verification_during_submission_does_not_repeat_submit(self):
        self.online_fixture(SUBMITTABLE)
        self.page.evaluate('''security=>{window.submitAction=()=>{window.submitClicks++;document.body.insertAdjacentHTML('beforeend',security);};}''',SECURITY)
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),auto_submit=True)
                until=time.monotonic()+5
                while time.monotonic()<until and not session.verification_wait:
                    session.tick();self.page.wait_for_timeout(40)
                self.assertIsNotNone(session.verification_wait)
                self.assertEqual(self.page.evaluate('window.submitClicks'),1)
                session.verification_wait['since']-=120
                self.page.evaluate("window.confirmSubmission();document.querySelector('#security').remove()")
                session.resume_after_verification()
                self.run_until_idle(session)
                self.assertEqual(self.page.evaluate('window.submitClicks'),1)
                self.assertEqual(session.submitted_count,1)
                self.assertEqual(len(server.requests),1)
        finally:
            session.close()

    def test_hidden_verification_dom_does_not_block_resumption(self):
        self.online_fixture(SINGLE + '<div style="opacity:0">' + SECURITY + '</div>')
        session = HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url, model='mock'))
                self.run_until_idle(session)
                self.assertEqual(session.completed, 1)
                self.assertIsNone(session.verification_wait)
        finally:
            session.close()

    def test_visible_srcdoc_verification_waits_until_frame_is_hidden(self):
        self.online_fixture()
        self.page.evaluate('html => {const frame=document.createElement("iframe"); frame.id="security-frame"; frame.srcdoc=html; document.body.append(frame);}', SECURITY)
        self.page.frame_locator('#security-frame').locator('#security').wait_for()
        session = HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url, model='mock'))
                self.assertIsNotNone(session.verification_wait)
                self.assertEqual(len(server.requests), 0)
                self.page.locator('#security-frame').evaluate("el=>el.style.display='none'")
                self.run_until_idle(session)
                self.assertEqual(session.completed, 1)
                self.assertEqual(len(server.requests), 1)
        finally:
            session.close()

    def test_verification_racing_with_fill_preserves_completed_model_result(self):
        self.online_fixture()
        session = HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url, model='mock'))
                self.page.evaluate('''html => {
                  const fill=window.__yktHomework.fill.bind(window.__yktHomework);
                  window.__yktHomework.fill=payload=>{
                    window.__yktHomework.fill=fill;
                    document.body.insertAdjacentHTML('beforeend',html);
                    throw Error('verification appeared before filling');
                  };
                }''', SECURITY)
                until = time.monotonic() + 5
                while time.monotonic() < until and not session.verification_wait:
                    session.tick(); self.page.wait_for_timeout(40)
                self.assertIsNotNone(session.verification_wait)
                self.assertIsNotNone(session.pending)
                self.assertEqual(session.completed, 0)
                self.page.locator('#security').evaluate('el=>el.remove()')
                self.run_until_idle(session)
                self.assertEqual(session.completed, 1)
                self.assertEqual(len(server.requests), 1)
        finally:
            session.close()

    def test_verification_after_option_click_keeps_filled_answer(self):
        self.online_fixture()
        self.page.evaluate('''html => {
          window.answerClicks=0;
          document.querySelector('input[value="B"]').addEventListener('click',()=>{
            window.answerClicks++;
            document.body.insertAdjacentHTML('beforeend',html);
          },{once:true});
        }''', SECURITY)
        session = HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url, model='mock'))
                until = time.monotonic() + 5
                while time.monotonic() < until and not session.verification_wait:
                    session.tick(); self.page.wait_for_timeout(40)
                self.assertIsNotNone(session.verification_wait)
                self.assertIsNotNone(session.pending)
                self.page.locator('#security').evaluate('el=>el.remove()')
                self.run_until_idle(session)
                self.assertEqual(session.completed, 1)
                self.assertEqual(self.page.evaluate('window.answerClicks'), 1)
                self.assertEqual(len(server.requests), 1)
        finally:
            session.close()

    def test_verification_between_fill_and_submission_retains_model_result(self):
        self.online_fixture(SUBMITTABLE)
        session = HomeworkSession(self.owner())
        queue_submission = session._queue_submission

        def interrupted_queue(*args):
            session._queue_submission = queue_submission
            self.page.evaluate('html=>document.body.insertAdjacentHTML("beforeend",html)', SECURITY)
            raise ValueError('question temporarily unavailable during verification')

        session._queue_submission = interrupted_queue
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url, model='mock'), auto_submit=True)
                until = time.monotonic() + 5
                while time.monotonic() < until and not session.verification_wait:
                    session.tick(); self.page.wait_for_timeout(40)
                self.assertIsNotNone(session.verification_wait)
                self.assertIsNotNone(session.pending)
                self.assertEqual(session.completed, 0)
                self.page.locator('#security').evaluate('el=>el.remove()')
                self.run_until_idle(session)
                self.assertEqual(session.completed, 1)
                self.assertEqual(session.submitted_count, 1)
                self.assertEqual(len(server.requests), 1)
                self.assertEqual(self.page.evaluate('window.submitClicks'), 1)
        finally:
            session.close()

    def test_verification_racing_with_next_keeps_advance_stage(self):
        self.online_fixture(GUARDED)
        session = HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url, model='mock'), batch=True)
                self.page.evaluate('''html => {
                  const next=window.__yktHomework.navigateNext.bind(window.__yktHomework);
                  window.__yktHomework.navigateNext=fp=>{
                    window.__yktHomework.navigateNext=next;
                    document.body.insertAdjacentHTML('beforeend',html);
                    return {state:'security-verification'};
                  };
                }''', SECURITY)
                until = time.monotonic() + 7
                while time.monotonic() < until and not session.verification_wait:
                    session.tick(); self.page.wait_for_timeout(40)
                self.assertIsNotNone(session.verification_wait)
                self.assertTrue(session.batch)
                self.assertIsNotNone(session.advance_at)
                self.page.locator('#security').evaluate('el=>el.remove()')
                self.run_until_idle(session)
                self.assertEqual(session.completed, 3)
                self.assertEqual(len(server.requests), 3)
        finally:
            session.close()

    def test_verification_waits_for_question_to_return_before_resuming(self):
        self.online_fixture(SINGLE + SECURITY)
        session = HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url, model='mock'))
                self.page.evaluate("() => {window.savedQuestion=document.querySelector('.container-problem'); window.savedQuestion.remove(); document.querySelector('#security').remove();}")
                session.resume_after_verification()
                session.tick()
                self.assertIsNotNone(session.verification_wait)
                self.assertTrue(session.deferred_solve)
                self.assertEqual(len(server.requests), 0)
                self.page.evaluate('document.body.append(window.savedQuestion)')
                self.run_until_idle(session)
                self.assertEqual(session.completed, 1)
        finally:
            session.close()

    def test_verification_racing_with_question_inspection_preserves_solve_stage(self):
        self.online_fixture()
        self.page.evaluate(HOMEWORK_SCRIPT)
        self.page.evaluate('''html => {
          const state=window.__yktHomework.navigationState.bind(window.__yktHomework);
          window.__yktHomework.navigationState=()=>{
            window.__yktHomework.navigationState=state;
            document.body.insertAdjacentHTML('beforeend',html);
            return {state:'security-verification'};
          };
        }''', SECURITY)
        session = HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url, model='mock'))
                self.assertTrue(session.deferred_solve)
                self.assertIsNotNone(session.verification_wait)
                self.assertEqual(len(server.requests), 0)
                self.page.locator('#security').evaluate('el=>el.remove()')
                self.run_until_idle(session)
                self.assertEqual(session.completed, 1)
        finally:
            session.close()

    def test_verification_query_refresh_keeps_same_question_model_result(self):
        self.online_fixture()
        session = HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url, model='mock'))
                self.page.evaluate('html=>document.body.insertAdjacentHTML("beforeend",html)', SECURITY)
                session.tick()
                self.assertIsNotNone(session.pending)
                self.page.evaluate("() => {document.querySelector('#security').remove();history.replaceState({},'',location.pathname+'?date=123&newWeb=1');}")
                self.run_until_idle(session)
                self.assertEqual(session.completed, 1)
                self.assertEqual(len(server.requests), 1)
        finally:
            session.close()

    def test_stop_during_verification_does_not_restart_when_modal_disappears(self):
        self.online_fixture(SINGLE + SECURITY)
        session = HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url, model='mock'), batch=True)
                session.cancel()
                self.page.locator('#security').evaluate('el=>el.remove()')
                self.run_until_idle(session)
                self.assertEqual(session.completed, 0)
                self.assertEqual(len(server.requests), 0)
                self.assertFalse(session.deferred_solve)
        finally:
            session.close()

    def test_unconfirmed_submission_stops_without_retry(self):
        self.network_submit_fixture()
        self.context.route('**/mooc-api/v1/lms/exercise/problem_apply/',lambda route:route.abort())
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                settings=ModelSettings(base_url=server.url,model='mock')
                session.start(settings,auto_submit=True)
                until=time.monotonic()+5
                while time.monotonic()<until and not (session.submission and session.submission['clicked']):
                    session.tick();self.page.wait_for_timeout(40)
                self.assertIsNotNone(session.submission)
                session.submission['deadline']=time.monotonic()-1
                session.tick()
                session.start(settings,auto_submit=True)
                self.assertEqual(self.page.evaluate('window.submitClicks'),1)
                self.assertEqual(session.submitted_count,0)
                self.assertIsNone(session.submission)
                self.assertEqual(len(server.requests),1)
        finally:
            session.close()

    def test_existing_draft_can_be_submitted_without_a_second_model_call(self):
        self.online_fixture(SUBMITTABLE)
        self.page.locator('input[value="B"]').check()
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),auto_submit=True)
                self.run_until_idle(session)
                self.assertEqual(len(server.requests),0)
                self.assertEqual(self.page.evaluate('window.submitClicks'),1)
                self.assertEqual(session.submitted_count,1)
        finally:
            session.close()

    def test_auto_submit_confirms_only_current_question_in_batch_prompt(self):
        self.online_fixture(SUBMITTABLE)
        self.page.evaluate('''() => {
          window.currentConfirmClicks=0;window.allConfirmClicks=0;
          window.submitAction=()=>{
            window.submitClicks++;
            document.body.insertAdjacentHTML('beforeend',`<div class="el-dialog" id="batch" role="dialog">
              3道已作答习题未提交，需要一起提交吗？
              <button type="button" onclick="window.currentConfirmClicks++;window.confirmSubmission();document.querySelector('#batch').remove()">仅提交本题</button>
              <button type="button" onclick="window.allConfirmClicks++">帮我提交</button></div>`);
          };
        }''')
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'),auto_submit=True)
                self.run_until_idle(session)
                self.assertEqual(self.page.evaluate('window.currentConfirmClicks'),1)
                self.assertEqual(self.page.evaluate('window.allConfirmClicks'),0)
                self.assertEqual(session.submitted_count,1)
        finally:
            session.close()

    def test_auto_submit_remains_off_by_default(self):
        self.online_fixture(SUBMITTABLE)
        session=HomeworkSession(self.owner())
        try:
            with MockServer() as server:
                session.start(ModelSettings(base_url=server.url,model='mock'))
                self.run_until_idle(session)
                self.assertEqual(session.completed,1)
                self.assertEqual(self.page.evaluate('window.submitClicks'),0)
                self.assertEqual(session.submitted_count,0)
        finally:
            session.close()
