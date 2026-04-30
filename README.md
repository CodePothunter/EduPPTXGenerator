# EduPPTX

AI Agent 驱动的教育演示文稿生成器。输入主题，自动生成全页 SVG 设计稿，转换为可编辑的原生形状 PPTX。

## 特性

- **V2 SVG Pipeline** — LLM 直接生成整页 SVG (Bento Grid 卡片布局)，SVG→DrawingML 原生形状转换
- **打开即编辑** — 输出原生 PowerPoint 形状，无需"转换为形状"
- **策划/设计分离** — Phase 1 专注信息架构 (金字塔原理)，Phase 3 专注视觉设计
- **5 套教育风格** — emerald / academic / warm / minimal / tech
- **联网搜索** — Responses API 内置 web_search，规划阶段自动补充实时信息
- **Debug 模式** — `--debug` 跳过素材获取，快速预览布局
- **教案备注** — 每页自动生成口语化教学脚本 (Speaker Notes)
- **LLM Review** — Phase 4 自动检测+LLM 审阅修正 SVG 质量

## 快速开始

### 安装

```bash
git clone https://github.com/CodePothunter/EduPPTXGenerator.git
cd EduPPTXGenerator
uv sync
source .venv/bin/activate
```

### 配置 API

```bash
cp .env.example .env
# 编辑 .env，填入 API 密钥
```

支持火山方舟 (豆包) 或任何 OpenAI 兼容接口：

```env
# 文本生成 LLM
GEN_MODEL=your-model-endpoint
GEN_APIKEY=your-api-key

# API 模式: "chat" = Chat Completions | "responses" = Responses API
LLM_PROVIDER=responses

# Provider-specific 推理控制（可选）
# DeepSeek: GEN_THINKING=enabled / GEN_REASONING_EFFORT=high
# OpenAI o-series: GEN_REASONING_EFFORT=low|medium|high
# GEN_THINKING=
# GEN_REASONING_EFFORT=

# 图片生成 (Seedream / DALL-E 兼容)
VISION_GEN_MODEL=your-image-model
VISION_GEN_APIKEY=your-image-key
```

### 生成 PPT

```bash
# 基础用法
uv run edupptx gen "勾股定理"

# 指定风格 + 附加要求
uv run edupptx gen "光合作用" -r "适合高中生" --style edu_academic

# Debug 模式 (跳过素材，快速预览)
uv run edupptx gen "计算机网络" --debug

# 启用 LLM 联网搜索 (仅 Responses API)
uv run edupptx gen "量子计算最新进展" --web-search

# 从文档生成 + Tavily 联网搜索
uv run edupptx gen "基于报告做汇报" --file report.pdf --research

# 分步：先出策划稿，审核后再渲染
uv run edupptx plan "人工智能"
uv run edupptx render output/session_xxx/plan.json

# 查看可用风格
uv run edupptx styles
```

### 作为 Agent 工具调用

EduPPTX CLI 设计为可被 LLM Agent 直接调用，提供机器可读输出。

```bash
# 静默 + JSON 输出 (适合 agent 解析)
uv run edupptx --quiet gen "牛顿三大定律" --debug --json
# → {"ok": true, "mode": "full", "session_dir": "...", "pptx_path": "...", ...}

# 失败时也返回 JSON 错误
uv run edupptx --quiet gen "x" --style invalid --json
# → {"ok": false, "error": "未知风格 'invalid'。可用: ...", "kind": "UnknownStyle"}

# styles 查询带描述
uv run edupptx --quiet styles --json
# → {"ok": true, "styles": [{"name": "edu_emerald", "description": "..."}, ...]}

# 生成后立即跑视觉 QA
uv run edupptx --quiet gen "电磁感应" --debug --json --qa
# 结果 payload 中追加 "qa": {...} 字段
```

**Agent 调用要点**
- `--quiet` 抑制日志，stderr 安静；`--json` 让 stdout 只输出一行 JSON
- 退出码：0=成功，1=运行时错误（JSON 模式 stdout 含详情），2=参数错误（Click 标准）
- 风格在生成前校验，无效风格立即失败而不会浪费 LLM 调用
- 失败时 LLM 原始响应保存到 `output/_debug/llm_parse_fail_*.txt`

