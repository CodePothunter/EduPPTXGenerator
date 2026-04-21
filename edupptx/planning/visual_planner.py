"""Phase 1b: visual planning with optional template palette guidance."""

from __future__ import annotations

import json
import re

from loguru import logger

from edupptx.config import Config
from edupptx.llm_client import create_llm_client
from edupptx.models import PlanningDraft, VisualPlan

_SYSTEM_PROMPT = """你是一位教育演示文稿的视觉设计顾问。

请根据 PPT 主题、页面结构和可能给定的模板调色板，输出一份统一视觉方案 JSON。

## 输出要求
只输出一个 JSON 对象，字段包括：
```json
{
  "primary_color": "#hex",
  "secondary_color": "#hex",
  "accent_color": "#hex",
  "background_prompt": "英文背景描述",
  "card_bg_color": "#hex",
  "secondary_bg_color": "#hex",
  "text_color": "#hex",
  "heading_color": "#hex",
  "content_density": "lecture"
}
```

## 原则
- 教育场景优先：可读、克制、清晰
- 如果用户或模板已经给出调色板，优先沿用，不要重新发明另一套主色
- `background_prompt` 应描述淡雅、低干扰、适合承载文字的背景
- `content_density` 只能是 `lecture` 或 `review`
- `accent_color` 只用于重点，不要做大面积背景色"""


def _apply_palette_hint(visual_plan: VisualPlan, palette_hint) -> VisualPlan:
    if palette_hint is None:
        return visual_plan
    visual_plan.primary_color = palette_hint.primary_color
    visual_plan.secondary_color = palette_hint.secondary_color
    visual_plan.accent_color = palette_hint.accent_color
    visual_plan.card_bg_color = palette_hint.card_bg_color
    visual_plan.secondary_bg_color = palette_hint.secondary_bg_color
    visual_plan.text_color = palette_hint.text_color
    visual_plan.heading_color = palette_hint.heading_color
    if palette_hint.background_prompt:
        if visual_plan.background_prompt and palette_hint.background_prompt not in visual_plan.background_prompt:
            visual_plan.background_prompt = f"{palette_hint.background_prompt}; {visual_plan.background_prompt}"
        elif not visual_plan.background_prompt:
            visual_plan.background_prompt = palette_hint.background_prompt
    return visual_plan


def generate_visual_plan(
    draft: PlanningDraft,
    config: Config,
    palette_hint=None,
    template_label: str = "",
) -> VisualPlan:
    """Call LLM to generate a visual plan based on content planning."""
    try:
        client = create_llm_client(config)
    except Exception as exc:
        logger.warning("Visual planning client unavailable, using defaults: {}", exc)
        return _apply_palette_hint(VisualPlan(), palette_hint)

    page_types = [f"{p.page_number}. {p.page_type}: {p.title}" for p in draft.pages]
    user_prompt = (
        "## PPT 信息\n"
        f"- 主题：{draft.meta.topic}\n"
        f"- 受众：{draft.meta.audience or '通用'}\n"
        f"- 目的：{draft.meta.purpose or '教学演示'}\n"
        f"- 风格方向：{draft.meta.style_direction or '专业教育'}\n"
        f"- 页数：{len(draft.pages)}\n\n"
        "## 页面结构\n"
        + "\n".join(page_types)
    )

    if palette_hint is not None:
        user_prompt += (
            "\n\n## 已选模板调色板（优先遵守）\n"
            f"- template: {template_label or draft.style_routing.style_name or 'selected'}\n"
            f"- primary: {palette_hint.primary_color}\n"
            f"- secondary: {palette_hint.secondary_color}\n"
            f"- accent: {palette_hint.accent_color}\n"
            f"- card_bg: {palette_hint.card_bg_color}\n"
            f"- secondary_bg: {palette_hint.secondary_bg_color}\n"
            f"- text: {palette_hint.text_color}\n"
            f"- heading: {palette_hint.heading_color}\n"
            "请保留这套颜色关系，只补充适合的 background_prompt 和 content_density。"
        )

    try:
        response = client.chat(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=1024,
        )
        visual_plan = _parse_visual_plan(response)
        return _apply_palette_hint(visual_plan, palette_hint)
    except Exception as exc:
        logger.warning("Visual planning failed, using defaults: {}", exc)
        return _apply_palette_hint(VisualPlan(), palette_hint)


def _parse_visual_plan(response: str) -> VisualPlan:
    """Extract `VisualPlan` JSON from LLM response."""

    match = re.search(r"```json\s*\n(.*?)```", response, re.DOTALL)
    if match:
        text = match.group(1).strip()
    else:
        match = re.search(r"\{[\s\S]*\}", response)
        text = match.group(0) if match else "{}"

    try:
        data = json.loads(text)
        return VisualPlan.model_validate(data)
    except Exception as exc:
        logger.warning("Failed to parse visual plan JSON: {}", exc)
        return VisualPlan()
