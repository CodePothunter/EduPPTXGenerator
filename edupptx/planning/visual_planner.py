"""Phase 1b: visual planning with optional template palette guidance."""

from __future__ import annotations

import json
import re
from typing import Any

import mistune
import yaml
from loguru import logger

from edupptx.config import Config
from edupptx.llm_client import create_llm_client
from edupptx.models import PlanningDraft, VisualPlan
from edupptx.style.design_md import PROSE_HEADINGS, parse_design_md, serialize_style
from edupptx.style_schema import (
    DecorationTokens,
    GlobalTokens,
    LayoutTokens,
    SchemaMeta,
    SemanticTokens,
    StyleSchema,
)

_SYSTEM_PROMPT = """你是一位教育演示文稿的视觉设计顾问。

请根据 PPT 主题、页面结构和可能给定的模板调色板，输出一份统一视觉方案 JSON。

## 输出要求
只输出一个 JSON 对象，字段包括：
```json
{
  "primary_color": "#hex",
  "secondary_color": "#hex",
  "accent_color": "#hex",
  "background_prompt": "中文背景描述",
  "card_bg_color": "#hex",
  "secondary_bg_color": "#hex",
  "text_color": "#hex",
  "heading_color": "#hex",
  "content_density": "lecture"
}
```

## 原则
- 教育场景优先：可读、克制、清晰
- 如果用户或模板已经给出调色参考，可以参考其色相与气质，但不要机械照抄
- `background_prompt` 必须使用中文，描述淡雅、低干扰、适合承载文字的背景
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
    if getattr(palette_hint, "background_color_bias", "") and not visual_plan.background_color_bias:
        visual_plan.background_color_bias = palette_hint.background_color_bias
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
            "\n\n## 已选模板调色参考（仅供参考，不要机械照抄）\n"
            f"- template: {template_label or draft.style_routing.style_name or 'selected'}\n"
            f"- primary: {palette_hint.primary_color}\n"
            f"- secondary: {palette_hint.secondary_color}\n"
            f"- accent: {palette_hint.accent_color}\n"
            f"- card_bg: {palette_hint.card_bg_color}\n"
            f"- secondary_bg: {palette_hint.secondary_bg_color}\n"
            f"- text: {palette_hint.text_color}\n"
            f"- heading: {palette_hint.heading_color}\n"
            f"- background_color_bias: {getattr(palette_hint, 'background_color_bias', '') or 'none'}\n"
            "请结合主题内容、背景气质和页面结构自行决定最终配色；"
            "可以参考这组颜色的明度、纯度和亲和感，但不要被它锁死。"
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


# ── Layer 2: DESIGN.md generation ────────────────────────


_DESIGN_MD_FULL_SYSTEM = """你是教育演示文稿视觉设计师。请为主题「{topic}」（受众：{audience}）输出
DESIGN.md 草案，严格遵循以下格式（不要任何 ``` 包裹，不要解释）：

---
schema_version: "1.0"
name: <风格名，2-4 字中文>
audience: {audience}
domain: <学科领域>
colors:
  primary: <#hex>
  accent: <#hex>
  bg: <#hex>
  card_fill: <#hex>
  text: <#hex>
  text_secondary: <#hex>
  shadow: <#hex>
  icon: <#hex>
typography:
  title:      {{ fontFamily: Noto Sans SC, fontSize: 38pt, fontWeight: 700 }}
  card-title: {{ fontFamily: Noto Sans SC, fontSize: 16pt, fontWeight: 600 }}
  body:       {{ fontFamily: Noto Sans SC, fontSize: 12pt }}
spacing:
  margin: comfortable
  card_gap: normal
rounded:
  sm: 4px
  md: 8px
  lg: 16px
---

## Overview
2–3 句中文，描述情绪基调与受众契合。

## Colors
对每个颜色用一句话解释“为什么是这个色”，强调教育场景约束。

## Typography
说明字体策略。硬约束：body ≥ 12pt，card-title ≥ 16pt，CJK 优先 Noto Sans SC。

## Layout
1280×720 viewBox，Bento Grid。说明本主题适合哪几种布局。

## Elevation
深度通过什么表达：阴影/边框/底色对比。教育 PPT 不建议大阴影。

## Shapes
圆角策略 + 选择理由。

## Components
至少定义：card-knowledge / card-formula / card-quote / card-stat。
每个给 backgroundColor / textColor / rounded（用 token 引用如 {{colors.primary}}）。

## Do's and Don'ts
本风格的 3 条守门规则。"""


_DESIGN_MD_PROSE_SYSTEM = """你是教育演示文稿视觉设计师。下面给出一组已确定的调色板（来自模板），
请围绕这些颜色为主题「{topic}」（受众：{audience}）撰写 DESIGN.md 的 8 段中文 prose。
**只输出 8 个 H2 段，不要 YAML frontmatter，不要任何 ``` 包裹，不要解释。**

