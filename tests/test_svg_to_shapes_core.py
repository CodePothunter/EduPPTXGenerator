"""Core SVG->DrawingML conversion regression tests for svg_to_shapes.py — the
project's headline "open-in-PowerPoint-and-edit" feature, previously near-zero
coverage. Pins the element converters, EMU coordinate mapping, CJK dual-font
emission, and the arc->cubic-Bézier endpoint.
"""

import base64
import io
import re
from xml.etree import ElementTree as ET

import pytest

from edupptx.output.svg_to_shapes import (
    ConvertContext,
    FONT_PX_TO_HUNDREDTHS_PT,
    _arc_to_cubic,
    convert_circle,
    convert_ellipse,
    convert_image,
    convert_line,
    convert_path,
    convert_rect,
    convert_svg_to_slide_shapes,
    convert_text,
    parse_font_family,
    px_to_emu,
)

SVG_NS = "http://www.w3.org/2000/svg"


def _el(tag, **attrs):
    e = ET.Element(tag)
    for k, v in attrs.items():
        e.set(k.replace("_", "-"), str(v))
    return e


def _off_ext(xml):
    # whitespace-tolerant so a harmless reflow of _wrap_shape can't silently
    # turn a geometry assertion into a None-subscript crash
    off = re.search(r'<a:off\s+x="(-?\d+)"\s+y="(-?\d+)"\s*/>', xml)
    ext = re.search(r'<a:ext\s+cx="(-?\d+)"\s+cy="(-?\d+)"\s*/>', xml)
    return int(off[1]), int(off[2]), int(ext[1]), int(ext[2])


class TestUnitConstants:
    def test_one_px_is_9525_emu(self):
        assert px_to_emu(1) == 9525

    def test_canvas_maps_to_standard_16_9_emu(self):
        # 1280x720 px -> the standard 16:9 EMU slide size
        assert px_to_emu(1280) == 12192000
        assert px_to_emu(720) == 6858000

    def test_font_px_to_hundredths_pt_constant(self):
        # pin the px->point basis non-circularly (96dpi px ≈ 0.75pt): 16px=12pt=1200
        assert FONT_PX_TO_HUNDREDTHS_PT == 75


class TestRect:
    def test_rect_position_size_fill_in_emu(self):
        xml = convert_rect(_el("rect", x=100, y=110, width=200, height=80, fill="#3366CC"),
                           ConvertContext())
        assert _off_ext(xml) == (px_to_emu(100), px_to_emu(110), px_to_emu(200), px_to_emu(80))
        assert 'prst="rect"' in xml
        assert '<a:srgbClr val="3366CC"' in xml  # # stripped, upper-cased

    def test_rx_makes_a_roundrect_with_adjust(self):
        xml = convert_rect(_el("rect", x=0, y=0, width=100, height=100, rx=10, fill="#fff"),
                           ConvertContext())
        assert 'prst="roundRect"' in xml
        assert '<a:gd name="adj"' in xml

    def test_negative_height_normalized_upward(self):
        # SVG bar charts grow upward via negative height; must become a positive
        # box whose top moved up by |h|.
        xml = convert_rect(_el("rect", x=50, y=300, width=40, height=-120, fill="#000"),
                           ConvertContext())
        off_x, off_y, _, ext_cy = _off_ext(xml)
        assert off_x == px_to_emu(50)
        assert off_y == px_to_emu(180)      # 300 + (-120)
        assert ext_cy == px_to_emu(120)     # height made positive

    def test_fill_none_emits_nofill(self):
        xml = convert_rect(_el("rect", x=0, y=0, width=10, height=10, fill="none"),
                           ConvertContext())
        assert "<a:noFill/>" in xml

    def test_zero_dimension_skipped(self):
        assert convert_rect(_el("rect", x=0, y=0, width=0, height=10), ConvertContext()) == ""


class TestEllipseLikeShapes:
    def test_circle_becomes_centered_ellipse_box(self):
        xml = convert_circle(_el("circle", cx=200, cy=150, r=50, fill="#abcdef"),
                             ConvertContext())
        # box top-left = center - r, extent = 2r
        assert _off_ext(xml) == (px_to_emu(150), px_to_emu(100), px_to_emu(100), px_to_emu(100))
        assert 'prst="ellipse"' in xml

    def test_ellipse_uses_rx_ry(self):
        xml = convert_ellipse(_el("ellipse", cx=100, cy=100, rx=60, ry=20, fill="#000"),
                              ConvertContext())
        assert _off_ext(xml) == (px_to_emu(40), px_to_emu(80), px_to_emu(120), px_to_emu(40))
        assert 'prst="ellipse"' in xml


