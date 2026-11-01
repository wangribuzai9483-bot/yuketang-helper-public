from types import SimpleNamespace
import unittest

from yuketang_helper.submission import SubmissionMonitor


class Context:
    def on(self,*args):pass
    def remove_listener(self,*args):pass


class Request:
    def __init__(self,page,path='problem_apply/',data=None,result=None,status=200):
        self.method='POST'
        self.url='https://school.yuketang.cn/mooc-api/v1/lms/exercise/'+path
        self.frame=SimpleNamespace(page=page)
        self.data=data or {'problem_id':'1','classroom_id':10}
        self.result=result if result is not None else {'success':True}
        self.status=status
        self.reads=0

    @property
    def post_data_json(self):
        self.reads+=1
        return self.data

    def response(self):
        return SimpleNamespace(status=self.status,json=lambda:self.result)


class SubmissionMonitorTests(unittest.TestCase):
    def setUp(self):
        self.page=object()
        self.monitor=SubmissionMonitor(Context())
        self.probe=self.monitor.begin(self.page,'1','10')

    def tearDown(self):self.monitor.close()

    def test_pending_and_accepted_are_distinct_from_no_request(self):
        self.assertEqual(self.probe.outcome,'not-sent')
        request=Request(self.page)
        self.monitor._request(request)
        self.assertEqual(self.probe.outcome,'pending')
        self.monitor._finished(request)
        self.assertEqual(self.probe.outcome,'accepted')

    def test_explicit_rejection_differs_from_network_failure(self):
        rejected=Request(self.page,result={'success':False})
        self.monitor._request(rejected);self.monitor._finished(rejected)
        self.assertEqual(self.probe.outcome,'rejected')
        failed=Request(self.page)
        self.monitor._request(failed);self.monitor._failed(failed)
        self.assertEqual(self.probe.outcome,'uncertain')

    def test_http_refusal_is_rejected_but_server_error_is_uncertain(self):
        refused=Request(self.page,status=403,result={})
        self.monitor._request(refused);self.monitor._finished(refused)
        self.assertEqual(self.probe.outcome,'rejected')
        failure=Request(self.page,status=500,result={})
        self.monitor._request(failure);self.monitor._finished(failure)
        self.assertEqual(self.probe.outcome,'uncertain')

    def test_batch_confirmation_is_specific_to_requested_question(self):
        request=Request(self.page,path='problem_batch_apply/',data={'problem_ids':['1','2'],'classroom_id':10},
                        result={'success':True,'data':{'1':[{},'0'],'2':[{},1]}})
        other=self.monitor.begin(self.page,'2','10')
        self.monitor._request(request);self.monitor._finished(request)
        self.assertEqual(self.probe.outcome,'accepted')
        self.assertEqual(other.outcome,'rejected')

    def test_official_batch_answer_dict_and_per_question_result_are_supported(self):
        request=Request(self.page,path='problem_batch_apply/',
                        data={'classroom_id':10,'answer_dict':{'1':{'answer':['B']}}},
                        result={'data':{'1':[{'my_count':1},0]}})
        self.monitor._request(request);self.monitor._finished(request)
        self.assertEqual(self.probe.outcome,'accepted')

    def test_verification_and_model_traffic_is_not_read(self):
        for url in ('https://school.yuketang.cn/captcha/verify','https://verify.example.test/verify',
                    'https://api.deepseek.com/chat/completions'):
            request=Request(self.page);request.url=url
            self.monitor._request(request)
            self.assertEqual(request.reads,0)
        self.assertEqual(self.probe.outcome,'not-sent')

    def test_unknown_assignment_write_blocks_automatic_replay(self):
        request=Request(self.page,path='new_submission_version/')
        self.monitor._request(request)
        self.assertEqual(self.probe.outcome,'uncertain')

    def test_other_pages_questions_and_classrooms_are_ignored(self):
        for request in (Request(object()),
                        Request(self.page,data={'problem_id':'1','classroom_id':99})):
            self.monitor._request(request)
        self.assertEqual(self.probe.outcome,'not-sent')

    def test_mismatched_question_write_is_not_mistaken_for_proof_of_no_request(self):
        self.monitor._request(Request(self.page,data={'problem_id':'2','classroom_id':10}))
        self.assertEqual(self.probe.outcome,'uncertain')

    def test_form_encoded_batch_answer_dict_is_supported(self):
        request=Request(self.page,path='problem_batch_apply/',data={'classroom_id':'10','answer_dict':'{"1":{"answer":["B"]}}'},
                        result={'success':True,'data':{'1':[{'my_count':1},0]}})
        self.monitor._request(request);self.monitor._finished(request)
        self.assertEqual(self.probe.outcome,'accepted')

    def test_text_hash_identity_cannot_mistake_an_observed_post_for_no_request(self):
        untrusted=self.monitor.begin(self.page,'stem-hash','10',trusted_identity=False)
        request=Request(self.page)
        self.monitor._request(request)
        self.assertEqual(untrusted.outcome,'uncertain')
