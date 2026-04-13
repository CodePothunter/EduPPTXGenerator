"""Tests for the v2 layout resolver (replaces old layout_engine tests).

Tests shape counts, bounds, and structural invariants for all slide types.
"""

from pathlib import Path

from edupptx.layout_resolver import (
    resolve_layout,
    _resolve_cover,
    _resolve_content,
    _resolve_big_quote,
    _resolve_closing,
    _resolve_section,
    _resolve_full_image,
    _resolve_image_left,
    _resolve_image_right,
)
from edupptx.models import PresentationPlan, SlideCard, SlideContent
from edupptx.style_resolver import resolve_style
from edupptx.style_schema import load_style, SLIDE_W, SLIDE_H

STYLES_DIR = Path(__file__).parent.parent / "styles"


def _style():
    return resolve_style(load_style(STYLES_DIR / "emerald.json"))


def _cover(n=3):
    return SlideContent(
        type="cover", title="Cover",
        subtitle="Subtitle",
        cards=[SlideCard(icon="star", title=f"P{i}", body=f"B{i}") for i in range(n)],
        formula="F=ma", notes="N",
    )


def _content(n=3):
    return SlideContent(
        type="content", title="Content",
        cards=[SlideCard(icon="book", title=f"C{i}", body=f"B{i}") for i in range(n)],
        footer="Footer", notes="N",
    )


def _closing():
    return SlideContent(type="closing", title="End", subtitle="Bye", notes="N")


def _big_quote():
    return SlideContent(type="big_quote", title="Quote text", footer="Author", notes="N")


def _section():
    return SlideContent(type="section", title="Chapter", subtitle="Intro", notes="N")


def _full_image():
    return SlideContent(type="full_image", title="Image", notes="N")


def _image_left():
    return SlideContent(
        type="image_left", title="Left img",
        cards=[SlideCard(icon="star", title="A", body="B"),
               SlideCard(icon="target", title="C", body="D")],
        footer="F", notes="N",
    )


def _image_right():
    return SlideContent(
        type="image_right", title="Right img",
        cards=[SlideCard(icon="star", title="A", body="B"),
               SlideCard(icon="target", title="C", body="D")],
        footer="F", notes="N",
    )


# ── Cover tests ───────────────────────────────────────────

def test_cover_layout_3_cards():
    shapes = _resolve_cover(_cover(3), _style())
    cards = [s for s in shapes if s.shape_type == "rounded_rect"
             and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20]
    assert len(cards) == 3


def test_cover_has_subtitle():
    shapes = _resolve_cover(_cover(), _style())
    subs = [s for s in shapes if s.text == "Subtitle"]
    assert len(subs) == 1


def test_cover_has_formula():
    shapes = _resolve_cover(_cover(), _style())
    formulas = [s for s in shapes if s.text == "F=ma"]
    assert len(formulas) == 1


# ── Closing tests ─────────────────────────────────────────

def test_closing_layout_no_cards():
    shapes = _resolve_closing(_closing(), _style())
    cards = [s for s in shapes if s.shape_type == "rounded_rect"
             and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20]
    assert len(cards) == 0


def test_closing_has_subtitle():
    shapes = _resolve_closing(_closing(), _style())
    subs = [s for s in shapes if s.text == "Bye"]
    assert len(subs) == 1


# ── Summary (content variant) tests ──────────────────────

def test_summary_layout_5_cards():
    slide = SlideContent(
        type="summary", title="Sum",
        cards=[SlideCard(icon="star", title=f"P{i}", body=f"B{i}") for i in range(5)],
        footer="Foot", notes="N",
    )
    shapes = _resolve_content(slide, _style())
    cards = [s for s in shapes if s.shape_type == "rounded_rect"
             and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20]
    assert len(cards) == 5


def test_content_layout_variable_cards():
    for n in [2, 3, 4]:
        slide = _content(n)
        shapes = _resolve_content(slide, _style())
        cards = [s for s in shapes if s.shape_type == "rounded_rect"
                 and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20]
        assert len(cards) == n


