# EduPPTX

AI Agent 驱动的教育演示文稿生成器。输入主题和自然语言风格要求，Agent 自动完成内容规划、风格协商、素材准备、排版渲染，输出专业教学 PPT。

面向 AI Agent 设计，可作为 Python 库集成到教学 Agent、备课系统、出题工具中。

## 特性

- **薄 Agent 架构** — 5 阶段管线（规划→风格协商→素材决策→并行执行→渲染）
- **自然语言风格控制** — "简约商务风" / "清新活泼" 等描述自动转译为样式参数
- **Schema 驱动渲染** — 三层架构（JSON 样式→EMU 解析→PPTX 写入），换 JSON 换风格
- **自适应卡片布局** — 根据样式组合自动选择最优布局模式，保证任何配置都不崩
- **并行素材执行** — 背景图、AI 插图并发生成，结果进入持久化素材库
- **图片智能适配** — 按 slot 比例选择最佳生成尺寸，渲染时 fit-within 保持宽高比
- **109 个 Lucide 图标** — 自动着色匹配主题，SVG+PNG 双格式嵌入
- **17 种页面布局** — 卡片页、全图页、左图右文、大字金句、章节过渡等
- **教案备注** — 每页自动生成口语化教学脚本（Speaker Notes）
- **思考过程可观测** — `thinking.jsonl` 记录 Agent 完整决策轨迹

## 快速开始

### 安装

```bash
git clone https://github.com/CodePothunter/EduPPTXGenerator.git
cd EduPPTXGenerator
uv sync
```

### 配置 API

```bash
cp .env.example .env
# 编辑 .env，填入 OpenAI 兼容 API 的密钥
```

支持任何 OpenAI 兼容接口（豆包、DeepSeek、OpenAI、Ollama 等）：

```env
GEN_MODEL=your-model-endpoint       # 文本模型
GEN_APIKEY=your-api-key
API_BASE_URL=https://api.openai.com/v1

# 可选：AI 插图生成（豆包 Seedream / 任何 OpenAI 兼容图像 API）
VISION_GEN_MODEL=your-image-model
VISION_GEN_APIKEY=your-image-key
```

### 生成 PPT

```bash
# 最简用法
uv run edupptx gen "勾股定理"

# 指定风格要求和输出目录
uv run edupptx gen "光合作用" -r "适合高中生，风格清新活泼" -o output/

# 查看可用样式主题
uv run edupptx palettes

# 查看可用图标
uv run edupptx icons
```

## 架构概览

```
用户输入 (主题 + 风格要求)
        │
        ▼
┌──────────────────────────────────────┐
│  Phase 1: 内容规划 (1 次 LLM 调用)   │  → PresentationPlan (JSON)
├──────────────────────────────────────┤
│  Phase 2: 风格协商 (1 次 LLM 调用)   │  → StyleSchema → ResolvedStyle
├──────────────────────────────────────┤
│  Phase 3: 素材决策 (N 次并行 LLM)    │  → 每页的背景/插图决策
├──────────────────────────────────────┤
│  Phase 4: 素材执行 (并行，无 LLM)    │  → 背景图 + AI 插图
├──────────────────────────────────────┤
│  Phase 5: Schema 渲染               │  → .pptx 文件
│  StyleSchema → layout_resolver       │
│  → validator → pptx_writer           │
└──────────────────────────────────────┘
        │
        ▼
output/session_xxx/
├── thinking.jsonl    # Agent 决策轨迹
├── plan.json         # 内容规划
├── style_schema.json # 协商后的样式
├── materials/        # 背景图 + 插图
├── slides/           # 每页状态快照
└── output.pptx       # 最终文件
```

详细架构设计见 [docs/design-philosophy.md](docs/design-philosophy.md)。

## 作为 Python 库使用

### 主要 API

```python
from edupptx import run_agent

# 运行 Agent，返回会话目录路径
session_dir = run_agent("勾股定理")
session_dir = run_agent(
    topic="光合作用",
    requirements="适合高中生，风格清新活泼",
)
```

### 纯数据驱动（不调用 LLM）

直接构造 `PresentationPlan`，跳过 LLM 规划阶段：

```python
from edupptx.models import PresentationPlan, SlideContent, SlideCard
from edupptx.pipeline_v2 import render_with_schema

plan = PresentationPlan(
    topic="自定义主题",
    palette="blue",
    slides=[
        SlideContent(
            type="cover",
            title="我的演示文稿",
            subtitle="副标题",
            cards=[
                SlideCard(icon="star", title="要点一", body="详细说明"),
                SlideCard(icon="target", title="要点二", body="详细说明"),
                SlideCard(icon="check", title="要点三", body="详细说明"),
            ],
            formula="E = mc²",
            notes="开场白",
        ),
        SlideContent(type="closing", title="谢谢", subtitle="演示结束", notes="结束语"),
    ],
)

render_with_schema(plan, "styles/blue.json", output_path="output.pptx")
```

