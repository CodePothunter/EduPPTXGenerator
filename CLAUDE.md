# CLAUDE.md — EduPPTX 项目约定

## 项目定位

AI Agent 驱动的教育演示文稿生成器。V2 SVG Pipeline：LLM 生成全页 SVG（Bento Grid 卡片布局），SVG→DrawingML 原生形状转换输出可编辑 PPTX。

## 架构（V2 SVG Pipeline）

```
用户输入 (主题 + 要求)
        │
        ▼
┌──────────────────────────────────────┐
│  Phase 0: Input Processing           │
│  文档解析 + 联网搜索 (可选)            │
├──────────────────────────────────────┤
│  Phase 1a: Content Planning (LLM#1) │
│  金字塔原理 → 大纲+内容 JSON          │
├──────────────────────────────────────┤
│  Phase 1b: Visual Planning (LLM#2)  │
│  主题色 + 背景 prompt + 卡片色        │
├──────────────────────────────────────┤
│  Phase 2: Background (Seedream AI)   │
│  Phase 2b: Materials (非 debug 模式)  │
├──────────────────────────────────────┤
│  Phase 3: SVG Generation (并行 LLM)  │
│  Bento Grid 卡片布局, 1280×720       │
├──────────────────────────────────────┤
│  Phase 4: Validate + LLM Review     │
│  自动修复 + LLM 审阅修正              │
├──────────────────────────────────────┤
│  Phase 5: SVG→DrawingML→PPTX        │
│  原生形状, 直接可编辑                  │
└──────────────────────────────────────┘
        │
        ▼
output/session_xxx/
├── plan.json       # Phase 1 输出 (含 VisualPlan)
├── DESIGN.md       # Phase 1b 视觉系统快照 (EDUPPTX_VISUAL_PLANNER_FORMAT=design_md 时写入)
├── materials/      # 背景图 + 素材
│   └── background.png
├── slides/         # Phase 4 输出 (修正后 SVG)
│   ├── slide_01.svg
│   └── ...
└── output.pptx     # Phase 5 输出 (原生形状 PPTX)
```

### 核心设计决策

1. **SVG 作为设计中间格式**：LLM 擅长生成 SVG（布局自由度高、视觉质量好），但 SVG 嵌入 PPTX 不可编辑
2. **SVG→DrawingML 原生形状转换**：解析 SVG 每个元素（rect/text/path/image），转为 PowerPoint 原生形状。打开即可编辑，无需"转换为形状"
3. **策划/设计分离**：Phase 1a 专注信息架构，Phase 1b 专注视觉方案，各自更聚焦
4. **Debug 模式**：`--debug` 跳过素材图片获取，图片位置用虚线矩形+描述文字占位，快速预览布局

## 目录结构

```
edupptx/
  agent.py                    # 5 阶段管线编排器
  models.py                   # 数据模型 (InputContext, PlanningDraft, VisualPlan, ...)
  llm_client.py               # OpenAI 兼容 LLM 客户端
  config.py                   # 环境变量配置
  session.py                  # 会话目录管理
  cli.py                      # CLI 入口 (gen/render/plan/styles[+convert])
  style_schema.py             # 风格 schema + ResolvedStyle (load_style 双格式分发 .md/.json)
  style_resolver.py           # palette ref 解析 + 命名 intent 映射 + lint 钩子
  style/
    design_md.py              # DESIGN.md ⇄ StyleSchema parser/serializer (mistune AST + pptx-extensions)
  planning/
    content_planner.py        # Phase 1a: 内容规划 LLM
    visual_planner.py         # Phase 1b: 视觉规划 LLM (双路径: VisualPlan JSON + DESIGN.md)
    prompts.py                # 规划阶段 prompt 模板
  design/
    prompts.py                # SVG 生成 prompt (Bento Grid + 约束)
    svg_generator.py          # Phase 3: 并行 SVG 生成
    style_templates/          # SVG 风格模板 (5 套教育主题)
    references/               # V3 设计参考文档 (design-base, shared-standards, executor-*, page-types)
    chart_templates/          # 图表 SVG 参考模板 (bar/line/pie/kpi/timeline)
  materials/
    image_provider.py         # 多源图片获取 (Pixabay/Unsplash/Seedream)
    background_generator.py   # Phase 2: Seedream 统一背景生成
    backgrounds.py            # Pillow 程序化背景生成 (渐变/几何)
    seedream.py               # Seedream AI 文生图 provider
    pixabay.py                # Pixabay 图片搜索
    unsplash.py               # Unsplash 图片搜索
    icons.py                  # 255 个 Lucide SVG 图标
  postprocess/
    svg_validator.py          # SVG 自动修复 (viewBox/字体/边界/重叠)
    svg_sanitizer.py          # PPT 兼容清理 (去 script/事件)
    svg_reviewer.py           # Phase 4: LLM 审阅修正 SVG
    style_linter.py           # WCAG 对比度 + palette broken-ref 自检 (resolve_style 钩子)
  output/
    svg_to_shapes.py          # SVG→DrawingML 原生形状转换器
    pptx_assembler.py         # PPTX 打包 (native shapes / embed 两种模式)
  input/
    document_parser.py        # PDF/Word/MD 文档解析
    web_researcher.py         # Tavily 联网搜索
styles/                       # 风格主题 (.md DESIGN.md 优先, .json 兼容回退)
assets/
  icons/                      # 255 个 Lucide SVG 图标 (MIT)
output/                       # 会话输出目录 (gitignored)
docs/
tests/
```

