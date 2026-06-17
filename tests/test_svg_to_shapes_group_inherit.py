"""convert_g must not permanently mutate child elements when propagating group
presentation attrs. Children can be shared nodes (reached again via <use> into
<defs>, or under another group); a permanent child.set() froze the first
group's fill/stroke/opacity onto them and bled into later renders.
"""

from xml.etree import ElementTree as ET

from edupptx.output.svg_to_shapes import ConvertContext, convert_g

SVG_NS = "http://www.w3.org/2000/svg"


def _group(fill):
    return ET.fromstring(
        f'<g xmlns="{SVG_NS}" fill="{fill}">'
        f'<rect x="10" y="10" width="20" height="20"/></g>'
    )


def test_group_fill_inherited_during_conversion_but_child_left_unmutated():
    g = _group("#ff0000")
    rect = g[0]
    xml = convert_g(g, ConvertContext())
    assert "FF0000" in xml.upper()        # inheritance still applied to the output
    assert rect.get("fill") is None       # but the shared child node is untouched


def test_reconverting_same_child_reflects_the_new_group_fill():
    # Simulates a child reached under two different group contexts (e.g. the
    # same <defs> node inlined by two <use>s). The first render must not freeze
    # its fill so the second sees the wrong color.
    g = _group("#ff0000")
    convert_g(g, ConvertContext())          # first render: red
    g.set("fill", "#0000ff")
    xml2 = convert_g(g, ConvertContext())   # second render: must be blue, not red
    assert "0000FF" in xml2.upper()
    assert "FF0000" not in xml2.upper()
