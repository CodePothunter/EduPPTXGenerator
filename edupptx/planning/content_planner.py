"""Phase 1: 策划稿生成 — 1 次 LLM 调用输出 PlanningDraft。"""

from __future__ import annotations

import json
import re

from loguru import logger

from edupptx.config import Config
from edupptx.llm_client import LLMClient
from edupptx.models import InputContext, PlanningDraft
from edupptx.planning.prompts import (
    build_planning_system_prompt,
    build_planning_user_prompt,
)


def generate_planning_draft(ctx: InputContext, config: Config) -> PlanningDraft:
    """Generate a planning draft from input context via a single LLM call."""
    client = LLMClient(config)

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

    return PlanningDraft.model_validate(data)
