# V3 Design Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Elevate SVG generation quality through richer prompt knowledge density, a formal design system (7 colors, 6-level typography, spacing spec), 5 educational page types, 5 chart templates, and expanded icon library — all without changing the 5-phase pipeline architecture.

**Architecture:** Extract hardcoded prompt strings from `design/prompts.py` into modular markdown reference files under `design/references/`. The Python assembler reads these files and composes them with VisualPlan data at runtime. New `content_density` field drives lecture/review mode selection.

**Tech Stack:** Python 3.10+, Pydantic models, markdown reference files, Lucide SVG icons (MIT)

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `edupptx/design/references/design-base.md` | 7 色系统 + 6 级字号 + 间距 + CRAP 原则 + 教育设计原则 |
| Create | `edupptx/design/references/shared-standards.md` | SVG 禁用特性黑名单 + PPT 兼容替代方案 |
| Create | `edupptx/design/references/executor-lecture.md` | 课堂讲授模式规范 |
| Create | `edupptx/design/references/executor-review.md` | 复习归纳模式规范 |
| Create | `edupptx/design/references/page-types.md` | 5 种教育页面类型定义 + SVG 示例 |
| Create | `edupptx/design/chart_templates/bar_chart.svg` | 柱状图参考模板 |
| Create | `edupptx/design/chart_templates/line_chart.svg` | 折线图参考模板 |
| Create | `edupptx/design/chart_templates/pie_chart.svg` | 饼图参考模板 |
| Create | `edupptx/design/chart_templates/kpi_cards.svg` | KPI 卡片参考模板 |
| Create | `edupptx/design/chart_templates/timeline.svg` | 时间线参考模板 |
| Modify | `edupptx/models.py:27-30,88-98` | 扩展 PageType + VisualPlan 字段 |
| Modify | `edupptx/planning/visual_planner.py:14-46` | 更新 prompt 输出新字段 |
| Modify | `edupptx/planning/prompts.py:27-109` | 扩展页面类型说明 |
| Rewrite | `edupptx/design/prompts.py` | 从硬编码改为 reference 文件组装 |
| Modify | `edupptx/design/svg_generator.py:112` | 传递 content_density 给 prompt 构建 |
| Add files | `assets/icons/*.svg` | 新增 ~140 个 Lucide 图标 SVG |
| Modify | `README.md:204` | 添加设计参考 Acknowledgments |
| Modify | `CLAUDE.md:54-90` | 更新目录结构 |

---

### Task 1: 扩展数据模型 — PageType + VisualPlan

**Files:**
- Modify: `edupptx/models.py:27-30,88-98`
- Test: `tests/test_models.py`

- [ ] **Step 1: 更新 PageType 和 VisualPlan**

```python
# edupptx/models.py — 修改 PageType (line 27-30)
PageType = Literal[
    "cover", "toc", "section", "content", "data", "case", "closing",
    "timeline", "comparison", "exercise", "summary",
    "quiz", "formula", "experiment",
]

# edupptx/models.py — 修改 VisualPlan (line 88-98)
class VisualPlan(BaseModel):
    """Phase 1b output: LLM-recommended visual design for the entire deck."""

    primary_color: str = Field(default="#1E40AF", description="主色 hex")
    secondary_color: str = Field(default="#3B82F6", description="辅色 hex")
    accent_color: str = Field(default="#F59E0B", description="强调色 hex")
    background_prompt: str = Field(default="", description="Seedream 背景生成 prompt")
    card_bg_color: str = Field(default="#FFFFFF", description="卡片背景色")
    secondary_bg_color: str = Field(default="#F8FAFC", description="次背景色")
    text_color: str = Field(default="#1E293B", description="正文色")
    heading_color: str = Field(default="#0F172A", description="标题色")
    content_density: Literal["lecture", "review"] = Field(
        default="lecture", description="内容密度模式"
    )
```