class TestLineAndPath:
    def test_line_is_custgeom_moveto_lineto_nofill(self):
        xml = convert_line(_el("line", x1=10, y1=20, x2=110, y2=20, stroke="#000"),
                           ConvertContext())
        assert "<a:custGeom>" in xml
        assert "<a:moveTo>" in xml and "<a:lnTo>" in xml
        assert "<a:noFill/>" in xml  # a line has no area fill

    def test_path_cubic_and_close(self):
        d = "M 100 100 C 120 100 140 120 140 140 Z"
        xml = convert_path(_el("path", d=d, fill="#112233"), ConvertContext())
        assert "<a:custGeom>" in xml
        assert "<a:cubicBezTo>" in xml
        assert "<a:close/>" in xml
        assert '<a:srgbClr val="112233"' in xml


class TestArcToCubic:
    def test_arc_endpoint_is_preserved(self):
        # A quarter-circle arc from (100,0) to (0,100); the textbook arc->cubic
        # approximation must land its last control endpoint exactly on (0,100).
        cmds = _arc_to_cubic(100.0, 0.0, 100.0, 100.0, 0.0, 0, 1, 0.0, 100.0)
        assert cmds, "arc produced no segments"
        assert all(c.cmd == "C" for c in cmds)
        last = cmds[-1].args
        assert last[4] == pytest.approx(0.0, abs=1e-6)
        assert last[5] == pytest.approx(100.0, abs=1e-6)

    def test_degenerate_radius_falls_back_to_line(self):
        cmds = _arc_to_cubic(0.0, 0.0, 0.0, 0.0, 0.0, 0, 1, 50.0, 50.0)
        assert len(cmds) == 1 and cmds[0].cmd == "L"


class TestText:
    def test_text_is_a_txbox_with_run_text_and_size(self):
        xml = convert_text(_el("text", x=100, y=200, font_size=16), ConvertContext())
        # convert_text reads the element's text content
        el = _el("text", x=100, y=200, font_size=16)
        el.text = "Hello"
        xml = convert_text(el, ConvertContext())
        assert 'txBox="1"' in xml
        assert "<a:t>Hello</a:t>" in xml
        # 16px = 12pt = 1200 hundredths-of-pt (literal, not re-derived via the constant)
        assert 'sz="1200"' in xml

    def test_text_emits_both_latin_and_ea_fonts(self):
        el = _el("text", x=10, y=20, font_size=20, font_family="Microsoft YaHei, Arial")
        el.text = "你好 world"
        xml = convert_text(el, ConvertContext())
        assert '<a:latin typeface="Arial"/>' in xml
        assert '<a:ea typeface="Microsoft YaHei"/>' in xml

    def test_text_anchor_maps_to_alignment(self):
        el = _el("text", x=640, y=100, font_size=24, text_anchor="middle")
        el.text = "Centered"
        xml = convert_text(el, ConvertContext())
        assert 'algn="ctr"' in xml


class TestFontFamilyParsing:
    def test_default_is_arial_plus_noto(self):
        assert parse_font_family("") == {"latin": "Arial", "ea": "Noto Sans SC"}

    def test_cjk_font_routed_to_ea_latin_to_latin(self):
        fonts = parse_font_family("Helvetica, PingFang SC, sans-serif")
        assert fonts["latin"] == "Helvetica"
        assert fonts["ea"] == "PingFang SC"


class TestContextTransform:
    def test_group_translate_scale_applied_to_rect(self):
        ctx = ConvertContext(translate_x=200, translate_y=100, scale_x=2.0, scale_y=2.0)
        xml = convert_rect(_el("rect", x=10, y=10, width=30, height=40, fill="#000"), ctx)
        # final = translate + scale*local
        assert _off_ext(xml) == (
            px_to_emu(200 + 2 * 10), px_to_emu(100 + 2 * 10),
            px_to_emu(2 * 30), px_to_emu(2 * 40),
        )


