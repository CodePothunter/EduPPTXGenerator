"""Tests for DESIGN.md ⇄ StyleSchema parser/serializer (Layer 3a)."""

from __future__ import annotations

import pytest

from edupptx.style import parse_design_md, serialize_style
from edupptx.style.design_md import (
    PROSE_HEADINGS,
    _parse_h2_sections,
    _resolve_color_tokens,
    build_phase3_constraints,
)


# ── Fixtures ──────────────────────────────────────────────


MINIMAL_DESIGN_MD = """\
---
schema_version: "1.0"
name: 测试主题
colors:
  primary: "#1E293B"
  accent: "#2563EB"
---

## Overview
A short tone description.
"""


FULL_DESIGN_MD = """\
---
schema_version: "1.0"
name: 科技蓝
audience: 中学生
domain: 信息技术
colors:
  primary: "#1E293B"
  accent: "#2563EB"
  bg: "#EFF6FF"
  card_fill: "#FFFFFF"
  text: "#1E293B"
  text_secondary: "#475569"
  shadow: "#93C5FD"
  icon: "#3B82F6"
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
pptx-extensions:
  decorations:
    title_underline: true
    content_panel: true
    panel_alpha_pct: 35
    footer_separator: true
    quote_bar: true
    section_diamond: true
    closing_circle: true
  card_shadow:
    blur_pt: 30
    dist_pt: 8
    color: "palette.shadow"
    alpha_pct: 14
  background:
    type: diagonal_gradient
    seed_extra: ""
  visual_plan:
    background_color_bias: ""
    content_density: lecture
---

## Overview
A clean, professional theme tuned for technology lectures.

## Colors
Accent blue conveys trust and focus.

## Typography
Body 12pt, card-title 16pt, CJK Noto Sans SC.

## Layout
1280x720 viewBox, Bento Grid friendly.

## Elevation
Subtle shadows; avoid heavy darkening.

## Shapes
Rounded 8px corners on cards.

## Components
card-knowledge / card-formula / card-quote / card-stat.

## Do's and Don'ts
Do keep contrast WCAG AA. Don't overcrowd. Don't use neon.
"""


# ── Tests ─────────────────────────────────────────────────


def test_parse_minimal():
    schema = parse_design_md(MINIMAL_DESIGN_MD)
    assert schema.meta.name == "测试主题"
    assert schema.meta.schema_version == "1.0"
    assert schema.global_tokens.palette["accent"] == "#2563EB"
    # Defaults when spacing not provided
    assert schema.layout.margin == "comfortable"
    assert schema.layout.card_spacing == "normal"
    assert "## Overview" in schema.meta.description


def test_parse_full_with_extensions():
    schema = parse_design_md(FULL_DESIGN_MD)
    assert schema.meta.name == "科技蓝"
    assert schema.global_tokens.palette["shadow"] == "#93C5FD"

    # Typography → semantic sizes
    assert schema.semantic.title_size_pt == 38
    assert schema.semantic.card_title_size_pt == 16
    assert schema.semantic.body_size_pt == 12

    # *_color refs stay at defaults
    assert schema.semantic.heading_color == "palette.text"
    assert schema.semantic.accent_color == "palette.accent"

    # pptx-extensions: decorations
    assert schema.decorations.title_underline is True
    assert schema.decorations.panel_alpha_pct == 35

    # pptx-extensions: card_shadow
    assert schema.semantic.card_shadow.blur_pt == 30
    assert schema.semantic.card_shadow.dist_pt == 8
    assert schema.semantic.card_shadow.color == "palette.shadow"
    assert schema.semantic.card_shadow.alpha_pct == 14

    # pptx-extensions: background
    assert schema.global_tokens.background["type"] == "diagonal_gradient"
    assert schema.global_tokens.background["seed_extra"] == ""

    # All 8 H2 sections survive
    for heading in PROSE_HEADINGS:
        assert f"## {heading}" in schema.meta.description


def test_idempotent_roundtrip():
    schema1 = parse_design_md(FULL_DESIGN_MD)
    md_text = serialize_style(schema1)
    schema2 = parse_design_md(md_text)

    assert schema1.global_tokens.palette == schema2.global_tokens.palette
    assert schema1.layout.margin == schema2.layout.margin
    assert schema1.layout.card_spacing == schema2.layout.card_spacing
    assert schema1.decorations == schema2.decorations
    assert schema1.semantic.card_shadow == schema2.semantic.card_shadow
    assert schema1.global_tokens.background == schema2.global_tokens.background
    assert schema1.meta.name == schema2.meta.name


