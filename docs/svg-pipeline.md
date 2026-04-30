# SVG Pipeline

本文档描述当前代码中的 SVG 到 PPTX 生成链路。代码仍保留“5-phase pipeline”的命名，但 Phase 1 已拆成多段：先做无模板约束的大纲，再做模板家族路由、页面模板匹配、二次规划、伪动画展开、视觉规划和模板约束对齐。

## 流程概览

核心原则：先生成可检查的 `1280x720` SVG，再通过后处理和 `svg_to_shapes` 转成 PPTX 原生 DrawingML 形状，尽量保留 PPT 可编辑性。

```text
Phase 0  → Input Processing (文档解析, 需求整理, 研究摘要接入)
Phase 1a → Content Planning Stage 1 (LLM#1: 无模板约束的大纲 JSON)
Phase 1b → Template Routing (选择低年级/高年级主家族, 合并复用模板, 选择 palette)
Phase 1c → Page Variant Assignment (按 page_type + layout_hint + ranges 匹配 SVG 模板)
Phase 1d → Content Planning Stage 2 (LLM#2: 带模板 soft_ranges 的二次规划)
Phase 1e → Reveal Expansion (展开伪动画页, 再次匹配页面模板)
Phase 1f → Visual Planning + Alignment (LLM#3: 主题色+背景 prompt, 对齐模板 contract)
Phase 2  → Background Generation (Seedream AI, 统一背景)
Phase 2b → Materials Fetch (图片搜索/生成与图标素材, debug 模式跳过)
Phase 3  → SVG Generation (N 次并行 LLM, 命中模板 SVG + style_guide 参与生成)
Phase 4  → Validate + LLM Review (自动修复→必要时 LLM 审阅→清理→公式/图标/图片注入)
Phase 5  → SVG→DrawingML→PPTX (原生形状, 直接可编辑, speaker notes 写入)
```

当前 `Session` 记录的实际 step 名称如下：

```text
input
planning_stage1
template_routing
page_variant_assignment
planning_stage2
reveal_expansion
page_variant_assignment
visual_planning
template_alignment
background
materials
design
postprocess
output
done
```

主要产物：

- `plan.json`：规划结果，包含页面、素材需求、模板路由、视觉方案。
- `design_spec.md`：便于人工检查的设计摘要。
- `slides_raw/slide_XX.svg`：LLM 原始 SVG。
- `slides/slide_XX.svg`：校验、修复、注图后的最终 SVG。
- `output.pptx`：最终 PPTX。

## 数据模型

规划阶段主要使用 `PlanningDraft`、`PagePlan`、`VisualPlan`、`SlideAssets`、`GeneratedSlide`。

`page_type` 表示页面语义，例如 `cover`、`toc`、`content`、`exercise`、`summary`。`layout_hint` 表示版式意图，例如 `center_hero`、`bento_3col`、`hero_with_microcards`、`comparison`、`relation`。

`comparison` 和 `relation` 当前是 `layout_hint`，不是 `page_type`。如果 LLM 把它们误写成 `page_type`，规划归一化逻辑会把页面改成 `content`，并把原值转移到 `layout_hint`。

## Phase 0: Input

入口在 `edupptx/agent.py`。输入会被整理成 `InputContext`，包含主题、需求、上传文档文本和研究摘要。后续模板家族路由优先使用 Stage 1 大纲文本，如果需要更早路由，也可以从输入文本中收集关键词。

## Phase 1: Planning And Routing

### Stage 1 Outline

`edupptx/planning/content_planner.py` 先调用 LLM 生成不带模板约束的页面大纲。这个阶段只关心课程结构、页面语义、内容点、素材需求和初始版式意图。

JSON 解析支持三层兜底：

- 直接 `json.loads`。
- 去除尾随逗号后重试。
- 对 LLM 偶发的字符串内部原始换行使用 `strict=False` 兜底。

### Deck Template Routing

