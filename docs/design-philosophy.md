# 设计理念

EduPPTX 的架构源于对豆包 AI 生成 PPT 的逆向工程分析。本文记录关键发现、设计决策和技术选型背后的思考。

## 1. 逆向分析：豆包是怎么做的

我们拆解了一份豆包生成的教学 PPT（"探索勾股定理的奥秘"，15 页），得出以下关键发现：

### 1.1 空白 Layout 策略

豆包的 15 张 slide 全部引用同一个 `slideLayout12`，这是一个**完全空白**的 layout（仅 3 行 XML，无任何 placeholder）。这意味着它完全绕过了 PowerPoint 的 placeholder 体系，每个形状都是**绝对定位**手动放置的 `<p:sp>` 和 `<p:pic>`。

这告诉我们：**模板化 layout 不适合动态内容生成**。卡片数量、文本长度、图标数量都在变化，placeholder 的固定位槽无法适应。正确的做法是用坐标计算引擎动态生成布局。

### 1.2 SVG+PNG 双轨图标

每个图标都由一对文件组成（如 `image3.svg` + `image2.png`）。XML 中使用 `asvg:svgBlip` 扩展嵌入 SVG，同时保留 PNG 作为低版本 PowerPoint 的降级方案。

```xml
<a:blip r:embed="rId3">  <!-- PNG (fallback) -->
  <a:extLst>
    <a:ext uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}">
      <asvg:svgBlip r:embed="rId4"/>  <!-- SVG (modern) -->
    </a:ext>
  </a:extLst>
</a:blip>
```

这是 Office 2019+ 的标准做法。我们在 `renderer.py` 的 `_patch_svg_blip()` 中复现了这个模式。

### 1.3 Tailwind 色系

分析所有 slide 的颜色值，我们发现完全匹配 Tailwind CSS 的 emerald 色系：

| 用途 | 色值 | Tailwind 对应 |
|------|------|--------------|
| 蒙版 | `#F0FDF4` | emerald-50 |
| 图标 | `#10B981` | emerald-500 |
| 强调 | `#059669` | emerald-600 |
| 阴影 | `#6EE7B7` | emerald-300 |
| 主文字 | `#1F2937` | gray-800 |
| 副文字 | `#4B5563` | gray-600 |

这说明豆包的设计系统基于 Tailwind 色板。我们直接采用了相同策略，提供 6 套 Tailwind 色系配色方案。

### 1.4 坐标模板化

分析多页 XML 中形状的 `<a:off>` 和 `<a:ext>` 值，发现同类型元素的 Y 坐标几乎完全一致。这说明布局引擎使用了**预定义的槽位模板**，根据 slide type 选择模板，然后按卡片数量等分宽度填入内容。

### 1.5 媒体文件结构

| 类型 | 数量 | 用途 |
|------|------|------|
| JPEG | 9 | 每页一张全屏背景图（AI 生成的抽象学术场景） |
| SVG | 33 | Lucide 风格线性图标（48x48 viewBox） |
| PNG | 34 | 同一图标的 PNG 降级版本 |

每个 JPEG 背景图 100-460KB，分辨率 2048x1152。全部铺满画布后叠加半透明蒙版。

## 2. 四层管线架构

基于逆向分析，我们设计了四层分离的生成管线：

```
┌─────────────────────────────────────────────────┐
│  Layer 1: 内容规划 (ContentPlanner)              │
│  输入: 主题 + 要求                               │
│  输出: PresentationPlan (结构化 JSON)             │
│  核心: LLM + 约束化提示词                         │
├─────────────────────────────────────────────────┤
│  Layer 2: 设计令牌 (DesignSystem)                │
│  输入: 配色方案名称                               │
│  输出: DesignTokens (9 色值 + 字体 + 字号)        │
│  核心: 预定义的 Tailwind 色系映射                  │
├─────────────────────────────────────────────────┤
│  Layer 3: 布局计算 (LayoutEngine)                │
│  输入: slide type + 卡片数                        │
│  输出: SlotLayout (每个元素的 EMU 坐标)           │
│  核心: 参数化的槽位模板                           │
├─────────────────────────────────────────────────┤
│  Layer 4: 渲染输出 (Renderer)                    │
│  输入: PresentationPlan + Backgrounds + Tokens   │
│  输出: .pptx 文件                                │
│  核心: python-pptx + lxml XML 补丁               │
└─────────────────────────────────────────────────┘
```

### 为什么分四层

