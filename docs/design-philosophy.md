# 设计理念

EduPPTX 是 AI Agent 驱动的教育演示文稿生成器。LLM 生成全页 SVG（Bento Grid 卡片布局），系统将 SVG 元素逐一转换为 PowerPoint 原生形状，输出直接可编辑的 PPTX。

## 1. 核心设计原则

### SVG 作为设计中间格式

LLM 擅长生成 SVG：布局自由度高、视觉质量好、支持渐变/阴影/圆角等现代设计语言。但 SVG 直接嵌入 PPTX 只是一张图片，不可编辑。

我们的方案：**让 LLM 发挥 SVG 设计能力，然后在构建时逐元素转译为 DrawingML 原生形状**。兼得两者：LLM 的视觉设计能力 + PowerPoint 的原生可编辑性。

### 策划/设计分离

借鉴顶级 PPT 设计公司的工作流：先有策划师做内容架构，再有设计师做视觉表达。

- **Phase 1a (内容规划)**: LLM 专注信息架构——页面类型、内容要点、布局模式
- **Phase 1b (视觉规划)**: LLM 专注视觉方案——主题色、背景风格、卡片配色；可选输出 8 段 DESIGN.md 作为视觉系统快照（YAML tokens + Markdown prose）
- **Phase 3 (SVG 设计)**: LLM 在明确的内容+视觉约束下，参照页面模板生成 SVG

每次调用任务更聚焦，输出更稳定。

### 分层质量管控

LLM 生成的 SVG 不可完全信任。多层防线保证输出质量：

1. **自动验证器** — 修复常见问题（viewBox、字体、边界溢出、文字重叠、PPT 不兼容特性）
2. **条件 LLM 审阅** — 仅对有严重 warning 的页面做 CRAP 设计原则评估（对齐/对比/重复/邻近）
3. **PPT 兼容清理** — 移除脚本、事件处理器、emoji、嵌套 tspan 等
4. **公式渲染** — LaTeX `data-latex` 标记自动渲染为高质量图片
5. **图标嵌入** — `data-icon` 占位符自动替换为 Lucide SVG 图标
6. **风格 lint** — `style_linter` 在 `resolve_style` 末尾跑 WCAG 对比度（正文 4.5:1 / 图标 3:1）+ palette broken-ref，阻止颜色深坑落入 Phase 3

原始 LLM 输出保留在 `slides_raw/` 目录，方便对比和调试。

### Debug 优先开发

`--debug` 模式跳过耗时耗钱的素材获取，保留完整 LLM 流程。图片位置用描述占位。开发者可以快速迭代布局和 prompt 质量。

## 2. 为什么选 SVG→DrawingML

### 尝试过的方案和放弃原因

| 方案 | 结果 | 放弃原因 |
|------|------|---------|
| python-pptx 直接生成 | 能用但丑 | 布局自由度低，无法做 Bento Grid |
| SVG 嵌入 PPTX (asvg:svgBlip) | 空白/不可编辑 | 只是图片，需手动"转换为形状"，转换后布局乱 |
| svg2pptx 库 | 部分可用 | CJK 文字宽度计算错误，不支持 tspan |
| SVG→DrawingML 自研转换 | 可用 | 当前方案 |

### 转换器核心思路

SVG 元素和 DrawingML 有 1:1 对应关系：

- `<rect rx="14">` → `<a:prstGeom prst="roundRect">` + avLst
- `<text><tspan>多行</tspan></text>` → `<p:sp txBox="1">` 多段落 + wrap="square"
- `<path d="M..C..Z">` → `<a:custGeom>` + moveTo/cubicBezTo/close
- 渐变、阴影、透明度都有对应的 DrawingML 属性

坐标转换公式: `EMU = SVG_px × 9525`

## 3. Bento Grid 布局系统

受苹果发布会设计启发的卡片式模块化布局：

- **卡片是基本单元**: 每页 1-5+ 张卡片，数量由内容决定
- **面积 = 重要性**: 最大卡片承载最核心信息
- **统一间距**: 所有卡片间保持 20px 间距
- **圆角一致**: 所有卡片使用相同圆角 (12-16px)

13 种布局组合覆盖从封面到数据页的所有教育场景。LLM 在内容规划阶段为每页选择最合适的 `layout_hint`。

## 4. 页面模板系统

### 参考继承模式

每种页面类型（封面、目录、正文等）对应一个参考 SVG 模板。LLM 生成 SVG 时读取对应模板代码，**照着画**而非**填空**：

- 继承模板的页面结构（标题位置、卡片布局、装饰元素）
- 使用模板的视觉风格（间距、圆角、阴影、色块比例）
- 用实际内容替换占位文字，根据要点数量调整布局

模板文件在 `edupptx/design/page_templates/`，每个 ≤3000 字符（~750 tokens），注入到单页的 user prompt 中。

### 设计规范层次