## 架构

```
用户输入 (主题 + 要求)
        │
        ▼
┌──────────────────────────────────────┐
│  Phase 0: Input Processing           │
│  文档解析 + 联网搜索 (可选)            │
├──────────────────────────────────────┤
│  Phase 1a: Content Planning (LLM)    │
│  金字塔原理 → 大纲+内容 JSON          │
├──────────────────────────────────────┤
│  Phase 1b: Visual Planning (LLM)     │
│  主题色 + 背景 prompt + 卡片色        │
├──────────────────────────────────────┤
│  Phase 2: Background (Seedream AI)   │
│  Phase 2b: Materials (非 debug 模式)  │
├──────────────────────────────────────┤
│  Phase 3: SVG Generation (并行 LLM)  │
│  Bento Grid 卡片布局, 1280×720       │
├──────────────────────────────────────┤
│  Phase 4: Validate + LLM Review      │
│  自动修复 + LLM 审阅修正              │
├──────────────────────────────────────┤
│  Phase 5: SVG→DrawingML→PPTX         │
│  原生形状, 直接可编辑                  │
└──────────────────────────────────────┘
        │
        ▼
output/session_xxx/
├── plan.json       # Phase 1 输出
├── materials/      # 背景图 + 素材
├── slides/         # Phase 4 修正后 SVG
└── output.pptx     # 原生形状 PPTX
```

## LLM Provider

支持两种 API 模式，通过 `.env` 中 `LLM_PROVIDER` 切换：

| 模式 | 环境变量 | 说明 |
|------|---------|------|
| Chat Completions | `LLM_PROVIDER=chat` | 默认，兼容所有 OpenAI 兼容 API |
| Responses API | `LLM_PROVIDER=responses` | 火山方舟专属，支持联网搜索、上下文缓存 |

`LLM_CONCURRENCY` 控制 SVG 生成和 Review 阶段的 LLM 并行请求数（默认 4）。API 有限流时调低。

Responses API 额外支持 `--web-search` CLI 参数，让 LLM 在规划阶段自动联网搜索补充内容。

## DESIGN.md 视觉规划（实验性）

Layer 3b 引入 DESIGN.md 作为人机共读的视觉风格中间产物：

| 环境变量 | 值 | 说明 |
|---------|---|------|
| `EDUPPTX_VISUAL_PLANNER_FORMAT` | `json` (默认) / `design_md` | 设为 `design_md` 时，规划阶段额外写出 `session_dir/DESIGN.md`（YAML frontmatter + 8 段中文 prose），供后续迭代或人工编辑。Phase 2/3 仍消费旧的 `VisualPlan`，不影响行为 |
| `EDUPPTX_LINT_STRICT` | `0` (默认) / `1` | 设为 `1` 时把 style linter 的对比度告警升级为错误 |

风格文件加载器 `load_style()` 同时支持 `.json`（旧路径）与 `.md`（DESIGN.md 解析后 → StyleSchema）。

### 调色板：`styles/<name>.md`（DESIGN.md 格式）

`styles/` 目录下的调色板文件可以是 `.json`（紧凑、机器可读）或 `.md`（YAML frontmatter + 8 段中文 prose，人机共读）。两种格式经 `load_style()` 解析后产出**严格等价**的 `ResolvedStyle`，由 `tests/test_style_migration_regression.py` 守护。

- `styles/blue.md` — 科技蓝主题，理工科课程
- `styles/emerald.md` — 翠绿主题，自然科学 / 生命科学
- `styles/blue.json` / `styles/emerald.json` — 等价 JSON 版本

prose 8 段固定为：`Overview`、`Colors`、`Typography`、`Layout`、`Elevation`、`Shapes`、`Components`、`Do's and Don'ts`，用 `{colors.xxx}` token 引用调色板字段，确保切换调色板时文档自动跟随。

需要把已有的 JSON 调色板转换为 .md 脚手架时：