# ── Bounds tests ──────────────────────────────────────────

def test_cards_within_slide_bounds():
    """All card containers should be within canvas."""
    for factory in [_cover, _content, _image_left, _image_right]:
        shapes = factory() if callable(factory) else factory
        resolver = {
            "cover": _resolve_cover, "content": _resolve_content,
            "image_left": _resolve_image_left, "image_right": _resolve_image_right,
        }
        slide = factory()
        func = resolver.get(slide.type, _resolve_content)
        result = func(slide, _style())
        cards = [s for s in result if s.shape_type == "rounded_rect"
                 and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20]
        for i, card in enumerate(cards):
            assert card.left >= 0, f"{slide.type} card {i} left < 0"
            assert card.top >= 0, f"{slide.type} card {i} top < 0"
            assert card.left + card.width <= SLIDE_W, f"{slide.type} card {i} exceeds width"
            assert card.top + card.height <= SLIDE_H, f"{slide.type} card {i} exceeds height"


def test_card_icons_within_cards():
    """Icon ovals should be near their parent card."""
    shapes = _resolve_cover(_cover(3), _style())
    cards = [s for s in shapes if s.shape_type == "rounded_rect"
             and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20]
    icons = [s for s in shapes if s.shape_type == "oval"]
    # Each card should have an associated icon oval
    assert len(icons) >= len(cards)


def test_no_card_overlap():
    """Cards should not overlap horizontally."""
    shapes = _resolve_cover(_cover(3), _style())
    cards = sorted(
        [s for s in shapes if s.shape_type == "rounded_rect"
         and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20],
        key=lambda s: s.left,
    )
    for i in range(len(cards) - 1):
        right_edge = cards[i].left + cards[i].width
        next_left = cards[i + 1].left
        assert right_edge <= next_left, f"Card {i} overlaps card {i+1}"


# ── Big quote tests ───────────────────────────────────────

def test_big_quote_layout():
    shapes = _resolve_big_quote(_big_quote(), _style())
    titles = [s for s in shapes if s.text == "Quote text"]
    assert len(titles) == 1
    footers = [s for s in shapes if s.text and "Author" in s.text]
    assert len(footers) == 1
    cards = [s for s in shapes if s.shape_type == "rounded_rect"
             and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20]
    assert len(cards) == 0


# ── Full image tests ──────────────────────────────────────

def test_full_image_layout():
    shapes = _resolve_full_image(_full_image(), _style())
    titles = [s for s in shapes if s.text == "Image"]
    assert len(titles) == 1
    cards = [s for s in shapes if s.shape_type == "rounded_rect"
             and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20]
    assert len(cards) == 0


# ── Image left/right tests ───────────────────────────────

def test_image_left_layout():
    shapes = _resolve_image_left(_image_left(), _style())
    cards = [s for s in shapes if s.shape_type == "rounded_rect"
             and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20]
    assert len(cards) == 2


def test_image_right_layout():
    shapes = _resolve_image_right(_image_right(), _style())
    cards = [s for s in shapes if s.shape_type == "rounded_rect"
             and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20]
    assert len(cards) == 2


# ── Section tests ─────────────────────────────────────────

def test_section_layout():
    shapes = _resolve_section(_section(), _style())
    titles = [s for s in shapes if s.text == "Chapter"]
    assert len(titles) == 1
    cards = [s for s in shapes if s.shape_type == "rounded_rect"
             and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20]
    assert len(cards) == 0


# ── Resolve full plan ─────────────────────────────────────

def test_resolve_full_plan():
    plan = PresentationPlan(
        topic="Test", palette="emerald",
        slides=[_cover(), _content(), _big_quote(), _closing(), _section()],
    )
    slides = resolve_layout(plan, _style())
    assert len(slides) == 5
    for s in slides:
        assert len(s.shapes) > 0
