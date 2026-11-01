from concurrent.futures import ThreadPoolExecutor
import json
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from tkinter import font as tkfont

from .llm import ModelClient, ModelSettings
from .secrets import KeyStore
from .workbench_ui import Workbench
from .page_components import FeaturePage, ScrollableForm
from .model_settings_ui import build_model_config
from .ui_shell import ASSETS, BG, Backdrop, DocumentMark, FlowSteps, InputCard, PageStack, PrimaryButton
from PIL import Image, ImageTk

TYPE_NAMES = {"single": "单选", "multiple": "多选", "judgment": "判断", "blank": "填空", "text": "简答"}
KEY_MASK = "************"


class HomeworkPanel:
    def __init__(self, parent, get_engine, send, hide_tabs=False):
        self.parent = parent
        self.get_engine = get_engine
        self.send = send
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model-connection-test")
        self.test_future = None
        self.closed = False
        try:
            self.settings = ModelSettings.load()
        except (ValueError, TypeError, OSError):
            self.settings = ModelSettings()
        self.vars = {key: tk.StringVar(value=str(getattr(self.settings, key))) for key in (
            "base_url", "model", "timeout", "homework_url", "instructions")}
        self.vars["json_mode"] = tk.BooleanVar(value=self.settings.json_mode)
        self.vars['review_summary'] = tk.BooleanVar(value=self.settings.review_summary)
        self.vars['review_techniques'] = tk.BooleanVar(value=self.settings.review_techniques)
        self.key = tk.StringVar()
        self.key_status = tk.StringVar()
        self._key_endpoint = None
        self._key_owner_endpoint = None
        self._key_dirty = False
        self._updating_key = False
        self.overwrite = tk.BooleanVar(value=False)
        self.auto_submit = tk.BooleanVar(value=self.settings.auto_submit)
        self.submit_hint = tk.StringVar()
        self.status = tk.StringVar(value="先设置模型接口，再打开作业并识别题面。")
        self.model_status = tk.StringVar(value="密钥按接口地址分别加密保存在本机。")
        tabs = PageStack(parent) if hide_tabs else ttk.Notebook(parent)
        self.tabs = tabs
        tabs.pack(fill="both", expand=True)
        work = ttk.Frame(tabs)
        model = ttk.Frame(tabs, padding=16)
        tabs.add(work, text="作业填写")
        tabs.add(model, text="模型设置")
        self.work_tab = work
        self.model_tab = model
        self.workspace_switch = PageStack(work)
        self.workspace_switch.pack(fill='both', expand=True)
        self.summary_page = ttk.Frame(self.workspace_switch)
        self.workspace_switch.add(self.summary_page)
        self.hero = tk.Canvas(self.summary_page, bg=BG, highlightthickness=0)
        self.hero.pack(fill="both", expand=True)
        self.background_source = Image.open(ASSETS / "workspace.png").convert("RGB")
        self.background_job = None
        self.hero.bind("<Configure>", self._draw_hero_background)
        self.hero_body = Backdrop(self.hero, self.hero)
        self.hero_body.place(relx=.5, rely=.48, anchor="center", relwidth=.86)
        self.hero_icon = DocumentMark(self.hero_body)
        self.hero_icon.pack(pady=(0, 17))
        self.hero_title = tk.StringVar(value="开始一项作业")
        self.hero_description = tk.StringVar(value="粘贴作业链接，打开后选择识别或填写")
        self.hero_title_label = ttk.Label(self.hero_body, textvariable=self.hero_title, style="Hero.TLabel")
        self.hero_title_label.pack()
        self.hero_hint = ttk.Label(self.hero_body, textvariable=self.hero_description, style="HeroHint.TLabel", anchor="center", justify="center")
        self.hero_hint.pack(pady=(10, 30))
        self.hero_actions = Backdrop(self.hero_body, self.hero)
        self.hero_actions.pack(fill="x")
        self.input_card = InputCard(self.hero_actions, self.vars["homework_url"])
        self.input_card.pack(fill="x")
        self.input_card.entry.bind("<Return>", lambda _: self.open())
        PrimaryButton(self.hero_actions, "打开作业并登录", self.open).pack(pady=(24, 0))
        self.steps = FlowSteps(self.hero_body)
        self.run_controls = ttk.Frame(self.hero_body)
        self.solve_current = ttk.Button(self.run_controls, text="填写当前题", command=lambda: self.solve(False))
        self.solve_batch = ttk.Button(self.run_controls, text="连续填写", command=lambda: self.solve(True))
        ttk.Button(self.run_controls, text="停止任务", command=lambda: self.send("homework_cancel")).pack(side="left", padx=8)
        ttk.Button(self.run_controls, text="恢复浏览器", command=lambda: self.send("restore")).pack(side="left", padx=8)
        self.return_button = ttk.Button(self.hero_body, text="返回输入", command=self.reset_idle)
        self.more_link = tk.Label(self.summary_page, text="更多操作与详情  ▾", bg=BG, fg="#777570",
                                  font=("Microsoft YaHei UI", 9), cursor="hand2")
        self.more_link.pack(side="bottom", pady=(0, 16))
        self.more_link.bind("<Button-1>", lambda _: self.toggle_details())
        self.detail_bar = ttk.Frame(self.summary_page)
        self.detail_buttons = ttk.Frame(self.detail_bar)
        self.detail_buttons.pack(fill="x", padx=18, pady=6)
        self.detail_kind = None
        self.current_question = None
        self.current_answer = None
        self.detail_selectors = {}
        self.detail_underlines = {}
        for label in ("题目内容", "识别结果", "模型说明", "参数设置"):
            button = tk.Label(self.detail_buttons, text=label, bg=BG, fg="#777570", padx=12, pady=12,
                              font=("Microsoft YaHei UI", 10), cursor="hand2", takefocus=1)
            button.pack(side="left", padx=4)
            button.bind("<Button-1>", lambda _, name=label: self.show_detail(name))
            button.bind("<Return>", lambda _, name=label: self.show_detail(name))
            self.detail_selectors[label] = button
            self.detail_underlines[label] = tk.Frame(button, bg="#252525", height=2)
        self.detail_content = ttk.Frame(self.detail_bar)
        self.detail_content.pack_propagate(False)
        self.detail_animation = 0
        self.detail_text = scrolledtext.ScrolledText(self.detail_content, height=7, wrap="word",
                                                    font=("Microsoft YaHei UI", 10), state="disabled",
                                                    relief="flat", borderwidth=0, highlightthickness=0,
                                                    background="#FCFBFA", foreground="#777570", padx=20, pady=18)
        self.detail_text.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        self.quick_settings = ttk.Frame(self.detail_content)
        ttk.Checkbutton(self.quick_settings, text="覆盖已有答案（默认保留）", variable=self.overwrite).pack(side="left", padx=12)
        ttk.Checkbutton(self.quick_settings, text="填写后自动提交本题", variable=self.auto_submit).pack(side="left", padx=12)
        details = ttk.Frame(self.workspace_switch)
        self.workspace_switch.add(details)
        self.details = details
        self.details_open = False
        self.last_stage = None
        self.workbench = Workbench(details, self)
        self.auto_submit.trace_add("write", self._update_submit_hint)
        self._update_submit_hint()
        self.model_heading = tk.StringVar(value=self.vars['model'].get() or '选择你的模型')
        self.model_feature = FeaturePage(model, self.model_heading, '选择并连接当前模型服务', '模型连接与设置', '连接参数与高级设置', icon='model')
        self.model_title_font = tkfont.Font(root=self.parent, family='Segoe UI Semibold', size=28, slant='roman')
        self.model_feature.title_label.configure(font=self.model_title_font, anchor='center')
        self.vars['model'].trace_add('write', self._update_model_heading)
        self._update_model_heading()
        PrimaryButton(self.model_feature.actions, text='配置模型', command=self.model_feature.open_details).pack()
        ttk.Label(self.model_feature.extra, textvariable=self.model_status, foreground='#777570', wraplength=600, justify='center').pack()
        self.model_scroll = ScrollableForm(self.model_feature.detail_body)
        model = self.model_scroll.body
        build_model_config(self, model)
        self.key.trace_add("write", self._key_edited)
        self.vars["base_url"].trace_add("write", self._sync_key)
        self._sync_key(force=True)

    def _update_model_heading(self, *_):
        name = self.vars['model'].get().strip()
        self.model_heading.set(name or '选择你的模型')
        latin = bool(name) and name.isascii()
        self.model_title_font.configure(family='Segoe UI Semibold' if latin else 'Microsoft YaHei UI',
                                        weight='normal' if latin else 'bold', slant='roman')

    def _current_endpoint(self):
        try:
            base = self.vars["base_url"].get().strip()
            return ModelSettings(base_url=base).validate().endpoint if base else None
        except ValueError:
            return None

    def _update_submit_hint(self, *_):
        self.submit_hint.set("已开启自动提交：逐题核对、提交，网页确认成功后再切题；安全核验会先自动处理。" if self.auto_submit.get() else
                             "未开启自动提交：答案填入后请在网页核对并提交。安全核验会先自动处理。")

    def _set_key(self, value, endpoint):
        self._updating_key = True
        try:
            self.key.set(value)
        finally:
            self._updating_key = False
        self._key_dirty = False
        self._key_owner_endpoint = endpoint

    def _key_edited(self, *_):
        if self._updating_key:
            return
        self._key_dirty = self.key.get() != KEY_MASK
        self._key_owner_endpoint = self._current_endpoint()
        self.key_status.set("新密钥尚未保存，点击“保存模型设置”后会保留。" if self.key.get().strip() else
                            "留空会复用此接口已保存的密钥；本机无认证模型可留空。")

    def _sync_key(self, *_, force=False):
        endpoint = self._current_endpoint()
        if not force and endpoint == self._key_endpoint:
            return
        # Support entering a new key before entering its URL. A key already
        # bound to an old endpoint must never be carried over to another one.
        unbound_input = self._key_dirty and self._key_owner_endpoint is None and self.key.get().strip()
        self._key_endpoint = endpoint
        if unbound_input:
            self.key_status.set("新密钥尚未保存，点击“保存模型设置”后会保留。")
            return
        self._set_key("", endpoint)
        if not endpoint:
            self.key_status.set("填写 API 地址后，会显示该接口已保存的密钥状态。")
            return
        try:
            saved = bool(KeyStore().load(endpoint))
        except (ValueError, OSError):
            self.key_status.set("此接口的已保存密钥无法读取，请重新填写并保存。")
            return
        if saved:
            self._set_key(KEY_MASK, endpoint)
            self.key_status.set("已保存 API Key（本机加密）；重启后自动使用，无需重复填写。")
        else:
            self.key_status.set("此接口尚未保存 API Key；本机无认证模型可留空。")

    def _select_saved_key(self, _event=None):
        if self.key.get() == KEY_MASK and not self._key_dirty:
            self.key_entry.after_idle(lambda: self.key_entry.selection_range(0, "end"))

    @staticmethod
    def replace_text(widget, content):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", content)
        widget.configure(state="disabled")

    def read_settings(self, require_model=False):
        return ModelSettings(
            base_url=self.vars["base_url"].get().strip(), model=self.vars["model"].get().strip(),
            timeout=int(self.vars["timeout"].get()), json_mode=self.vars["json_mode"].get(),
            homework_url=self.vars["homework_url"].get().strip(), instructions=self.vars["instructions"].get().strip(),
            exam_url=self.settings.exam_url,
            auto_submit=self.auto_submit.get(),
            review_summary=self.vars['review_summary'].get(),review_techniques=self.vars['review_techniques'].get(),
        ).validate(require_model)

    def save(self, require_model=False):
        try:
            settings = self.read_settings(require_model)
            self._sync_key()
            entered_key = self.key.get().strip()
            if entered_key and entered_key != KEY_MASK:
                if not settings.base_url:
                    raise ValueError("保存密钥前请先填写 API 地址。")
                KeyStore().save(settings.endpoint, entered_key)
                self._set_key(KEY_MASK, settings.endpoint)
            settings.save()
            self.settings = settings
            self._sync_key(force=True)
            self.model_status.set("模型设置已保存；密钥不会写入配置或日志。")
            return True
        except (ValueError, OSError) as exc:
            messagebox.showerror("设置错误", str(exc))
            return False

    def open(self):
        if not self.save() or not self.settings.homework_url:
            self.status.set("请先填写作业详情页地址。")
            return
        engine = self.get_engine(self.settings.homework_url)
        if engine:
            engine.command("homework_open", self.settings.homework_url)

    def solve(self, batch):
        if not self.save(require_model=True):
            return
        self.send("homework_solve", {"settings": self.settings, "batch": batch, "overwrite": self.overwrite.get(),
                                     "auto_submit": self.auto_submit.get()})

    def test(self):
        if self.test_future and not self.test_future.done():
            return
        if not self.save(require_model=True):
            return
        try:
            client = ModelClient(self.settings, KeyStore().load(self.settings.endpoint))
        except ValueError as exc:
            self.model_status.set(str(exc))
            return
        self.test_button.configure(state="disabled")
        self.model_status.set("正在测试模型连接…")
        self.test_future = self.executor.submit(client.test)
        self.parent.after(150, self.poll_test)

    def poll_test(self):
        if self.closed:
            return
        if not self.test_future.done():
            self.parent.after(150, self.poll_test)
            return
        self.test_button.configure(state="normal")
        try:
            self.test_future.result()
            self.model_status.set("连接成功，接口返回了有效文本。")
        except Exception as exc:
            self.model_status.set(str(exc) if isinstance(exc, ValueError) else "接口测试失败，请检查设置。")

    def render(self, data):
        self.status.set(data["message"])
        self._render_stage(data)
        if "question" in data:
            q = data["question"]
            self.current_question = q
            source = "（本机 OCR）" if q.get("text_source") == "windows-ocr" else ""
            content = f"题型：{TYPE_NAMES.get(q['type'], q['type'])}{source}\n\n{q['stem']}\n\n"
            content += "\n".join(f"{o['id']}. {o['text']}" for o in q["options"])
            self.replace_text(self.question, content)
        if "answer" in data:
            self.current_answer = data["answer"]
            self.replace_text(self.answer, json.dumps(data["answer"], ensure_ascii=False, indent=2))
        self.workbench.refresh()
        if self.detail_kind:
            self._fill_detail()

    def _draw_hero_background(self, event):
        self.hero_body.place_configure(relwidth=0, width=min(int(event.width * .86), 1000))
        self.hero_hint.configure(wraplength=max(280, min(event.width - 70, 850)))
        if self.background_job is not None:
            self.hero.after_cancel(self.background_job)
        self.background_job = self.hero.after(70, self._resize_background)

    def _resize_background(self):
        self.background_job = None
        width, height = self.hero.winfo_width(), self.hero.winfo_height()
        if width < 2 or height < 2:
            return
        self.hero.background_scaled = self.background_source.resize((width, height), Image.Resampling.LANCZOS)
        self.background_image = ImageTk.PhotoImage(self.hero.background_scaled, master=self.hero)
        self.hero.delete("ambient")
        self.hero.create_image(0, 0, image=self.background_image, anchor="nw", tags="ambient")
        self.hero.tag_lower("ambient")
        self.hero_body.refresh()
        self.hero_actions.refresh()

    def toggle_details(self):
        self.details_open = not self.details_open
        if self.details_open:
            self.detail_animation += 1
            self.detail_kind = None
            self.detail_content.pack_forget()
            self.detail_bar.pack_forget()
            self.hero_body.place_configure(rely=.44)
            self.workspace_switch.select(self.details)
        else:
            self.workspace_switch.select(self.summary_page)
            if self.last_stage:
                self.detail_bar.pack(side="bottom", fill="x")

    def show_detail(self, kind):
        if self.detail_kind == kind:
            self.detail_kind = None
            self._mark_detail()
            self._animate_detail(False)
            return
        was_open = self.detail_kind is not None
        self.detail_kind = kind
        self._mark_detail()
        self._fill_detail()
        if not was_open:
            self.detail_content.configure(height=0)
            self.detail_content.pack(fill="x")
            self._animate_detail(True)

    def _mark_detail(self):
        for name, button in self.detail_selectors.items():
            button.configure(fg="#252525" if name == self.detail_kind else "#777570",
                             font=("Microsoft YaHei UI", 10, "bold" if name == self.detail_kind else "normal"))
            if name == self.detail_kind:
                self.detail_underlines[name].place(relx=.1, rely=1, relwidth=.8, height=2, anchor="sw")
            else:
                self.detail_underlines[name].place_forget()

    def _animate_detail(self, opening):
        self.detail_animation += 1
        generation = self.detail_animation
        start = self.detail_content.winfo_height() if self.detail_content.winfo_manager() else 0
        end = 190 if opening else 0
        start_position = float(self.hero_body.place_info().get("rely", .44))
        end_position = .48
        def frame(step):
            if generation != self.detail_animation or not self.detail_bar.winfo_manager():
                return
            part = step / 6
            self.detail_content.configure(height=round(start + (end - start) * part))
            self.hero_body.place_configure(rely=start_position + (end_position - start_position) * part)
            if step < 6:
                self.parent.after(35, lambda: frame(step + 1))
            elif not opening:
                self.detail_content.pack_forget()
        frame(0)

    def _fill_detail(self):
        self.quick_settings.pack_forget()
        if self.detail_kind == "参数设置":
            self.detail_text.pack_forget()
            self.quick_settings.pack(fill="x", padx=15, pady=15)
            return
        self.detail_text.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        question = self.current_question or {}
        answer = self.current_answer or {}
        if self.detail_kind == "题目内容":
            content = question.get("stem", "尚未识别题目。")
            content += "\n\n" + "\n".join(f"{item['id']}. {item['text']}" for item in question.get("options", []))
        elif self.detail_kind == "识别结果":
            values = answer.get("option_ids") or answer.get("blank_answers") or answer.get("text")
            content = "答案：" + ("、".join(map(str, values)) if isinstance(values, list) else str(values or "尚无结果"))
        else:
            content = answer.get("explanation") or "暂无模型说明。"
        self.replace_text(self.detail_text, content)

    def reset_idle(self):
        self.detail_animation += 1
        self.last_stage = None
        self.hero_title.set("开始一项作业")
        self.hero_title_label.configure(font=("Microsoft YaHei UI", 31, "bold"))
        self.hero_icon.set_size(132)
        self.hero_icon.set_active(False)
        self.hero_hint.pack_configure(pady=(10, 30))
        self.hero_description.set("粘贴作业链接，打开后选择识别或填写")
        self.steps.pack_forget()
        self.run_controls.pack_forget()
        self.return_button.pack_forget()
        self.hero_icon.pack(before=self.hero_title_label, pady=(0, 12))
        self.hero_actions.pack(fill="x")
        self.detail_kind = None
        self.detail_content.pack_forget()
        self.detail_bar.pack_forget()
        self.hero_body.place_configure(rely=.44)
        self.steps.observed.clear()
        self.steps.use_model = False
        self._mark_detail()

    def _render_stage(self, data):
        state = data.get("state")
        names = {
            "opening": "打开作业", "waiting": "等待网页", "recognizing": "识别解析",
            "ready": "识别解析", "thinking": "模型求解", "filled": "填写核对",
            "submitting": "填写提交", "submitted": "提交已确认", "switching": "获取下一题",
            "complete": "任务完成", "stopped": "任务已停止", "error": "任务遇到问题",
            "blocked": "等待处理", "verification": "安全核验", "unsupported": "需要人工处理",
            "skipped": "已保留原答案", "exported": "题面已导出", "resumed": "继续作业",
        }
        if state not in names:
            return
        title = names[state]
        if state == "thinking" and "题目自带" in data.get("message", ""):
            title = "使用题目答案"
        self.last_stage = state
        self.hero_title.set(title)
        self.hero_title_label.configure(font=("Microsoft YaHei UI", 28, "bold"))
        self.hero_icon.set_size(92)
        self.hero_icon.set_active(state in {"opening", "recognizing", "thinking", "submitting", "switching", "resumed"})
        self.hero_hint.pack_configure(pady=(8, 16))
        self.hero_description.set(data.get("message", ""))
        self.hero_actions.pack_forget()
        self.hero_icon.pack(before=self.hero_title_label, pady=(0, 10))
        self.steps.update_state(state, embedded="题目自带" in data.get("message", ""))
        self.steps.pack(pady=(0, 24))
        self.run_controls.pack()
        self.solve_current.pack_forget()
        self.solve_batch.pack_forget()
        if state in {"ready", "filled", "skipped"}:
            self.solve_current.pack(side="left", padx=5)
            self.solve_batch.pack(side="left", padx=5)
        if state in {"complete", "stopped", "error"}:
            self.return_button.pack(pady=15)
        else:
            self.return_button.pack_forget()
        if not self.details_open and not self.detail_bar.winfo_manager():
            self.detail_bar.pack(side="bottom", fill="x")

    def close(self):
        self.closed = True
        self.steps.stop()
        self.hero_icon.set_active(False)
        if self.background_job is not None:
            self.hero.after_cancel(self.background_job)
        self.executor.shutdown(wait=False, cancel_futures=True)
