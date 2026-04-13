# 设计理念

EduPPTX 的架构源于对豆包 AI 生成 PPT 的逆向工程分析，后演进为 Schema 驱动的三层管线。本文记录关键发现、设计决策和技术选型背后的思考。

## 1. 逆向分析：豆包是怎么做的

我们拆解了一份豆包生成的教学 PPT（"探索勾股定理的奥秘"，15 页），得出以下关键发现：

### 1.1 空白 Layout 策略

豆包的 15 张 slide 全部引用同一个 `slideLayout12`，这是一个**完全空白**的 layout（仅 3 行 XML，无任何 placeholder）。每个形状都是**绝对定位**手动放置的 `<p:sp>` 和 `<p:pic>`。

这告诉我们：**模板化 layout 不适合动态内容生成**。卡片数量、文本长度、图标数量都在变化，placeholder 的固定位槽无法适应。正确的做法是用坐标计算引擎动态生成布局。

### 1.2 SVG+PNG 双轨图标

每个图标由一对文件组成（如 `image3.svg` + `image2.png`）。XML 中使用 `asvg:svgBlip` 扩展嵌入 SVG，同时保留 PNG 作为低版本 PowerPoint 的降级方案。

```xml
<a:blip r:embed="rId3">  <!-- PNG (fallback) -->
  <a:extLst>
    <a:ext uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}">
      <asvg:svgBlip r:embed="rId4"/>  <!-- SVG (modern) -->
    </a:ext>
  </a:extLst>
</a:blip>
```

这是 Office 2019+ 的标准做法。我们在 `xml_patches.py` 中复现了这个模式。

### 1.3 Tailwind 色系

分析所有 slide 的颜色值，完全匹配 Tailwind CSS 的 emerald 色系：

| 用途 | 色值 | Tailwind 对应 |
|------|------|--------------|
| 蒙版 | `#F0FDF4` | emerald-50 |
| 图标 | `#10B981` | emerald-500 |
| 强调 | `#059669` | emerald-600 |
| 阴影 | `#6EE7B7` | emerald-300 |
| 主文字 | `#1F2937` | gray-800 |
| 副文字 | `#4B5563` | gray-600 |

我们直接采用了相同策略，将配色定义在 `styles/*.json` 中，每套主题一个文件。

### 1.4 坐标模板化

分析多页 XML 中形状的 `<a:off>` 和 `<a:ext>` 值，发现同类型元素的 Y 坐标几乎完全一致。这说明布局引擎使用了**预定义的槽位模板**，根据 slide type 选择模板，然后按卡片数量等分宽度填入内容。

### 1.5 媒体文件结构

| 类型 | 数量 | 用途 |
|------|------|------|
| JPEG | 9 | 每页一张全屏背景图（AI 生成的抽象学术场景） |
| SVG | 33 | Lucide 风格线性图标（48x48 viewBox） |
| PNG | 34 | 同一图标的 PNG 降级版本 |

## 2. Agent 架构

我们没有采用固定的四层管线，而是构建了一个 **薄 Agent**，由 5 个阶段串联，其中内容规划和素材执行可以利用 LLM 做智能决策：

```
用户输入 (主题 + 要求)
        │
        ▼
┌──────────────────────────────────────┐
│  Phase 1: 内容规划 (1 次 LLM 调用)   │  → PresentationPlan
├──────────────────────────────────────┤
│  Phase 2: 风格协商 (1 次 LLM 调用)   │  → ResolvedStyle
├──────────────────────────────────────┤
│  Phase 3: 素材决策 (N 次并行 LLM)    │  → 每页的背景/图表/插图决策
├──────────────────────────────────────┤
│  Phase 4: 素材执行 (并行，无 LLM)    │  → 背景图 + AI 插图文件
├──────────────────────────────────────┤
│  Phase 5: Schema 渲染               │  → .pptx 文件
│  StyleSchema → ResolvedStyle         │
│  → ResolvedSlide[] → PptxWriter      │
└──────────────────────────────────────┘
        │
        ▼
output/session_xxx/
├── thinking.jsonl    # 每阶段日志
├── plan.json         # 内容规划结果
├── style_schema.json # 协商后的样式
├── materials/        # 背景图 + 插图
├── slides/           # 每页状态快照
└── output.pptx       # 最终文件
```

