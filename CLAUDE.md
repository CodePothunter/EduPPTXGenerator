# CLAUDE.md — EduPPTX 项目约定

## 项目定位

AI Agent 驱动的教育演示文稿生成器。薄 Agent 架构：1 次 LLM 调用完成内容规划 + 素材决策，并行执行素材生成，顺序渲染幻灯片，输出到会话目录。

## 架构

```
用户输入 (主题 + 要求)
        │
        ▼
┌──────────────────────────────────────┐
│  Enriched LLM Planning (1 call)     │
│  Slides + Material Decisions        │
├──────────────────────────────────────┤
│  Parallel Material Execution        │
│  Backgrounds + Diagrams + AI Images │
├──────────────────────────────────────┤
│  Sequential Slide Rendering         │
│  Layout Engine + python-pptx        │
└──────────────────────────────────────┘
        │
        ▼
output/session_xxx/
├── thinking.jsonl
├── plan.json
├── materials/
├── slides/
└── output.pptx
```

### 渲染管线（Schema 驱动）

```
StyleSchema JSON ──→ style_resolver ──→ ResolvedStyle
                                            │
PresentationPlan ───────+                   │
                        │                   ▼
                        +→ layout_resolver → list[ResolvedSlide]
                                                 │
                                                 ▼
                                          validator (clamp+warn)
                                                 │
                                                 ▼
                                          pptx_writer → .pptx
```

三层架构：样式(JSON) → 解析(EMU) → 写入(PPTX)。换 JSON 即换风格，无需改 Python。

## 目录结构

```
edupptx/
  __init__.py             # 公开 API: run_agent(), PPTXAgent, generate()
  agent.py                # Agent 编排器（规划 + 并行素材执行）
  content_planner.py      # LLM 内容规划 + 素材决策
  style_schema.py         # StyleSchema Pydantic 模型 + 命名意图查表
  style_resolver.py       # palette ref 解引用 + intent → EMU
  style_negotiator.py     # LLM 自然语言风格协商
  layout_resolver.py      # Plan + Style → list[ResolvedSlide]
  validator.py            # 布局验证（越界/重叠/最小尺寸）
  pptx_writer.py          # 纯形状写入器
  xml_patches.py          # XML 工具函数（阴影/透明/圆角/字体）
  pipeline_v2.py          # 端到端入口 render_with_schema()
  icons.py                # 109 个 Lucide SVG 图标管理
  backgrounds.py          # 背景管理器
  material_library.py     # 素材库管理
  diagram_native.py       # 程序化图表生成
  models.py               # Pydantic + dataclass 数据模型
  llm_client.py           # OpenAI 兼容客户端
  config.py               # 环境变量配置
  session.py              # 会话目录管理
  cli.py                  # CLI 入口
  prompts/
    content.py            # LLM 内容规划提示词
    agent.py              # LLM Agent 提示词
styles/                   # 样式 JSON 文件
  emerald.json            # 翠绿主题
  blue.json               # 蓝色主题
assets/icons/             # Lucide SVG 图标 (24x24)
materials_library/        # 持久素材库 (gitignored)
output/                   # 会话输出目录 (gitignored)
docs/
  design-philosophy.md    # 设计理念
  layout-system.md        # 布局系统
tests/
examples/
```

## 环境

- **Python**: 3.10+ (uv 管理)
- **包管理**: uv (`uv sync`, `uv add`, `uv run`)
- **运行生成**: `uv run edupptx gen "主题" -o output.pptx`
- **运行测试**: `uv run pytest tests/ -v`
- **API 配置**: `.env` 文件 (GEN_MODEL, GEN_APIKEY, VISION_GEN_MODEL, VISION_GEN_APIKEY)

## LLM 配置

使用 OpenAI 兼容 API，配置在 `.env`：

```
GEN_MODEL=model-endpoint
GEN_APIKEY=api-key
API_BASE_URL=https://ark.cn-beijing.volces.com/api/v3

VISION_GEN_MODEL=image-model-endpoint
VISION_GEN_APIKEY=image-api-key
```

- **thinking.type = "disabled"** —— 豆包 Seed-2.0 默认开启深度思考，对结构化输出任务浪费延迟
- **timeout=180s** —— 大 JSON 输出需要较长时间
- **max_retries=1** —— 避免重试风暴

## 编码约定

### 风格
- **显式优于巧妙** — 不用元编程、装饰器魔法
- **最小 diff** — 用最少的文件和抽象解决问题
- **类型标注** — 所有公开函数都加 type hints
- **不加不必要的 docstring** — 函数名自解释则不写

### 布局系统
- **EMU 坐标** — 所有位置/尺寸用 EMU 整数值 (1pt = 12700 EMU)
- **槽位模板** — 新 slide type 加 resolver 函数 + 注册到 `_SLIDE_RESOLVERS`
- **卡片自适应** — `_make_card_columns(n)` 等分内容区宽度

### 渲染
- **python-pptx 为主** — 大部分元素用原生 API
- **XML 补丁为辅** — 阴影/透明度/SVG 用 lxml 操作 `shape._element`
- **SVG+PNG 双轨** — 现代 PPT 用 SVG，旧版降级 PNG

## 样式系统

- **JSON Schema 驱动**: `styles/` 目录下的 JSON 文件定义完整视觉风格
- **三层 Token 层级**: global (palette/fonts) → semantic (sizes/colors as palette refs) → layout (named intents)
- **命名意图**: margin=comfortable/tight/spacious, card_spacing=normal/tight/wide, icon_size=small/medium/large
- **入口**: `from edupptx.pipeline_v2 import render_with_schema`
- **支持 17 种 slide 类型**: cover, content, lead_in, definition, history, proof, example, exercise, answer, summary, extension, big_quote, closing, section, full_image, image_left, image_right

## 测试

- **框架**: pytest
- **112 个测试**: 全量 Schema 管线测试
- **运行**: `uv run pytest tests/ -v`

## 设计文档

- 设计理念: `docs/design-philosophy.md`
- 布局系统: `docs/layout-system.md`

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
