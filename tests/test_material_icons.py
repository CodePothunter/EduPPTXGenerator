import pytest

from edupptx.materials.icons import _FALLBACK_SVG, _ICON_ALIASES, get_icon_svg, list_icons


def test_svg_icon_helpers_do_not_require_cairo_runtime():
    assert isinstance(list_icons(), list)
    assert "<svg" in get_icon_svg("not-a-real-icon")


def test_unknown_icon_falls_back_to_circle():
    assert get_icon_svg("definitely-not-an-icon", "#333") == _FALLBACK_SVG.format(color="#333")


@pytest.mark.parametrize("name,target", sorted(_ICON_ALIASES.items()))
def test_alias_targets_exist_and_resolve_to_real_icon(name, target):
    # Every alias must point at an icon that actually ships, and asking for the
    # missing common name must yield that real icon — not the blank circle.
    assert target in set(list_icons()), f"alias target '{target}' missing from icon set"
    svg = get_icon_svg(name, "#333")
    assert svg != _FALLBACK_SVG.format(color="#333")
    assert "<svg" in svg
