# V3 Design Upgrade — 设计元素大提升

> **Goal:** 通过提升 prompt 知识密度和设计系统精细度，显著提高 SVG 生成的视觉质量，同时保持自动化管线的轻量化。
>
> **策略:** Prompt 工程优先 — 核心投入在设计知识文档，工程改动最小化。
>
> **参考:** 设计系统方法论参考 [PPT Master](https://github.com/hugohe3/ppt-master)（MIT 许可，咨询类演示文稿生成）。EduPPTX 专注 K12 教育场景，页面类型、密度分级、自动化管线均为独立设计。

---

## 1. Prompt 分层架构

当前 prompt 是硬编码在 `design/prompts.py` 中的大字符串。V3 拆分为 **markdown reference 文件 + Python 组装器**。

### 文件结构

```
edupptx/design/
  references/
    design-base.md          # 公共规范：7 色系统、6 级字号、间距、设计原则
    shared-standards.md      # SVG 禁用特性黑名单 + PPT 兼容替代方案
    executor-lecture.md      # 课堂讲授风格（大字、宽松、视觉冲击）
    executor-review.md       # 复习归纳风格（密集、小字、知识点梳理）
    page-types.md            # 5 种教育页面类型的布局定义和 SVG 示例
  chart_templates/           # 5 种图表的 SVG 参考模板
    bar_chart.svg
    line_chart.svg
    pie_chart.svg
    kpi_cards.svg
    timeline.svg
  style_templates/           # 现有 5 套配色模板（保留不变）
  prompts.py                 # 组装器：读取 reference 文件 + VisualPlan 数据拼接为完整 prompt
```

### 组装逻辑

`prompts.py` 的 `build_svg_system_prompt()` 改为：

1. 读取 `design-base.md`（始终加载）
2. 读取 `shared-standards.md`（始终加载）
3. 根据 `content_density` 选择 `executor-lecture.md` 或 `executor-review.md`
4. 读取 `page-types.md`（始终加载）
5. 拼接 VisualPlan 的配色数据
6. 拼接风格模板（现有 style_templates/*.svg 内容）

好处：prompt 内容可独立迭代，不需要改 Python 代码。

---

## 2. 设计系统

### 2.1 色彩系统（7 色）

| 角色 | 字段名 | 用途 |
|------|--------|------|
| 主色 | `primary_color` | 标题栏装饰条、重要元素、页面顶部色带 |
| 辅色 | `secondary_color` | 次级标题、图标填充、辅助装饰 |
| 强调色 | `accent_color` | 关键数据、重点标注（全局 ≤3 处） |
| 卡片背景 | `card_bg_color` | 卡片 `<rect>` 填充 |
| **次背景** | `secondary_bg_color` | 区域分隔、交替行背景、引用块背景 |
| 正文色 | `text_color` | 正文 `<text>` 填充 |
| 标题色 | `heading_color` | 页面标题 `<text>` 填充 |

配色规则：主色 60% / 辅色 30% / 强调色 10%。正文对比度 ≥ 4.5:1，大字 ≥ 3:1。

### 2.2 字号体系（6 级）

以正文字号为 1x 基准，用倍率定义。支持两种内容密度：

| 层级 | 倍率 | 讲授模式 (24px) | 复习模式 (18px) | 字重 |
|------|------|----------------|----------------|------|
| 封面标题 | 2.5x | 60px | 45px | Bold |
| 页标题 | 1.5-2x | 36-48px | 27-36px | Bold |
| 副标题 | 1.2x | 29px | 22px | SemiBold |
| **正文** | **1x** | **24px** | **18px** | Regular |
| 注释 | 0.75x | 18px | 14px | Regular |
| 页码 | 0.55x | 13px | 10px | Regular |

### 2.3 间距规范

| 元素 | 值 |
|------|-----|
| 卡片间距 | 20px |
| 卡片内边距 | 24px |
| 卡片圆角 | 12-16px |
| 内容块间距 | 32px |
| 图标-文字间距 | 12px |
| 正文行高 | 1.5 |
| 标题行高 | 1.2 |

---

## 3. 内容密度分级

### 3.1 讲授模式 (lecture)

- **目标场景：** 课堂投影，教师面对面讲解
- **正文基准：** 24px
- **每页要点：** 3-5 个
- **视觉特点：** 大字、宽松留白、视觉冲击、适合远距离观看
- **卡片高度：** 偏大（单行高 530-600px，双行各 265-295px）

### 3.2 复习模式 (review)

- **目标场景：** 复习资料、打印讲义、平板阅读
- **正文基准：** 18px
- **每页要点：** 6-8 个
- **视觉特点：** 信息密集、小字紧凑、知识点梳理、适合近距离阅读
- **卡片高度：** 偏紧凑，充分利用画布空间

### 3.3 模式选择

由 VisualPlan LLM 根据用户需求自动判断，新增 `content_density` 字段：
- 用户说"课件""课堂""讲课" → `lecture`
- 用户说"复习""总结""归纳""打印" → `review`
- 默认 → `lecture`

---

## 4. 教育专属页面类型

在现有 7 种类型（cover/toc/section/content/data/case/closing）基础上新增 5 种：

### 4.1 `quiz` — 练习/检测页

- **布局：** 题目大卡片（上）+ 选项卡片 2x2（下）
- **元素：** 题号圆、题目文本、选项标签（A/B/C/D）
- **配色：** 正确选项可用辅色高亮（教师控制）

### 4.2 `formula` — 公式推导页

- **布局：** 步骤卡片纵向排列，箭头连接
- **元素：** 序号圆、公式文本（等宽字体）、文字说明
- **特点：** 最后一步（结论）用强调色卡片

### 4.3 `experiment` — 实验步骤页

- **布局：** 左窄右宽 (3:7)
- **左侧：** 器材列表卡片
- **右侧：** 步骤列表 + 底部结论高亮卡片
- **元素：** 步骤编号、器材图标、结论灯泡图标

### 4.4 `comparison` — 对比表格页

- **布局：** 表格样式，表头行 + 交替背景色数据行
- **元素：** `<rect>` 行 + `<text>` 单元格，`<line>` 分隔
- **配色：** 表头用主色背景白字，数据行交替用 card_bg 和 secondary_bg

### 4.5 `summary` — 知识归纳页

- **布局：** 分类卡片纵向排列
- **元素：** 分类标题栏（辅色背景）+ 知识点列表 + 可选的警示卡片（易错点）
- **特点：** 紧凑排版，强调知识结构性

### 实现方式

每种类型在 `page-types.md` 中给出：
1. 布局描述和坐标参考
2. 完整的 SVG 代码示例片段（约 30-50 行）
3. 适配规则（内容多/少时如何调整）

策划稿 LLM 在 `page_type` 中选择这些类型，SVG 生成 LLM 根据 `page-types.md` 中的定义生成对应布局。

---

## 5. 图表系统

### 5.1 支持的图表类型

| 类型 | 文件 | 教育用途 | SVG 实现 |
|------|------|---------|---------|
| 柱状图 | `bar_chart.svg` | 数据对比、实验数据 | `<rect>` 柱 + `<text>` 标签 + `<line>` 轴 |
| 折线图 | `line_chart.svg` | 趋势变化、函数图像 | `<polyline>` + `<circle>` 数据点 |
| 饼图 | `pie_chart.svg` | 成分占比、分类统计 | `<path>` 扇形弧线 |
| KPI 卡片 | `kpi_cards.svg` | 关键数据展示 | 2x2 卡片，大数字 48px + 小标签 14px |
| 时间线 | `timeline.svg` | 历史事件、发展历程 | 横向 `<line>` + `<circle>` 节点 + 卡片 |

### 5.2 集成方式

- 图表模板放在 `design/chart_templates/`，每个是一个带标注的 SVG 参考实现
- `page-types.md` 中描述何时使用哪种图表
- 策划稿中通过 `material_needs.chart.type` 指定图表类型
- SVG 生成 LLM 根据模板参考在页面中直接绘制图表（不需要运行时代码）
- 图表配色使用 VisualPlan 的色板

---

## 6. 图标扩充

从 109 个 Lucide 图标扩充到约 250 个。

### 扩充分类

| 类别 | 新增示例 | 数量 |
|------|---------|------|
| 教育 | book, notebook, pen, ruler, calculator, microscope, atom, dna, globe, graduation-cap, school, apple, pencil | ~30 |
| 数据 | chart-bar, chart-line, chart-pie, trending-up, percent, hash, database, table | ~15 |
| 交互 | check-circle, x-circle, alert-triangle, info, help-circle, thumbs-up, thumbs-down | ~15 |
| 箭头 | arrow-right, arrow-down, chevron-right, corner-down-right, move, repeat, refresh | ~15 |
| 科学 | flask, beaker, magnet, thermometer, zap, wind, droplet, sun, moon | ~20 |
| 通用补充 | folder, file-text, link, external-link, copy, share, filter, sort | ~20 |

来源：Lucide Icons（MIT 许可），SVG path 直接嵌入 `icons.py`。

---

## 7. 设计原则注入

在 `design-base.md` 中编码以下设计知识：

### 7.1 CRAP 四原则

- **Contrast（对比）：** 通过字号、字重、颜色深浅建立 3-4 个视觉层次
- **Repetition（重复）：** 同一章节内保持布局一致，跨章节可变化
- **Alignment（对齐）：** 所有元素对齐到网格，不出现"差一点"的错位
- **Proximity（亲密性）：** 相关内容靠近，不相关内容拉开距离

### 7.2 教育专属原则

- **结论先行但展开推导：** 区别于咨询的纯结论先行，教育内容需要展示推理过程
- **信息密度控制：** 讲授模式 3-5 要点/页，复习模式 6-8 要点/页
- **视觉节奏：** 密集页后跟一个"呼吸页"（大图/引言/过渡），防止学生疲劳
- **颜色语义：** 主色标识章节归属，强调色标识重要知识点，不要"彩虹配色"

### 7.3 PPT 兼容硬约束

从 `shared-standards.md` 移入，明确列出禁用特性和替代方案：
- `clipPath` / `mask` / `<style>` / `class` / `foreignObject` / `<animate>` 等全部禁用
- `rgba()` → `fill-opacity`
- `<g opacity>` → 逐元素设置
- `marker-end` → `<polygon>` 三角箭头

---

## 8. 工程改动汇总

| 改动 | 文件 | 内容 |
|------|------|------|
| prompt 组装器重构 | `design/prompts.py` | 从硬编码字符串改为读取 `.md` 文件拼接 |
| VisualPlan 新增字段 | `planning/visual_planner.py` + `models.py` | 新增 `secondary_bg_color`, `content_density` |
| 策划稿新增页面类型 | `planning/prompts.py` | page_type 枚举加 5 种教育类型 |
| 图标扩充 | `materials/icons.py` | 从 109 扩到 ~250 |
| 图表模板 | `design/chart_templates/*.svg` (新建) | 5 个参考 SVG |
| reference 文档 | `design/references/*.md` (新建) | 5 个设计知识文档 |
| README 参考来源 | `README.md` | 添加 Acknowledgments 段落 |
| CLAUDE.md 更新 | `CLAUDE.md` | 更新目录结构 |

### 不改动

- `agent.py` — 5 Phase 管线架构不变
- `output/svg_to_shapes.py` — DrawingML 转换不变
- `postprocess/` — 后处理逻辑不变
- `llm_client.py` — LLM 客户端不变
- `config.py` — 无新配置项

---

## 9. README 参考来源说明

在 README.md 的 Acknowledgments 段落添加：

> **设计参考**
>
> V3 的设计系统（色彩层级、字号体系、SVG 技术约束规范）参考了 [PPT Master](https://github.com/hugohe3/ppt-master)（MIT 许可）的设计方法论。PPT Master 专注于咨询类演示文稿的高质量生成，其分层 prompt 架构和设计规范体系对本项目的教育类设计系统建设有重要启发。
>
> EduPPTX 专注于 K12 教育场景，在以下方面有独立的设计：教育专属页面类型（练习题/公式推导/实验步骤/对比表格/知识归纳）、面向课堂投影的内容密度分级（讲授/复习模式）、自动化 SVG→DrawingML 原生形状管线、以及面向教师的一键生成工作流。