## 环境

- **Python**: 3.10+ (uv 管理)
- **包管理**: uv (`uv sync`, `uv add`, `uv run`)
- **运行生成**: `uv run edupptx gen "主题"`
- **Debug 模式**: `uv run edupptx gen "主题" --debug` (跳过素材，快速预览布局)
- **API 配置**: `.env` 文件

## LLM 配置

使用 OpenAI 兼容 API，配置在 `.env`：

```
GEN_MODEL=model-endpoint
GEN_APIKEY=api-key
GEN_BASE_URL=https://ark.cn-beijing.volces.com/api/v3

VISION_GEN_MODEL=image-model-endpoint
VISION_GEN_APIKEY=image-api-key
```

- **timeout=300s** —— SVG 生成需要较长时间
- **thinking.type = "disabled"** —— 豆包 Seed-2.0 专用（自动检测 volces.com）
- **max_retries=1** —— 避免重试风暴

### 可选行为开关

- **`EDUPPTX_VISUAL_PLANNER_FORMAT`**: `json`（默认旧路径）| `design_md`（新 8 段 DESIGN.md 路径，写入 `session_dir/DESIGN.md`）
- **`EDUPPTX_LINT_STRICT`**: `0`（默认）| `1`（contrast warning 升级为 error）

## 编码约定

### 风格
- **显式优于巧妙** — 不用元编程、装饰器魔法
- **最小 diff** — 用最少的文件和抽象解决问题
- **类型标注** — 所有公开函数都加 type hints
- **不加不必要的 docstring** — 函数名自解释则不写

### 配置变更三件套
- **新增/修改 config.py 字段时，必须同步更新三个文件**：`.env`、`.env.example`、`README.md`
- `.env` 加注释掉的默认值，`.env.example` 加占位符说明，`README.md` 更新对应文档段落

### SVG→PPTX 转换
- **SVG 是设计中间格式** — LLM 生成 SVG，不直接生成 PPTX
- **svg_to_shapes.py 逐元素转换** — rect→roundRect, text→txBox, path→custGeom
- **1 SVG px = 9525 EMU** — 坐标转换常量
- **CJK 字体映射** — Noto Sans SC 优先，EA font 自动检测

### Bento Grid 布局系统
- **viewBox="0 0 1280 720"** — 固定 16:9 画布
- **卡片区域 x∈[50,1230], y∈[110,660]** — 上留标题区，下留页脚
- **卡片间距 20px** — 统一
- **11 种布局模式** — center_hero, vertical_list, bento_2col/3col, hero_top, mixed_grid 等

### 自检审查
- **SVG 直接渲染**: `cairosvg.svg2png()` 直出 PNG，不绕 LibreOffice/PDF
- **迭代循环**: 生成→渲染→审查→修 prompt→再生成
- **风格 lint**: `style_linter` 在 `resolve_style` 末尾调用——`broken-ref` 必抛 `StyleValidationError`；`contrast-ratio` 默认 warning（`EDUPPTX_LINT_STRICT=1` 升级为 error）。WCAG 阈值：正文 4.5:1，图标/装饰 3.0:1（SC 1.4.11）。

### DESIGN.md 视觉系统
- **格式**：YAML frontmatter + 8 段 H2 prose（Overview / Colors / Typography / Layout / Elevation / Shapes / Components / Do's and Don'ts）
- **PPT 特有字段**：`pptx-extensions:` 命名空间承载 decorations / card_shadow / background / semantic（DESIGN.md 规范允许 unknown sections preserve）
- **解析**：`mistune` AST 切 H2，避开 fenced code 内的 `## ` 误识别
- **来源**：Phase 1b `generate_design_md` LLM 输出（双路径：`palette_hint` 锁定颜色仅生成 prose / 无 hint 全自由生成）；LLM 全失败时 `_fallback_design_md` 兜底
- **加载**：`load_style(path)` 按 suffix 分发，`.md` 走 `parse_design_md`，`.json` 兼容回退（迁移期 6 个月）

## 测试

- **框架**: pytest
- **运行**: `uv run pytest tests/ -v`

## 设计文档

- 设计理念: `docs/design-philosophy.md`
- 布局系统: `docs/layout-system.md`
- SVG 管线: `docs/svg-pipeline.md`
- 历史设计规格: `docs/_archive/`（v1 迁移、v2 SVG pipeline、v3 design upgrade、DESIGN.md 集成等）

## Git 约定

- 使用 Emoji+中文 commit message
- 不自动 commit，等用户指示
- 不 push 到远程除非明确要求

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health

## Self-Validation
Create a visual QA validation pipeline for our PPTX generator at `tests/visual_qa.py`. It should:
1. Take a generated .pptx file path as input
2. Convert each slide to PNG using `libreoffice --headless --convert-to png` (or python-pptx shape bounds if LibreOffice unavailable)
3. For each slide, extract all shape bounding boxes from python-pptx and check: (a) no two non-background shapes overlap by more than 10%, (b) no text frame content overflows its container height (compare actual text lines × font size vs shape height), (c) no shape extends beyond slide dimensions, (d) no slide is more than 70% empty space
4. Output a JSON report: `{slide_number, issues: [{type, severity, description, shapes_involved}]}`
5. Add a pytest wrapper `test_visual_qa.py` that generates a sample PPTX with our pipeline and asserts zero critical issues
6. Run the tests and iterate until they pass. Then integrate this as a post-generation validation step in the main generator pipeline so it runs automatically after every generation.