`edupptx/design/template_router.py` 读取 `edupptx/design/page_templates/*/metadata.xml`。当前主模板家族包括低年级、高年级和复用目录，其中复用目录是共享结构库。

整套课件先选择一个主家族，例如低年级或高年级。随后代码会把该主家族与 `复用` 家族合并成一个有效 manifest。也就是说，页面级模板匹配时，主家族模板和复用模板会同等参与打分，不是先主家族、后复用兜底。

Deck 级家族选择主要依赖：

- `identity`、`routing`、`tags`、`subjects`、`scenes`、`priority_keywords`、`negative_keywords`。
- Stage 1 大纲中的 topic、audience、purpose、style_direction、page_type、layout_hint、标题和内容点。
- 如果关键词不足，可能调用 LLM 在候选主家族中选择。

### Palette Routing

配色由 `resolve_palette_preset` 决定。优先级大致为：显式指定 palette，其次参考色彩路由规则，其次默认 palette。选中的 palette 会传给 `visual_planner`，并写入 `VisualPlan`。

### Page Variant Assignment

页面级模板命中由 `variant_catalog` 驱动。每个 `<variant>` 指向一个 SVG 模板 stem，例如 `content_relation_1` 对应 `content_relation_1.svg`。

打分因素包括：

- `page_type` 是否一致。
- `layout_hint` 是否一致，权重最高。
- `hit_keywords` 是否出现在页面路由文本中。
- stem 中的 token 是否出现在页面路由文本中。
- `card_range`、`subcard_range`、`image_range` 是否贴近估算值。
- `toc`、`hero_with_microcards` 等特殊版式的专用评分。
- 分数接近时，可让 LLM 在前几个候选中做 tie-break。

当前约定：

- `card_range` 只表示外层大 card 或顶层信息块数量。
- `subcard_range` 表示内部子结构数量，例如 microcard、subcard、关系节点、比较项、表格行列。
- 如果某个 variant 没写 `subcard_range`，表示不对内部子 card 数量打分限制，不等于禁止使用子 card。
- `image_range` 表示该模板适合的图片数量区间，不强制图片只能放在大 card 或小 card 中。

### Stage 2 Refinement

Stage 2 会把 Stage 1 大纲和页面匹配到的模板 brief 一起传给 LLM。brief 中包含：

- matched SVG stem。
- `page_type` 和 `layout_hint`。
- `hit_keywords`。
- `soft_ranges`，包括 `top_level_card_range`、`subcard_range`、`image_range`。
- `page_features` 和 `reference_rule`。

这些范围是软约束，作用是指导页面信息密度和结构，不是硬性生成失败条件。

### Reveal Expansion

`finalize_reveal_pages` 会在 Stage 2 后展开伪动画页面，例如题目页和答案揭示页。展开后会再次执行页面模板匹配，避免新增页面沿用错误模板。reveal 页如果声明了 `reveal_from_page`，会优先复用源页模板。

### Visual Planning And Alignment

视觉规划生成 `VisualPlan`，包括主色、辅色、强调色、卡片底色、文字色、背景色倾向和背景 prompt。随后 `template_alignment` 会根据模板 contract 对规划做一次对齐。

可选 DESIGN.md 路径（实验性，由 `EDUPPTX_VISUAL_PLANNER_FORMAT=design_md` 开启）：除了 `VisualPlan` JSON 之外，还会让 LLM 输出一份 8 段 markdown DESIGN.md 写入 `session_dir/DESIGN.md`，作为可读、可改、可 diff 的视觉系统快照。当 `palette_hint` 已确定（来自模板路由）时走"prose-only"路径——颜色锁定、LLM 仅生成 prose；无 hint 时让 LLM 自由生成完整 DESIGN.md。LLM 全失败时 `_fallback_design_md` 兜底，不阻塞 Phase 3。

