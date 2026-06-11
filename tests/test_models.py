"""Tests for V2 data models."""

import json

import pytest

from edupptx.models import (
    GeneratedSlide,
    ImageNeed,
    IMAGE_RATIO_SIZES,
    IMAGE_RATIO_VALUES,
    InputContext,
    MaterialNeeds,
    PagePlan,
    PlanningDraft,
    PlanningMeta,
    SlideAssets,
    VisualPlan,
    match_aspect_ratio,
    normalize_image_aspect_ratio,
    parse_aspect_ratio,
)


class TestImageNeed:
    def test_normalizes_source_aliases(self):
        assert ImageNeed(query="old photo", source="public_domain").source == "search"
        assert ImageNeed(query="new illustration", source="seedream").source == "ai_generate"
        assert ImageNeed(query="default illustration").source == "ai_generate"


class TestImageAspectRatios:
    def test_ratio_size_and_value_keys_match(self):
        assert tuple(IMAGE_RATIO_VALUES.keys()) == tuple(IMAGE_RATIO_SIZES.keys())

    def test_parse_ratio_string(self):
        assert parse_aspect_ratio("32:15") == pytest.approx(32 / 15)
        assert parse_aspect_ratio(" 4 : 3 ") == pytest.approx(4 / 3)

    def test_parse_invalid_ratio_returns_none(self):
        assert parse_aspect_ratio("wide") is None
        assert parse_aspect_ratio("4:0") is None
        assert parse_aspect_ratio("") is None

    def test_normalize_supported_ratio_unchanged(self):
        assert normalize_image_aspect_ratio("4:3") == "4:3"
        assert normalize_image_aspect_ratio("16:9") == "16:9"

    def test_normalize_unknown_ratio_to_nearest_supported(self):
        assert normalize_image_aspect_ratio("32:15") == "16:9"
        assert normalize_image_aspect_ratio("5:4") == "4:3"
        assert normalize_image_aspect_ratio("2:3") == "3:4"

    def test_normalize_invalid_ratio_to_default(self):
        assert normalize_image_aspect_ratio("not-a-ratio") == "16:9"
        assert normalize_image_aspect_ratio(None) == "16:9"

    def test_match_aspect_ratio_uses_same_supported_set(self):
        assert match_aspect_ratio(472, 210) == "16:9"
        assert match_aspect_ratio(400, 300) == "4:3"


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

    def test_visual_plan_new_fields(self):
        vp = VisualPlan(
            primary_color="#1E40AF",
            secondary_bg_color="#F1F5F9",
            content_density="review",
        )
        assert vp.secondary_bg_color == "#F1F5F9"
        assert vp.content_density == "review"

    def test_visual_plan_defaults_backward_compatible(self):
        vp = VisualPlan()
        assert vp.secondary_bg_color == "#F8FAFC"
        assert vp.content_density == "lecture"


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