def test_partial_design_md_uses_defaults():
    partial = """\
---
name: minimal
colors:
  accent: "#FF0000"
---

## Overview
Stub.
"""
    schema = parse_design_md(partial)
    assert schema.layout.margin == "comfortable"
    assert schema.layout.card_spacing == "normal"
    # No typography → semantic sizes hold their defaults
    assert schema.semantic.title_size_pt == 38
    assert schema.semantic.body_size_pt == 12
    assert schema.semantic.card_title_size_pt == 16
    # No pptx-extensions → DecorationTokens defaults
    assert schema.decorations.title_underline is True
    assert schema.decorations.panel_alpha_pct == 35


def test_chinese_yaml():
    schema = parse_design_md(FULL_DESIGN_MD)
    assert schema.meta.name == "科技蓝"
    out = serialize_style(schema)
    # No \uXXXX escapes — allow_unicode=True must be respected.
    assert "\\u" not in out
    assert "科技蓝" in out


def test_h2_sections_skip_fenced_code():
    """v2.1 A4 critical: fenced code blocks containing '## ' must NOT be detected as headings.

    We check the *split* directly via _parse_h2_sections — the dict keys are the
    detected H2 section headings. The fenced ``## NotASection`` line must NOT
    become its own key.
    """
    body = """\
## RealSection
Content here.

```yaml
## NotASection
- still inside fence
```

## SecondReal
After fence.
"""
    sections = _parse_h2_sections(body)
    assert set(sections.keys()) == {"RealSection", "SecondReal"}
    assert "NotASection" not in sections
    # The fenced content should have been preserved inside RealSection's body
    assert "NotASection" in sections["RealSection"]


@pytest.mark.parametrize(
    "bad_margin,expected",
    [
        ("weird-value", "comfortable"),
        ("", "comfortable"),
        ("XL", "comfortable"),
    ],
)
def test_invalid_margin_falls_back(bad_margin: str, expected: str):
    src = f"""\
---
name: bad-margin
spacing:
  margin: {bad_margin!r}
---

## Overview
x
"""
    schema = parse_design_md(src)
    assert schema.layout.margin == expected


@pytest.mark.parametrize(
    "bad_card_gap,expected",
    [
        ("xxx", "normal"),
        ("zero", "normal"),
    ],
)
def test_invalid_card_gap_falls_back(bad_card_gap: str, expected: str):
    src = f"""\
---
name: bad-gap
spacing:
  margin: comfortable
  card_gap: {bad_card_gap!r}
---

## Overview
x
"""
    schema = parse_design_md(src)
    assert schema.layout.card_spacing == expected


def test_serialize_produces_8_section_placeholders_by_default():
    schema = parse_design_md(MINIMAL_DESIGN_MD)
    out = serialize_style(schema)  # no prose_sections passed
    for heading in PROSE_HEADINGS:
        assert f"## {heading}" in out


def test_semantic_extensions_roundtrip():
    """C1 regression: non-default semantic values must roundtrip via pptx-extensions.semantic."""
    from edupptx.style_schema import (
        DecorationTokens,
        GlobalTokens,
        LayoutTokens,
        SchemaMeta,
        SemanticTokens,
        ShadowSpec,
        StyleSchema,
    )

    schema = StyleSchema(
        meta=SchemaMeta(schema_version="1.0", name="custom-semantic"),
        global_tokens=GlobalTokens(palette={"primary": "#000000", "accent": "#FF0000"}),
        semantic=SemanticTokens(
            title_size_pt=42,
            subtitle_size_pt=24,        # non-default (default 20)
            body_size_pt=14,
            card_title_size_pt=18,
            footer_size_pt=15,           # non-default (default 13)
            formula_size_pt=24,          # non-default (default 18)
            card_corner_radius=10000,    # non-default (default 8000)
            bg_overlay_alpha=0.78,       # non-default (default 0.55)
            card_shadow=ShadowSpec(blur_pt=20, dist_pt=4, color="palette.accent", alpha_pct=20),
        ),
        layout=LayoutTokens(),
        decorations=DecorationTokens(),
    )

    md_text = serialize_style(schema)
    parsed = parse_design_md(md_text)

    # All non-default semantic fields preserved
    assert parsed.semantic.subtitle_size_pt == 24
    assert parsed.semantic.footer_size_pt == 15
    assert parsed.semantic.formula_size_pt == 24
    assert parsed.semantic.card_corner_radius == 10000
    assert parsed.semantic.bg_overlay_alpha == pytest.approx(0.78)
    # Sanity: typography sizes also roundtrip
    assert parsed.semantic.title_size_pt == 42
    assert parsed.semantic.body_size_pt == 14
    assert parsed.semantic.card_title_size_pt == 18
    # Shadow spec roundtrips
    assert parsed.semantic.card_shadow.blur_pt == 20
    assert parsed.semantic.card_shadow.dist_pt == 4
    assert parsed.semantic.card_shadow.color == "palette.accent"
    assert parsed.semantic.card_shadow.alpha_pct == 20