- [ ] **Step 2: 运行现有测试确认不破坏**

Run: `uv run pytest tests/test_models.py -v`

Expected: 全部 PASS（新字段有默认值，向后兼容）

- [ ] **Step 3: 添加新字段测试**

在 `tests/test_models.py` 的 `TestVisualPlan` 中追加：

```python
def test_visual_plan_new_fields():
    vp = VisualPlan(
        primary_color="#1E40AF",
        secondary_bg_color="#F1F5F9",
        content_density="review",
    )
    assert vp.secondary_bg_color == "#F1F5F9"
    assert vp.content_density == "review"


def test_visual_plan_defaults_backward_compatible():
    vp = VisualPlan()
    assert vp.secondary_bg_color == "#F8FAFC"
    assert vp.content_density == "lecture"
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/test_models.py -v`

Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add edupptx/models.py tests/test_models.py
git commit -m "✨【功能】：V3 #1 — 扩展 PageType + VisualPlan 数据模型"
```

---

### Task 2: 更新 VisualPlan LLM prompt

**Files:**
- Modify: `edupptx/planning/visual_planner.py:14-46`

- [ ] **Step 1: 更新 _SYSTEM_PROMPT 输出新字段**

替换 `edupptx/planning/visual_planner.py` 中 `_SYSTEM_PROMPT` 的 JSON 示例部分：

```python
_SYSTEM_PROMPT = """\
你是一位教育演示文稿的视觉设计顾问。根据 PPT 的主题和内容结构，推荐一套统一的视觉方案。

## 输出要求

输出一个 JSON 对象（用 ```json 包裹），包含以下字段：

```json
{
  "primary_color": "#hex — 主色，用于标题栏装饰条、重要元素",
  "secondary_color": "#hex — 辅色，用于次级标题、图标填充",
  "accent_color": "#hex — 强调色，仅用于关键数据（全局≤3处）",
  "background_prompt": "英文，用于 AI 生图的背景描述，抽象纹理/渐变，16:9，淡色调",
  "card_bg_color": "#hex — 卡片背景色",
  "secondary_bg_color": "#hex — 次背景色，用于区域分隔、交替行、引用区块",
  "text_color": "#hex — 正文文字颜色",
  "heading_color": "#hex — 标题文字颜色",
  "content_density": "lecture 或 review"
}
```

## 配色原则

1. **教育场景优先**：颜色清晰易读，不要花哨
2. **主色决定气质**：理科偏蓝绿，文科偏暖色，综合偏灰蓝
3. **对比度充足**：text_color 和 card_bg_color 的对比度 ≥ 4.5:1
4. **背景要淡**：background_prompt 生成的图应是淡色抽象纹理，不抢内容焦点
5. **强调色慎用**：accent_color 只用于关键数据/按钮，与主色有明显区分
6. **次背景色**：secondary_bg_color 应比 card_bg_color 略深一点（如 #F8FAFC vs #FFFFFF），用于区域分隔
7. **色彩比例**：主色 60% / 辅色 30% / 强调色 10%

## 内容密度判断

根据用户需求和主题特点选择：
- **lecture**（课堂讲授）：大字、宽松留白、适合投影，正文 24px 基准
- **review**（复习归纳）：信息密集、小字紧凑、适合打印/平板，正文 18px 基准
- 如果用户提到"课件""课堂""讲课""教学" → lecture
- 如果用户提到"复习""总结""归纳""打印""知识点" → review
- 默认 → lecture

## background_prompt 示例

- "Subtle abstract geometric pattern, soft blue gradient, minimalist, light background, 16:9 aspect ratio"
- "Elegant soft green watercolor texture, gentle flowing shapes, light and airy, presentation background"
- "Clean minimal tech grid pattern, very light gray and blue, professional, 1920x1080"
"""
```

- [ ] **Step 2: 验证模块加载**

Run: `uv run python3 -c "from edupptx.planning.visual_planner import generate_visual_plan; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add edupptx/planning/visual_planner.py
git commit -m "✨【功能】：V3 #2 — VisualPlan LLM prompt 输出 secondary_bg + content_density"
```

---

### Task 3: 扩展策划稿页面类型说明

**Files:**
- Modify: `edupptx/planning/prompts.py:27-109`

- [ ] **Step 1: 更新 _SYSTEM_PROMPT_TEMPLATE 中的页面类型列表**

在 `edupptx/planning/prompts.py` 的 `## 页面类型` 区域追加 5 种教育类型：

