# EduPPTX

AI 驱动的教育演示文稿生成器。输入主题和要求，自动生成带有教案备注的专业 PPT。

面向 AI Agent 设计 —— 可作为 Python 库集成到教学 Agent、备课系统、出题工具中。

## 项目起源

本项目通过逆向分析豆包生成的教学 PPT，提炼出四层生成管线架构，用 Python 完整复现。详见 [设计理念文档](docs/design-philosophy.md)。

## 特性

- **LLM 内容规划** —— 自动生成 10-15 页教学结构（引入、定义、例题、练习、总结）
- **6 套配色方案** —— emerald / blue / violet / amber / rose / slate（基于 Tailwind 色板）
- **109 个 Lucide 图标** —— 自动着色匹配主题，SVG+PNG 双格式嵌入
- **卡片式布局** —— 圆角阴影卡片 + 半透明蒙版，10 种页面模板
- **教案备注** —— 每页自动生成口语化教学脚本（Speaker Notes）
- **三级背景系统** —— 缓存库 → 程序生成 → AI 生图，结果自动缓存
- **Agent 友好** —— 可拦截修改 Plan 后再渲染，支持 `generate_from_plan()`

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
# 最简用法
uv run edupptx gen "勾股定理"

# 指定要求和配色
uv run edupptx gen "光合作用" -r "适合高中生，强调实验部分" -p blue -o biology.pptx

# 查看可用配色
uv run edupptx palettes

# 查看可用图标
uv run edupptx icons
```

## 作为 Python 库使用

### 基础用法

```python
from edupptx import generate

# 一行生成
path = generate("勾股定理")

# 带参数
path = generate(
    topic="光合作用",
    requirements="适合高中生，强调实验部分",
    palette="emerald",
    output_path="photosynthesis.pptx",
)
```

### Agent 集成

Agent 可以生成 Plan、检查修改、再渲染 —— 完全控制内容：

```python
from edupptx.config import Config
from edupptx.content_planner import ContentPlanner
from edupptx.generator import generate_from_plan
from edupptx.llm_client import LLMClient
from edupptx.models import SlideCard, SlideContent

# 1. 用 LLM 生成内容方案
config = Config.from_env()
llm = LLMClient(config)
planner = ContentPlanner(llm)
plan = planner.plan("勾股定理", "适合初中生")

# 2. Agent 可以检查和修改方案
for slide in plan.slides:
    print(f"[{slide.type}] {slide.title} ({len(slide.cards)} cards)")

# 3. 插入自定义页面
plan.slides.insert(-1, SlideContent(
    type="content",
    title="趣味数学：勾股数",
    cards=[
        SlideCard(icon="sparkles", title="经典三元组", body="(3,4,5) (5,12,13) (8,15,17)"),
        SlideCard(icon="search", title="发现规律", body="尝试找出更多满足 a²+b²=c² 的整数组合"),
    ],
    notes="这是一个扩展探索环节，鼓励学生自主发现勾股数的规律。",
))

# 4. 渲染修改后的方案
path = generate_from_plan(plan, output_path="agent_modified.pptx")
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

## 架构概览

```
用户输入: 主题 + 要求
         │
         ▼
┌─────────────────────────────────────────────┐
│  Phase 1: ContentPlanner (LLM)              │
│  主题 → 结构化 JSON (PresentationPlan)       │
│  10-15 页教学结构 + 图标选择 + 教案备注       │
├─────────────────────────────────────────────┤
│  Phase 2: DesignSystem                      │
│  配色方案 → 9 个设计令牌 (颜色/字体/字号)     │
├─────────────────────────────────────────────┤
│  Phase 3: BackgroundManager                 │
│  缓存库 → 程序生成(Pillow) → AI 生图         │
│  结果自动缓存到 backgrounds_cache/           │
├─────────────────────────────────────────────┤
│  Phase 4: PresentationRenderer              │
│  LayoutEngine (槽位坐标) + python-pptx       │
│  + XML 补丁 (阴影/透明度/SVG图标)            │
└─────────────────────────────────────────────┘
         │
         ▼
      output.pptx
```

详细架构设计见 [docs/design-philosophy.md](docs/design-philosophy.md)。

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
├── generator.py          # 主编排器：topic → .pptx
├── content_planner.py    # LLM 内容规划
├── design_system.py      # 6 套配色 + 字体定义
├── layout_engine.py      # 10 种槽位模板 → EMU 坐标
├── renderer.py           # python-pptx + XML 补丁渲染
├── icons.py              # 109 个 Lucide SVG 图标管理
├── backgrounds.py        # 三级背景管理器
├── models.py             # Pydantic 数据模型
├── llm_client.py         # OpenAI 兼容客户端
├── config.py             # 环境变量配置
├── cli.py                # CLI 入口
└── prompts/content.py    # LLM 提示词模板
```

## CLI 参考

```
edupptx gen TOPIC [OPTIONS]     生成演示文稿
edupptx palettes                列出配色方案
edupptx icons                   列出可用图标
```

`gen` 选项：

| 选项 | 说明 |
|------|------|
| `-r`, `--requirements` | 附加要求（如"适合初中生"） |
| `-o`, `--output` | 输出文件路径 |
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
