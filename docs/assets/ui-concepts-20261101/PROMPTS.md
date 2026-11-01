# UI 概念参考图 v1

日期：2026-11-01。使用内置 imagegen 工具生成；工具未暴露可选择的模型 ID，不能据此确认具体后端型号。两张图片是待用户确认的视觉参考，尚未集成到 GUI。原始参考未覆盖。

- `idle-v1.png`：空闲启动页，参考 `../ui-idle-reference.png`。
- `running-v1.png`：运行页与底部详情展开状态，参考本目录空闲页以保持一致。
- 悬停副标题、动态流程、响应式与动画是后续真实 GUI 的交互要求，静态图片仅展示其中一个状态。

## 空闲页生成提示词

```text
Use case: ui-mockup
Asset type: high fidelity Windows desktop application UI visual reference, idle launch state, one full app window only, landscape approximately 1536x1024.
Primary request: design a production-quality Chinese desktop interface for “雨课堂 · 学习助手”. Use the supplied image as the visual style and composition reference, NOT as an edit target. Preserve its polished warm-white minimalism, sophisticated spacing, restrained soft surfaces, black primary button and very subtle warm abstract background. Refine it into a coherent practical interface, with no perspective, no device mockup, no explanatory annotations.
Layout: integrated warm-white title bar with app mark, exact title “雨课堂 · 学习助手” and Windows minimize/maximize/close controls. Left sidebar about 190px wide; central workspace is spacious with one clear visual center. Sidebar items show a clean monochrome icon plus main title ONLY in their default state, no subtitles visible. Main navigation exact text: “作业填写”, “视频播放”, “模型设置”, “运行日志”; secondary entries below a subtle divider: “课程流程”, “记录与经验”, “期末考试”. Active 作业填写 has a quiet rounded warm-gray selection. Small collapse control, balanced density. The real interface will reveal subtitles on hover, but this image depicts the default state.
Central composition: a refined small document emblem on a softly rounded warm-white tile, optional tiny restrained amber sparkle like the supplied reference. Strong large Chinese title exactly “开始一项作业”. One smaller gray line exactly “粘贴作业链接，打开后选择识别或填写”. A generously wide rounded white link input with thin warm-gray border, very soft shadow, elegant link icon on left, placeholder exactly “粘贴雨课堂作业链接…”, discreet paste/clear utility icons on right. Below it a rounded nearly black primary button with play icon and exact label “打开作业并登录”. A very subtle text-only bottom entry “更多操作与详情”.
Visual system: background #F8F7F4, surface #FFFFFF, soft #F1F0EC, ink #252525, muted #777570. Excellent modern Chinese sans typography, decisive title/body contrast, main container corners around 14–18px with proportionate smaller corners. Tiny utility glyphs from a unified professionally designed icon family. Clean fine antialiasing, consistent baseline and optical spacing.
Constraints: no third settings column, no logs/questions/JSON on idle page, no promotional feature cards or four decorative badges, no blue/purple gradients, no glassmorphism, no neon or heavy shadows. Do not add dummy controls or promises of automatic submission. Text must be accurate Chinese, readable and sparse. This is a static visual target for a responsive interactive GUI, not a flattened image to be used as the whole app.
```

## 运行页生成提示词

```text
Use case: ui-mockup
Asset type: high fidelity Windows desktop app RUNNING STATE visual reference, one full window, landscape approximately 1536x1024.
Primary request: create the running-state companion of the supplied idle UI image for “雨课堂 · 学习助手”. Input image is a STYLE AND DESIGN-SYSTEM REFERENCE, not an edit target. Match its exact window proportions, integrated title bar, sidebar, warm-white material, typography family, refined icons, minimal shadows and warm abstract background. This must look like the same polished application on a different state.
Keep the title bar “雨课堂 · 学习助手”, Windows controls, left navigation with default main titles only: “作业填写”, “视频播放”, “模型设置”, “运行日志”, then secondary “课程流程”, “记录与经验”, “期末考试”. Keep active 作业填写 and add a tiny restrained running dot. No subtitle visible in default navigation state. Keep collapse control.
Running workspace: remove link input and launch button. The single main visual center is the current task stage, in large bold elegant Chinese text exactly “识别解析”. Above the title put a beautiful restrained recognition/document emblem, black linework on soft rounded warm surface; suggest gentle ongoing activity through a delicate partial progress arc or tiny subtle dots, not a neon spinner. One short gray line under title exactly “正在识别题目内容”. No numeric completion counts, no fake percent, no JSON.
Beneath main stage, a small understated horizontal four-step overview with exact labels “获取题目”, “识别解析”, “模型求解”, “填写提交”. Completed first step has subtle check, current second step softly emphasized, future stages pale gray. Delicate connecting rules, no big card per step. This is a sample task that includes all four stages; the real app will dynamically choose applicable stages.
Secondary controls below steps, quiet and compact: “停止任务” and “恢复浏览器”. No pause button since this task may not support pause.
Bottom details panel is EXPANDED, occupying roughly bottom quarter of central workspace. Central stage shifts upward and remains fully readable. Top of panel contains four low-salience detail selectors: “题目内容”, “识别结果”, “模型说明”, “参数设置”. Select “题目内容” with restrained dark text and slim underline, include a small collapse chevron on right. Under a hairline warm divider show a spacious very lightly rounded white details surface with a short gray empty-state line “题目内容将在识别完成后显示”. The panel feels integrated with the workspace, not a permanent third column. No question count, no distracting detailed text.
Visual system: warm white #F8F7F4, white #FFFFFF, soft #F1F0EC, ink #252525, muted #777570, tiny restrained state accent only. Strong title/body size hierarchy, breathing room, consistent optical alignment, professionally drawn unified monochrome icons, moderate radii and hairline borders.
Constraints: no blue-purple gradients, no glassmorphism, no heavy shadow, no flashy glow, no promotional badges or bottom feature cards, no permanently open right sidebar, no fake progress or results. Accurate readable sparse Chinese text. Static reference for a real responsive interactive GUI; show a calm shippable product UI, not a presentation, wireframe, or technical diagram.
```