# ── build_phase3_constraints (v3.2) ───────────────────────


PHASE3_FULL_DESIGN_MD = """\
---
schema_version: "1.0"
name: 光合作用
colors:
  primary: "#69B578"
  accent: "#3D5A40"
  bg: "#F5FBEE"
  card_fill: "#FFFFFF"
  text: "#1A2E1F"
typography:
  title:      { fontFamily: "Noto Sans SC", fontSize: 38pt, fontWeight: 700 }
  card-title: { fontFamily: "Noto Sans SC", fontSize: 16pt, fontWeight: 600 }
  body:       { fontFamily: "Noto Sans SC", fontSize: 12pt }
---

## Overview
应被跳过的段。

## Colors
应被跳过的段。

## Typography
应被跳过——typography 走 frontmatter 路径。

## Layout
应被跳过的段。

## Elevation
卡片使用 {colors.primary}10 的柔和阴影；正文区域避免阴影叠加。

## Shapes
卡片使用 8px 圆角；图标采用 {colors.accent} 描边。

## Components
- card-knowledge: 背景 {colors.card_fill}，标题用 {colors.primary}。
- card-formula: 公式居中，背景 {colors.bg}。

## Do's and Don'ts
- Do 保持 ≤3 核心知识点/页。
- Don't 在小卡片里塞超过 2 行正文。
"""


def test_build_phase3_constraints_full_md():
    out = build_phase3_constraints(PHASE3_FULL_DESIGN_MD)

    # Header banner present
    assert "DESIGN.md 视觉契约" in out

    # Typography hard constraints injected (3 roles)
    assert "字体与字号" in out
    assert "title" in out and "38pt" in out
    assert "card-title" in out and "16pt" in out
    assert "body" in out and "12pt" in out

    # The four executable prose sections present
    assert "### Components" in out
    assert "### Elevation" in out
    assert "### Shapes" in out
    assert "### Do's and Don'ts" in out

    # Skipped sections must NOT bleed in
    assert "### Overview" not in out
    assert "### Colors" not in out
    assert "### Layout" not in out
    assert "### Typography" not in out

    # Color tokens resolved to actual hex
    assert "{colors.primary}" not in out
    assert "{colors.accent}" not in out
    assert "{colors.card_fill}" not in out
    assert "{colors.bg}" not in out
    assert "#69B578" in out  # primary
    assert "#3D5A40" in out  # accent
    assert "#FFFFFF" in out  # card_fill
    assert "#F5FBEE" in out  # bg


def test_build_phase3_constraints_empty_palette():
    """No palette in frontmatter, no override → tokens stay as raw text, no crash."""
    src = """\
---
schema_version: "1.0"
name: 无配色
---

## Components
card uses {colors.primary} fill.

## Do's and Don'ts
- Do something.
"""
    out = build_phase3_constraints(src)
    assert "{colors.primary}" in out  # left intact
    assert "### Components" in out


def test_build_phase3_constraints_unresolved_token():
    """Token referencing a key not in palette stays raw — no KeyError."""
    src = """\
---
schema_version: "1.0"
name: 部分配色
colors:
  primary: "#112233"
---

## Components
- a uses {colors.primary}, b uses {colors.nonexistent}.
"""
    out = build_phase3_constraints(src)
    assert "#112233" in out
    assert "{colors.nonexistent}" in out  # unresolved, raw


def test_build_phase3_constraints_missing_sections():
    """Missing prose sections must not raise; output skips them silently."""
    src = """\
---
schema_version: "1.0"
name: 仅 typography
typography:
  title: { fontFamily: "Noto Sans SC", fontSize: 38pt }
---

## Overview
仅有 Overview，无 Components / Elevation / Shapes / Do's。
"""
    out = build_phase3_constraints(src)
    assert "字体与字号" in out
    assert "title" in out
    # No prose section headers should be emitted when bodies are missing
    assert "### Components" not in out
    assert "### Elevation" not in out
    assert "### Shapes" not in out
    assert "### Do's and Don'ts" not in out


def test_resolve_color_tokens_multiple_refs():
    palette = {"primary": "#AABBCC", "accent": "#112233"}
    text = "use {colors.primary} and {colors.accent}, also {colors.primary} again."
    out = _resolve_color_tokens(text, palette)
    assert out == "use #AABBCC and #112233, also #AABBCC again."
