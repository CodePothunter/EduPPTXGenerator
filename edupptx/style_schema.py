"""Style schema: declarative JSON-driven design tokens for the v2 pipeline.

Three-tier token hierarchy:
  global  -> palette, fonts, background config
  semantic -> sizes, colors (as palette refs), shadows
  layout  -> named intents (comfortable/tight/spacious)
  decorations -> boolean toggle flags
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field

# ── Named intent lookup tables ────────────────────────────
# "comfortable" values match existing layout_engine.py constants exactly.

MARGIN_PRESETS: dict[str, dict[str, int]] = {
    "tight":       {"left": 635_000,   "top": 508_000,   "content_w": 10_922_000},
    "comfortable": {"left": 1_016_000, "top": 635_000,   "content_w": 10_160_000},
    "spacious":    {"left": 1_524_000, "top": 762_000,   "content_w": 9_144_000},
}

CARD_SPACING_PRESETS: dict[str, int] = {
    "tight":  152_400,   # 12pt
    "normal": 304_800,   # 24pt  (matches CARD_GAP)
    "wide":   457_200,   # 36pt
}

ICON_SIZE_PRESETS: dict[str, int] = {
    "small":  304_800,   # 24pt
    "medium": 457_200,   # 36pt
    "large":  609_600,   # 48pt  (matches ICON_SIZE)
}

CONTENT_DENSITY_PRESETS: dict[str, dict[str, int]] = {
    "compact":  {"card_pad": 152_400, "icon_margin": 76_200,  "title_h": 254_000},
    "standard": {"card_pad": 228_600, "icon_margin": 152_400, "title_h": 381_000},
    "relaxed":  {"card_pad": 304_800, "icon_margin": 228_600, "title_h": 508_000},
}

# Vertical positions (EMU) derived from margin presets
TITLE_Y = 635_000       # 50pt
TITLE_H = 762_000       # 60pt
SUBTITLE_Y = 1_397_000  # 110pt
SUBTITLE_H = 444_500    # 35pt
CARD_TOP = 2_159_000    # 170pt
CARD_H = 2_540_000      # 200pt
FOOTER_Y = 5_334_000    # 420pt
FOOTER_H = 889_000      # 70pt

SLIDE_W = 12_192_000    # 960pt
SLIDE_H = 6_858_000     # 540pt

PT = 12_700


# ── Pydantic schema models ────────────────────────────────


class FontSpec(BaseModel):
    family: str = "Noto Sans SC"
    fallback: str = "Arial"


class ShadowSpec(BaseModel):
    blur_pt: int = 30
    dist_pt: int = 8
    color: str = "palette.shadow"
    alpha_pct: int = 14


class SchemaMeta(BaseModel):
    schema_version: str = "0.1"
    name: str = ""
    description: str = ""


class GlobalTokens(BaseModel):
    palette: dict[str, str] = Field(default_factory=dict)
    fonts: dict[str, FontSpec] = Field(default_factory=lambda: {
        "heading": FontSpec(), "body": FontSpec(),
    })
    background: dict[str, str] = Field(default_factory=lambda: {
        "type": "diagonal_gradient", "seed_extra": "",
    })


class SemanticTokens(BaseModel):
    title_size_pt: int = 38
    subtitle_size_pt: int = 20
    body_size_pt: int = 12
    card_title_size_pt: int = 16
    footer_size_pt: int = 13
    formula_size_pt: int = 18

    heading_color: str = "palette.text"
    body_color: str = "palette.text_secondary"
    accent_color: str = "palette.accent"
    card_fill_color: str = "palette.card_fill"
    card_title_color: str = "palette.accent"
    icon_color: str = "palette.icon"

    bg_overlay_color: str = "palette.bg"
    bg_overlay_alpha: float = 0.55

    card_corner_radius: int = 8000
    card_shadow: ShadowSpec = Field(default_factory=ShadowSpec)


class LayoutTokens(BaseModel):
    margin: Literal["tight", "comfortable", "spacious"] = "comfortable"
    card_spacing: Literal["tight", "normal", "wide"] = "normal"
    icon_size: Literal["small", "medium", "large"] = "large"
    title_position: Literal["top_left", "center"] = "top_left"
    content_density: Literal["compact", "standard", "relaxed"] = "standard"


class DecorationTokens(BaseModel):
    title_underline: bool = True
    content_panel: bool = True
    panel_alpha_pct: int = 35
    footer_separator: bool = True
    quote_bar: bool = True
    section_diamond: bool = True
    closing_circle: bool = True


class StyleSchema(BaseModel):
    """Top-level style schema — loaded from JSON, drives the entire visual output."""

    meta: SchemaMeta = Field(default_factory=SchemaMeta)
    global_tokens: GlobalTokens = Field(default_factory=GlobalTokens, alias="global")
    semantic: SemanticTokens = Field(default_factory=SemanticTokens)
    layout: LayoutTokens = Field(default_factory=LayoutTokens)
    decorations: DecorationTokens = Field(default_factory=DecorationTokens)
    slide_overrides: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


def load_style(path: Path) -> StyleSchema:
    """Load a StyleSchema from a JSON or DESIGN.md file (dispatched by suffix)."""
    if path.suffix == ".md":
        # Lazy import to avoid circular import (design_md imports style_schema).
        from edupptx.style.design_md import parse_design_md
        return parse_design_md(path.read_text(encoding="utf-8"))
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return StyleSchema.model_validate(data)


# ── Resolved style (all concrete values) ──────────────────


@dataclass
class ResolvedStyle:
    """Fully resolved style — no palette refs, no named intents. All EMU/hex."""

    # Colors (resolved hex)
    heading_color: str
    body_color: str
    accent_color: str
    card_fill_color: str
    card_title_color: str
    icon_color: str
    bg_overlay_color: str
    bg_overlay_alpha: float
    shadow_color: str

    # Fonts
    heading_font: FontSpec
    body_font: FontSpec

    # Sizes (pt)
    title_size_pt: int
    subtitle_size_pt: int
    body_size_pt: int
    card_title_size_pt: int
    footer_size_pt: int
    formula_size_pt: int

    # Card styling
    card_corner_radius: int
    card_shadow_blur_emu: int
    card_shadow_dist_emu: int
    card_shadow_color: str
    card_shadow_alpha_pct: int

    # Layout (resolved EMU)
    margin_left: int
    margin_top: int
    content_w: int
    card_gap: int
    icon_size: int
    card_pad: int
    icon_margin: int
    card_title_h: int

    # Title position
    title_position: str

    # Decorations
    decorations: DecorationTokens

    # Background
    bg_type: str
    bg_seed_extra: str

    # Raw palette for background generation
    palette: dict[str, str]
