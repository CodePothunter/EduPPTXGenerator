"""Validate and auto-fix LLM-generated SVG for PPT compatibility."""

import re
from lxml import etree

SVG_NS = "http://www.w3.org/2000/svg"
NSMAP = {"svg": SVG_NS}

EXPECTED_VIEWBOX = "0 0 1280 720"
MAX_X = 1280
MAX_Y = 720

SAFE_FONTS = {"Noto Sans SC", "微软雅黑", "Microsoft YaHei", "Arial", "Helvetica", "sans-serif"}
FALLBACK_FONT = "Noto Sans SC, Microsoft YaHei, Arial, sans-serif"

MATH_FONTS = {"Courier New", "Consolas", "monospace"}

# Pattern to detect math-like content in text elements
_MATH_CONTENT_RE = re.compile(
    r'[0-9²³√∑∫±×÷≠≤≥≈∞πΔαβγ=+\-*/^(){}|]'
)


def validate_and_fix(svg_content: str) -> tuple[str, list[str]]:
    """Validate SVG, auto-fix issues. Returns (fixed_svg, list_of_warnings)."""
    warnings: list[str] = []

    # Pre-clean XML-unsafe characters (common LLM artifacts)
    import re
    svg_content = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", svg_content)
    # Escape bare < not part of XML tags (e.g., "k < 0" in math formulas)
    svg_content = re.sub(r"<(?![a-zA-Z/!?])", "&lt;", svg_content)

    try:
        root = etree.fromstring(svg_content.encode("utf-8"))
    except etree.XMLSyntaxError as e:
        warnings = [f"SVG parse error: {e}"]
        # Try recovery: wrap in minimal SVG if missing root
        try:
            wrapped = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">{svg_content}</svg>'
            root = etree.fromstring(wrapped.encode("utf-8"))
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
    _wrap_long_text(root, warnings)
    _clamp_boundaries(root, warnings)
    _fix_text_overlaps(root, warnings)
    _fix_text_outside_cards(root, warnings)
    _check_image_hrefs(root, warnings)
    _warn_layout_issues(root, warnings)

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
    # If more than 30% of characters are math-like, consider it math
    math_chars = len(_MATH_CONTENT_RE.findall(content))
    return math_chars / max(len(content), 1) > 0.3


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


def _wrap_long_text(root: etree._Element, warnings: list[str]) -> None:
    """Wrap long <text> content into <tspan> lines to prevent overflow."""
    MAX_CHARS_PER_LINE = 22  # ~22 Chinese characters fit in a typical card

    for text_el in list(root.iter(f"{{{SVG_NS}}}text")):
        # Skip if already has tspan children
        if text_el.find(f"{{{SVG_NS}}}tspan") is not None:
            continue

        content = (text_el.text or "").strip()
        if not content or len(content) <= MAX_CHARS_PER_LINE:
            continue

        # Get text attributes
        x = text_el.get("x", "0")
        fs_str = text_el.get("font-size", "16")
        try:
            fs = float(fs_str.replace("px", ""))
        except (ValueError, TypeError):
            fs = 16

        # Split into lines
        lines = []
        while len(content) > MAX_CHARS_PER_LINE:
            # Try to break at punctuation or space
            cut = MAX_CHARS_PER_LINE
            for sep in ("，", "。", "、", "；", " ", ",", ".", ";"):
                idx = content.rfind(sep, 0, MAX_CHARS_PER_LINE + 1)
                if idx > MAX_CHARS_PER_LINE // 2:
                    cut = idx + 1
                    break
            lines.append(content[:cut])
            content = content[cut:]
        if content:
            lines.append(content)

        if len(lines) <= 1:
            continue

        # Replace text content with tspan elements
        text_el.text = None
        line_height = int(fs * 1.4)
        for i, line in enumerate(lines):
            tspan = etree.SubElement(text_el, f"{{{SVG_NS}}}tspan")
            tspan.set("x", x)
            tspan.set("dy", str(line_height) if i > 0 else "0")
            tspan.text = line

        warnings.append(f"Wrapped long text ({sum(len(l) for l in lines)} chars) into {len(lines)} lines")


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
            # Allow full-width top decoration bar (x=0, y=0, height≤8)
            if tag.endswith("rect"):
                h = el.get("height", "0")
                y = el.get("y", "0")
                try:
                    if float(h) <= 8 and float(y) <= 2:
                        continue  # Skip decoration bar
                except (ValueError, TypeError):
                    pass
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


