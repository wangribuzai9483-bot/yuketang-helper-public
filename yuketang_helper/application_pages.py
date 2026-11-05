"""Visual pages backed by the App's existing configuration and task commands."""
import tkinter as tk
from tkinter import ttk
from . import scroll_widgets as scrolledtext
from tkinter import font as tkfont
from .page_components import FeaturePage, Disclosure
from .ui_shell import ActionButton, FlowConnector, InputCard, PlaybackProgress, PrimaryButton, BG, LINE, INK, MUTED, rounded_rect


class CourseSequence(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.signature = None
        self.render({})

    def render(self, data):
        stage = data.get('state')
        index = 0 if data.get('kind') == 'video' else 1 if data.get('kind') == 'homework' else -1
        if stage in {'advance-wait', 'navigating'}:
            index = 2
        active = index >= 0 and stage not in {None, 'idle', 'stopped', 'paused', 'blocked', 'error', 'complete', 'review'}
        signature = (index, active)
        if signature == self.signature:
            return
        self.signature = signature
        for widget in self.winfo_children():
            widget.destroy()
        for position, text in enumerate(('视频学习', '作业处理', '下一项目')):
            if position:
                connector = FlowConnector(self, active=active and position == max(1, index))
                connector.pack(side='left', padx=8)
            ttk.Label(self, text=text, foreground='#252525' if position == index else MUTED,
                      font=('Microsoft YaHei UI', 11)).pack(side='left', pady=8)


def set_video_active(app, active):
    if active:
        app.video_page.content.pack_forget()
        app.video_input.pack_forget()
        app.start_button.pack_forget()
    else:
        app.video_page.content.pack(before=app.video_page.actions, fill='x')
        app.video_input.pack(fill='x')
        app.start_button.pack(before=app.video_control_row, pady=(0, 12))


def text_view(parent, height=8):
    view = scrolledtext.ScrolledText(parent, height=height, wrap='word', state='disabled',
                                    relief='flat', borderwidth=0, highlightthickness=0,
                                    font=('Microsoft YaHei UI', 10), bg='#FCFBFA', fg='#252525', padx=18, pady=16)
    view.pack(fill='both', expand=True)
    return view


def build_video(app, parent, answer_labels):
    for key in ('login_url', 'course_name', 'course_url', 'browser'):
        app.vars[key] = tk.StringVar(value=getattr(app.config, key))
    app.vars['speed'] = tk.StringVar(value=str(app.config.speed))
    for key in ('muted', 'auto_next', 'auto_minimize', 'resume_paused'):
        app.vars[key] = tk.BooleanVar(value=getattr(app.config, key))
    app.vars['bypass'] = tk.BooleanVar(value=app.config.verification_bypass != 'off')
    app.vars['answer_source'] = tk.StringVar(value=answer_labels.get(app.config.answer_source, answer_labels['auto']))
    app.status = tk.StringVar(value='开始一段学习')
    app.video = tk.StringVar(value='登录后进入视频学习页')
    app.progress_text = tk.StringVar(value='00:00:00 / 00:00:00')
    page = FeaturePage(parent, app.status, '粘贴课程链接，登录后继续视频学习', '播放设置与详情', '播放设置与详情', icon='video')
    app.video_page = page
    app.video_input = InputCard(page.content, app.vars['course_url'], placeholder='粘贴课程直达链接…')
    app.video_input.pack(fill='x')
    app.start_button = PrimaryButton(page.actions, text='启动并登录', command=app.start)
    app.start_button.pack(pady=(0, 12))
    row = ttk.Frame(page.actions)
    app.video_control_row = row
    row.pack()
    app.pause_button = ttk.Button(row, text='暂停自动播放', command=lambda: app.send('pause'), state='disabled')
    app.pause_button.pack(side='left', padx=6)
    app.stop_button = ttk.Button(row, text='停止并关闭浏览器', command=app.stop, state='disabled')
    app.stop_button.pack(side='left', padx=6)
    app.video_progress_panel = tk.Canvas(page.front, bg=BG, highlightthickness=0)
    app.video_progress_panel.place(relx=.5, rely=1, y=-64, anchor='s', relwidth=.84, height=92)
    caption = tk.Label(app.video_progress_panel, textvariable=app.video, bg='#FCFBFA', fg=MUTED,
                       font=('Microsoft YaHei UI', 10), anchor='w')
    progress_clock = tk.StringVar(value=app.progress_text.get())
    progress_rate = tk.StringVar()
    def sync_timing(*_):
        clock, separator, rate = app.progress_text.get().partition(' · ')
        progress_clock.set(clock)
        progress_rate.set(rate if separator else '')
    app.progress_text.trace_add('write', sync_timing)
    timing_font = tkfont.Font(root=page, family='Consolas', size=10, slant='roman')
    timing = tk.Label(app.video_progress_panel, textvariable=progress_clock, bg='#FCFBFA', fg=INK,
                      font=timing_font, anchor='e')
    rate_label = tk.Label(app.video_progress_panel, textvariable=progress_rate, bg='#FCFBFA', fg=MUTED,
                         font=('Microsoft YaHei UI', 8), anchor='e')
    app.progress = PlaybackProgress(app.video_progress_panel)
    def arrange_progress(event):
        width = event.width
        time_width = max(220, timing_font.measure('00:00:00 / 00:00:00')+12)
        panel = app.video_progress_panel
        panel.delete('surface')
        rounded_rect(panel, 1, 1, width-1, 91, 15, fill='#FCFBFA', outline=LINE, width=1, tags='surface')
        panel.tag_lower('surface')
        caption.place(x=22, y=14, width=max(100,width-time_width-58), height=26)
        timing.place(x=max(130,width-time_width-22), y=14, width=time_width, height=26)
        app.progress.place(x=18, y=43, width=max(100,width-36), height=22)
        rate_label.place(x=max(130,width-time_width-22), y=69, width=time_width, height=16)
    app.video_progress_panel.bind('<Configure>', arrange_progress)
    page.front.bind('<Configure>', lambda event: app.video_progress_panel.place_configure(relwidth=0, width=min(int(event.width*.84),930)), add='+')
    settings = page.detail_body
    form = ttk.Frame(settings)
    form.pack(fill='x')
    form.columnconfigure(1, weight=1)
    for index, (key, title) in enumerate((('login_url', '登录入口'), ('course_name', '目标课程'), ('course_url', '课程直达链接'))):
        ttk.Label(form, text=title).grid(row=index, column=0, sticky='w', padx=(0, 18), pady=9)
        ttk.Entry(form, textvariable=app.vars[key]).grid(row=index, column=1, sticky='ew', pady=9)
    ttk.Label(form, text='浏览器').grid(row=3, column=0, sticky='w', padx=(0, 18), pady=9)
    selectors = ttk.Frame(form)
    selectors.grid(row=3, column=1, sticky='w', pady=9)
    ttk.Combobox(selectors, textvariable=app.vars['browser'],
                 values=('msedge', 'chrome', 'chromium'), state='readonly',
                 width=11, style='Polished.TCombobox').pack(side='left')
    ttk.Label(selectors, text='播放速度').pack(side='left', padx=(22, 12))
    ttk.Combobox(selectors, textvariable=app.vars['speed'],
                 values=('1.0', '1.25', '1.5', '2.0'), state='readonly',
                 width=11, style='Polished.TCombobox').pack(side='left')
    options = ttk.Frame(settings)
    options.pack(fill='x', pady=8)
    for index, (key, title) in enumerate((('muted','静音播放'),('auto_next','自动播放下一视频'),('auto_minimize','开始播放后最小化'),('resume_paused','恢复意外暂停'),('bypass','自动处理安全核验'))):
        ttk.Checkbutton(options, text=title, variable=app.vars[key]).grid(row=index//2, column=index%2, sticky='w', padx=(0, 24), pady=7)
    ttk.Label(settings, text='选择题答案来源', foreground=MUTED).pack(anchor='w', pady=(15, 6))
    ttk.Combobox(settings, textvariable=app.vars['answer_source'],
                 values=tuple(answer_labels.values()), state='readonly',
                 style='Polished.TCombobox').pack(fill='x')
    actions = ttk.Frame(settings)
    actions.pack(fill='x', pady=18)
    ActionButton(actions, '应用设置', app.apply, primary=True).pack(side='left', padx=(0, 8))
    ActionButton(actions, '最小化浏览器', lambda: app.send('minimize')).pack(side='left', padx=4)
    ActionButton(actions, '恢复浏览器', lambda: app.send('restore')).pack(side='left', padx=4)
    fold = Disclosure(settings, '技术运行记录', height=140)
    fold.pack(fill='x')
    app.logs = text_view(fold.body, height=4)


def build_course(app, parent):
    app.course_status = tk.StringVar(value='尚未启动统一课程流程')
    app.course_item = tk.StringVar(value='等待进入课程目录')
    app.course_progress = tk.StringVar(value='')
    app.course_heading = tk.StringVar(value='继续你的课程')
    page = FeaturePage(parent, app.course_heading, '按目录衔接视频与作业，保留当前学习进度', '流程详情与设置', '流程详情与设置', icon='course')
    app.course_page = page
    app.course_sequence = CourseSequence(page.content)
    app.course_sequence.pack(pady=(0, 12))
    PrimaryButton(page.actions, text='开始 / 继续课程', command=app.start_course).pack(pady=(0, 12))
    row = ttk.Frame(page.actions)
    row.pack()
    ActionButton(row, '暂停 / 恢复', lambda: app.send('course_pause')).pack(side='left', padx=6)
    ActionButton(row, '停止流程', lambda: app.send('course_stop')).pack(side='left', padx=6)
    for variable in (app.course_item, app.course_status, app.course_progress):
        ttk.Label(page.extra, textvariable=variable, foreground=MUTED, anchor='center', justify='center', wraplength=650).pack(pady=4)
    settings = page.detail_body
    ttk.Label(settings, text='课程与任务', font=('Microsoft YaHei UI', 16, 'bold')).pack(anchor='w', pady=(0, 12))
    ttk.Label(settings, textvariable=app.vars['course_name'], font=('Microsoft YaHei UI', 13)).pack(anchor='w')
    ttk.Label(settings, textvariable=app.course_item, foreground=MUTED, wraplength=680).pack(anchor='w', pady=10)
    row = ttk.Frame(settings)
    row.pack(fill='x', pady=12)
    ActionButton(row, '播放与课程设置', lambda: (app.show_page('video'), app.video_page.open_details())).pack(side='left', padx=(0, 10))
    ActionButton(row, '模型配置', lambda: app.show_page('model')).pack(side='left')
    ttk.Checkbutton(settings, text='填写后自动提交本题（与作业填写同步）', variable=app.homework_panel.auto_submit).pack(anchor='w', pady=12)
    row = ttk.Frame(settings)
    row.pack(fill='x', pady=14)
    for title, command in [('后台运行', 'minimize'),('恢复浏览器','restore'),('导出核验结构','verification_probe')]:
        ActionButton(row, title, lambda name=command: app.send(name)).pack(side='left', padx=(0, 8))
    fold = Disclosure(settings, '流程规则与注意事项', height=190)
    fold.pack(fill='x', pady=10)
    ttk.Label(fold.body, text='从网页当前目录位置继续，保留已有答案和提交记录。\n\n未开启自动提交时，作业填写后等待你核对和手动提交。\n\n讨论项会略过且不会代替发言；遇到考试、锁定内容或错误时暂停。\n\n课程位置按课程隔离保存，浏览器仍需使用有效登录会话。',
              foreground=MUTED, justify='left', wraplength=650).pack(anchor='w', padx=12, pady=10)


def build_logs(app, parent):
    page = FeaturePage(parent, '运行日志', '重要事件优先，完整技术记录按需查看', '完整运行日志', '完整日志与详情', icon='logs')
    app.logs_page = page
    page.mark.set_size(72)
    app.important_logs = text_view(page.content, height=8)
    app.log_view = text_view(page.detail_body, height=18)
    ttk.Label(page.extra, text='可选中文本并复制；日志最多保留最近 300 行。', foreground=MUTED).pack()
