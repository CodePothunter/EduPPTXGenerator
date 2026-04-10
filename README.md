# EduPPTX

AI Agent 驱动的教育演示文稿生成器。输入主题和要求，Agent 自动完成内容规划、素材准备、排版渲染，一次性输出专业教学 PPT。

面向 AI Agent 设计 —— 可作为 Python 库集成到教学 Agent、备课系统、出题工具中。

## 项目起源

本项目通过逆向分析豆包生成的教学 PPT，提炼出架构，用 Python 完整复现。详见 [设计理念文档](docs/design-philosophy.md)。

## 特性

- **薄 Agent 架构** —— 1 次 LLM 调用完成内容规划 + 素材决策，其余全部确定性执行
- **并行素材执行** —— 背景图、图表、AI 插图并发生成，结果进入持久化素材库
- **思考过程可观测** —— `thinking.jsonl` 记录 Agent 决策轨迹，便于人类和其他 Agent 检查
- **6 套配色方案** —— emerald / blue / violet / amber / rose / slate（基于 Tailwind 色板）
- **109 个 Lucide 图标** —— 自动着色匹配主题，SVG+PNG 双格式嵌入
- **卡片式布局** —— 圆角阴影卡片 + 半透明蒙版，10 种页面模板
- **教案备注** —— 每页自动生成口语化教学脚本（Speaker Notes）
- **持久素材库** —— 素材跨会话复用，避免重复生成

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

# 可选：AI 背景图生成
VISION_GEN_MODEL=your-image-model
VISION_GEN_APIKEY=your-image-key
```

### 生成 PPT

```bash
# 最简用法，输出到 output/session_xxx/ 目录
uv run edupptx gen "勾股定理"

# 指定要求和配色，输出到指定目录
uv run edupptx gen "光合作用" -r "适合高中生，强调实验部分" -p blue -o output/

# 查看可用配色
uv run edupptx palettes

# 查看可用图标
uv run edupptx icons
```

## 架构概览

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

详细架构设计见 [docs/design-philosophy.md](docs/design-philosophy.md)。

## 思考过程可观测

Agent 每次运行都会在会话目录输出 `thinking.jsonl`，记录规划决策的完整轨迹：

```jsonl
{"step": "plan", "topic": "勾股定理", "slide_count": 12, "ts": "2026-04-10T10:00:00"}
{"step": "material_decision", "slide": 3, "background": "geometry_abstract", "icon": "triangle"}
{"step": "material_execution", "type": "background", "key": "geometry_abstract", "source": "library"}
{"step": "render", "slide": 3, "layout": "definition", "duration_ms": 45}
```

这个文件的价值：
- **人类可读** —— 出错时可以快速定位是规划阶段还是渲染阶段的问题
- **Agent 可读** —— 外层编排 Agent 可以解析 thinking.jsonl，判断是否需要干预或重试
- **调试友好** —— 结合 `plan.json` 可以复现任意会话，修改 plan 后重新渲染

## 素材库

Agent 维护一个跨会话的持久素材库，生成过的背景图、图表、AI 插图会自动入库复用。

```bash
# 列出素材库中的所有素材
uv run edupptx library list

# 搜索素材
uv run edupptx library search "几何"

# 查看素材库统计
uv run edupptx library stats
```

素材库位于项目根目录的 `materials_library/`，结构如下：

```
materials_library/
├── backgrounds/    # 背景图（程序生成 + AI 生图）
├── diagrams/       # 图表（流程图、示意图）
└── illustrations/  # AI 插图
```

## 会话输出结构

每次运行 `edupptx gen` 都会创建一个独立的会话目录：

```
output/
└── session_20260410_100000_勾股定理/
    ├── thinking.jsonl   # Agent 决策轨迹
    ├── plan.json        # 结构化内容方案
    ├── materials/       # 本次用到的素材快照
    ├── slides/          # 逐页渲染中间产物
    └── output.pptx      # 最终输出
```

## 作为 Python 库使用

### 主要 API

```python
from edupptx import run_agent

# 运行 Agent，返回会话目录路径
session_dir = run_agent("勾股定理")
session_dir = run_agent(
    topic="光合作用",
    requirements="适合高中生，强调实验部分",
    palette="emerald",
    output_dir="output/",
)
```

### 更多控制

```python
from edupptx import PPTXAgent

