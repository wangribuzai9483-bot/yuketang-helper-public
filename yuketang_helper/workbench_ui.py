"""Advanced homework controls; every action delegates to the existing panel."""
import tkinter as tk
from tkinter import ttk, scrolledtext
from .ui_shell import ActionButton, InputCard, PageStack, BG, INK, MUTED, LINE


class Workbench(ttk.Frame):
    def __init__(self, parent, panel):
        super().__init__(parent, padding=(24, 20))
        self.pack(fill='both', expand=True)
        self.panel = panel
        heading = ttk.Frame(self)
        heading.pack(fill='x', pady=(0, 18))
        ActionButton(heading, '‹ 返回流程', panel.toggle_details, primary=True, width=130).pack(side='right')
        ttk.Label(heading, text='作业工作台', font=('Microsoft YaHei UI', 24, 'bold')).pack(anchor='w')
        ttk.Label(heading, text='操作、识别结果与任务参数', foreground=MUTED).pack(anchor='w', pady=(5, 0))
        InputCard(self, panel.vars['homework_url']).pack(fill='x', pady=(0, 12))
        self.toolbar = ttk.Frame(self)
        self.toolbar.pack(fill='x', pady=(0, 14))
        actions = [('打开并登录', panel.open), ('识别当前题', lambda: panel.send('homework_read')),
                   ('填写当前题', lambda: panel.solve(False)), ('连续填写', lambda: panel.solve(True))]
        self.buttons = [ActionButton(self.toolbar, text=text, command=command, primary=index == 0)
                        for index, (text, command) in enumerate(actions)]
        menu_button = ttk.Menubutton(self.toolbar, text='更多操作', style='Workbench.TMenubutton')
        menu = tk.Menu(menu_button, tearoff=False, bg=BG, fg=INK)
        for text, command in [('导出题面样本', lambda: panel.send('homework_export')),
                              ('返回视频模式', lambda: panel.send('video_mode')),
                              ('后台运行（最小化）', lambda: panel.send('minimize')),
                              ('核验完成后继续', lambda: panel.send('homework_resume')),
                              ('查看原始结果 JSON', lambda: self.select('raw'))]:
            menu.add_command(label=text, command=command)
        menu_button.configure(menu=menu)
        self.buttons.append(menu_button)
        self.toolbar.bind('<Configure>', self._layout_toolbar)
        strip = ttk.Frame(self)
        strip.pack(fill='x', pady=(0, 12))
        ttk.Checkbutton(strip, text='覆盖已有答案', variable=panel.overwrite).pack(side='left', padx=(0, 16))
        ttk.Checkbutton(strip, text='填写后自动提交', variable=panel.auto_submit).pack(side='left')
        ActionButton(strip, text='停止任务', command=lambda: panel.send('homework_cancel'), width=110).pack(side='right', padx=4)
        ActionButton(strip, text='恢复浏览器', command=lambda: panel.send('restore'), width=130).pack(side='right', padx=4)
        ttk.Label(self, textvariable=panel.status, foreground=MUTED, wraplength=700).pack(anchor='w', pady=(0, 12))
        card = tk.Frame(self, bg=BG, highlightthickness=1, highlightbackground=LINE)
        card.pack(fill='both', expand=True)
        tabbar = tk.Frame(card, bg=BG)
        tabbar.pack(fill='x', padx=14, pady=(5, 0))
        self.labels = {}
        for key, title in [('question', '题目内容'), ('result', '识别结果'), ('explanation', '模型说明'), ('settings', '参数设置')]:
            label = tk.Label(tabbar, text=title, bg=BG, fg=MUTED, padx=16, pady=13,
                             font=('Microsoft YaHei UI', 10), cursor='hand2', takefocus=1)
            label.pack(side='left')
            label.bind('<Button-1>', lambda _, name=key: self.select(name))
            label.bind('<Return>', lambda _, name=key: self.select(name))
            self.labels[key] = label
        ttk.Separator(card).pack(fill='x', padx=16)
        self.stack = PageStack(card)
        self.stack.pack(fill='both', expand=True, padx=14, pady=14)
        self.pages = {}
        self.texts = {}
        for key in ('question', 'result', 'explanation', 'settings', 'raw'):
            page = ttk.Frame(self.stack)
            self.stack.add(page)
            self.pages[key] = page
            if key != 'settings':
                view = scrolledtext.ScrolledText(page, wrap='word', relief='flat', borderwidth=0,
                                                highlightthickness=0, bg='#FCFBFA', fg=INK,
                                                font=('Microsoft YaHei UI', 11), padx=22, pady=22, height=5)
                view.configure(state='disabled')
                view.pack(fill='both', expand=True)
                self.texts[key] = view
        panel.question, panel.answer = self.texts['question'], self.texts['raw']
        settings = self.pages['settings']
        ttk.Label(settings, text='当前任务', font=('Microsoft YaHei UI', 13, 'bold')).pack(anchor='w', pady=12)
        ttk.Checkbutton(settings, text='覆盖已有答案（默认保留）', variable=panel.overwrite).pack(anchor='w', pady=8)
        ttk.Checkbutton(settings, text='填写后自动提交本题', variable=panel.auto_submit).pack(anchor='w', pady=8)
        ttk.Label(settings, textvariable=panel.submit_hint, wraplength=650, foreground=MUTED).pack(anchor='w', pady=12)
        ttk.Label(settings, text='选择题答案来源沿用视频播放页设置。\n完整模型连接参数位于左侧「模型设置」。',
                  foreground=MUTED, wraplength=600).pack(anchor='w', pady=12)
        ttk.Label(self, text='提示：识别后可选择填写当前题或连续填写；任务参数在开始填写时生效。',
                  foreground=MUTED, wraplength=700).pack(anchor='w', pady=(12, 0))
        self.columns = None
        self.select('question')
        self.refresh()

    def _layout_toolbar(self, event):
        columns = 5 if event.width >= 700 else 3
        if columns == self.columns:
            return
        self.columns = columns
        for column in range(5):
            self.toolbar.columnconfigure(column, weight=1 if column < columns else 0)
        for index, button in enumerate(self.buttons):
            button.grid(row=index // columns, column=index % columns, sticky='ew', padx=4, pady=4)

    def select(self, key):
        self.stack.select(self.pages[key])
        for name, label in self.labels.items():
            label.configure(fg=INK if name == key else MUTED,
                            font=('Microsoft YaHei UI', 10, 'bold' if name == key else 'normal'))

    def refresh(self):
        p = self.panel
        if not p.current_question:
            p.replace_text(p.question, '识别后将在这里显示题目内容')
            p.question.tag_configure('empty', justify='center', foreground=MUTED, spacing1=12)
            p.question.tag_add('empty', '1.0', 'end')
        answer = p.current_answer or {}
        values = answer.get('option_ids') or answer.get('blank_answers') or answer.get('text')
        result = '、'.join(map(str, values)) if isinstance(values, list) else str(values or '暂无识别结果')
        p.replace_text(self.texts['result'], result)
        p.replace_text(self.texts['explanation'], answer.get('explanation') or '暂无模型说明')
