"""Tests for layout resolution — plan + style -> list[ResolvedSlide]."""

import pytest
from pathlib import Path

from edupptx.layout_resolver import resolve_layout, _resolve_cover, _resolve_content, _resolve_big_quote
from edupptx.models import PresentationPlan, SlideContent, SlideCard
from edupptx.style_resolver import resolve_style
from edupptx.style_schema import load_style, SLIDE_W, SLIDE_H


STYLES_DIR = Path(__file__).parent.parent / "styles"


def _style():
    return resolve_style(load_style(STYLES_DIR / "emerald.json"))


def _cover_slide():
    return SlideContent(
        type="cover",
        title="Test Cover Title",
        subtitle="Test subtitle",
        cards=[
            SlideCard(icon="star", title="Point 1", body="Description 1"),
            SlideCard(icon="target", title="Point 2", body="Description 2"),
            SlideCard(icon="check", title="Point 3", body="Description 3"),
        ],
        formula="E = mc²",
        notes="Speaker notes",
    )


def _content_slide():
    return SlideContent(
        type="content",
        title="Content Title",
        cards=[
            SlideCard(icon="book", title="Card A", body="Body A text"),
            SlideCard(icon="globe", title="Card B", body="Body B text"),
        ],
        footer="Footer summary",
        notes="Notes here",
    )


def _big_quote_slide():
    return SlideContent(
        type="big_quote",
        title="The only way to do great work is to love what you do.",
        footer="— Steve Jobs",
        notes="Motivational quote",
    )


def _simple_plan():
    return PresentationPlan(
        topic="Test",
        palette="emerald",
        slides=[_cover_slide(), _content_slide(), _big_quote_slide()],
    )


class TestResolveLayout:
    def test_produces_correct_slide_count(self):
        plan = _simple_plan()
        slides = resolve_layout(plan, _style())
        assert len(slides) == 3

    def test_all_slides_have_shapes(self):
        slides = resolve_layout(_simple_plan(), _style())
        for s in slides:
            assert len(s.shapes) > 0

    def test_notes_preserved(self):
        slides = resolve_layout(_simple_plan(), _style())
        assert slides[0].notes == "Speaker notes"
        assert slides[2].notes == "Motivational quote"


class TestCoverSlide:
    def test_has_title_textbox(self):
        shapes = _resolve_cover(_cover_slide(), _style())
        titles = [s for s in shapes if s.shape_type == "textbox" and s.text == "Test Cover Title"]
        assert len(titles) == 1

    def test_has_subtitle(self):
        shapes = _resolve_cover(_cover_slide(), _style())
        subs = [s for s in shapes if s.text == "Test subtitle"]
        assert len(subs) == 1

    def test_has_3_card_containers(self):
        shapes = _resolve_cover(_cover_slide(), _style())
        # Filter out the content panel (alpha_pct < 100) to find actual card containers
        cards = [s for s in shapes if s.shape_type == "rounded_rect"
                 and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20]
        assert len(cards) == 3

    def test_has_formula_bar(self):
        shapes = _resolve_cover(_cover_slide(), _style())
        formulas = [s for s in shapes if s.text == "E = mc²"]
        assert len(formulas) == 1

    def test_all_shapes_within_bounds(self):
        shapes = _resolve_cover(_cover_slide(), _style())
        for s in shapes:
            assert s.left >= -200_000, f"Shape left={s.left} too far left"
            assert s.top >= -2_000_000, f"Shape top={s.top} too far up"  # decorations can be above
            assert s.left + s.width <= SLIDE_W + 200_000

    def test_has_icon_images(self):
        shapes = _resolve_cover(_cover_slide(), _style())
        icons = [s for s in shapes if s.shape_type == "image"]
        assert len(icons) == 3


class TestContentSlide:
    def test_has_title(self):
        shapes = _resolve_content(_content_slide(), _style())
        titles = [s for s in shapes if s.text == "Content Title"]
        assert len(titles) == 1

    def test_has_2_card_containers(self):
        shapes = _resolve_content(_content_slide(), _style())
        cards = [s for s in shapes if s.shape_type == "rounded_rect"
                 and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20]
        assert len(cards) == 2

    def test_has_footer(self):
        shapes = _resolve_content(_content_slide(), _style())
        footers = [s for s in shapes if s.text == "Footer summary"]
        assert len(footers) == 1

    def test_no_card_overlap(self):
        shapes = _resolve_content(_content_slide(), _style())
        cards = [s for s in shapes if s.shape_type == "rounded_rect"
                 and s.shadow is not None and s.alpha_pct == 100 and s.z_order >= 20]
        for i in range(len(cards)):
            for j in range(i + 1, len(cards)):
                assert cards[i].left + cards[i].width <= cards[j].left or \
                       cards[j].left + cards[j].width <= cards[i].left


class TestBigQuoteSlide:
    def test_has_quote_text(self):
        shapes = _resolve_big_quote(_big_quote_slide(), _style())
        quotes = [s for s in shapes if "great work" in (s.text or "")]
        assert len(quotes) == 1

    def test_has_footer_attribution(self):
        shapes = _resolve_big_quote(_big_quote_slide(), _style())
        footers = [s for s in shapes if s.text and "Steve Jobs" in s.text]
        assert len(footers) == 1

    def test_has_quote_decoration(self):
        shapes = _resolve_big_quote(_big_quote_slide(), _style())
        # Should have opening quote mark and accent bar
        decorations = [s for s in shapes if s.z_order <= 6]
        assert len(decorations) >= 2


class TestZOrdering:
    def test_panel_before_cards(self):
        shapes = _resolve_content(_content_slide(), _style())
        panels = [s for s in shapes if s.alpha_pct < 100]
        cards = [s for s in shapes if s.shape_type == "rounded_rect"
                 and s.alpha_pct == 100 and s.z_order >= 20]
        if panels and cards:
            assert panels[0].z_order < cards[0].z_order

    def test_title_before_cards(self):
        shapes = _resolve_content(_content_slide(), _style())
        titles = [s for s in shapes if s.text == "Content Title"]
        cards = [s for s in shapes if s.shape_type == "rounded_rect"
                 and s.alpha_pct == 100 and s.z_order >= 20]
        assert titles[0].z_order < cards[0].z_order
