from edupptx.materials.icons import get_icon_svg, list_icons


def test_svg_icon_helpers_do_not_require_cairo_runtime():
    assert isinstance(list_icons(), list)
    assert "<svg" in get_icon_svg("not-a-real-icon")