### Phase 1: 内容规划

一次 LLM 调用，输出完整的 `PresentationPlan`（JSON）。提示词中包含：
- 17 种 slide 类型的定义和卡片数量约束
- 109 个可用图标名（LLM 只能从中选择，避免虚构）
- 推荐的教学结构模板（引入→定义→例题→练习→总结）
- 配色-主题映射规则（数学→emerald，文学→violet）

### Phase 2: 风格协商

详见 [style-negotiation.md](style-negotiation.md)。用户的自然语言要求（如"简约商务风"）被 LLM 转译为 JSON 补丁，深度合并到基础 StyleSchema 上。

### Phase 3: 素材决策

对每张非简单类型的 slide（跳过 big_quote/closing/section），发起一次小型 LLM 调用，决定：
- 背景风格（diagonal_gradient/radial_gradient/geometric_circles/geometric_triangles）
- 是否需要图表（5 种类型），或
- 是否需要 AI 插图（描述、风格、锚点、缩放）

使用 4 个线程并行执行。背景风格额外做了**强制轮转**（`_BG_STYLES[idx % 4]`），防止 LLM 每页都选同一种。

### Phase 4: 素材执行

纯执行，无 LLM 调用。并行生成：
- **背景图**：Pillow 程序生成（渐变+装饰几何体），或从素材库缓存复用
- **AI 插图**：调用图片生成 API（Seedream），自动选择最佳分辨率比例

三级降级策略：
```
缓存库命中 → 直接复用（0ms）
           → 程序生成（Pillow，~50ms）
           → AI 生图 + 压缩 + 缓存（~5s）
           → 无 API key 则跳过
```

### Phase 5: Schema 渲染

详见下文"三层 Schema 架构"。

## 3. 三层 Schema 架构

这是渲染管线的核心设计。受 CSS 启发，将所有视觉决策从代码中抽离到 JSON 文件：

```
styles/emerald.json ──→ style_resolver ──→ ResolvedStyle
                                               │
PresentationPlan ───────+                      │
                        │                      ▼
                        +→ layout_resolver → list[ResolvedSlide]
                                                  │
                                                  ▼
                                           validator (clamp+warn)
                                                  │
                                                  ▼
                                           pptx_writer → .pptx
```

### Layer 1: StyleSchema（样式表）

JSON 文件，三层 token 层级：

- **global**: palette（9 色调色板）、fonts（标题/正文字体）、background（背景配置）
- **semantic**: 字号、颜色引用（`"palette.accent"` 点路径）、卡片圆角、阴影参数
- **layout**: 命名意图（margin=comfortable/tight/spacious, content_density=compact/standard/relaxed），不用具体数值
- **decorations**: 装饰元素 boolean 开关（下划线、面板、引用栏、章节菱形等）

添加新主题 = 创建一个 JSON 文件，零代码。

### Layer 2: 解析管线

三个纯函数串联：

1. **style_resolver**: 解引用 `palette.accent` → `#059669`，解析 `comfortable` → 具体 EMU 值 → 输出 `ResolvedStyle`
2. **layout_resolver**: `PresentationPlan` + `ResolvedStyle` → `list[ResolvedSlide]`，每个 shape 都带完整 EMU 坐标
3. **validator**: 越界 clamp、重叠检测、最小尺寸验证。只警告不崩溃

### Layer 3: PptxWriter

~200 行的循环，读取 `ResolvedShape` 字段调用 python-pptx API。不做任何决策。XML 补丁（阴影、透明度、SVG 嵌入、圆角、CJK 字体）委托给 `xml_patches.py`。

### 为什么这样分