```python
# 在现有的 "summary" 行后面追加：
# - `quiz`: 练习检测页 — 题目 + 选项（A/B/C/D），适用于课堂互动和随堂检测
# - `formula`: 公式推导页 — 步骤式推理，序号→公式→说明，适用于数学/物理/化学
# - `experiment`: 实验步骤页 — 左侧器材，右侧步骤+现象+结论，适用于理科实验
# - `comparison`: 对比表格页 — 表头+交替行，适用于概念对比分析
# 注意：`comparison` 和 `summary` 的 PageType 已存在，无需重复声明
```

同时更新 `## 布局意图 (layout_hint)` 部分和约束中的内容密度说明。

- [ ] **Step 2: 验证模块加载**

Run: `uv run python3 -c "from edupptx.planning.prompts import build_planning_system_prompt; print(len(build_planning_system_prompt()), 'chars')"`

Expected: 输出字符数（应比之前增大）

- [ ] **Step 3: Commit**

```bash
git add edupptx/planning/prompts.py
git commit -m "✨【功能】：V3 #3 — 策划稿 prompt 增加 5 种教育页面类型"
```

---

### Task 4: 创建 design reference 文件 — design-base.md

**Files:**
- Create: `edupptx/design/references/design-base.md`

- [ ] **Step 1: 创建 references 目录**

```bash
mkdir -p edupptx/design/references
```

- [ ] **Step 2: 编写 design-base.md**

内容覆盖：
1. 角色定义（"你是教育演示文稿的 SVG 设计专家"）
2. 7 色系统规范（使用 `{placeholder}` 占位，运行时替换为 VisualPlan 值）
3. 6 级字号体系（用倍率定义，根据 content_density 自动选择 24px 或 18px 基准）
4. 间距规范（卡片间距 20px、内边距 24px、圆角 12-16px 等）
5. CRAP 四原则（Contrast/Repetition/Alignment/Proximity，每条 1-2 句）
6. 教育专属设计原则（结论先行但展开推导、信息密度控制、视觉节奏、颜色语义）
7. Bento Grid 布局系统（从现有 BENTO_GRID_SPEC 迁移并增强）
8. 坐标规则和自检清单

文件约 200-300 行，是最核心的设计知识文档。

- [ ] **Step 3: Commit**

```bash
git add edupptx/design/references/design-base.md
git commit -m "✨【功能】：V3 #4 — design-base.md 核心设计规范"
```

---

### Task 5: 创建 design reference 文件 — shared-standards.md

**Files:**
- Create: `edupptx/design/references/shared-standards.md`

- [ ] **Step 1: 编写 shared-standards.md**

