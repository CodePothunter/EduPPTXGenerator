"""Data models for the V2 SVG pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


# ── Phase 0: Input ──────────────────────────────────────


@dataclass
class InputContext:
    """Unified input from topic, document, or web research."""

    topic: str
    source_text: str | None = None
    research_summary: str | None = None
    requirements: str = ""


# ── Phase 1: Planning Draft ─────────────────────────────

PageType = Literal[
    "cover", "toc", "section", "content", "data", "case", "closing",
    "timeline", "exercise", "summary", "quiz", "formula", "experiment",
]

LayoutHint = Literal[
    "center_hero",
    "vertical_list",
    "bento_2col_equal",
    "bento_2col_asymmetric",
    "bento_3col",
    "hero_top_cards_bottom",
    "cards_top_hero_bottom",
    "hero_with_microcards",
    "mixed_grid",
    "full_image",
    "timeline",
    "comparison",
    "relation",
]

RevealMode = Literal[
    "highlight_correct_option",
    "show_answer",
]


# Predefined aspect ratios and their Seedream 2K generation sizes
IMAGE_RATIO_SIZES: dict[str, str] = {
    "1:1": "2048x2048",
    "3:4": "1728x2304",
    "4:3": "2304x1728",
    "16:9": "2848x1600",
    "9:16": "1600x2848",
    "3:2": "2496x1664",
    "2:3": "1664x2496",
    "21:9": "3136x1344",
}

# Numeric values for ratio matching
_RATIO_VALUES: dict[str, float] = {
    "1:1": 1.0,
    "3:4": 0.75,
    "4:3": 1.333,
    "16:9": 1.778,
    "9:16": 0.5625,
    "3:2": 1.5,
    "2:3": 0.667,
    "21:9": 2.333,
}


def match_aspect_ratio(width: float, height: float) -> str:
    """Find the closest predefined aspect ratio for given dimensions.

    Returns ratio string like "16:9", "4:3", etc.
    """
    if height <= 0:
        return "16:9"
    target = width / height
    best_ratio = "16:9"
    best_diff = float("inf")
    for name, value in _RATIO_VALUES.items():
        diff = abs(target - value)
        if diff < best_diff:
            best_diff = diff
            best_ratio = name
    return best_ratio


class ImageNeed(BaseModel):
    """A single image request within a page's material_needs."""

    query: str = Field(description="Search keyword or generation prompt")
    source: Literal["search", "ai_generate"] = "search"
    role: Literal["hero", "illustration", "icon", "background"] = "illustration"
    aspect_ratio: str = Field(
        default="16:9",
        description="Aspect ratio from predefined set: 1:1, 3:4, 4:3, 16:9, 9:16, 3:2, 2:3, 21:9",
    )


def build_image_slot_key(role: str, occurrence: int) -> str:
    """Return a stable per-slide image slot key like ``illustration_2``."""
    return f"{role}_{occurrence}"


def iter_image_slot_keys(needs: list[ImageNeed]) -> list[tuple[str, ImageNeed]]:
    """Assign stable slot keys to image needs while preserving list order."""
    counts: dict[str, int] = {}
    slots: list[tuple[str, ImageNeed]] = []
    for need in needs:
        occurrence = counts.get(need.role, 0) + 1
        counts[need.role] = occurrence
        slots.append((build_image_slot_key(need.role, occurrence), need))
    return slots


class MaterialNeeds(BaseModel):
    """Material requirements for a single page."""

    background: str | None = Field(default=None, description="Background style name")
    images: list[ImageNeed] = Field(default_factory=list)
    icons: list[str] = Field(default_factory=list, description="Lucide icon names")
    chart: dict | None = Field(default=None, description="Chart spec if needed")