调色板（已锁定，请围绕这些颜色撰写）：
- primary: {primary}
- accent: {accent}
- bg: {bg}
- card_fill: {card_fill}
- text: {text}
- text_secondary: {text_secondary}
- shadow: {shadow}
- icon: {icon}
模板标签：{template_label}

请严格按以下顺序输出 8 个 H2：

## Overview
2–3 句中文，描述情绪基调与受众契合。

## Colors
对每个颜色用一句话解释“为什么是这个色”，强调教育场景约束。请引用上面已锁定的色值。

## Typography
说明字体策略。硬约束：body ≥ 12pt，card-title ≥ 16pt，CJK 优先 Noto Sans SC。

## Layout
1280×720 viewBox，Bento Grid。说明本主题适合哪几种布局。

## Elevation
深度通过什么表达：阴影/边框/底色对比。教育 PPT 不建议大阴影。

## Shapes
圆角策略 + 选择理由。

## Components
至少定义：card-knowledge / card-formula / card-quote / card-stat。
每个给 backgroundColor / textColor / rounded（用 token 引用如 {{colors.primary}}）。

## Do's and Don'ts
本风格的 3 条守门规则。"""


def _page_structure_summary(draft: PlanningDraft) -> str:
    page_lines = [f"{p.page_number}. {p.page_type}: {p.title}" for p in draft.pages]
    return (
        "## PPT 信息\n"
        f"- 主题：{draft.meta.topic}\n"
        f"- 受众：{draft.meta.audience or '通用'}\n"
        f"- 目的：{draft.meta.purpose or '教学演示'}\n"
        f"- 风格方向：{draft.meta.style_direction or '专业教育'}\n"
        f"- 页数：{len(draft.pages)}\n\n"
        "## 页面结构\n"
        + "\n".join(page_lines)
    )


def build_full_prompt(draft: PlanningDraft) -> tuple[str, str]:
    audience = draft.meta.audience or "通用"
    system = _DESIGN_MD_FULL_SYSTEM.format(
        topic=draft.meta.topic,
        audience=audience,
    )
    user = _page_structure_summary(draft)
    return system, user


def build_prose_only_prompt(
    draft: PlanningDraft,
    palette_hint: Any,
    template_label: str = "",
) -> tuple[str, str]:
    audience = draft.meta.audience or "通用"
    palette = _palette_from_hint(palette_hint)
    system = _DESIGN_MD_PROSE_SYSTEM.format(
        topic=draft.meta.topic,
        audience=audience,
        primary=palette["primary"],
        accent=palette["accent"],
        bg=palette["bg"],
        card_fill=palette["card_fill"],
        text=palette["text"],
        text_secondary=palette["text_secondary"],
        shadow=palette["shadow"],
        icon=palette["icon"],
        template_label=template_label or "未指定",
    )
    user = _page_structure_summary(draft)
    return system, user


def _count_h2_via_mistune(md_str: str) -> int:
    """Count H2 headings using mistune AST (skips fenced code blocks)."""
    md = mistune.create_markdown(renderer=None)
    try:
        tokens = md(md_str)
    except Exception:
        return 0
    count = 0
    for tok in tokens:
        if tok.get("type") == "heading" and (tok.get("attrs") or {}).get("level") == 2:
            count += 1
    return count


def _validate_8_sections(md_str: str) -> bool:
    """Validate that md_str parses cleanly with ≥5 palette keys and ≥8 H2 sections."""
    if not md_str or not md_str.strip():
        return False
    try:
        schema = parse_design_md(md_str)
    except Exception as exc:
        logger.debug("DESIGN.md parse failed in validation: {}", exc)
        return False
    palette = schema.global_tokens.palette or {}
    required_any = {"primary", "accent", "bg", "card_fill", "text"}
    if len(required_any & set(palette.keys())) < 5:
        return False
    # Count H2 sections in the original body (post frontmatter)
    body = md_str.split("---", 2)[-1] if md_str.lstrip().startswith("---") else md_str
    if _count_h2_via_mistune(body) < 8:
        return False
    return True


def _validate_prose(md_str: str) -> bool:
    if not md_str or not md_str.strip():
        return False
    return _count_h2_via_mistune(md_str) >= 8


def _default_palette() -> dict[str, str]:
    return {
        "primary": "#1F2937",
        "accent": "#059669",
        "bg": "#F0FDF4",
        "card_fill": "#FFFFFF",
        "text": "#1F2937",
        "text_secondary": "#4B5563",
        "shadow": "#6EE7B7",
        "icon": "#059669",
    }


def _palette_from_hint(palette_hint: Any) -> dict[str, str]:
    h = palette_hint
    return {
        "primary": getattr(h, "primary_color", "") or "#1F2937",
        "accent": getattr(h, "accent_color", "") or "#059669",
        "bg": getattr(h, "secondary_bg_color", "") or "#F8FAFC",
        "card_fill": getattr(h, "card_bg_color", "") or "#FFFFFF",
        "text": getattr(h, "text_color", "") or "#1F2937",
        "text_secondary": getattr(h, "secondary_color", "") or getattr(h, "text_color", "") or "#4B5563",
        "shadow": "#CBD5E1",
        "icon": getattr(h, "accent_color", "") or "#059669",
    }


def _compose_design_md(palette_hint: Any, prose_md: str, draft: PlanningDraft) -> str:
    """Build YAML frontmatter from palette_hint + standard defaults, prepend to prose."""
    palette = _palette_from_hint(palette_hint)
    name = draft.style_routing.style_name or "default"
    yaml_data = {
        "schema_version": "1.0",
        "name": name,
        "audience": draft.meta.audience or "",
        "colors": palette,
        "typography": {
            "title": {"fontFamily": "Noto Sans SC", "fontSize": "38pt", "fontWeight": 700},
            "card-title": {"fontFamily": "Noto Sans SC", "fontSize": "16pt", "fontWeight": 600},
            "body": {"fontFamily": "Noto Sans SC", "fontSize": "12pt"},
        },
        "spacing": {"margin": "comfortable", "card_gap": "normal"},
        "rounded": {"sm": "4px", "md": "8px", "lg": "16px"},
        "pptx-extensions": {
            # Mirrors DecorationTokens defaults — keeps roundtrip stable.
            "decorations": {
                "title_underline": True,
                "content_panel": True,
                "panel_alpha_pct": 35,
                "footer_separator": True,
                "quote_bar": True,
                "section_diamond": True,
                "closing_circle": True,
            },
            "card_shadow": {
                "blur_pt": 30,
                "dist_pt": 8,
                "color": "palette.shadow",
                "alpha_pct": 14,
            },
            "background": {"type": "diagonal_gradient", "seed_extra": ""},
            # Mirrors SemanticTokens defaults — full roundtrip.
            "semantic": {
                "subtitle_size_pt": 20,
                "footer_size_pt": 13,
                "formula_size_pt": 18,
                "card_corner_radius": 8000,
                "bg_overlay_alpha": 0.55,
            },
        },
    }
    fm = yaml.safe_dump(yaml_data, allow_unicode=True, sort_keys=False)
    return f"---\n{fm}---\n\n{prose_md.strip()}\n"


def _default_prose_for_topic(topic: str) -> dict[str, str]:
    t = topic or "教育主题"
    return {
        "Overview": f"教育主题「{t}」的视觉风格强调清晰、克制与专业感，便于受众长时间阅读。",
        "Colors": "主色用于标题与重点元素，强调色仅用于关键节点；背景色保持淡雅，正文使用深色保证对比度。",
        "Typography": "硬约束：body ≥ 12pt，card-title ≥ 16pt，标题 38pt；CJK 优先 Noto Sans SC，西文回退 Arial。",
        "Layout": "采用 1280×720 viewBox 的 Bento Grid，常用 center_hero / vertical_list / bento_2col 等布局。",
        "Elevation": "通过浅色阴影或边框暗示卡片层级；教育 PPT 不建议使用深重阴影，避免视觉噪声。",
        "Shapes": "卡片使用 8px 圆角，关键容器可用 16px；保持几何统一，避免不规则形状。",
        "Components": "card-knowledge / card-formula / card-quote / card-stat 四类组件提供统一的内容容器。",
        "Do's and Don'ts": "Do 保持配色克制；Do 保证 WCAG AA 对比度；Don't 大面积使用强调色。",
    }


def _fallback_design_md(palette: dict[str, str], draft: PlanningDraft) -> str:
    schema = StyleSchema(
        meta=SchemaMeta(
            schema_version="1.0",
            name=draft.style_routing.style_name or "default",
            description="",
        ),
        global_tokens=GlobalTokens(palette=dict(palette)),
        semantic=SemanticTokens(),
        layout=LayoutTokens(),
        decorations=DecorationTokens(),
    )
    prose = _default_prose_for_topic(draft.meta.topic)
    # Ensure dict order matches PROSE_HEADINGS for canonical output
    ordered_prose = {h: prose.get(h, "") for h in PROSE_HEADINGS}
    return serialize_style(schema, prose_sections=ordered_prose)


def _call_llm(config: Config, prompts: tuple[str, str]) -> str | None:
    """Call LLM with (system, user) prompts. Returns None on any failure."""
    system, user = prompts
    try:
        client = create_llm_client(config)
    except Exception as exc:
        logger.warning("DESIGN.md LLM client unavailable: {}", exc)
        return None
    try:
        response = client.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
            max_tokens=2048,
        )
    except Exception as exc:
        logger.warning("DESIGN.md LLM call failed: {}", exc)
        return None
    return response or None


def generate_design_md(
    draft: PlanningDraft,
    config: Config,
    palette_hint: Any = None,
    template_label: str = "",
) -> str:
    """Return a DESIGN.md string. Two prompt paths + fallback.

    - palette_hint provided → ask LLM only for prose (palette is locked)
    - no hint → ask LLM for full DESIGN.md
    LLM all-fail → _fallback_design_md (does not block Phase 3).
    """
    has_hint = palette_hint is not None

    if has_hint:
        prompt = build_prose_only_prompt(draft, palette_hint, template_label)
        prose_md = _call_llm(config, prompt)
        if prose_md is None or not _validate_prose(prose_md):
            return _fallback_design_md(_palette_from_hint(palette_hint), draft)
        return _compose_design_md(palette_hint, prose_md, draft)

    prompt = build_full_prompt(draft)
    full_md = _call_llm(config, prompt)
    if full_md is None or not _validate_8_sections(full_md):
        return _fallback_design_md(_default_palette(), draft)
    return full_md
