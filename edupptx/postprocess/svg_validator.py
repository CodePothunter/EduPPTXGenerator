"""Validate and auto-fix LLM-generated SVG for PPT compatibility."""

import re
from lxml import etree

SVG_NS = "http://www.w3.org/2000/svg"
NSMAP = {"svg": SVG_NS}

EXPECTED_VIEWBOX = "0 0 1280 720"
MAX_X = 1280
MAX_Y = 720

SAFE_FONTS = {"微软雅黑", "Microsoft YaHei", "Arial", "Helvetica", "sans-serif"}
FALLBACK_FONT = "Microsoft YaHei, Arial, sans-serif"


def validate_and_fix(svg_content: str) -> tuple[str, list[str]]:
    """Validate SVG, auto-fix issues. Returns (fixed_svg, list_of_warnings)."""
    warnings: list[str] = []

    # Pre-clean unescaped & (common LLM artifact)
    import re
    svg_content = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", svg_content)

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

    _fix_viewbox(root, warnings)
    _remove_foreign_objects(root, warnings)
    _remove_css_animations(root, warnings)
    _fix_fonts(root, warnings)
    _clamp_boundaries(root, warnings)
    _check_image_hrefs(root, warnings)

    fixed = etree.tostring(root, encoding="unicode", xml_declaration=False)
    return fixed, warnings


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


def _is_font_safe(font_family: str) -> bool:
    fonts = [f.strip().strip("'\"") for f in font_family.split(",")]
    return all(f in SAFE_FONTS for f in fonts if f)


def _fix_fonts(root: etree._Element, warnings: list[str]) -> None:
    for el in root.iter():
        ff = el.get("font-family")
        if ff and not _is_font_safe(ff):
            el.set("font-family", FALLBACK_FONT)
            warnings.append(f"Replaced unsafe font '{ff}' with '{FALLBACK_FONT}'")


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
    for tag in (f"{{{SVG_NS}}}text", f"{{{SVG_NS}}}rect"):
        for el in root.iter(tag):
            for attr, limit in [("x", MAX_X), ("y", MAX_Y)]:
                val = el.get(attr)
                if val is not None:
                    new_val, changed = _clamp_value(val, 0, limit)
                    if changed:
                        el.set(attr, new_val)
                        tag_name = etree.QName(el.tag).localname
                        warnings.append(
                            f"Clamped <{tag_name}> {attr}={val} to {new_val}"
                        )


def _check_image_hrefs(root: etree._Element, warnings: list[str]) -> None:
    xlink_href = "{http://www.w3.org/1999/xlink}href"
    for img in root.iter(f"{{{SVG_NS}}}image"):
        href = img.get("href") or img.get(xlink_href) or ""
        if not href or not href.strip():
            warnings.append("Found <image> with empty href")
