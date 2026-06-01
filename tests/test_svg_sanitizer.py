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
        # The < at end gets escaped to &lt; by pre-clean, but still unparseable
        assert result == "<not-svg>broken&lt;"


def _text_of(result: str) -> str:
    from lxml import etree

    return "".join(etree.fromstring(result.encode()).itertext())


class TestHtmlEntityResolution:
    """LLM emits HTML named entities (&nbsp; etc.); SVG is XML and only
    predefines amp/lt/gt/quot/apos. They must resolve to real characters,
    not get blindly &-escaped into visible literal text."""

    def test_nbsp_becomes_real_nbsp_not_literal(self):
        svg = _make_svg('<text x="10" y="10">叫做（&nbsp;&nbsp;）</text>')
        result = sanitize_for_ppt(svg)
        assert " " in _text_of(result)  # rendered as real NBSP
        assert "&nbsp;" not in _text_of(result)  # not literal garbage
        assert "&amp;nbsp;" not in result  # not double-escaped in markup

    def test_mdash_resolved(self):
        svg = _make_svg('<text x="10" y="10">A&mdash;B</text>')
        result = sanitize_for_ppt(svg)
        assert "—" in _text_of(result)
        assert "mdash" not in result

    def test_bare_ampersand_still_escaped(self):
        # "A & B" is not an entity (no trailing ;) — must stay literal &
        svg = _make_svg('<text x="10" y="10">A & B</text>')
        result = sanitize_for_ppt(svg)
        assert _text_of(result).strip() == "A & B"

    def test_bare_lt_still_escaped(self):
        svg = _make_svg('<text x="10" y="10">k < 0</text>')
        result = sanitize_for_ppt(svg)
        assert "k < 0" in _text_of(result)

    def test_predefined_and_numeric_untouched(self):
        svg = _make_svg('<text x="10" y="10">&amp; &#160; &lt;</text>')
        result = sanitize_for_ppt(svg)
        text = _text_of(result)
        assert "&" in text and " " in text and "<" in text


class TestResolveHtmlEntitiesUnit:
    def test_maps_named_entities_to_numeric(self):
        from edupptx.postprocess.svg_sanitizer import resolve_html_entities

        assert resolve_html_entities("&nbsp;") == "&#160;"
        assert resolve_html_entities("&mdash;") == "&#8212;"
        assert resolve_html_entities("&times;") == "&#215;"

    def test_preserves_xml_predefined(self):
        from edupptx.postprocess.svg_sanitizer import resolve_html_entities

        s = "&amp;&lt;&gt;&quot;&apos;"
        assert resolve_html_entities(s) == s

    def test_leaves_unknown_and_bare_ampersand_and_numeric(self):
        from edupptx.postprocess.svg_sanitizer import resolve_html_entities

        assert resolve_html_entities("&notareal;") == "&notareal;"
        assert resolve_html_entities("Tom & Jerry") == "Tom & Jerry"
        assert resolve_html_entities("&#160;") == "&#160;"
