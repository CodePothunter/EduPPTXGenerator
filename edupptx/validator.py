"""Layout validator: catches layout crimes before they hit the PPTX.

Checks bounds, overlap, minimum sizes. Clamps violations and logs warnings.
Never crashes — returns a list of warning strings.
"""

from __future__ import annotations

from loguru import logger

from edupptx.models import ResolvedShape, ResolvedSlide
from edupptx.style_schema import PT, SLIDE_H, SLIDE_W


def _clamp_bounds(shape: ResolvedShape) -> list[str]:
    """Clamp shape to canvas bounds. Returns warnings for any clamped values."""
    warnings = []
    if shape.left < 0:
        warnings.append(f"Shape '{shape.text or shape.shape_type}' left={shape.left} < 0, clamped")
        shape.left = 0
    if shape.top < 0:
        warnings.append(f"Shape '{shape.text or shape.shape_type}' top={shape.top} < 0, clamped")
        shape.top = 0
    if shape.left + shape.width > SLIDE_W:
        new_w = SLIDE_W - shape.left
        if new_w > 0:
            warnings.append(f"Shape '{shape.text or shape.shape_type}' exceeds right edge, width clamped {shape.width}->{new_w}")
            shape.width = new_w
    if shape.top + shape.height > SLIDE_H:
        new_h = SLIDE_H - shape.top
        if new_h > 0:
            warnings.append(f"Shape '{shape.text or shape.shape_type}' exceeds bottom edge, height clamped {shape.height}->{new_h}")
            shape.height = new_h
    return warnings


def _check_text_min_width(shape: ResolvedShape) -> list[str]:
    """Check that textbox is wide enough for at least 6 CJK characters."""
    if shape.shape_type != "textbox" or shape.font is None:
        return []
    min_w = 6 * shape.font.size_pt * PT
    if shape.width < min_w:
        return [f"Textbox '{shape.text[:20] if shape.text else ''}...' width={shape.width/PT:.0f}pt < min {min_w/PT:.0f}pt for 6 chars"]
    return []


def _check_card_body_height(shape: ResolvedShape) -> list[str]:
    """Warn if a card body textbox is shorter than 30pt.

    Only checks textboxes with long text (> 20 chars), since short card titles
    don't need the minimum height guarantee.
    """
    if shape.shape_type != "textbox" or shape.font is None:
        return []
    text_len = len(shape.text) if shape.text else 0
    if shape.auto_shrink and shape.height < 30 * PT and text_len > 20:
        return [f"Card body '{shape.text[:20] if shape.text else ''}...' height={shape.height/PT:.0f}pt < 30pt minimum"]
    return []


def _check_overlap(shapes: list[ResolvedShape]) -> list[str]:
    """Check for bounding box overlaps between card containers only."""
    # Only check overlap between actual card containers (same z-order range), not panels
    cards = [s for s in shapes if s.shape_type == "rounded_rect"
             and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20]
    warnings = []
    for i in range(len(cards)):
        for j in range(i + 1, len(cards)):
            a, b = cards[i], cards[j]
            if (a.left < b.left + b.width and a.left + a.width > b.left and
                    a.top < b.top + b.height and a.top + a.height > b.top):
                warnings.append(f"Card overlap detected between shapes at ({a.left},{a.top}) and ({b.left},{b.top})")
    return warnings


def validate_slides(slides: list[ResolvedSlide]) -> list[str]:
    """Validate all slides. Clamps violations, returns warning list."""
    all_warnings: list[str] = []
    for i, slide in enumerate(slides):
        for shape in slide.shapes:
            all_warnings.extend(_clamp_bounds(shape))
            all_warnings.extend(_check_text_min_width(shape))
            all_warnings.extend(_check_card_body_height(shape))
        all_warnings.extend(_check_overlap(slide.shapes))

    for w in all_warnings:
        logger.warning("Validator: {}", w)

    return all_warnings
