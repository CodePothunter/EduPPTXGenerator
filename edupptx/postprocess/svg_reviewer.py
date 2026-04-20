"""LLM-based SVG review: validate generated SVG and fix issues."""

from __future__ import annotations

import re

from loguru import logger

from edupptx.config import Config
from edupptx.llm_client import create_llm_client
from edupptx.models import PagePlan, VisualPlan

_REVIEW_SYSTEM_PROMPT = """\
你是 SVG 质量审核专家。你的任务是审查一个 PPT 页面的 SVG 代码，结合自动检测到的问题列表，输出修正后的完整 SVG。

## 审查重点

1. **页面标题位置**：对除 `center_hero` 布局之外的页面，页面标题必须在 x=50, y=50 附近（font-size=28-36），副标题在 y=78-90。如果标题 y > 100 或被其他元素遮挡，必须修正到标准位置
2. **文字溢出/重叠**：检查 <text> 的 y 坐标是否在其所属 <rect> 的 y~y+height 范围内。特别注意 <tspan dy="..."> 会让实际渲染位置下移，最后一个 tspan 的累加 y 不能超出卡片底部
3. **圆形编号对齐**：<circle> + <text> 组成的序号组件，text 的 y 必须等于 circle 的 cy，text 的 x 必须等于 circle 的 cx。如果发现 text y 比 circle cy 大 20px 以上，修正 text y = circle cy
4. **卡片边界**：所有卡片和内容元素 x ≥ 50，x+width ≤ 1230，不超出画布。唯一例外是顶部装饰条（height≤8 的全宽 rect）允许 x=0
5. **配色一致性**：检查颜色是否符合指定的主题色方案
6. **布局平衡**：卡片之间有合理间距（≥20px），不拥挤也不过于稀疏
7. **内容完整性**：确认页面标题、内容要点都已呈现，没有遗漏
8. **字体一致**：所有 <text> 必须使用 font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
9. **禁止 Emoji**：SVG 中不能出现任何 Emoji 表情符号，用 SVG 图形或文字符号（●→①）替代
10. **对齐一致性 (Alignment)**：检查同类元素是否对齐——所有卡片的 x 坐标是否一致？标题和正文的 x 是否统一？左侧列元素的 x 应相同。如有 2-5px 偏差，修正为统一值。
11. **对比层次 (Contrast)**：检查字号是否形成层次——标题 ≥28px，正文 16-24px，标注 ≤14px。如层次不清，调整关键字号。确保主色和强调色有足够的视觉区分。
12. **重复统一 (Repetition)**：检查同类元素的视觉一致性——所有卡片的 rx 值是否相同？颜色是否遵循配色方案？间距是否统一？如有不一致，统一为出现次数最多的值。
13. **邻近分组 (Proximity)**：检查相关内容是否在空间上靠近——标题与正文的间距应 < 卡片之间的间距。不同内容分组之间应有明确的间距分隔（≥20px）。
14. **表格结构保护**：对于 `comparison` 页面或 `comparison` 布局，必须保留原始表格的行数、列数、表头行、列分隔线、行高顺序和单元格内容顺序。不要把表格重写成普通卡片布局；只允许在单元格内部微调文字位置、字号和换行。
15. **行内高亮保护**：如果一句正文使用同一个 `<text>` 内的连续 `<tspan>` 做局部高亮，必须保留前文、高亮词和后文的连续关系。不要把同一句话拆成多个独立 `<text>`，不要生成嵌套 `<tspan>`，不要丢失高亮词前后的正文。

## 输出要求

直接输出修正后的完整 SVG 代码，用 ```svg 和 ``` 包裹。
如果没有需要修改的地方，也要输出原始 SVG 代码（保持不变）。
不要输出任何解释文字，只要 SVG 代码。
"""

_REVIEW_SYSTEM_PROMPT = _REVIEW_SYSTEM_PROMPT.replace(
    "对除 `center_hero` 布局之外的页面，页面标题必须在 x=50, y=50 附近（font-size=28-36），副标题在 y=78-90。如果标题 y > 100 或被其他元素遮挡，必须修正到标准位置",
    "对除 `center_hero` 布局和 `section/session` 分节页之外的页面，页面标题必须在 x=50, y=50 附近（font-size=28-36），副标题在 y=78-90。如果标题 y > 100 或被其他元素遮挡，必须修正到标准位置。`section/session` 分节页允许保留居中标题、居中副标题和居中分隔装饰，不要强制挪到顶部标题区",
)


