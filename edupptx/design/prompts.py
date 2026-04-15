"""SVG 生成的提示词工程。"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from edupptx.models import PagePlan, SlideAssets


def _compress_and_encode(image_path: Path, max_width: int = 400, quality: int = 50) -> str | None:
    """Compress image to thumbnail JPEG and return data URI string."""
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{b64}"
    except Exception:
        return None


BENTO_GRID_SPEC = """\
## Bento Grid 布局系统

Bento Grid 是一种基于卡片的模块化布局，专为教育演示设计。

### 核心原则
1. **卡片是基本布局单元**：每页包含 1-5+ 张卡片，数量由内容决定
2. **面积 = 重要性**：最大的卡片承载最核心的信息
3. **统一间距**：所有卡片之间保持 20px 间距
4. **圆角一致**：所有卡片使用相同的圆角半径（推荐 12-16px）
5. **内边距充足**：卡片内部至少 24px 内边距，文字不贴边

### 布局组合

| 布局名称 | 适用场景 | 卡片分布描述 |
|---------|---------|------------|
| center_hero | 封面、标题页 | 单张大卡片居中，承载标题和副标题 |
| vertical_list | 要点列举、定义 | 纵向等宽卡片堆叠，每张一个要点 |
| bento_2col_equal | 对比、两方面分析 | 左右等宽两列 |
| bento_2col_asymmetric | 主次内容 | 左宽右窄（约 2:1），左侧为主内容 |
| bento_3col | 三要素并列 | 三列等宽卡片 |
| hero_top_cards_bottom | 概述+细节 | 上方大卡片 + 下方 2-3 小卡片 |
| cards_top_hero_bottom | 铺垫+结论 | 上方 2-3 小卡片 + 下方大卡片 |
| mixed_grid | 复杂内容 | 自由组合大小卡片，填满画布 |
| full_image | 全图展示 | 背景图+浮动文字卡片 |
| timeline | 时间线、流程 | 横向时间轴 + 节点卡片 |
| comparison | 正反对比 | 左右对比卡片，颜色区分 |

### 画布分区（严格遵守）

画布 1280x720 分为以下固定区域：

```
┌─────────────────────────────────────┐ y=0
│  页面标题区 (y: 30-80)               │
│  主标题 y=50, font-size=32, bold     │
│  副标题 y=78, font-size=16           │
├─────────────────────────────────────┤ y=90
│                                     │
│  卡片内容区 (y: 100-660)             │
│  左边距 x=50, 右边距 x=1230          │
│  可用宽度 = 1180                     │
│  可用高度 = 560                      │
│                                     │
│  卡片内部：                          │
│    标题 y_offset=30, font-size=20    │
│    正文 y_offset=60, font-size=16    │
│    行间距 dy=24                       │
│                                     │
├─────────────────────────────────────┤ y=670
│  页脚区 (y: 680-710)                 │
│  页码 x=1220, y=700, font-size=12   │
└─────────────────────────────────────┘ y=720
```

**硬性坐标规则（违反会被自动裁切）：**
- **x 最小值 = 50**：所有元素的 x 坐标 ≥ 50（标题、卡片、文字全部如此）
- **x 最大值 = 1230**：所有元素的 x + width ≤ 1230
- **y 最小值 = 0**：没有负数 y 坐标
- **y 最大值 = 710**：所有元素在 y=710 以内
- 页面标题：x=50, y=50, font-size=28-32
- 副标题：x=50, y=90, font-size=14-16（必须在标题下方 ≥ 40px）
- 卡片内容区：x ∈ [50, 1230], y ∈ [110, 660]
- 页码：x=1220, y=700, font-size=12
- 卡片间距 = 20px

**常见错误（必须避免）：**
- ❌ 标题 x=0 或 x 为负数 → 会被左边界裁切
- ❌ 卡片宽度加 x 超过 1230 → 右边溢出
- ❌ 卡片只有标题没有正文 → 信息密度不足，必须填入内容
- ❌ 文字超过 20 个汉字没换行 → 必须用 <tspan> 分行
- ❌ 多个 <text> 放在相同 y 坐标且 x 也重叠 → 文字堆叠不可读

