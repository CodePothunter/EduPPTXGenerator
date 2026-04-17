"""Embed icon placeholders — replace <use data-icon="xxx"/> with actual Lucide SVG content."""

from __future__ import annotations

from lxml import etree
from loguru import logger

from edupptx.materials.icons import get_icon_svg

SVG_NS = "http://www.w3.org/2000/svg"
_PRESENTATION_ATTRS = (
    "fill",
    "stroke",
    "opacity",
    "fill-opacity",
    "stroke-opacity",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-dasharray",
)


def _copy_presentation_attrs(source: etree._Element, target: etree._Element) -> None:
    """Copy SVG presentation attributes so icon styling survives <svg> -> <g> embedding."""
    for attr in _PRESENTATION_ATTRS:
        value = source.get(attr)
        if value is not None:
            target.set(attr, value)


def embed_icon_placeholders(svg_content: str, icon_color: str = "#333") -> tuple[str, int]:
    """Replace <use data-icon="xxx"/> with actual Lucide SVG content.

    Args:
        svg_content: SVG string possibly containing <use data-icon="..."/> placeholders.
        icon_color: Default color for icons (hex).

    Returns:
        (processed_svg, count_of_replacements)
    """
    # Quick check: skip parsing if no data-icon present
    if "data-icon" not in svg_content:
        return svg_content, 0

    try:
        root = etree.fromstring(svg_content.encode("utf-8"))
    except etree.XMLSyntaxError:
        return svg_content, 0

    count = 0
    # Find all <use> elements with data-icon attribute
    for use_el in list(root.iter(f"{{{SVG_NS}}}use")):
        icon_name = use_el.get("data-icon")
        if not icon_name:
            continue

        # Get position and size from the <use> element
        x = float(use_el.get("x", "0"))
        y = float(use_el.get("y", "0"))
        w = float(use_el.get("width", "48"))
        h = float(use_el.get("height", "48"))
        color = use_el.get("fill", icon_color)

        # Load the icon SVG
        icon_svg_str = get_icon_svg(icon_name, color=color)

        # Parse the icon SVG to extract child elements
        try:
            icon_root = etree.fromstring(icon_svg_str.encode("utf-8"))
        except etree.XMLSyntaxError:
            logger.warning("Failed to parse icon SVG for '{}'", icon_name)
            continue

        # Lucide icons are 24x24 viewBox. Scale to requested width/height.
        sx = w / 24.0
        sy = h / 24.0

        # Create a <g> wrapper with transform
        g = etree.Element(f"{{{SVG_NS}}}g")
        g.set("transform", f"translate({x},{y}) scale({sx},{sy})")
        _copy_presentation_attrs(icon_root, g)

        # Copy all child elements from icon SVG into the group
        for child in icon_root:
            g.append(child)

        # Replace the <use> with the <g>
        parent = use_el.getparent()
        if parent is not None:
            idx = list(parent).index(use_el)
            parent.remove(use_el)
            parent.insert(idx, g)
            count += 1

    if count == 0:
        return svg_content, 0

    result = etree.tostring(root, encoding="unicode", xml_declaration=False)
    return result, count
