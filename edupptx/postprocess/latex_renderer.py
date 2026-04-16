"""Render LaTeX formulas to images — replace <text data-latex="..."> with PNG."""

from __future__ import annotations

import base64
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from lxml import etree
from loguru import logger
from PIL import Image

SVG_NS = "http://www.w3.org/2000/svg"
_DPI = 200
_SCALE = 1.5  # matplotlib fontsize multiplier for clarity at _DPI


def render_latex_formulas(
    svg_content: str, text_color: str = "#1E293B",
) -> tuple[str, int]:
    """Replace <text data-latex="..."> with rendered formula images.

    Returns (processed_svg, count_of_replacements).
    """
    if "data-latex" not in svg_content:
        return svg_content, 0

    try:
        root = etree.fromstring(svg_content.encode("utf-8"))
    except etree.XMLSyntaxError:
        return svg_content, 0

    count = 0
    for text_el in list(root.iter(f"{{{SVG_NS}}}text")):
        latex = text_el.get("data-latex")
        if not latex:
            continue

        # Read position and font size from the <text> element
        try:
            x = float(text_el.get("x", "0"))
            y = float(text_el.get("y", "0"))
        except (ValueError, TypeError):
            continue
        fs_str = text_el.get("font-size", "18")
        try:
            font_size = float(fs_str.replace("px", ""))
        except (ValueError, TypeError):
            font_size = 18.0

        color = text_el.get("fill", text_color)

        # Render the formula
        png_bytes = _render_formula(latex, font_size, color)
        if png_bytes is None:
            continue  # Keep original <text> as fallback

        # Get image dimensions for SVG sizing
        img = Image.open(io.BytesIO(png_bytes))
        img_w, img_h = img.size
        # Convert pixel dimensions to SVG units (at _DPI)
        svg_w = img_w * 72.0 / _DPI
        svg_h = img_h * 72.0 / _DPI

        # Position: text y is baseline, image y should be above baseline
        img_x = x
        img_y = y - svg_h * 0.75

        # Create <image> element with base64 data URI
        b64 = base64.b64encode(png_bytes).decode("ascii")
        image_el = etree.Element(f"{{{SVG_NS}}}image")
        image_el.set("href", f"data:image/png;base64,{b64}")
        image_el.set("x", str(round(img_x, 1)))
        image_el.set("y", str(round(img_y, 1)))
        image_el.set("width", str(round(svg_w, 1)))
        image_el.set("height", str(round(svg_h, 1)))
        image_el.set("preserveAspectRatio", "xMidYMid meet")

        # Replace <text> with <image>
        parent = text_el.getparent()
        if parent is not None:
            idx = list(parent).index(text_el)
            parent.remove(text_el)
            parent.insert(idx, image_el)
            count += 1

    if count == 0:
        return svg_content, 0

    result = etree.tostring(root, encoding="unicode", xml_declaration=False)
    return result, count


def _render_formula(
    latex: str, font_size: float, color: str,
) -> bytes | None:
    """Render a LaTeX formula to transparent PNG bytes using matplotlib mathtext.

    Args:
        latex: LaTeX expression (without $ delimiters).
        font_size: Target font size in SVG pixels.
        color: Hex color for the formula text.

    Returns:
        PNG bytes, or None if rendering fails.
    """
    mpl_fontsize = font_size * _SCALE
    try:
        fig = plt.figure(figsize=(10, 2))
        fig.patch.set_alpha(0)
        fig.text(
            0.5, 0.5, f"${latex}$",
            fontsize=mpl_fontsize,
            ha="center", va="center",
            color=color,
        )
        buf = io.BytesIO()
        fig.savefig(
            buf, format="png",
            transparent=True,
            dpi=_DPI,
            bbox_inches="tight",
            pad_inches=0.02,
        )
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as exc:
        plt.close("all")
        logger.warning("LaTeX render failed for '{}': {}", latex[:60], exc)
        return None
