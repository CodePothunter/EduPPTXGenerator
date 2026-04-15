"""Data models for the V2 SVG pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


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
    "timeline", "comparison", "exercise", "summary",
    "quiz", "formula", "experiment",
]

LayoutHint = Literal[
    "center_hero",
    "vertical_list",
    "bento_2col_equal",
    "bento_2col_asymmetric",
    "bento_3col",
    "hero_top_cards_bottom",
    "cards_top_hero_bottom",
    "mixed_grid",
    "full_image",
    "timeline",
    "comparison",
]


class ImageNeed(BaseModel):
    """A single image request within a page's material_needs."""

    query: str = Field(description="Search keyword or generation prompt")
    source: Literal["search", "ai_generate"] = "search"
    role: Literal["hero", "illustration", "icon", "background"] = "illustration"


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
    notes: str = Field(default="", description="Speaker notes")


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
    content_density: Literal["lecture", "review"] = Field(
        default="lecture", description="内容密度模式"
    )


class PlanningDraft(BaseModel):
    """Complete planning draft — Phase 1 output."""

    meta: PlanningMeta
    visual: VisualPlan = Field(default_factory=VisualPlan)
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
    image_paths: dict[str, Path] = field(default_factory=dict)  # role -> path
    icon_svgs: dict[str, str] = field(default_factory=dict)     # name -> svg string


@dataclass
class GeneratedSlide:
    """A generated SVG slide — Phase 3 output."""

    page_number: int
    svg_content: str
    svg_path: Path | None = None
