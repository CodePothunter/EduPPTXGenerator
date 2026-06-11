from edupptx.design.template_router import (
    ImageSlotSpec,
    PlannerPageSpec,
    TemplateManifest,
    align_draft_to_template,
)
from edupptx.models import ImageNeed, MaterialNeeds, PagePlan, PlanningDraft, PlanningMeta


def test_align_draft_to_template_preserves_exercise_asset_aspect_ratio():
    manifest = TemplateManifest(
        style_name="test",
        template_family="test",
        planner_page_specs={
            "exercise": PlannerPageSpec(
                page_type="exercise",
                variant="exercise",
                preferred_layout_hints=["mixed_grid"],
                image_slots=[
                    ImageSlotSpec(
                        slot_id="exercise_right_tip",
                        role="illustration",
                        aspect_ratio="32:15",
                        source="search",
                    )
                ],
            )
        },
    )
    draft = PlanningDraft(
        meta=PlanningMeta(topic="geometry", subject="Math", grade="Grade 8"),
        pages=[
            PagePlan(
                page_number=1,
                page_type="exercise",
                title="Practice",
                layout_hint="mixed_grid",
                material_needs=MaterialNeeds(
                    images=[
                        ImageNeed(
                            query="database question image",
                            source="exercise_asset",
                            role="illustration",
                            path="materials/exercises/q_1_img.png",
                            aspect_ratio="3:4",
                        )
                    ]
                ),
                exercise_payloads=[
                    {
                        "exercise_id": "q_1",
                        "image_assets": [
                            {
                                "image_id": "img",
                                "path": "materials/exercises/q_1_img.png",
                                "aspect_ratio": "3:4",
                            }
                        ],
                    }
                ],
            )
        ],
    )

    align_draft_to_template(draft, manifest)

    image_need = draft.pages[0].material_needs.images[0]
    assert image_need.source == "exercise_asset"
    assert image_need.path == "materials/exercises/q_1_img.png"
    assert image_need.aspect_ratio == "3:4"
