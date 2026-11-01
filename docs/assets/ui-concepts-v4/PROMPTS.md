# 第四版视觉资料

使用内置 imagegen 生成，具体后端型号未公开。用户已授权将同一设计语言扩展到全部页面。课程与视频图片是排版参考，GUI 的文字、控件、数据和状态都实时呈现。六类主图标以透明素材表保存到 `yuketang_helper/assets/ui-v4/emblems.png`，运行时按单元裁取并缩放，不调用图片 API。页面截图位于 `outputs/ui-v4/`，均使用空配置与临时模拟记录。

## 课程页提示词

```text
Use case: ui-mockup. Generate one polished Windows app window for 雨课堂·学习助手 COURSE WORKFLOW page, using supplied approved idle reference as exact warm-white visual design system. Single integrated title bar; sidebar order 课程流程(active), 作业填写, 视频播放, 模型设置, 运行日志; divider; 记录与经验, 期末考试. Main content has airy central composition, small refined course/book emblem on warm rounded tile, large Chinese headline “继续你的课程”, muted line “按目录衔接视频与作业，保留当前学习进度”. Under headline a quiet three-step sequence “视频学习 → 作业处理 → 下一项目” with thin warm connecting line, small selected point, no fake numbers. Black rounded main button “开始 / 继续课程”, secondary “暂停 / 恢复” and “停止流程”. Very small live-status line “等待进入课程目录”. Bottom subtle clickable entry “流程详情与设置” which will open a separate detailed workspace with a 180ms vertical slide transition. No densely packed instructions or technical prose in the main page. Very subtle warm abstract white ribbons from reference at edges, readable center. No neon, blue/purple gradients, marketing cards, JSON, third sidebar or fake progress. Production-ready typography with strong hierarchy, matching supplied reference. Chinese exact labels readable.
```

## 视频页提示词

```text
Use case: ui-mockup. One full-window VIDEO PLAYBACK page for Chinese Windows app 雨课堂 · 学习助手. Match supplied approved reference: warm white minimalist premium product UI, soft neutral surfaces, excellent Chinese type hierarchy, subtle edge ribbons, integrated title bar. Sidebar with 课程流程, 作业填写, 视频播放(active), 模型设置, 运行日志; divider; 记录与经验, 期末考试. Main spacious centered layout: small play emblem, large headline “开始一段学习”, gray line “粘贴课程链接，登录后继续视频学习”, large rounded course URL input placeholder “粘贴课程直达链接…”, black rounded button “启动并登录”. Beneath show compact quiet live playback status module: “尚未开始播放”, thin progress track and neutral time “00:00 / 00:00”, secondary actions “暂停 / 恢复”, “停止并关闭”. Keep other details hidden behind bottom subtle entry “播放设置与详情”. Real app will open settings in an animated detail workspace with same visual language. Do not add fake video title, fake percent, thumbnails, dense settings, question results, JSON, or third sidebar. Consistent corner radii, spacing and professional monochrome icons, no gradients blue, no promotional cards. This should feel like a sibling of the provided idle page.
```

## 图标素材表提示词

```text
Generate a production GUI sprite sheet: exactly SIX separate refined warm-white rounded-square tile emblems in a strict 3 columns by 2 rows grid, each centered inside an equal 512x512 cell, entire image 1536x1024. Genuine transparent background outside the six tiles, no text, no letters, no labels, no frame, no other objects. Each tile fills central 70% of its cell and leaves generous transparent margins so cells can be sliced without overlap. Unified black smooth rounded linework, ivory subtle surface, tiny restrained amber sparkle, softly shaded like the document emblem in supplied UI reference. TOP ROW left-to-right: open BOOK (course workflow), circular PLAY icon (video), CUBE (model settings). BOTTOM ROW left-to-right: minimal LIST/DOCUMENT (logs), open book with bookmark (learning records), document with small check (exam). All six matching visual weight and scale, no busy detail, polished neutral app icons, no blue/purple, no heavy 3D. Asset sheet only for extracting six bitmap GUI emblems.
```