```
design-base.md         — 公共设计规范（7色系统、6级字号、间距、CRAP 原则）
shared-standards.md    — SVG/PPT 技术约束黑名单
executor-lecture.md    — 讲授模式规范（大字、宽松、3-5 要点/页）
executor-review.md     — 复习模式规范（小字、紧凑、6-8 要点/页）
page-types.md          — 14 种教育页面类型定义
page_templates/*.svg   — 页面级 SVG 参考模板
style_templates/*.svg  — 整体风格模板（5 套教育主题）
chart_templates/*.svg  — 图表参考模板（5 种）
```

详见 `docs/page-template-guide.md`。

## 5. 公式与化学方程式

数学公式和化学方程式使用 LaTeX 渲染为图片嵌入：

1. LLM 在 SVG 中用 `data-latex` 属性标记公式：`<text data-latex="\frac{a}{b}">a/b</text>`
2. 后处理用 matplotlib mathtext 引擎渲染为透明 PNG（200dpi）
3. 替换 `<text>` 为 `<image>` 嵌入 SVG
4. 渲染失败时保留原 `<text>` 中的 Unicode 回退文字

支持范围：基础数学（分数、根号、上下标、希腊字母）、简单化学式（`\mathrm{CaCO_3}`）。

## 6. SVG 后处理管线

Phase 4 的完整处理链：

```
原始 LLM SVG
    ↓ 保存到 slides_raw/ (调试用)
    ↓
Step 1: 验证器自动修复
    - XML 安全字符、circle cx/cy 修正、viewBox 标准化
    - PPT 黑名单检测 (clipPath/mask/style/rgba/SMIL 等)
    - 字体安全替换 (数学内容保留等宽字体)
    - 长文本自动换行 (跳过公式内容)
    - 边界钳制、文字重叠修复、卡片高度扩展
    - 圆内标签自动注入 dominant-baseline + snap 对齐
    ↓
Step 2: 条件 LLM 审阅
    - 仅对有严重 warning 的页面调用 LLM
    - 13 条审阅标准: 定位/溢出/颜色/字体 + CRAP 设计原则
    - 纯 minor auto-fix 的页面跳过审阅 (节省 ~30% 时间)
    ↓
Step 3: PPT 兼容清理
    - 移除 script/事件处理器/emoji/注释
    - 展平嵌套 tspan、修正圆内标签
    - 确保 SVG namespace、移除 width/height
    ↓
Step 3.3: LaTeX 公式渲染
    - data-latex → matplotlib → PNG → base64 image
    ↓
Step 3.5: 图标占位符嵌入
    - data-icon → Lucide SVG path → <g transform> 组
    ↓
Step 4: 图片注入
    - __IMAGE_HERO__ 等占位符 → base64 data URI
    ↓
保存到 slides/ → Phase 5 PPTX 组装
```

## 7. 扩展点

### 添加新页面模板

在 `edupptx/design/page_templates/` 创建 `{page_type}.svg`（≤3000 字符）。需在 `prompts.py` 的 `_PAGE_TYPE_TEMPLATE_MAP` 中注册映射。详见 `docs/page-template-guide.md`。

### 添加新风格模板

在 `edupptx/design/style_templates/` 创建 SVG 文件。SVG 内容作为风格参考注入 LLM 的 system prompt（上限 8000 字符）。

### 添加新图标

将 24x24 SVG 放入 `assets/icons/` 目录，文件名即图标名。自动纳入 LLM 可用图标列表。LLM 可通过 `<use data-icon="icon-name"/>` 占位符引用。

### 添加新页面类型

1. 在 `edupptx/models.py` 的 `PageType` Literal 中添加
2. 在 `edupptx/planning/prompts.py` 中添加描述
3. 在 `edupptx/design/prompts.py` 的 `type_hints` 中添加设计指引
4. (可选) 在 `edupptx/design/page_templates/` 创建对应 SVG 模板
5. (可选) 在 `edupptx/design/references/page-types.md` 中添加布局定义

### 添加新布局提示

1. 在 `edupptx/models.py` 的 `LayoutHint` Literal 中注册新布局名
2. 在 `edupptx/planning/content_planner.py` 与 `edupptx/planning/prompts.py` 中补齐合法值和规划描述
3. 在 `edupptx/design/references/design-base.md`、`docs/layout-system.md`、`docs/svg-pipeline.md` 中补齐布局说明
4. 在具体模板族的 `metadata.xml` 里把新布局写入 `preferred_layout_hints`，并在 `variant_catalog` 中声明可命中的 SVG 变体
5. 在对应模板族的 `style_guide.md` 中补齐模板用途说明；如布局依赖关系图等特殊结构，再补 `chart_templates/` 或额外生成提示

### 添加新图表模板

在 `edupptx/design/chart_templates/` 创建 SVG 文件。会被 `page-types.md` 引用作为图表布局参考。
