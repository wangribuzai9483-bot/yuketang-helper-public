"""Connection form retaining HomeworkPanel's save, key and test behavior."""
from tkinter import ttk
from .page_components import Disclosure
from .ui_shell import MUTED


def build_model_config(panel, parent):
    form = ttk.Frame(parent)
    form.pack(fill='x')
    form.columnconfigure(1, weight=1)
    for row, (key, title) in enumerate((('base_url','API Base URL'),('model','模型名称'))):
        ttk.Label(form, text=title).grid(row=row, column=0, sticky='w', padx=(0, 18), pady=10)
        ttk.Entry(form, textvariable=panel.vars[key]).grid(row=row, column=1, sticky='ew', pady=10)
    ttk.Label(form, text='API Key').grid(row=2, column=0, sticky='w', pady=10)
    panel.key_entry = ttk.Entry(form, textvariable=panel.key, show='●')
    panel.key_entry.grid(row=2, column=1, sticky='ew', pady=10)
    panel.key_entry.bind('<FocusIn>', panel._select_saved_key)
    ttk.Label(parent, textvariable=panel.key_status, foreground=MUTED, wraplength=650).pack(anchor='w', pady=8)
    controls = ttk.Frame(parent)
    controls.pack(fill='x', pady=16)
    ttk.Button(controls, text='保存模型设置', command=panel.save, style='Primary.TButton').pack(side='left', padx=(0, 12))
    panel.test_button = ttk.Button(controls, text='测试连接', command=panel.test)
    panel.test_button.pack(side='left')
    ttk.Label(parent, textvariable=panel.model_status, foreground=MUTED, wraplength=650).pack(anchor='w', pady=10)
    advanced = Disclosure(parent, '高级连接与输出参数', height=185)
    advanced.pack(fill='x', pady=5)
    body = advanced.body
    row = ttk.Frame(body)
    row.pack(fill='x', pady=8)
    ttk.Label(row, text='超时（秒）').pack(side='left', padx=(10, 12))
    ttk.Entry(row, textvariable=panel.vars['timeout'], width=10).pack(side='left')
    ttk.Checkbutton(body, text='使用 JSON 模式（接口须支持 response_format）', variable=panel.vars['json_mode']).pack(anchor='w', padx=10, pady=8)
    ttk.Label(body, text='答题要求', foreground=MUTED).pack(anchor='w', padx=10)
    ttk.Entry(body, textvariable=panel.vars['instructions']).pack(fill='x', padx=10, pady=8)
    review = Disclosure(parent, '额外学习复盘（可选）', height=150)
    review.pack(fill='x', pady=5)
    ttk.Checkbutton(review.body, text='作业结束后生成知识总结', variable=panel.vars['review_summary']).pack(anchor='w', padx=10, pady=8)
    ttk.Checkbutton(review.body, text='另外生成适用的做题技巧', variable=panel.vars['review_techniques']).pack(anchor='w', padx=10, pady=8)
    ttk.Label(review.body, text='额外调用当前模型并消耗 API 额度；默认关闭。保存密钥不会触发复盘。', foreground=MUTED, wraplength=620).pack(anchor='w', padx=10, pady=8)
    ttk.Label(parent, text='接口使用 Chat Completions 格式，可填 /v1 或完整 /chat/completions 地址。',
              foreground=MUTED, wraplength=650).pack(anchor='w', pady=12)
