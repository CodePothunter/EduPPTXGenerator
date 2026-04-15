# EduPPTX V2: SVG Pipeline 设计文档

## 概述

EduPPTX V2 从原生 python-pptx 渲染管线全面切换到 **SVG 生成管线**。LLM 直接输出整页 SVG 代码（viewBox 0 0 1280 720），以 Bento Grid 卡片式布局为核心设计语言，最终嵌入 .pptx 或直接输出 SVG 文件。

同时引入**策划/设计分离**的两阶段 LLM 工作流、联网搜索、文档输入和多源图片系统。

## 核心设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 输出格式 | 全 SVG（嵌入 .pptx） | 视觉表达力最强，PPT 中可编辑，无限放大不失真 |
| LLM 策略 | 策划/设计分离（2 阶段） | 内容决策与视觉设计解耦，中间可审核 |
| 布局系统 | Bento Grid + layout_hint | 内容驱动布局，灵活卡片组合 |
| 风格系统 | SVG 模板 + 设计指导 prompt | 风格丰富度高，LLM 理解力强 |
| 迁移策略 | 一刀切，新分支 `feature/V2-SVG-optimization` | 干净重构，不保留旧管线 |

## 架构：5 阶段管线

```
用户输入 (主题/文档/URL)
        |
        v
+----------------------------------------------+
|  Phase 0: 输入处理                            |
|  文档解析(PDF/Word/MD) + 联网搜索(可选)        |
+----------------------------------------------+
|  Phase 1: 策划稿 (1 次 LLM)                   |
|  需求理解 + 大纲 + 每页内容 + 布局意图          |
|  输出: PlanningDraft (JSON)                   |
+----------------------------------------------+
|  Phase 2: 素材并行执行                         |
|  图片搜索(Pixabay/Unsplash) + AI图片生成       |
|  背景生成(programmatic) + 图标准备             |
+----------------------------------------------+
|  Phase 3: 设计稿 (N 次 LLM, 并行)             |
|  每页独立 LLM 调用: 策划稿 + 风格参考 + 素材    |
|  输出: 完整 SVG (viewBox 0 0 1280 720)        |
+----------------------------------------------+
|  Phase 4: 后处理校验 + 修复                    |
|  SVG 合法性 + 文本溢出 + 配色一致 + PPT兼容性   |
+----------------------------------------------+
|  Phase 5: 输出组装                             |
|  SVG -> .pptx (每页一个 SVG) + 独立 SVG 文件   |
+----------------------------------------------+
        |
        v
output/session_xxx/
  plan.json          # 策划稿
  slides/            # 每页 SVG
    slide_01.svg
    slide_02.svg
  materials/         # 图片素材
  output.pptx        # 嵌入 SVG 的 PPTX
  thinking.jsonl     # 过程日志
```

**Phase 1 和 Phase 3 之间用户可介入**：CLI `--review` 参数在策划稿生成后暂停，用户审核/编辑 plan.json 后继续。

## Phase 0: 输入处理

### 数据模型

```python
@dataclass
class InputContext:
    topic: str                    # 主题
    source_text: str | None       # 文档提取的原始文本
    research_summary: str | None  # 联网搜索摘要
    requirements: str             # 用户附加要求
```

### 文档解析

| 格式 | 解析方式 |
|------|---------|
| PDF | PyPDF2 文本提取 |
| Word (.docx) | python-docx 段落提取 |
| Markdown | 直接读取 |
| TXT | 直接读取 |

### 联网搜索

使用 Tavily API，环境变量 `TAVILY_API_KEY`。搜索结果作为 `research_summary` 传入 Phase 1 prompt，为 LLM 提供真实、准确的背景信息。

## Phase 1: 策划稿

### PlanningDraft 数据模型