DESIGN.md 当前为只读产物，Phase 3 SVG 生成仍消费 `VisualPlan`；用户编辑 DESIGN.md 后重跑 `render` **不会**生效（消费路径在路线图上）。

## Phase 2: Background And Materials

背景生成使用 `VisualPlan.background_prompt` 和配色倾向。素材阶段根据每页 `material_needs` 获取图片和图标。`--debug` 模式会跳过外部素材获取，但仍会生成背景，并为每页创建空的 `SlideAssets`。

普通模式下，图片会先下载或生成到素材目录，真正写入 SVG 发生在 Phase 4 的 image injection。

## Phase 3: SVG Design

SVG 生成入口是 `edupptx/design/svg_generator.py`。系统 prompt 由 `build_svg_system_prompt` 构造，用户 prompt 由 `build_svg_user_prompt` 构造。

系统 prompt 当前会合并：

- `design/references/design-base.md`。
- `design/references/shared-standards.md`。
- `executor-lecture.md` 或 `executor-review.md`。
- `design/references/page-types.md`。
- 图片边界规则。
- `VisualPlan` 中的颜色规范。
- 当前模板家族的 `style_guide.md`。

`style_guide.md` 只进入 SVG 生成阶段，不进入 planning 阶段。Planning 阶段使用的是 `metadata.xml` 里的 deck 路由信息、planner page spec 和 variant brief。因此低年级“字少图多”、高年级“结构化推理”等规划建议应写在 `metadata.xml` 中；具体视觉风格、纹理、装饰、卡片观感等应写在 `style_guide.md` 中。

用户 prompt 当前会包含：

- 页码、页面类型、标题、副标题、`layout_hint`。
- 内容点和 `design_notes`。
- 图片和图标需求。
- 已命中的 `page.template_variant`。
- 对应模板 SVG 源码。
- reveal 页的参考 SVG。

图片占位规则：

- 普通模式使用 `<image href="__IMAGE_ROLE__" .../>` 形式，后处理阶段再替换成 base64 JPEG。
- debug 模式不使用真实图片 token，而是要求 LLM 画虚线占位框。
- reviewer 阶段会保护 `__IMAGE_...__` token，避免 LLM 修改后导致注图失败。

如果存在 reveal 页，SVG 生成会切到顺序执行，因为后续 reveal 页需要读取源页 SVG 作为参考。否则按 `config.llm_concurrency` 并行生成。

## Phase 4: Postprocess

后处理入口是 `EduPPTXAgent._phase4_postprocess`，每页并行执行。

处理顺序：

```text
save raw SVG
validate_and_fix
optional LLM review
sanitize_for_ppt
render_latex_formulas
embed_icon_placeholders
inject real images
save final SVG
```

`validate_and_fix` 负责自动修复和告警，包括 XML 可解析性、`viewBox`、PPT 不支持标签、字体、越界元素、文本包裹、图片 href、图片尺寸溢出，以及特定版式的结构检查。

LLM review 只在存在有意义 warning 时触发。轻微自动修复类 warning 会跳过 review，避免 reviewer 对已经可用的 SVG 做额外扰动。reviewer 还有 image placeholder 保护逻辑：如果 review 后 `__IMAGE_...__` 占位丢失或脱离 `<image href>`，会回退到 review 前 SVG。

`sanitize_for_ppt` 会移除或规整 PPTX 转换不稳定的 SVG 内容。随后公式渲染会把 LaTeX 转成 SVG 元素，图标嵌入会把 `<use data-icon="...">` 替换成真实 SVG 图形。最后 `_inject_images` 会把 `__IMAGE_ROLE__` 替换成压缩后的 JPEG data URI。

## Phase 5: PPTX Output

PPTX 输出入口是 `edupptx/output/pptx_assembler.py`。默认路径会把 SVG 转成 PPTX 原生 DrawingML 形状，再写入 zip 结构。这样生成的图形和文字在 PowerPoint 中更容易编辑。

常见 SVG 到 PPTX 映射：

