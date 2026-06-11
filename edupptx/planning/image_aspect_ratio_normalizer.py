"""Normalize planned image aspect ratios before material and SVG generation."""

from __future__ import annotations

from dataclasses import dataclass

from edupptx.models import (
    PlanningDraft,
    iter_image_slot_keys,
    normalize_image_aspect_ratio,
)


@dataclass(frozen=True)
class ImageAspectRatioChange:
    page_number: int
    slot_key: str
    role: str
    original_ratio: str
    normalized_ratio: str


def normalize_draft_image_aspect_ratios(draft: PlanningDraft) -> list[ImageAspectRatioChange]:
    """Normalize all page image ratios in place and return changed entries."""
    changes: list[ImageAspectRatioChange] = []
    for page in draft.pages:
        image_needs = page.material_needs.images or []
        for slot_key, need in iter_image_slot_keys(image_needs):
            original = str(need.aspect_ratio or "").strip()
            normalized = normalize_image_aspect_ratio(original)
            if normalized == original:
                continue
            need.aspect_ratio = normalized
            changes.append(
                ImageAspectRatioChange(
                    page_number=page.page_number,
                    slot_key=slot_key,
                    role=need.role,
                    original_ratio=original,
                    normalized_ratio=normalized,
                )
            )
    return changes
