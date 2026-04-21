"""SVG 生成的提示词工程 — V3 reference 文件组装。"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Literal

from loguru import logger

from edupptx.models import PagePlan, SlideAssets, VisualPlan, iter_image_slot_keys

_REFS_DIR = Path(__file__).parent / "references"
_PAGE_TEMPLATES_DIR = Path(__file__).parent / "page_templates"
_CHART_TEMPLATES_DIR = Path(__file__).parent / "chart_templates"

# Page types that map to one or more template stems.
# `section` keeps `session` as an alias so subject-specific template folders
# can provide divider pages without having to rename existing assets.
_PAGE_TYPE_TEMPLATE_STEMS = {
    "cover": ("cover",),
    "toc": ("toc",),
    "section": ("section",),
    "content": ("content",),
    "closing": ("closing",),
    # Other types (quiz, formula, experiment, etc.) fall back to content.svg
}

_MAX_TEMPLATE_CHARS = 3000  # Token budget per template
_DEFAULT_TEMPLATE_FAMILY = "basic"
_CHART_TEMPLATE_MAP = {
    "timeline": ("timeline.svg",),
    "relation": ("关系图.svg",),
}

_IMAGE_BOUNDARY_RULES = """
## 图片边界硬性规则

当你把 `<image>` 放进卡片或任何有边界的面板时，图片框本身必须完全落在该容器内部。

- 不要依赖 `clipPath`、`mask` 或 overflow hidden 去掩盖错误的图片框。
- 必须满足以下不等式：
  `image_x >= card_x`
  `image_y >= card_y`
  `image_x + image_width <= card_x + card_width`
  `image_y + image_height <= card_y + card_height`
