"""Validate and auto-fix LLM-generated SVG for PPT compatibility."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from lxml import etree

if TYPE_CHECKING:
    from edupptx.models import PagePlan

SVG_NS = "http://www.w3.org/2000/svg"
NSMAP = {"svg": SVG_NS}

# Hardened parser: blocks XXE / external DTDs / network fetches.
_SAFE_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)

EXPECTED_VIEWBOX = "0 0 1280 720"
MAX_X = 1280
MAX_Y = 720
CARD_OVERFLOW_TOLERANCE = 6
SHALLOW_CARD_OVERFLOW_TOLERANCE = 12
CONTENT_BOTTOM_LIMIT = 680
TEXT_COLUMN_TOLERANCE = 60
SUBTITLE_MAX_CHARS = 24
MIN_TEXT_WRAP_WIDTH = 96.0
TRANSLATE_RE = re.compile(r"translate\(\s*[-\d.]+(?:[\s,]+[-\d.]+)?\s*\)")
WRAP_BREAK_AFTER_CHARS = set(" ,.;:!?，。！？；：、)]}】》」』")
INLINE_STYLE_ATTRS = (
    "fill",
    "font-weight",
    "font-style",
    "text-decoration",
    "opacity",
    "stroke",
    "stroke-width",
)

SAFE_FONTS = {"Noto Sans SC", "微软雅黑", "Microsoft YaHei", "Arial", "Helvetica", "sans-serif"}
FALLBACK_FONT = "Noto Sans SC, Microsoft YaHei, Arial, sans-serif"

MATH_FONTS = {"Courier New", "Consolas", "monospace"}

# Pattern to detect math-like content in text elements
_MATH_CONTENT_RE = re.compile(
    r'[0-9²³₂₃₄√∑∫±×÷≠≤≥≈∞πΔαβγ=+\-*/^(){}|↑↓→←·]'
)


def _uses_structured_table(page: PagePlan | None) -> bool:
    if page is None:
        return False
    return page.layout_hint == "comparison"


def _uses_timeline_layout(page: PagePlan | None) -> bool:
    if page is None:
        return False
    return page.page_type == "timeline" or page.layout_hint == "timeline"


def _uses_center_hero_layout(page: PagePlan | None) -> bool:
    if page is None:
        return False
    return page.layout_hint == "center_hero" or page.page_type in {"cover", "section"}


def validate_and_fix(svg_content: str, page: PagePlan | None = None) -> tuple[str, list[str]]:
    """Validate SVG, auto-fix issues. Returns (fixed_svg, list_of_warnings)."""
    warnings: list[str] = []

    # Pre-clean XML-unsafe characters (common LLM artifacts)
    svg_content = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", svg_content)
    # Escape bare < not part of XML tags (e.g., "k < 0" in math formulas)
    svg_content = re.sub(r"<(?![a-zA-Z/!?])", "&lt;", svg_content)

    try:
        root = etree.fromstring(svg_content.encode("utf-8"), parser=_SAFE_PARSER)
    except etree.XMLSyntaxError as e:
        warnings = [f"SVG parse error: {e}"]
        # Try recovery: wrap in minimal SVG if missing root
        try:
            wrapped = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">{svg_content}</svg>'
            root = etree.fromstring(wrapped.encode("utf-8"), parser=_SAFE_PARSER)
            warnings.append("Recovered by wrapping in <svg> root")
        except etree.XMLSyntaxError:
            return svg_content, warnings

    _fix_circle_attrs(root, warnings)
    _fix_circle_label_attrs(root, warnings)
    _fix_viewbox(root, warnings)
    _check_ppt_blacklist(root, warnings)
    _remove_foreign_objects(root, warnings)
    _remove_css_animations(root, warnings)
    _fix_fonts(root, warnings)
    if not _uses_center_hero_layout(page):
        _wrap_long_text(root, warnings)
    _clamp_boundaries(root, warnings)
    _fix_image_overflow(root, warnings)
    if not _uses_structured_table(page):
        if not _uses_center_hero_layout(page):
            _fix_text_overlaps(root, warnings)
        _fix_text_outside_cards(root, warnings)
        _normalize_stacked_card_heights(root, warnings)
    if _uses_timeline_layout(page):
        _normalize_timeline_layout(root, page, warnings)
    _check_image_hrefs(root, warnings)
    _warn_layout_issues(root, warnings, page)

    fixed = etree.tostring(root, encoding="unicode", xml_declaration=False)
    return fixed, warnings


def _parse_rgba(value: str) -> tuple[str, str] | None:
    """Parse rgba(r,g,b,a) → ('#RRGGBB', 'a') or None if not rgba."""
    m = re.match(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([0-9.]+)\s*\)", value)
    if not m:
        return None
    r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
    a = m.group(4)
    hex_color = f"#{r:02X}{g:02X}{b:02X}"
    return hex_color, a


def _check_ppt_blacklist(root: etree._Element, warnings: list[str]) -> None:
    """Detect and handle PPT-incompatible SVG features."""

    # --- Auto-remove: SMIL animation elements ---
    smil_tags = ("animate", "animateTransform", "animateMotion", "set")
    for tag_name in smil_tags:
        for el in list(root.iter(f"{{{SVG_NS}}}{tag_name}")):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
                warnings.append(f"移除 SMIL 动画元素 <{tag_name}>（PPT 不支持）")

    # --- Auto-remove: <marker> elements + marker-* attributes ---
    for marker_el in list(root.iter(f"{{{SVG_NS}}}marker")):
        parent = marker_el.getparent()
        if parent is not None:
            parent.remove(marker_el)
            warnings.append("移除 <marker> 元素（PPT 不支持）")

    marker_attrs = ("marker-start", "marker-mid", "marker-end")
    for el in root.iter():
        for attr in marker_attrs:
            if el.get(attr) is not None:
                del el.attrib[attr]
                warnings.append(f"移除 {attr} 属性（PPT 不支持 marker）")

    # --- Auto-fix: rgba() → hex + opacity ---
    for el in root.iter():
        for attr in ("fill", "stroke"):
            val = el.get(attr)
            if val and "rgba(" in val:
                parsed = _parse_rgba(val)
                if parsed:
                    hex_color, alpha = parsed
                    el.set(attr, hex_color)
                    opacity_attr = f"{attr}-opacity"
                    el.set(opacity_attr, alpha)
                    warnings.append(
                        f"转换 {attr}=\"rgba(...)\" → {attr}=\"{hex_color}\" {opacity_attr}=\"{alpha}\""
                    )

    # --- Warning-only checks ---
    # clipPath
    if root.find(f".//{{{SVG_NS}}}clipPath") is not None:
        warnings.append("PPT 对 SVG clipPath 支持有限，可能导致渲染差异")

    # mask
    if root.find(f".//{{{SVG_NS}}}mask") is not None:
        warnings.append("PPT 对 SVG mask 支持有限，可能导致渲染差异")

    # <style> element (not inside <defs> filter — warn about standalone style blocks)
    for style_el in root.iter(f"{{{SVG_NS}}}style"):
        warnings.append("检测到 <style> 块，PPT 可能无法正确解析 CSS 选择器")
        break  # warn once

    # class= attribute on any element
    has_class = False
    for el in root.iter():
        if el.get("class") is not None:
            has_class = True
            break
    if has_class:
        warnings.append("检测到 class= 属性，PPT 不支持 CSS 类选择器")

    # <g> with opacity
    for g in root.iter(f"{{{SVG_NS}}}g"):
        if g.get("opacity") is not None:
            warnings.append("检测到 <g opacity>，应改为逐子元素设置 opacity")
            break  # warn once

    # <image> with opacity
    for img in root.iter(f"{{{SVG_NS}}}image"):
        if img.get("opacity") is not None:
            warnings.append("检测到 <image opacity>，PPT 可能无法正确渲染透明度")
            break  # warn once

    # <textPath>
    if root.find(f".//{{{SVG_NS}}}textPath") is not None:
        warnings.append("检测到 <textPath>，PPT 不支持文字沿路径排列")

    # @font-face in <style> text
    for style_el in root.iter(f"{{{SVG_NS}}}style"):
        if style_el.text and "@font-face" in style_el.text:
            warnings.append("检测到 @font-face，PPT 不支持嵌入式字体声明")
            break

    # <symbol> AND <use> both present
    has_symbol = root.find(f".//{{{SVG_NS}}}symbol") is not None
    has_use = root.find(f".//{{{SVG_NS}}}use") is not None
    if has_symbol and has_use:
        warnings.append("检测到 <symbol>+<use> 组合，PPT 转换可能无法正确解析复杂引用")


def _fix_circle_attrs(root: etree._Element, warnings: list[str]) -> None:
    """Fix <circle> elements using x/y instead of cx/cy (common LLM mistake)."""
    for circle in root.iter(f"{{{SVG_NS}}}circle"):
        # <circle> requires cx/cy, not x/y
        for wrong, right in [("x", "cx"), ("y", "cy")]:
            val = circle.get(wrong)
            if val is not None and circle.get(right) is None:
                circle.set(right, val)
                del circle.attrib[wrong]
                warnings.append(f"Fixed <circle> {wrong}={val} → {right}={val}")


def _fix_circle_label_attrs(root: etree._Element, warnings: list[str]) -> None:
    """Auto-fix circle labels: add dominant-baseline and snap position."""
    circles = []
    for c in root.iter(f"{{{SVG_NS}}}circle"):
        try:
            cx = float(c.get("cx", "0"))
            cy = float(c.get("cy", "0"))
            r = float(c.get("r", "0"))
            if 0 < r < 50:
                circles.append((cx, cy, r))
        except (ValueError, TypeError):
            pass

    if not circles:
        return

    for text_el in root.iter(f"{{{SVG_NS}}}text"):
        if text_el.get("text-anchor") != "middle":
            continue
        content = (text_el.text or "").strip()
        if not content or len(content) > 3:
            continue
        try:
            tx = float(text_el.get("x", "0"))
            ty = float(text_el.get("y", "0"))
        except (ValueError, TypeError):
            continue

        for cx, cy, r in circles:
            if abs(cx - tx) < 25 and abs(cy - ty) < r * 3:
                fixed_something = False
                # Add dominant-baseline if missing
                if text_el.get("dominant-baseline") != "middle":
                    text_el.set("dominant-baseline", "middle")
                    fixed_something = True
                # Snap y to circle cy
                if abs(cy - ty) > 2:
                    text_el.set("y", str(int(cy)))
                    fixed_something = True
                # Snap x to circle cx
                if abs(cx - tx) > 2:
                    text_el.set("x", str(int(cx)))
                    fixed_something = True
                if fixed_something:
                    warnings.append(
                        f"Auto-fixed circle label \"{content}\": "
                        f"snapped to cx={int(cx)},cy={int(cy)} + dominant-baseline"
                    )
                break


def _fix_viewbox(root: etree._Element, warnings: list[str]) -> None:
    vb = root.get("viewBox")
    if vb != EXPECTED_VIEWBOX:
        root.set("viewBox", EXPECTED_VIEWBOX)
        warnings.append(f"viewBox fixed: '{vb}' -> '{EXPECTED_VIEWBOX}'")


def _remove_foreign_objects(root: etree._Element, warnings: list[str]) -> None:
    for fo in root.iter(f"{{{SVG_NS}}}foreignObject"):
        parent = fo.getparent()
        if parent is not None:
            parent.remove(fo)
            warnings.append("Removed <foreignObject> (unsupported in PPT)")


def _remove_css_animations(root: etree._Element, warnings: list[str]) -> None:
    animation_pattern = re.compile(
        r"@keyframes\s+[^{]+\{[^}]*(?:\{[^}]*\}[^}]*)*\}|"
        r"animation\s*:[^;]+;|"
        r"transition\s*:[^;]+;",
        re.DOTALL,
    )
    for style_el in root.iter(f"{{{SVG_NS}}}style"):
        if style_el.text and animation_pattern.search(style_el.text):
            style_el.text = animation_pattern.sub("", style_el.text)
            warnings.append("Removed CSS animations/transitions from <style>")


def _is_math_content(text_el) -> bool:
    """Check if a text element contains math-like content."""
    # Gather all text content including tspan children
    parts = []
    if text_el.text:
        parts.append(text_el.text)
    for child in text_el:
        if child.text:
            parts.append(child.text)
        if child.tail:
            parts.append(child.tail)
    content = "".join(parts).strip()
    if not content:
        return False
    # If more than 20% of characters are math/chemistry-like, consider it formula
    math_chars = len(_MATH_CONTENT_RE.findall(content))
    return math_chars / max(len(content), 1) > 0.2


def _is_font_safe(font_family: str) -> bool:
    fonts = [f.strip().strip("'\"") for f in font_family.split(",")]
    return all(f in SAFE_FONTS for f in fonts if f)


def _fix_fonts(root: etree._Element, warnings: list[str]) -> None:
    for el in root.iter():
        ff = el.get("font-family")
        if not ff:
            continue
        if not _is_font_safe(ff):
            # Check if this is a math font on math content
            fonts = [f.strip().strip("'\"") for f in ff.split(",")]
            has_math_font = any(f in MATH_FONTS for f in fonts)
            if has_math_font and el.tag.endswith("}text") and _is_math_content(el):
                # Keep math font but add safe fallback chain
                el.set("font-family", f"Courier New, Consolas, {FALLBACK_FONT}")
                # Don't warn — this is intentional math content
                continue
            el.set("font-family", FALLBACK_FONT)
            warnings.append(f"Replaced unsafe font '{ff}'")
        elif "Noto Sans SC" not in ff:
            # Ensure Noto Sans SC is first for cross-platform rendering
            el.set("font-family", f"Noto Sans SC, {ff}")


def _normalize_inline_text(payload: str | None) -> str | None:
    if not payload:
        return None
    if payload.strip() == "":
        if any(ch in payload for ch in "\n\r\t"):
            return None
        return " "
    return re.sub(r"\s+", " ", payload)


def _collect_inline_runs(
    node: etree._Element,
    inherited_attrs: dict[str, str] | None = None,
) -> list[tuple[str, dict[str, str]]]:
    inherited = dict(inherited_attrs or {})
    runs: list[tuple[str, dict[str, str]]] = []

    head = _normalize_inline_text(node.text)
    if head:
        runs.append((head, dict(inherited)))

    for child in node:
        if not _is_real_element(child) or etree.QName(child.tag).localname != "tspan":
            continue
        child_attrs = dict(inherited)
        for attr in INLINE_STYLE_ATTRS:
            val = child.get(attr)
            if val is not None:
                child_attrs[attr] = val
        runs.extend(_collect_inline_runs(child, child_attrs))
        tail = _normalize_inline_text(child.tail)
        if tail:
            runs.append((tail, dict(inherited)))

    return runs


def _has_explicit_multiline_tspans(text_el: etree._Element) -> bool:
    fs = _text_font_size(text_el)
    tspans = [
        child
        for child in text_el
        if _is_real_element(child) and etree.QName(child.tag).localname == "tspan"
    ]
    if not tspans:
        return False

    for idx, tspan in enumerate(tspans):
        dy = _parse_dy_value(tspan.get("dy"), fs)
        if idx == 0:
            if abs(dy) > 0.1:
                return True
            continue
        if tspan.get("x") is not None:
            return True
        if abs(dy) > 0.1:
            return True
    return False


def _expand_runs_to_chars(
    runs: list[tuple[str, dict[str, str]]],
) -> list[tuple[str, dict[str, str]]]:
    chars: list[tuple[str, dict[str, str]]] = []
    for text, attrs in runs:
        for ch in text:
            chars.append((ch, attrs))
    return chars


def _merge_char_runs(
    chars: list[tuple[str, dict[str, str]]],
) -> list[tuple[str, dict[str, str]]]:
    if not chars:
        return []

    merged: list[tuple[str, dict[str, str]]] = []
    current_text: list[str] = []
    current_attrs = dict(chars[0][1])

    for ch, attrs in chars:
        if attrs != current_attrs and current_text:
            merged.append(("".join(current_text), dict(current_attrs)))
            current_text = [ch]
            current_attrs = dict(attrs)
            continue
        current_text.append(ch)

    if current_text:
        merged.append(("".join(current_text), dict(current_attrs)))
    return merged


def _truncate_inline_runs(
    runs: list[tuple[str, dict[str, str]]],
    max_chars: int,
) -> list[tuple[str, dict[str, str]]]:
    remaining = max_chars
    truncated: list[tuple[str, dict[str, str]]] = []

    for text, attrs in runs:
        if remaining <= 0:
            break
        piece = text[:remaining]
        if piece:
            truncated.append((piece, dict(attrs)))
            remaining -= len(piece)

    original_len = sum(len(text) for text, _ in runs)
    if original_len > max_chars:
        if truncated:
            last_text, last_attrs = truncated[-1]
            truncated[-1] = (last_text.rstrip(), last_attrs)
        truncated.append(("...", {}))
    return truncated


def _estimate_char_width(ch: str, font_size: float) -> float:
    if not ch:
        return 0.0
    if ch.isspace():
        return font_size * 0.33
    if ord(ch) < 128:
        if ch.isalnum():
            return font_size * 0.58
        return font_size * 0.42
    if ch in WRAP_BREAK_AFTER_CHARS:
        return font_size * 0.55
    return font_size * 0.96


def _find_text_container_rect(
    root: etree._Element,
    tx: float,
    ty: float,
    text_bottom: float,
) -> etree._Element | None:
    candidates: list[tuple[etree._Element, tuple[float, float, float, float]]] = []
    for rect in root.iter(f"{{{SVG_NS}}}rect"):
        if _element_in_defs(rect) or _has_translated_group_ancestor(rect):
            continue
        box = _rect_box(rect)
        if box is None:
            continue
        _, y, width, height = box
        if width < 100 or height < 36:
            continue
        if height <= 8 and y <= 2:
            continue
        if _text_starts_in_card(tx, ty, box, edge_slack=6, vertical_slack=80) and text_bottom >= y - 4:
            candidates.append((rect, box))

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda item: (
            item[1][2] * item[1][3],
            abs((item[1][0] + item[1][2] / 2.0) - tx),
            abs((item[1][1] + item[1][3] / 2.0) - ((ty + text_bottom) / 2.0)),
        ),
    )[0]


def _estimate_wrap_width(
    root: etree._Element,
    text_el: etree._Element,
    tx: float,
    ty: float,
    text_bottom: float,
) -> float:
    container = _find_text_container_rect(root, tx, ty, text_bottom)
    if container is not None:
        box = _rect_box(container)
        if box is not None:
            rx, _, rw, _ = box
            left_padding = max(12.0, tx - rx)
            return max(MIN_TEXT_WRAP_WIDTH, rw - left_padding - 18.0)

    if text_el.get("text-anchor") == "middle":
        half_width = min(max(0.0, tx - 50.0), max(0.0, MAX_X - tx - 50.0))
        return max(MIN_TEXT_WRAP_WIDTH, half_width * 2.0)

    return max(MIN_TEXT_WRAP_WIDTH, MAX_X - tx - 50.0)


def _wrap_char_runs_to_width(
    chars: list[tuple[str, dict[str, str]]],
    max_width: float,
    font_size: float,
) -> list[list[tuple[str, dict[str, str]]]]:
    if not chars:
        return []

    lines: list[list[tuple[str, dict[str, str]]]] = []
    start = 0
    total = len(chars)

    while start < total:
        width = 0.0
        last_break = None
        end = start

        while end < total:
            ch = chars[end][0]
            next_width = width + _estimate_char_width(ch, font_size)
            if next_width > max_width and end > start:
                break
            width = next_width
            if ch.isspace() or ch in WRAP_BREAK_AFTER_CHARS:
                last_break = end + 1
            end += 1

        if end < total and last_break and last_break > start:
            end = last_break

        while end > start and chars[end - 1][0].isspace():
            end -= 1
        if end <= start:
            end = start + 1

        lines.append(_merge_char_runs(chars[start:end]))
        start = end
        while start < total and chars[start][0].isspace():
            start += 1

    return [line for line in lines if line]


def _rewrite_text_with_lines(
    text_el: etree._Element,
    x: str,
    lines: list[list[tuple[str, dict[str, str]]]],
    line_height: int,
) -> None:
    for child in list(text_el):
        text_el.remove(child)
    text_el.text = None

    for line_idx, line_runs in enumerate(lines):
        for run_idx, (text, attrs) in enumerate(line_runs):
            if not text:
                continue
            tspan = etree.SubElement(text_el, f"{{{SVG_NS}}}tspan")
            if run_idx == 0:
                tspan.set("x", x)
                tspan.set("dy", str(line_height) if line_idx > 0 else "0")
            for attr, value in attrs.items():
                tspan.set(attr, value)
            tspan.text = text


def _is_page_subtitle_text(root: etree._Element, text_el: etree._Element) -> bool:
    try:
        tx = float(text_el.get("x", "0"))
        ty = float(text_el.get("y", "0"))
    except (ValueError, TypeError):
        return False

    fs = _text_font_size(text_el)
    if ty < 68 or ty > 96 or fs < 16 or fs > 26:
        return False

    text_bottom = _get_text_bottom_y(text_el)
    return _find_parent_card(root, text_el, tx, ty, text_bottom) is None


def _wrap_long_text(root: etree._Element, warnings: list[str]) -> None:
    """Wrap long <text> content into <tspan> lines to prevent overflow."""
    for text_el in list(root.iter(f"{{{SVG_NS}}}text")):
        if _is_footer_page_number_text(text_el):
            continue
        if _is_centered_short_label(text_el):
            continue
        if _is_centered_page_banner(text_el):
            continue
        if _has_translated_group_ancestor(text_el):
            continue
        if text_el.get("data-latex") is not None or _is_math_content(text_el):
            continue

        runs = _collect_inline_runs(text_el)
        content = "".join(text for text, _ in runs).strip()
        if not content:
            continue

        x = text_el.get("x", "0")
        try:
            tx = float(x)
            ty = float(text_el.get("y", "0"))
        except (ValueError, TypeError):
            continue

        fs = _text_font_size(text_el)
        line_height = int(fs * 1.4)

        if _is_page_subtitle_text(root, text_el):
            compact = re.sub(r"\s+", "", content)
            if len(compact) > SUBTITLE_MAX_CHARS:
                truncated_runs = _truncate_inline_runs(runs, SUBTITLE_MAX_CHARS)
                _rewrite_text_with_lines(text_el, x, [truncated_runs], line_height)
                warnings.append(
                    f"Trimmed subtitle from {len(compact)} to {SUBTITLE_MAX_CHARS} chars to keep a single line"
                )
            continue

        if _has_explicit_multiline_tspans(text_el):
            continue

        text_bottom = _get_text_bottom_y(text_el)
        max_width = _estimate_wrap_width(root, text_el, tx, ty, text_bottom)
        chars = _expand_runs_to_chars(runs)
        estimated_width = sum(_estimate_char_width(ch, fs) for ch, _ in chars)
        if estimated_width <= max_width:
            continue

        lines = _wrap_char_runs_to_width(chars, max_width, fs)
        if len(lines) <= 1:
            continue

        _rewrite_text_with_lines(text_el, x, lines, line_height)
        warnings.append(
            f"Wrapped long text ({len(content)} chars) into {len(lines)} lines for {int(round(max_width))}px"
        )


def _clamp_value(val_str: str, lo: float, hi: float) -> tuple[str, bool]:
    try:
        val = float(val_str)
    except (ValueError, TypeError):
        return val_str, False
    if val < lo:
        return str(lo), True
    if val > hi:
        return str(hi), True
    return val_str, False


def _clamp_boundaries(root: etree._Element, warnings: list[str]) -> None:
    # Minimum x=50 (matches design-base.md spec: all elements x ≥ 50)
    MIN_X = 50
    for tag in (f"{{{SVG_NS}}}text", f"{{{SVG_NS}}}rect", f"{{{SVG_NS}}}image"):
        for el in root.iter(tag):
            if _has_translated_group_ancestor(el):
                continue
            # Allow full-width top decoration bar (x=0, y=0, height≤8)
            if tag.endswith("rect"):
                box = _rect_box(el)
                if box is not None:
                    _, y, _, h = box
                    if h <= 8 and y <= 2:
                        continue  # Skip decoration bar
                    if _is_full_slide_background_box(box):
                        continue
            for attr, lo, hi in [("x", MIN_X, MAX_X), ("y", 0, MAX_Y)]:
                val = el.get(attr)
                if val is not None:
                    new_val, changed = _clamp_value(val, lo, hi)
                    if changed:
                        el.set(attr, new_val)
                        tag_name = etree.QName(el.tag).localname
                        warnings.append(
                            f"Clamped <{tag_name}> {attr}={val} to {new_val}"
                        )

    # Hard bottom-edge cap: rect/image with y + height > MAX_Y get height clipped
    # so they sit fully inside the canvas. This is a deterministic safety net for
    # the high-frequency LLM mistake of placing footer cards / images at y=670+
    # with height that extends past 720.
    for tag in (f"{{{SVG_NS}}}rect", f"{{{SVG_NS}}}image"):
        for el in root.iter(tag):
            if _has_translated_group_ancestor(el):
                continue
            box = _rect_box(el)
            if box is None:
                continue
            x, y, w, h = box
            if h <= 8 and y <= 2:
                continue  # decoration bar
            if _is_full_slide_background_box(box):
                continue
            bottom = y + h
            if bottom > MAX_Y:
                # Clip height so y + height == MAX_Y - 4 (4px safety inset)
                new_h = max(8.0, MAX_Y - 4 - y)
                if new_h < h:
                    el.set("height", str(int(new_h)))
                    tag_name = etree.QName(el.tag).localname
                    warnings.append(
                        f"Clipped <{tag_name}> height {int(h)}→{int(new_h)} "
                        f"(was extending to y={int(bottom)} > {MAX_Y})"
                    )

    # Circle: clamp cy + r ≤ MAX_Y (skip decorative low-opacity blobs that
    # intentionally bleed off-canvas)
    for el in root.iter(f"{{{SVG_NS}}}circle"):
        if _has_translated_group_ancestor(el):
            continue
        opacity_str = el.get("opacity") or el.get("fill-opacity")
        try:
            opacity = float(opacity_str) if opacity_str else 1.0
        except (ValueError, TypeError):
            opacity = 1.0
        if opacity <= 0.25:
            continue  # decorative atmospheric blob
        cy_str = el.get("cy")
        r_str = el.get("r")
        if cy_str is None or r_str is None:
            continue
        try:
            cy = float(cy_str)
            r = float(r_str)
        except (ValueError, TypeError):
            continue
        if cy + r > MAX_Y:
            new_cy = MAX_Y - r - 2
            if new_cy < r:  # would push center off the top
                continue
            el.set("cy", str(int(new_cy)))
            warnings.append(
                f"Clamped <circle> cy {int(cy)}→{int(new_cy)} (cy+r was > {MAX_Y})"
            )

    # Text: clamp y so that the alphabetic-baseline text bbox stays in canvas
    for el in root.iter(f"{{{SVG_NS}}}text"):
        if _has_translated_group_ancestor(el):
            continue
        y_str = el.get("y")
        fs_str = el.get("font-size", "16")
        if y_str is None:
            continue
        try:
            y = float(y_str)
            fs = float(str(fs_str).replace("px", ""))
        except (ValueError, TypeError):
            continue
        # Compensate for text drop below baseline (~0.25 * font_size)
        max_y = MAX_Y - fs * 0.3
        if y > max_y:
            new_y = max_y
            el.set("y", str(int(new_y)))
            warnings.append(
                f"Clamped <text> y {int(y)}→{int(new_y)} (text bottom > {MAX_Y})"
            )


def _fix_text_overlaps(root: etree._Element, warnings: list[str]) -> None:
    """Detect and fix overlapping <text> elements in the same horizontal column."""
    texts = list(root.iter(f"{{{SVG_NS}}}text"))
    if len(texts) < 2:
        return

    # Collect (element, x, y, font_size, parent_card_id) for text elements
    text_info = []
    for t in texts:
        if _is_footer_page_number_text(t):
            continue
        if _is_centered_short_label(t):
            continue
        if _is_centered_page_banner(t):
            continue
        if _is_page_subtitle_text(root, t):
            continue
        if _has_translated_group_ancestor(t):
            continue
        y_str = t.get("y")
        x_str = t.get("x", "0")
        if not y_str:
            continue
        try:
            y = float(y_str)
            x = float(x_str)
        except (ValueError, TypeError):
            continue
        fs_str = t.get("font-size", "16")
        try:
            fs = float(fs_str.replace("px", ""))
        except (ValueError, TypeError):
            fs = 16.0
        text_bottom = _get_text_bottom_y(t)
        parent_rect = _find_parent_card(root, t, x, y, text_bottom)
        parent_key = id(parent_rect) if parent_rect is not None else None
        text_info.append((t, x, y, fs, parent_key))

    if len(text_info) < 2:
        return

    # Group by horizontal column (texts within 100px x-range are in the same column)
    text_info.sort(key=lambda t: (t[4] is None, t[4] or -1, t[1], t[2]))  # sort by card, then x/y
    columns: list[list] = []
    for item in text_info:
        placed = False
        for col in columns:
            same_parent = col[0][4] == item[4]
            if same_parent and abs(col[0][1] - item[1]) < TEXT_COLUMN_TOLERANCE:
                col.append(item)
                placed = True
                break
        if not placed:
            columns.append([item])

    # Fix overlaps within each column
    for col in columns:
        col.sort(key=lambda t: t[2])  # sort by y within column
        for i in range(1, len(col)):
            prev_el, _, prev_y, prev_fs, _ = col[i - 1]
            el, x, curr_y, fs, parent_key = col[i]
            # Use actual bottom of previous text (including tspan dy offsets)
            prev_bottom = _get_text_bottom_y(prev_el)
            min_next_y = prev_bottom + 6  # 6px gap after actual text bottom
            # Fallback: at least prev_y + prev_fs + 6
            min_gap_y = prev_y + prev_fs + 6
            effective_min = max(min_next_y, min_gap_y)
            if curr_y < effective_min:
                new_y = effective_min
                el.set("y", str(int(new_y)))
                col[i] = (el, x, new_y, fs, parent_key)
                warnings.append(f"Fixed text overlap: pushed y from {curr_y} to {new_y}")


def _get_text_bottom_y(text_el: etree._Element) -> float:
    """Calculate the actual bottom y of a <text> element including all tspan dy offsets."""
    y_str = text_el.get("y", "0")
    try:
        y = float(y_str)
    except (ValueError, TypeError):
        return 0.0
    fs_str = text_el.get("font-size", "16")
    try:
        fs = float(fs_str.replace("px", ""))
    except (ValueError, TypeError):
        fs = 16.0

    # Accumulate dy from child tspans
    curr_y = y
    for tspan in text_el.iter(f"{{{SVG_NS}}}tspan"):
        dy_str = tspan.get("dy", "0")
        try:
            if "em" in dy_str:
                dy_val = float(dy_str.replace("em", "")) * fs
            else:
                dy_val = float(dy_str)
            curr_y += dy_val
        except (ValueError, TypeError):
            pass
    return curr_y + fs * 0.82  # Approximate baseline-to-bottom for PPT-like text metrics


def _rect_box(rect: etree._Element) -> tuple[float, float, float, float] | None:
    try:
        x = float(rect.get("x", "0"))
        y = float(rect.get("y", "0"))
        width = float(rect.get("width", "0"))
        height = float(rect.get("height", "0"))
    except (ValueError, TypeError):
        return None
    return x, y, width, height


def _is_meaningful_card_rect(rect: etree._Element) -> bool:
    box = _rect_box(rect)
    if box is None:
        return False
    if _is_full_slide_background_box(box):
        return False
    _, y, width, height = box
    if _is_shallow_footer_like_card(box):
        return True
    if width < 100 or height < 50:
        return False
    if height <= 8 and y <= 2:
        return False
    return True


def _is_real_element(el: etree._Element) -> bool:
    return isinstance(getattr(el, "tag", None), str)


def _is_full_slide_background_box(box: tuple[float, float, float, float]) -> bool:
    x, y, width, height = box
    return x <= 2 and y <= 2 and width >= MAX_X - 2 and height >= MAX_Y - 20


def _text_content(text_el: etree._Element) -> str:
    return "".join(text_el.itertext()).strip()


def _text_font_size(text_el: etree._Element) -> float:
    fs_str = text_el.get("font-size", "16")
    try:
        return float(fs_str.replace("px", ""))
    except (ValueError, TypeError):
        return 16.0


def _parse_dy_value(value: str | None, font_size: float) -> float:
    if value is None:
        return 0.0
    try:
        if "em" in value:
            return float(value.replace("em", "")) * font_size
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _is_centered_short_label(text_el: etree._Element) -> bool:
    if text_el.get("text-anchor") != "middle":
        return False
    compact = re.sub(r"\s+", "", _text_content(text_el))
    return bool(compact) and len(compact) <= 3


def _is_centered_page_banner(text_el: etree._Element) -> bool:
    if text_el.get("text-anchor") != "middle":
        return False
    compact = re.sub(r"\s+", "", _text_content(text_el))
    if len(compact) <= 3:
        return False
    try:
        x = float(text_el.get("x", "0"))
        y = float(text_el.get("y", "0"))
    except (ValueError, TypeError):
        return False
    return 420 <= x <= 860 and y >= 560


def _is_footer_page_number_text(text_el: etree._Element) -> bool:
    try:
        x = float(text_el.get("x", "0"))
        y = float(text_el.get("y", "0"))
    except (ValueError, TypeError):
        return False

    fs_str = text_el.get("font-size", "16")
    try:
        fs = float(fs_str.replace("px", ""))
    except (ValueError, TypeError):
        fs = 16.0

    if y < 680 or x < 1180:
        return False
    if fs > 18:
        return False
    if text_el.get("text-anchor") != "end":
        return False

    compact = re.sub(r"\s+", "", _text_content(text_el))
    return bool(re.fullmatch(r"\d+(?:/\d+)?", compact))


def _element_in_defs(el: etree._Element) -> bool:
    if not _is_real_element(el):
        return False
    defs_tag = f"{{{SVG_NS}}}defs"
    return any(_is_real_element(ancestor) and ancestor.tag == defs_tag for ancestor in el.iterancestors())


def _collect_card_rects(root: etree._Element) -> list[etree._Element]:
    rects = [
        rect
        for rect in root.iter(f"{{{SVG_NS}}}rect")
        if not _element_in_defs(rect)
        and not _has_translated_group_ancestor(rect)
        and _is_meaningful_card_rect(rect)
    ]
    if len(rects) < 2:
        return rects

    boxes = {id(rect): _rect_box(rect) for rect in rects}
    cards: list[etree._Element] = []
    for rect in rects:
        box = boxes[id(rect)]
        if box is None:
            continue
        if _is_shallow_footer_like_card(box):
            cards.append(rect)
            continue
        x, y, width, height = box
        right = x + width
        bottom = y + height
        nested = False
        for other in rects:
            if other is rect:
                continue
            other_box = boxes[id(other)]
            if other_box is None:
                continue
            ox, oy, ow, oh = other_box
            other_right = ox + ow
            other_bottom = oy + oh
            if ow * oh <= width * height:
                continue
            inset = 12
            if (
                x >= ox + inset
                and y >= oy + inset
                and right <= other_right - inset
                and bottom <= other_bottom - inset
            ):
                nested = True
                break
        if not nested:
            cards.append(rect)
    return cards


def _horizontal_overlap(box_a: tuple[float, float, float, float], box_b: tuple[float, float, float, float]) -> float:
    ax, _, aw, _ = box_a
    bx, _, bw, _ = box_b
    return min(ax + aw, bx + bw) - max(ax, bx)


def _is_shallow_footer_like_card(box: tuple[float, float, float, float]) -> bool:
    x, y, width, height = box
    if height > 110:
        return False
    if width < 500:
        return False
    if height <= 0 or width / height < 4.0:
        return False
    return y + height >= 560


def _shift_text_up_within_card(
    root: etree._Element,
    text_el: etree._Element,
    box: tuple[float, float, float, float],
    overflow: float,
) -> float:
    try:
        ty = float(text_el.get("y", "0"))
    except (ValueError, TypeError):
        return 0.0

    _, ry, _, _ = box
    target_top = ry + 18
    available_shift = max(0.0, ty - target_top)
    desired_shift = max(0.0, overflow + 4.0)
    applied_shift = min(available_shift, desired_shift)
    if applied_shift <= 0:
        return 0.0

    _shift_element_vertically(root, text_el, -applied_shift)
    return applied_shift


def _element_anchor(el: etree._Element) -> tuple[float, float] | None:
    if not _is_real_element(el):
        return None
    tag_name = etree.QName(el.tag).localname

    if tag_name in {"text", "rect", "image", "use"}:
        try:
            return float(el.get("x", "0")), float(el.get("y", "0"))
        except (ValueError, TypeError):
            return None

    if tag_name in {"circle", "ellipse"}:
        try:
            return float(el.get("cx", "0")), float(el.get("cy", "0"))
        except (ValueError, TypeError):
            return None

    if tag_name == "line":
        try:
            return float(el.get("x1", "0")), float(el.get("y1", "0"))
        except (ValueError, TypeError):
            return None

    if tag_name == "g":
        transform = el.get("transform", "")
        m = re.search(r"translate\(\s*([-\d.]+)(?:[\s,]+([-\d.]+))?\s*\)", transform)
        if m:
            try:
                tx = float(m.group(1))
                ty = float(m.group(2) or "0")
                return tx, ty
            except (ValueError, TypeError):
                return None
        for child in el:
            anchor = _element_anchor(child)
            if anchor is not None:
                return anchor
    return None


def _shift_numeric_attr(el: etree._Element, attr: str, dy: float) -> bool:
    val = el.get(attr)
    if val is None:
        return False
    try:
        new_val = float(val) + dy
    except (ValueError, TypeError):
        return False
    el.set(attr, str(int(new_val) if new_val.is_integer() else new_val))
    return True


def _format_numeric(val: float) -> str:
    return str(int(val)) if float(val).is_integer() else str(val)


def _shift_transform_translate(el: etree._Element, dx: float = 0, dy: float = 0) -> bool:
    transform = el.get("transform")
    if not transform or "translate" not in transform:
        return False

    def _replace(match: re.Match[str]) -> str:
        tx = float(match.group(1)) + dx
        ty = float(match.group(2) or "0") + dy
        tx_str = _format_numeric(tx)
        ty_str = _format_numeric(ty)
        return f"translate({tx_str},{ty_str})"

    new_transform, count = re.subn(
        r"translate\(\s*([-\d.]+)(?:[\s,]+([-\d.]+))?\s*\)",
        _replace,
        transform,
        count=1,
    )
    if count:
        el.set("transform", new_transform)
        return True
    return False


def _shift_element_vertically(
    root: etree._Element,
    el: etree._Element,
    dy: float,
    shifted_ids: set[int] | None = None,
) -> None:
    if not _is_real_element(el):
        return
    if shifted_ids is not None and id(el) in shifted_ids:
        return

    tag_name = etree.QName(el.tag).localname
    shifted = False

    if tag_name in {"text", "rect", "image", "use"}:
        shifted = _shift_numeric_attr(el, "y", dy)
    elif tag_name in {"circle", "ellipse"}:
        shifted = _shift_numeric_attr(el, "cy", dy)
    elif tag_name == "line":
        shifted = _shift_numeric_attr(el, "y1", dy) | _shift_numeric_attr(el, "y2", dy)
    elif tag_name == "g":
        shifted = _shift_transform_translate(el, dy=dy)
        if not shifted:
            for child in el:
                _shift_element_vertically(root, child, dy, shifted_ids)

    if tag_name == "image":
        clip_path = el.get("clip-path", "")
        m = re.fullmatch(r"url\(#([^)]+)\)", clip_path.strip())
        if m:
            clip_id = m.group(1)
            clip_path_el = root.find(f".//{{{SVG_NS}}}clipPath[@id='{clip_id}']")
            if clip_path_el is not None:
                for child in clip_path_el:
                    _shift_element_vertically(root, child, dy, shifted_ids)

    if shifted_ids is not None:
        shifted_ids.add(id(el))


def _shift_element_horizontally(
    root: etree._Element,
    el: etree._Element,
    dx: float,
    shifted_ids: set[int] | None = None,
) -> None:
    if not _is_real_element(el):
        return
    if shifted_ids is not None and id(el) in shifted_ids:
        return

    tag_name = etree.QName(el.tag).localname
    shifted = False

    if tag_name in {"text", "rect", "image", "use"}:
        shifted = _shift_numeric_attr(el, "x", dx)
    elif tag_name in {"circle", "ellipse"}:
        shifted = _shift_numeric_attr(el, "cx", dx)
    elif tag_name == "line":
        shifted = _shift_numeric_attr(el, "x1", dx) | _shift_numeric_attr(el, "x2", dx)
    elif tag_name == "g":
        shifted = _shift_transform_translate(el, dx=dx)
        if not shifted:
            for child in el:
                _shift_element_horizontally(root, child, dx, shifted_ids)

    if tag_name == "image":
        clip_path = el.get("clip-path", "")
        m = re.fullmatch(r"url\(#([^)]+)\)", clip_path.strip())
        if m:
            clip_id = m.group(1)
            clip_path_el = root.find(f".//{{{SVG_NS}}}clipPath[@id='{clip_id}']")
            if clip_path_el is not None:
                for child in clip_path_el:
                    _shift_element_horizontally(root, child, dx, shifted_ids)

    if shifted_ids is not None:
        shifted_ids.add(id(el))


def _element_belongs_to_card(
    el: etree._Element,
    card_box: tuple[float, float, float, float],
) -> bool:
    anchor = _element_anchor(el)
    if anchor is None:
        return False
    x, y = anchor
    card_x, card_y, card_w, card_h = card_box
    return (
        card_x - 16 <= x <= card_x + card_w + 16
        and card_y - 16 <= y <= card_y + card_h + 24
    )


def _iter_group_ancestors(el: etree._Element):
    group_tag = f"{{{SVG_NS}}}g"
    for ancestor in el.iterancestors(group_tag):
        if _is_real_element(ancestor):
            yield ancestor


def _has_translated_group_ancestor(el: etree._Element) -> bool:
    return any(TRANSLATE_RE.search(ancestor.get("transform", "")) for ancestor in _iter_group_ancestors(el))


def _text_matches_card_box(
    tx: float,
    ty: float,
    text_bottom: float,
    card_box: tuple[float, float, float, float],
    *,
    edge_slack: float = 8,
    overflow_slack: float = 28,
) -> bool:
    rx, ry, rw, rh = card_box
    card_bottom = ry + rh
    return (
        rx - edge_slack <= tx <= rx + rw + edge_slack
        and ry - 4 <= ty <= card_bottom + overflow_slack
        and text_bottom >= ry - 4
        and text_bottom <= card_bottom + overflow_slack
    )


def _text_starts_in_card(
    tx: float,
    ty: float,
    card_box: tuple[float, float, float, float],
    *,
    edge_slack: float = 8,
    vertical_slack: float = 64,
) -> bool:
    rx, ry, rw, rh = card_box
    return (
        rx - edge_slack <= tx <= rx + rw + edge_slack
        and ry - 4 <= ty <= ry + rh + vertical_slack
    )


def _allows_relaxed_card_match(text_el: etree._Element) -> bool:
    return not _is_centered_page_banner(text_el)


def _pick_best_card(
    candidates: list[tuple[etree._Element, tuple[float, float, float, float]]],
    tx: float,
    ty: float,
    text_bottom: float,
) -> etree._Element | None:
    if not candidates:
        return None

    def _score(item: tuple[etree._Element, tuple[float, float, float, float]]):
        _, (rx, ry, rw, rh) = item
        card_bottom = ry + rh
        overflow = max(0.0, text_bottom - card_bottom)
        center_y = (ty + text_bottom) / 2.0
        return (
            0 if _is_shallow_footer_like_card((rx, ry, rw, rh)) else 1,
            rw * rh,
            overflow,
            abs((ry + rh / 2.0) - center_y),
            abs((rx + rw / 2.0) - tx),
        )

    return min(candidates, key=_score)[0]


def _find_parent_card_in_groups(
    text_el: etree._Element,
    tx: float,
    ty: float,
    text_bottom: float,
) -> etree._Element | None:
    for group in _iter_group_ancestors(text_el):
        group_cards: list[tuple[etree._Element, tuple[float, float, float, float]]] = []
        for rect in _collect_card_rects(group):
            box = _rect_box(rect)
            if box is not None:
                group_cards.append((rect, box))

        if not group_cards:
            continue

        if len(group_cards) == 1:
            rect, box = group_cards[0]
            if _text_matches_card_box(
                tx,
                ty,
                text_bottom,
                box,
                overflow_slack=48 if _is_shallow_footer_like_card(box) else 28,
            ):
                return rect
            if _allows_relaxed_card_match(text_el) and _text_starts_in_card(tx, ty, box):
                return rect
            continue

        matched = [
            item for item in group_cards
            if _text_matches_card_box(
                tx,
                ty,
                text_bottom,
                item[1],
                overflow_slack=48 if _is_shallow_footer_like_card(item[1]) else 28,
            )
        ]
        best = _pick_best_card(matched, tx, ty, text_bottom)
        if best is not None:
            return best
    return None


def _find_card_group(rect: etree._Element) -> etree._Element | None:
    for group in _iter_group_ancestors(rect):
        group_cards = _collect_card_rects(group)
        if len(group_cards) == 1 and group_cards[0] is rect:
            return group
    return None


def _timeline_stage_circle(group: etree._Element) -> tuple[etree._Element, float, float, float] | None:
    best: tuple[etree._Element, float, float, float] | None = None
    for circle in group.iter(f"{{{SVG_NS}}}circle"):
        try:
            cx = float(circle.get("cx", "0"))
            cy = float(circle.get("cy", "0"))
            r = float(circle.get("r", "0"))
        except (ValueError, TypeError):
            continue
        if not (10 <= r <= 40):
            continue
        if best is None or r > best[3]:
            best = (circle, cx, cy, r)
    return best


def _timeline_stage_groups(root: etree._Element) -> list[dict[str, object]]:
    stage_groups: list[dict[str, object]] = []
    seen_groups: set[int] = set()

    for group in root.iter(f"{{{SVG_NS}}}g"):
        if id(group) in seen_groups or _element_in_defs(group):
            continue

        circle_info = _timeline_stage_circle(group)
        if circle_info is None:
            continue

        circle, cx, cy, _ = circle_info
        circle_count = sum(1 for _ in group.iter(f"{{{SVG_NS}}}circle"))
        if circle_count > 3:
            continue

        top_rects: list[tuple[etree._Element, tuple[float, float, float, float]]] = []
        bottom_rects: list[tuple[etree._Element, tuple[float, float, float, float]]] = []
        for rect in group.iter(f"{{{SVG_NS}}}rect"):
            box = _rect_box(rect)
            if box is None:
                continue
            rx, ry, rw, rh = box
            center_y = ry + rh / 2.0
            if rw >= 80 and rh >= 80 and center_y < cy - 20:
                top_rects.append((rect, box))
            elif rw >= 120 and rh >= 80 and center_y > cy + 20:
                bottom_rects.append((rect, box))

        if not top_rects or not bottom_rects:
            continue

        image_el = None
        for img in group.iter(f"{{{SVG_NS}}}image"):
            image_el = img
            break

        top_rect = min(top_rects, key=lambda item: abs((item[1][0] + item[1][2] / 2.0) - cx))[0]
        bottom_rect = min(bottom_rects, key=lambda item: abs((item[1][0] + item[1][2] / 2.0) - cx))[0]

        stage_groups.append(
            {
                "group": group,
                "circle": circle,
                "cx": cx,
                "cy": cy,
                "top_rect": top_rect,
                "bottom_rect": bottom_rect,
                "image": image_el,
            }
        )
        seen_groups.add(id(group))

    stage_groups.sort(key=lambda item: float(item["cx"]))
    return stage_groups


def _normalize_timeline_line(
    root: etree._Element,
    target_centers: list[float],
    line_y: float,
) -> None:
    best_line: etree._Element | None = None
    best_span = 0.0
    for line in root.iter(f"{{{SVG_NS}}}line"):
        try:
            x1 = float(line.get("x1", "0"))
            x2 = float(line.get("x2", "0"))
            y1 = float(line.get("y1", "0"))
            y2 = float(line.get("y2", "0"))
        except (ValueError, TypeError):
            continue
        if abs(y1 - y2) > 2 or abs(y1 - line_y) > 30:
            continue
        span = abs(x2 - x1)
        if span > best_span:
            best_line = line
            best_span = span

    if best_line is None:
        return

    best_line.set("x1", _format_numeric(target_centers[0]))
    best_line.set("x2", _format_numeric(target_centers[-1]))
    best_line.set("y1", _format_numeric(line_y))
    best_line.set("y2", _format_numeric(line_y))


def _normalize_timeline_arrows(
    root: etree._Element,
    target_centers: list[float],
    line_y: float,
) -> None:
    arrows = [
        use
        for use in root.iter(f"{{{SVG_NS}}}use")
        if use.get("data-icon") == "arrow-right"
    ]
    if len(arrows) != max(len(target_centers) - 1, 0):
        return

    arrows.sort(key=lambda el: float(el.get("x", "0")))
    for idx, arrow in enumerate(arrows):
        try:
            width = float(arrow.get("width", "32"))
            height = float(arrow.get("height", "32"))
        except (ValueError, TypeError):
            width = 32.0
            height = 32.0
        mid_x = (target_centers[idx] + target_centers[idx + 1]) / 2.0
        arrow.set("x", _format_numeric(mid_x - width / 2.0))
        arrow.set("y", _format_numeric(line_y - height / 2.0))


def _normalize_timeline_image_slots(
    stage_groups: list[dict[str, object]],
    page: PagePlan | None,
    warnings: list[str],
) -> None:
    material_needs = getattr(page, "material_needs", None)
    image_needs = getattr(material_needs, "images", None)
    if not image_needs:
        return

    counts: dict[str, int] = {}
    expected_slots: list[str] = []
    for need in image_needs:
        role = getattr(need, "role", None)
        if not role:
            return
        occurrence = counts.get(role, 0) + 1
        counts[role] = occurrence
        expected_slots.append(f"__IMAGE_{str(role).upper()}_{occurrence}__")
    if len(expected_slots) != len(stage_groups):
        return

    stage_images: list[etree._Element] = []
    for stage in stage_groups:
        image_el = stage.get("image")
        if image_el is None or not _is_real_element(image_el):
            return
        stage_images.append(image_el)

    href_attr = "{http://www.w3.org/1999/xlink}href"
    changed = False
    for image_el, expected_href in zip(stage_images, expected_slots):
        current_href = image_el.get("href") or image_el.get(href_attr) or ""
        if current_href == expected_href or not current_href.startswith("__IMAGE_"):
            continue
        if image_el.get("href") is not None:
            image_el.set("href", expected_href)
        else:
            image_el.set(href_attr, expected_href)
        changed = True

    if changed:
        warnings.append("Normalized timeline image slots to match left-to-right stage order")


def _normalize_timeline_layout(
    root: etree._Element,
    page: PagePlan | None,
    warnings: list[str],
) -> None:
    stage_groups = _timeline_stage_groups(root)
    if len(stage_groups) < 2:
        return

    safe_left = 140.0
    safe_right = 1140.0
    if len(stage_groups) == 1:
        target_centers = [(safe_left + safe_right) / 2.0]
    else:
        step = (safe_right - safe_left) / (len(stage_groups) - 1)
        target_centers = [safe_left + step * idx for idx in range(len(stage_groups))]

    moved = False
    for idx, (stage, target_cx) in enumerate(zip(stage_groups, target_centers), start=1):
        current_cx = float(stage["cx"])
        dx = target_cx - current_cx
        if abs(dx) < 1:
            continue
        group = stage["group"]
        if isinstance(group, etree._Element):
            _shift_element_horizontally(root, group, dx, shifted_ids=set())
            warnings.append(
                f"Normalized timeline stage {idx} x from {current_cx:.0f} to {target_cx:.0f}"
            )
            moved = True

    if moved:
        line_y = float(stage_groups[0]["cy"])
        _normalize_timeline_line(root, target_centers, line_y)
        _normalize_timeline_arrows(root, target_centers, line_y)

    _normalize_timeline_image_slots(stage_groups, page, warnings)


def _shift_following_cards(
    root: etree._Element,
    source_rect: etree._Element,
    source_box: tuple[float, float, float, float],
    original_bottom: float,
    dy: float,
) -> int:
    shifted_ids: set[int] = {id(source_rect)}
    shifted_cards = 0

    candidates: list[tuple[float, etree._Element, tuple[float, float, float, float]]] = []
    for rect in _collect_card_rects(root):
        if rect is source_rect:
            continue
        rect_box = _rect_box(rect)
        if rect_box is None:
            continue
        _, rect_y, _, _ = rect_box
        if rect_y + 1 < original_bottom:
            continue
        if _horizontal_overlap(source_box, rect_box) < 24:
            continue
        candidates.append((rect_y, rect, rect_box))

    candidates.sort(key=lambda item: item[0])

    for _, rect, rect_box in candidates:
        card_group = _find_card_group(rect)
        if card_group is not None:
            _shift_element_vertically(root, card_group, dy, shifted_ids)
            shifted_cards += 1
            continue

        _shift_element_vertically(root, rect, dy, shifted_ids)
        shifted_cards += 1

        for el in root.iter():
            if not _is_real_element(el) or el is root or _element_in_defs(el) or id(el) in shifted_ids:
                continue
            if _element_belongs_to_card(el, rect_box):
                _shift_element_vertically(root, el, dy, shifted_ids)

    return shifted_cards


def _find_parent_card(
    root: etree._Element,
    text_el: etree._Element,
    tx: float,
    ty: float,
    text_bottom: float,
) -> etree._Element | None:
    """Find the best-matching outer card rect for a text element by reading live DOM."""
    group_rect = _find_parent_card_in_groups(text_el, tx, ty, text_bottom)
    if group_rect is not None:
        return group_rect

    candidates: list[tuple[etree._Element, tuple[float, float, float, float]]] = []
    for rect in _collect_card_rects(root):
        box = _rect_box(rect)
        if box is None:
            continue
        if _text_matches_card_box(
            tx,
            ty,
            text_bottom,
            box,
            overflow_slack=48 if _is_shallow_footer_like_card(box) else 28,
        ):
            candidates.append((rect, box))
    best = _pick_best_card(candidates, tx, ty, text_bottom)
    if best is not None:
        return best

    relaxed_candidates: list[tuple[etree._Element, tuple[float, float, float, float]]] = []
    for rect in _collect_card_rects(root):
        box = _rect_box(rect)
        if box is None:
            continue
        if _allows_relaxed_card_match(text_el) and _text_starts_in_card(tx, ty, box):
            relaxed_candidates.append((rect, box))
    return _pick_best_card(relaxed_candidates, tx, ty, text_bottom)


def _find_parent_card_for_box(
    root: etree._Element,
    x: float,
    y: float,
    bottom: float,
) -> etree._Element | None:
    candidates: list[tuple[etree._Element, tuple[float, float, float, float]]] = []
    for rect in _collect_card_rects(root):
        box = _rect_box(rect)
        if box is None:
            continue
        if _text_starts_in_card(x, y, box, edge_slack=12, vertical_slack=24):
            candidates.append((rect, box))
    return _pick_best_card(candidates, x, y, bottom)


def _fix_image_overflow(root: etree._Element, warnings: list[str]) -> None:
    padding = 12.0
    for img in root.iter(f"{{{SVG_NS}}}image"):
        if _has_translated_group_ancestor(img):
            continue
        try:
            x = float(img.get("x", "0"))
            y = float(img.get("y", "0"))
            width = float(img.get("width", "0"))
            height = float(img.get("height", "0"))
        except (ValueError, TypeError):
            continue
        if width <= 0 or height <= 0:
            continue

        card_rect = _find_parent_card_for_box(root, x, y, y + height)
        if card_rect is None:
            continue
        card_box = _rect_box(card_rect)
        if card_box is None:
            continue

        card_x, card_y, card_w, card_h = card_box
        card_right = card_x + card_w
        card_bottom = card_y + card_h

        min_x = card_x + padding
        max_x = card_right - padding - width
        if max_x >= min_x:
            target_x = min(max(x, min_x), max_x)
        else:
            target_x = min_x
        target_y = max(y, card_y + padding)

        available_w = card_right - padding - target_x
        available_h = card_bottom - padding - target_y
        if available_w <= 0 or available_h <= 0:
            continue

        scale = min(1.0, available_w / width, available_h / height)
        new_width = width * scale
        new_height = height * scale

        changed = False
        if abs(target_x - x) > 0.5:
            img.set("x", _format_numeric(target_x))
            changed = True
        if abs(target_y - y) > 0.5:
            img.set("y", _format_numeric(target_y))
            changed = True
        if abs(new_width - width) > 0.5:
            img.set("width", _format_numeric(new_width))
            changed = True
        if abs(new_height - height) > 0.5:
            img.set("height", _format_numeric(new_height))
            changed = True

        if changed:
            warnings.append(
                "Adjusted image to fit card bounds "
                f"{int(round(width))}x{int(round(height))}->{int(round(new_width))}x{int(round(new_height))}"
            )


def _fix_text_outside_cards_legacy_unused(root: etree._Element, warnings: list[str]) -> None:
    """Fix text that overflows its parent card rect boundary."""
    for text_el in root.iter(f"{{{SVG_NS}}}text"):
        try:
            tx = float(text_el.get("x", "0"))
            ty = float(text_el.get("y", "0"))
        except (ValueError, TypeError):
            continue

        text_bottom = _get_text_bottom_y(text_el)

        # Find parent card from live DOM (not a snapshot — avoids stale height)
        card_rect = _find_parent_card(root, text_el, tx, ty, text_bottom)
        if card_rect is None:
            continue

        ry = float(card_rect.get("y", "0"))
        rh = float(card_rect.get("height", "0"))
        card_bottom = ry + rh
        overflow = text_bottom - card_bottom

        if overflow > CARD_OVERFLOW_TOLERANCE:
            new_h = text_bottom - ry + 4
            card_rect.set("height", str(int(new_h)))
            warnings.append(
                f"Expanded card height {int(rh)}→{int(new_h)} "
                f"(text bottom {text_bottom:.0f} overflowed card bottom {card_bottom:.0f})"
            )



def _fix_text_outside_cards(root: etree._Element, warnings: list[str]) -> None:
    """Fix text that overflows its parent card rect boundary."""
    for text_el in root.iter(f"{{{SVG_NS}}}text"):
        if _is_footer_page_number_text(text_el):
            continue
        if _is_centered_short_label(text_el):
            continue
        if _is_centered_page_banner(text_el):
            continue
        if _has_translated_group_ancestor(text_el):
            continue
        try:
            tx = float(text_el.get("x", "0"))
            ty = float(text_el.get("y", "0"))
        except (ValueError, TypeError):
            continue

        text_bottom = _get_text_bottom_y(text_el)

        # Read the parent card from the live DOM so reflowed positions are visible.
        card_rect = _find_parent_card(root, text_el, tx, ty, text_bottom)
        if card_rect is None:
            continue

        box = _rect_box(card_rect)
        if box is None:
            continue

        _, ry, _, rh = box
        card_bottom = ry + rh
        overflow = text_bottom - card_bottom
        overflow_tolerance = (
            SHALLOW_CARD_OVERFLOW_TOLERANCE
            if _is_shallow_footer_like_card(box)
            else CARD_OVERFLOW_TOLERANCE
        )

        if overflow > overflow_tolerance:
            is_shallow_card = _is_shallow_footer_like_card(box)
            target_h = text_bottom - ry + 4
            max_h = CONTENT_BOTTOM_LIMIT - ry
            new_h = min(target_h, max_h) if is_shallow_card else target_h

            if is_shallow_card and new_h <= rh:
                shifted_up = _shift_text_up_within_card(root, text_el, box, overflow)
                if shifted_up > 0:
                    text_bottom = _get_text_bottom_y(text_el)
                    overflow = text_bottom - card_bottom
                    warnings.append(
                        f"Shifted footer-card text upward by {int(round(shifted_up))} "
                        f"to avoid expanding shallow card at y={ry:.0f}"
                    )
                    if overflow <= overflow_tolerance:
                        continue
                target_h = text_bottom - ry + 4
                new_h = min(target_h, max_h)

            rounded_new_h = float(int(new_h))
            delta = max(0.0, rounded_new_h - rh)
            if delta <= 0:
                warnings.append(
                    f"Detected footer-card text overflow {overflow:.0f}px near page bottom, "
                    f"but skipped card expansion to keep bottom within y={CONTENT_BOTTOM_LIMIT}"
                )
                continue

            card_rect.set("height", str(int(rounded_new_h)))

            shifted_cards = 0
            if delta > 0:
                card_box = _rect_box(card_rect)
                if card_box is not None:
                    shifted_cards = _shift_following_cards(root, card_rect, card_box, card_bottom, delta)

            warning = (
                f"Expanded card height {int(rh)}->{int(rounded_new_h)} "
                f"(text bottom {text_bottom:.0f} overflowed card bottom {card_bottom:.0f})"
            )
            if shifted_cards:
                warning += f"; shifted {shifted_cards} following cards by {int(delta)}"
            warnings.append(warning)


def _normalize_stacked_card_heights(root: etree._Element, warnings: list[str]) -> None:
    groups: dict[tuple[int, int], list[tuple[etree._Element, tuple[float, float, float, float]]]] = {}
    for rect in root.iter(f"{{{SVG_NS}}}rect"):
        if _element_in_defs(rect) or _has_translated_group_ancestor(rect):
            continue
        if not _is_meaningful_card_rect(rect):
            continue
        box = _rect_box(rect)
        if box is None:
            continue
        x, _, width, height = box
        if width < 500 or height < 70 or height > 180:
            continue
        key = (round(x / 8), round(width / 8))
        groups.setdefault(key, []).append((rect, box))

    for cards in groups.values():
        if len(cards) < 3:
            continue
        cards.sort(key=lambda item: item[1][1])
        heights = [box[3] for _, box in cards]
        target_h = max(heights)
        if target_h - min(heights) < 8:
            continue

        changed = 0
        for idx, (rect, box) in enumerate(cards):
            _, y, _, height = box
            if height >= target_h:
                continue
            if idx + 1 < len(cards):
                next_y = cards[idx + 1][1][1]
                if y + target_h > next_y - 8:
                    continue
            elif y + target_h > CONTENT_BOTTOM_LIMIT:
                continue
            rect.set("height", str(int(target_h)))
            changed += 1

        if changed:
            warnings.append(
                f"Normalized {changed} stacked card height(s) to {int(target_h)}"
            )


def _check_image_hrefs(root: etree._Element, warnings: list[str]) -> None:
    xlink_href = "{http://www.w3.org/1999/xlink}href"
    for img in root.iter(f"{{{SVG_NS}}}image"):
        href = img.get("href") or img.get(xlink_href) or ""
        if not href or not href.strip():
            warnings.append("Found <image> with empty href")


def _warn_layout_issues(
    root: etree._Element,
    warnings: list[str],
    page: PagePlan | None = None,
) -> None:
    """Detect layout issues and emit warnings for LLM reviewer to fix.

    Does NOT auto-fix — only reports problems so the review LLM can
    make intelligent corrections with full context.
    """
    texts = list(root.iter(f"{{{SVG_NS}}}text"))

    # 0. Nested transform check
    for g in root.iter(f"{{{SVG_NS}}}g"):
        transform = g.get("transform", "")
        if "translate" not in transform:
            continue
        # Check if this g contains cards (rect with meaningful size)
        has_card = False
        for rect in g.iter(f"{{{SVG_NS}}}rect"):
            try:
                w = float(rect.get("width", "0"))
                h = float(rect.get("height", "0"))
                if w > 100 and h > 40:
                    has_card = True
                    break
            except (ValueError, TypeError):
                pass
        if has_card:
            warnings.append(
                f"布局使用了 <g transform=\"{transform}\"> 包裹卡片内容，"
                f"内部坐标是相对值，会导致边界检查失效和 PPT 转换错乱。"
                f"应改为所有元素使用绝对坐标（直接设 x/y），去掉 transform"
            )

    # 1. Page title position check
    # First bold large text should be near y=50, except divider-like section pages.
    if page is None or page.page_type != "section":
        for t in texts:
            fs_str = t.get("font-size", "16")
            try:
                fs = float(fs_str.replace("px", ""))
            except (ValueError, TypeError):
                continue
            weight = t.get("font-weight", "")
            if fs >= 28 and weight in ("bold", "700", "800", "900"):
                y_str = t.get("y", "0")
                try:
                    y = float(y_str)
                except (ValueError, TypeError):
                    continue
                if y > 100:
                    warnings.append(
                        f"页面标题位置异常：y={y:.0f}（应在 y=50 附近）。"
                        f"标题可能被卡片遮挡或布局错乱，建议移到 y=50"
                    )
                break  # Only check first title

    # 2. Circle-label alignment check
    circles = []
    for c in root.iter(f"{{{SVG_NS}}}circle"):
        try:
            cx = float(c.get("cx", "0"))
            cy = float(c.get("cy", "0"))
            r = float(c.get("r", "0"))
            if 0 < r < 50:
                circles.append((cx, cy, r))
        except (ValueError, TypeError):
            pass

    for t in texts:
        if t.get("text-anchor") != "middle":
            continue
        text_content = (t.text or "").strip()
        if not text_content or len(text_content) > 3:
            continue
        try:
            tx = float(t.get("x", "0"))
            ty = float(t.get("y", "0"))
        except (ValueError, TypeError):
            continue
        for cx, cy, r in circles:
            if abs(cx - tx) < 25 and abs(cy - ty) < r * 3 and abs(cy - ty) > 5:
                warnings.append(
                    f"圆内编号 \"{text_content}\" 未对齐：text y={ty:.0f} 但 circle cy={cy:.0f}，"
                    f"差 {ty-cy:.0f}px。应设 text y = circle cy"
                )
                break
