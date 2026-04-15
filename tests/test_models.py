"""Tests for V2 data models."""

import json

import pytest

from edupptx.models import (
    GeneratedSlide,
    ImageNeed,
    InputContext,
    MaterialNeeds,
    PagePlan,
    PlanningDraft,
    PlanningMeta,
    SlideAssets,
    VisualPlan,
)


class TestInputContext:
    def test_defaults(self):
        ctx = InputContext(topic="勾股定理")
        assert ctx.topic == "勾股定理"
        assert ctx.source_text is None
        assert ctx.research_summary is None
        assert ctx.requirements == ""

    def test_with_all_fields(self):
        ctx = InputContext(
            topic="光合作用",
            source_text="Some text",
            research_summary="Summary",
            requirements="高中生",
        )
        assert ctx.source_text == "Some text"
        assert ctx.requirements == "高中生"


class TestPagePlan:
    def test_defaults(self):
        page = PagePlan(page_number=1, page_type="cover", title="Hello")
        assert page.layout_hint == "mixed_grid"
        assert page.content_points == []
        assert page.design_notes == ""
        assert page.notes == ""

    def test_with_material_needs(self):
        page = PagePlan(
            page_number=2,
            page_type="content",
            title="Intro",
            material_needs=MaterialNeeds(
                images=[ImageNeed(query="tree", source="search")],
                icons=["leaf", "sun"],
            ),
        )
        assert len(page.material_needs.images) == 1
        assert page.material_needs.icons == ["leaf", "sun"]


class TestPlanningDraft:
    def test_serialization_roundtrip(self):
        draft = PlanningDraft(
            meta=PlanningMeta(topic="勾股定理", total_pages=3),
            pages=[
                PagePlan(page_number=1, page_type="cover", title="封面"),
                PagePlan(page_number=2, page_type="content", title="证明"),
                PagePlan(page_number=3, page_type="closing", title="总结"),
            ],
        )
        data = draft.model_dump()
        restored = PlanningDraft.model_validate(data)
        assert restored.meta.topic == "勾股定理"
        assert len(restored.pages) == 3
        assert restored.pages[0].page_type == "cover"

    def test_json_roundtrip(self):
        draft = PlanningDraft(
            meta=PlanningMeta(topic="光合作用"),
            visual=VisualPlan(primary_color="#10B981"),
            pages=[PagePlan(page_number=1, page_type="content", title="T")],
        )
        json_str = draft.model_dump_json()
        restored = PlanningDraft.model_validate_json(json_str)
        assert restored.visual.primary_color == "#10B981"

    def test_visual_defaults(self):
        draft = PlanningDraft(
            meta=PlanningMeta(topic="test"),
            pages=[],
        )
        assert draft.visual.primary_color == "#1E40AF"
        assert draft.visual.card_bg_color == "#FFFFFF"


class TestVisualPlan:
    def test_all_fields(self):
        vp = VisualPlan(
            primary_color="#FF0000",
            secondary_color="#00FF00",
            accent_color="#0000FF",
            background_prompt="a starry sky",
            card_bg_color="#F0F0F0",
            text_color="#333333",
            heading_color="#111111",
        )
        assert vp.background_prompt == "a starry sky"


class TestSlideAssets:
    def test_defaults(self):
        assets = SlideAssets(page_number=1)
        assert assets.background_path is None
        assert assets.image_paths == {}
        assert assets.icon_svgs == {}


class TestGeneratedSlide:
    def test_basic(self):
        slide = GeneratedSlide(
            page_number=1,
            svg_content='<svg viewBox="0 0 1280 720"></svg>',
        )
        assert slide.svg_path is None
        assert "1280" in slide.svg_content
