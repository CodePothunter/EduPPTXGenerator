"""Tests for style_linter — WCAG contrast + broken-ref rules."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from edupptx.postprocess.style_linter import (
    Finding,
    StyleValidationError,
    _is_valid_hex,
    contrast_ratio,
    lint_resolved_style,
    lint_style_schema,
    wcag_relative_luminance,
)
from edupptx.style_resolver import resolve_style
from edupptx.style_schema import StyleSchema, load_style

STYLES_DIR = Path(__file__).parent.parent / "styles"


# ── Helpers ────────────────────────────────────────────────


def _load_emerald_schema() -> StyleSchema:
    return load_style(STYLES_DIR / "emerald.json")


def _load_blue_schema() -> StyleSchema:
    return load_style(STYLES_DIR / "blue.json")


# ── 1. Existing emerald.json: 0 findings ───────────────────


def test_emerald_no_findings():
    schema = _load_emerald_schema()
    resolved = resolve_style(schema)  # should not raise
    assert lint_style_schema(schema) == []
    assert lint_resolved_style(resolved) == []


# ── 2. Existing blue.json: 0 findings ──────────────────────


def test_blue_no_findings():
    schema = _load_blue_schema()
    resolved = resolve_style(schema)  # should not raise
    assert lint_style_schema(schema) == []
    assert lint_resolved_style(resolved) == []


# ── 3. Crafted contrast warning (#888 on #FFF) ─────────────


def test_low_contrast_triggers_warning():
    schema = _load_emerald_schema()
    resolved = resolve_style(schema)
    # body=#888888 on card_fill=#FFFFFF -> 3.54:1 < 4.5
    bad = replace(resolved, body_color="#888888", card_fill_color="#FFFFFF")
    findings = lint_resolved_style(bad)
    contrast_findings = [f for f in findings if f.rule == "contrast-ratio"]
    assert any("body_on_card_fill" in f.path for f in contrast_findings)
    assert all(f.severity == "warning" for f in contrast_findings)
    # Sanity: actual WCAG ratio for #888 on #FFF
    ratio = contrast_ratio("#888888", "#FFFFFF")
    assert 3.5 < ratio < 3.6


# ── 4. Crafted broken-ref triggers error ───────────────────


def test_broken_ref_triggers_error():
    schema = _load_emerald_schema()
    schema.semantic.body_color = "palette.nonexistent"
    findings = lint_style_schema(schema)
    broken = [f for f in findings if f.rule == "broken-ref"]
    assert len(broken) == 1
    assert broken[0].severity == "error"
    assert broken[0].path == "semantic.body_color"
    assert "nonexistent" in broken[0].message


# ── 5. [critical] None / rgba never raises ─────────────────


def test_none_and_rgba_skipped_safely():
    schema = _load_emerald_schema()
    resolved = resolve_style(schema)

    # None card_fill should not raise
    safe1 = replace(resolved, card_fill_color=None)  # type: ignore[arg-type]
    findings1 = lint_resolved_style(safe1)
    # No contrast finding involving card_fill bg should exist
    assert not any("on_card_fill" in f.path for f in findings1)

    # rgba card_fill should also be skipped
    safe2 = replace(resolved, card_fill_color="rgba(255,0,0,0.5)")
    findings2 = lint_resolved_style(safe2)
    assert not any("on_card_fill" in f.path for f in findings2)

    # gradient string should also be skipped
    safe3 = replace(resolved, bg_overlay_color="linear-gradient(45deg, #fff, #000)")
    findings3 = lint_resolved_style(safe3)
    assert not any("on_bg_overlay" in f.path for f in findings3)


# ── 6. [critical] Short-form #fff handled correctly ────────


def test_short_hex_supported():
    assert _is_valid_hex("#fff") is True
    assert _is_valid_hex("#FFF") is True
    assert _is_valid_hex("#ffffff") is True
    assert _is_valid_hex("#FFFFFF") is True

    # Invalid forms
    assert _is_valid_hex(None) is False
    assert _is_valid_hex("") is False
    assert _is_valid_hex("rgba(255,0,0,0.5)") is False
    assert _is_valid_hex("linear-gradient(45deg,#fff,#000)") is False
    assert _is_valid_hex("#xyz") is False        # invalid hex chars (3-char)
    assert _is_valid_hex("#abcd") is False       # 5 chars total (not 4 or 7)
    assert _is_valid_hex("#xyzxyz") is False     # invalid hex chars (6-char)
    assert _is_valid_hex("fff") is False         # missing '#'

    # Short hex contrast: #fff luminance == #ffffff luminance
    assert wcag_relative_luminance("#fff") == pytest.approx(
        wcag_relative_luminance("#ffffff")
    )
    # Short hex pair must work without error in lint pipeline
    ratio = contrast_ratio("#000", "#fff")
    assert ratio == pytest.approx(21.0, abs=0.01)


# ── 7. EDUPPTX_LINT_STRICT=1 escalates warning -> error ────


def test_strict_mode_escalates_warning(monkeypatch):
    monkeypatch.setenv("EDUPPTX_LINT_STRICT", "1")
    schema = _load_emerald_schema()
    # Make low-contrast: body color = light gray on white card
    schema.semantic.body_color = "#888888"
    schema.semantic.card_fill_color = "#FFFFFF"
    with pytest.raises(StyleValidationError) as exc_info:
        resolve_style(schema)
    findings = exc_info.value.findings
    assert any(f.rule == "contrast-ratio" for f in findings)


def test_non_strict_mode_warning_does_not_raise(monkeypatch):
    monkeypatch.delenv("EDUPPTX_LINT_STRICT", raising=False)
    schema = _load_emerald_schema()
    schema.semantic.body_color = "#888888"
    schema.semantic.card_fill_color = "#FFFFFF"
    # No raise — warnings are logged, not raised
    resolve_style(schema)


# ── Bonus: broken-ref always raises regardless of strict mode ──


def test_broken_ref_raises_in_non_strict(monkeypatch):
    monkeypatch.delenv("EDUPPTX_LINT_STRICT", raising=False)
    schema = _load_emerald_schema()
    schema.semantic.body_color = "palette.nonexistent"
    with pytest.raises(StyleValidationError) as exc_info:
        resolve_style(schema)
    assert any(f.rule == "broken-ref" for f in exc_info.value.findings)


def test_broken_ref_raises_in_strict(monkeypatch):
    monkeypatch.setenv("EDUPPTX_LINT_STRICT", "1")
    schema = _load_emerald_schema()
    schema.semantic.body_color = "palette.nonexistent"
    with pytest.raises(StyleValidationError) as exc_info:
        resolve_style(schema)
    assert any(f.rule == "broken-ref" for f in exc_info.value.findings)


# ── Sanity tests for math ───────────────────────────────────


def test_contrast_ratio_extremes():
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio("#FFFFFF", "#FFFFFF") == pytest.approx(1.0, abs=0.01)


def test_finding_dataclass_fields():
    f = Finding(severity="warning", rule="contrast-ratio", path="x", message="y")
    assert f.severity == "warning"
    assert f.rule == "contrast-ratio"
