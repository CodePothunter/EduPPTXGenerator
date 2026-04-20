"""Phase 1: 策划稿生成 — 1 次 LLM 调用输出 PlanningDraft。"""

from __future__ import annotations

import json
import re

from loguru import logger

from edupptx.config import Config
from edupptx.llm_client import create_llm_client
from edupptx.models import InputContext, PlanningDraft
from edupptx.planning.prompts import (
    build_planning_system_prompt,
    build_planning_user_prompt,
)


def generate_planning_draft(ctx: InputContext, config: Config) -> PlanningDraft:
    """Generate a planning draft from input context via a single LLM call."""
    client = create_llm_client(config)

    system = build_planning_system_prompt()
    user = build_planning_user_prompt(
        topic=ctx.topic,
        requirements=ctx.requirements,
        source_text=ctx.source_text,
        research_summary=ctx.research_summary,
    )

    logger.info("Generating planning draft for topic={}", ctx.topic)
    response = client.chat(messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])

    draft = _parse_draft(response, ctx)
    logger.info("Planning draft: {} pages", len(draft.pages))
    return draft


def _parse_draft(response: str, ctx: InputContext) -> PlanningDraft:
    """Extract JSON from LLM response and parse into PlanningDraft."""
    # Try to extract JSON from markdown code block
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)```", response, re.DOTALL)
    raw = json_match.group(1).strip() if json_match else response.strip()

    # Clean common LLM artifacts
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("JSON parse failed, attempting repair: {}", e)
        # Try fixing common issues: trailing commas
        cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
        data = json.loads(cleaned)

    # Coerce unknown page_type/layout_hint to safe defaults
    _VALID_PAGE_TYPES = {"cover", "toc", "section", "content", "data", "case", "closing",
                         "timeline", "comparison", "exercise", "summary", "relation",
                         "quiz", "formula", "experiment"}
    _VALID_LAYOUT_HINTS = {
        "center_hero", "vertical_list", "bento_2col_equal", "bento_2col_asymmetric",
        "bento_3col", "hero_top_cards_bottom", "cards_top_hero_bottom",
        "mixed_grid", "full_image", "timeline", "comparison", "relation",
    }
    for page in data.get("pages", []):
        if page.get("page_type") not in _VALID_PAGE_TYPES:
            logger.warning("Unknown page_type '{}' → 'content'", page.get("page_type"))
            page["page_type"] = "content"
        if page.get("layout_hint") not in _VALID_LAYOUT_HINTS:
            logger.warning("Unknown layout_hint '{}' → 'mixed_grid'", page.get("layout_hint"))
            page["layout_hint"] = "mixed_grid"
        # LLM sometimes outputs content_points as a dict instead of a list
        cp = page.get("content_points")
        if isinstance(cp, dict):
            # Flatten dict values into a list of strings
            flat: list = []
            for k, v in cp.items():
                if isinstance(v, list):
                    flat.extend(f"{k}: {item}" if len(cp) > 1 else str(item) for item in v)
                else:
                    flat.append(f"{k}: {v}")
            page["content_points"] = flat

    return PlanningDraft.model_validate(data)