从现有 `SVG_CONSTRAINTS` 字符串迁移并增强，内容覆盖：
1. SVG 禁用特性黑名单（表格形式列出每个禁用项和原因）
2. PPT 兼容替代方案（rgba→fill-opacity, group opacity→逐元素, marker→polygon）
3. 文字规范（font-family 完整列表、tspan 用法、行间距 dy 值）
4. 图片规范（href、preserveAspectRatio、clipPath 用法）
5. 渐变和装饰规范
6. 输出格式要求（```svg 包裹，不附加解释文字）

文件约 80-120 行。

- [ ] **Step 2: Commit**

```bash
git add edupptx/design/references/shared-standards.md
git commit -m "✨【功能】：V3 #5 — shared-standards.md SVG 技术约束"
```

---

### Task 6: 创建 executor 风格文件

**Files:**
- Create: `edupptx/design/references/executor-lecture.md`
- Create: `edupptx/design/references/executor-review.md`

- [ ] **Step 1: 编写 executor-lecture.md**

课堂讲授模式规范（约 60-80 行）：
1. 模式定义和目标场景（课堂投影，远距离观看）
2. 字号具体值（60/36-48/29/24/18/13 px）
3. 每页要点数限制（3-5 个）
4. 卡片高度参考（单行 530-600px）
5. 视觉特点（大留白、视觉冲击、图文 6:4 分配）
6. 特殊布局技巧（封面居中大卡、数据页 KPI 大字）

- [ ] **Step 2: 编写 executor-review.md**

复习归纳模式规范（约 60-80 行）：
1. 模式定义和目标场景（复习资料、打印讲义、平板阅读）
2. 字号具体值（45/27-36/22/18/14/10 px）
3. 每页要点数限制（6-8 个）
4. 卡片高度参考（紧凑，充分利用画布）
5. 视觉特点（信息密集、分类清晰、结构化列表）
6. 特殊布局技巧（summary 分类卡片、comparison 交替行表格）

- [ ] **Step 3: Commit**

```bash
git add edupptx/design/references/executor-lecture.md edupptx/design/references/executor-review.md
git commit -m "✨【功能】：V3 #6 — executor-lecture/review 两种密度模式"
```

---

### Task 7: 创建 page-types.md

**Files:**
- Create: `edupptx/design/references/page-types.md`

- [ ] **Step 1: 编写 page-types.md**

5 种教育页面类型，每种包含（约 200-250 行总计）：
1. **quiz** — 布局描述 + 坐标 + SVG 示例（题目大卡 + 2x2 选项卡）
2. **formula** — 布局描述 + 坐标 + SVG 示例（步骤卡片纵向 + 箭头连接）
3. **experiment** — 布局描述 + 坐标 + SVG 示例（左窄右宽 3:7）
4. **comparison** — 布局描述 + 坐标 + SVG 示例（表头行 + 交替行）
5. **summary** — 布局描述 + 坐标 + SVG 示例（分类卡片 + 警示卡片）

每种给出完整的 30-40 行 SVG 代码示例，使用 `{primary_color}` 等占位符。

- [ ] **Step 2: Commit**

```bash
git add edupptx/design/references/page-types.md
git commit -m "✨【功能】：V3 #7 — page-types.md 5 种教育页面类型定义"
```

---

### Task 8: 创建 5 种图表 SVG 模板

**Files:**
- Create: `edupptx/design/chart_templates/bar_chart.svg`
- Create: `edupptx/design/chart_templates/line_chart.svg`
- Create: `edupptx/design/chart_templates/pie_chart.svg`
- Create: `edupptx/design/chart_templates/kpi_cards.svg`
- Create: `edupptx/design/chart_templates/timeline.svg`

- [ ] **Step 1: 创建目录**

```bash
mkdir -p edupptx/design/chart_templates
```

- [ ] **Step 2: 编写 bar_chart.svg**

带标注的柱状图参考（viewBox="0 0 600 400"）：坐标轴 + 4-6 根柱子 + 数据标签 + 图例。使用占位色 `#4A90D9` 等中性色。

- [ ] **Step 3: 编写 line_chart.svg**

折线图参考：坐标轴 + polyline + 圆形数据点 + 网格线。

- [ ] **Step 4: 编写 pie_chart.svg**

饼图/环形图参考：3-5 个扇区用 `<path>` arc 绘制 + 百分比标签。

- [ ] **Step 5: 编写 kpi_cards.svg**

KPI 卡片参考：2x2 布局，每个卡片含大数字(48px) + 指标名(14px) + 趋势箭头。