**关注点分离** —— 每层只处理一件事，任何一层都可以独立替换：
- 换 LLM？只改 Layer 1 的 prompt 和 client
- 换配色？只改 Layer 2 的 palette 定义
- 换布局风格？只改 Layer 3 的坐标模板
- 换输出格式（比如 PDF）？只改 Layer 4

**Agent 可拦截** —— Agent 可以在 Layer 1 输出后拦截 `PresentationPlan`，修改内容再交给后续层渲染。这是纯模板方案做不到的。

## 3. 核心设计决策

### 3.1 为什么不用 python-pptx 的 Layout/Placeholder

python-pptx 支持 slide layout 中的 placeholder（标题占位符、内容占位符等），这是编辑已有模板的标准方式。但我们不用，原因：

1. **卡片数量动态变化** —— 同一种 slide type，可能有 2、3、4、5 张卡片。placeholder 数量是固定的
2. **精细控制需求** —— 阴影、透明度、SVG 嵌入都需要操控 XML，placeholder 反而增加了一层间接性
3. **复现分析** —— 豆包自己也没用 placeholder，说明这个选择是经过验证的

我们选择了**绝对定位 + 坐标计算**的方式，与豆包一致。代价是需要自己管理所有元素的位置，但 `LayoutEngine` 的槽位模板已经封装了这个复杂性。

### 3.2 为什么用 Pydantic 做中间表示

`PresentationPlan` 是 LLM 输出和渲染器之间的桥梁。用 Pydantic 而不是 dict 的原因：

1. **LLM 输出校验** —— LLM 经常返回不合规的 JSON（多余字段、类型错误）。Pydantic 的 `model_validate()` 自动纠正或报错
2. **类型安全** —— IDE 补全，减少拼写错误
3. **序列化/反序列化** —— Agent 可以把 plan 保存为 JSON，后续加载修改再渲染
4. **文档即代码** —— Field description 就是字段文档

### 3.3 三级背景系统

为什么不直接用 AI 生图或纯程序生成？

```
优先级: 缓存库 → 程序生成 → AI 生图
```

**缓存库优先** —— 最快（0ms），且积累越多效果越好。每次生成的背景都会自动缓存，标记 topic tags 和 palette，下次同类主题可以直接复用。

**程序生成兜底** —— 不依赖外部 API，离线也能用。Pillow 生成渐变、几何图案的风格干净现代，足够作为教学 PPT 背景。

**AI 生图锦上添花** —— 需要额外 API 配置，但生成的背景更贴合主题。结果同样缓存，下次不重复调用。

这个三级降级策略保证了：
- 无 API 也能跑（程序生成兜底）
- 有 API 效果更好（AI 生图 + 缓存复用）
- 用得越多越快（缓存积累）

### 3.4 XML 补丁而非原生 API

python-pptx 不支持以下特性，我们通过 lxml 直接操作 XML 来实现：

| 特性 | 补丁方法 | XML 元素 |
|------|---------|---------|
| 卡片阴影 | `_patch_card_shadow()` | `<a:outerShdw>` |
| 蒙版透明度 | 内联在 `_add_overlay()` | `<a:alpha>` |
| SVG 图标嵌入 | `_patch_svg_blip()` | `<asvg:svgBlip>` |
| 圆角半径 | `_patch_corner_radius()` | `<a:gd name="adj">` |
| CJK 字体 | 内联在 `_set_font()` | `<a:ea>`, `<a:cs>` |

这种 "高层 API + 底层补丁" 的混合方式兼顾了开发效率和视觉效果。大部分元素用 python-pptx 的 `add_shape()` / `add_textbox()` 创建，少数高级效果用 XML 补丁。

### 3.5 LLM 提示词设计

内容规划的提示词（`prompts/content.py`）遵循几个原则：

**约束化输出** —— 明确指定 JSON schema、每种 slide type 的卡片数量范围、可用图标名单。约束越明确，LLM 输出越稳定。

**教学结构引导** —— 提示词中给出推荐的页面顺序（引入→定义→例题→练习→总结），但不强制。LLM 可以根据主题调整。

**图标目录注入** —— 把 109 个可用图标名直接写进提示词，LLM 只能从中选择。避免了 LLM 虚构不存在的图标名。

**配色自动选择** —— 提示词中包含配色-主题映射规则（数学→emerald，文学→violet），LLM 根据主题自动选择。

## 4. 坐标系与布局系统

详见 [layout-system.md](layout-system.md)。

### 核心概念

