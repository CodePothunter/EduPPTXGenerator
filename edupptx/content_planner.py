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


# Semantic fallback mapping for common invalid icon names
_ICON_FALLBACKS = {
    "car": "compass", "truck": "compass", "vehicle": "compass",
    "archery": "target", "bow": "target", "arrow": "arrow-right",
    "scale": "ruler", "weight": "ruler", "balance": "ruler", "weight-scale": "ruler",
    "trampoline": "arrow-up", "jump": "arrow-up", "bounce": "arrow-up",
    "spring": "zap", "elastic": "zap", "stretch": "zap",
    "file-text": "file", "document": "file", "paper": "file",
    "check-circle": "check", "check-mark": "check",
    "arrow-left-right": "arrow-right", "arrows": "arrow-right",
    "hand-shake": "hand", "handshake": "hand",
    "plant": "sprout", "seedling": "sprout", "grow": "sprout",
    "bulb": "lightbulb", "lamp": "lightbulb", "idea": "lightbulb",
    "graph": "chart-line", "chart": "chart-line", "plot": "chart-line",
    "experiment": "flask-conical", "lab": "flask-conical", "test-tube": "flask-conical",
    "magnifier": "search", "magnifying-glass": "search", "zoom": "search",
    "clock-2": "clock", "timer": "clock", "stopwatch": "clock",
    "photo": "image", "picture": "image", "gallery": "image",
    "mail": "message-circle", "email": "message-circle", "letter": "message-circle",
    "gear": "settings", "config": "settings", "cog": "settings",
    "tool": "wrench", "tools": "wrench", "repair": "wrench",
    "world": "globe", "earth-2": "globe", "planet": "globe",
    "fire": "flame", "hot": "flame", "burn": "flame",
    "rain": "droplets", "water": "droplets", "liquid": "droplets",
    "tree": "tree-pine", "forest": "tree-pine", "wood": "tree-pine",
    "flower-2": "flower", "rose": "flower", "blossom": "flower",
    "music-2": "music", "note": "music", "song": "music",
}


def _validate_plan_icons(plan: PresentationPlan) -> None:
    """Replace invalid icon names with semantically similar alternatives."""
    valid = _get_valid_icons()
    for slide in plan.slides:
        for card in slide.cards:
            if card.icon not in valid:
                fallback = _ICON_FALLBACKS.get(card.icon, "circle")
                if fallback not in valid:
                    fallback = "circle"
                logger.warning("Invalid icon '{}' → '{}' in slide '{}'", card.icon, fallback, slide.title)
                card.icon = fallback


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