### 排版规范
- 标题：28-32px，加粗，x ≥ 50
- 正文：16-18px，行高 dy=24
- 辅助文字：14px
- 页码：12px，(1220, 700)
- 要点：用 ● 或数字序号
- 中文左对齐
- 每行最多 18-20 个汉字，超出用 <tspan> 换行
- 每个卡片必须有标题 + 至少 2 行正文内容
"""

SVG_CONSTRAINTS = """\
## SVG 技术约束（PPT 兼容）

生成的 SVG 必须符合以下约束，确保在 PowerPoint 和浏览器中正常显示：

### 画布
- viewBox="0 0 1280 720"（16:9 标准比例）
- 不设置 width/height 属性，让容器控制缩放

### 禁止使用
- ❌ <foreignObject>（PPT 不支持）
- ❌ CSS @keyframes / animation / transition
- ❌ <style> 标签中的复杂选择器
- ❌ JavaScript / <script>
- ❌ CSS filter（用 SVG <filter> 替代）
- ❌ clip-path 使用百分比（用绝对坐标）

### 文字
- 只用 <text> + <tspan> 渲染文字
- 安全字体：font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"（所有 <text> 元素都必须使用这个完整的 font-family 列表）
- 用 dy 属性控制行间距（如 dy="1.4em"）
- 长文本手动分行，每个 <tspan> 一行，约 20-25 个中文字符换行
- 文字不能超出卡片边界

### 图片
- 用 <image href="URL"> 嵌入图片
- 设置 preserveAspectRatio="xMidYMid slice" 防止变形
- 用 <clipPath> + <rect rx="..."> 实现圆角图片

### 渐变与装饰
- 渐变定义在 <defs> 中
- 使用 <linearGradient> 或 <radialGradient>
- 装饰元素用低透明度（opacity 0.05-0.2）

### 阴影
- 使用 SVG <filter> 实现阴影效果
- 定义在 <defs> 中，通过 filter="url(#shadow)" 引用
- 示例：<feDropShadow dx="0" dy="2" stdDeviation="4" flood-opacity="0.1"/>
"""


def build_svg_system_prompt(style_guide: str, visual_plan=None) -> str:
    """构建 SVG 生成的系统提示词。

    Args:
        style_guide: SVG 风格模板内容
        visual_plan: VisualPlan 对象，提供统一配色（可选，优先于 style_guide 配色）
    """
    color_spec = ""
    if visual_plan:
        color_spec = f"""
## 统一配色方案（必须严格遵守）

本套幻灯片使用以下统一配色，所有页面必须一致：

- **主色 (primary)**: {visual_plan.primary_color} — 用于标题栏、重要元素、页面顶部装饰条
- **辅色 (secondary)**: {visual_plan.secondary_color} — 用于次级标题、图标、辅助装饰
- **强调色 (accent)**: {visual_plan.accent_color} — 仅用于关键数据、重点标注（慎用）
- **卡片背景**: {visual_plan.card_bg_color}
- **正文色**: {visual_plan.text_color}
- **标题色**: {visual_plan.heading_color}

**严格要求**：
- 所有 `<rect>` 卡片的 fill 使用 `{visual_plan.card_bg_color}`
- 所有正文 `<text>` 的 fill 使用 `{visual_plan.text_color}`
- 页面标题的 fill 使用 `{visual_plan.heading_color}`
- 页面顶部装饰条使用 `{visual_plan.primary_color}`
- 不要自行发明其他颜色，严格在以上色板范围内配色
"""

    return f"""\
你是一位专业的信息架构师和 SVG 视觉设计专家，专注于教育演示文稿的设计。

## 你的职责
将教育内容转化为视觉清晰、信息层次分明的 SVG 幻灯片。每一页都应该：
1. **信息优先**：内容的视觉层次要准确反映其重要性
2. **教学友好**：学生能快速抓住要点，教师能流畅讲解
3. **视觉一致**：遵循给定的风格指南，保持全套幻灯片的视觉统一

## 输出格式
直接输出完整的 SVG 代码，不要包含任何解释文字。SVG 代码用 ```svg 和 ``` 包裹。

{BENTO_GRID_SPEC}

{SVG_CONSTRAINTS}
{color_spec}
## 风格指南

