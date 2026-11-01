from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from tests.test_llm import MockServer
from yuketang_helper.learning import LearningService, LearningStore
from yuketang_helper.llm import ModelClient, ModelError, ModelSettings


ASSIGNMENT=('school.yuketang.cn','/ai-workspace/lms-graph/100001/exercise/200002')
QUESTION={'id':'q1','type':'multiple','stem':'选择相关概念','options':[{'id':'A','text':'甲'},{'id':'B','text':'乙'}],
          'answer_source':'model','cookie':'private-cookie','embedded_answer':{'secret':'private-key'}}
ANSWER={'option_ids':['A','B'],'explanation':'模型说明','private':'must-not-persist'}


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'learning.sqlite3'
        self.store=LearningStore(self.path)
        self.events=[]
        self.service=LearningService(self.store,self.events.append,key_loader=lambda endpoint:'')

    def tearDown(self):
        self.service.executor.shutdown(wait=True,cancel_futures=True)
        self.temp.cleanup()

    def record(self, question=None, assignment=ASSIGNMENT, answer=ANSWER, status='filled'):
        return self.store.record('我的课程',assignment,question or QUESTION,answer,model='mock',status=status)

    def item(self, ids=None, summary='关键知识', techniques=None):
        return {'topic':'文学概念','summary':summary,'techniques':techniques or [],
                'record_ids':ids or [self.store.records()[0]['id']]}

    def test_records_survive_restart_and_do_not_persist_private_fields(self):
        key=self.record()
        rows=LearningStore(self.path).records()
        self.assertEqual(rows[0]['id'],key)
        self.assertEqual(rows[0]['answer']['option_ids'],['A','B'])
        encoded=json.dumps(rows,ensure_ascii=False)
        for secret in ('private-cookie','private-key','must-not-persist','embedded_answer'):
            self.assertNotIn(secret,encoded)
        self.assertEqual(rows[0]['correctness'],'unknown')

    def test_updates_preserve_versions_and_submission_does_not_mean_correct(self):
        key=self.record()
        self.record(status='submitted')
        self.record(status='submitted')
        self.assertEqual(len(self.store.records()),1)
        self.assertEqual(len(self.store.history(key)),2)
        self.assertEqual(self.store.records()[0]['status'],'submitted')
        self.assertEqual(self.store.records()[0]['correctness'],'unknown')
        self.record(answer={'option_ids':['B']})
        self.assertEqual(len(self.store.history(key)),3)
        self.assertEqual(self.store.records()[0]['status'],'filled')

    def test_same_question_ids_in_other_courses_are_isolated(self):
        self.record()
        self.record(assignment=(ASSIGNMENT[0],ASSIGNMENT[1].replace('100001','999')))
        self.assertEqual(len(self.store.records()),2)
        self.assertEqual(len({r['course_key'] for r in self.store.records()}),2)

    def test_explicit_website_feedback_is_separate_from_submission_status(self):
        self.record(question={**QUESTION,'correctness':'correct'})
        self.assertEqual(self.store.records()[0]['correctness'],'unknown')
        self.record(question={**QUESTION,'correctness':'incorrect'},status='submitted')
        self.assertEqual(self.store.records()[0]['correctness'],'incorrect')
        self.record(status='submitted')
        self.assertEqual(self.store.records()[0]['correctness'],'incorrect')
        self.record(answer={'option_ids':['B']})
        self.assertEqual(self.store.records()[0]['correctness'],'unknown')

    def test_revisiting_existing_answer_keeps_model_explanation_and_history(self):
        key=self.record(status='submitted')
        self.record(question={**QUESTION,'answer_source':'existing'},answer={'option_ids':['A','B']},status='submitted')
        row=self.store.records()[0]
        self.assertEqual(row['answer_source'],'model')
        self.assertEqual(row['answer']['explanation'],'模型说明')
        self.assertEqual(len(self.store.history(key)),1)
        self.record(question={**QUESTION,'answer_source':'existing'},answer=None,status='submitted')
        self.assertEqual(self.store.records()[0]['answer']['option_ids'],['A','B'])

    def test_disabled_review_never_loads_key_or_calls_model(self):
        self.record()
        with patch.object(self.service,'key_loader') as key, patch.object(ModelClient,'review') as api:
            self.assertEqual(self.service.request_review(''.join(ASSIGNMENT),ModelSettings()),[])
            key.assert_not_called();api.assert_not_called()

    def test_summary_only_calls_configured_endpoint_and_restricts_payload(self):
        self.record()
        with MockServer() as server:
            server.content=json.dumps({'experiences':[self.item(techniques=['不应保留的技巧'])]},ensure_ascii=False)
            settings=ModelSettings(base_url=server.url,model='review-model',review_summary=True)
            jobs=self.service.request_review(''.join(ASSIGNMENT),settings)
            for job in jobs:job.result(timeout=5)
            self.assertEqual(len(server.requests),1)
            payload=server.requests[0][1]
            self.assertEqual(payload['model'],'review-model')
            self.assertIn('techniques必须为空数组',payload['messages'][0]['content'])
            for secret in ('private-cookie','private-key','must-not-persist','school.yuketang.cn','scope'):
                self.assertNotIn(secret,json.dumps(payload,ensure_ascii=False))
        items=self.store.experiences()
        self.assertEqual(items[0]['summary'],'关键知识')
        self.assertEqual(items[0]['techniques'],[])
        self.assertEqual(self.service.request_review(''.join(ASSIGNMENT),settings),[])

    def test_techniques_only_and_invalid_source_rejected(self):
        self.record()
        with MockServer() as server:
            settings=ModelSettings(base_url=server.url,model='mock',review_techniques=True)
            client=ModelClient(settings,'')
            server.content=json.dumps({'experiences':[self.item(techniques=['先辨析概念'])]},ensure_ascii=False)
            result=client.review(self.store.records())
            self.assertEqual(result[0]['summary'],'')
            self.assertEqual(result[0]['techniques'],['先辨析概念'])
            server.content=json.dumps({'experiences':[self.item(ids=['invented-id'])]})
            with self.assertRaises(ModelError):client.review(self.store.records())

    def test_no_applicable_techniques_keeps_records_without_invented_material(self):
        self.record()
        with MockServer() as server:
            server.content='{"experiences":[]}'
            settings=ModelSettings(base_url=server.url,model='mock',review_techniques=True)
            for job in self.service.request_review(''.join(ASSIGNMENT),settings):job.result(timeout=5)
            self.assertEqual(self.store.experiences(),[])
            self.assertEqual(len(self.store.records()),1)
            self.assertEqual(self.store.reviews()[0]['state'],'complete')
            self.assertIn('没有适用',self.store.reviews()[0]['message'])

    def test_error_is_recorded_and_manual_retry_can_succeed(self):
        self.record()
        settings=ModelSettings(base_url='http://localhost:1234',model='mock',review_summary=True)
        with patch.object(ModelClient,'review',side_effect=ModelError('模型调用超时')):
            for job in self.service.request_review(''.join(ASSIGNMENT),settings):job.result(timeout=5)
        self.assertEqual(len(self.store.records()),1)
        self.assertEqual(self.store.reviews()[0]['state'],'error')
        with patch.object(ModelClient,'review',return_value=[self.item()]):
            for job in self.service.request_review(''.join(ASSIGNMENT),settings,force=True):job.result(timeout=5)
        self.assertEqual(self.store.reviews()[0]['state'],'complete')
        self.assertEqual(len(self.store.experiences()),1)

    def test_review_runs_in_background_and_duplicate_pending_work_is_coalesced(self):
        self.record()
        entered=threading.Event();release=threading.Event()
        def slow(_records):
            entered.set();release.wait(5);return [self.item()]
        settings=ModelSettings(base_url='http://localhost:1234',model='mock',review_summary=True)
        with patch.object(ModelClient,'review',side_effect=slow):
            try:
                jobs=self.service.request_review(''.join(ASSIGNMENT),settings)
                self.assertTrue(entered.wait(2))
                self.assertFalse(jobs[0].done())
                self.assertIs(self.service.request_review(''.join(ASSIGNMENT),settings)[0],jobs[0])
                self.record(question={**QUESTION,'id':'q2'})
                self.assertEqual(len(self.store.records()),2)
            finally:release.set()
            jobs[0].result(timeout=5)

    def test_same_topic_merges_sources_and_preserves_personal_notes(self):
        first=self.record()
        rows=self.store.records()
        self.store.save_experiences(rows,[self.item([first])],'mock','s1')
        key=self.store.experiences()[0]['id']
        self.store.notes(key,'我的修订')
        second=self.record(question={**QUESTION,'id':'q2'})
        self.store.save_experiences(self.store.records(),[self.item([second],summary='新的总结')],'another-model','s2')
        item=self.store.experiences()[0]
        self.assertEqual(len(self.store.experiences()),1)
        self.assertEqual(item['record_ids'],sorted([first,second]))
        self.assertEqual(item['notes'],'我的修订')
        self.store.delete_experience(key)
        self.assertEqual(self.store.experiences(),[])
        self.assertEqual(len(self.store.records()),2)

    def test_large_assignments_are_batched_and_restart_does_not_repeat_completed_reviews(self):
        for index in range(21):self.record(question={**QUESTION,'id':str(index)})
        settings=ModelSettings(base_url='http://localhost:1234',model='mock',review_summary=True)
        def review(rows):return [self.item(ids=[r['id'] for r in rows])]
        with patch.object(ModelClient,'review',side_effect=review) as api:
            for job in self.service.request_review(''.join(ASSIGNMENT),settings):job.result(timeout=5)
            self.assertEqual(api.call_count,2)
            self.assertEqual(len(self.store.experiences()[0]['record_ids']),21)
            restarted=LearningService(LearningStore(self.path),key_loader=lambda endpoint:'')
            try:
                self.assertEqual(restarted.request_review(''.join(ASSIGNMENT),settings),[])
                self.assertEqual(api.call_count,2)
            finally:restarted.close()

    def test_review_settings_default_off_and_persist_independently(self):
        self.assertFalse(ModelSettings().review_summary)
        self.assertFalse(ModelSettings().review_techniques)
        path=Path(self.temp.name)/'settings.json'
        ModelSettings(review_summary=True).save(path)
        self.assertTrue(ModelSettings.load(path).review_summary)
        self.assertFalse(ModelSettings.load(path).review_techniques)
        with self.assertRaises(ValueError):ModelSettings(review_summary='true').validate()
