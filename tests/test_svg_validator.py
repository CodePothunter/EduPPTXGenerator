"""Tests for V2 SVG validator and auto-fixer."""

import pytest
from lxml import etree

from edupptx.postprocess.svg_validator import validate_and_fix


SVG_NS = "http://www.w3.org/2000/svg"


def _make_svg(body: str = "", viewbox: str = "0 0 1280 720") -> str:
    return f'<svg xmlns="{SVG_NS}" viewBox="{viewbox}">{body}</svg>'


class TestViewboxFix:
    def test_correct_viewbox_no_warning(self):
        svg = _make_svg()
        fixed, warnings = validate_and_fix(svg)
        viewbox_warnings = [w for w in warnings if "viewBox" in w]
        assert viewbox_warnings == []

    def test_wrong_viewbox_fixed(self):
        svg = _make_svg(viewbox="0 0 800 600")
        fixed, warnings = validate_and_fix(svg)
        assert any("viewBox" in w for w in warnings)
        root = etree.fromstring(fixed.encode())
        assert root.get("viewBox") == "0 0 1280 720"


class TestForeignObjectRemoval:
    def test_removes_foreign_object(self):
        body = '<foreignObject x="0" y="0" width="100" height="100"><div>hi</div></foreignObject>'
        svg = _make_svg(body)
        fixed, warnings = validate_and_fix(svg)
        assert "foreignObject" not in fixed


class TestFontFix:
    def test_unsafe_font_replaced(self):
        body = '<text x="10" y="10" font-family="Comic Sans">hello</text>'
        svg = _make_svg(body)
        fixed, warnings = validate_and_fix(svg)
        assert any("font" in w.lower() for w in warnings)
        assert "Noto Sans SC" in fixed

    def test_safe_font_untouched(self):
        body = '<text x="10" y="10" font-family="Noto Sans SC, Arial, sans-serif">hello</text>'
        svg = _make_svg(body)
        fixed, warnings = validate_and_fix(svg)
        font_warnings = [w for w in warnings if "Replaced unsafe font" in w]
        assert font_warnings == []


class TestTextWrapping:
    def test_long_text_wrapped(self):
        long_text = "这是一段很长的中文文本，" * 5  # ~55 chars
        body = f'<text x="100" y="200" font-size="16">{long_text}</text>'
        svg = _make_svg(body)
        fixed, warnings = validate_and_fix(svg)
        assert any("Wrapped" in w for w in warnings)
        assert "tspan" in fixed

    def test_short_text_not_wrapped(self):
        body = '<text x="100" y="200" font-size="16">短文本</text>'
        svg = _make_svg(body)
        fixed, warnings = validate_and_fix(svg)
        wrap_warnings = [w for w in warnings if "Wrapped" in w]
        assert wrap_warnings == []


class TestBoundaryClamp:
    def test_negative_x_clamped(self):
        body = '<rect x="-10" y="100" width="200" height="100"/>'
        svg = _make_svg(body)
        fixed, warnings = validate_and_fix(svg)
        assert any("Clamped" in w for w in warnings)

    def test_within_bounds_no_clamp(self):
        body = '<rect x="100" y="100" width="200" height="100"/>'
        svg = _make_svg(body)
        fixed, warnings = validate_and_fix(svg)
        clamp_warnings = [w for w in warnings if "Clamped" in w]
        assert clamp_warnings == []


class TestTextOverlap:
    def test_overlapping_texts_fixed(self):
        body = (
            '<text x="100" y="200" font-size="20">Line 1</text>'
            '<text x="100" y="205" font-size="20">Line 2</text>'  # too close
        )
        svg = _make_svg(body)
        fixed, warnings = validate_and_fix(svg)
        assert any("overlap" in w.lower() for w in warnings)

    def test_well_spaced_texts_no_fix(self):
        body = (
            '<text x="100" y="200" font-size="16">Line 1</text>'
            '<text x="100" y="300" font-size="16">Line 2</text>'  # far apart
        )
        svg = _make_svg(body)
        fixed, warnings = validate_and_fix(svg)
        overlap_warnings = [w for w in warnings if "overlap" in w.lower()]
        assert overlap_warnings == []


class TestImageHrefCheck:
    def test_empty_href_warns(self):
        body = '<image x="100" y="100" width="200" height="150" href=""/>'
        svg = _make_svg(body)
        _, warnings = validate_and_fix(svg)
        assert any("empty href" in w for w in warnings)


class TestUnescapedAmpersand:
    def test_unescaped_amp_recovered(self):
        svg = _make_svg('<text x="10" y="10">A & B</text>')
        fixed, warnings = validate_and_fix(svg)
        assert "&amp;" in fixed


class TestCSSAnimationRemoval:
    def test_removes_animations(self):
        body = '<style>@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } } .box { animation: spin 2s; }</style><rect class="box" x="0" y="0" width="100" height="100"/>'
        svg = _make_svg(body)
        fixed, warnings = validate_and_fix(svg)
        assert any("animation" in w.lower() for w in warnings)
        assert "@keyframes" not in fixed


class TestPPTBlacklist:
    def test_removes_smil_animate(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect x="50" y="50" width="100" height="100"/><animate attributeName="x" from="0" to="100" dur="1s"/></svg>'
        fixed, warnings = validate_and_fix(svg)
        assert '<animate' not in fixed
        assert any('animate' in w.lower() or 'SMIL' in w for w in warnings)

    def test_removes_marker(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><defs><marker id="arrow"><path d="M0,0 L10,5 L0,10"/></marker></defs><line x1="50" y1="50" x2="200" y2="200" marker-end="url(#arrow)"/></svg>'
        fixed, warnings = validate_and_fix(svg)
        assert '<marker' not in fixed
        assert 'marker-end' not in fixed

    def test_fixes_rgba_color(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect x="50" y="50" width="100" height="100" fill="rgba(30,64,175,0.5)"/></svg>'
        fixed, warnings = validate_and_fix(svg)
        assert 'rgba' not in fixed
        assert any('rgba' in w for w in warnings)

    def test_warns_clippath(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><defs><clipPath id="c"><rect x="0" y="0" width="100" height="100"/></clipPath></defs><rect x="50" y="50" width="100" height="100"/></svg>'
        _, warnings = validate_and_fix(svg)
        assert any('clipPath' in w for w in warnings)

    def test_warns_g_opacity(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><g opacity="0.5"><rect x="50" y="50" width="100" height="100"/></g></svg>'
        _, warnings = validate_and_fix(svg)
        assert any('opacity' in w for w in warnings)
