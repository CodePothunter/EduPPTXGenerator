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

# Material area proportions
MAT_RATIO = 0.45      # material area takes 45% of content width
MAT_GAP_RATIO = 0.05  # gap between material and cards

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
    material_slot: SlotPosition | None = None


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


def layout_big_quote() -> SlotLayout:
    """Big quote slide — centered large quote + source footer, no cards."""
    return SlotLayout(
        title=SlotPosition(
            MARGIN_X,
            int(SLIDE_H * 0.3),
            CONTENT_W,
            TITLE_H * 2,  # large area for quote text
        ),
        footer=SlotPosition(MARGIN_X, FOOTER_Y, CONTENT_W, FOOTER_H),
    )


def layout_full_image() -> SlotLayout:
    """Full image slide — title + material_slot filling content area, no cards."""
    return SlotLayout(
        material_slot=SlotPosition(
            MARGIN_X, CARD_TOP, CONTENT_W, FOOTER_Y - CARD_TOP
        ),
    )


def layout_image_left(n_cards: int = 2) -> SlotLayout:
    """Image-left slide — material on left 45%, cards on right 50%."""
    content_h = FOOTER_Y - CARD_TOP

    mat_w = int(CONTENT_W * MAT_RATIO)
    mat_gap = int(CONTENT_W * MAT_GAP_RATIO)
    cards_x = MARGIN_X + mat_w + mat_gap
    cards_w = CONTENT_W - mat_w - mat_gap

    cards, icons, titles, bodies = _make_card_columns(
        n_cards, top=CARD_TOP, height=content_h,
        left=cards_x, total_width=cards_w,
    )
    return SlotLayout(
        material_slot=SlotPosition(MARGIN_X, CARD_TOP, mat_w, content_h),
        cards=cards,
        card_icons=icons,
        card_titles=titles,
        card_bodies=bodies,
        footer=SlotPosition(MARGIN_X, FOOTER_Y, CONTENT_W, FOOTER_H),
    )


def layout_image_right(n_cards: int = 2) -> SlotLayout:
    """Image-right slide — cards on left 50%, material on right 45%."""
    content_h = FOOTER_Y - CARD_TOP

    mat_w = int(CONTENT_W * MAT_RATIO)
    mat_gap = int(CONTENT_W * MAT_GAP_RATIO)
    cards_w = CONTENT_W - mat_w - mat_gap
    mat_x = MARGIN_X + cards_w + mat_gap

    cards, icons, titles, bodies = _make_card_columns(
        n_cards, top=CARD_TOP, height=content_h,
        left=MARGIN_X, total_width=cards_w,
    )
    return SlotLayout(
        material_slot=SlotPosition(mat_x, CARD_TOP, mat_w, content_h),
        cards=cards,
        card_icons=icons,
        card_titles=titles,
        card_bodies=bodies,
        footer=SlotPosition(MARGIN_X, FOOTER_Y, CONTENT_W, FOOTER_H),
    )


def layout_section() -> SlotLayout:
    """Section transition slide — centered title + subtitle, no cards."""
    return SlotLayout(
        title=SlotPosition(
            MARGIN_X,
            SLIDE_H // 2 - 952_500,  # slightly higher than closing
            CONTENT_W,
            762_000,
        ),
        subtitle=SlotPosition(
            MARGIN_X,
            SLIDE_H // 2 - 63_500,
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
    "big_quote": layout_big_quote,
    "full_image": layout_full_image,
    "image_left": layout_image_left,
    "image_right": layout_image_right,
    "section": layout_section,
}


def get_layout(slide_type: str, card_count: int, material_position: str | None = None) -> SlotLayout:
    """Get the layout for a slide type with the given card count.

    material_position: full | left | right | center | None
    """
    func = _LAYOUT_MAP.get(slide_type, layout_content)
    if slide_type in ("closing", "big_quote", "full_image", "section"):
        layout = func()
    else:
        layout = func(card_count)

    if material_position is None:
        return layout

    # Content area boundaries (EMU)
    content_x = MARGIN_X                      # 80pt
    content_y = CARD_TOP                      # 170pt
    content_w = CONTENT_W                     # 800pt
    content_h = FOOTER_Y - CARD_TOP           # 420pt - 170pt = 250pt

    if material_position == "full":
        layout.material_slot = SlotPosition(content_x, content_y, content_w, content_h)
        layout.cards = []
        layout.card_icons = []
        layout.card_titles = []
        layout.card_bodies = []

    elif material_position == "left":
        # Material takes left 45%, cards squeezed into right 50%
        mat_w = int(content_w * MAT_RATIO)
        mat_gap = int(content_w * MAT_GAP_RATIO)
        cards_x = content_x + mat_w + mat_gap
        cards_w = content_w - mat_w - mat_gap
        layout.material_slot = SlotPosition(content_x, content_y, mat_w, content_h)
        cards, icons, titles, bodies = _make_card_columns(
            card_count, top=content_y, height=content_h,
            left=cards_x, total_width=cards_w,
        )
        layout.cards = cards
        layout.card_icons = icons
        layout.card_titles = titles
        layout.card_bodies = bodies

    elif material_position == "right":
        # Material takes right 45%, cards squeezed into left 50%
        mat_w = int(content_w * MAT_RATIO)
        cards_w = content_w - mat_w - int(content_w * MAT_GAP_RATIO)
        mat_x = content_x + cards_w + int(content_w * MAT_GAP_RATIO)
        layout.material_slot = SlotPosition(mat_x, content_y, mat_w, content_h)
        cards, icons, titles, bodies = _make_card_columns(
            card_count, top=content_y, height=content_h,
            left=content_x, total_width=cards_w,
        )
        layout.cards = cards
        layout.card_icons = icons
        layout.card_titles = titles
        layout.card_bodies = bodies

    elif material_position == "center":
        # Material placed between title and cards, using ~35% of content height
        mat_h = int(content_h * 0.35)
        mat_y = content_y
        cards_top = mat_y + mat_h + CARD_GAP
        cards_h = FOOTER_Y - cards_top - CARD_GAP
        layout.material_slot = SlotPosition(content_x, mat_y, content_w, mat_h)
        cards, icons, titles, bodies = _make_card_columns(
            card_count, top=cards_top, height=cards_h,
        )
        layout.cards = cards
        layout.card_icons = icons
        layout.card_titles = titles
        layout.card_bodies = bodies

    return layout
