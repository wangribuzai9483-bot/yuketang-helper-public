# 工作台视觉参考 v3

2026-11-01，内置 imagegen 生成。`workbench-reference.png` 为设计参考，实际可操作视图在 `yuketang_helper/workbench_ui.py`。按钮、文本、参数与结果由真实 GUI 实现，不把参考图铺作操作页面。原任务命令与设置复用 `HomeworkPanel`。

## 生成提示词

```text
Use case: ui-mockup. High fidelity Windows desktop app operations/details page for 雨课堂 · 学习助手. Use supplied image as visual style reference: polished warm white, precise Chinese sans typography, restrained rounded surfaces, soft shadow, black primary actions. Generate one entire app window landscape, no annotations.
Top single integrated warm-white title bar with exact app title and Windows controls. No duplicate title rows.
Sidebar main group IN THIS ORDER: 课程流程, 作业填写 (active), 视频播放, 模型设置, 运行日志; divider BELOW these five; secondary group 记录与经验, 期末考试. Default subtitles hidden.
Central page is an elegant practical advanced operations/details workspace, NOT the idle hero. Header large “作业工作台”, small gray “操作、识别结果与任务参数”. Compact “返回流程” link at upper right.
Near top a rounded link field labeled “作业链接”, placeholder “粘贴雨课堂作业链接…”. Then a compact horizontal toolbar: dark primary “打开并登录”, secondary “识别当前题”, “填写当前题”, “连续填写”; small overflow “更多操作” holds export and mode switch. Do not make full-width button grid.
Below a discreet warm-gray task options strip with switches “覆盖已有答案” and “填写后自动提交”, compact window actions “恢复浏览器”, “停止任务”. Subtle inline status “等待识别题目”, no long technical text.
Main content is a spacious integrated white rounded panel with tabs “题目内容”, “识别结果”, “模型说明”, “参数设置”; selected 题目内容. Quiet empty state centered with thin gray document icon, line “识别后将在这里显示题目内容”. Bottom one small helper line. Strong heading/body hierarchy, all controls clear and usable. No dense form grid, no large JSON, no fake progress or results, no blue gradients, no neon, no marketing cards.
Keep real-function layout plausible for native GUI implementation. Prioritize polished spacing and visual calm, matching supplied design.
```

