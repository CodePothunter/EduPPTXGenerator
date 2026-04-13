"""Tests for style resolution — palette refs and named intents."""

import pytest
from pathlib import Path

from edupptx.style_resolver import resolve_style, _resolve_ref
from edupptx.style_schema import load_style, MARGIN_PRESETS, CARD_SPACING_PRESETS, ICON_SIZE_PRESETS


STYLES_DIR = Path(__file__).parent.parent / "styles"


def test_resolve_palette_ref():
    palette = {"accent": "#059669", "bg": "#F0FDF4"}
    assert _resolve_ref("palette.accent", palette) == "#059669"
    assert _resolve_ref("palette.bg", palette) == "#F0FDF4"


def test_resolve_non_ref_passthrough():
    assert _resolve_ref("#FF0000", {}) == "#FF0000"
    assert _resolve_ref("red", {}) == "red"


def test_resolve_unknown_ref_returns_raw():
    result = _resolve_ref("palette.nonexistent", {"accent": "#000"})
    assert result == "palette.nonexistent"


def test_resolve_emerald_colors():
    schema = load_style(STYLES_DIR / "emerald.json")
    resolved = resolve_style(schema)
    assert resolved.accent_color == "#059669"
    assert resolved.heading_color == "#1F2937"
    assert resolved.icon_color == "#10B981"


def test_resolve_blue_colors():
    schema = load_style(STYLES_DIR / "blue.json")
    resolved = resolve_style(schema)
    assert resolved.accent_color == "#2563EB"
    assert resolved.icon_color == "#3B82F6"


def test_resolve_margin_matches_existing_constants():
    """comfortable margins MUST equal existing layout_engine constants."""
    schema = load_style(STYLES_DIR / "emerald.json")
    resolved = resolve_style(schema)
    assert resolved.margin_left == 1_016_000   # MARGIN_X
    assert resolved.margin_top == 635_000      # MARGIN_Y
    assert resolved.content_w == 10_160_000    # CONTENT_W


def test_resolve_card_gap_matches_existing():
    """normal card spacing MUST equal existing CARD_GAP."""
    schema = load_style(STYLES_DIR / "emerald.json")
    resolved = resolve_style(schema)
    assert resolved.card_gap == 304_800  # CARD_GAP


def test_resolve_icon_size_matches_existing():
    """large icon MUST equal existing ICON_SIZE."""
    schema = load_style(STYLES_DIR / "emerald.json")
    resolved = resolve_style(schema)
    assert resolved.icon_size == 609_600  # ICON_SIZE


def test_resolve_shadow_emu():
    schema = load_style(STYLES_DIR / "emerald.json")
    resolved = resolve_style(schema)
    assert resolved.card_shadow_blur_emu == 30 * 12_700  # 381000
    assert resolved.card_shadow_dist_emu == 8 * 12_700   # 101600


def test_different_styles_produce_different_colors():
    emerald = resolve_style(load_style(STYLES_DIR / "emerald.json"))
    blue = resolve_style(load_style(STYLES_DIR / "blue.json"))
    assert emerald.accent_color != blue.accent_color
    assert emerald.icon_color != blue.icon_color
