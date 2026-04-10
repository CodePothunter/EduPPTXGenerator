"""Tests for data models."""

from edupptx.models import PresentationPlan, SlideCard, SlideContent


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
