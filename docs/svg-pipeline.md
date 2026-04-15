# V2 SVG Pipeline

EduPPTX V2 使用 LLM 生成全页 SVG，再转换为 PowerPoint 原生形状。本文档记录管线架构、关键设计决策和技术细节。

## 管线总览

7 个阶段，4 次 LLM 调用 (每页 SVG 1 次 + review 1 次 = 2N+2 次总调用):

```
Phase 0  → Input Processing (文档解析, 联网搜索)
Phase 1a → Content Planning (LLM#1: 金字塔原理→大纲 JSON)
Phase 1b → Visual Planning  (LLM#2: 主题色+背景 prompt)
Phase 2  → Background Generation (Seedream AI)
Phase 2b → Materials Fetch (图片搜索/生成, debug 模式跳过)
Phase 3  → SVG Generation (N 次并行 LLM, Bento Grid 布局)
Phase 4  → Validate + LLM Review (自动修复→LLM 审阅→清理)
Phase 5  → SVG→DrawingML→PPTX (原生形状, 直接可编辑)
```

## Phase 1a: 内容规划

**文件**: `edupptx/planning/content_planner.py`

1 次 LLM 调用，输入主题+要求+背景资料，输出 `PlanningDraft` JSON:
- `meta`: 主题、受众、目的、总页数
- `pages[]`: 每页的 page_type, title, content_points, layout_hint, material_needs

使用金字塔原理（结论先行、以上统下、归类分组、逻辑递进）。

## Phase 1b: 视觉规划

**文件**: `edupptx/planning/visual_planner.py`

1 次 LLM 调用，基于 Phase 1a 的内容规划推荐统一视觉方案:
- `VisualPlan`: primary_color, secondary_color, accent_color, card_bg_color, text_color, heading_color
- `background_prompt`: 给 Seedream AI 的背景生成描述 (英文)

LLM 根据主题自动匹配配色：理科偏蓝绿，文科偏暖色，历史偏古色。

## Phase 3: SVG 生成

**文件**: `edupptx/design/svg_generator.py`, `edupptx/design/prompts.py`

N 次并行 LLM 调用 (max 4 workers)，每页生成一个完整 SVG (viewBox="0 0 1280 720")。

### Bento Grid 布局系统

卡片式模块化布局，11 种组合模式:

| 布局 | 适用场景 |
|------|---------|
| center_hero | 封面、标题页 |
| vertical_list | 目录、步骤序列 |
| bento_2col_equal | 对比、两方面分析 |
| bento_2col_asymmetric | 主次内容 (2:1) |
| bento_3col | 三要素并列 |
| hero_top_cards_bottom | 概述+细节 |
| cards_top_hero_bottom | 铺垫+结论 |
| mixed_grid | 复杂内容 |
| full_image | 全图展示 |
| timeline | 时间线、流程 |
| comparison | 正反对比 |

### SVG 约束

- 画布: viewBox="0 0 1280 720"
- 卡片区域: x∈[50,1230], y∈[110,660]
- 页面标题: x=50, y=50, font-size=28-32
- 副标题: x=50, y=90, font-size=14-16
- 页码: x=1220, y=700, font-size=12
- 安全字体: Noto Sans SC, 微软雅黑, Arial
- 禁止: foreignObject, CSS animation, JavaScript

### Debug 模式图片占位

`--debug` 模式下，图片位置用虚线矩形+描述文字标注:
```svg
<rect x="50" y="120" width="300" height="200" rx="8"
      fill="#F1F5F9" stroke="#94A3B8" stroke-width="1.5" stroke-dasharray="6,4"/>
<text x="200" y="225" text-anchor="middle" font-size="14"
      fill="#94A3B8">图片描述文字</text>
```

## Phase 4: 验证 + LLM 审阅

### 自动验证 (`svg_validator.py`)

按顺序执行:
1. 转义未编码的 `&` → `&amp;`
2. 修复 viewBox 为 "0 0 1280 720"
3. 移除 `<foreignObject>`
4. 清除 CSS animation/transition
5. 注入安全字体 (Noto Sans SC)
6. 长文本自动 tspan 换行 (22 字/行)
7. 坐标越界钳制 (x≥40, y∈[0,720])
8. 列内文字重叠检测修复
9. 图片 href 有效性检查

### LLM 审阅 (`svg_reviewer.py`)

将 SVG 代码 + validator warnings 发给 LLM 审阅:
- 检查文字溢出/重叠
- 检查配色是否符合 VisualPlan 主题色
- 检查布局平衡和内容完整性
- 输出修正后的完整 SVG

temperature=0.3 保证修正稳定。

## Phase 5: SVG→DrawingML→PPTX

**文件**: `edupptx/output/svg_to_shapes.py`, `edupptx/output/pptx_assembler.py`

### 核心转换: SVG→原生形状

逐元素解析 SVG，转为 PowerPoint DrawingML XML:

| SVG 元素 | DrawingML 形状 |
|----------|---------------|
| `<rect>` | `<p:sp>` + roundRect (rx/ry→avLst) |
| `<text>` + `<tspan>` | `<p:sp txBox="1">` 多段落文本框 |
| `<circle>`, `<ellipse>` | `<p:sp>` + ellipse |
| `<path>` | `<p:sp>` + custGeom (M/L/C/Z→moveTo/lnTo/cubicBezTo) |
| `<line>` | `<p:sp>` + custGeom |
| `<image>` (base64) | `<p:pic>` + blip |
| `<g>` | 递归展开子元素 (translate/scale 累积) |
| `<use href="#id">` | 内联引用 defs 中的元素 |
| linearGradient | `<a:gradFill>` + `<a:lin>` |
| radialGradient | `<a:gradFill>` + `<a:path>` |
| feDropShadow | `<a:outerShdw>` |

坐标转换: **1 SVG px = 9525 EMU** (96 DPI)

### 文本框处理

- 单行 `<text>`: `wrap="none"` + `<a:spAutoFit/>`
- 多行 `<text>` + `<tspan>`: `wrap="square"` + 固定宽度 (从 tspan x 推断卡片边界)
- 行距: 基于 tspan dy 值设置 `<a:lnSpc>/<a:spcPts>`
- CJK 字体: 自动分离 latin/ea 字体，Noto Sans SC 优先

### PPTX 打包

ZIP 后处理方式:
1. python-pptx 创建 base.pptx (空白 slide 占位)
2. 解压 ZIP
3. 替换每页 slide XML + rels + 写入 media
4. 更新 Content_Types.xml
5. 重新打包 ZIP

三种模式: native shapes (默认) | SVG+PNG embed (--embed) | svg2pptx (legacy)

## Debug 模式

`uv run edupptx gen "主题" --debug`

- 跳过: 素材图片获取 (Phase 2b)
- 保留: 所有 LLM 调用 (规划/视觉/SVG/review) + 背景生成
- 用途: 快速迭代布局质量，无需等待图片 API

## 自检审查流程

```bash
# SVG 直接渲染为 PNG (不绕 PDF)
uv run python3 -c "
import cairosvg
cairosvg.svg2png(url='output/session_xxx/slides/slide_01.svg',
                 write_to='/tmp/review/s01.png',
                 output_width=1920, output_height=1080)
"
```
