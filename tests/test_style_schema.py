"""Tests for style schema loading and validation."""

import json
import pytest
from pathlib import Path

from edupptx.style_schema import StyleSchema, load_style, MARGIN_PRESETS, CARD_SPACING_PRESETS


STYLES_DIR = Path(__file__).parent.parent / "styles"


def test_load_emerald_schema():
    schema = load_style(STYLES_DIR / "emerald.json")
    assert schema.meta.name == "emerald"
    assert schema.global_tokens.palette["accent"] == "#059669"


def test_load_blue_schema():
    schema = load_style(STYLES_DIR / "blue.json")
    assert schema.meta.name == "blue"
    assert schema.global_tokens.palette["accent"] == "#2563EB"


def test_palette_refs_are_strings():
    schema = load_style(STYLES_DIR / "emerald.json")
    assert schema.semantic.heading_color.startswith("palette.")
    assert schema.semantic.accent_color == "palette.accent"


def test_layout_intents_valid():
    schema = load_style(STYLES_DIR / "emerald.json")
    assert schema.layout.margin in MARGIN_PRESETS
    assert schema.layout.card_spacing in CARD_SPACING_PRESETS


def test_decoration_flags():
    schema = load_style(STYLES_DIR / "emerald.json")
    assert schema.decorations.title_underline is True
    assert schema.decorations.content_panel is True
    assert isinstance(schema.decorations.panel_alpha_pct, int)


def test_schema_defaults():
    """A minimal schema should have sensible defaults."""
    schema = StyleSchema.model_validate({"global": {"palette": {"accent": "#FF0000"}}})
    assert schema.semantic.title_size_pt == 38
    assert schema.layout.margin == "comfortable"
    assert schema.decorations.title_underline is True


def test_schema_roundtrip(tmp_path):
    """Load, modify, save, reload."""
    schema = load_style(STYLES_DIR / "emerald.json")
    out = tmp_path / "test.json"
    with open(out, "w") as f:
        json.dump(schema.model_dump(by_alias=True), f)
    reloaded = load_style(out)
    assert reloaded.meta.name == schema.meta.name
    assert reloaded.global_tokens.palette == schema.global_tokens.palette