class PagePlan(BaseModel):
    """Planning draft for a single page."""

    page_number: int
    page_type: PageType
    title: str
    subtitle: str | None = None
    content_points: list = Field(default_factory=list)
    layout_hint: LayoutHint = "mixed_grid"
    material_needs: MaterialNeeds = Field(default_factory=MaterialNeeds)
    design_notes: str = ""
    template_variant: str | None = Field(
        default=None,
        description="Preferred SVG template stem selected during style routing.",
    )
    reveal_from_page: int | None = Field(
        default=None,
        description="If set, this page should reuse the referenced source page layout and only reveal the answer layer.",
    )
    reveal_mode: RevealMode | None = Field(
        default=None,
        description="Pseudo-animation answer reveal mode for quiz/exercise pages.",
    )
    notes: str = Field(default="", description="Speaker notes")

    @model_validator(mode="before")
    @classmethod
    def _normalize_layout_only_page_types(cls, data):
        if not isinstance(data, dict):
            return data
        page_type = str(data.get("page_type") or "").strip()
        if page_type not in {"comparison", "relation"}:
            return data

        normalized = dict(data)
        layout_hint = str(normalized.get("layout_hint") or "").strip()
        if not layout_hint or layout_hint == "mixed_grid":
            normalized["layout_hint"] = page_type
        normalized["page_type"] = "content"
        return normalized


class PlanningMeta(BaseModel):
    """Metadata for the planning draft."""

    topic: str
    audience: str = ""
    purpose: str = ""
    style_direction: str = ""
    total_pages: int = 0


class VisualPlan(BaseModel):
    """Phase 1b output: LLM-recommended visual design for the entire deck."""

    primary_color: str = Field(default="#1E40AF", description="主色 hex")
    secondary_color: str = Field(default="#3B82F6", description="辅色 hex")
    accent_color: str = Field(default="#F59E0B", description="强调色 hex")
    background_prompt: str = Field(default="", description="Seedream 背景生成 prompt")
    card_bg_color: str = Field(default="#FFFFFF", description="卡片背景色")
    secondary_bg_color: str = Field(default="#F8FAFC", description="次背景色")
    text_color: str = Field(default="#1E293B", description="正文色")
    heading_color: str = Field(default="#0F172A", description="标题色")
    background_color_bias: str = Field(
        default="",
        description="Optional palette-specific color-bias sentence appended after the background prompt.",
    )
    content_density: Literal["lecture", "review"] = Field(
        default="lecture", description="内容密度模式"
    )


class StyleRouting(BaseModel):
    """Deck-level style routing resolved before SVG generation."""

    style_name: str = Field(default="shared_reusable_core", description="Resolved style manifest id")
    template_family: str = Field(default="复用", description="Resolved page template family")
    palette_id: str = Field(default="default", description="Resolved palette preset id")
    resolved_by: Literal["keyword", "llm", "fallback"] = Field(
        default="fallback",
        description="How the style manifest was selected.",
    )


class PlanningDraft(BaseModel):
    """Complete planning draft — Phase 1 output."""

    meta: PlanningMeta
    visual: VisualPlan = Field(default_factory=VisualPlan)
    style_routing: StyleRouting = Field(default_factory=StyleRouting)
    research_context: str = ""
    pages: list[PagePlan]


# ── Phase 2: Materials ──────────────────────────────────


@dataclass
class ImageResult:
    """A fetched or generated image."""

    url: str
    width: int = 0
    height: int = 0
    source: str = ""  # "pixabay" / "unsplash" / "seedream"
    local_path: Path | None = None


class MaterialEntry(BaseModel):
    """A material asset in the library."""

    id: str
    type: Literal["background", "illustration", "image"]
    tags: list[str] = Field(default_factory=list)
    palette: str = ""
    source: Literal["programmatic", "ai_generated", "search", "user_uploaded"] = "programmatic"
    description: str = ""
    resolution: tuple[int, int] = (1920, 1080)
    path: str = Field(description="Relative path within library directory")
    created_at: str = ""


# ── Phase 3-5: SVG + Output ─────────────────────────────


@dataclass
class SlideAssets:
    """Collected assets for a single slide."""

    page_number: int
    background_path: Path | None = None
    image_paths: dict[str, Path] = field(default_factory=dict)  # slot_key -> path
    icon_svgs: dict[str, str] = field(default_factory=dict)     # name -> svg string


@dataclass
class GeneratedSlide:
    """A generated SVG slide — Phase 3 output."""

    page_number: int
    svg_content: str
    svg_path: Path | None = None