agent = PPTXAgent()

# 单独执行规划阶段，可以检查和修改方案
plan = agent.plan("勾股定理", "适合初中生")
for slide in plan.slides:
    print(f"[{slide.type}] {slide.title}")

# 修改方案后再执行后续阶段
session_dir = agent.execute(plan, output_dir="output/")
```

### 向后兼容

```python
from edupptx import generate

# 原有 API 保持不变
path = generate("勾股定理")
path = generate(
    topic="光合作用",
    requirements="适合高中生，强调实验部分",
    palette="emerald",
    output_path="photosynthesis.pptx",
)
```

### 纯数据驱动（不调用 LLM）

直接构造 `PresentationPlan`，跳过 LLM 规划阶段：

```python
from edupptx.models import PresentationPlan, SlideContent, SlideCard
from edupptx.generator import generate_from_plan

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

path = generate_from_plan(plan)
```

## 页面类型

| 类型 | 用途 | 卡片数 | 特殊字段 |
|------|------|--------|---------|
| `cover` | 封面 | 3 | subtitle, formula |
| `lead_in` | 情境引入 | 3-4 | subtitle |
| `definition` | 核心定义 | 2-4 | - |
| `content` | 通用内容 | 2-4 | - |
| `history` | 历史背景 | 3-4 | - |
| `proof` | 推导证明 | 2-3 | formula |
| `example` | 例题讲解 | 1-2 | - |
| `exercise` | 练习题 | 2-3 | - |
| `answer` | 答案揭晓 | 2-3 | - |
| `summary` | 总结回顾 | 3-5 | footer |
| `extension` | 延伸思考 | 2-3 | footer |
| `closing` | 结束页 | 0 | subtitle |

## 配色方案

| 方案 | 主色 | 适用场景 |
|------|------|---------|
| `emerald` | #059669 | 数学、理科、通用 |
| `blue` | #2563EB | 科技、工程 |
| `violet` | #7C3AED | 文学、艺术、创意 |
| `amber` | #D97706 | 历史、社科 |
| `rose` | #E11D48 | 音乐、美术 |
| `slate` | #334155 | 商务、正式场合 |

## 项目结构

```
edupptx/
├── __init__.py           # 公开 API: run_agent(), PPTXAgent, generate()
├── agent.py              # Agent 编排器（规划 + 并行素材执行）
├── generator.py          # 向后兼容编排器
├── content_planner.py    # LLM 内容规划 + 素材决策
├── design_system.py      # 6 套配色 + 字体定义
├── layout_engine.py      # 10 种槽位模板 → EMU 坐标
├── renderer.py           # python-pptx + XML 补丁渲染
├── icons.py              # 109 个 Lucide SVG 图标管理
├── backgrounds.py        # 背景管理器
├── materials.py          # 素材库管理
├── models.py             # Pydantic 数据模型
├── llm_client.py         # OpenAI 兼容客户端
├── config.py             # 环境变量配置
├── cli.py                # CLI 入口
└── prompts/content.py    # LLM 提示词模板
```

## CLI 参考

```
edupptx gen TOPIC [OPTIONS]          生成演示文稿（输出到会话目录）
edupptx library list|search|stats   素材库管理
edupptx palettes                     列出配色方案
edupptx icons                        列出可用图标
```

`gen` 选项：

| 选项 | 说明 |
|------|------|
| `-r`, `--requirements` | 附加要求（如"适合初中生"） |
| `-o`, `--output` | 输出目录路径（默认 `output/`） |
| `-p`, `--palette` | 配色方案名称 |
| `-v`, `--verbose` | 详细日志 |
| `--env-file` | .env 文件路径（默认 `.env`） |

## 开发

```bash
# 安装开发依赖
uv sync --group dev

# 运行测试
uv run pytest tests/ -v

# 详细日志模式生成
uv run edupptx -v gen "测试主题"
```

## 文档

- [设计理念](docs/design-philosophy.md) —— 逆向分析、架构决策、技术选型
- [布局系统](docs/layout-system.md) —— EMU 坐标系、槽位模板、卡片计算

## License

MIT
