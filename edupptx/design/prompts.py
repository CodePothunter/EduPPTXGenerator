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

### 排版规范
- 标题：28-36px，加粗，位于卡片顶部
- 正文：18-22px，行高 1.4-1.6
- 辅助文字：14-16px，用于标注、说明
- 页码/页脚：12px，右下角或底部居中
- 要点符号：使用实心圆点 ● 或数字序号
- 中文内容左对齐，避免两端对齐
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


def build_svg_system_prompt(style_guide: str) -> str:
    """构建 SVG 生成的系统提示词。"""
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

## 风格指南

以下是本套幻灯片的视觉风格参考。请严格遵循其中的配色方案、字体、装饰元素风格：

{style_guide}

## 防重叠规则（强制遵守）
1. **标题区安全间距**：主标题 y 坐标从 50 开始，副标题/描述文字 y 坐标 ≥ 主标题 y + 字号 + 20。装饰线不能压在文字上。
2. **文本垂直间距**：相邻 <text> 元素的 y 坐标差 ≥ 上方文字的 font-size + 8（最小呼吸间距）
3. **卡片内部**：卡片标题 y 与卡片顶部 y 至少差 40px；正文 <tspan> 的 dy 至少为 font-size × 1.4
4. **元素不出界**：所有 <text>、<rect>、<image> 的 x 和 y 必须 ≥ 0，x + width ≤ 1280，y + height ≤ 720
5. **生成后自检**：输出 SVG 前在脑中逐一检查每个 <text> 元素的 y 坐标，确认不与前一个元素重叠

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
) -> str:
    """构建单页 SVG 生成的用户提示词。"""
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

    # 可用图片 — 告知 LLM 使用占位符，后处理注入真实图片
    image_lines: list[str] = []
    for role, path in assets.image_paths.items():
        image_lines.append(f"- **{role}** 图片可用，请用 `<image href=\"__IMAGE_{role.upper()}__\" .../>` 作为占位")
    if image_lines:
        lines.append("\n### 可用图片资源")
        lines.extend(image_lines)
        lines.append(
            "\n**必须** 在合适位置放置 `<image>` 元素。"
            " 使用 `href=\"__IMAGE_HERO__\"` 或 `href=\"__IMAGE_ILLUSTRATION__\"` 等占位符。"
            " 系统会自动替换为真实图片。给 image 设置合理的 x, y, width, height 属性。"
        )

    # 可用图标
    if assets.icon_svgs:
        icon_names = ", ".join(assets.icon_svgs.keys())
        lines.append(f"\n### 可用图标\n{icon_names}")
        lines.append("将图标 SVG 内容直接嵌入为 <g> 元素，适当缩放。")

    # 页面类型特殊说明
    type_hints = {
        "cover": "这是封面页。标题要大而醒目，居中展示。包含演讲主题和副标题即可，不需要过多内容。",
        "toc": "这是目录页。清晰列出所有章节标题，用序号标注，布局简洁有序。",
        "section": "这是章节分隔页。只需要章节标题，字大醒目，可配合装饰图形。",
        "closing": "这是结束页。展示感谢语或总结语，风格与封面呼应，简洁大方。",
        "data": "这是数据展示页。重点突出关键数据和趋势，用卡片区分不同数据维度。",
        "case": "这是案例分析页。突出案例标题，清晰展示分析要点，可配图说明。",
    }
    if page.page_type in type_hints:
        lines.append(f"\n### 页面类型提示\n{type_hints[page.page_type]}")

    lines.append(f"\n请生成第 {page.page_number} 页的完整 SVG 代码。")
    return "\n".join(lines)
