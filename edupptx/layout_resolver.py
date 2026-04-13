"""Layout resolver: PresentationPlan + ResolvedStyle -> list[ResolvedSlide].

Computes absolute EMU coordinates for every shape on every slide.
Outputs a flat list of ResolvedShape per slide — no style lookups remain.
"""

from __future__ import annotations

from pathlib import Path

from edupptx.models import (
    PresentationPlan,
    ResolvedFont,
    ResolvedShadow,
    ResolvedShape,
    ResolvedSlide,
    SlideContent,
)
from edupptx.style_schema import (
    CARD_H,
    CARD_TOP,
    FOOTER_H,
    FOOTER_Y,
    PT,
    SLIDE_H,
    SLIDE_W,
    SUBTITLE_H,
    SUBTITLE_Y,
    TITLE_H,
    TITLE_Y,
    ResolvedStyle,
)

# z-order layers
Z_PANEL = 1
Z_DECORATION = 5
Z_TITLE = 10
Z_CARDS = 20
Z_FOOTER = 50


def _tint_color(hex_color: str, amount: float = 0.12) -> str:
    r = int(hex_color.lstrip("#")[0:2], 16)
    g = int(hex_color.lstrip("#")[2:4], 16)
    b = int(hex_color.lstrip("#")[4:6], 16)
    r = int(255 + (r - 255) * amount)
    g = int(255 + (g - 255) * amount)
    b = int(255 + (b - 255) * amount)
    return f"#{r:02X}{g:02X}{b:02X}"


def _make_font(style: ResolvedStyle, size_pt: int, bold: bool = False,
               color: str | None = None) -> ResolvedFont:
    return ResolvedFont(
        family=style.heading_font.family if bold else style.body_font.family,
        fallback=style.heading_font.fallback if bold else style.body_font.fallback,
        size_pt=size_pt,
        bold=bold,
        color=color or style.heading_color,
    )


def _card_shadow(style: ResolvedStyle) -> ResolvedShadow:
    return ResolvedShadow(
        blur_emu=style.card_shadow_blur_emu,
        dist_emu=style.card_shadow_dist_emu,
        color=style.card_shadow_color,
        alpha_pct=style.card_shadow_alpha_pct,
    )


