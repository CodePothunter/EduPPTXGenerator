from edupptx.materials.image_prompt_router import build_routed_image_needs
from edupptx.models import ImageNeed, MaterialNeeds, PagePlan, PlanningDraft, PlanningMeta, StyleRouting


def test_routed_ai_image_need_preserves_semantic_query_and_adds_generation_prompt():
    draft = PlanningDraft(
        meta=PlanningMeta(topic="lesson"),
        style_routing=StyleRouting(template_family="unknown"),
        pages=[],
    )
    page = PagePlan(
        page_number=1,
        page_type="content",
        title="Intro",
        material_needs=MaterialNeeds(
            images=[ImageNeed(query="author portrait", source="ai_generate", role="illustration", aspect_ratio="1:1")]
        ),
    )

    routed = build_routed_image_needs(draft, page)

    assert routed[0].query == "author portrait"
    assert routed[0].generation_prompt
    assert routed[0].generation_prompt.startswith("author portrait")
    assert routed[0].prompt_route["role_prompt_terms"]
    assert routed[0].prompt_route["aspect_ratio_prompt_terms"]
