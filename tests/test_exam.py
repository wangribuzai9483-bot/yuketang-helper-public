from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import sync_playwright

from yuketang_helper.config import Config
from yuketang_helper.exam import EXAM_SCRIPT, ExamSession, exam_frame_url
from yuketang_helper.learning import LearningService, LearningStore
from yuketang_helper.llm import ModelSettings, is_exam_portal_url, is_homework_url

PORTAL='https://school.yuketang.cn/ai-workspace/lms-graph/100001/quiz/200005?node_id=300001'
PAPER='https://examination.xuetangx.com/exam/123'
INTRO='''<meta charset="utf-8"><div class="exam-container"><h1>期末考试</h1><p>100分，33题</p>
<button onclick="window.starts++">开始答题</button></div><script>window.starts=0</script>'''
PAPER_HTML='''<meta charset="utf-8"><div class="exam exam-answer"><div class="exam-main">
<button onclick="window.submits++">交卷</button><div id="questions"></div></div></div><script>
window.submits=0;window.challenges=0;window.drafts={};window.props={};
window.items=[
{problem_id:'judge',ProblemType:6,Body:'这是判断题',Options:[{key:'true',value:'正确'},{key:'false',value:'错误'}],result:[]},
{problem_id:'single',ProblemType:1,Body:'这是单选题',Options:[{key:'A',value:'甲'},{key:'B',value:'乙'}],result:[],Answer:['B']},
{problem_id:'multi',ProblemType:2,Body:'这是多选题',Options:[{key:'A',value:'甲'},{key:'B',value:'乙'},{key:'C',value:'丙'}],result:[]},
{problem_id:'short',ProblemType:5,Body:'这是简答题',Options:[],result:''},
{problem_id:'essay',ProblemType:5,TypeRenameText:'论述题',Body:'这是论述题',Options:[],result:''}
];
items.forEach((item,index)=>{
  const root=document.createElement('div');root.className='exercise-item';root.__vue__={item};
  root.innerHTML=`<div class="item-type">${index+1}.题目</div><div class="item-body"><h4 class="clearfix exam-font">${item.Body}</h4><div class="choices"></div></div>`;
  window.props[item.problem_id]=[];
  const draw=()=>{
    const box=root.querySelector('.choices');
    if(item.ProblemType===5){
      box.innerHTML='<textarea></textarea>';box.querySelector('textarea').addEventListener('input',e=>{item.result=e.target.value;drafts[item.problem_id]=item.result});return;
    }
    box.innerHTML=item.Options.map(o=>`<label><input name="${item.problem_id}" type="${item.ProblemType===2?'checkbox':'radio'}" value="${o.key}" ${props[item.problem_id].includes(o.key)?'checked':''}><span class="${item.ProblemType===2?'checkboxText':'radioText'}">${o.value}</span></label>`).join('');
    box.querySelectorAll('input').forEach(input=>input.addEventListener('change',()=>{
      const key=input.dataset.optionKey||input.value;
      let next=item.ProblemType===2?props[item.problem_id].filter(id=>id!==key):[];
      if(input.checked)next.push(key);item.result=next;drafts[item.problem_id]=[...next];
      setTimeout(()=>{props[item.problem_id]=[...item.result];draw()},0);
    }));
  };draw();document.querySelector('#questions').append(root);
});
</script>'''


class ExamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright=sync_playwright().start();cls.browser=cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.playwright.stop()

    def setUp(self):
        self.context=self.browser.new_context()
        self.context.route('**/*',lambda route:route.fulfill(body=PAPER_HTML if '/exam/123' in route.request.url else INTRO,content_type='text/html; charset=utf-8'))
        self.portal=self.context.new_page();self.portal.goto(PORTAL)
        self.events=[]
        self.owner=SimpleNamespace(context=self.context,config=Config(course_url=PORTAL),owned_page=self.portal,mode='exam',
            emit=lambda kind,data:self.events.append((kind,data)),_set_paused=lambda _:None,bypass=Mock())
        self.session=ExamSession(self.owner);self.session.open(PORTAL)

    def tearDown(self):
        self.session.close();self.context.close()

    def paper(self):
        with self.context.expect_page() as created:self.portal.evaluate('url=>window.open(url)',PAPER)
        page=created.value;page.wait_for_load_state();return page

    def run_idle(self, timeout=12):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            self.session.tick()
            if not self.session.pending and not self.session.batch and not self.session.deferred and not self.session.waiting:return
            self.portal.wait_for_timeout(25)
        self.fail('Exam did not become idle')

    def test_intro_never_starts_exam_and_homework_stays_separate(self):
        self.session.inspect()
        self.assertEqual(self.portal.evaluate('window.starts'),0)
        self.assertEqual(self.events[-1][1]['state'],'waiting')
        self.assertFalse(is_homework_url(PORTAL));self.assertTrue(is_exam_portal_url(PORTAL))
        self.assertFalse(is_exam_portal_url('https://example.test/quiz/1'))
        self.assertFalse(exam_frame_url('https://examination.xuetangx.com.evil.test/exam/123'))

    def test_model_fills_all_five_types_without_starting_or_submitting(self):
        page=self.paper()
        def solve(q):
            return {'option_ids':['true']} if q['type']=='judgment' else {'option_ids':['A','C']} if q['type']=='multiple' else {'option_ids':['A']} if q['type']=='single' else {'text':'论述内容' if q['id']=='essay' else '简答内容'}
        with patch('yuketang_helper.exam.ModelClient.solve',side_effect=solve) as model,patch('yuketang_helper.exam.KeyStore.load',return_value=''):
            self.session.start(ModelSettings(base_url='http://localhost:1234',model='mock',auto_submit=True),batch=True)
            self.run_idle()
            self.assertEqual(model.call_count,5)
            self.assertEqual(self.session.completed,5)
            drafts=page.evaluate('window.drafts')
            self.assertEqual(sorted(drafts['multi']),['A','C'])
            self.assertEqual(drafts['single'],['A'])
            self.assertEqual(drafts['judge'],['true'])
            self.assertEqual(drafts['short'],'简答内容');self.assertEqual(drafts['essay'],'论述内容')
            self.assertEqual(page.evaluate('window.submits'),0)
            self.assertEqual(self.portal.evaluate('window.starts'),0)
            self.owner.bypass.handle.assert_not_called()

    def test_pasted_answer_needs_no_api_and_existing_answer_is_kept(self):
        page=self.paper()
        with patch('yuketang_helper.exam.ModelClient') as api:
            self.session.fill({'option_ids':['A','C']},index=2)
            self.assertEqual(sorted(page.evaluate('items[2].result')),['A','C'])
            with self.assertRaises(Exception):self.session.fill({'option_ids':['B']},index=2)
            self.assertEqual(sorted(page.evaluate('items[2].result')),['A','C'])
            api.assert_not_called()

    def test_unrelated_exam_tab_is_not_adopted(self):
        unrelated=self.context.new_page();unrelated.goto(PAPER)
        self.assertIsNone(self.session.current())
        self.assertEqual(unrelated.evaluate('window.submits'),0)

    def test_legacy_native_inputs_map_display_letters_to_real_option_ids(self):
        page=self.paper()
        page.evaluate('''() => {
          const root=document.querySelectorAll('.exercise-item')[1];
          items[1].Options.forEach((o,i)=>o.key='option-'+i);
          root.querySelectorAll('input').forEach((input,i)=>{input.value='on';input.dataset.optionKey='option-'+i;});
          root.querySelectorAll('.radioText').forEach((span,i)=>span.textContent=String.fromCharCode(65+i)+'. '+span.textContent);
        }''')
        self.session.fill({'option_ids':['option-1']},index=1)
        self.assertEqual(page.evaluate('items[1].result'),['option-1'])

    def test_stop_discards_a_late_model_result(self):
        page=self.paper()
        from concurrent.futures import Future
        ready=Future();ready.set_running_or_notify_cancel()
        current=self.session.current()
        self.session.pending=(ready,current[0],current[1],current[2])
        self.session.cancel()
        ready.set_result({'option_ids':['true']});self.run_idle()
        self.assertEqual(page.evaluate('items[0].result'),[])

    def test_custom_teacher_dialog_blocks_filling_without_closing_it(self):
        page=self.paper()
        page.evaluate("document.body.insertAdjacentHTML('beforeend','<div class=message-dialog-wrapper><div class=message-dialog>教师提醒</div></div>')")
        with self.assertRaises(ValueError):self.session.fill({'option_ids':['true']})
        self.assertEqual(page.evaluate('items[0].result'),[])
        self.assertEqual(page.locator('.message-dialog').count(),1)

    def test_changed_question_rejects_pending_model_result(self):
        page=self.paper()
        from concurrent.futures import Future
        ready=Future()
        self.session.settings=ModelSettings(base_url='http://localhost:1234',model='mock')
        current=self.session.current()
        self.session.pending=(ready,current[0],current[1],current[2])
        page.locator('.exercise-item h4').first.evaluate("el=>el.textContent='题干已变化'")
        ready.set_result({'option_ids':['true']});self.run_idle()
        self.assertEqual(page.evaluate('items[0].result'),[])
        self.assertEqual(self.events[-1][1]['state'],'error')

    def test_collapsed_questions_are_not_reported_as_whole_paper_complete(self):
        page=self.paper()
        page.locator('.exercise-item').last.evaluate("el=>el.style.display='none'")
        current=self.session.current(index=3)
        self.assertEqual(current[2]['total'],4)
        self.assertEqual(current[2]['expected_total'],5)
        self.session.batch=True;self.session.index=3
        self.session._next(current[2])
        self.assertEqual(self.events[-1][1]['state'],'partial')

    def test_manual_security_verification_resumes_single_question_without_solver(self):
        page=self.paper()
        page.evaluate("document.body.insertAdjacentHTML('beforeend','<div id=security role=dialog><h3>请完成安全核验</h3></div>')")
        with patch('yuketang_helper.exam.ModelClient.solve',return_value={'option_ids':['true']}) as api,patch('yuketang_helper.exam.KeyStore.load',return_value=''):
            self.session.start(ModelSettings(base_url='http://localhost:1234',model='mock'))
            self.assertTrue(self.session.waiting);api.assert_not_called()
            page.locator('#security').evaluate('el=>el.remove()')
            self.session.clear_since=time.monotonic()-2
            self.run_idle()
            self.assertEqual(page.evaluate('items[0].result'),['true'])
            self.assertEqual(api.call_count,1)
            self.owner.bypass.handle.assert_not_called()

    def test_manual_verification_during_pasted_multiple_choice_preserves_full_answer(self):
        page=self.paper()
        page.locator('.exercise-item').nth(2).locator('input[value="A"]').evaluate('''el=>el.addEventListener('change',()=>{
          document.body.insertAdjacentHTML('beforeend','<div id=security role=dialog><h3>请完成安全核验</h3></div>');
        },{once:true})''')
        self.session.fill({'option_ids':['A','C']},index=2)
        self.assertTrue(self.session.waiting)
        page.locator('#security').evaluate('el=>el.remove()')
        self.session.clear_since=time.monotonic()-2
        self.run_idle()
        self.assertEqual(sorted(page.evaluate('items[2].result')),['A','C'])
        self.assertEqual(self.session.completed,1)
        self.assertEqual(page.evaluate('window.submits'),0)

    def test_exam_fills_are_recorded_as_drafts(self):
        self.paper()
        with tempfile.TemporaryDirectory() as folder:
            service=LearningService(LearningStore(Path(folder)/'learning.db'))
            self.owner.learning=service
            try:
                self.session.fill({'text':'手动论述答案'},index=4)
                row=service.store.records()[0]
                self.assertEqual(row['assignment_title'],'期末考试')
                self.assertEqual(row['answer_source'],'user')
                self.assertEqual(row['status'],'filled');self.assertEqual(row['correctness'],'unknown')
            finally:service.close()