以下是本套幻灯片的视觉风格参考。请严格遵循其中的配色方案、字体、装饰元素风格：

{style_guide}

## 布局规则（强制遵守，违反会导致渲染错误）

### 卡片结构模式
每个卡片必须用 <g> 元素包裹，内含一个 <rect> 和若干 <text>。文字的 y 坐标必须在 rect 的 y ~ y+height 范围内。

正确示例：
```
<g>
  <rect x="50" y="100" width="550" height="200" rx="14" fill="#FFF"/>
  <text x="74" y="132" font-size="20" font-weight="bold">标题</text>
  <text x="74" y="164" font-size="16">
    <tspan x="74" dy="0">第一行内容</tspan>
    <tspan x="74" dy="24">第二行内容</tspan>
  </text>
</g>
```

错误示例（文字 y=320 超出了 rect y+height=300）：
```
<rect x="50" y="100" width="550" height="200"/>
<text x="74" y="320">这行文字在卡片外面！</text>
```

### 坐标规则
1. **文字必须在卡片内**：每个 <text> 的 y 坐标 ≥ 所属 rect 的 y + 24（上内边距），且 ≤ rect 的 y + height - 8（下内边距）
2. **文字不能叠在一起**：相邻 <text> 的 y 坐标差 ≥ 上方文字的 font-size + 8
3. **内容不够高度就减少**：如果卡片放不下所有文字，减少文字条目数而不是让文字溢出
4. **页面标题固定位置**：y=50，副标题 y=78
5. **所有元素在画布内**：x ∈ [0, 1280], y ∈ [0, 720]
6. **页码固定**：(1220, 700)

### 自检清单
输出 SVG 前确认：
- [ ] 每个 <text> 的 y 在其所属 <rect> 卡片的 y 到 y+height 范围内
- [ ] 没有相邻文字的 y 坐标差小于 font-size
- [ ] 没有元素的 x 或 y 为负数
- [ ] 卡片不超出 1280x720 画布

