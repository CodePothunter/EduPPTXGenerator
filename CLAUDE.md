# CLAUDE.md — EduPPTX 项目约定

## 项目定位

AI 驱动的教育演示文稿生成器。输入主题+要求，通过 LLM 内容规划 → 设计系统 → 布局引擎 → OOXML 渲染四层管线，生成专业教学 PPT。

## 架构

```
用户输入 (主题 + 要求)
        │
        ▼
┌─────────────────────────────────────────────┐
│  Layer 1: ContentPlanner (LLM)              │
│  主题 → PresentationPlan (结构化 JSON)       │
├─────────────────────────────────────────────┤
│  Layer 2: DesignSystem                      │
│  配色方案 → DesignTokens (9色值+字体+字号)   │
├─────────────────────────────────────────────┤
│  Layer 3: BackgroundManager                 │
│  缓存库 → 程序生成(Pillow) → AI 生图        │
├─────────────────────────────────────────────┤
│  Layer 4: PresentationRenderer              │
│  LayoutEngine(槽位→EMU坐标) + python-pptx   │
│  + XML 补丁 (阴影/透明度/SVG)               │
└─────────────────────────────────────────────┘
        │
        ▼
     output.pptx
```

## 目录结构

```
edupptx/
  __init__.py             # 公开 API: generate()
  generator.py            # 主编排器
  content_planner.py      # LLM 内容规划
  design_system.py        # 6 套配色方案
  layout_engine.py        # 10 种槽位模板 → EMU 坐标
  renderer.py             # python-pptx + XML 补丁渲染
  icons.py                # 109 个 Lucide SVG 图标管理
  backgrounds.py          # 三级背景管理器
  models.py               # Pydantic 数据模型
  llm_client.py           # OpenAI 兼容客户端
  config.py               # 环境变量配置
  cli.py                  # CLI 入口
  prompts/content.py      # LLM 提示词模板
assets/icons/             # Lucide SVG 图标 (24x24)
backgrounds_cache/        # 背景图缓存 (gitignored)
docs/
  design-philosophy.md    # 设计理念（逆向分析+架构决策）
  layout-system.md        # 布局系统（EMU坐标+模板机制）
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
- **槽位模板** — 新 slide type 加 layout 函数 + 注册到 `_LAYOUT_MAP`
- **卡片自适应** — `_make_card_columns(n)` 等分内容区宽度

### 渲染
- **python-pptx 为主** — 大部分元素用原生 API
- **XML 补丁为辅** — 阴影/透明度/SVG 用 lxml 操作 `shape._element`
- **SVG+PNG 双轨** — 现代 PPT 用 SVG，旧版降级 PNG

## 测试

- **框架**: pytest
- **14 个单元测试**: 模型序列化、布局边界检查、渲染输出验证
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
