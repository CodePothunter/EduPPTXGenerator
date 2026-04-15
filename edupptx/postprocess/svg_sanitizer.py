"""PPT-specific SVG sanitization."""

import re
from lxml import etree

SVG_NS = "http://www.w3.org/2000/svg"

EVENT_ATTRS = re.compile(r"^on[a-z]+$", re.IGNORECASE)


def sanitize_for_ppt(svg_content: str) -> str:
    """Apply PPT-specific fixes to SVG content."""
    # Pre-clean unescaped &
    svg_content = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", svg_content)
    try:
        root = etree.fromstring(svg_content.encode("utf-8"))
    except etree.XMLSyntaxError:
        return svg_content  # Return as-is if unparseable

    _remove_scripts(root)
    _remove_event_handlers(root)
    _ensure_svg_namespace(root)
    _remove_width_height(root)
    _strip_comments(root)

    return etree.tostring(root, encoding="unicode", xml_declaration=False)


def _remove_scripts(root: etree._Element) -> None:
    for script in list(root.iter(f"{{{SVG_NS}}}script")):
        parent = script.getparent()
        if parent is not None:
            parent.remove(script)


def _remove_event_handlers(root: etree._Element) -> None:
    for el in root.iter():
        for attr in list(el.attrib):
            if EVENT_ATTRS.match(attr):
                del el.attrib[attr]


def _ensure_svg_namespace(root: etree._Element) -> None:
    if root.nsmap.get(None) != SVG_NS:
        root.set("xmlns", SVG_NS)


def _remove_width_height(root: etree._Element) -> None:
    for attr in ("width", "height"):
        if attr in root.attrib:
            del root.attrib[attr]


def _strip_comments(root: etree._Element) -> None:
    for comment in root.iter(etree.Comment):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)
