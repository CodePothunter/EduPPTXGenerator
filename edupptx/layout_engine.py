"""Slot-based layout engine — maps slide types to EMU coordinates.

All coordinates are in EMU (English Metric Units). 1pt = 12700 EMU.
Standard 16:9 slide: 12,192,000 x 6,858,000 EMU (960pt x 540pt).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Canvas constants (EMU)
SLIDE_W = 12_192_000  # 960pt
SLIDE_H = 6_858_000   # 540pt

# Margins
MARGIN_X = 1_016_000   # 80pt
MARGIN_Y = 635_000     # 50pt
CONTENT_W = 10_160_000  # 800pt

# Common vertical positions
TITLE_Y = MARGIN_Y
TITLE_H = 762_000       # 60pt
SUBTITLE_Y = 1_397_000  # 110pt
SUBTITLE_H = 444_500    # 35pt
CARD_TOP = 2_159_000    # 170pt
CARD_H = 2_540_000      # 200pt (default card height)
FOOTER_Y = 5_334_000    # 420pt
FOOTER_H = 889_000      # 70pt

# Icon sizing within cards
ICON_SIZE = 762_000      # 60pt
ICON_MARGIN = 127_000    # 10pt

# Card internal padding
CARD_PAD = 190_500       # 15pt

# Spacing between cards
CARD_GAP = 254_000       # 20pt

PT = 12_700  # 1 point in EMU


@dataclass
class SlotPosition:
    """A rectangular region on the slide (EMU coordinates)."""
    x: int
    y: int
    width: int
    height: int

    def as_emu_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)


@dataclass
class SlotLayout:
    """Complete layout specification for a single slide."""
    background: SlotPosition = field(
        default_factory=lambda: SlotPosition(0, 0, SLIDE_W, SLIDE_H)
    )
    overlay: SlotPosition = field(
        default_factory=lambda: SlotPosition(0, 0, SLIDE_W, SLIDE_H)
    )
    title: SlotPosition = field(
        default_factory=lambda: SlotPosition(MARGIN_X, TITLE_Y, CONTENT_W, TITLE_H)
    )
    subtitle: SlotPosition | None = None
    cards: list[SlotPosition] = field(default_factory=list)
    card_icons: list[SlotPosition] = field(default_factory=list)
    card_titles: list[SlotPosition] = field(default_factory=list)
    card_bodies: list[SlotPosition] = field(default_factory=list)
    footer: SlotPosition | None = None
    formula: SlotPosition | None = None


def _make_card_columns(
    n: int,
    top: int = CARD_TOP,
    height: int = CARD_H,
    left: int = MARGIN_X,
    total_width: int = CONTENT_W,
) -> tuple[list[SlotPosition], list[SlotPosition], list[SlotPosition], list[SlotPosition]]:
    """Generate n equal-width card columns with icons, titles, and bodies.

    Returns: (cards, icons, titles, bodies)
    """
    if n <= 0:
        return [], [], [], []

    gap = CARD_GAP
    card_w = (total_width - gap * (n - 1)) // n

    cards, icons, titles, bodies = [], [], [], []
    for i in range(n):
        cx = left + i * (card_w + gap)

        cards.append(SlotPosition(cx, top, card_w, height))

        # Icon: centered at top of card
        icon_x = cx + (card_w - ICON_SIZE) // 2
        icon_y = top + CARD_PAD
        icons.append(SlotPosition(icon_x, icon_y, ICON_SIZE, ICON_SIZE))

        # Title: below icon
        title_y = icon_y + ICON_SIZE + ICON_MARGIN
        title_h = 381_000  # 30pt
        titles.append(SlotPosition(
            cx + CARD_PAD, title_y, card_w - 2 * CARD_PAD, title_h
        ))

        # Body: below title, fill remaining card space
        body_y = title_y + title_h + ICON_MARGIN
        body_h = top + height - body_y - CARD_PAD
        bodies.append(SlotPosition(
            cx + CARD_PAD, body_y, card_w - 2 * CARD_PAD, max(body_h, 200_000)
        ))

    return cards, icons, titles, bodies


# ── Layout template functions ──────────────────────────────────────


def layout_cover(n_cards: int = 3) -> SlotLayout:
    """Cover slide: title + subtitle + N cards + formula footer."""
    cards, icons, titles, bodies = _make_card_columns(n_cards)
    return SlotLayout(
        subtitle=SlotPosition(MARGIN_X, SUBTITLE_Y, CONTENT_W, SUBTITLE_H),
        cards=cards,
        card_icons=icons,
        card_titles=titles,
        card_bodies=bodies,
        formula=SlotPosition(MARGIN_X, FOOTER_Y, CONTENT_W, FOOTER_H),
    )


def layout_lead_in(n_cards: int = 4) -> SlotLayout:
    """Lead-in slide: question title + N cards + footer."""
    cards, icons, titles, bodies = _make_card_columns(n_cards)
    return SlotLayout(
        subtitle=SlotPosition(MARGIN_X, SUBTITLE_Y, CONTENT_W, SUBTITLE_H),
        cards=cards,
        card_icons=icons,
        card_titles=titles,
        card_bodies=bodies,
        footer=SlotPosition(MARGIN_X, FOOTER_Y, CONTENT_W, FOOTER_H),
    )


def layout_definition(n_cards: int = 3) -> SlotLayout:
    """Definition slide: title + definition box + term cards + callouts."""
    # Definition box spans top area
    def_box = SlotPosition(MARGIN_X, SUBTITLE_Y, CONTENT_W, 508_000)  # 40pt

    # Cards below definition
    card_top = SUBTITLE_Y + 508_000 + CARD_GAP
    card_h = FOOTER_Y - card_top - CARD_GAP
    cards, icons, titles, bodies = _make_card_columns(
        n_cards, top=card_top, height=card_h
    )
    layout = SlotLayout(
        subtitle=def_box,
        cards=cards,
        card_icons=icons,
        card_titles=titles,
        card_bodies=bodies,
        footer=SlotPosition(MARGIN_X, FOOTER_Y, CONTENT_W, FOOTER_H),
    )
    return layout


def layout_content(n_cards: int = 3) -> SlotLayout:
    """Generic content slide with N card columns."""
    cards, icons, titles, bodies = _make_card_columns(n_cards)
    return SlotLayout(
        cards=cards,
        card_icons=icons,
        card_titles=titles,
        card_bodies=bodies,
        footer=SlotPosition(MARGIN_X, FOOTER_Y, CONTENT_W, FOOTER_H),
    )


def layout_example(n_cards: int = 2) -> SlotLayout:
    """Example slide with side-by-side example panels."""
    # Taller cards for examples
    card_top = SUBTITLE_Y + CARD_GAP
    card_h = FOOTER_Y - card_top - CARD_GAP
    cards, icons, titles, bodies = _make_card_columns(
        n_cards, top=card_top, height=card_h
    )
    return SlotLayout(
        cards=cards,
        card_icons=icons,
        card_titles=titles,
        card_bodies=bodies,
    )


def layout_exercise(n_cards: int = 3) -> SlotLayout:
    """Exercise slide with difficulty-graded columns."""
    subtitle = SlotPosition(MARGIN_X, SUBTITLE_Y, CONTENT_W, SUBTITLE_H)
    card_top = SUBTITLE_Y + SUBTITLE_H + CARD_GAP
    card_h = FOOTER_Y - card_top - CARD_GAP
    cards, icons, titles, bodies = _make_card_columns(
        n_cards, top=card_top, height=card_h
    )
    return SlotLayout(
        subtitle=subtitle,
        cards=cards,
        card_icons=icons,
        card_titles=titles,
        card_bodies=bodies,
    )


def layout_summary(n_cards: int = 5) -> SlotLayout:
    """Summary slide with compact cards + footer thought."""
    # Smaller cards for summary
    card_h = 1_905_000  # 150pt — more compact
    cards, icons, titles, bodies = _make_card_columns(
        n_cards, height=card_h
    )
    return SlotLayout(
        cards=cards,
        card_icons=icons,
        card_titles=titles,
        card_bodies=bodies,
        footer=SlotPosition(MARGIN_X, FOOTER_Y, CONTENT_W, FOOTER_H),
    )


def layout_closing() -> SlotLayout:
    """Closing slide — centered title + subtitle, no cards."""
    return SlotLayout(
        title=SlotPosition(
            MARGIN_X,
            SLIDE_H // 2 - 762_000,  # Centered vertically
            CONTENT_W,
            762_000,
        ),
        subtitle=SlotPosition(
            MARGIN_X,
            SLIDE_H // 2 + 127_000,
            CONTENT_W,
            508_000,
        ),
    )


# ── Dispatcher ─────────────────────────────────────────────────────

_LAYOUT_MAP = {
    "cover": layout_cover,
    "lead_in": layout_lead_in,
    "definition": layout_definition,
    "content": layout_content,
    "history": layout_content,
    "proof": layout_content,
    "example": layout_example,
    "exercise": layout_exercise,
    "answer": layout_exercise,
    "summary": layout_summary,
    "extension": layout_content,
    "closing": layout_closing,
}


def get_layout(slide_type: str, n_cards: int) -> SlotLayout:
    """Get the layout for a slide type with the given card count."""
    func = _LAYOUT_MAP.get(slide_type, layout_content)
    if slide_type == "closing":
        return func()
    return func(n_cards)
