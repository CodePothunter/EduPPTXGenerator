"""Tests for Phase 3 DESIGN.md injection (v3.2).

Verifies:
- build_svg_system_prompt emits a "DESIGN.md 视觉契约" block when design_md
  is passed and stays bytewise-equal to the v3.1 path when it's not.
- Agent's lint hook on a broken-ref DESIGN.md triggers StyleValidationError
  (the precondition that makes _phase1e flow downgrade design_md to None).
- Phase 3 with design_md=None produces no DESIGN.md visual contract section.
"""

from __future__ import annotations

import pytest

from edupptx.design.prompts import build_svg_system_prompt
from edupptx.postprocess.style_linter import StyleValidationError
from edupptx.style.design_md import parse_design_md
from edupptx.style_resolver import resolve_style


# ── Fixtures ──────────────────────────────────────────────


_VALID_DESIGN_MD = """\
---
schema_version: "1.0"
name: 注入测试
colors:
  primary: "#69B578"
  accent: "#3D5A40"
  bg: "#F5FBEE"
  card_fill: "#FFFFFF"
  text: "#1A2E1F"
  text_secondary: "#475569"
  shadow: "#9CC59E"
  icon: "#3D5A40"
typography:
  title:      { fontFamily: "Noto Sans SC", fontSize: 38pt, fontWeight: 700 }
  card-title: { fontFamily: "Noto Sans SC", fontSize: 16pt, fontWeight: 600 }
  body:       { fontFamily: "Noto Sans SC", fontSize: 12pt }
---

## Overview
skipped section.

## Components
- card-knowledge uses {colors.card_fill} background and {colors.primary} accent.

## Do's and Don'ts
- Do keep ≤3 ideas per page.
- Don't overcrowd cards.
"""


_BROKEN_REF_DESIGN_MD = """\
---
schema_version: "1.0"
name: 坏引用
colors:
  primary: "#1E293B"
  accent: "#2563EB"
  bg: "#EFF6FF"
  card_fill: "#FFFFFF"
  text: "#1E293B"
  text_secondary: "#475569"
  shadow: "#93C5FD"
  icon: "#3B82F6"
pptx-extensions:
  card_shadow:
    color: "palette.totally_made_up_key"
---

## Overview
broken via shadow color ref.
"""


# ── Tests ─────────────────────────────────────────────────


def test_build_svg_system_prompt_with_design_md():
    """When design_md is passed, the visual contract block must appear."""
    prompt = build_svg_system_prompt(
        style_guide="some style guide",
        visual_plan=None,
        content_density="lecture",
        design_md=_VALID_DESIGN_MD,
    )
    assert "DESIGN.md 视觉契约" in prompt
    # Token resolution survives all the way through into the final prompt
    assert "{colors.card_fill}" not in prompt
    assert "{colors.primary}" not in prompt
    assert "#69B578" in prompt
    assert "#FFFFFF" in prompt
    # Typography hard constraints reach the prompt
    assert "字体与字号" in prompt
    assert "38pt" in prompt
    # The skipped sections must NOT bleed into the prompt
    assert "### Overview" not in prompt


def test_build_svg_system_prompt_without_design_md():
    """When design_md is omitted, output is bytewise-identical to passing None."""
    no_arg = build_svg_system_prompt(
        style_guide="some style guide",
        visual_plan=None,
        content_density="lecture",
    )
    explicit_none = build_svg_system_prompt(
        style_guide="some style guide",
        visual_plan=None,
        content_density="lecture",
        design_md=None,
    )
    assert no_arg == explicit_none
    # And no DESIGN.md visual contract block leaks in
    assert "DESIGN.md 视觉契约" not in no_arg


def test_phase3_lint_fail_falls_back():
    """Broken-ref DESIGN.md raises in resolve_style; agent's try/except sets
    design_md_str=None, so Phase 3 takes the legacy path. Verifying the two
    halves: (1) lint catches it, (2) the legacy path with design_md=None
    produces a valid prompt that omits the visual contract block."""
    # 1. Confirm that broken-ref DESIGN.md trips the lint rule.
    schema = parse_design_md(_BROKEN_REF_DESIGN_MD)
    with pytest.raises(StyleValidationError):
        resolve_style(schema)

    # 2. Phase 3 fallback (design_md=None) returns a non-empty prompt without
    #    the DESIGN.md visual contract block — i.e. the run is not blocked.
    fallback_prompt = build_svg_system_prompt(
        style_guide="guide",
        visual_plan=None,
        content_density="lecture",
        design_md=None,
    )
    assert fallback_prompt
    assert "DESIGN.md 视觉契约" not in fallback_prompt
