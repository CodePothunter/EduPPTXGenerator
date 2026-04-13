# 设计理念

EduPPTX 是一个 Schema 驱动的教育演示文稿生成器。用户给出主题和自然语言风格要求，Agent 编排 LLM 完成内容规划、风格协商、素材生成，最终通过三层渲染管线输出 PPTX 文件。

本文记录架构选型、核心设计决策和背后的思考。

## 1. 设计原则

### 数据驱动，代码不做视觉决策

所有视觉参数（配色、字号、间距、装饰开关）定义在 JSON 文件里，而不是 Python 代码中。渲染管线只是一个"JSON → EMU 坐标 → PPTX 形状"的机械映射。换风格 = 换 JSON 文件，零代码变更。

### Agent 做决策，管线做执行

LLM 负责两类决策：内容规划（选什么 slide 类型、写什么文案）和风格协商（把"清新活泼"翻译成具体的 margin/spacing/density 参数）。管线收到决策结果后纯机械执行，不再有任何智能判断。

### 防御性布局

LLM 的决策不可完全信任。风格协商可能选出让卡片空间不足的参数组合。布局引擎的回应不是报错，而是自动降级（去掉图标、缩小标题）保证文字始终有足够空间。任何样式组合都能产出合法的 PPTX。

### 单一配置源

颜色、字体、间距的定义只存在于 `styles/*.json`。不存在第二套配色定义。Agent、背景生成器、图表渲染器、测试 fixture 全部从同一个 `ResolvedStyle` 取值。

## 2. Agent 架构

5 个阶段串联。前 3 个阶段用 LLM 做决策，后 2 个阶段纯执行：

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

用户的自然语言要求（如"简约商务风"）被 LLM 转译为 JSON 补丁，深度合并到基础 StyleSchema 上。详见 [style-negotiation.md](style-negotiation.md)。

### Phase 3: 素材决策

对每张非简单类型的 slide（跳过 big_quote/closing/section），发起一次小型 LLM 调用，决定：
- 背景风格（diagonal_gradient/radial_gradient/geometric_circles/geometric_triangles）
- 是否需要图表（5 种类型），或 AI 插图（描述、风格、锚点、缩放）

使用 4 个线程并行执行。背景风格做了**强制轮转**（`_BG_STYLES[idx % 4]`），防止 LLM 每页都选同一种。

### Phase 4: 素材执行

纯执行，无 LLM 调用。并行生成背景图和 AI 插图。三级降级策略：

```
缓存库命中 → 直接复用（0ms）
           → 程序生成（Pillow，~50ms）
           → AI 生图 + 压缩 + 缓存（~5s）
           → 无 API key 则跳过
```

### Phase 5: Schema 渲染

详见下文"三层 Schema 架构"。

## 3. 三层 Schema 架构

渲染管线的核心。受 CSS 启发，将视觉决策从代码中抽离到 JSON：

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

- **换 JSON 换风格**: 不改 Python 就能完全改变视觉风格
- **EMU 坐标而非比例**: 固定画布不需要响应式，命名意图解析为 EMU 比比例转换更直接
- **Writer 不做决策**: 渲染层是纯机械映射，所有智能在 resolver 层完成

## 4. 核心设计决策

### 4.1 绝对定位，不用 Placeholder

PowerPoint 的 slide layout placeholder 体系适合编辑已有模板，不适合动态内容生成。卡片数量在 1-4 之间变化，阴影、透明度、SVG 嵌入需要操控底层 XML，placeholder 反而增加了间接性。我们选择**绝对定位 + 坐标计算**，由 layout resolver 动态生成每个形状的 EMU 坐标。

### 4.2 Pydantic 做中间表示

`PresentationPlan` 是 LLM 输出和渲染器之间的桥梁：

1. **LLM 输出校验** — Pydantic 的 `model_validate()` 自动纠正或报错
2. **类型安全** — IDE 补全，减少拼写错误
3. **序列化/反序列化** — Agent 可以把 plan 保存为 JSON，后续加载修改再渲染

### 4.3 自适应卡片布局

卡片区域高度固定（200pt），但内部布局根据可用空间自动选择三种模式：