_REVIEW_SYSTEM_PROMPT += """

## 图片边界硬性规则

- 如果某个 `<image>` 属于卡片或其他有边界的面板，图片框本身必须完全落在该容器内部。
- 必须满足以下不等式：
  `image_x >= card_x`
  `image_y >= card_y`
  `image_x + image_width <= card_x + card_width`
  `image_y + image_height <= card_y + card_height`
- 不要假设 `clipPath`、`mask` 或 overflow hidden 能挽救错误的图片框。
- 如果图片对卡片来说过大，就缩小图片，或把它向内移动。
- 与其溢出，不如使用更小但安全的图片框。
"""

def review_and_fix_svg(
    svg_content: str,
    warnings: list[str],
    page: PagePlan,
    visual: VisualPlan,
    config: Config,
) -> str:
    """Send SVG + warnings to LLM for review and get fixed version.

    Returns the reviewed SVG (or original if review fails).
    """
    client = create_llm_client(config, web_search=False)

    # Build user prompt with SVG + context
    warnings_text = "\n".join(f"- {w}" for w in warnings) if warnings else "（无自动检测问题）"

    user_prompt = (
        f"## 页面信息\n"
        f"- 第 {page.page_number} 页：{page.title}\n"
        f"- 类型：{page.page_type}\n"
        f"- 布局：{page.layout_hint}\n\n"
        f"## 主题色方案\n"
        f"- 主色: {visual.primary_color}\n"
        f"- 辅色: {visual.secondary_color}\n"
        f"- 卡片背景: {visual.card_bg_color}\n"
        f"- 标题色: {visual.heading_color}\n"
        f"- 正文色: {visual.text_color}\n\n"
        f"## 自动检测到的问题\n{warnings_text}\n\n"
        f"## SVG 代码\n```svg\n{svg_content}\n```\n\n"
        f"请审查以上 SVG，修正所有问题后输出完整 SVG。"
    )
    if page.page_type == "comparison" or page.layout_hint == "comparison":
        user_prompt += (
            "\n\n## comparison 页额外要求\n"
            "- 保留原始表格结构：表头、列分隔线、各数据行、单元格顺序都不能改\n"
            "- 不要新增或删除行列，不要改成普通卡片布局\n"
            "- 只允许在单元格内部微调文本位置和换行"
        )
    if page.page_type == "section":
        user_prompt += (
            "\n\n## section/session 页面额外要求\n"
            "- 这是分节页。允许主标题、副标题和分隔装饰保持居中构图。\n"
            "- 不要把分节页改写成普通内容页，也不要把主标题强行移动到顶部标题区 x=50, y=50。\n"
            "- 如果原 SVG 已经是居中主标题 + 居中副标题/分隔线，请优先保留这种结构。"
        )
    if "<tspan" in svg_content:
        user_prompt += (
            "\n\n## 行内高亮额外要求\n"
            "- 如果原始 SVG 已经用同一个 `<text>` 内的连续 `<tspan>` 表示局部高亮，保持这种结构，不要拆成多个 `<text>`\n"
            "- 不要生成嵌套 `<tspan>`\n"
            "- 修正时必须同时保留高亮词前文和后文，不能只留下高亮片段"
        )

    try:
        response = client.chat(
            messages=[
                {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=16384,
        )
        reviewed = _extract_svg(response)
        if reviewed and "<svg" in reviewed:
            logger.info("Slide {} reviewed by LLM ({} chars)", page.page_number, len(reviewed))
            return reviewed
        logger.warning("Slide {} LLM review returned invalid SVG, keeping original", page.page_number)
        return svg_content
    except Exception as e:
        logger.warning("Slide {} LLM review failed: {}", page.page_number, e)
        return svg_content


def _extract_svg(response: str) -> str:
    m = re.search(r"```svg\s*\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"```xml\s*\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"(<svg[\s\S]*?</svg>)", response)
    if m:
        return m.group(1).strip()
    return response.strip()