def _resolve_cards(
    slide: SlideContent, style: ResolvedStyle,
    top: int, height: int, left: int, total_width: int,
    base_z: int,
) -> list[ResolvedShape]:
    """Generate resolved shapes for N card columns."""
    n = len(slide.cards)
    if n == 0:
        return []

    shapes: list[ResolvedShape] = []
    gap = style.card_gap
    card_w = (total_width - gap * (n - 1)) // n
    pad = style.card_pad
    icon_sz = style.icon_size
    icon_margin = style.icon_margin
    card_title_h = style.card_title_h

    usable_h = height - 2 * pad
    full_threshold = 1_778_000
    compact_threshold = 1_016_000
    MIN_BODY_H = 38 * PT  # 30pt minimum + 8pt buffer

    # Adaptive mode selection: predict body_h and downgrade if too small
    _full_overhead = icon_sz + icon_margin + card_title_h + icon_margin
    _full_body_h = usable_h - _full_overhead
    if usable_h >= full_threshold and _full_body_h >= MIN_BODY_H:
        layout_mode = "full"
    elif usable_h >= compact_threshold:
        _compact_overhead = 406_400 + 101_600 + 304_800 + 101_600  # 32pt icon + 8pt + 24pt title + 8pt
        _compact_body_h = usable_h - _compact_overhead
        if _compact_body_h >= MIN_BODY_H:
            layout_mode = "compact"
        else:
            layout_mode = "minimal"
    else:
        layout_mode = "minimal"

    card_fill = _tint_color(style.card_fill_color, 0.15)

    for i, card in enumerate(slide.cards):
        cx = left + i * (card_w + gap)
        card_bottom = top + height - pad
        text_w = card_w - 2 * pad
        z = base_z + i * 5

        # Card container
        shapes.append(ResolvedShape(
            shape_type="rounded_rect",
            left=cx, top=top, width=card_w, height=height,
            fill_color=card_fill,
            corner_radius=style.card_corner_radius,
            shadow=_card_shadow(style),
            z_order=z,
        ))

        if layout_mode == "full":
            # Full layout: icon + title + body
            actual_icon_sz = icon_sz
            icon_x = cx + (card_w - actual_icon_sz) // 2
            icon_y = top + pad

            # Icon circle bg
            icon_pad = int(actual_icon_sz * 0.20)
            shapes.append(ResolvedShape(
                shape_type="oval",
                left=icon_x - icon_pad, top=icon_y - icon_pad,
                width=actual_icon_sz + icon_pad * 2,
                height=actual_icon_sz + icon_pad * 2,
                fill_color=style.accent_color.replace(style.accent_color, _tint_color(style.accent_color, 0.3)),
                z_order=z + 1,
            ))

            # Icon image placeholder
            shapes.append(ResolvedShape(
                shape_type="image",
                left=icon_x, top=icon_y,
                width=actual_icon_sz, height=actual_icon_sz,
                image_path=f"icon:{card.icon}:{style.icon_color}",
                z_order=z + 2,
            ))

            title_y = icon_y + actual_icon_sz + icon_margin
            body_y = title_y + card_title_h + icon_margin
            body_h = max(card_bottom - body_y, 0)

        elif layout_mode == "compact":
            # Compact: smaller icon + title + body
            actual_icon_sz = 406_400  # 32pt
            icon_x = cx + (card_w - actual_icon_sz) // 2
            icon_y = top + pad
            compact_margin = 101_600  # 8pt

            icon_pad = int(actual_icon_sz * 0.20)
            shapes.append(ResolvedShape(
                shape_type="oval",
                left=icon_x - icon_pad, top=icon_y - icon_pad,
                width=actual_icon_sz + icon_pad * 2,
                height=actual_icon_sz + icon_pad * 2,
                fill_color=_tint_color(style.accent_color, 0.3),
                z_order=z + 1,
            ))
            shapes.append(ResolvedShape(
                shape_type="image",
                left=icon_x, top=icon_y,
                width=actual_icon_sz, height=actual_icon_sz,
                image_path=f"icon:{card.icon}:{style.icon_color}",
                z_order=z + 2,
            ))

            title_y = icon_y + actual_icon_sz + compact_margin
            card_title_h_compact = 304_800  # 24pt
            body_y = title_y + card_title_h_compact + compact_margin
            body_h = max(card_bottom - body_y, 0)
            card_title_h = card_title_h_compact

        else:
            # Minimal: no icon, just title + body
            title_y = top + pad
            card_title_h_min = 304_800
            body_y = title_y + card_title_h_min + 76_200
            body_h = max(card_bottom - body_y, 0)
            card_title_h = card_title_h_min

        # Card title
        shapes.append(ResolvedShape(
            shape_type="textbox",
            left=cx + pad, top=title_y,
            width=text_w, height=card_title_h,
            text=card.title,
            font=_make_font(style, style.card_title_size_pt, bold=True,
                            color=style.card_title_color),
            v_anchor="ctr",
            auto_shrink=True,
            z_order=z + 3,
        ))

        # Card body
        if body_h > 0:
            body_size = style.body_size_pt
            if len(card.body) > 50:
                body_size = max(8, body_size - 2)
            shapes.append(ResolvedShape(
                shape_type="textbox",
                left=cx + pad, top=body_y,
                width=text_w, height=body_h,
                text=card.body,
                font=_make_font(style, body_size, color=style.body_color),
                auto_shrink=True,
                z_order=z + 4,
            ))

    return shapes


def _resolve_content_panel(style: ResolvedStyle, has_footer: bool,
                           card_height: int = CARD_H) -> ResolvedShape | None:
    """Semi-transparent panel behind card area."""
    if not style.decorations.content_panel:
        return None

    pad = 190_500  # 15pt
    panel_x = style.margin_left - pad
    panel_y = CARD_TOP - pad

    if has_footer:
        panel_bottom = FOOTER_Y + FOOTER_H + pad
    else:
        panel_bottom = CARD_TOP + card_height + pad
    panel_h = panel_bottom - panel_y

    return ResolvedShape(
        shape_type="rounded_rect",
        left=panel_x, top=panel_y,
        width=style.content_w + pad * 2, height=panel_h,
        fill_color="#FFFFFF",
        corner_radius=4000,
        alpha_pct=style.decorations.panel_alpha_pct,
        shadow=ResolvedShadow(
            blur_emu=457_200, dist_emu=63_500,
            color="#000000", alpha_pct=8,
        ),
        z_order=Z_PANEL,
    )


