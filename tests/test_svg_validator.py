"""Tests for V2 SVG validator and auto-fixer."""

import pytest
from lxml import etree

from edupptx.models import ImageNeed, MaterialNeeds, PagePlan
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
        # The new validator needs a container rect to compute wrap width.
        body = (
            '<rect x="100" y="100" width="600" height="200" fill="#FFF"/>'
            f'<text x="110" y="200" font-size="16">{long_text}</text>'
        )
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


class TestImageAspectRatioFrames:
    def _page_with_ratio(self, aspect_ratio: str) -> PagePlan:
        return PagePlan(
            page_number=8,
            page_type="content",
            title="比例校验",
            material_needs=MaterialNeeds(
                images=[
                    ImageNeed(
                        query="triangle diagram",
                        source="ai_generate",
                        role="illustration",
                        aspect_ratio=aspect_ratio,
                    )
                ]
            ),
        )

    def _first_image(self, fixed_svg: str):
        root = etree.fromstring(fixed_svg.encode())
        return root.find(f".//{{{SVG_NS}}}image")

    def test_repairs_4_3_image_frame_drawn_too_wide(self):
        page = self._page_with_ratio("4:3")
        svg = _make_svg(
            '<image href="__IMAGE_ILLUSTRATION_1__" x="94" y="174" '
            'width="472" height="210" preserveAspectRatio="xMidYMid slice"/>'
        )

        fixed, warnings = validate_and_fix(svg, page=page)

        img = self._first_image(fixed)
        width = float(img.get("width"))
        height = float(img.get("height"))
        assert width / height == pytest.approx(4 / 3, rel=0.01)
        assert any("image aspect ratio" in w.lower() for w in warnings)

    def test_accepts_valid_4_3_image_frame(self):
        page = self._page_with_ratio("4:3")
        svg = _make_svg(
            '<image href="__IMAGE_ILLUSTRATION_1__" x="100" y="120" '
            'width="400" height="300" preserveAspectRatio="xMidYMid slice"/>'
        )

        fixed, warnings = validate_and_fix(svg, page=page)

        img = self._first_image(fixed)
        assert float(img.get("width")) / float(img.get("height")) == pytest.approx(4 / 3)
        assert not any("image aspect ratio" in w.lower() for w in warnings)

    def test_defensively_normalizes_unsupported_page_ratio(self):
        page = self._page_with_ratio("32:15")
        svg = _make_svg(
            '<image href="__IMAGE_ILLUSTRATION_1__" x="74" y="210" '
            'width="472" height="221.25" preserveAspectRatio="xMidYMid slice"/>'
        )

        fixed, warnings = validate_and_fix(svg, page=page)

        img = self._first_image(fixed)
        width = float(img.get("width"))
        height = float(img.get("height"))
        assert width / height == pytest.approx(16 / 9, rel=0.01)
        assert any("unsupported image aspect ratio" in w.lower() for w in warnings)

    def test_updates_image_clip_rect_when_ratio_is_repaired(self):
        page = self._page_with_ratio("4:3")
        svg = _make_svg(
            '<defs><clipPath id="imgClip"><rect x="94" y="174" width="472" height="210"/></clipPath></defs>'
            '<image href="__IMAGE_ILLUSTRATION_1__" x="94" y="174" width="472" height="210" '
            'preserveAspectRatio="xMidYMid slice" clip-path="url(#imgClip)"/>'
        )

        fixed, warnings = validate_and_fix(svg, page=page)

        root = etree.fromstring(fixed.encode())
        img = root.find(f".//{{{SVG_NS}}}image")
        clip_rect = root.find(f".//{{{SVG_NS}}}clipPath/{{{SVG_NS}}}rect")
        assert clip_rect.get("x") == img.get("x")
        assert clip_rect.get("y") == img.get("y")
        assert clip_rect.get("width") == img.get("width")
        assert clip_rect.get("height") == img.get("height")
        assert any("image aspect ratio" in w.lower() for w in warnings)


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


class TestCircleLabelAutoFix:
    def test_adds_dominant_baseline_and_snaps(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
            '<circle cx="100" cy="200" r="18" fill="#2563EB"/>'
            '<text x="100" y="210" text-anchor="middle" font-size="16" fill="white">1</text>'
            '</svg>'
        )
        fixed, warnings = validate_and_fix(svg)
        assert 'dominant-baseline="middle"' in fixed
        assert 'y="200"' in fixed  # snapped to cy
        assert any('Auto-fixed circle label' in w for w in warnings)

    def test_skips_non_label_text(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
            '<circle cx="100" cy="200" r="18" fill="#2563EB"/>'
            '<text x="300" y="400" font-size="16">This is a long paragraph text</text>'
            '</svg>'
        )
        fixed, warnings = validate_and_fix(svg)
        assert not any('Auto-fixed circle label' in w for w in warnings)


