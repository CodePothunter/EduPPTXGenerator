"""LLM-driven content planning: topic → structured PresentationPlan."""

from __future__ import annotations

from loguru import logger

from edupptx.icons import list_icons
from edupptx.llm_client import LLMClient
from edupptx.models import PresentationPlan
from edupptx.prompts.content import SYSTEM_PROMPT, build_user_message

_VALID_ICONS = None


def _get_valid_icons() -> set[str]:
    global _VALID_ICONS
    if _VALID_ICONS is None:
        _VALID_ICONS = set(list_icons())
    return _VALID_ICONS


def _validate_plan_icons(plan: PresentationPlan) -> None:
    """Replace invalid icon names with 'circle'."""
    valid = _get_valid_icons()
    for slide in plan.slides:
        for card in slide.cards:
            if card.icon not in valid:
                logger.warning("Invalid icon '{}' in slide '{}', replacing with 'circle'", card.icon, slide.title)
                card.icon = "circle"


class ContentPlanner:
    def __init__(self, llm: LLMClient):
        self._llm = llm

    def plan(
        self,
        topic: str,
        requirements: str = "",
        palette: str | None = None,
    ) -> PresentationPlan:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(topic, requirements)},
        ]

        logger.info("Generating content plan for topic={}", topic)
        data = self._llm.chat_json(messages, temperature=0.7)

        # Override palette if user specified
        if palette:
            data["palette"] = palette

        plan = PresentationPlan.model_validate(data)
        _validate_plan_icons(plan)
        logger.info("Plan generated: {} slides, palette={}", len(plan.slides), plan.palette)
        return plan
