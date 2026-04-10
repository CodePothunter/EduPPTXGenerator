"""Tests for data models."""

from edupptx.models import BackgroundAction, ContentMaterial, MaterialEntry, PresentationPlan, SlideCard, SlideContent


def test_slide_card_creation():
    card = SlideCard(icon="triangle", title="Test", body="Body text")
    assert card.icon == "triangle"
    assert card.title == "Test"


def test_slide_content_defaults():
    slide = SlideContent(type="cover", title="Title", notes="Notes")
    assert slide.subtitle is None
    assert slide.cards == []
    assert slide.formula is None


def test_presentation_plan_serialization():
    plan = PresentationPlan(
        topic="Test",
        palette="emerald",
        slides=[
            SlideContent(
                type="cover",
                title="Cover",
                cards=[SlideCard(icon="star", title="A", body="B")],
                notes="Notes",
            )
        ],
    )
    data = plan.model_dump()
    assert data["topic"] == "Test"
    assert len(data["slides"]) == 1
    assert data["slides"][0]["cards"][0]["icon"] == "star"

    # Round-trip
    plan2 = PresentationPlan.model_validate(data)
    assert plan2.topic == plan.topic
    assert len(plan2.slides) == len(plan.slides)


def test_presentation_plan_from_json():
    """Test parsing the kind of JSON the LLM would return."""
    data = {
        "topic": "勾股定理",
        "palette": "blue",
        "slides": [
            {
                "type": "cover",
                "title": "勾股定理",
                "subtitle": "探索直角三角形的奥秘",
                "cards": [
                    {"icon": "triangle", "title": "几何", "body": "面积关系"},
                    {"icon": "calculator", "title": "代数", "body": "平方关系"},
                    {"icon": "target", "title": "应用", "body": "实际问题"},
                ],
                "formula": "a² + b² = c²",
                "notes": "开场介绍",
            },
            {
                "type": "closing",
                "title": "谢谢",
                "subtitle": "课程结束",
                "notes": "结束语",
            },
        ],
        "language": "zh",
    }
    plan = PresentationPlan.model_validate(data)
    assert len(plan.slides) == 2
    assert plan.slides[0].cards[0].icon == "triangle"
    assert plan.slides[1].type == "closing"


def test_material_entry_creation():
    entry = MaterialEntry(
        id="mat_0001",
        type="background",
        tags=["math", "geometry"],
        palette="emerald",
        source="programmatic",
        description="Diagonal gradient background",
        resolution=(1920, 1080),
        path="backgrounds/mat_0001_bg.jpg",
        created_at="2026-04-10T14:30:00",
    )
    assert entry.id == "mat_0001"
    assert entry.type == "background"
    assert entry.tags == ["math", "geometry"]


def test_material_entry_serialization():
    entry = MaterialEntry(
        id="mat_0002",
        type="diagram",
        tags=["biology"],
        palette="emerald",
        source="programmatic",
        description="Flowchart",
        resolution=(1200, 800),
        path="diagrams/mat_0002_flow.png",
        created_at="2026-04-10T14:30:00",
    )
    data = entry.model_dump()
    restored = MaterialEntry.model_validate(data)
    assert restored.id == entry.id
    assert restored.resolution == (1200, 800)


def test_background_action_generate():
    action = BackgroundAction(action="generate", style="diagonal_gradient", tags=["math"])
    assert action.action == "generate"
    assert action.material_id is None


def test_background_action_reuse():
    action = BackgroundAction(action="reuse", material_id="mat_0001")
    assert action.action == "reuse"
    assert action.style is None


def test_content_material_diagram():
    mat = ContentMaterial(
        action="generate_diagram",
        position="center",
        diagram_type="flowchart",
        diagram_data={"nodes": [{"id": "1", "label": "Start"}], "edges": [], "direction": "TB"},
    )
    assert mat.action == "generate_diagram"
    assert mat.diagram_type == "flowchart"


def test_slide_content_with_materials():
    slide = SlideContent(
        type="content",
        title="Test",
        notes="Notes",
        bg_action=BackgroundAction(action="generate", style="radial_gradient", tags=["test"]),
        content_materials=[
            ContentMaterial(action="generate_diagram", position="center", diagram_type="timeline",
                           diagram_data={"events": [{"year": "2020", "label": "Event"}]}),
        ],
    )
    assert slide.bg_action is not None
    assert len(slide.content_materials) == 1


def test_slide_content_backward_compat():
    slide = SlideContent(type="cover", title="Test", notes="Notes")
    assert slide.bg_action is None
    assert slide.content_materials is None
