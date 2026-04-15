"""Tests for V2 SVG sanitizer (PPT-specific fixes)."""

from edupptx.postprocess.svg_sanitizer import sanitize_for_ppt

SVG_NS = "http://www.w3.org/2000/svg"


def _make_svg(body: str = "") -> str:
    return f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720">{body}</svg>'


class TestScriptRemoval:
    def test_removes_script_tags(self):
        svg = _make_svg('<script>alert("xss")</script>')
        result = sanitize_for_ppt(svg)
        assert "<script" not in result
        assert "alert" not in result


class TestEventHandlerRemoval:
    def test_removes_onclick(self):
        svg = _make_svg('<rect onclick="alert(1)" x="0" y="0" width="100" height="100"/>')
        result = sanitize_for_ppt(svg)
        assert "onclick" not in result

    def test_removes_onmouseover(self):
        svg = _make_svg('<rect onmouseover="foo()" x="0" y="0" width="50" height="50"/>')
        result = sanitize_for_ppt(svg)
        assert "onmouseover" not in result


class TestWidthHeightRemoval:
    def test_removes_width_height(self):
        svg = f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720" width="1280" height="720"></svg>'
        result = sanitize_for_ppt(svg)
        # width/height on root should be removed, viewBox kept
        assert 'viewBox' in result
        # Check width= attr is gone (not part of viewBox)
        from lxml import etree
        root = etree.fromstring(result.encode())
        assert root.get("width") is None
        assert root.get("height") is None


class TestCommentStripping:
    def test_strips_comments(self):
        svg = _make_svg('<!-- this is a comment --><rect x="0" y="0" width="100" height="100"/>')
        result = sanitize_for_ppt(svg)
        assert "comment" not in result


class TestUnparseableSvg:
    def test_returns_as_is(self):
        bad_svg = "<not-svg>broken<"
        result = sanitize_for_ppt(bad_svg)
        assert result == bad_svg