```bash
uv run edupptx styles convert <name>      # 把 styles/<name>.json → styles/<name>.md（脚手架）
uv run edupptx styles convert blue --force # 强制覆盖（注意：会清空手写 prose）
```

转换后编辑 8 段 prose，再用 `uv run pytest tests/test_style_migration_regression.py -v` 验证 .md ↔ .json 等价。

## 风格模板

| 模板 | 适用场景 |
|------|---------|
| `edu_emerald` | 数学、自然科学 (默认) |
| `edu_academic` | 学术、论文汇报 |
| `edu_warm` | 文科、人文社科 |
| `edu_minimal` | 简约通用 |
| `edu_tech` | 计算机、工程技术 |

## 项目结构

```
edupptx/
  agent.py                    # 7 阶段管线编排器
  models.py                   # 数据模型 (InputContext, PlanningDraft, VisualPlan, ...)
  llm_client.py               # LLM 客户端 (Chat + Responses API)
  config.py                   # 环境变量配置
  session.py                  # 会话目录管理
  cli.py                      # CLI 入口 (gen/render/plan/styles)
  planning/
    content_planner.py        # Phase 1a: 内容规划
    visual_planner.py         # Phase 1b: 视觉规划
    prompts.py                # 规划阶段 prompt 模板
  design/
    prompts.py                # SVG 生成 prompt (Bento Grid + 约束)
    svg_generator.py          # Phase 3: 并行 SVG 生成
    style_templates/          # 5 套教育主题 SVG 模板
  materials/
    image_provider.py         # 多源图片获取 (Pixabay/Unsplash/Seedream)
    background_generator.py   # Phase 2: Seedream 背景生成
    icons.py                  # 109 个 Lucide SVG 图标
  postprocess/
    svg_validator.py          # SVG 自动修复 (viewBox/字体/边界/重叠)
    svg_sanitizer.py          # PPT 兼容清理 (去 script/事件)
    svg_reviewer.py           # Phase 4: LLM 审阅修正
  output/
    svg_to_shapes.py          # SVG→DrawingML 原生形状转换器
    pptx_assembler.py         # PPTX 组装 (原生形状模式)
  input/
    document_parser.py        # PDF/Word/MD 文档解析
    web_researcher.py         # Tavily 联网搜索
```

## CLI 参考

```
edupptx gen TOPIC [OPTIONS]     从主题生成演示文稿
edupptx plan TOPIC [OPTIONS]    只生成策划稿
edupptx render PLAN [OPTIONS]   从策划稿渲染
edupptx styles                  列出可用风格模板
```

`gen` 选项：

| 选项 | 说明 |
|------|------|
| `-r`, `--requirements` | 附加要求 (如"适合高中生") |
| `-s`, `--style` | 风格模板 (默认 `edu_emerald`) |
| `--file` | 输入文档 (PDF/Word/MD/TXT) |
| `--research` | 启用 Tavily 联网搜索 |
| `--web-search` | 启用 LLM 联网搜索 (仅 Responses API) |
| `--review` | 策划稿生成后暂停审核 |
| `--debug` | 跳过素材获取，快速预览布局 |
| `-o`, `--output` | 输出目录 (默认 `./output`) |
| `-v`, `--verbose` | 详细日志 |

## 开发

```bash
uv sync
uv run pytest tests/ -v
```

## 文档

- [设计理念](docs/design-philosophy.md)
- [布局系统](docs/layout-system.md)
- [SVG 管线](docs/svg-pipeline.md)

## 设计参考

V3 的设计系统（色彩层级、字号体系、SVG 技术约束规范）参考了 [PPT Master](https://github.com/hugohe3/ppt-master)（MIT 许可）的设计方法论。PPT Master 专注于咨询类演示文稿的高质量生成，其分层 prompt 架构和设计规范体系对本项目的教育类设计系统建设有重要启发。

EduPPTX 专注于 K12 教育场景，在以下方面有独立的设计：教育专属页面类型（练习题/公式推导/实验步骤/对比表格/知识归纳）、面向课堂投影的内容密度分级（讲授/复习模式）、自动化 SVG→DrawingML 原生形状管线、以及面向教师的一键生成工作流。

## License

MIT
