# 设计理念

EduPPTX 是 AI Agent 驱动的教育演示文稿生成器。LLM 生成全页 SVG（Bento Grid 卡片布局），系统将 SVG 元素逐一转换为 PowerPoint 原生形状，输出直接可编辑的 PPTX。

## 1. 核心设计原则

### SVG 作为设计中间格式

LLM 擅长生成 SVG：布局自由度高、视觉质量好、支持渐变/阴影/圆角等现代设计语言。但 SVG 直接嵌入 PPTX 只是一张图片，不可编辑。

我们的方案：**让 LLM 发挥 SVG 设计能力，然后在构建时逐元素转译为 DrawingML 原生形状**。兼得两者：LLM 的视觉设计能力 + PowerPoint 的原生可编辑性。

### 策划/设计分离

借鉴顶级 PPT 设计公司的工作流：先有策划师做内容架构，再有设计师做视觉表达。

- **Phase 1a (内容规划)**: LLM 专注信息架构——页面类型、内容要点、布局模式
- **Phase 1b (视觉规划)**: LLM 专注视觉方案——主题色、背景风格、卡片配色
- **Phase 3 (SVG 设计)**: LLM 在明确的内容+视觉约束下生成 SVG

每次调用任务更聚焦，输出更稳定。

### 防御性后处理

LLM 生成的 SVG 不可完全信任。自动验证器修复常见问题（viewBox 偏差、字体缺失、边界溢出、文字重叠），然后 LLM Review 再审一遍。两层防线保证输出质量。

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

11 种布局组合覆盖从封面到数据页的所有教育场景。LLM 在内容规划阶段为每页选择最合适的 `layout_hint`。

## 4. 扩展点

### 添加新风格模板

在 `edupptx/design/style_templates/` 创建 SVG 文件。SVG 内容作为风格参考注入 LLM 的 system prompt。

### 添加新图标

将 24x24 SVG 放入 `assets/icons/` 目录，文件名即图标名。自动纳入 LLM 可用图标列表。

### 添加新页面类型

1. 在 `edupptx/models.py` 的 `PageType` Literal 中添加
2. 在 `edupptx/planning/prompts.py` 中添加描述
3. 在 `edupptx/design/prompts.py` 的 `type_hints` 中添加设计指引
