"""Tests for the layout engine."""

from edupptx.layout_engine import (
    CONTENT_W,
    MARGIN_X,
    SLIDE_H,
    SLIDE_W,
    get_layout,
    layout_big_quote,
    layout_closing,
    layout_content,
    layout_cover,
    layout_full_image,
    layout_image_left,
    layout_image_right,
    layout_section,
    layout_summary,
)


def test_cover_layout_3_cards():
    layout = layout_cover(3)
    assert len(layout.cards) == 3
    assert len(layout.card_icons) == 3
    assert len(layout.card_titles) == 3
    assert len(layout.card_bodies) == 3
    assert layout.formula is not None
    assert layout.subtitle is not None


def test_closing_layout_no_cards():
    layout = layout_closing()
    assert len(layout.cards) == 0
    assert layout.subtitle is not None


def test_summary_layout_5_cards():
    layout = layout_summary(5)
    assert len(layout.cards) == 5
    assert layout.footer is not None


def test_content_layout_variable_cards():
    for n in [2, 3, 4]:
        layout = layout_content(n)
        assert len(layout.cards) == n


def test_cards_within_slide_bounds():
    """All card positions should be within the slide canvas."""
    for slide_type in ["cover", "lead_in", "definition", "content", "example",
                       "exercise", "summary", "extension", "closing",
                       "big_quote", "full_image", "image_left", "image_right",
                       "section"]:
        if slide_type in ("closing", "big_quote", "full_image", "section"):
            n_cards = 0
        else:
            n_cards = 3
        layout = get_layout(slide_type, n_cards)

        for i, card in enumerate(layout.cards):
            assert card.x >= 0, f"{slide_type} card {i} x < 0"
            assert card.y >= 0, f"{slide_type} card {i} y < 0"
            assert card.x + card.width <= SLIDE_W, (
                f"{slide_type} card {i} exceeds slide width"
            )
            assert card.y + card.height <= SLIDE_H, (
                f"{slide_type} card {i} exceeds slide height"
            )


def test_card_icons_within_cards():
    """Icon slots should be within their parent card."""
    layout = layout_cover(3)
    for i in range(3):
        card = layout.cards[i]
        icon = layout.card_icons[i]
        assert icon.x >= card.x, f"Icon {i} x before card"
        assert icon.y >= card.y, f"Icon {i} y before card"
        assert icon.x + icon.width <= card.x + card.width, f"Icon {i} exceeds card width"


def test_no_card_overlap():
    """Cards should not overlap horizontally."""
    layout = layout_cover(3)
    for i in range(len(layout.cards) - 1):
        right_edge = layout.cards[i].x + layout.cards[i].width
        next_left = layout.cards[i + 1].x
        assert right_edge <= next_left, f"Card {i} overlaps card {i+1}"


def test_content_layout_with_full_material():
    layout = get_layout("content", 0, material_position="full")
    assert layout.material_slot is not None
    assert layout.material_slot.width > 0
    assert len(layout.cards) == 0


def test_content_layout_with_left_material():
    layout = get_layout("content", 2, material_position="left")
    assert layout.material_slot is not None
    assert layout.material_slot.x < layout.cards[0].x


def test_content_layout_with_center_material():
    layout = get_layout("content", 2, material_position="center")
    assert layout.material_slot is not None
    assert layout.material_slot.y > layout.title.y
    assert layout.material_slot.y < layout.cards[0].y


def test_layout_without_material_unchanged():
    """Existing layouts should be unchanged when material_position is None."""
    layout = get_layout("content", 3)
    assert layout.material_slot is None
    assert len(layout.cards) == 3


# ── New layout tests ──────────────────────────────────────────────


def test_big_quote_layout():
    """big_quote: title and footer exist, no cards."""
    layout = layout_big_quote()
    assert layout.title is not None
    assert layout.footer is not None
    assert len(layout.cards) == 0


def test_full_image_layout():
    """full_image: material_slot exists with full content width, no cards."""
    layout = layout_full_image()
    assert layout.material_slot is not None
    assert layout.material_slot.width == CONTENT_W
    assert len(layout.cards) == 0


def test_image_left_layout():
    """image_left: material_slot on left, cards on right, no overlap."""
    layout = layout_image_left(2)
    assert layout.material_slot is not None
    assert layout.material_slot.x == MARGIN_X
    assert len(layout.cards) == 2
    mat_right = layout.material_slot.x + layout.material_slot.width
    assert layout.cards[0].x > mat_right, "Cards must be to the right of material_slot"


def test_image_right_layout():
    """image_right: cards on left, material_slot on right."""
    layout = layout_image_right(2)
    assert layout.material_slot is not None
    assert len(layout.cards) == 2
    # Cards should be on the left side
    cards_right = layout.cards[-1].x + layout.cards[-1].width
    assert layout.material_slot.x > cards_right, "Material must be to the right of cards"


def test_section_layout():
    """section: title exists, no cards."""
    layout = layout_section()
    assert layout.title is not None
    assert len(layout.cards) == 0
