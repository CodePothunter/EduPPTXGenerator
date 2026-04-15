"""SVG 生成的提示词工程 — V3 reference 文件组装。"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Literal

from edupptx.models import PagePlan, SlideAssets, VisualPlan

_REFS_DIR = Path(__file__).parent / "references"


def _load_ref(name: str) -> str:
    """Load a reference markdown file."""
    path = _REFS_DIR / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


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


def _build_color_spec(vp: VisualPlan) -> str:
    """Build color specification block from VisualPlan."""
    return f"""
## 统一配色方案（必须严格遵守）

本套幻灯片使用以下统一配色，所有页面必须一致：

- **主色 (primary)**: {vp.primary_color} — 标题栏装饰条、重要元素
- **辅色 (secondary)**: {vp.secondary_color} — 次级标题、图标填充
- **强调色 (accent)**: {vp.accent_color} — 关键数据、重点标注（慎用）
- **卡片背景**: {vp.card_bg_color}
- **次背景**: {vp.secondary_bg_color} — 区域分隔、交替行背景
- **正文色**: {vp.text_color}
- **标题色**: {vp.heading_color}

**严格要求**：
- 卡片 `<rect>` fill 使用 `{vp.card_bg_color}`
- 交替行/引用区块 fill 使用 `{vp.secondary_bg_color}`
- 正文 `<text>` fill 使用 `{vp.text_color}`
- 页面标题 fill 使用 `{vp.heading_color}`
- 装饰条/图标使用 `{vp.primary_color}` 或 `{vp.secondary_color}`
- 不要自行发明其他颜色
"""


def build_svg_system_prompt(
    style_guide: str,
    visual_plan: VisualPlan | None = None,
    content_density: Literal["lecture", "review"] = "lecture",
) -> str:
    """构建 SVG 生成的系统提示词。

    从 design/references/ 读取 markdown 文件并组装。
    """
    parts: list[str] = []

    # 1. 公共设计规范
    parts.append(_load_ref("design-base.md"))

    # 2. SVG 技术约束
    parts.append(_load_ref("shared-standards.md"))

    # 3. 密度模式
    if content_density == "review":
        parts.append(_load_ref("executor-review.md"))
    else:
        parts.append(_load_ref("executor-lecture.md"))

    # 4. 教育页面类型
    parts.append(_load_ref("page-types.md"))

    # 5. 配色方案
    if visual_plan:
        parts.append(_build_color_spec(visual_plan))

    # 6. 风格模板
    if style_guide:
        parts.append(f"\n## 风格指南\n\n{style_guide}")

    return "\n\n".join(p for p in parts if p.strip())


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
        "quiz": (
            "这是练习检测页。设计要求：\n"
            "1. 题目大卡片在上方，选项卡片 2x2 在下方\n"
            "2. 题号用主色圆形背景 + 白色数字\n"
            "3. 选项标签 A/B/C/D 用辅色圆形\n"
            "4. 参考 page-types.md 中 quiz 类型的布局定义"
        ),
        "formula": (
            "这是公式推导页。设计要求：\n"
            "1. 步骤卡片纵向排列，用箭头（<polygon>）连接\n"
            "2. 每步有序号圆 + 公式（等宽字体） + 文字说明\n"
            "3. 最后一步（结论）用强调色卡片高亮\n"
            "4. 参考 page-types.md 中 formula 类型的布局定义"
        ),
        "experiment": (
            "这是实验步骤页。设计要求：\n"
            "1. 左窄右宽 (3:7) 布局\n"
            "2. 左侧：器材列表卡片，每项配图标\n"
            "3. 右侧：步骤编号列表 + 底部结论高亮卡片\n"
            "4. 参考 page-types.md 中 experiment 类型的布局定义"
        ),
        "comparison": (
            "这是对比表格页。设计要求：\n"
            "1. 表头行用主色背景 + 白色文字\n"
            "2. 数据行交替使用 card_bg 和 secondary_bg\n"
            "3. 用 <rect> + <text> + <line> 构建表格\n"
            "4. 参考 page-types.md 中 comparison 类型的布局定义"
        ),
        "summary": (
            "这是知识归纳页。设计要求：\n"
            "1. 分类卡片纵向排列，每个分类有标题栏（辅色背景）\n"
            "2. 知识点用列表形式，配图标前缀\n"
            "3. 可选：底部放「易错点」警示卡片（浅红/浅黄背景）\n"
            "4. 参考 page-types.md 中 summary 类型的布局定义"
        ),
    }
    if page.page_type in type_hints:
        lines.append(f"\n### 页面类型提示\n{type_hints[page.page_type]}")

    lines.append(
        f"\n请生成第 {page.page_number} 页的完整 SVG 代码。"
        "\n\n**重要提醒**：绝对禁止使用任何 Emoji 表情符号（如 🔍📋💡🎯✨🕐 等），"
        "改用 SVG 图形（圆形+数字、色块、箭头 polygon）或纯文字符号（●、→、①②③）。"
    )
    return "\n".join(lines)
