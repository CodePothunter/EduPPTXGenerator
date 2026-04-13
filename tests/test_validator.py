"""Tests for the layout validator."""

import pytest

from edupptx.models import ResolvedFont, ResolvedShape, ResolvedSlide, ResolvedShadow
from edupptx.validator import validate_slides
from edupptx.style_schema import SLIDE_W, SLIDE_H, PT


def _shape(**kwargs):
    defaults = dict(shape_type="textbox", left=0, top=0, width=1000000, height=500000)
    defaults.update(kwargs)
    return ResolvedShape(**defaults)


def test_valid_shapes_no_warnings():
    slide = ResolvedSlide(shapes=[
        _shape(left=100000, top=100000, width=500000, height=300000),
    ])
    warnings = validate_slides([slide])
    assert len(warnings) == 0


def test_clamp_shape_beyond_right_edge():
    shape = _shape(left=SLIDE_W - 100000, width=500000)
    slide = ResolvedSlide(shapes=[shape])
    warnings = validate_slides([slide])
    assert any("exceeds right edge" in w for w in warnings)
    assert shape.width == 100000  # clamped


def test_clamp_negative_left():
    shape = _shape(left=-50000)
    slide = ResolvedSlide(shapes=[shape])
    warnings = validate_slides([slide])
    assert shape.left == 0  # clamped


def test_text_min_width_warning():
    font = ResolvedFont(family="Test", fallback="Arial", size_pt=12)
    shape = _shape(
        width=int(3 * 12 * PT),  # only 3 chars wide, need 6
        font=font,
    )
    slide = ResolvedSlide(shapes=[shape])
    warnings = validate_slides([slide])
    assert any("min" in w for w in warnings)


def test_no_false_positive_overlap():
    """Two non-overlapping card-like shapes should pass."""
    shadow = ResolvedShadow(blur_emu=100, dist_emu=50, color="#000", alpha_pct=10)
    s1 = _shape(shape_type="rounded_rect", left=0, width=500000, shadow=shadow)
    s2 = _shape(shape_type="rounded_rect", left=600000, width=500000, shadow=shadow)
    slide = ResolvedSlide(shapes=[s1, s2])
    warnings = validate_slides([slide])
    assert not any("overlap" in w.lower() for w in warnings)
