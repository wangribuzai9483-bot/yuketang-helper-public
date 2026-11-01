"""Browse local question records, review jobs and editable experience notes."""
import json
from tkinter import messagebox, scrolledtext, ttk
import tkinter as tk
from .page_components import Disclosure
from .ui_shell import DocumentMark, MUTED


STATUS = {'filled':'已填写','submitted':'已提交','submission_unknown':'提交待确认','rejected':'提交未接受'}
SOURCE = {'model':'模型','embedded':'题目自带答案','existing':'已有答案','user':'手动提供'}
CORRECTNESS = {'unknown':'待确认（尚无网站判分）','correct':'网站显示回答正确',
               'incorrect':'网站显示回答错误','partial':'网站显示部分正确'}


class LearningPanel:
    def __init__(self, parent, service, prepare_settings):
        self.service=service
        self.prepare_settings=prepare_settings
        self.rows={};self.items={};self.editing=None
        page=ttk.Frame(parent,padding=(24,20));page.pack(fill='both',expand=True)
        parent=page
        heading=ttk.Frame(parent);heading.pack(fill='x',pady=(0,12))
        icon=DocumentMark(heading,'learning');icon.set_size(64);icon.pack(side='left',padx=(0,12))
        title=ttk.Frame(heading);title.pack(side='left')
        ttk.Label(title,text='记录与经验',font=('Microsoft YaHei UI',23,'bold')).pack(anchor='w')
        ttk.Label(title,text='回看题目记录，积累自己的学习经验',foreground=MUTED).pack(anchor='w',pady=(5,0))
        self.status=tk.StringVar(value='做题记录自动保存在本机。额外总结/技巧请在“作业 → 模型设置”开启。')
        ttk.Label(parent,textvariable=self.status,wraplength=720).pack(anchor='w',padx=14,pady=10)
        controls=ttk.Frame(parent);controls.pack(fill='x',padx=14)
        ttk.Button(controls,text='刷新记录',command=self.refresh).pack(side='left')
        ttk.Button(controls,text='生成 / 重试所选作业复盘',command=self.review).pack(side='left',padx=6)
        self.search=tk.StringVar()
        ttk.Label(controls,text='搜索').pack(side='left',padx=(10,4))
        entry=ttk.Entry(controls,textvariable=self.search);entry.pack(side='left',fill='x',expand=True)
        entry.bind('<Return>',lambda _:self.refresh())
        self.tabs=ttk.Notebook(parent);self.tabs.pack(fill='both',expand=True,padx=14,pady=10)
        records=ttk.Frame(self.tabs);experiences=ttk.Frame(self.tabs)
        self.tabs.add(records,text='已做题记录');self.tabs.add(experiences,text='经验清单')
        self.records=self._table(records,('course','assignment','stem','status'),('课程','作业','题目','状态'))
        self.records.bind('<<TreeviewSelect>>',self.show_record)
        self.record_fold=Disclosure(records,'题目记录与复盘详情',height=210);self.record_fold.pack(fill='x',pady=8)
        self.detail=scrolledtext.ScrolledText(self.record_fold.body,height=8,wrap='word',state='disabled')
        self.detail.pack(fill='both',expand=True,pady=8)
        self.experiences=self._table(experiences,('course','topic','updated'),('课程','知识点','更新时间'))
        self.experiences.bind('<<TreeviewSelect>>',self.show_experience)
        self.experience_fold=Disclosure(experiences,'经验来源与个人修订',height=280);self.experience_fold.pack(fill='x',pady=8)
        experience_body=self.experience_fold.body
        self.material=scrolledtext.ScrolledText(experience_body,height=5,wrap='word',state='disabled')
        self.material.pack(fill='both',expand=True,pady=8)
        ttk.Label(experience_body,text='个人修订 / 补充（模型再次生成时保留）').pack(anchor='w')
        self.notes=scrolledtext.ScrolledText(experience_body,height=3,wrap='word');self.notes.pack(fill='x',pady=4)
        row=ttk.Frame(experience_body);row.pack(fill='x')
        ttk.Button(row,text='保存修订',command=self.save_notes).pack(side='left')
        ttk.Button(row,text='删除所选经验',command=self.delete).pack(side='left',padx=6)
        self.tabs.bind('<<NotebookTabChanged>>',lambda _:self.refresh())
        for view in (self.detail,self.material,self.notes):
            view.configure(relief='flat',borderwidth=0,highlightthickness=0,bg='#FCFBFA',fg='#252525',
                           font=('Microsoft YaHei UI',10),padx=14,pady=12)

    @staticmethod
    def _table(parent, columns, labels):
        box=ttk.Frame(parent);box.pack(fill='x')
        tree=ttk.Treeview(box,columns=columns,show='headings',height=5,selectmode='browse')
        for col,label in zip(columns,labels):
            tree.heading(col,text=label);tree.column(col,width=150 if col!='stem' else 280,minwidth=65)
        bar=ttk.Scrollbar(box,orient='vertical',command=tree.yview);tree.configure(yscrollcommand=bar.set)
        horizontal=ttk.Scrollbar(box,orient='horizontal',command=tree.xview);tree.configure(xscrollcommand=horizontal.set)
        box.columnconfigure(0,weight=1)
        tree.grid(row=0,column=0,sticky='nsew');bar.grid(row=0,column=1,sticky='ns');horizontal.grid(row=1,column=0,sticky='ew')
        return tree

    @staticmethod
    def _text(widget, value):
        widget.configure(state='normal');widget.delete('1.0','end');widget.insert('1.0',value);widget.configure(state='disabled')

    def refresh(self):
        try:
            selected=self.records.selection();experience=self.experiences.selection()
            needle=self.search.get().strip().casefold()
            self.rows={row['id']:row for row in self.service.store.records() if not needle or needle in json.dumps(row,ensure_ascii=False).casefold()}
            self.items={row['id']:row for row in self.service.store.experiences() if not needle or needle in json.dumps(row,ensure_ascii=False).casefold()}
            self.records.delete(*self.records.get_children());self.experiences.delete(*self.experiences.get_children())
            for key,row in self.rows.items():
                self.records.insert('', 'end',iid=key,values=(row['course'],row['assignment_title'],row['stem'][:65],STATUS.get(row['status'],row['status'])))
            for key,row in self.items.items():
                self.experiences.insert('','end',iid=key,values=(row['course'],row['topic'],row['updated']))
            if selected and selected[0] in self.rows:self.records.selection_set(selected)
            if experience and experience[0] in self.items:self.experiences.selection_set(experience)
            elif self.editing and self.editing not in self.items:
                self.editing=None;self.notes.delete('1.0','end');self._text(self.material,'')
        except Exception:
            self.status.set('无法读取学习记录，请检查本地存储目录是否可写。')

    def show_record(self, _event=None):
        selection=self.records.selection()
        if not selection or selection[0] not in self.rows:return
        row=self.rows[selection[0]]
        self.record_fold.set_open(True)
        versions=self.service.store.history(row['id'])
        reviews=self.service.store.reviews(row['scope'])
        text=(f"课程：{row['course']}\n作业：{row['assignment_title']}\n题目：{row['stem']}\n\n"
              +'\n'.join(f"{o['id']}. {o['text']}" for o in row['options'])
              +f"\n\n采用的答案：{json.dumps(row['answer'],ensure_ascii=False,indent=2)}\n"
              +f"来源：{SOURCE.get(row['answer_source'],row['answer_source'])}  模型：{row.get('model') or '—'}\n"
              +f"状态：{STATUS.get(row['status'],row['status'])}；正确性：{CORRECTNESS.get(row['correctness'],row['correctness'])}\n"
              +f"最近更新：{row['updated']}\n记录版本：{len(versions)}\n\n历次记录：\n"
              +'\n\n'.join(f"{v['recorded_at']} {STATUS.get(v['status'],v['status'])}\n{json.dumps(v['answer'],ensure_ascii=False)}" for v in versions))
        if reviews:
            text+='\n\n复盘任务记录（失败或未完成可点击上方按钮重试）：\n'+'\n'.join(
                f"{job['updated']} [{job['state']}] {job['message']}" for job in reviews)
        self._text(self.detail,text)

    def show_experience(self, _event=None):
        selection=self.experiences.selection()
        if not selection or selection[0] not in self.items:return
        key=selection[0];row=self.items[key]
        self.experience_fold.set_open(True)
        all_records={record['id']:record for record in self.service.store.records()}
        sources=[all_records[ref] for ref in row['record_ids'] if ref in all_records]
        text=f"{row['course']} · {row['topic']}\n\n{row['summary']}"
        if row['techniques']:text+='\n\n做题技巧：\n'+'\n'.join(row['techniques'])
        text+='\n\n来源题目：\n'+'\n'.join(f"{r['assignment_title']}：{r['stem']}（{STATUS.get(r['status'],r['status'])}，{CORRECTNESS.get(r['correctness'],r['correctness'])}）" for r in sources)
        text+=f"\n\n生成模型：{row['model']}\n模型整理内容请结合教材及网站反馈核对。"
        self._text(self.material,text)
        if self.editing!=key:
            self.editing=key;self.notes.delete('1.0','end');self.notes.insert('1.0',row['notes'])

    def save_notes(self):
        if not self.editing:return
        try:
            self.service.store.notes(self.editing,self.notes.get('1.0','end-1c'))
            self.status.set('个人修订已保存。');self.refresh()
        except Exception:self.status.set('保存修订失败，请检查存储目录。')

    def delete(self):
        if self.editing and messagebox.askyesno('删除经验','删除所选经验及其个人修订？来源题目记录会保留。'):
            try:self.service.store.delete_experience(self.editing);self.refresh()
            except Exception:self.status.set('删除经验失败，请检查存储目录。')

    def review(self):
        selected=self.records.selection()
        if not selected or selected[0] not in self.rows:
            self.status.set('请先在“已做题记录”选择一题，将为其所在作业生成复盘。');return
        settings=self.prepare_settings()
        if settings is None:return
        if not (settings.review_summary or settings.review_techniques):
            self.status.set('请先在“作业 → 模型设置”开启知识总结或做题技巧，再保存。');return
        try:self.service.request_review(self.rows[selected[0]]['scope'],settings,force=True)
        except Exception as exc:self.status.set(str(exc) if isinstance(exc,ValueError) else '无法启动复盘，请稍后重试。')

    def render(self, data):
        self.status.set(data['message']);self.refresh()