- **换 JSON 换风格**: 不需要改 Python 就能完全改变视觉风格
- **EMU 坐标而非比例**: 固定画布不需要响应式，命名意图解析为 EMU 比比例转换更直接
- **Writer 不做决策**: 渲染层是纯机械映射，所有智能在 resolver 层完成

## 4. 核心设计决策

### 4.1 为什么不用 python-pptx 的 Layout/Placeholder

python-pptx 支持 slide layout 中的 placeholder，但我们不用：

1. **卡片数量动态变化** — 同一种 slide type，可能有 2、3、4 张卡片
2. **精细控制需求** — 阴影、透明度、SVG 嵌入都需要操控 XML
3. **复现分析** — 豆包自己也没用 placeholder

选择了**绝对定位 + 坐标计算**，与豆包一致。

### 4.2 为什么用 Pydantic 做中间表示

`PresentationPlan` 是 LLM 输出和渲染器之间的桥梁：

1. **LLM 输出校验** — Pydantic 的 `model_validate()` 自动纠正或报错
2. **类型安全** — IDE 补全，减少拼写错误
3. **序列化/反序列化** — Agent 可以把 plan 保存为 JSON，后续加载修改再渲染

### 4.3 自适应卡片布局

卡片区域高度固定（200pt），但卡片内部布局会根据可用空间自动选择三种模式：

| 模式 | 条件 | 图标 | 标题 | Body 高度 |
|------|------|------|------|-----------|
| Full | body_h >= 38pt | 48pt | 30pt | 充足 |
| Compact | body_h >= 38pt（compact） | 32pt | 24pt | 中等 |
| Minimal | 以上都不满足 | 无 | 24pt | 最大化 |

当 LLM 风格协商选择了 `relaxed` 密度（更大的内部间距），full 模式的 body 区域可能不足 30pt。此时布局引擎自动降级到 compact 或 minimal 模式，保证文字始终有足够空间。这个机制使得任何样式组合都能正确渲染。

### 4.4 XML 补丁而非原生 API

python-pptx 不支持以下特性，通过 lxml 直接操作 XML 实现：

| 特性 | XML 元素 |
|------|---------|
| 卡片阴影 | `<a:outerShdw>` |
| 蒙版透明度 | `<a:alpha>` |
| SVG 图标嵌入 | `<asvg:svgBlip>` |
| 圆角半径 | `<a:gd name="adj">` |
| CJK 字体 | `<a:ea>`, `<a:cs>` |

"高层 API + 底层补丁" 的混合方式兼顾开发效率和视觉效果。

### 4.5 LLM 提示词设计

**约束化输出** — 明确指定 JSON schema、每种 slide type 的卡片数量范围、可用图标名单。约束越明确，LLM 输出越稳定。

**教学结构引导** — 提示词中给出推荐的页面顺序（引入→定义→例题→练习→总结），但不强制。

**图标目录注入** — 109 个可用图标名直接写进提示词，LLM 只能从中选择。无效图标会被自动替换为 `circle` fallback。

## 5. 与豆包生成结果的对比

| 维度 | 豆包 | EduPPTX |
|------|------|---------|
| 背景图 | AI 生成（高质量） | 三级策略（缓存→程序→AI） |
| 图标 | 自定义 SVG | Lucide 开源图标库（109 个） |
| 布局精度 | 像素级精调 | Schema 驱动（命名意图→EMU） |
| 风格控制 | 固定 | 自然语言风格协商 |
| 渲染方式 | 原始 OOXML 拼装 | python-pptx + XML 补丁 |
| 可扩展性 | 闭源 | JSON 换风格，函数注册换布局 |

## 6. 扩展点

### 添加新主题

在 `styles/` 目录创建 JSON 文件即可。结构参考 `styles/emerald.json`。无需改代码。

### 添加新 slide 类型

1. 在 `layout_resolver.py` 写 resolver 函数
2. 注册到 `_SLIDE_RESOLVERS` dict
3. 在 `prompts/content.py` 的约束表中添加类型

### 添加新图标

将 24x24 的 SVG 放入 `assets/icons/` 目录，文件名即图标名。
