"""Explicit `-s/--style` locks that theme's color palette (overriding keyword routing)."""

import re

import pytest

from edupptx.agent import _inject_dark_canvas, _is_dark_palette
from edupptx.design.template_router import (
    _STYLE_PALETTES,
    TemplateManifest,
    palette_for_style,
    resolve_palette_preset,
)
from edupptx.models import VisualPlan
from edupptx.planning.visual_planner import _apply_palette_hint

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_COLOR_FIELDS = (
    "primary_color",
    "secondary_color",
    "accent_color",
    "card_bg_color",
    "secondary_bg_color",
    "text_color",
    "heading_color",
)
# The four explicitly lockable themes (edu_emerald is the auto-route default).
_LOCKABLE = ["edu_academic", "edu_minimal", "edu_tech", "edu_warm"]


@pytest.mark.parametrize("name", _LOCKABLE)
def test_explicit_style_locks_palette(name):
    preset = palette_for_style(name)
    assert preset is not None
    assert preset.id == name


@pytest.mark.parametrize("name", [None, "", "edu_emerald", "not-a-style", "EDU_WARM"])
def test_auto_or_unknown_returns_none(name):
    # None / empty / default-sentinel / unknown / wrong-case → keep keyword routing.
    assert palette_for_style(name) is None


def test_catalog_has_five_themes_with_valid_hex():
    assert set(_STYLE_PALETTES) == {
        "edu_academic",
        "edu_emerald",
        "edu_minimal",
        "edu_tech",
        "edu_warm",
    }
    for name, preset in _STYLE_PALETTES.items():
        for field in _COLOR_FIELDS:
            value = getattr(preset, field)
            assert _HEX.match(value), f"{name}.{field}={value!r} is not #RRGGBB"


def test_apply_palette_hint_overwrites_visual_colors():
    warm = palette_for_style("edu_warm")
    vp = _apply_palette_hint(VisualPlan(), warm)
    assert vp.primary_color == "#F97316"
    assert vp.heading_color == "#9A3412"
    assert vp.card_bg_color == "#FFFFFF"


def test_resolve_palette_preset_resolves_locked_style_id():
    # A CLI-locked style id round-trips through resolve_palette_preset even when
    # the manifest knows nothing about it — the path render/re-resolve takes.
    manifest = TemplateManifest(style_name="复用")
    preset = resolve_palette_preset(manifest, preferred_palette_id="edu_warm")
    assert preset.id == "edu_warm"
    assert preset.primary_color == "#F97316"


def test_edu_tech_is_dark_theme():
    # The one dark theme keeps light text on a dark card (high contrast).
    tech = _STYLE_PALETTES["edu_tech"]
    assert tech.card_bg_color == "#1E293B"
    assert tech.text_color == "#E2E8F0"


class TestDarkCanvas:
    """A dark palette gets a flat dark page backing so on-canvas text stays legible."""

    def test_edu_tech_detected_as_dark(self):
        vp = _apply_palette_hint(VisualPlan(), palette_for_style("edu_tech"))
        assert _is_dark_palette(vp) is True

    def test_light_palettes_not_dark(self):
        assert _is_dark_palette(VisualPlan()) is False
        for name in ("edu_academic", "edu_warm", "edu_minimal"):
            vp = _apply_palette_hint(VisualPlan(), palette_for_style(name))
            assert _is_dark_palette(vp) is False, name

    def test_inject_prepends_fullpage_rect(self):
        vp = _apply_palette_hint(VisualPlan(), palette_for_style("edu_tech"))
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><text>x</text></svg>'
        out = _inject_dark_canvas(svg, vp)
        # The backing rect sits right after <svg>, before any content, in the page color.
        assert out.index('width="1280" height="720"') < out.index("<text")
        assert vp.secondary_bg_color in out  # #0F172A

    def test_inject_no_svg_tag_is_noop(self):
        assert _inject_dark_canvas("no svg here", VisualPlan()) == "no svg here"