- [ ] **Step 6: 编写 timeline.svg**

时间线参考：横向线条 + 节点圆 + 事件卡片。

- [ ] **Step 7: Commit**

```bash
git add edupptx/design/chart_templates/
git commit -m "✨【功能】：V3 #8 — 5 种图表 SVG 参考模板"
```

---

### Task 9: 扩充图标库

**Files:**
- Add: `assets/icons/*.svg` (~140 个新图标)

- [ ] **Step 1: 从 Lucide Icons 下载新图标**

使用脚本从 Lucide GitHub 仓库下载 SVG 文件到 `assets/icons/`。目标图标列表：

**教育类** (~30): graduation-cap, school, pencil, notebook, clipboard, file-text, presentation, lightbulb-off, brain, dna, flask, beaker, magnet, thermometer, globe-2, map, compass, ruler, protractor, eraser, palette, music, headphones, video, film, camera-off, megaphone, trophy, medal, badge

**数据类** (~15): chart-pie, chart-area, bar-chart-2, trending-down, percent, hash, database, table, table-2, kanban, pie-chart, activity, gauge, signal, wifi

**交互类** (~15): check-circle-2, x-circle, alert-triangle, alert-circle, info, help-circle, thumbs-up, thumbs-down, hand, pointer, mouse-pointer, touch, scan, qr-code, loader

**箭头类** (~15): arrow-up-right, arrow-down-right, corner-down-right, corner-up-right, move, repeat, refresh-cw, undo, redo, shuffle, rotate-cw, maximize, minimize, expand, shrink

**科学类** (~20): atom, zap, wind, droplet, sun, moon, cloud, snowflake, flame, leaf, sprout, flower, tree, mountain, waves, orbit, satellite, telescope, microscope, test-tube

**通用补充** (~20): folder, folder-open, link, external-link, copy, clipboard-copy, share, filter, sort-asc, sort-desc, search, zoom-in, zoom-out, tag, tags, pin, map-pin, navigation, layout, grid

（实际下载时按 Lucide 官方仓库中的确切文件名匹配）

- [ ] **Step 2: 验证图标数量**

Run: `ls assets/icons/*.svg | wc -l`

Expected: ≥ 240

- [ ] **Step 3: 验证代码自动识别新图标**

Run: `uv run python3 -c "from edupptx.materials.icons import list_icons; print(len(list_icons()), 'icons')"`

Expected: ≥ 240 icons（`list_icons()` 扫描 `assets/icons/` 目录，自动识别新文件）

- [ ] **Step 4: Commit**

```bash
git add assets/icons/
git commit -m "✨【功能】：V3 #9 — 图标库扩充至 250+"
```

---

### Task 10: 重构 prompts.py — reference 文件组装器

**Files:**
- Rewrite: `edupptx/design/prompts.py`
- Modify: `edupptx/design/svg_generator.py:112`

这是核心改动。将硬编码的 `BENTO_GRID_SPEC` 和 `SVG_CONSTRAINTS` 字符串替换为从 `.md` 文件动态加载的组装器。

- [ ] **Step 1: 重写 build_svg_system_prompt()**