```json
{
  "meta": {
    "topic": "勾股定理",
    "audience": "初中二年级学生",
    "purpose": "数学课堂教学",
    "style_direction": "清新教育风，浅色背景，翠绿强调色",
    "total_pages": 10
  },
  "research_context": "...(联网搜索摘要，供 Phase 3 参考)",
  "pages": [
    {
      "page_number": 1,
      "page_type": "cover",
      "title": "勾股定理",
      "subtitle": "直角三角形中最优美的数学关系",
      "content_points": [],
      "layout_hint": "center_hero",
      "material_needs": {
        "background": "diagonal_gradient",
        "images": [{"query": "geometric right triangle", "source": "search", "role": "hero"}],
        "icons": [],
        "chart": null
      },
      "design_notes": "全屏封面，标题居中，几何图形装饰"
    },
    {
      "page_number": 2,
      "page_type": "toc",
      "title": "目录",
      "content_points": ["定理引入", "几何证明", "代数推导", "实际应用", "课堂练习"],
      "layout_hint": "vertical_list",
      "material_needs": {"background": "subtle_pattern"},
      "design_notes": "简洁目录，带序号"
    },
    {
      "page_number": 4,
      "page_type": "content",
      "title": "勾股定理的表述",
      "content_points": [
        "直角三角形中，两条直角边的平方和等于斜边的平方",
        "公式: a² + b² = c²，其中 c 为斜边",
        "反之亦然：若三边满足此关系，则为直角三角形"
      ],
      "layout_hint": "bento_2col_asymmetric",
      "material_needs": {
        "images": [{"query": "right triangle labeled sides", "source": "ai_generate", "role": "illustration"}]
      },
      "design_notes": "左侧大卡片展示公式和说明，右侧配图"
    },
    {
      "page_number": 7,
      "page_type": "content",
      "title": "生活中的勾股定理",
      "content_points": [
        {"title": "建筑测量", "body": "工人用 3:4:5 验证墙角是否为直角"},
        {"title": "导航定位", "body": "两点间直线距离 = √(Δx² + Δy²)"},
        {"title": "屏幕尺寸", "body": "55寸电视对角线长度的计算"}
      ],
      "layout_hint": "bento_3col",
      "material_needs": {
        "icons": ["ruler", "navigation", "monitor"]
      },
      "design_notes": "三个等分卡片，每个配图标，展示实际应用"
    }
  ]
}
```

### page_type 枚举

`cover` | `toc` | `section` | `content` | `data` | `case` | `closing`

### layout_hint 系统

Bento Grid 布局意图。LLM 在 Phase 1 根据内容特征选择最合适的布局，Phase 3 据此生成 SVG。

| layout_hint | 含义 | 适用场景 |
|---|---|---|
| `center_hero` | 居中大焦点 | 封面、引言、大数字 |
| `vertical_list` | 纵向列表 | 目录、步骤 |
| `bento_2col_equal` | 两等分卡片 | 对比、双主题 |
| `bento_2col_asymmetric` | 非对称两栏 (2:1) | 主内容+辅助 |
| `bento_3col` | 三等分卡片 | 三项并列、数据指标 |
| `hero_top_cards_bottom` | 顶部大卡+底部小卡 | 图表+解释 |
| `cards_top_hero_bottom` | 顶部小卡+底部大卡 | 概述+详情 |
| `mixed_grid` | 自由混合网格 | 复杂信息、案例展示 |
| `full_image` | 全幅图片+文字叠加 | 视觉冲击页 |
| `timeline` | 时间线布局 | 历史、里程碑 |
| `comparison` | 左右对比 | 优劣、Before/After |

## Phase 2: 素材执行

并行执行所有素材生成任务（ThreadPoolExecutor）。

### 图片系统

```python
class ImageProvider(Protocol):
    async def search(self, query: str, count: int = 3) -> list[ImageResult]: ...
    async def generate(self, prompt: str, size: str) -> list[ImageResult]: ...

@dataclass
class ImageResult:
    url: str
    width: int
    height: int
    source: str  # "pixabay" / "unsplash" / "seedream"
    local_path: Path | None  # 下载后的本地路径
```

三个实现：
- `PixabayProvider`: 免费 API，关键词搜索真实照片
- `UnsplashProvider`: 免费 API，高质量图片
- `SeedreamProvider`: AI 图片生成

图片选择策略：策划稿中 `material_needs.images[].source` 指定 `"search"` 或 `"ai_generate"`。

### 背景和图标

- 背景：复用现有程序化背景生成（`backgrounds.py`）
- 图标：复用现有 Lucide SVG 图标（`icons.py`），SVG 中直接内联

## Phase 3: SVG 设计

### LLM 调用结构

每页 SVG 生成携带：
1. **全局设计指导**：从风格模板提取的设计基因，所有页共享
2. **当前页策划稿**：content_points + layout_hint + design_notes
3. **素材 URL/路径**：Phase 2 准备好的图片/图标
4. **Bento Grid 规范**：卡片布局规则和组合示例
5. **SVG 技术约束**：PPT 兼容性要求