| SVG | PPTX |
| --- | --- |
| `rect` | `p:sp`，可映射圆角 |
| `circle` / `ellipse` | `p:sp` ellipse |
| `path` | custom geometry |
| `line` / `polygon` / `polyline` | custom geometry |
| `image` | `p:pic` |
| `text` / `tspan` | text box |
| `g transform` | 子元素坐标变换 |
| `use href="#id"` | 展开 defs 引用 |
| `linearGradient` / `radialGradient` | `a:gradFill` |
| `feDropShadow` | `a:outerShdw` |

单位换算为 `1 SVG px = 9525 EMU`。

### Text Conversion

`edupptx/output/svg_to_shapes.py` 中的 `convert_text` 是 SVG 转 PPTX 最容易出问题的部分。当前规则：

- 继承父级 `fill`、`font-size`、`font-weight`、`font-family`、`text-anchor` 等文字属性。
- 含 `<tspan>` 的文本会保留父级 text、子级 text 和 tail，避免富文本片段丢字。
- pretty-printed SVG 里的缩进空白会在 tspan 文本中被归一化，避免 PPT 把源码换行和缩进当成真实文本。
- `dy > 0` 的 tspan 会被视为新段落。
- 多行 tspan 的行距取第一个正向 `dy`，没有时使用默认行距。
- plain text 使用 `wrap="square"`，保留 PPT 自动换行编辑能力。
- tspan-authored text 使用 `wrap="none"`，因为 SVG 已经显式声明换行，禁止 PPT 再自动重排，减少 SVG 转 PPTX 后的换行错位。

如果出现“SVG 正常但 PPTX 换行错位”，优先检查：

- SVG 文本是否把同一行拆成多个 sibling `<text>`。
- tspan 是否存在源码缩进导致的空白。
- 是否本应使用显式 tspan 换行，却让 PPT 自动 wrap。
- 文本框估算宽度是否小于实际文字宽度。
- card 内文字是否已经超出 SVG 侧边界。

## 模板文件职责

一个模板家族目录通常包含：

```text
page_templates/<family>/
  metadata.xml
  style_guide.md
  <variant>.svg
  <variant>.png
```

`metadata.xml` 用于规划和路由：

- deck 级家族命中。
- planner page specs。
- variant_catalog。
- `hit_keywords`。
- `image_range`、`card_range`、`subcard_range`。
- `page_features` 和 `reference_rule`。

`style_guide.md` 用于 SVG 生成：

- 视觉语言。
- 颜色使用方式。
- 图片风格。
- card 质感。
- 装饰元素。
- 年级差异化视觉表达。

SVG 模板用于给 LLM 提供具体布局参考。PNG 预览主要用于人工检查和扩展模板时对齐命名。

新增模板时，如果不新增 `page_type` 和 `layout_hint`，通常只需要增加 SVG、PNG，并在对应 `metadata.xml` 的 `variant_catalog` 中登记。只有新增语义页型或全新版式意图时，才需要同步修改模型枚举、规划 prompt、路由评分和 SVG prompt 参考。

## Debug And Review

常用命令：

```bash
uv run edupptx gen "课件主题" --debug
uv run edupptx gen "课件主题" --review
uv run edupptx render output/session_xxx/plan.json
```

`--debug` 跳过素材获取，适合验证规划、模板命中、SVG 布局和 PPTX 转换。`--review` 会在保存 `plan.json` 后停止，适合人工修改规划后再用 `edupptx render` 继续生成。

排查顺序建议：

- 先看 `plan.json`，确认 `page_type`、`layout_hint`、`template_variant`、`material_needs` 是否正确。
- 再看 `design_spec.md`，确认整体设计和页面密度是否合理。
- 再看 `slides_raw` 和 `slides` 差异，判断问题来自 LLM、validator、reviewer 还是 image injection。
- 最后看 `output.pptx`，判断问题是否只发生在 SVG 到 PPTX 转换。
