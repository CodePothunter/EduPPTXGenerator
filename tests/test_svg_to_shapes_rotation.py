"""convert_path maps SVG rotate() pivot semantics onto PowerPoint's rotation.

SVG ``rotate(theta[, cx, cy])`` pivots about the origin (or cx,cy); PPT's xfrm
``rot`` pivots about the shape's bbox center. convert_path must offset the
placement so the two land in the same spot. Regression for the bbox-center
pivot bug (rotated paths landed rotated-in-place instead of swung about the
SVG pivot).
"""

import re
from xml.etree import ElementTree as ET

from edupptx.output.svg_to_shapes import ConvertContext, convert_path, px_to_emu

# 10x10 square at (100,100); unrotated bbox center = (105,105)
SQUARE = "M 100 100 L 110 100 L 110 110 L 100 110 Z"


def _convert(transform):
    elem = ET.Element("path")
    elem.set("d", SQUARE)
    if transform:
        elem.set("transform", transform)
    return convert_path(elem, ConvertContext())


def _xfrm(xml):
    off = re.search(r'<a:off x="(-?\d+)" y="(-?\d+)"/>', xml)
    rot = re.search(r'rot="(-?\d+)"', xml)
    return (int(off.group(1)), int(off.group(2))), (int(rot.group(1)) if rot else 0)


def test_rotate_about_origin_offsets_placement():
    # 90° about the origin sends center (105,105) -> (-105,105); the bbox
    # top-left must follow to (-110,100). (Old code ignored the pivot and left
    # it at (100,100).)
    off, rot = _xfrm(_convert("rotate(90 0 0)"))
    assert off == (px_to_emu(-110), px_to_emu(100))
    assert rot == 90 * 60000


def test_rotate_no_pivot_defaults_to_origin():
    # rotate(theta) with no cx,cy pivots about the origin: 180° sends center
    # (105,105) -> (-105,-105); top-left -> (-110,-110).
    off, rot = _xfrm(_convert("rotate(180)"))
    assert off == (px_to_emu(-110), px_to_emu(-110))
    assert rot == 180 * 60000


def test_rotate_about_own_center_leaves_placement_unchanged():
    # When the SVG pivot equals the bbox center, PPT's center-rotation already
    # matches, so the offset is zero.
    off, rot = _xfrm(_convert("rotate(45 105 105)"))
    assert off == (px_to_emu(100), px_to_emu(100))
    assert rot == 45 * 60000


def test_unrotated_path_placement_unchanged():
    off, rot = _xfrm(_convert(None))
    assert off == (px_to_emu(100), px_to_emu(100))
    assert rot == 0