并行执行：N 页 SVG 生成可以并行（ThreadPoolExecutor），每页是独立 LLM 调用。

### SVG 风格模板

存放在 `design/style_templates/` 目录下的 SVG 文件。每个模板定义一种视觉风格（配色、字体、卡片样式、装饰语言）。LLM 从模板中提取"设计基因"，在生成每页时保持风格一致。

初始风格集（教育场景为主）：
- `edu_emerald.svg` — 教育翠绿（清新课堂，适合中小学教学）
- `edu_academic.svg` — 学术蓝（论文答辩、学术报告）
- `edu_warm.svg` — 暖色教育（低龄教育、互动课堂）
- `edu_minimal.svg` — 简约白（通用教育、培训讲义）
- `edu_tech.svg` — 科技深色（计算机/STEM 教学）

### SVG 技术约束（PPT 兼容性）

```
强制约束:
- viewBox="0 0 1280 720"
- 禁止 <foreignObject>（PPT 不支持）
- 禁止 CSS animation / @keyframes
- 文本必须用 <text> + <tspan>
- 字体限制: 系统安全字体（微软雅黑/Arial/Helvetica）
- 图片用 <image href="..."> 或内联 base64
- 渐变用 <linearGradient>/<radialGradient> 在 <defs> 中
- 圆角矩形用 <rect rx="..." ry="...">

允许的 SVG 元素:
<svg>, <g>, <defs>, <rect>, <circle>, <ellipse>, <line>, <polyline>,
<polygon>, <path>, <text>, <tspan>, <image>, <use>, <clipPath>,
<mask>, <linearGradient>, <radialGradient>, <stop>, <filter>
```

### Bento Grid 布局规范

Bento Grid 是本项目的核心布局语言，prompt 中需传达以下设计原则：

**核心原则：**
- **内容驱动**：卡片数量和尺寸由信息结构决定，不是固定模板
- **视觉层级**：最重要的信息占据最大的卡片面积
- **呼吸感**：卡片间保持统一间距（20px），避免拥挤
- **圆角统一**：所有卡片使用一致的圆角半径

**布局组合（layout_hint 对应关系）：**

| layout_hint | 卡片组合 | 教育场景举例 |
|---|---|---|
| `center_hero` | 单一大焦点区 | 封面、定义展示、核心公式 |
| `bento_2col_equal` | 两等分 | 概念对比、优缺点分析 |
| `bento_2col_asymmetric` | 2/3 + 1/3 | 主内容+知识点补充 |
| `bento_3col` | 三等分 | 三个知识要点并列 |
| `hero_top_cards_bottom` | 顶部大卡+底部小卡 | 图表/图片+要点解释 |
| `mixed_grid` | 自由混合 | 复杂知识点、案例集合 |

### SVG 生成 Prompt 设计

Phase 3 的 system prompt 需包含以下要素（用自己的语言组织，不使用固定模板）：

1. **角色设定**：信息架构与 SVG 编码专家，擅长教育演示设计
2. **画布约束**：viewBox 0 0 1280 720，固定比例
3. **布局规范**：Bento Grid 原则和当前页对应的布局组合
4. **风格约束**：从风格模板提取的配色、字体、装饰语言
5. **PPT 兼容性**：SVG 技术约束清单（见上方）
6. **教育场景适配**：内容清晰易读、信息层级分明、适合投屏演示

实际 prompt 在此基础上追加：当前页策划稿内容、可用素材 URL、全局设计指导等上下文。prompt 的具体措辞在实现时编写和迭代。

## Phase 4: 后处理校验

用 lxml 解析 SVG，执行检查和自动修复：

| 检查项 | 修复方式 |
|---|---|
| viewBox 不正确 | 自动修正为 `0 0 1280 720` |
| 存在 `<foreignObject>` | 删除 |
| 存在 CSS animation | 删除动画属性 |
| 文本超出 viewBox 边界 | 调整坐标 |
| 字体不在安全列表 | 替换为安全字体 |
| `<image>` 引用失效 | 替换为占位矩形 |
| 配色偏离风格定义 | 替换为最近的 palette 颜色（可选） |

## Phase 5: 输出组装

### SVG -> PPTX

python-pptx 支持 SVG 图片。每页幻灯片 = 1 个全屏 SVG：