```python
# edupptx/design/prompts.py — 新版组装器

"""SVG 生成的提示词工程 — V3 reference 文件组装。"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Literal

from edupptx.models import PagePlan, SlideAssets, VisualPlan

_REFS_DIR = Path(__file__).parent / "references"


def _load_ref(name: str) -> str:
    """Load a reference markdown file."""
    path = _REFS_DIR / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _build_color_spec(vp: VisualPlan) -> str:
    """Build color specification block from VisualPlan."""
    return f"""
## 统一配色方案（必须严格遵守）

本套幻灯片使用以下统一配色，所有页面必须一致：

- **主色 (primary)**: {vp.primary_color} — 标题栏装饰条、重要元素
- **辅色 (secondary)**: {vp.secondary_color} — 次级标题、图标填充
- **强调色 (accent)**: {vp.accent_color} — 关键数据、重点标注（慎用）
- **卡片背景**: {vp.card_bg_color}
- **次背景**: {vp.secondary_bg_color} — 区域分隔、交替行背景
- **正文色**: {vp.text_color}
- **标题色**: {vp.heading_color}

**严格要求**：
- 卡片 `<rect>` fill 使用 `{vp.card_bg_color}`
- 交替行/引用区块 fill 使用 `{vp.secondary_bg_color}`
- 正文 `<text>` fill 使用 `{vp.text_color}`
- 页面标题 fill 使用 `{vp.heading_color}`
- 装饰条/图标使用 `{vp.primary_color}` 或 `{vp.secondary_color}`
- 不要自行发明其他颜色
"""


def build_svg_system_prompt(
    style_guide: str,
    visual_plan: VisualPlan | None = None,
    content_density: Literal["lecture", "review"] = "lecture",
) -> str:
    """构建 SVG 生成的系统提示词。

    从 design/references/ 读取 markdown 文件并组装。
    """
    parts: list[str] = []

    # 1. 公共设计规范
    parts.append(_load_ref("design-base.md"))

    # 2. SVG 技术约束
    parts.append(_load_ref("shared-standards.md"))

    # 3. 密度模式
    if content_density == "review":
        parts.append(_load_ref("executor-review.md"))
    else:
        parts.append(_load_ref("executor-lecture.md"))

    # 4. 教育页面类型
    parts.append(_load_ref("page-types.md"))

    # 5. 配色方案
    if visual_plan:
        parts.append(_build_color_spec(visual_plan))

    # 6. 风格模板
    if style_guide:
        parts.append(f"\n## 风格指南\n\n{style_guide}")

    return "\n\n".join(p for p in parts if p.strip())
```

保留 `_compress_and_encode()`、`build_svg_user_prompt()` 函数不变。删除 `BENTO_GRID_SPEC` 和 `SVG_CONSTRAINTS` 常量（已迁移到 .md 文件）。

- [ ] **Step 2: 更新 build_svg_user_prompt 中的 type_hints**

更新 `build_svg_user_prompt()` 中的 `type_hints` dict，添加 5 种新页面类型的提示：

```python
type_hints = {
    # ... 保留现有 cover/toc/section/closing/data/case ...
    "quiz": (
        "这是练习检测页。设计要求：\n"
        "1. 题目大卡片在上方，选项卡片 2x2 在下方\n"
        "2. 题号用主色圆形背景 + 白色数字\n"
        "3. 选项标签 A/B/C/D 用辅色圆形\n"
        "4. 参考 page-types.md 中 quiz 类型的布局定义"
    ),
    "formula": (
        "这是公式推导页。设计要求：\n"
        "1. 步骤卡片纵向排列，用箭头（<polygon>）连接\n"
        "2. 每步有序号圆 + 公式（等宽字体） + 文字说明\n"
        "3. 最后一步（结论）用强调色卡片高亮\n"
        "4. 参考 page-types.md 中 formula 类型的布局定义"
    ),
    "experiment": (
        "这是实验步骤页。设计要求：\n"
        "1. 左窄右宽 (3:7) 布局\n"
        "2. 左侧：器材列表卡片，每项配图标\n"
        "3. 右侧：步骤编号列表 + 底部结论高亮卡片\n"
        "4. 参考 page-types.md 中 experiment 类型的布局定义"
    ),
    "comparison": (
        "这是对比表格页。设计要求：\n"
        "1. 表头行用主色背景 + 白色文字\n"
        "2. 数据行交替使用 card_bg 和 secondary_bg\n"
        "3. 用 <rect> + <text> + <line> 构建表格\n"
        "4. 参考 page-types.md 中 comparison 类型的布局定义"
    ),
    "summary": (
        "这是知识归纳页。设计要求：\n"
        "1. 分类卡片纵向排列，每个分类有标题栏（辅色背景）\n"
        "2. 知识点用列表形式，配图标前缀\n"
        "3. 可选：底部放「易错点」警示卡片（浅红/浅黄背景）\n"
        "4. 参考 page-types.md 中 summary 类型的布局定义"
    ),
}
```

