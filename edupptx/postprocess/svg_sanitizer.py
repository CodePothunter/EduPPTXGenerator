"""PPT-specific SVG sanitization."""

from __future__ import annotations

import re
from lxml import etree

SVG_NS = "http://www.w3.org/2000/svg"
TSPAN_TAG = f"{{{SVG_NS}}}tspan"

EVENT_ATTRS = re.compile(r"^on[a-z]+$", re.IGNORECASE)
_POSITION_ATTRS = {"x", "y", "dx", "dy", "rotate", "textLength", "lengthAdjust"}


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
        text_content = "".join(t.itertext()).strip()
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
            if abs(cx - tx) > 25:  # x within 25px (covers slight offsets)
                continue
            dist = abs(cy - ty)
            if dist < best_dist and dist < r * 3:  # Within reasonable range
                best_dist = dist
                best_circle = (cx, cy, r)

        if best_circle and best_dist > 2:  # Only fix if meaningfully off
            _, cy, _ = best_circle
            t.set("y", str(cy))
            # Also ensure dominant-baseline is set for PPT converter
            if not t.get("dominant-baseline"):
                t.set("dominant-baseline", "middle")


def _flatten_nested_tspans(root: etree._Element) -> None:
    """Flatten nested <tspan> into sibling runs while preserving inline styling.

    Example:
      <tspan x="100" dy="0">前文<tspan fill="red">重点</tspan>后文</tspan>
    becomes:
      <tspan x="100" dy="0">前文</tspan>
      <tspan fill="red">重点</tspan>
      <tspan>后文</tspan>

    This keeps highlighted inline runs intact instead of collapsing everything
    into a single plain-text tspan.
    """
    while True:
        target = None
        for tspan in root.iter(TSPAN_TAG):
            if any(child.tag == TSPAN_TAG for child in tspan):
                target = tspan
                break
        if target is None:
            break

        parent = target.getparent()
        if parent is None:
            break

        runs = _build_flat_tspan_runs(target)
        preserved_tail = target.tail
        insert_at = parent.index(target)
        parent.remove(target)
        for offset, run in enumerate(runs):
            parent.insert(insert_at + offset, run)
        _restore_replacement_tail(parent, insert_at, runs, preserved_tail)


def _split_tspan_attrs(attrs: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    style_attrs = {k: v for k, v in attrs.items() if k not in _POSITION_ATTRS}
    position_attrs = {k: v for k, v in attrs.items() if k in _POSITION_ATTRS}
    return style_attrs, position_attrs


def _build_flat_tspan_runs(
    tspan: etree._Element,
    inherited_style: dict[str, str] | None = None,
    inherited_position: dict[str, str] | None = None,
) -> list[etree._Element]:
    inherited_style = dict(inherited_style or {})
    inherited_position = dict(inherited_position or {})

    own_style, own_position = _split_tspan_attrs(dict(tspan.attrib))
    merged_style = dict(inherited_style)
    merged_style.update(own_style)
    merged_position = dict(inherited_position)
    merged_position.update(own_position)

    runs: list[etree._Element] = []
    has_emitted_content = False

    if tspan.text:
        runs.append(_make_tspan_run(tspan.text, merged_style, merged_position))
        has_emitted_content = True

    for child in tspan:
        if child.tag != TSPAN_TAG:
            continue

        child_position = merged_position if not has_emitted_content else {}
        child_runs = _build_flat_tspan_runs(child, merged_style, child_position)
        if child_runs:
            runs.extend(child_runs)
            has_emitted_content = True

        if child.tail:
            tail_position = merged_position if not has_emitted_content else {}
            runs.append(_make_tspan_run(child.tail, merged_style, tail_position))
            has_emitted_content = True

    return [run for run in runs if run.text]


def _make_tspan_run(
    text: str,
    style_attrs: dict[str, str],
    position_attrs: dict[str, str] | None = None,
) -> etree._Element:
    run = etree.Element(TSPAN_TAG)
    for key, value in style_attrs.items():
        run.set(key, value)
    for key, value in (position_attrs or {}).items():
        run.set(key, value)
    run.text = text
    return run


def _restore_replacement_tail(
    parent: etree._Element,
    insert_at: int,
    runs: list[etree._Element],
    tail_text: str | None,
) -> None:
    if not tail_text:
        return

    if runs:
        last = runs[-1]
        last.tail = (last.tail or "") + tail_text
        return

    if insert_at > 0:
        prev = parent[insert_at - 1]
        prev.tail = (prev.tail or "") + tail_text
        return

    parent.text = (parent.text or "") + tail_text


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
