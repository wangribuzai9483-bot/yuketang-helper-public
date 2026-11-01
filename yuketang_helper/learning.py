"""Durable study records and optional reviews; no browser objects enter workers."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading

from .config import RUNTIME
from .llm import ModelClient
from .secrets import KeyStore


def timestamp():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


class LearningStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else RUNTIME / 'learning.sqlite3'
        self.lock = threading.RLock()

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            db.executescript('''
          CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, scope TEXT, course_key TEXT,
            course TEXT, assignment_title TEXT, data TEXT, updated TEXT);
          CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, record_id TEXT, data TEXT, created TEXT);
          CREATE TABLE IF NOT EXISTS experiences (id TEXT PRIMARY KEY, course_key TEXT, course TEXT,
            topic TEXT, data TEXT, notes TEXT DEFAULT '', updated TEXT);
          CREATE TABLE IF NOT EXISTS reviews (signature TEXT PRIMARY KEY, scope TEXT, state TEXT,
            message TEXT, updated TEXT);
          CREATE INDEX IF NOT EXISTS record_scope ON records(scope);
            ''')
            with db:
                yield db
        finally:
            db.close()

    def record(self, course, assignment, question, answer, model='', status='filled', assignment_title=''):
        host, path = assignment
        path = path.split('?', 1)[0].split('#', 1)[0]
        scope = host + path
        match = re.search(r'/lms-graph/(\d+)', path)
        course_key = host + ':' + (match[1] if match else course)
        key = digest([scope, str(question['id'])])
        # Only displayed question fields and the adopted answer. No raw metadata,
        # embedded answer keys, page addresses, cookies or API credentials.
        clean_answer = {k:answer[k] for k in ('option_ids','blank_answers','text','explanation') if k in answer} if answer else None
        data = dict(id=key, scope=scope, course_key=course_key, course=course,
                    assignment_title=assignment_title or '作业 ' + path.rstrip('/').split('/')[-1],
                    question_id=str(question['id']), question_type=question['type'],
                    stem=question['stem'], options=[{'id':o['id'],'text':o['text']} for o in question.get('options',[])],
                    answer=clean_answer, answer_source=question.get('answer_source','existing'),
                    model=model if question.get('answer_source')=='model' else '', status=status,
                    correctness=question.get('correctness') if status=='submitted' and
                        question.get('correctness') in {'correct','incorrect','partial'} else 'unknown')
        with self.lock, self._connect() as db:
            old = db.execute('SELECT data FROM records WHERE id=?',(key,)).fetchone()
            if old:
                previous=json.loads(old['data'])
                same_question=all(previous.get(k)==data.get(k) for k in ('stem','options','question_type'))
                if same_question and data['answer'] is None:
                    data['answer']=previous['answer']
                def core(value):
                    return {k:v for k,v in value.items() if k!='explanation'} if value else value
                if same_question and core(previous['answer'])==core(data['answer']):
                    if data['answer'] and previous['answer'] and not data['answer'].get('explanation') and previous['answer'].get('explanation'):
                        data['answer']['explanation']=previous['answer']['explanation']
                    if data['correctness']=='unknown':data['correctness']=previous.get('correctness','unknown')
                    if previous['status']=='submitted' and status=='filled':
                        data['status']='submitted'
                    if previous.get('answer_source')!='existing' and data['answer_source']=='existing':
                        data['answer_source']=previous['answer_source'];data['model']=previous.get('model','')
                if previous==data:
                    return key
            encoded=json.dumps(data,ensure_ascii=False)
            now=timestamp()
            db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?,?,?,?)',
                       (key,scope,course_key,course,data['assignment_title'],encoded,now))
            db.execute('INSERT INTO history(record_id,data,created) VALUES(?,?,?)',(key,encoded,now))
        return key

    def records(self, scope=None):
        with self.lock, self._connect() as db:
            rows=db.execute('SELECT data,updated FROM records '+('WHERE scope=? ' if scope else '')+'ORDER BY updated DESC,id',
                            (scope,) if scope else ()).fetchall()
            return [dict(json.loads(row['data']),updated=row['updated']) for row in rows]

    def history(self, key):
        with self.lock, self._connect() as db:
            return [dict(json.loads(row['data']),recorded_at=row['created']) for row in
                    db.execute('SELECT data,created FROM history WHERE record_id=? ORDER BY id DESC',(key,))]

    def experiences(self):
        with self.lock, self._connect() as db:
            return [dict(json.loads(row['data']),id=row['id'],course=row['course'],notes=row['notes'],updated=row['updated'])
                    for row in db.execute('SELECT * FROM experiences ORDER BY updated DESC,id')]

    def save_experiences(self, rows, items, model, signature):
        source={row['id']:row for row in rows}
        message='复盘已完成，经验清单已更新。' if items else '本次没有适用的做题技巧，做题记录已保留。'
        with self.lock, self._connect() as db:
            for item in items:
                refs=item['record_ids']
                course=source[refs[0]]
                key=digest([course['course_key'], re.sub(r'\W+','',item['topic']).casefold()])
                existing=db.execute('SELECT data FROM experiences WHERE id=?',(key,)).fetchone()
                data=dict(item,model=model)
                if existing:
                    previous=json.loads(existing['data'])
                    data['record_ids']=sorted(set(refs+previous['record_ids']))
                    if not data['summary']:data['summary']=previous.get('summary','')
                    if not data['techniques']:data['techniques']=previous.get('techniques',[])
                # Update generated material without overwriting personal notes.
                db.execute('''INSERT INTO experiences(id,course_key,course,topic,data,updated) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET topic=excluded.topic,data=excluded.data,updated=excluded.updated''',
                           (key,course['course_key'],course['course'],item['topic'],json.dumps(data,ensure_ascii=False),timestamp()))
            self._review_status(db,signature,rows[0]['scope'],'complete',message)
        return message

    def notes(self, key, value):
        with self.lock, self._connect() as db:
            db.execute('UPDATE experiences SET notes=?,updated=? WHERE id=?',(value[:30000],timestamp(),key))

    def delete_experience(self, key):
        with self.lock, self._connect() as db:
            db.execute('DELETE FROM experiences WHERE id=?',(key,))

    @staticmethod
    def _review_status(db, signature, scope, state, message):
        db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?,?,?)',(signature,scope,state,message,timestamp()))

    def review_status(self, signature, scope='', state=None, message=''):
        with self.lock, self._connect() as db:
            if state:
                self._review_status(db,signature,scope,state,message)
            row=db.execute('SELECT * FROM reviews WHERE signature=?',(signature,)).fetchone()
            return dict(row) if row else None

    def reviews(self, scope=None):
        with self.lock, self._connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM reviews '+('WHERE scope=? ' if scope else '')+
                    'ORDER BY updated DESC LIMIT 100',(scope,) if scope else ())]


class LearningService:
    def __init__(self, store=None, notify=None, key_loader=None):
        self.store=store or LearningStore()
        self.notify=notify or (lambda data:None)
        self.key_loader=key_loader or (lambda endpoint:KeyStore().load(endpoint))
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='learning-review')
        self.lock=threading.RLock()
        self.pending={}
        self.closed=False

    def emit(self, message, state='updated'):
        if not self.closed:
            self.notify({'state':state,'message':message})

    def record(self, *args, **kwargs):
        key=self.store.record(*args,**kwargs)
        self.emit('做题记录已保存；提交状态与答案正确性分别记录。')
        return key

    def request_review(self, scope, settings, force=False):
        settings=replace(settings).validate()
        if not (settings.review_summary or settings.review_techniques):
            return []
        settings.validate(require_model=True)
        rows=self.store.records(scope)
        if not rows:
            raise ValueError('这份作业还没有本地做题记录。')
        # Keep requests bounded and batch by assignment, not by every question.
        batches=[];batch=[];size=0
        for row in sorted(rows,key=lambda row:row['id']):
            length=len(json.dumps(row,ensure_ascii=False))
            if length>45000:
                raise ValueError('某道题内容过长，未生成复盘；做题记录已保留。')
            if batch and (len(batch)>=20 or size+length>45000):
                batches.append(batch);batch=[];size=0
            batch.append(row);size+=length
        if batch:batches.append(batch)
        futures=[]
        for batch in batches:
            signature=digest([[{k:v for k,v in row.items() if k!='updated'} for row in batch],
                              settings.endpoint,settings.model,settings.review_summary,settings.review_techniques])
            with self.lock:
                if self.closed:return futures
                if signature in self.pending:
                    futures.append(self.pending[signature]);continue
                previous=self.store.review_status(signature)
                if previous and previous['state']=='complete' and not force:continue
                self.store.review_status(signature,scope,'queued','等待后台复盘。')
                self.emit('复盘已加入后台队列，课程流程可继续。','queued')
                future=self.executor.submit(self._review,batch,settings,signature)
                self.pending[signature]=future
                future.add_done_callback(lambda done,key=signature:self._finished(key))
                futures.append(future)
        return futures

    def _finished(self, signature):
        with self.lock:self.pending.pop(signature,None)

    def _review(self, rows, settings, signature):
        try:
            client=ModelClient(replace(settings,timeout=min(settings.timeout,60)),self.key_loader(settings.endpoint))
            items=client.review(rows)
            message=self.store.save_experiences(rows,items,settings.model,signature)
            self.emit(message,'complete')
        except Exception as exc:
            message=str(exc) if isinstance(exc,ValueError) else '复盘失败；记录已保留，可稍后手动重试。'
            try:self.store.review_status(signature,rows[0]['scope'],'error',message)
            except sqlite3.Error:message='复盘记录写入失败，请检查本地存储目录；课程流程继续。'
            self.emit(message,'error')

    def close(self):
        self.closed=True
        self.executor.shutdown(wait=False,cancel_futures=True)