class TestEndToEnd:
    def test_convert_svg_file_emits_a_shape_per_element(self, tmp_path):
        svg = (
            f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720">'
            '<rect x="50" y="60" width="200" height="100" fill="#224488"/>'
            '<text x="60" y="120" font-size="18">标题 Title</text>'
            '<circle cx="900" cy="400" r="40" fill="#cc0000"/>'
            '</svg>'
        )
        p = tmp_path / "slide.svg"
        p.write_text(svg, encoding="utf-8")

        slide_xml, _media, _rels = convert_svg_to_slide_shapes(p, slide_num=1)

        assert slide_xml.count("<p:sp>") == 3           # exactly rect + text + circle
        assert "标题 Title" in slide_xml                # CJK + latin text preserved
        assert 'prst="rect"' in slide_xml and 'prst="ellipse"' in slide_xml
        assert '<a:srgbClr val="224488"' in slide_xml   # rect fill carried through


class TestStroke:
    def test_stroke_width_color_dash_and_cap(self):
        el = _el("rect", x=0, y=0, width=10, height=10, fill="none",
                 stroke="#FF0000", stroke_width=3, stroke_dasharray="4,4", stroke_linecap="round")
        xml = convert_rect(el, ConvertContext())
        assert f'<a:ln w="{px_to_emu(3)}"' in xml      # stroke-width px -> EMU
        assert '<a:srgbClr val="FF0000"' in xml
        assert 'prstDash val="dash"' in xml
        assert 'cap="rnd"' in xml

    def test_no_stroke_is_a_nofill_line(self):
        xml = convert_rect(_el("rect", x=0, y=0, width=10, height=10, fill="#000"), ConvertContext())
        assert "<a:ln><a:noFill/></a:ln>" in xml


class TestGradientFill:
    def test_linear_gradient_stops_colors_and_angle(self):
        grad = ET.fromstring(
            f'<linearGradient xmlns="{SVG_NS}" id="g" x1="0" y1="0" x2="100" y2="0">'
            '<stop offset="0" stop-color="#ff0000"/>'
            '<stop offset="100%" stop-color="#0000ff"/>'
            '</linearGradient>'
        )
        ctx = ConvertContext(defs={"g": grad})
        xml = convert_rect(_el("rect", x=0, y=0, width=100, height=100, fill="url(#g)"), ctx)
        assert "<a:gradFill>" in xml
        assert '<a:gs pos="0">' in xml and '<a:gs pos="100000">' in xml  # 0 and 100%
        assert '<a:srgbClr val="FF0000"' in xml and '<a:srgbClr val="0000FF"' in xml
        assert "<a:lin ang=" in xml

    def test_radial_gradient_emits_circle_path(self):
        grad = ET.fromstring(
            f'<radialGradient xmlns="{SVG_NS}" id="r">'
            '<stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#000000"/>'
            '</radialGradient>'
        )
        ctx = ConvertContext(defs={"r": grad})
        xml = convert_rect(_el("rect", x=0, y=0, width=50, height=50, fill="url(#r)"), ctx)
        assert "<a:gradFill>" in xml
        assert '<a:path path="circle">' in xml


class TestImage:
    def test_image_emits_pic_with_registered_media_and_relationship(self):
        buf = io.BytesIO()
        from PIL import Image
        Image.new("RGB", (10, 10), (200, 30, 30)).save(buf, "PNG")
        data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        el = _el("image", x=100, y=100, width=200, height=150, preserveAspectRatio="none")
        el.set("href", data_uri)
        ctx = ConvertContext()

        xml = convert_image(el, ctx)

        assert "<p:pic>" in xml
        # preserveAspectRatio="none" -> raw box, no aspect refit
        assert _off_ext(xml) == (px_to_emu(100), px_to_emu(100), px_to_emu(200), px_to_emu(150))
        # the only converter that registers media + a relationship
        assert len(ctx.media_files) == 1
        assert len(ctx.rel_entries) == 1
        rel = ctx.rel_entries[0]
        assert rel["type"].endswith("/image")
        assert rel["target"].startswith("../media/")
        # the blip's embed id must match the registered relationship id
        embed = re.search(r'r:embed="([^"]+)"', xml)[1]
        assert embed == rel["id"]
