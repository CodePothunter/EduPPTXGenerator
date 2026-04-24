# 页面模板系统指南

## 概述

页面模板是 EduPPTX 的 SVG 视觉参考系统。每种页面类型（封面、目录、正文等）对应一个参考 SVG 模板文件。LLM 在生成 SVG 时会读取对应类型的模板代码，**参照其布局结构和视觉风格**来生成新页面。

### 核心原则：参考继承，不是模板填充

模板是"**照着画**"的范例，不是"**填空**"的表格。LLM 会：
- 继承模板的页面结构（标题位置、卡片布局、装饰元素）
- 使用模板的视觉风格（间距、圆角、阴影、色块比例）
- 用实际内容替换占位文字
- 根据页面要点数量调整布局

LLM **不会**：
- 复制粘贴模板 SVG 代码
- 仅替换 `{title}` 等占位符
- 被模板限制创意布局

## 目录结构

```
edupptx/design/page_templates/
├── README.md           # 模板索引说明
├── cover.svg           # 封面页模板
├── toc.svg             # 目录页模板
├── section.svg         # 章节分隔页模板
├── content.svg         # 正文内容页模板（默认 fallback）
├── closing.svg         # 结束页模板
├── quiz.svg            # (可选) 练习检测页模板
├── formula.svg         # (可选) 公式推导页模板
├── content_comparison.svg      # (可选) content 页的 comparison 布局模板
├── experiment.svg      # (可选) 实验步骤页模板
├── summary.svg         # (可选) 知识归纳页模板
├── data.svg            # (可选) 数据展示页模板
├── timeline.svg        # (可选) 时间线页模板
└── case.svg            # (可选) 案例分析页模板
```

没有对应模板文件的页面类型会 fallback 到 `content.svg`。

## 模板映射规则

| PageType | 模板文件 | 说明 |
|----------|---------|------|
| `cover` | `cover.svg` | 封面，居中大标题+装饰 |
| `toc` | `toc.svg` | 目录，纵向列表+序号 |
| `section` | `section.svg` | 章节分隔，居中标题+装饰 |
| `content` | `content.svg` | 通用正文（双列 Bento Grid） |
| `closing` | `closing.svg` | 结束页，感谢语+回顾 |
| `quiz` | `quiz.svg` 或 fallback `content.svg` | 练习题布局 |
| `formula` | `formula.svg` 或 fallback `content.svg` | 公式推导步骤 |
| 其他 | fallback `content.svg` | 默认布局 |

## 如何制作模板

### 1. 画布规格

所有模板必须遵守：

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">
```

- 画布 1280x720（16:9）
- 不设 `width`/`height` 属性
- 坐标规则：x∈[50,1230], y∈[0,720]

### 2. 颜色占位符

模板中使用以下占位符，运行时由 `VisualPlan` 的实际色值替换：

| 占位符 | 用途 | 示例值 |
|--------|------|--------|
| `{primary_color}` | 主色，标题装饰条 | `#2563EB` |
| `{secondary_color}` | 辅色，次级元素 | `#0EA5E9` |
| `{accent_color}` | 强调色，≤3处使用 | `#F97316` |
| `{card_bg_color}` | 卡片背景 | `#FFFFFF` |
| `{secondary_bg_color}` | 次背景，交替行 | `#F0F7FF` |
| `{text_color}` | 正文色 | `#1E293B` |
| `{heading_color}` | 标题色 | `#1E40AF` |

**注意**：这些占位符是给 LLM 看的参考，不会在代码层面被自动替换。LLM 会根据 system prompt 中注入的 VisualPlan 颜色值来替换。

### 3. 内容占位符

模板中的文字内容用描述性占位符表示：

| 占位符 | 说明 |
|--------|------|
| `{title}` | 页面主标题 |
| `{subtitle}` | 页面副标题 |
| `{page_number}` | 当前页码 |
| `{total_pages}` | 总页数 |

### 4. 必须包含的结构元素

每个模板必须包含：

```svg
<!-- 顶部装饰条（唯一允许 x=0 的元素） -->
<rect x="0" y="0" width="1280" height="6" fill="{primary_color}"/>

<!-- 页码 -->
<text x="1220" y="700" text-anchor="end" font-size="13" fill="{text_color}"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif">
  {page_number} / {total_pages}
</text>
```

### 5. 字体规范

所有 `<text>` 元素必须使用完整 font-family：

```
font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
```

### 6. 阴影效果

推荐使用标准阴影 filter：

```svg
<defs>
  <filter id="shadow" x="-5%" y="-5%" width="110%" height="110%">
    <feDropShadow dx="0" dy="2" stdDeviation="4" flood-opacity="0.08"/>
  </filter>
</defs>

<!-- 使用方式 -->
<rect ... filter="url(#shadow)"/>
```

### 7. 文件大小限制

