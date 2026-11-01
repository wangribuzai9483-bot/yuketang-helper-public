# UI v2 素材来源

2026-11-01。用户确认空闲页和运行页视觉后，使用内置 imagegen 生成以下素材。内置工具未公开可选择的模型 ID；不声称使用了某个确定后端型号。

- `workspace.png`：暖白抽象工作区背景，不含 UI 或文字。
- `document.png`：保留 alpha 的主视觉图标；GUI 按尺寸缩放。
- 参考：`docs/assets/ui-concepts-20261101/idle-v1.png`。
- GUI 在开发时生成素材，启动后离线加载，不调用图片 API。Pillow 用于 GUI 中的高质量缩放、背景对齐和 Tk 图片加载。
- 实际界面截图：`outputs/ui-v2/`；截图使用临时模拟数据与空配置，不读取真实课程或密钥。

## 背景提示词

```text
Extract and redesign ONLY the warm-white abstract WORKSPACE BACKGROUND from the supplied approved UI reference, for use as a real desktop app background asset. Landscape 1536x1024 composition. No UI whatsoever: no window border, no title bar, no sidebar, no icons, no document emblem, no text, no controls, no shadows from missing controls. Base almost white ivory #FDFCFB with extremely subtle warm silk/paper-like broad flowing abstract shapes entering from far left and top-right and bottom-right. Keep the center 65% virtually uniform and empty to support legible live GUI text. Preserve reference's refined minimal warm-white material, very low contrast, no visible noise, no hard geometric outlines, no blue or purple, no dramatic gradient. Background asset only.
```

## 主视觉提示词

```text
Create a single transparent PNG GUI asset based on the supplied approved interface reference. Extract/recreate ONLY the main central document emblem: refined rounded warm ivory square tile, black outline paper document with folded corner and three thick rounded lines, tiny amber four-point sparkle slightly outside top-right. Match its exact refined aesthetic, proportions, gentle surface and subtle shadow. Object fills about 85% of square image, centered. Genuine transparent alpha background outside the tile and sparkle, no checkerboard, no lettering, no app window, no surrounding UI, no other elements. Keep crisp professional icon edges and restrained lighting. This is a raster asset for an interactive GUI, not a full screenshot.
```

