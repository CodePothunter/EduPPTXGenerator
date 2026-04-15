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
  agent.py                    # 7 阶段管线编排器
  models.py                   # 数据模型 (InputContext, PlanningDraft, VisualPlan, ...)
  llm_client.py               # OpenAI 兼容 LLM 客户端
  config.py                   # 环境变量配置
  session.py                  # 会话目录管理
  cli.py                      # CLI 入口 (gen/render/plan/styles)
  planning/
    content_planner.py        # Phase 1a: 内容规划 LLM
    visual_planner.py         # Phase 1b: 视觉规划 LLM
    prompts.py                # 规划阶段 prompt 模板
  design/
    prompts.py                # SVG 生成 prompt (Bento Grid + 约束)
    svg_generator.py          # Phase 3: 并行 SVG 生成
    style_templates/          # SVG 风格模板 (5 套教育主题)
  materials/
    image_provider.py         # 多源图片获取 (Pixabay/Unsplash/Seedream)
    background_generator.py   # Phase 2: Seedream 统一背景生成
    seedream.py               # Seedream AI 文生图 provider
    icons.py                  # 109 个 Lucide SVG 图标
  postprocess/
    svg_validator.py          # SVG 自动修复 (viewBox/字体/边界/重叠)
    svg_sanitizer.py          # PPT 兼容清理 (去 script/事件)
    svg_reviewer.py           # Phase 4: LLM 审阅修正 SVG
  output/
    svg_to_shapes.py          # SVG→DrawingML 原生形状转换器
    pptx_assembler.py         # PPTX ZIP 打包 (3 种模式)
  input/
    document_parser.py        # PDF/Word/MD 文档解析
    web_researcher.py         # Tavily 联网搜索
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

## 编码约定

### 风格
- **显式优于巧妙** — 不用元编程、装饰器魔法
- **最小 diff** — 用最少的文件和抽象解决问题
- **类型标注** — 所有公开函数都加 type hints
- **不加不必要的 docstring** — 函数名自解释则不写

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

## 测试

- **框架**: pytest
- **运行**: `uv run pytest tests/ -v`
- **注意**: V1 测试待迁移，部分 import 已失效

## 设计文档

- 设计理念: `docs/design-philosophy.md`
- 布局系统: `docs/layout-system.md`
- SVG 管线: `docs/svg-pipeline.md` (V2)

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