- **EMU (English Metric Units)** —— PowerPoint 的内部坐标系。1pt = 12700 EMU，1 inch = 914400 EMU
- **画布尺寸** —— 12,192,000 x 6,858,000 EMU（标准 16:9 宽屏）
- **槽位模板** —— 每种 slide type 对应一个 `SlotLayout`，定义了所有元素的坐标
- **自适应卡片列** —— `_make_card_columns(n)` 根据卡片数等分内容区宽度

### 页面结构

每张 slide 的渲染分为固定层和内容层：

```
固定层 (每张 slide 都有):
  ├── 背景图 (JPEG, 全屏铺满)
  └── 蒙版层 (矩形, 半透明, 92% 不透明度)

内容层 (由 SlotLayout 定义):
  ├── 标题 (顶部, 36pt, 加粗)
  ├── 副标题 (标题下方, 20pt, 可选)
  ├── 卡片区域 (N 列等宽卡片)
  │   ├── 卡片容器 (圆角矩形 + 阴影)
  │   ├── 图标 (居中, 60pt)
  │   ├── 卡片标题 (居中, 16pt, 加粗)
  │   └── 卡片正文 (13pt, 副色)
  ├── 公式栏 (底部, 浅色背景, 可选)
  └── 页脚文字 (底部, 居中, 可选)
```

## 5. 与豆包生成结果的差异

| 维度 | 豆包 | EduPPTX |
|------|------|---------|
| 背景图 | AI 生成（高质量） | 三级策略（可选 AI 生图） |
| 图标 | 自定义 SVG（语义匹配强） | Lucide 开源图标库（覆盖广） |
| 字体 | Noto Sans SC（内嵌？） | Noto Sans SC（需系统安装） |
| 布局精度 | 像素级精调 | 槽位模板（80% 还原度） |
| 渲染方式 | 原始 OOXML 拼装 | python-pptx + XML 补丁 |
| 内容质量 | 豆包专有模型 | 取决于用户配置的 LLM |

**主要差距**在于：
1. 背景图的视觉丰富度（可通过配置 AI 生图 API 缩小差距）
2. 文本排版的精细度（动态字号调整、溢出检测等尚未实现）
3. 图标的语义匹配度（Lucide 是通用图标库，非专门教育向）

## 6. 扩展点

### 已预留的扩展接口

- **多语言支持** —— `PresentationPlan.language` 字段已定义，prompt 模板可按语言切换
- **自定义配色** —— `DesignTokens` 是 dataclass，可直接构造自定义配色传入
- **自定义布局** —— `_LAYOUT_MAP` 支持注册新的 layout 函数
- **自定义图标** —— 将 SVG 放入 `assets/icons/` 即可使用

### 未来可能的增强

- 更多布局模板（时间线、对比表、流程图）
- 图表嵌入（matplotlib/echarts → 图片 → slide）
- 多轮对话式 PPT 编辑（"把第 3 页的例题换成..."）
- MCP Server 接口（供 Claude Code 等工具直接调用）

## 6. 三层 Schema 架构（v2 管线）

v1 管线的核心问题：所有视觉决策硬编码在 Python 中。renderer.py 同时扮演布局引擎、样式解析器和形状写入器，771 行代码无法通过数据驱动控制。

v2 管线引入 "CSS for PPTX" 理念，将渲染拆分为三层：

### 6.1 Layer 1: StyleSchema（样式表）

JSON 文件，三层 token 层级：
- **global**: palette（调色板）、fonts（字体）、background（背景配置）
- **semantic**: 字号、颜色引用（`"palette.accent"` 点路径）、阴影参数
- **layout**: 命名意图（`"comfortable"` → 具体 EMU 值），不用比例或绝对坐标
- **decorations**: 装饰元素开关（下划线、面板、引用栏等 boolean flag）

### 6.2 Layer 2: Resolution Pipeline（解析管线）

纯函数链：
1. `style_resolver`: 解引用 palette ref + 解析 named intent → ResolvedStyle
2. `layout_resolver`: Plan + ResolvedStyle → list[ResolvedSlide]（全部 EMU 坐标）
3. `validator`: 越界检查、重叠检测、最小尺寸验证（clamp + warn，不 crash）

### 6.3 Layer 3: PptxWriter（形状写入器）

~200 行的 match/case 循环。不做任何决策，只读取 ResolvedShape 字段并调用 python-pptx API。XML 补丁委托给 xml_patches.py。

### 6.4 设计决策

- **EMU 坐标而非比例**: 固定画布不需要响应式布局，命名意图解析为 EMU 比比例转换更直接
- **新旧并存**: v2 管线独立运行，不修改 v1 代码，通过对比测试验证等价性
- **换 JSON 换风格**: 添加新风格只需创建 JSON 文件，零 Python 代码变更
