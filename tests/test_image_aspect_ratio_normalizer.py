from edupptx.models import ImageNeed, MaterialNeeds, PagePlan, PlanningDraft, PlanningMeta
from edupptx.planning.image_aspect_ratio_normalizer import (
    ImageAspectRatioChange,
    normalize_draft_image_aspect_ratios,
)


def _draft_with_image(aspect_ratio: str) -> PlanningDraft:
    return PlanningDraft(
        meta=PlanningMeta(topic="geometry"),
        pages=[
            PagePlan(
                page_number=17,
                page_type="content",
                title="拓展练习",
                material_needs=MaterialNeeds(
                    images=[
                        ImageNeed(
                            query="geometry proof diagram",
                            source="ai_generate",
                            role="illustration",
                            aspect_ratio=aspect_ratio,
                        )
                    ]
                ),
            )
        ],
    )


def test_normalizes_unsupported_ratio_in_place():
    draft = _draft_with_image("32:15")

    changes = normalize_draft_image_aspect_ratios(draft)

    assert draft.pages[0].material_needs.images[0].aspect_ratio == "16:9"
    assert changes == [
        ImageAspectRatioChange(
            page_number=17,
            slot_key="illustration_1",
            role="illustration",
            original_ratio="32:15",
            normalized_ratio="16:9",
        )
    ]


def test_keeps_supported_ratio_and_returns_no_changes():
    draft = _draft_with_image("4:3")

    changes = normalize_draft_image_aspect_ratios(draft)

    assert draft.pages[0].material_needs.images[0].aspect_ratio == "4:3"
    assert changes == []


def test_invalid_ratio_normalizes_to_default():
    draft = _draft_with_image("wide")

    changes = normalize_draft_image_aspect_ratios(draft)

    assert draft.pages[0].material_needs.images[0].aspect_ratio == "16:9"
    assert changes[0].original_ratio == "wide"
    assert changes[0].normalized_ratio == "16:9"