| 模式 | 条件 | 图标 | 标题 | Body 高度 |
|------|------|------|------|-----------|
| Full | body_h >= 38pt | 48pt | 30pt | 充足 |
| Compact | body_h >= 38pt（compact） | 32pt | 24pt | 中等 |
| Minimal | 以上都不满足 | 无 | 24pt | 最大化 |

当风格协商选择了 `relaxed` 密度（更大的内部间距），full 模式的 body 区域可能不足。布局引擎自动降级到 compact 或 minimal 模式。这个机制使得**任何样式组合都能正确渲染**。

### 4.4 XML 补丁

python-pptx 不支持卡片阴影、蒙版透明度、SVG 图标嵌入、圆角半径、CJK 字体。通过 lxml 直接操作 XML 的 `<a:outerShdw>`、`<a:alpha>`、`<asvg:svgBlip>`、`<a:gd>`、`<a:ea>` 等元素实现。"高层 API + 底层补丁" 的混合方式兼顾开发效率和视觉效果。

### 4.5 LLM 提示词约束

**约束化输出** — 明确指定 JSON schema、每种 slide type 的卡片数量范围、可用图标名单。约束越明确，LLM 输出越稳定。

**图标目录注入** — 109 个可用图标名直接写进提示词，LLM 只能从中选择。无效图标被自动替换为 `circle` fallback。

**卡片数量上限** — 大多数 slide 类型限制为 2-3 张卡片，仅 summary 允许 4 张。过多卡片导致文字拥挤，约束比修布局成本低。

## 5. 扩展点

### 添加新主题

在 `styles/` 目录创建 JSON 文件。结构参考 `styles/emerald.json`。CLI 的 `palettes` 命令会自动发现。

### 添加新 slide 类型

1. 在 `layout_resolver.py` 写 resolver 函数
2. 注册到 `_SLIDE_RESOLVERS` dict
3. 在 `prompts/content.py` 的约束表中添加类型

### 添加新图标

将 24x24 的 SVG 放入 `assets/icons/` 目录，文件名即图标名。

---

## 附录：设计考古 — 豆包逆向分析

项目早期拆解了一份豆包 AI 生成的教学 PPT（"探索勾股定理的奥秘"，15 页），获得了几个关键洞察，影响了后续的架构选型。

### 空白 Layout 策略

豆包的 15 张 slide 全部引用同一个完全空白的 `slideLayout12`（无任何 placeholder），每个形状都是绝对定位的 `<p:sp>` 和 `<p:pic>`。这验证了我们"不用 placeholder，用坐标计算"的方向。

### SVG+PNG 双轨图标

每个图标由 SVG + PNG 一对文件组成，通过 `asvg:svgBlip` 扩展嵌入 SVG，PNG 作为低版本降级。这是 Office 2019+ 的标准做法，我们在 `xml_patches.py` 中复现了这个模式。

```xml
<a:blip r:embed="rId3">  <!-- PNG fallback -->
  <a:extLst>
    <a:ext uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}">
      <asvg:svgBlip r:embed="rId4"/>  <!-- SVG modern -->
    </a:ext>
  </a:extLst>
</a:blip>
```

### Tailwind 色系

分析色值发现完全匹配 Tailwind CSS 的 emerald 色系。我们沿用了 Tailwind 色板作为主题基础。

### 坐标模板化

同类型元素的 Y 坐标几乎完全一致，证明背后有预定义的槽位模板。这与我们的 layout resolver 方案一致。

### 已超越的部分

EduPPTX 在以下方面已经超出了对豆包的逆向复现：

| 能力 | 豆包 | EduPPTX |
|------|------|---------|
| 风格控制 | 固定风格 | **自然语言风格协商**，LLM 实时转译 |
| 主题扩展 | 闭源，不可扩展 | **JSON 驱动**，加文件即加主题 |
| 布局自适应 | 固定参数 | **预计算降级**，任意样式组合都安全 |
| 可观测性 | 黑盒 | **会话目录**，每阶段产物可检视 |
| 素材管理 | 一次性生成 | **素材库缓存**，跨会话复用 |
| 架构 | 单体管线 | **Agent 编排**，5 阶段可独立替换 |