- [ ] **Step 3: 更新 svg_generator.py 传递 content_density**

修改 `edupptx/design/svg_generator.py` 第 112 行：

```python
# OLD (line 112):
    system_prompt = build_svg_system_prompt(style_guide, visual_plan=draft.visual)

# NEW:
    system_prompt = build_svg_system_prompt(
        style_guide,
        visual_plan=draft.visual,
        content_density=draft.visual.content_density,
    )
```

- [ ] **Step 4: 验证完整管线加载**

Run: `uv run python3 -c "from edupptx.design.svg_generator import generate_slide_svgs; print('OK')"`

Expected: `OK`

- [ ] **Step 5: 运行全量测试**

Run: `uv run pytest tests/ -v`

Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add edupptx/design/prompts.py edupptx/design/svg_generator.py
git commit -m "🚀【重构】：V3 #10 — prompts.py 从硬编码重构为 reference 文件组装器"
```

---

### Task 11: 更新 README + CLAUDE.md

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: 在 README.md 的 License 段前添加设计参考**

```markdown
## 设计参考

V3 的设计系统（色彩层级、字号体系、SVG 技术约束规范）参考了 [PPT Master](https://github.com/hugohe3/ppt-master)（MIT 许可）的设计方法论。PPT Master 专注于咨询类演示文稿的高质量生成，其分层 prompt 架构和设计规范体系对本项目的教育类设计系统建设有重要启发。

EduPPTX 专注于 K12 教育场景，在以下方面有独立的设计：教育专属页面类型（练习题/公式推导/实验步骤/对比表格/知识归纳）、面向课堂投影的内容密度分级（讲授/复习模式）、自动化 SVG→DrawingML 原生形状管线、以及面向教师的一键生成工作流。
```

- [ ] **Step 2: 更新 CLAUDE.md 目录结构**

在 `design/` 部分添加 `references/` 和 `chart_templates/` 的说明。

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "📝【文档】：V3 #11 — README 添加设计参考 + CLAUDE.md 更新目录结构"
```

---

### Task 12: 集成测试验证

**Files:**
- Test: 全量测试 + 手动验证

- [ ] **Step 1: 运行全量测试**

Run: `uv run pytest tests/ -v`

Expected: 全部 PASS

- [ ] **Step 2: 验证 prompt 组装输出**

Run:
```bash
uv run python3 -c "
from edupptx.design.prompts import build_svg_system_prompt
from edupptx.models import VisualPlan
vp = VisualPlan(content_density='lecture')
prompt = build_svg_system_prompt('', visual_plan=vp)
print(f'Lecture prompt: {len(prompt)} chars')
vp2 = VisualPlan(content_density='review')
prompt2 = build_svg_system_prompt('', visual_plan=vp2)
print(f'Review prompt: {len(prompt2)} chars')
# 验证关键内容存在
assert 'CRAP' in prompt or '对比' in prompt, 'Missing design principles'
assert '24px' in prompt, 'Missing lecture font size'
assert '18px' in prompt2, 'Missing review font size'
assert 'quiz' in prompt.lower() or '练习' in prompt, 'Missing quiz page type'
print('All checks passed')
"
```

Expected: 两种模式的 prompt 长度不同，关键内容检查全部通过

- [ ] **Step 3: 验证 CLI 加载**

Run: `uv run edupptx --help`

Expected: 正常输出帮助信息

- [ ] **Step 4: Commit (if any fixes)**

```bash
git add -u
git commit -m "🐛【修复】：V3 #12 — 集成验证修复"
```