class TestMathFontHandling:
    def test_math_font_preserved_for_equations(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><text x="50" y="200" font-family="Courier New, Consolas, monospace">x² + 2x + 1 = 0</text></svg>'
        fixed, warnings = validate_and_fix(svg)
        assert 'Courier New' in fixed
        assert not any('Replaced unsafe font' in w for w in warnings)

    def test_math_font_replaced_for_chinese_text(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><text x="50" y="200" font-family="Courier New">这是普通中文文本</text></svg>'
        fixed, warnings = validate_and_fix(svg)
        assert 'Courier New' not in fixed
        assert any('Replaced unsafe font' in w for w in warnings)


class TestHtmlEntityResolution:
    """Regression for the fill-in-the-blank slides that rendered literal
    '&nbsp;&nbsp;...' garbage: HTML named entities must resolve to real
    characters, never get blind-escaped into &amp;nbsp;."""

    def test_nbsp_renders_as_real_space_not_literal(self):
        body = '<text x="150" y="220">叫做（&nbsp;&nbsp;&nbsp;）</text>'
        fixed, _ = validate_and_fix(_make_svg(body))
        text = "".join(etree.fromstring(fixed.encode()).itertext())
        assert " " in text
        assert "&nbsp;" not in text
        assert "&amp;nbsp;" not in fixed

    def test_tspan_fillblank_mirrors_real_slide(self):
        # mirrors the broken slide_10 structure from session_20260531_233444
        body = (
            '<text x="150" y="220" font-size="24">'
            '<tspan x="150" dy="0">每份分得同样多，叫做（&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</tspan>'
            '<tspan x="150" dy="33">&nbsp;），它是除法的基础</tspan>'
            '</text>'
        )
        fixed, _ = validate_and_fix(_make_svg(body))
        assert "&amp;nbsp;" not in fixed
        text = "".join(etree.fromstring(fixed.encode()).itertext())
        assert "&nbsp;" not in text
        assert " " in text


class TestBareLessThanInMathText:
    """Regression: a bare '<' in math/chemistry text written WITHOUT a space
    after it (e.g. 'n<k', '0<x<1') used to be misread as a tag open, making
    lxml reject the slide. validate_and_fix then returned the UNPARSED original,
    silently skipping every other auto-fix. The escaper must recognise these as
    literal text while leaving real (nested) tags untouched."""

    def test_bare_lt_no_space_does_not_disable_the_whole_fixer(self):
        # The inequality is glued to a letter ("n<k") AND the font is unsafe.
        # If the bare '<' aborts parsing, the font fix never runs — so asserting
        # the font WAS replaced proves the fixer did not bail out.
        body = '<text x="100" y="200" font-family="Comic Sans" font-size="16">当 n<k 时</text>'
        fixed, warnings = validate_and_fix(_make_svg(body))
        assert not any("parse error" in w.lower() for w in warnings)
        assert "Noto Sans SC" in fixed  # downstream font fix still ran
        text = "".join(etree.fromstring(fixed.encode()).itertext())
        assert "n<k" in text  # &lt; round-trips back to a literal '<'

    def test_chained_inequality_preserved(self):
        body = '<text x="100" y="200" font-size="16">满足 0<x<1 的实数</text>'
        fixed, warnings = validate_and_fix(_make_svg(body))
        assert not any("parse error" in w.lower() for w in warnings)
        text = "".join(etree.fromstring(fixed.encode()).itertext())
        assert "0<x<1" in text

    def test_bare_lt_with_space_still_handled(self):
        # The case the old regex already handled must keep working.
        body = '<text x="100" y="200" font-size="16">当 k < 0 时</text>'
        fixed, warnings = validate_and_fix(_make_svg(body))
        assert not any("parse error" in w.lower() for w in warnings)
        text = "".join(etree.fromstring(fixed.encode()).itertext())
        assert "k < 0" in text

    def test_real_nested_tspan_not_corrupted(self):
        # The tag-shaped lookahead must NOT escape genuine inline child tags.
        body = (
            '<text x="100" y="200" font-size="16">'
            '前缀<tspan font-weight="bold">重点</tspan>后缀</text>'
        )
        fixed, warnings = validate_and_fix(_make_svg(body))
        assert not any("parse error" in w.lower() for w in warnings)
        root = etree.fromstring(fixed.encode())
        assert len(root.findall(f".//{{{SVG_NS}}}tspan")) == 1
        assert "重点" in "".join(root.itertext())


class TestTextReClampAfterOverlap:
    """Regression: _fix_text_overlaps cascades stacked text downward with no
    upper bound, and it runs AFTER _clamp_boundaries — so the lower lines used
    to march past y=720 (off-canvas, invisible) with nothing to re-clamp them.
    A final text re-clamp must keep every line inside the canvas."""

    def test_overlap_cascade_does_not_push_text_off_canvas(self):
        # Six near-identical lines bunched 5px apart near the bottom edge: the
        # overlap fix pushes each well below the previous, cascading past 720
        # (≈780 for the last line) before this fix was added.
        lines = "".join(
            f'<text x="120" y="{y}" font-size="20">内容行 {i} 的描述文字</text>'
            for i, y in enumerate(range(650, 680, 5))  # 650,655,660,665,670,675
        )
        fixed, warnings = validate_and_fix(_make_svg(lines))
        assert any("overlap" in w.lower() for w in warnings)  # scenario engaged
        root = etree.fromstring(fixed.encode())
        ys = [float(t.get("y")) for t in root.iter(f"{{{SVG_NS}}}text")]
        assert ys
        assert max(ys) <= 720, f"text pushed off-canvas: {ys}"