def _fix_text_overlaps(root: etree._Element, warnings: list[str]) -> None:
    """Detect and fix overlapping <text> elements in the same horizontal column."""
    texts = list(root.iter(f"{{{SVG_NS}}}text"))
    if len(texts) < 2:
        return

    # Collect (element, x, y, font_size) for text elements
    text_info = []
    for t in texts:
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
        text_info.append((t, x, y, fs))

    if len(text_info) < 2:
        return

    # Group by horizontal column (texts within 100px x-range are in the same column)
    text_info.sort(key=lambda t: (t[1], t[2]))  # sort by x, then y
    columns: list[list] = []
    for item in text_info:
        placed = False
        for col in columns:
            if abs(col[0][1] - item[1]) < 100:  # same x-column (100px tolerance)
                col.append(item)
                placed = True
                break
        if not placed:
            columns.append([item])

    # Fix overlaps within each column
    for col in columns:
        col.sort(key=lambda t: t[2])  # sort by y within column
        for i in range(1, len(col)):
            prev_el, _, prev_y, prev_fs = col[i - 1]
            el, x, curr_y, fs = col[i]
            # Use actual bottom of previous text (including tspan dy offsets)
            prev_bottom = _get_text_bottom_y(prev_el)
            min_next_y = prev_bottom + 6  # 6px gap after actual text bottom
            # Fallback: at least prev_y + prev_fs + 6
            min_gap_y = prev_y + prev_fs + 6
            effective_min = max(min_next_y, min_gap_y)
            if curr_y < effective_min:
                new_y = effective_min
                el.set("y", str(int(new_y)))
                col[i] = (el, x, new_y, fs)
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
    return curr_y + fs  # Add font-size for text descent


def _find_parent_card(root: etree._Element, tx: float, ty: float) -> etree._Element | None:
    """Find the best-matching card rect for a text element by reading live DOM."""
    best_rect = None
    best_gap = float("inf")
    for rect in root.iter(f"{{{SVG_NS}}}rect"):
        try:
            rx = float(rect.get("x", "0"))
            ry = float(rect.get("y", "0"))
            rw = float(rect.get("width", "0"))
            rh = float(rect.get("height", "0"))
        except (ValueError, TypeError):
            continue
        if rw < 100 or rh < 50:
            continue
        card_bottom = ry + rh
        if rx - 10 <= tx <= rx + rw + 10 and ry <= ty <= card_bottom + 20:
            gap = card_bottom - ty
            if gap < best_gap:
                best_gap = gap
                best_rect = rect
    return best_rect


def _fix_text_outside_cards(root: etree._Element, warnings: list[str]) -> None:
    """Fix text that overflows its parent card rect boundary."""
    for text_el in root.iter(f"{{{SVG_NS}}}text"):
        try:
            tx = float(text_el.get("x", "0"))
            ty = float(text_el.get("y", "0"))
        except (ValueError, TypeError):
            continue

        text_bottom = _get_text_bottom_y(text_el)

        # Find parent card from live DOM (not a snapshot — avoids stale height)
        card_rect = _find_parent_card(root, tx, ty)
        if card_rect is None:
            continue

        ry = float(card_rect.get("y", "0"))
        rh = float(card_rect.get("height", "0"))
        card_bottom = ry + rh
        overflow = text_bottom - card_bottom

        if overflow > 2:
            new_h = text_bottom - ry + 4
            card_rect.set("height", str(int(new_h)))
            warnings.append(
                f"Expanded card height {int(rh)}→{int(new_h)} "
                f"(text bottom {text_bottom:.0f} overflowed card bottom {card_bottom:.0f})"
            )


def _check_image_hrefs(root: etree._Element, warnings: list[str]) -> None:
    xlink_href = "{http://www.w3.org/1999/xlink}href"
    for img in root.iter(f"{{{SVG_NS}}}image"):
        href = img.get("href") or img.get(xlink_href) or ""
        if not href or not href.strip():
            warnings.append("Found <image> with empty href")


def _warn_layout_issues(root: etree._Element, warnings: list[str]) -> None:
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
    # First bold large text should be near y=50
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