## 设计原则
1. **留白充分**：不要填满每一寸空间，给内容呼吸感
2. **配色克制**：主色 + 中性色，强调色只用于关键元素
3. **层次清晰**：通过字号、字重、颜色深浅建立 3-4 个视觉层次
4. **图文配合**：有图片时必须使用提供的 data URI 图片。合理分配图文空间，图片不小于 200x150
5. **中文优化**：中文内容适当增大字号（比英文大 2-4px），行距更宽松
6. **充实内容**：避免大片空白。封面页标题应居中偏上，章节页要有装饰图形或引导语填充空间
"""


def build_svg_user_prompt(
    page: PagePlan,
    assets: SlideAssets,
    total_pages: int,
    debug: bool = False,
) -> str:
    """构建单页 SVG 生成的用户提示词。

    Args:
        debug: If True, use description placeholders instead of __IMAGE__ tokens.
    """
    lines: list[str] = []

    # 页面基本信息
    lines.append(f"## 第 {page.page_number}/{total_pages} 页")
    lines.append(f"- 页面类型：{page.page_type}")
    lines.append(f"- 标题：{page.title}")
    if page.subtitle:
        lines.append(f"- 副标题：{page.subtitle}")
    lines.append(f"- 建议布局：{page.layout_hint}")

    # 内容要点
    if page.content_points:
        lines.append("\n### 内容要点")
        for i, point in enumerate(page.content_points, 1):
            if isinstance(point, dict):
                title = point.get("title", point.get("heading", ""))
                body = point.get("body", point.get("detail", point.get("text", "")))
                lines.append(f"{i}. **{title}**：{body}")
            else:
                lines.append(f"{i}. {point}")

    # 设计备注
    if page.design_notes:
        lines.append(f"\n### 设计备注\n{page.design_notes}")

    # 图片处理：debug 模式用描述占位，正常模式用 __IMAGE__ token
    if debug:
        # Debug mode: describe image needs as visual placeholders
        image_needs = page.material_needs.images if page.material_needs.images else []
        if image_needs:
            lines.append("\n### 图片占位（Debug 模式）")
            lines.append(
                "以下位置需要插图。请用**虚线矩形 + 居中灰色描述文字**标注图片区域："
            )
            for img in image_needs:
                lines.append(f"- {img.role}: {img.query}")
            lines.append(
                "\n占位示例：\n"
                "```svg\n"
                '<rect x="50" y="120" width="300" height="200" rx="8" '
                'fill="#F1F5F9" stroke="#94A3B8" stroke-width="1.5" stroke-dasharray="6,4"/>\n'
                '<text x="200" y="225" text-anchor="middle" font-size="14" '
                'fill="#94A3B8" font-family="Noto Sans SC, Arial, sans-serif">'
                "图片描述文字</text>\n"
                "```\n"
                "请在合适位置留出图片空间，居中写上图片描述。"
            )
    else:
        # Normal mode: use __IMAGE__ placeholders for post-processing injection
        image_lines: list[str] = []
        for role, path in assets.image_paths.items():
            image_lines.append(f'- **{role}** 图片可用，请用 `<image href="__IMAGE_{role.upper()}__" .../>` 作为占位')
        if image_lines:
            lines.append("\n### 可用图片资源")
            lines.extend(image_lines)
            lines.append(
                "\n**必须** 在合适位置放置 `<image>` 元素。"
                ' 使用 `href="__IMAGE_HERO__"` 或 `href="__IMAGE_ILLUSTRATION__"` 等占位符。'
                " 系统会自动替换为真实图片。给 image 设置合理的 x, y, width, height 属性。"
            )

    # 可用图标
    if assets.icon_svgs:
        icon_names = ", ".join(assets.icon_svgs.keys())
        lines.append(f"\n### 可用图标\n{icon_names}")
        lines.append("将图标 SVG 内容直接嵌入为 <g> 元素，适当缩放。")

    # 页面类型特殊说明
    type_hints = {
        "cover": (
            "这是封面页。设计要求：\n"
            "1. 使用 center_hero 布局：一张大卡片（w≥900, h≥350）居中\n"
            "2. 主标题 font-size=56-72，加粗，居中\n"
            "3. 副标题在主标题下方 40px，font-size=20-24\n"
            "4. 如有图片占位，放在标题上方或侧面，尺寸不小于 300x200\n"
            "5. 用装饰圆形、渐变色块填充空白区域，体现主题氛围\n"
            "6. 页面不能有大面积空白——标题区上下都要有视觉元素"
        ),
        "toc": (
            "这是目录页。设计要求：\n"
            "1. 使用 vertical_list 布局：每个章节一个横向卡片\n"
            "2. 每个卡片左侧用主色显示序号（font-size=24, bold），右侧是标题+简短描述\n"
            "3. 卡片之间间距 16-20px，卡片高度根据内容自适应\n"
            "4. 整体居中，左右留 margin ≥ 50px"
        ),
        "section": (
            "这是章节分隔页。设计要求：\n"
            "1. 章节标题要大（font-size=40-48），居中偏上\n"
            "2. 下方配一句引导语或本章概述（font-size=18-20）\n"
            "3. 可用装饰图形（圆形、线条、色块）填充，体现视觉节奏\n"
            "4. 如有图片占位，居中大尺寸展示"
        ),
        "closing": (
            "这是结束页。设计要求：\n"
            "1. 感谢语 font-size=40-48，居中\n"
            "2. 下方可放课程回顾要点（3-4 条，简洁）\n"
            "3. 风格与封面呼应，使用相同配色和装饰元素\n"
            "4. 底部可放联系信息或二维码占位"
        ),
        "data": (
            "这是数据展示页。设计要求：\n"
            "1. 数据用 hero_top_cards_bottom 布局：上方大卡放图表，下方小卡放关键数字\n"
            "2. 关键数字用大号字体（font-size=32-40, bold, 强调色）\n"
            "3. 图表可用 SVG 矩形柱状图、折线等简单图表\n"
            "4. 每个数据卡片要有标签+数值+简短说明"
        ),
        "case": "这是案例分析页。突出案例标题，清晰展示分析要点，可配图说明。",
    }
    if page.page_type in type_hints:
        lines.append(f"\n### 页面类型提示\n{type_hints[page.page_type]}")

    lines.append(f"\n请生成第 {page.page_number} 页的完整 SVG 代码。")
    return "\n".join(lines)
