"""Tests for the layout engine."""

from edupptx.layout_engine import (
    SLIDE_H,
    SLIDE_W,
    get_layout,
    layout_closing,
    layout_content,
    layout_cover,
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
                       "exercise", "summary", "extension", "closing"]:
        n_cards = 3 if slide_type != "closing" else 0
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
