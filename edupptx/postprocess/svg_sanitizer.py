"""PPT-specific SVG sanitization."""

import re
from lxml import etree

SVG_NS = "http://www.w3.org/2000/svg"

EVENT_ATTRS = re.compile(r"^on[a-z]+$", re.IGNORECASE)


def sanitize_for_ppt(svg_content: str) -> str:
    """Apply PPT-specific fixes to SVG content."""
    # Pre-clean unescaped &
    svg_content = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", svg_content)
    # Pre-clean unescaped < in text content (e.g., "k < 0" in math formulas)
    # Match < that is NOT part of a valid XML tag start (< followed by letter or / or !)
    svg_content = re.sub(r"<(?![a-zA-Z/!?])", "&lt;", svg_content)
    try:
        root = etree.fromstring(svg_content.encode("utf-8"))
    except etree.XMLSyntaxError:
        return svg_content  # Return as-is if unparseable

    _remove_scripts(root)
    _remove_event_handlers(root)
    _replace_emoji(root)
    _flatten_nested_tspans(root)
    _snap_circle_labels(root)
    _ensure_svg_namespace(root)
    _remove_width_height(root)
    _strip_comments(root)

    return etree.tostring(root, encoding="unicode", xml_declaration=False)


_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"  # Misc Symbols, Emoticons, etc.
    "\U00002600-\U000027BF"  # Misc symbols
    "\U0001F600-\U0001F64F"  # Emoticons
    "\U0001F680-\U0001F6FF"  # Transport & Map
    "]+",
)


def _replace_emoji(root: etree._Element) -> None:
    """Strip emoji from text content — they render inconsistently in PPT."""
    for el in root.iter():
        if el.text and _EMOJI_RE.search(el.text):
            el.text = _EMOJI_RE.sub("", el.text).strip()
        if el.tail and _EMOJI_RE.search(el.tail):
            el.tail = _EMOJI_RE.sub("", el.tail).strip()


def _snap_circle_labels(root: etree._Element) -> None:
    """Snap centered label text y to its parent circle cy.

    LLM often places text y ~30px below circle cy, relying on
    dominant-baseline="middle" for visual alignment. This breaks in PPT.
    Fix: find circle+text pairs and set text y = circle cy.
    """
    # Collect circles: (cx, cy, r)
    circles = []
    for c in root.iter(f"{{{SVG_NS}}}circle"):
        try:
            cx = float(c.get("cx", "0"))
            cy = float(c.get("cy", "0"))
            r = float(c.get("r", "0"))
            if 0 < r < 50:  # Small decorative circles only
                circles.append((cx, cy, r))
        except (ValueError, TypeError):
            pass

    if not circles:
        return

    # Find centered label texts and snap to nearest circle
    for t in root.iter(f"{{{SVG_NS}}}text"):
        if t.get("text-anchor") != "middle":
            continue
        if t.get("dominant-baseline") not in ("middle", "central"):
            continue
        text_content = (t.text or "").strip()
        if not text_content or len(text_content) > 3:
            continue  # Only short labels (1, 2, A, B, etc.)

        try:
            tx = float(t.get("x", "0"))
            ty = float(t.get("y", "0"))
        except (ValueError, TypeError):
            continue

        # Find matching circle: same x (within tolerance), closest y
        best_circle = None
        best_dist = float("inf")
        for cx, cy, r in circles:
            if abs(cx - tx) > 5:  # x must match closely
                continue
            dist = abs(cy - ty)
            if dist < best_dist and dist < r * 3:  # Within reasonable range
                best_dist = dist
                best_circle = (cx, cy, r)

        if best_circle and best_dist > 2:  # Only fix if meaningfully off
            _, cy, _ = best_circle
            t.set("y", str(cy))


def _flatten_nested_tspans(root: etree._Element) -> None:
    """Flatten nested <tspan> elements into single-level tspan.

    LLM sometimes generates: <tspan><tspan fill="green">●</tspan> text</tspan>
    PPT converter can't handle nested tspan, so flatten to: <tspan>● text</tspan>
    """
    for tspan in list(root.iter(f"{{{SVG_NS}}}tspan")):
        children = list(tspan)
        if not children:
            continue
        # Check if children are nested tspans
        nested = [c for c in children if etree.QName(c.tag).localname == "tspan"]
        if not nested:
            continue
        # Collect all text content in order
        parts: list[str] = []
        if tspan.text:
            parts.append(tspan.text)
        for child in children:
            if child.text:
                parts.append(child.text)
            if child.tail:
                parts.append(child.tail)
            tspan.remove(child)
        tspan.text = "".join(parts)


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
