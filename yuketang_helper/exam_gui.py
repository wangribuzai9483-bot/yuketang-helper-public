from dataclasses import replace
import json
import tkinter as tk
from tkinter import ttk
from . import scroll_widgets as scrolledtext
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .homework_gui import TYPE_NAMES, HomeworkPanel
from .llm import is_exam_portal_url
from .page_components import FeaturePage
from .ui_shell import ActionButton, InputCard, PrimaryButton, MUTED

DEFAULT_EXAM = ''  # Each user supplies their own portal URL in local settings.


class ExamPanel:
    def __init__(self, parent, model_panel, get_engine, send):
        self.model_panel=model_panel;self.get_engine=get_engine;self.send=send
        self.url=tk.StringVar(value=model_panel.settings.exam_url or DEFAULT_EXAM)
        self.index=tk.IntVar(value=1);self.overwrite=tk.BooleanVar(value=False)
        self.status=tk.StringVar(value='先打开考试说明页；自行开始答题后，再识别或填写。模型沿用“作业 → 模型设置”。')
        self.feature=FeaturePage(parent,'考试工作区','打开考试说明页，逐题识别与填写；交卷请在网页手动完成',
                                 '考试操作与详情','考试操作与详情',icon='exam')
        InputCard(self.feature.content,self.url,placeholder='粘贴考试说明页链接…').pack(fill='x')
        PrimaryButton(self.feature.actions,text='打开考试入口',command=self.open).pack()
        ttk.Label(self.feature.extra,textvariable=self.status,foreground=MUTED,wraplength=600,justify='center').pack()
        frame=self.feature.detail_body
        InputCard(frame,self.url,placeholder='粘贴考试说明页链接…').pack(fill='x',pady=(0,12))
        row=ttk.Frame(frame);row.pack(fill='x',pady=10)
        ttk.Label(row,text='题号').pack(side='left')
        ttk.Spinbox(row,from_=1,to=500,textvariable=self.index,width=5).pack(side='left',padx=10)
        ttk.Checkbutton(row,text='覆盖已有答案（默认保留）',variable=self.overwrite).pack(side='left',padx=12)
        tools=ttk.Frame(frame);tools.pack(fill='x',pady=8)
        for index,(title,command) in enumerate((('识别该题',self.inspect),('填写该题',lambda:self.solve(False)),
                ('连续填写',lambda:self.solve(True)),('停止填写',lambda:self.send('exam_cancel')),
                ('恢复窗口',lambda:self.send('restore')),('导出结构',lambda:self.send('exam_export')))):
            tools.columnconfigure(index%3,weight=1)
            ActionButton(tools,title,command).grid(row=index//3,column=index%3,sticky='ew',padx=4,pady=4)
        ttk.Label(frame,textvariable=self.status,foreground=MUTED,wraplength=650).pack(anchor='w',pady=10)
        tabs=ttk.Notebook(frame);tabs.pack(fill='both',expand=True)
        question_page=ttk.Frame(tabs);answer_page=ttk.Frame(tabs)
        tabs.add(question_page,text='题目内容');tabs.add(answer_page,text='模型结果 / 手动答案')
        self.question=scrolledtext.ScrolledText(question_page,height=7,wrap='word',state='disabled')
        self.question.pack(fill='both',expand=True,pady=8)
        self.answer=scrolledtext.ScrolledText(answer_page,height=7,wrap='word');self.answer.pack(fill='both',expand=True,pady=8)
        ttk.Button(answer_page,text='填入所提供的答案',command=self.fill).pack(anchor='w',pady=6)
        ttk.Label(answer_page,text='选择题：{"option_ids":["A","C"]}；简答：{"text":"答案"}；填空：{"blank_answers":["第一空"]}',
                  foreground=MUTED,wraplength=620).pack(anchor='w',pady=6)
        for view in (self.question,self.answer):
            view.configure(relief='flat',borderwidth=0,highlightthickness=0,bg='#FCFBFA',fg='#252525',font=('Microsoft YaHei UI',10),padx=18,pady=14)
        ttk.Label(frame,text='答案填写可能触发网页草稿保存，交卷请在网页核对后手动操作。',foreground=MUTED,wraplength=650).pack(anchor='w',pady=12)

    def position(self):
        value=self.index.get()
        if not 1<=value<=500:raise ValueError('题号需在 1–500 之间。')
        return value-1

    def open(self):
        try:
            url=self.url.get().strip()
            if not is_exam_portal_url(url):raise ValueError('请填写学校雨课堂门户的 quiz 或 exam 考试详情地址。')
            parts=urlparse(url);params=dict(parse_qsl(parts.query,keep_blank_values=True));params.pop('date',None)
            url=urlunparse(parts._replace(query=urlencode(params)));self.url.set(url)
            self.model_panel.settings=replace(self.model_panel.settings,exam_url=url)
            self.model_panel.settings.save()
            engine=self.get_engine(url)
            if engine:engine.command('exam_open',url)
        except (ValueError,OSError,tk.TclError) as exc:self.status.set(str(exc))

    def inspect(self):
        try:self.send('exam_read',{'index':self.position()})
        except (ValueError,tk.TclError):self.status.set('请输入有效题号。')

    def solve(self, batch):
        try:
            if not self.model_panel.save(require_model=True):return
            self.send('exam_solve',{'settings':self.model_panel.settings,'index':self.position(),'batch':batch,'overwrite':self.overwrite.get()})
        except (ValueError,tk.TclError):self.status.set('请输入有效题号。')

    def fill(self):
        try:self.send('exam_fill',{'answer':json.loads(self.answer.get('1.0','end')),'index':self.position(),'overwrite':self.overwrite.get()})
        except (ValueError,tk.TclError):self.status.set('请检查题号和答案 JSON 格式。')

    def render(self, data):
        self.status.set(data['message'])
        question=data.get('question')
        if question:
            self.index.set(question['index'])
            text=f"第 {question['index']}/{question['total']} 题 · {TYPE_NAMES.get(question['type'],question['type'])}\n\n{question['stem']}\n\n"
            text+='\n'.join(f"{o['id']}. {o['text']}" for o in question['options'])
            HomeworkPanel.replace_text(self.question,text)
        if 'answer' in data:
            self.answer.delete('1.0','end');self.answer.insert('1.0',json.dumps(data['answer'],ensure_ascii=False,indent=2))
