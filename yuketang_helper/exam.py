"""Fill the paper the user has opened, through its ordinary visible controls."""
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import json
import time
from urllib.parse import urlparse

from .config import RUNTIME, allowed_url
from .homework import HOMEWORK_SCRIPT
from .llm import ModelClient, is_exam_portal_url, validate_answer
from .ocr import RenderedTextReader
from .secrets import KeyStore
from .verification import verification_state

EXAM_SCRIPT=HOMEWORK_SCRIPT+'\n'+Path(__file__).with_suffix('.js').read_text(encoding='utf-8')


def exam_frame_url(url):
    parts=urlparse(url)
    return allowed_url(url) or (parts.scheme=='https' and parts.hostname=='examination.xuetangx.com'
                               and not parts.username and not parts.password)


class ExamSession:
    def __init__(self, owner):
        self.owner=owner
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='exam-model')
        self.portal_url='';self.trusted=set();self.page=None;self.bound_paper=None
        self.pending=None;self.batch=False;self.deferred=False;self.index=0;self.overwrite=False
        self.pending_source='model';self.manual_deferred=None
        self.settings=None;self.waiting=False;self.clear_since=None;self.completed=0;self.seen=set()
        self.reader=RenderedTextReader(notify=lambda:self.emit('recognizing','正在识别考试页实际显示的文字。'),blocked=self._verification)

    def emit(self, state, message, **extra):
        self.owner.emit('exam',{'state':state,'message':message,**extra})

    def open(self, url):
        if not is_exam_portal_url(url):raise ValueError('请填写雨课堂学校门户的考试说明页地址。')
        self.cancel(quiet=True)
        self.portal_url=url;self.trusted.clear();self.bound_paper=None;self.page=None
        self.owner.mode='exam';self.owner._set_paused(True);self.owner.emit('mode','exam')
        wanted=urlparse(url)
        for page in self.owner.context.pages:
            if not page.is_closed() and urlparse(page.url)[:3]==wanted[:3]:
                self.page=page;break
        if self.page is None:
            login=self.owner.owned_page
            if login and not login.is_closed() and urlparse(login.url).path.rstrip('/')=='/web':
                self.page=login
                self.page.goto(replace(self.owner.config,course_url=url).entry_url(),wait_until='commit',timeout=15000)
            else:
                self.page=self.owner.context.new_page();self.page.goto(url,wait_until='domcontentloaded',timeout=30000)
        self.trusted.add(self.page)
        self.page.bring_to_front()
        self.emit('opening','已打开考试入口。请自行阅读说明并开始答题，之后回助手识别或填写；当前没有开始考试。')

    def _pages(self):
        if not self.portal_url:return []
        portal=urlparse(self.portal_url)
        pages=[p for p in self.owner.context.pages if not p.is_closed()]
        for page in pages:
            if urlparse(page.url)[:3]==portal[:3]:self.trusted.add(page)
        for _ in range(3):
            for page in pages:
                if page not in self.trusted and exam_frame_url(page.url) and page.opener() in self.trusted:
                    self.trusted.add(page)
        return [p for p in pages if p in self.trusted and exam_frame_url(p.url)]

    def _find(self):
        found=[]
        for page in self._pages():
            for frame in page.frames:
                if not exam_frame_url(frame.url):continue
                frame.evaluate(EXAM_SCRIPT)
                if frame.evaluate('window.__yktExam.count()'):
                    if frame.evaluate('window.__yktExam.blocked()'):raise ValueError('考试页面有待处理提示或核验窗口，请先在网页处理后再继续。')
                    identity=(urlparse(frame.url).netloc,urlparse(frame.url).path)
                    if self.bound_paper and identity!=self.bound_paper:continue
                    found.append((page,frame,identity))
        if len(found)>1:raise ValueError('发现多个考试答题页，请仅保留当前要填写的一份试卷。')
        if not found:return None
        page,frame,identity=found[0]
        self.page=page;self.bound_paper=identity
        return page,frame

    def current(self, index=None, resolve=True):
        target=self._find()
        if not target:return None
        page,frame=target
        question=frame.evaluate('i=>window.__yktExam.select(i)',self.index if index is None else index)
        if not question:return None
        if resolve:question=self.reader.resolve(frame,question)
        return page,frame,question

    def _verification(self):
        if self.owner.mode!='exam':return False
        required=next((page for page in self._pages() if verification_state(page)['required']),None)
        if required:
            self.clear_since=None
            if not self.waiting:
                self.waiting=True;self.page=required
                if hasattr(self.owner,'set_verification_page'):self.owner.set_verification_page(required)
                self.owner.emit('attention',{'state':'verification','message':'考试页面需要手动完成安全核验，完成后继续原填写任务。'})
                try:self.owner.window_state(required,'normal');required.bring_to_front()
                except Exception:pass
                self.emit('verification','已暂停填写，请在考试窗口手动完成安全核验。')
            return True
        if self.waiting:
            if self.clear_since is None:self.clear_since=time.monotonic()
            if time.monotonic()-self.clear_since<1:return True
            self.waiting=False
            if hasattr(self.owner,'clear_verification_page'):self.owner.clear_verification_page()
            self.owner.emit('attention',{'state':'verification-cleared'})
            self.emit('resumed','核验窗口已关闭，正在继续原填写任务。')
        return False

    def inspect(self, index=0):
        if self.pending:raise ValueError('模型请求尚未结束，请先停止填写后再定位其他题目。')
        self.owner.mode='exam';self.index=index
        if self._verification():return
        current=self.current()
        if not current:
            self.emit('waiting','尚未找到该题。考试说明页没有题面；请自行开始答题后再识别，或检查题号。');return
        self.emit('ready',f'已识别第 {index+1}/{current[2]["total"]} 题。',question=current[2])

    def start(self, settings, index=0, batch=False, overwrite=False):
        if self.pending:raise ValueError('当前请求尚未结束，请等待或先停止填写。')
        if not self.portal_url:raise ValueError('请先打开考试入口，绑定当前试卷。')
        self.settings=replace(settings).validate(require_model=True)
        self.manual_deferred=None
        self.owner.mode='exam';self.index=index;self.batch=batch;self.overwrite=overwrite
        self.completed=0;self.seen.clear()
        self._solve()

    def _solve(self):
        if self._verification():self.deferred=True;return
        self.deferred=False
        try:current=self.current()
        except ValueError:
            if self._verification():self.deferred=True;return
            raise
        if not current:
            self.batch=False;self.emit('waiting','尚未找到答题页或题号。请自行开始答题后再识别；没有自动点击开始。');return
        page,frame,question=current
        if question['id'] in self.seen:
            self.batch=False;raise ValueError('题目标识重复，已停止填写。')
        self.seen.add(question['id'])
        if question['read_only'] or (question['answered'] and not self.overwrite):
            self.emit('skipped',f'第 {self.index+1} 题已有答案或只读，已保留。',question=question)
            return self._next(question)
        client=ModelClient(self.settings,KeyStore().load(self.settings.endpoint))
        self.pending_source='model'
        self.pending=(self.executor.submit(client.solve,question),page,frame,question)
        self.emit('thinking',f'正在解答第 {self.index+1}/{question["total"]} 题，返回后填写。',question=question)

    def fill(self, answer, index=0, overwrite=False):
        if self.pending:raise ValueError('模型请求尚未结束，请先停止填写。')
        self.owner.mode='exam';self.index=index;self.overwrite=overwrite;self.batch=False
        if self._verification():
            self.manual_deferred=(answer,index,overwrite)
            return
        current=self.current()
        if not current:raise ValueError('未找到可填写的题目，请先识别考试题面。')
        page,frame,question=current
        if question['read_only'] or (question['answered'] and not overwrite):raise ValueError('本题已有答案或只读，已保留当前内容。')
        answer=validate_answer(question,answer)
        ready=Future();ready.set_result(answer)
        self.pending=(ready,page,frame,question);self.pending_source='user'
        self.tick()

    def _fill(self, page, frame, question, answer, source='model'):
        live=frame.evaluate('i=>window.__yktExam.select(i)',self.index)
        if not live or live['fingerprint']!=question['fingerprint']:
            raise ValueError('考试题目已变化，没有填入旧答案。')
        verified=frame.evaluate('p=>window.__yktHomework.fill(p)',{
            'fingerprint':question['fingerprint'],'draft_fingerprint':question['draft_fingerprint'],
            'answer':answer,'overwrite':self.overwrite})
        if self._verification():return False
        if not verified:raise ValueError('填入后核对不一致，请在网页检查。')
        self._accept(question,answer,source)
        return True

    def _accept(self, question, answer, source='model'):
        self.completed+=1
        self.emit('filled',f'已填入第 {self.index+1}/{question["total"]} 题；请在网页核对，交卷由你操作。',question=question,answer=answer)
        service=getattr(self.owner,'learning',None)
        if service:
            portal=urlparse(self.portal_url)
            try:service.record(self.owner.config.course_name,(portal.netloc,portal.path),{**question,'answer_source':source},answer,
                               model=self.settings.model if self.settings else '',assignment_title='期末考试',status='filled')
            except Exception:service.emit('考试填写记录暂未保存，请检查本机存储目录。','error')

    def _next(self, question):
        if not self.batch:return
        if self.index+1>=question['total']:
            self.batch=False
            if question.get('expected_total',question['total'])>question['total']:
                self.emit('partial','当前显示的题目已处理，试卷还有未展开或未加载的题组。请在网页切换题组后重新识别；尚未到整卷末尾。')
            else:self.emit('complete',f'已处理到试卷末尾，本次填写 {self.completed} 题。请在网页核对并自行交卷。')
        else:self.index+=1

    def tick(self):
        if self.owner.mode!='exam':return
        if self._verification():return
        if not self.pending:
            if self.manual_deferred:
                payload=self.manual_deferred;self.manual_deferred=None
                self.fill(*payload)
                return
            if self.batch or self.deferred:self._solve()
            return
        future,page,frame,question=self.pending
        if not future.done():return
        try:
            answer=future.result()
            current=self.current(resolve=False)
            if not current or current[0]!=page or current[1]!=frame or current[2]['fingerprint']!=question['fingerprint']:
                raise ValueError('考试页或题目已变化，没有填入旧答案。')
            # A manual verification can interrupt a checkbox click. A completed
            # answer is retained; a partial fill resumes its existing transaction.
            done=frame.evaluate('p=>window.__yktHomework.verify(p)',{'fingerprint':question['fingerprint'],'answer':answer})
            if not done and not self._fill(page,frame,question,answer,self.pending_source):return
            if done:self._accept(question,answer,self.pending_source)
            self.pending=None;self._next(question)
        except Exception as exc:
            if self._verification():return
            self.pending=None;self.batch=False
            self.emit('error',str(exc) if isinstance(exc,ValueError) else '考试控件已变化，请重新识别题目。')

    def export(self):
        samples=[]
        for page in self._pages():
            for frame in page.frames:
                if not exam_frame_url(frame.url):continue
                frame.evaluate(EXAM_SCRIPT);samples.append(frame.evaluate('window.__yktExam.sample()'))
        folder=RUNTIME/'exam-samples';folder.mkdir(parents=True,exist_ok=True)
        path=folder/(time.strftime('%Y%m%d-%H%M%S')+'.json')
        path.write_text(json.dumps({'frames':samples},ensure_ascii=False,indent=2),encoding='utf-8')
        self.emit('exported',f'已导出本机考试页面结构：{path}')

    def cancel(self, quiet=False):
        if self.pending:self.pending[0].cancel()
        self.pending=None;self.batch=False;self.deferred=False
        self.manual_deferred=None
        if self.waiting:
            self.waiting=False;self.owner.emit('attention',{'state':'verification-cleared'})
            if hasattr(self.owner,'clear_verification_page'):self.owner.clear_verification_page()
        if not quiet:self.emit('stopped','已停止考试填写；已发出请求的返回结果不会继续填入。')

    def close(self):
        self.cancel(quiet=True);self.executor.shutdown(wait=False,cancel_futures=True)