## 页面类型

| 类型 | 用途 | 卡片数 | 特殊字段 |
|------|------|--------|---------|
| `cover` | 封面 | 3 | subtitle, formula |
| `lead_in` | 情境引入 | 3 | subtitle |
| `definition` | 核心定义 | 2-3 | - |
| `content` | 通用内容 | 2-3 | - |
| `history` | 历史背景 | 3 | - |
| `proof` | 推导证明 | 2-3 | formula |
| `example` | 例题讲解 | 1-2 | - |
| `exercise` | 练习题 | 2-3 | - |
| `answer` | 答案揭晓 | 2-3 | - |
| `summary` | 总结回顾 | 3-4 | footer |
| `extension` | 延伸思考 | 2-3 | footer |
| `closing` | 结束页 | 0 | subtitle |
| `big_quote` | 大字金句 | 0 | title(引文), footer(出处) |
| `full_image` | 全图页 | 0 | title, AI 插图填满内容区 |
| `image_left` | 左图右文 | 1-2 | 左侧插图 + 右侧卡片 |
| `image_right` | 左文右图 | 1-2 | 左侧卡片 + 右侧插图 |
| `section` | 章节过渡 | 0 | 居中大标题 + 副标题 |

## 样式主题

当前提供 2 套 JSON 主题文件，可通过自然语言风格协商自动调整：

| 主题 | 主色 | 适用场景 |
|------|------|---------|
| `emerald` | #059669 | 数学、自然科学、通用 |
| `blue` | #2563EB | 科技、工程 |

添加新主题只需在 `styles/` 目录创建 JSON 文件，零代码。结构参考 `styles/emerald.json`。

## 项目结构

```
edupptx/
├── __init__.py           # 公开 API: run_agent(), PPTXAgent, generate()
├── agent.py              # Agent 编排器（5 阶段管线）
├── content_planner.py    # LLM 内容规划 + 图标校验
├── style_schema.py       # StyleSchema Pydantic 模型
├── style_resolver.py     # palette ref → hex, intent → EMU
├── style_negotiator.py   # LLM 自然语言风格协商
├── layout_resolver.py    # Plan + Style → list[ResolvedSlide]
├── validator.py          # 布局验证（越界/重叠/最小尺寸）
├── pptx_writer.py        # 纯形状写入器
├── xml_patches.py        # XML 工具函数（阴影/透明/圆角/字体）
├── pipeline_v2.py        # 端到端入口 render_with_schema()
├── icons.py              # 109 个 Lucide SVG 图标管理
├── backgrounds.py        # 背景图生成（Pillow + AI）
├── diagram_native.py     # 程序化图表（5 种类型）
├── material_library.py   # 持久素材库（搜索/添加/复用）
├── models.py             # Pydantic 数据模型
├── llm_client.py         # OpenAI 兼容客户端
├── config.py             # 环境变量配置
├── session.py            # 会话目录 + thinking.jsonl 管理
├── cli.py                # CLI 入口
└── prompts/content.py    # LLM 提示词模板
styles/                   # 样式 JSON 文件
  emerald.json            # 翠绿主题
  blue.json               # 蓝色主题
```

## CLI 参考

```
edupptx gen TOPIC [OPTIONS]          生成演示文稿
edupptx library list|search|stats   素材库管理
edupptx palettes                     列出样式主题
edupptx icons                        列出可用图标
```

`gen` 选项：

| 选项 | 说明 |
|------|------|
| `-r`, `--requirements` | 风格和内容要求（如"适合初中生，简约风"） |
| `-o`, `--output` | 输出目录路径（默认 `output/`） |
| `-p`, `--palette` | 样式主题名称 |
| `-v`, `--verbose` | 详细日志 |
| `--env-file` | .env 文件路径（默认 `.env`） |

## 开发

```bash
# 安装开发依赖
uv sync --group dev

# 运行测试（112 个）
uv run pytest tests/ -v
```

## 文档

- [设计理念](docs/design-philosophy.md) — Agent 架构、三层 Schema 设计、设计决策
- [布局系统](docs/layout-system.md) — EMU 坐标、命名意图、自适应卡片算法
- [风格协商](docs/style-negotiation.md) — 自然语言→JSON patch→样式覆盖

## License

MIT
