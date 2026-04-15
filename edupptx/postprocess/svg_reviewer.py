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

1. **文字溢出/重叠**：检查 <text> 元素的 y 坐标是否在其所属 <rect> 的 y~y+height 范围内；相邻文字 y 差是否 ≥ font-size
2. **卡片边界**：所有元素的 x ∈ [0, 1280], y ∈ [0, 720]，卡片不超出画布
3. **配色一致性**：检查颜色是否符合指定的主题色方案
4. **布局平衡**：卡片之间有合理间距（≥20px），不拥挤也不过于稀疏
5. **内容完整性**：确认页面标题、内容要点都已呈现，没有遗漏
6. **字体一致**：所有 <text> 必须使用 font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"

## 输出要求

直接输出修正后的完整 SVG 代码，用 ```svg 和 ``` 包裹。
如果没有需要修改的地方，也要输出原始 SVG 代码（保持不变）。
不要输出任何解释文字，只要 SVG 代码。
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