- 如果卡片需要内边距，除非模板明确展示为贴边媒体，否则四周至少保留 12px 内边距。
- 如果不确定安全尺寸，宁可把图片做小，也不要做大。
- `<image>` 的 width/height 必须先根据卡片边界来确定，实际位图会在后续步骤再注入。
- 如果 `material_needs.images` 为某张图片指定了 `aspect_ratio`，则该图片对应的 SVG 图片框宽高比必须与该比例严格一致。
- 严禁把 `1:1` 的图片框画成 `4:3`、`16:9` 或其他比例；也严禁把 `4:3`/`3:4`/`16:9`/`9:16` 的图片框偷改成近似比例。
- 先决定图片框的 `width/height`，再检查 `width / height` 是否匹配规划比例；比例不匹配时，必须改框尺寸，不要硬塞图片。
- 对所有真实图片元素，默认添加 `preserveAspectRatio="xMidYMid slice"`，避免拉伸变形。
"""


def _truncate_template_content(content: str) -> str:
    if len(content) > _MAX_TEMPLATE_CHARS:
        return content[:_MAX_TEMPLATE_CHARS] + "\n<!-- ... 截断 ... -->\n</svg>"
    return content


def _template_stems_for_page_type(page_type: str) -> tuple[str, ...]:
    return _PAGE_TYPE_TEMPLATE_STEMS.get(page_type, ("content",))


def _load_page_templates(
    page_type: str,
    template_family: str = _DEFAULT_TEMPLATE_FAMILY,
) -> list[tuple[str, str]]:
    """Load all matching SVG reference templates for a page type and family."""
    target_stems = _template_stems_for_page_type(page_type)
    families_to_try: list[str] = []
    if template_family:
        families_to_try.append(template_family)
    if _DEFAULT_TEMPLATE_FAMILY not in families_to_try:
        families_to_try.append(_DEFAULT_TEMPLATE_FAMILY)

    for family in families_to_try:
        family_dir = _PAGE_TEMPLATES_DIR / family
        if not family_dir.exists():
            continue
        matches: list[Path] = []
        seen_paths: set[Path] = set()
        for stem in target_stems:
            exact_matches = sorted(family_dir.glob(f"{stem}.svg"))
            prefixed_matches = sorted(family_dir.glob(f"{stem}_*.svg"))
            for path in exact_matches + prefixed_matches:
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                matches.append(path)
        if not matches:
            continue
        loaded: list[tuple[str, str]] = []
        for path in matches:
            if path.suffix.lower() != ".svg":
                continue
            content = _truncate_template_content(path.read_text(encoding="utf-8"))
            loaded.append((f"{family}/{path.name}", content))
        if loaded:
            return loaded
    return []


def _load_chart_templates(page_type: str, layout_hint: str | None = None) -> list[tuple[str, str]]:
    keys_to_try: list[str] = []
    if page_type:
        keys_to_try.append(page_type)
    if layout_hint and layout_hint not in keys_to_try:
        keys_to_try.append(layout_hint)

    loaded: list[tuple[str, str]] = []
    for key in keys_to_try:
        for filename in _CHART_TEMPLATE_MAP.get(key, ()):
            path = _CHART_TEMPLATES_DIR / filename
            if not path.exists():
                continue
            content = _truncate_template_content(path.read_text(encoding="utf-8"))
            loaded.append((filename, content))
    return loaded


def _load_ref(name: str) -> str:
    """Load a reference markdown file."""
    path = _REFS_DIR / name
    if not path.exists():
        logger.warning("Reference file not found: {} — prompt quality may degrade", path)
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
    parts.append(_IMAGE_BOUNDARY_RULES)

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
    reference_svg: str | None = None,
    template_family: str = _DEFAULT_TEMPLATE_FAMILY,
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

    lines.append(
        "\n### 行内高亮文本规则\n"
        "如果一句正文里需要局部高亮，必须把整句话写在同一个 `<text>` 元素内，"
        "只允许使用同层、连续的 `<tspan>` 来切分前文 / 高亮词 / 后文。\n"
        "- 禁止把同一句话拆成多个独立 `<text>` 元素\n"
        "- 禁止生成嵌套 `<tspan>`；只允许一层 sibling `<tspan>`\n"
        "- 高亮词前后的正文必须完整保留，按阅读顺序连续输出，不能只剩高亮词\n"
        "- 同一行内连续 `<tspan>` 默认不要重复设置 `x` 或 `dy`；只有真正换行时才设置新的 `x` 与 `dy`\n"
        "推荐写法示例：\n"
        "```svg\n"
        '<text x="128" y="632" font-size="20" fill="{text_color}">\n'
        '  <tspan x="128" dy="0">“脑袋”的“袋”读</tspan>\n'
        '  <tspan fill="{accent_color}" font-weight="bold">轻声</tspan>\n'
        '  <tspan>，“眼睛”的“睛”是</tspan>\n'
        '  <tspan fill="{accent_color}" font-weight="bold">目字旁</tspan>\n'
        "</text>\n"
        "```"
    )

    if page.layout_hint in {"bento_2col_equal", "bento_2col_asymmetric", "bento_3col"}:
        point_count = len(page.content_points or [])
        if "stacked_subcards" in (page.design_notes or "") or 3 <= point_count <= 5:
            lines.append(
                "\n### 内部子卡片优先规则\n"
                "如果某一张大卡片承载的是 3–5 个同级短要点，请优先使用内部子卡片模式（`stacked_subcards`），"
                "不要直接输出成长编号列表或大段正文。\n"
                "- 子卡片只允许上下纵向堆叠\n"
                "- 每张子卡片承载“短标题 + 1–2 行说明”\n"
                "- 子卡片数量优先与该卡片内的同级短要点数量一致（允许 2–5 个）\n"
                "- 只有在均分后高度明显不足，或该大卡片本身是大面积图片区时，才回退为普通列表"
            )

    if page.reveal_from_page:
        reveal_mode_text = {
            "highlight_correct_option": "仅高亮正确选项或正确判断，不改动原选项卡位置与文字换行。",
            "show_answer": "仅在原留白位置或答案区补充答案，不改动原题干与留白布局。",
        }.get(page.reveal_mode, "仅新增答案揭晓层，不改动原布局。")
        lines.append(
            "\n### 伪动画答案揭晓页\n"
            f"本页是第 {page.reveal_from_page} 页的答案揭晓页，必须复用上一页版式，不能重新布局。\n"
            "- 保持原有题目卡、选项卡、文本块、图片、图标的 x/y/width/height、font-size、换行与对齐方式不变\n"
            "- 只允许新增答案、高亮、勾选、角标、描边等叠加层，不允许移动、缩放、删除或重排原有元素\n"
            f"- 揭晓方式：{reveal_mode_text}"
        )
        if reference_svg:
            lines.append(
                "\n### 上一页参考 SVG\n"
                "以下是必须复用的上一页完整 SVG。请以它为基底保留全部现有元素，只添加答案揭晓层，并返回完整 SVG。\n"
                f"```svg\n{reference_svg}\n```"
            )

    # 图片处理：debug 模式用描述占位，正常模式用 __IMAGE__ token
    if debug:
        # Debug mode: describe image needs as visual placeholders
        image_needs = page.material_needs.images if page.material_needs.images else []
        if image_needs:
            lines.append("\n### 图片占位（Debug 模式）")
            lines.append(
                "以下位置需要插图。请用**虚线矩形 + 居中灰色描述文字**标注图片区域。\n"
                "**重要**：占位框的宽高比必须使用以下预定比例之一：\n"
                "1:1, 4:3, 3:4, 16:9, 9:16, 3:2, 2:3, 21:9\n"
            )
            for img in image_needs:
                ratio = img.aspect_ratio
                lines.append(f"- {img.role} (比例 {ratio}): {img.query}")
            lines.append(
                "\n占位示例（注意宽高比必须匹配预定比例）：\n"
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
        # Build role→ratio mapping from page's image needs
        for slot_key, img in iter_image_slot_keys(page.material_needs.images or []):
            if slot_key not in assets.image_paths:
                continue
            slot_label = slot_key.upper()
            role = slot_key
            ratio = img.aspect_ratio
            image_lines.append(
                f'- **{role}** (比例 {ratio}) 图片可用，'
                f'请用 `<image href="__IMAGE_{role.upper()}__" .../>` 作为占位'
            )
        if image_lines:
            lines.append("\n### 可用图片资源")
            lines.extend(image_lines)
            lines.append(
                "\n**必须** 在合适位置放置 `<image>` 元素。"
                ' 使用 `href="__IMAGE_HERO__"` 或 `href="__IMAGE_ILLUSTRATION__"` 等占位符。'
                " 系统会自动替换为真实图片。`<image>` 的宽高比**必须严格匹配**上面标注的比例。"
                " 先按规划比例确定图片框，再填写 `x/y/width/height`；不要先随意画框，再拿图片去凑。"
                " 如果规划比例是 `1:1`，图片框就必须满足 `width = height`；如果是 `4:3`，就必须满足 `width / height ≈ 1.333`；如果是 `16:9`，就必须满足 `width / height ≈ 1.778`。"
                ' 每个真实图片元素默认写成 `<image ... preserveAspectRatio="xMidYMid slice"/>`，不要省略。'
            )

    # 可用图标
    if assets.icon_svgs:
        icon_names = ", ".join(assets.icon_svgs.keys())
        lines.append(f"\n### 可用图标\n可用图标名: {icon_names}")
        lines.append(
            "使用占位符语法嵌入图标（系统自动替换为实际图标 SVG）：\n"
            "```svg\n"
            '<use data-icon="图标名" x="100" y="200" width="48" height="48" fill="{primary_color}"/>\n'
            "```\n"
            "`data-icon` 的值必须是上面列出的图标名之一。"
            "设置合理的 x, y, width, height 属性控制位置和大小。"
        )
    elif page.material_needs.icons:
        # Debug mode: icons not fetched, but hint the LLM to use SVG decorations
        lines.append("\n### 装饰元素提示")
        lines.append(
            "本页建议使用以下视觉元素（请用 SVG 图形替代，不要用 emoji）：\n"
            f"图标关键词: {', '.join(page.material_needs.icons)}\n"
            "实现方式：用主色圆形 `<circle>` 内放白色数字序号（1, 2, 3...），"
            "或用辅色矩形做标签。不要使用 Unicode 特殊符号（如 ▭、◆、▶ 等），"
            "这些符号跨平台渲染不一致。"
        )

    # 页面 SVG 参考模板（LLM 照着画，不是填充模板）
    template_svgs = _load_page_templates(page.page_type, template_family=template_family)
    if template_svgs:
        loaded_families = ", ".join(sorted({name.split("/", 1)[0] for name, _ in template_svgs}))
        lines.append(
            "\n### 参考模板家族\n"
            f"当前页面优先参考 `{template_family}` 模板目录。实际导入模板来源：{loaded_families}。"
            "请综合下面所有同页型参考模板的布局结构和视觉风格生成新的 SVG，"
            "但不要复制模板中的具体文字内容。"
        )
        for template_name, template_svg in template_svgs:
            lines.append(f"\n#### 参考模板：{template_name}\n```svg\n{template_svg}\n```")

    chart_templates = _load_chart_templates(page.page_type, page.layout_hint)
    if chart_templates:
        lines.append(
            "\n### 图示结构参考模板\n"
            "以下模板用于约束图示结构本身。请优先复用其节点组织方式、连接关系和整体构图，"
            "但必须替换成当前页面的真实内容。"
        )
        for template_name, template_svg in chart_templates:
            lines.append(f"\n#### 图示参考：chart_templates/{template_name}\n```svg\n{template_svg}\n```")

    # 页面类型特殊说明
    type_hints = {
        "cover": (
            "这是封面页。设计要求：\n"
            "1. 必须使用 center_hero 布局：一张大卡片（w≥900, h≥350）居中\n"
            "2. 主标题 font-size=56-72，加粗，居中\n"
            "3. 副标题在主标题下方 40px，font-size=20-24\n"
            "4. 用装饰圆形、渐变色块填充空白区域，体现主题氛围\n"
            "5. 页面不能有大面积空白——标题区上下都要有视觉元素\n"
            "6. 默认 `material_needs.images = []`；若该字段为空，不要自行新增任何前景 `<image>`"
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
            "1. 章节标题要大（font-size=40-48），居中\n"
            "2. 下方配一句引导语或本章概述（font-size=18-20）\n"
            "3. 可用装饰图形（圆形、线条、色块）填充，体现视觉节奏\n"
            "4. 如有图片占位，居中大尺寸展示"
            "5. 必须使用 center_hero 布局"
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
        "exercise": (
            "这是练习题页面。设计要求：\n"
            "1. 题目页优先保持清晰的题干区和答题留白区\n"
            "2. 如果本页是答案揭晓页，只在原留白区或答案标注区补充答案，不重新布局\n"
            "3. 答题区、下划线、留白框与题干文本的位置必须稳定，避免前后页切换错位"
        ),
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
            "2. 每步有序号圆 + 公式 + 文字说明\n"
            "3. 最后一步（结论）用强调色卡片高亮\n"
            "4. 参考 page-types.md 中 formula 类型的布局定义\n"
            '5. **公式必须使用 data-latex 属性标记**，例如：\n'
            '   `<text data-latex="\\frac{a}{b}" fill="#1E293B">a/b</text>`\n'
            "   系统会自动将 LaTeX 渲染为高质量图片"
        ),
        "experiment": (
            "这是实验步骤页。设计要求：\n"
            "1. 左窄右宽 (3:7) 布局\n"
            "2. 左侧：器材列表卡片，每项配图标\n"
            "3. 右侧：步骤编号列表 + 底部结论高亮卡片\n"
            "4. 参考 page-types.md 中 experiment 类型的布局定义"
        ),
        "timeline": (
            "这是时间线页。设计要求：\n"
            "1. 时间线节点数量必须严格等于 content_points 数量，按从左到右顺序排列\n"
            "2. 如果 material_needs.images 非空，则每个节点必须对应一张图片，图片数量、节点数量、content_points 数量三者必须一致\n"
            "3. 图片槽位必须按顺序一一对应：第 1 个节点只能使用第 1 个图片槽位，第 2 个节点只能使用第 2 个图片槽位，不能跳号、复用或回退到前面的槽位\n"
            "4. 所有节点圆心、图片框、下方文字卡都必须完整落在安全区 x=50..1230 内，最后一个节点不能越过右边界\n"
            "5. 默认将节点圆心均匀分布在 x=140..1140 之间；箭头位于相邻节点中点；不要把 5 个以上节点继续按固定大间距硬排\n"
            "6. 每个节点使用一个独立 <g> 包裹图片、圆点、说明卡和文字，便于后处理整体微调"
        ),
        "relation": (
            "这是关系图页。设计要求：\n"
            "1. 使用中心节点 + 分支节点 + 连接线/箭头的关系图结构，而不是普通列表或表格。\n"
            "2. 将 content_points 解释为关系节点、分支节点或关系陈述，优先组织成 3-6 个短节点。\n"
            "3. 若存在核心概念，放在中心或左侧主节点；其余节点按层级或因果关系分布在周围。\n"
            "4. 每个节点使用独立 `<g>` 包裹节点框和文字，连接线必须指向明确，不要压到文本上。\n"
            "5. 所有节点、箭头和连接线必须完整落在安全区内，不要越出 x=50..1230、y=100..650。\n"
            "6. 优先参考图示结构模板，保持关系图的构图感，不要退化为普通竖排卡片。"
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
    '''
    type_hints["cover"] = (
        "这是封面页。\n"
        "1. 必须使用 center_hero 布局。\n"
        "2. 不要绘制任何铺满整页的背景矩形、渐变矩形，或覆盖大部分画布的遮罩矩形。\n"
        "3. 不要创建任何大型不透明卡片。避免使用宽度超过 700 或高度超过 260 的填充矩形，除非它们只是很小的强调标签。\n"
        "4. 标题和副标题应直接放在背景上，只使用轻量装饰，例如短下划线、小标签或细描边装饰。\n"
        "5. 如果 `layout_hint = center_hero` 且 `material_needs.images = []`，不要添加任何前景 `<image>`，也不要用大块纯色遮罩去模拟 hero 区域。\n"
        "6. 如果确实需要小范围文字衬底，必须保持局部且柔和，并满足 fill-opacity <= 0.12。\n"
    )
    type_hints["section"] = (
        "这是章节过渡页。\n"
        "1. 必须使用 center_hero 布局。\n"
        "2. 不要绘制任何整页填充矩形，也不要在标题后放置大型居中的不透明卡片。\n"
        "3. 只使用轻量装饰：短下划线、细长标签、小描边徽标，或细微的角落装饰。\n"
        "4. 如果为了可读性确实需要衬底，它必须保持小范围、局部且柔和。避免使用宽度超过 700 或高度超过 240 的填充矩形，并保持 fill-opacity <= 0.12。\n"
        "5. 让背景图在画布的大部分区域保持可见。\n"
    )
    '''
    type_hints["toc"] = (
        "这是一个使用纵向列表横向卡片的 TOC 页面。\n"
        "1. 将 TOC 卡片视为固定高度的导航卡片，而不是可伸缩的内容容器。\n"
        "2. 如果有 4 个 TOC 卡片，每张卡片高度必须至少为 104px；如果有 5 个 TOC 卡片，每张卡片高度必须至少为 96px。\n"
        "3. 整个 TOC 卡片堆叠区域必须保持在 y=110..650 范围内。不要依赖 step6 去扩展卡片高度。\n"
        "4. 每张 TOC 卡片最多只能包含一行简短标题加一行简短描述，或者总共最多两行简短文本。\n"
        "5. 如果文本放不下，应缩短措辞，而不是把卡片压缩到低于最小高度。\n"
        "6. 使用稳定的卡片间距：4 张卡片时为 14-16px，5 张卡片时为 10-12px。\n"
    )
    if page.page_type in type_hints:
        lines.append(f"\n### 页面类型提示\n{type_hints[page.page_type]}")

    lines.append(
        f"\n请生成第 {page.page_number} 页的完整 SVG 代码。"
        "\n\n**重要提醒**：绝对禁止使用任何 Emoji 表情符号（如 🔍📋💡🎯✨🕐 等），"
        "改用 SVG 图形（圆形+数字、色块、箭头 polygon）或纯文字符号（●、→、①②③）。"
    )
    if page.material_needs.images:
        lines.append(
            "\n### 图片边界硬性规则\n"
            "如果 `<image>` 放在卡片 `<rect>` 内部，图片框必须完全落在该卡片内部。"
            "必须严格满足以下不等式："
            "`image_x >= card_x`, "
            "`image_y >= card_y`, "
            "`image_x + image_width <= card_x + card_width`, "
            "`image_y + image_height <= card_y + card_height`。"
            "同时，图片框的宽高比必须与该图片在 `material_needs.images` 中声明的 `aspect_ratio` 严格一致。"
            "不要把规划为横图的图片画成方图，也不要把规划为方图的图片画成长图。"
            "如果图片框比例与规划比例冲突，优先修改图片框尺寸，绝不要通过拉伸图片来适配。"
            '所有真实图片默认添加 `preserveAspectRatio="xMidYMid slice"`。'
            "不要假设 `clipPath` 或遮罩能隐藏溢出。"
            "如果安全尺寸不明确，就缩小图片，并至少保留 12px 内边距。"
        )
    return "\n".join(lines)