```python
prs = Presentation()
prs.slide_width = Emu(12192000)   # 1280px * 9525
prs.slide_height = Emu(6858000)   # 720px * 9525

for svg_path in svg_files:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_picture(
        str(svg_path), Emu(0), Emu(0),
        prs.slide_width, prs.slide_height,
    )
```

### 双输出

- `output.pptx` — 嵌入 SVG 的 PPTX，Office 2016+ 原生支持
- `slides/*.svg` — 独立 SVG 文件，可直接拖入 PPT 或用于其他用途

## 目录结构

```
edupptx/
  __init__.py
  agent.py                # Agent 编排器（5 阶段管线）
  config.py               # 环境变量配置
  cli.py                  # CLI 入口
  session.py              # 会话目录管理
  models.py               # 数据模型
  llm_client.py           # OpenAI 兼容客户端

  input/
    document_parser.py    # PDF/Word/MD 解析
    web_researcher.py     # Tavily 联网搜索

  planning/
    content_planner.py    # 策划稿 LLM 调用
    prompts.py            # 策划阶段 prompt

  materials/
    image_provider.py     # ImageProvider 接口
    pixabay.py
    unsplash.py
    seedream.py
    material_library.py   # 素材缓存库
    backgrounds.py        # 程序化背景
    icons.py              # Lucide 图标

  design/
    svg_generator.py      # SVG LLM 调用 + 并行
    prompts.py            # 设计 prompt (含 Bento Grid)
    style_templates/      # SVG 风格参考模板
      edu_emerald.svg
      edu_academic.svg
      edu_warm.svg
      edu_minimal.svg
      edu_tech.svg

  postprocess/
    svg_validator.py      # SVG 校验 + 自动修复
    svg_sanitizer.py      # PPT 兼容性处理

  output/
    pptx_assembler.py     # SVG -> PPTX 组装
```

## CLI 接口

```bash
# 基础用法
edupptx gen "主题"

# 联网搜索
edupptx gen "主题" --research

# 从文档生成
edupptx gen --file report.pdf "基于报告做汇报"

# 指定风格
edupptx gen "主题" --style edu_tech

# 审核模式（策划稿后暂停）
edupptx gen "主题" --review

# 只出策划稿
edupptx plan "主题"

# 从策划稿渲染
edupptx render plan.json

# 查看风格
edupptx styles

# 素材库管理
edupptx library list
edupptx library search --tags "keyword"
```

## 环境变量

```bash
# LLM (必需)
GEN_MODEL=model-endpoint
GEN_APIKEY=api-key
API_BASE_URL=https://api.endpoint.com/v1

# 图片 AI 生成 (可选)
VISION_GEN_MODEL=image-model
VISION_GEN_APIKEY=image-api-key

# 图片搜索 (可选)
PIXABAY_API_KEY=pixabay-key
UNSPLASH_ACCESS_KEY=unsplash-key

# 联网搜索 (可选)
TAVILY_API_KEY=tavily-key
```

## 迁移策略

一刀切重构，新分支 `feature/V2-SVG-optimization`。

### 删除的模块

| 模块 | 理由 |
|------|------|
| `layout_resolver.py` | 替换为 SVG 生成 |
| `pptx_writer.py` | 替换为 pptx_assembler |
| `style_schema.py` / `style_resolver.py` | 替换为 SVG 风格模板 |
| `style_negotiator.py` | 合并到策划 prompt |
| `diagram_native.py` | 图表由 SVG 直接表达 |
| `validator.py` | 替换为 svg_validator |
| `xml_patches.py` | 不再需要 |
| `pipeline_v2.py` | 替换为新 agent 管线 |

### 保留/复用的模块

| 模块 | 处理方式 |
|------|---------|
| `backgrounds.py` | 保留，SVG 可引用程序化背景 |
| `icons.py` | 保留，SVG 中内联 Lucide 图标 |
| `material_library.py` | 保留并扩展 |
| `llm_client.py` | 保留，扩展图片搜索 API |
| `session.py` | 保留 |
| `config.py` | 扩展新环境变量 |

## 验证计划

1. **单元测试**：每个新模块独立测试（输入解析、策划稿生成、SVG 校验等）
2. **集成测试**：端到端管线测试（主题 -> SVG 文件 -> PPTX）
3. **SVG 兼容性测试**：生成的 SVG 在 PowerPoint、LibreOffice、浏览器中验证
4. **回归对比**：同一主题分别用 V1 和 V2 生成，对比质量
