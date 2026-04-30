"""Tests for Layer 2: generate_design_md dual-path + 8-section validation + fallback."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from edupptx.models import PagePlan, PlanningDraft, PlanningMeta, StyleRouting
from edupptx.planning import visual_planner
from edupptx.planning.visual_planner import (
    _compose_design_md,
    _default_palette,
    _fallback_design_md,
    _palette_from_hint,
    _validate_8_sections,
    _validate_prose,
    generate_design_md,
)
from edupptx.style import parse_design_md
from edupptx.style.design_md import PROSE_HEADINGS


# ── Helpers ───────────────────────────────────────────────


def _make_draft(topic: str = "光合作用", style_name: str = "emerald") -> PlanningDraft:
    return PlanningDraft(
        meta=PlanningMeta(topic=topic, audience="高中生", purpose="教学", style_direction="清新"),
        style_routing=StyleRouting(style_name=style_name),
        pages=[
            PagePlan(page_number=1, page_type="cover", title="封面"),
            PagePlan(page_number=2, page_type="content", title="原理"),
            PagePlan(page_number=3, page_type="closing", title="总结"),
        ],
    )


def _make_palette_hint() -> SimpleNamespace:
    return SimpleNamespace(
        primary_color="#2563EB",
        secondary_color="#3B82F6",
        accent_color="#F59E0B",
        card_bg_color="#FFFFFF",
        secondary_bg_color="#EFF6FF",
        text_color="#1E293B",
        heading_color="#0F172A",
        background_color_bias="",
    )


_VALID_FULL_MD = """\
---
schema_version: "1.0"
name: 测试蓝
audience: 高中生
domain: 生物
colors:
  primary: "#1E40AF"
  accent: "#F59E0B"
  bg: "#EFF6FF"
  card_fill: "#FFFFFF"
  text: "#1E293B"
  text_secondary: "#475569"
  shadow: "#93C5FD"
  icon: "#2563EB"
typography:
  title:      { fontFamily: "Noto Sans SC", fontSize: 38pt, fontWeight: 700 }
  card-title: { fontFamily: "Noto Sans SC", fontSize: 16pt, fontWeight: 600 }
  body:       { fontFamily: "Noto Sans SC", fontSize: 12pt }
spacing:
  margin: comfortable
  card_gap: normal
rounded:
  sm: 4px
  md: 8px
  lg: 16px
---

## Overview
A brief tone description for the audience.

## Colors
Primary blue conveys trust; accent amber for highlights.

## Typography
Body 12pt, card-title 16pt, CJK Noto Sans SC.

## Layout
1280x720 Bento Grid; suits hero_top and bento_2col.

## Elevation
Subtle shadows only — avoid heavy darkening.

## Shapes
Rounded 8px corners on cards.

## Components
card-knowledge / card-formula / card-quote / card-stat.

## Do's and Don'ts
Do keep contrast WCAG AA. Don't overcrowd.
"""


_VALID_PROSE_ONLY = """\
## Overview
情绪基调克制、专业。

## Colors
主色用于标题；强调色仅作重点。

## Typography
body 12pt，card-title 16pt，CJK Noto Sans SC。

## Layout
1280×720 Bento Grid。

## Elevation
浅色阴影；避免大阴影。

## Shapes
卡片 8px 圆角。

## Components
card-knowledge / card-formula / card-quote / card-stat。

