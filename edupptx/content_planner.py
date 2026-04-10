"""LLM-driven content planning: topic → structured PresentationPlan."""

from __future__ import annotations

from loguru import logger

from edupptx.llm_client import LLMClient
from edupptx.models import PresentationPlan
from edupptx.prompts.content import SYSTEM_PROMPT, build_user_message


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

        logger.info("Generating content plan for topic=%s", topic)
        data = self._llm.chat_json(messages, temperature=0.7)

        # Override palette if user specified
        if palette:
            data["palette"] = palette

        plan = PresentationPlan.model_validate(data)
        logger.info(
            "Plan generated: %d slides, palette=%s",
            len(plan.slides),
            plan.palette,
        )
        return plan