def _resolve_title_underline(
    style: ResolvedStyle, title_x: int, title_y: int,
    title_w: int, title_h: int, text: str, size_pt: int,
    centered: bool = False,
) -> ResolvedShape | None:
    """Short accent underline below title."""
    if not style.decorations.title_underline:
        return None

    line_w = 762_000   # 60pt
    line_h = 38_100    # 3pt

    # Estimate text height for positioning
    avg_cw = size_pt * 0.9
    effective_w = max((title_w - 91_440) / PT, 10)
    chars_per_line = max(1, int(effective_w / avg_cw))
    num_lines = max(1, -(-len(text) // chars_per_line))
    text_h = int(num_lines * size_pt * PT * 1.3)
    text_h = min(text_h, title_h)

    gap = 63_500  # 5pt
    line_y = title_y + text_h + gap

    if centered:
        line_x = title_x + (title_w - line_w) // 2
    else:
        line_x = title_x

    return ResolvedShape(
        shape_type="rounded_rect",
        left=line_x, top=line_y,
        width=line_w, height=line_h,
        fill_color=style.accent_color,
        corner_radius=50_000,
        z_order=Z_DECORATION,
    )


def _resolve_footer(
    style: ResolvedStyle, text: str,
) -> list[ResolvedShape]:
    """Footer text + separator line."""
    shapes: list[ResolvedShape] = []
    footer_slot_y = FOOTER_Y
    footer_slot_w = style.content_w

    if style.decorations.footer_separator:
        line_w = int(footer_slot_w * 0.3)
        line_h = 19_050  # 1.5pt
        line_x = style.margin_left + (footer_slot_w - line_w) // 2
        line_y = footer_slot_y - 76_200  # 6pt above
        shapes.append(ResolvedShape(
            shape_type="rounded_rect",
            left=line_x, top=line_y,
            width=line_w, height=line_h,
            fill_color=_tint_color(style.accent_color, 0.3),
            corner_radius=50_000,
            z_order=Z_FOOTER - 1,
        ))

    shapes.append(ResolvedShape(
        shape_type="textbox",
        left=style.margin_left, top=footer_slot_y,
        width=footer_slot_w, height=FOOTER_H,
        text=text,
        font=_make_font(style, style.footer_size_pt, color=style.body_color),
        v_anchor="t",
        z_order=Z_FOOTER,
    ))
    return shapes


def _resolve_formula_bar(style: ResolvedStyle, formula: str) -> list[ResolvedShape]:
    """Formula highlight bar with accent border."""
    shapes: list[ResolvedShape] = []
    shapes.append(ResolvedShape(
        shape_type="rounded_rect",
        left=style.margin_left, top=FOOTER_Y,
        width=style.content_w, height=FOOTER_H,
        fill_color=_tint_color(style.accent_color, 0.3),
        line_color=style.accent_color,
        corner_radius=5000,
        z_order=Z_FOOTER - 1,
    ))
    shapes.append(ResolvedShape(
        shape_type="textbox",
        left=style.margin_left, top=FOOTER_Y,
        width=style.content_w, height=FOOTER_H,
        text=formula,
        font=_make_font(style, style.formula_size_pt, bold=True,
                        color=style.accent_color),
        v_anchor="ctr",
        z_order=Z_FOOTER,
    ))
    return shapes


# ── Slide type resolvers ──────────────────────────────────


def _resolve_cover(slide: SlideContent, style: ResolvedStyle) -> list[ResolvedShape]:
    shapes: list[ResolvedShape] = []

    title_size = style.title_size_pt + 4
    is_centered = style.title_position == "center" or True  # cover always centered

    # Title
    shapes.append(ResolvedShape(
        shape_type="textbox",
        left=style.margin_left, top=TITLE_Y,
        width=style.content_w, height=TITLE_H,
        text=slide.title,
        font=_make_font(style, title_size, bold=True),
        v_anchor="t",
        z_order=Z_TITLE,
    ))

    # Title underline (centered for cover)
    underline = _resolve_title_underline(
        style, style.margin_left, TITLE_Y,
        style.content_w, TITLE_H,
        slide.title, title_size, centered=True,
    )
    if underline:
        shapes.append(underline)

    # Subtitle
    if slide.subtitle:
        shapes.append(ResolvedShape(
            shape_type="textbox",
            left=style.margin_left, top=SUBTITLE_Y,
            width=style.content_w, height=SUBTITLE_H,
            text=slide.subtitle,
            font=_make_font(style, style.subtitle_size_pt, color=style.body_color),
            v_anchor="t",
            auto_shrink=True,
            z_order=Z_TITLE + 1,
        ))

    # Content panel
    panel = _resolve_content_panel(style, has_footer=bool(slide.formula))
    if panel:
        shapes.append(panel)

    # Cards
    card_shapes = _resolve_cards(
        slide, style, top=CARD_TOP, height=CARD_H,
        left=style.margin_left, total_width=style.content_w,
        base_z=Z_CARDS,
    )
    shapes.extend(card_shapes)

    # Formula bar
    if slide.formula:
        shapes.extend(_resolve_formula_bar(style, slide.formula))

    return shapes


def _resolve_content(slide: SlideContent, style: ResolvedStyle) -> list[ResolvedShape]:
    shapes: list[ResolvedShape] = []

    # Determine material position (affects card placement)
    mat_pos = None
    if slide.content_materials:
        mat_pos = slide.content_materials[0].position

    # Title
    shapes.append(ResolvedShape(
        shape_type="textbox",
        left=style.margin_left, top=TITLE_Y,
        width=style.content_w, height=TITLE_H,
        text=slide.title,
        font=_make_font(style, style.title_size_pt, bold=True),
        z_order=Z_TITLE,
    ))

    # Title underline
    underline = _resolve_title_underline(
        style, style.margin_left, TITLE_Y,
        style.content_w, TITLE_H,
        slide.title, style.title_size_pt,
    )
    if underline:
        shapes.append(underline)

    # Compute card area based on material position
    content_h = FOOTER_Y - CARD_TOP
    card_top = CARD_TOP
    card_h = CARD_H
    card_left = style.margin_left
    card_w = style.content_w

    if mat_pos == "center":
        # Material on top, cards below. Ratio adapts to ensure cards have enough space.
        half_gap = style.card_gap // 2
        # Minimum card height: 2*pad + title(24pt) + gap(6pt) + body(38pt)
        min_card_h = 2 * style.card_pad + 304_800 + 76_200 + 38 * PT
        max_mat_ratio = max(0.25, 1.0 - (min_card_h + half_gap * 2) / content_h)
        mat_h = int(content_h * min(0.45, max_mat_ratio))
        card_top = CARD_TOP + mat_h + half_gap
        card_h = FOOTER_Y - card_top - half_gap
    elif mat_pos == "left":
        # Material takes left 50%, cards on right
        mat_w = int(style.content_w * 0.50)
        mat_gap = int(style.content_w * 0.04)
        card_left = style.margin_left + mat_w + mat_gap
        card_w = style.content_w - mat_w - mat_gap
        card_h = content_h
    elif mat_pos == "right":
        # Cards on left, material on right 50%
        mat_w = int(style.content_w * 0.50)
        mat_gap = int(style.content_w * 0.04)
        card_w = style.content_w - mat_w - mat_gap
        card_h = content_h
    elif mat_pos == "full":
        # No cards, material fills content area
        card_w = 0

    # Content panel
    panel = _resolve_content_panel(style, has_footer=bool(slide.footer), card_height=card_h)
    if panel:
        shapes.append(panel)

    # Cards (skip if full material)
    if card_w > 0:
        card_shapes = _resolve_cards(
            slide, style, top=card_top, height=card_h,
            left=card_left, total_width=card_w,
            base_z=Z_CARDS,
        )
        shapes.extend(card_shapes)

    # Footer
    if slide.footer:
        shapes.extend(_resolve_footer(style, slide.footer))

    return shapes


def _resolve_big_quote(slide: SlideContent, style: ResolvedStyle) -> list[ResolvedShape]:
    shapes: list[ResolvedShape] = []

    quote_area_y = int(SLIDE_H * 0.3)
    quote_area_h = TITLE_H * 2

    # Quote decoration
    if style.decorations.quote_bar:
        # Large decorative opening quote mark
        quote_size = 1_524_000
        shapes.append(ResolvedShape(
            shape_type="textbox",
            left=style.margin_left - 127_000,
            top=quote_area_y - int(quote_size * 0.6),
            width=quote_size, height=quote_size,
            text="\u201C",
            font=_make_font(style, 96, bold=True,
                            color=_tint_color(style.accent_color, 0.3)),
            z_order=Z_DECORATION,
        ))

        # Vertical accent bar
        bar_w = 50_800    # 4pt
        bar_h = quote_area_h + 254_000
        shapes.append(ResolvedShape(
            shape_type="rounded_rect",
            left=style.margin_left - 254_000,
            top=quote_area_y - 127_000,
            width=bar_w, height=bar_h,
            fill_color=style.accent_color,
            corner_radius=50_000,
            z_order=Z_DECORATION + 1,
        ))

    # Quote text
    title_size = style.title_size_pt + 4
    shapes.append(ResolvedShape(
        shape_type="textbox",
        left=style.margin_left, top=quote_area_y,
        width=style.content_w, height=quote_area_h,
        text=slide.title,
        font=_make_font(style, title_size),
        auto_shrink=True,
        z_order=Z_TITLE,
    ))

    # Footer (source attribution)
    if slide.footer:
        shapes.extend(_resolve_footer(style, slide.footer))

    return shapes


def _resolve_closing(slide: SlideContent, style: ResolvedStyle) -> list[ResolvedShape]:
    shapes: list[ResolvedShape] = []

    # Decorative circle (translucent background element)
    if style.decorations.closing_circle:
        circle_size = 2_540_000  # 200pt
        circle_x = (SLIDE_W - circle_size) // 2
        circle_y = (SLIDE_H - circle_size) // 2 - 381_000
        shapes.append(ResolvedShape(
            shape_type="oval",
            left=circle_x, top=circle_y,
            width=circle_size, height=circle_size,
            fill_color=_tint_color(style.accent_color, 0.3),
            alpha_pct=30,
            z_order=Z_DECORATION,
        ))

    # Centered title
    title_y = SLIDE_H // 2 - 762_000
    shapes.append(ResolvedShape(
        shape_type="textbox",
        left=style.margin_left, top=title_y,
        width=style.content_w, height=762_000,
        text=slide.title,
        font=_make_font(style, style.title_size_pt, bold=True),
        v_anchor="ctr",
        z_order=Z_TITLE,
    ))

    # Subtitle
    if slide.subtitle:
        shapes.append(ResolvedShape(
            shape_type="textbox",
            left=style.margin_left, top=SLIDE_H // 2 + 127_000,
            width=style.content_w, height=508_000,
            text=slide.subtitle,
            font=_make_font(style, style.subtitle_size_pt, color=style.body_color),
            v_anchor="t",
            auto_shrink=True,
            z_order=Z_TITLE + 1,
        ))

    return shapes


def _resolve_section(slide: SlideContent, style: ResolvedStyle) -> list[ResolvedShape]:
    shapes: list[ResolvedShape] = []

    title_y = SLIDE_H // 2 - 952_500

    # Decorative line + diamond above title
    if style.decorations.section_diamond:
        line_w = 1_270_000  # 100pt
        line_h = 50_800     # 4pt
        line_x = (SLIDE_W - line_w) // 2
        line_y = title_y - 254_000

        shapes.append(ResolvedShape(
            shape_type="rounded_rect",
            left=line_x, top=line_y,
            width=line_w, height=line_h,
            fill_color=style.accent_color,
            corner_radius=50_000,
            z_order=Z_DECORATION,
        ))

        # Diamond
        diamond_size = 127_000
        shapes.append(ResolvedShape(
            shape_type="oval",  # approximate diamond with oval
            left=(SLIDE_W - diamond_size) // 2,
            top=line_y - diamond_size - 63_500,
            width=diamond_size, height=diamond_size,
            fill_color=style.accent_color,
            z_order=Z_DECORATION + 1,
        ))

    # Title
    shapes.append(ResolvedShape(
        shape_type="textbox",
        left=style.margin_left, top=title_y,
        width=style.content_w, height=762_000,
        text=slide.title,
        font=_make_font(style, style.title_size_pt, bold=True),
        v_anchor="ctr",
        z_order=Z_TITLE,
    ))

    # Subtitle
    if slide.subtitle:
        shapes.append(ResolvedShape(
            shape_type="textbox",
            left=style.margin_left, top=SLIDE_H // 2 - 63_500,
            width=style.content_w, height=508_000,
            text=slide.subtitle,
            font=_make_font(style, style.subtitle_size_pt, color=style.body_color),
            v_anchor="t",
            auto_shrink=True,
            z_order=Z_TITLE + 1,
        ))

    return shapes


def _resolve_full_image(slide: SlideContent, style: ResolvedStyle) -> list[ResolvedShape]:
    """Full-image slide: material_slot fills content area, no cards."""
    shapes: list[ResolvedShape] = []

    # Title
    shapes.append(ResolvedShape(
        shape_type="textbox",
        left=style.margin_left, top=TITLE_Y,
        width=style.content_w, height=TITLE_H,
        text=slide.title,
        font=_make_font(style, style.title_size_pt, bold=True),
        z_order=Z_TITLE,
    ))

    # Material slot placeholder (actual image added by resolve_layout)
    # The material area spans from CARD_TOP to FOOTER_Y
    return shapes


def _resolve_image_left(slide: SlideContent, style: ResolvedStyle) -> list[ResolvedShape]:
    """Image-left: material on left 45%, cards on right 50%."""
    shapes: list[ResolvedShape] = []
    content_h = FOOTER_Y - CARD_TOP

    # Title
    shapes.append(ResolvedShape(
        shape_type="textbox",
        left=style.margin_left, top=TITLE_Y,
        width=style.content_w, height=TITLE_H,
        text=slide.title,
        font=_make_font(style, style.title_size_pt, bold=True),
        z_order=Z_TITLE,
    ))

    # Title underline
    underline = _resolve_title_underline(
        style, style.margin_left, TITLE_Y,
        style.content_w, TITLE_H,
        slide.title, style.title_size_pt,
    )
    if underline:
        shapes.append(underline)

    # Material takes left 50%, cards take right 46% (4% gap)
    mat_w = int(style.content_w * 0.50)
    mat_gap = int(style.content_w * 0.04)
    cards_x = style.margin_left + mat_w + mat_gap
    cards_w = style.content_w - mat_w - mat_gap

    # Cards on the right
    card_shapes = _resolve_cards(
        slide, style, top=CARD_TOP, height=content_h,
        left=cards_x, total_width=cards_w,
        base_z=Z_CARDS,
    )
    shapes.extend(card_shapes)

    # Footer
    if slide.footer:
        shapes.extend(_resolve_footer(style, slide.footer))

    return shapes


def _resolve_image_right(slide: SlideContent, style: ResolvedStyle) -> list[ResolvedShape]:
    """Image-right: cards on left 46%, material on right 50%."""
    shapes: list[ResolvedShape] = []
    content_h = FOOTER_Y - CARD_TOP

    # Title
    shapes.append(ResolvedShape(
        shape_type="textbox",
        left=style.margin_left, top=TITLE_Y,
        width=style.content_w, height=TITLE_H,
        text=slide.title,
        font=_make_font(style, style.title_size_pt, bold=True),
        z_order=Z_TITLE,
    ))

    # Title underline
    underline = _resolve_title_underline(
        style, style.margin_left, TITLE_Y,
        style.content_w, TITLE_H,
        slide.title, style.title_size_pt,
    )
    if underline:
        shapes.append(underline)

    # Cards on the left (46% width)
    mat_w = int(style.content_w * 0.50)
    mat_gap = int(style.content_w * 0.04)
    cards_w = style.content_w - mat_w - mat_gap

    card_shapes = _resolve_cards(
        slide, style, top=CARD_TOP, height=content_h,
        left=style.margin_left, total_width=cards_w,
        base_z=Z_CARDS,
    )
    shapes.extend(card_shapes)

    # Footer
    if slide.footer:
        shapes.extend(_resolve_footer(style, slide.footer))

    return shapes


# ── Dispatcher ────────────────────────────────────────────

_SLIDE_RESOLVERS = {
    "cover": _resolve_cover,
    "content": _resolve_content,
    "lead_in": _resolve_content,
    "definition": _resolve_content,
    "history": _resolve_content,
    "proof": _resolve_content,
    "example": _resolve_content,
    "exercise": _resolve_content,
    "answer": _resolve_content,
    "summary": _resolve_content,
    "extension": _resolve_content,
    "big_quote": _resolve_big_quote,
    "closing": _resolve_closing,
    "section": _resolve_section,
    "full_image": _resolve_full_image,
    "image_left": _resolve_image_left,
    "image_right": _resolve_image_right,
}


def _compute_material_slot(
    slide_type: str, style: ResolvedStyle,
    material_position: str | None,
) -> tuple[int, int, int, int] | None:
    """Compute the EMU rectangle for material (illustration/diagram) placement.

    Returns (x, y, w, h) or None.
    """
    content_x = style.margin_left
    content_y = CARD_TOP
    content_w = style.content_w
    content_h = FOOTER_Y - CARD_TOP

    if slide_type == "full_image":
        return (content_x, content_y, content_w, content_h)

    if material_position == "full":
        return (content_x, content_y, content_w, content_h)

    if material_position == "left":
        mat_w = int(content_w * 0.50)
        return (content_x, content_y, mat_w, content_h)

    if material_position == "right":
        mat_w = int(content_w * 0.50)
        mat_gap = int(content_w * 0.04)
        cards_w = content_w - mat_w - mat_gap
        mat_x = content_x + cards_w + mat_gap
        return (mat_x, content_y, mat_w, content_h)

    if material_position == "center":
        mat_h = int(content_h * 0.45)
        return (content_x, content_y, content_w, mat_h)

    return None


Z_MATERIAL = 15


def resolve_layout(
    plan: PresentationPlan,
    style: ResolvedStyle,
    bg_paths: list[Path] | None = None,
    material_paths: dict[int, Path] | None = None,
) -> list[ResolvedSlide]:
    """Resolve an entire presentation plan into a list of ResolvedSlide.

    Each ResolvedSlide contains a flat list of ResolvedShape with all values
    concrete. The PPTX writer can render them without any style lookups.

    material_paths: {slide_index: Path} for illustration/diagram images.
    """
    slides: list[ResolvedSlide] = []

    for i, slide_content in enumerate(plan.slides):
        resolver = _SLIDE_RESOLVERS.get(slide_content.type, _resolve_content)
        shapes = resolver(slide_content, style)

        # Add material image if present (aspect-ratio preserving fit)
        mat_path = material_paths.get(i) if material_paths else None
        if mat_path and mat_path.exists() and slide_content.content_materials:
            mat = slide_content.content_materials[0]
            mat_pos = mat.position
            slot = _compute_material_slot(slide_content.type, style, mat_pos)
            if slot:
                mx, my, mw, mh = slot
                scale = getattr(mat, "image_scale", 0.85)
                avail_w = int(mw * scale)
                avail_h = int(mh * scale)

                # Read image dimensions and fit within slot preserving aspect ratio
                try:
                    from PIL import Image as _PILImage
                    with _PILImage.open(str(mat_path)) as _img:
                        img_w, img_h = _img.size
                    if img_w > 0 and img_h > 0:
                        ratio_w = avail_w / img_w
                        ratio_h = avail_h / img_h
                        fit_ratio = min(ratio_w, ratio_h)
                        final_w = int(img_w * fit_ratio)
                        final_h = int(img_h * fit_ratio)
                    else:
                        final_w, final_h = avail_w, avail_h
                except Exception:
                    final_w, final_h = avail_w, avail_h

                img_x = mx + (mw - final_w) // 2
                img_y = my + (mh - final_h) // 2
                shapes.append(ResolvedShape(
                    shape_type="image",
                    left=img_x, top=img_y,
                    width=final_w, height=final_h,
                    image_path=str(mat_path),
                    z_order=Z_MATERIAL,
                ))

        bg = bg_paths[i % len(bg_paths)] if bg_paths else None

        slides.append(ResolvedSlide(
            background_path=bg,
            shapes=shapes,
            notes=slide_content.notes,
        ))

    return slides