## Do's and Don'ts
保持克制；保证对比度。
"""


# ── Tests ─────────────────────────────────────────────────


def test_full_path_returns_llm_output(monkeypatch):
    draft = _make_draft()

    def fake_call_llm(config, prompts):
        return _VALID_FULL_MD

    monkeypatch.setattr(visual_planner, "_call_llm", fake_call_llm)
    out = generate_design_md(draft, config=None, palette_hint=None)
    assert out == _VALID_FULL_MD
    # Sanity: parses cleanly
    schema = parse_design_md(out)
    assert schema.global_tokens.palette["primary"] == "#1E40AF"


def test_full_path_invalid_falls_back(monkeypatch):
    draft = _make_draft()

    def fake_call_llm(config, prompts):
        return "garbage non-design-md output without any structure"

    monkeypatch.setattr(visual_planner, "_call_llm", fake_call_llm)
    out = generate_design_md(draft, config=None, palette_hint=None)
    schema = parse_design_md(out)
    # Fallback uses _default_palette()
    default = _default_palette()
    assert schema.global_tokens.palette["primary"] == default["primary"]
    assert schema.global_tokens.palette["accent"] == default["accent"]
    # All 8 prose sections present
    for h in PROSE_HEADINGS:
        assert f"## {h}" in out


def test_full_path_none_falls_back(monkeypatch):
    draft = _make_draft()

    def fake_call_llm(config, prompts):
        return None

    monkeypatch.setattr(visual_planner, "_call_llm", fake_call_llm)
    out = generate_design_md(draft, config=None, palette_hint=None)
    schema = parse_design_md(out)
    default = _default_palette()
    assert schema.global_tokens.palette["primary"] == default["primary"]
    assert schema.global_tokens.palette["accent"] == default["accent"]


def test_hint_path_uses_hint_palette(monkeypatch):
    draft = _make_draft()
    hint = _make_palette_hint()

    def fake_call_llm(config, prompts):
        return _VALID_PROSE_ONLY

    monkeypatch.setattr(visual_planner, "_call_llm", fake_call_llm)
    out = generate_design_md(draft, config=None, palette_hint=hint, template_label="blue")
    schema = parse_design_md(out)
    expected = _palette_from_hint(hint)
    # Palette must come from the hint, NOT from any LLM output
    assert schema.global_tokens.palette["primary"] == expected["primary"]
    assert schema.global_tokens.palette["accent"] == expected["accent"]
    assert schema.global_tokens.palette["bg"] == expected["bg"]
    assert schema.global_tokens.palette["card_fill"] == expected["card_fill"]
    # Prose body preserved
    assert "情绪基调" in out


def test_hint_path_invalid_prose_falls_back_to_hint_palette(monkeypatch):
    draft = _make_draft()
    hint = _make_palette_hint()

    # Only 3 H2 sections — fails _validate_prose
    bad_prose = "## Overview\nx\n\n## Colors\ny\n\n## Typography\nz\n"

    def fake_call_llm(config, prompts):
        return bad_prose

    monkeypatch.setattr(visual_planner, "_call_llm", fake_call_llm)
    out = generate_design_md(draft, config=None, palette_hint=hint)
    schema = parse_design_md(out)
    expected = _palette_from_hint(hint)
    default = _default_palette()
    # Hint path fallback uses _palette_from_hint, NOT _default_palette
    assert schema.global_tokens.palette["primary"] == expected["primary"]
    assert schema.global_tokens.palette["primary"] != default["primary"]
    assert schema.global_tokens.palette["accent"] == expected["accent"]


def test_validate_8_sections_counts_correctly():
    # 7 H2 → False
    md_7 = _VALID_FULL_MD.replace("## Do's and Don'ts\nDo keep contrast WCAG AA. Don't overcrowd.\n", "")
    assert _validate_8_sections(md_7) is False

    # 8 H2 with full palette → True
    assert _validate_8_sections(_VALID_FULL_MD) is True

    # 8 H2 but palette only has 2 keys → False (need ≥5 of {primary, accent, bg, card_fill, text})
    md_thin_palette = _VALID_FULL_MD.replace(
        """colors:
  primary: "#1E40AF"
  accent: "#F59E0B"
  bg: "#EFF6FF"
  card_fill: "#FFFFFF"
  text: "#1E293B"
  text_secondary: "#475569"
  shadow: "#93C5FD"
  icon: "#2563EB" """.rstrip(),
        """colors:
  primary: "#1E40AF"
  accent: "#F59E0B" """.rstrip(),
    )
    assert _validate_8_sections(md_thin_palette) is False


def test_validate_skips_h2_in_fenced_code():
    """8 real H2 + fake H2 inside fenced block → mistune AST ignores fenced content."""
    md_with_fenced = """\
---
schema_version: "1.0"
name: fence-test
colors:
  primary: "#111111"
  accent: "#222222"
  bg: "#333333"
  card_fill: "#FFFFFF"
  text: "#000000"
---

## Overview
real overview

```yaml
## fakeSection
this is not a heading
```

## Colors
real colors

## Typography
real typography

## Layout
real layout

## Elevation
real elevation

## Shapes
real shapes

## Components
real components

## Do's and Don'ts
real rules
"""
    assert _validate_8_sections(md_with_fenced) is True


def test_validate_prose_counts_h2():
    assert _validate_prose(_VALID_PROSE_ONLY) is True
    assert _validate_prose("## A\nx\n\n## B\ny\n") is False
    assert _validate_prose("") is False
    assert _validate_prose(None) is False  # type: ignore[arg-type]


def test_compose_design_md_uses_hint_palette():
    draft = _make_draft()
    hint = _make_palette_hint()
    out = _compose_design_md(hint, _VALID_PROSE_ONLY, draft)
    schema = parse_design_md(out)
    expected = _palette_from_hint(hint)
    assert schema.global_tokens.palette["primary"] == expected["primary"]
    assert "情绪基调" in out


def test_fallback_roundtrips_through_parser():
    draft = _make_draft()
    out = _fallback_design_md(_default_palette(), draft)
    schema = parse_design_md(out)
    # All 8 sections present
    for h in PROSE_HEADINGS:
        assert f"## {h}" in out
    # Default palette intact
    assert schema.global_tokens.palette["primary"] == _default_palette()["primary"]
    # Prose includes the topic
    assert draft.meta.topic in out