每个模板 SVG 文件不超过 **3000 字符**（约 750 tokens）。超出部分会被截断。这是因为模板会被注入到 LLM 的 user prompt 中，需要控制 token 预算。

如果模板较复杂，可以：
- 只展示**核心布局骨架**，省略重复的装饰元素
- 用注释 `<!-- 更多同类元素 -->` 表示重复结构
- 减少属性冗余（如简写 font-family）

## 设计质量标准

一个好的模板应该：

### 视觉层面
- [ ] 配色使用占位符，不硬编码颜色
- [ ] 有明确的视觉层次（标题 > 卡片标题 > 正文 > 标注）
- [ ] 使用圆角卡片（rx=14）而非方角
- [ ] 有适当的装饰元素（圆形、渐变色块、分隔线）
- [ ] 空间利用合理，不太空也不太挤

### 结构层面
- [ ] 遵循 Bento Grid 布局系统
- [ ] 卡片区域 y∈[100,660]，标题区 y∈[30,80]
- [ ] 卡片间距 20px
- [ ] 卡片内边距 24px
- [ ] 页码位置 (1220, 700)

### PPT 兼容层面
- [ ] 无 `<style>` 块
- [ ] 无 `class=` 属性
- [ ] 无 `rgba()` 颜色
- [ ] 无 `<foreignObject>`
- [ ] 无 `<animate>` 或 SMIL 动画
- [ ] `<filter>` 仅用 `feDropShadow` 或 `feGaussianBlur`

## 模板开发工作流

### 步骤 1: 选择页面类型

从 `PageType` 枚举中选一个类型开始：

```python
PageType = Literal[
    "cover", "toc", "section", "content", "data", "case", "closing",
    "timeline", "exercise", "summary",
    "quiz", "formula", "experiment",
]
```

`comparison` 和 `relation` 不属于 `PageType`，应作为 `layout_hint` 使用；对应模板通常写成
`content_comparison*.svg` 或 `content_relation*.svg`，并在 metadata 的 `variant_catalog` 中声明
`page_type="content"`。

### 步骤 2: 创建 SVG 文件

在 `edupptx/design/page_templates/` 下创建 `{page_type}.svg`。

推荐工具：
- **Figma**: 设计完后导出 SVG，手动清理
- **VS Code + SVG Preview**: 直接编写 SVG 代码
- **浏览器直接预览**: `file:///path/to/template.svg`

### 步骤 3: 验证渲染

```bash
# 渲染为 PNG 预览
uv run python3 -c "
import cairosvg
cairosvg.svg2png(url='edupptx/design/page_templates/cover.svg',
                 write_to='/tmp/template_preview.png',
                 output_width=1280, output_height=720)
print('Rendered to /tmp/template_preview.png')
"
```

### 步骤 4: 检查文件大小

```bash
wc -c edupptx/design/page_templates/*.svg
# 每个文件不超过 3000 字符
```

### 步骤 5: 注册映射（可选）

如果添加了新页面类型的模板，需要在 `edupptx/design/prompts.py` 的 `_PAGE_TYPE_TEMPLATE_MAP` 字典中注册：

```python
_PAGE_TYPE_TEMPLATE_MAP = {
    "cover": "cover.svg",
    "toc": "toc.svg",
    "section": "section.svg",
    "content": "content.svg",
    "closing": "closing.svg",
    "quiz": "quiz.svg",        # 新增
    "formula": "formula.svg",  # 新增
}
```

### 步骤 6: 端到端测试

```bash
uv run edupptx gen "测试主题" --debug
# 检查生成的 SVG 是否参照了新模板的布局风格
```

## 运行时流程

```
Phase 3: SVG 生成
    │
    ├─ build_svg_system_prompt()
    │   ├─ design-base.md (设计规范)
    │   ├─ shared-standards.md (技术约束)
    │   ├─ executor-lecture.md 或 executor-review.md
    │   ├─ page-types.md (页面类型定义)
    │   ├─ VisualPlan 颜色注入
    │   └─ style_guide (风格模板 SVG)
    │
    ├─ build_svg_user_prompt() [每页独立]
    │   ├─ 页面基本信息 (类型、标题、布局)
    │   ├─ 内容要点
    │   ├─ ★ 页面 SVG 参考模板 ← 新增
    │   ├─ 图片/图标资源
    │   └─ 页面类型提示
    │
    └─ LLM 并行生成 → GeneratedSlide[]
```

模板 SVG 被注入到每页的 **user prompt** 中（不是 system prompt），这样每页只看到自己类型的模板，不浪费 token。

## 设计参考

模板设计可以参考：

1. **ppt-master 项目** — `templates/layouts/` 中有多种风格的完整页面模板
2. **现有 design-base.md** — 包含 Bento Grid 布局规范、CRAP 设计原则
3. **现有 chart_templates/** — 柱状图、折线图、饼图等图表参考
4. **page-types.md** — 14 种教育页面类型的布局定义和 SVG 代码示例
