"""Tests for the OOXML renderer."""

import tempfile
from pathlib import Path

from pptx import Presentation

from edupptx.design_system import get_design_tokens
from edupptx.models import PresentationPlan, SlideCard, SlideContent
from edupptx.renderer import PresentationRenderer
from edupptx.backgrounds import generate_background


def _make_simple_plan() -> PresentationPlan:
    return PresentationPlan(
        topic="Test",
        palette="emerald",
        slides=[
            SlideContent(
                type="cover",
                title="Test Presentation",
                subtitle="A test subtitle",
                cards=[
                    SlideCard(icon="star", title="Point 1", body="Description 1"),
                    SlideCard(icon="target", title="Point 2", body="Description 2"),
                    SlideCard(icon="check", title="Point 3", body="Description 3"),
                ],
                formula="E = mc²",
                notes="Test speaker notes",
            ),
            SlideContent(
                type="content",
                title="Content Slide",
                cards=[
                    SlideCard(icon="book", title="Topic A", body="Details about A"),
                    SlideCard(icon="lightbulb", title="Topic B", body="Details about B"),
                ],
                notes="More notes",
            ),
            SlideContent(
                type="closing",
                title="Thank You",
                subtitle="End of presentation",
                notes="Closing remarks",
            ),
        ],
    )


def test_renderer_creates_valid_pptx():
    """Render a simple plan and verify the output is a valid PPTX."""
    plan = _make_simple_plan()
    design = get_design_tokens(plan.palette)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Generate backgrounds
        cache_dir = Path(tmpdir) / "cache"
        styles = ["diagonal_gradient", "radial_gradient", "geometric_circles"]
        backgrounds = [
            generate_background(design, styles[i % len(styles)], cache_dir)
            for i in range(len(plan.slides))
        ]

        renderer = PresentationRenderer(design)
        renderer.render(plan, backgrounds)

        out_path = Path(tmpdir) / "test.pptx"
        renderer.save(out_path)

        assert out_path.exists()
        assert out_path.stat().st_size > 10000  # Reasonable file size

        # Verify with python-pptx
        prs = Presentation(str(out_path))
        assert len(prs.slides) == 3


def test_renderer_slide_dimensions():
    """Verify slide dimensions match 16:9 standard."""
    design = get_design_tokens("emerald")
    renderer = PresentationRenderer(design)
    assert renderer.prs.slide_width == 12192000
    assert renderer.prs.slide_height == 6858000


def test_renderer_speaker_notes():
    """Verify speaker notes are embedded."""
    plan = _make_simple_plan()
    design = get_design_tokens(plan.palette)

    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir) / "cache"
        styles = ["diagonal_gradient", "radial_gradient", "geometric_circles"]
        backgrounds = [
            generate_background(design, styles[i % len(styles)], cache_dir)
            for i in range(len(plan.slides))
        ]

        renderer = PresentationRenderer(design)
        renderer.render(plan, backgrounds)

        out_path = Path(tmpdir) / "test.pptx"
        renderer.save(out_path)

        prs = Presentation(str(out_path))
        slide = prs.slides[0]
        assert slide.has_notes_slide
        notes_text = slide.notes_slide.notes_text_frame.text
        assert "Test speaker notes" in notes_text


def test_renderer_with_illustration():
    """Test rendering a slide with an illustration material."""
    design = get_design_tokens("emerald")
    renderer = PresentationRenderer(design)

    # Create a test illustration image
    from PIL import Image
    img = Image.new("RGB", (1024, 768), (200, 220, 200))
    img_path = Path(tempfile.mktemp(suffix=".png"))
    img.save(img_path, "PNG")

    from edupptx.models import ContentMaterial
    slide = SlideContent(
        type="content",
        title="Test with illustration",
        cards=[SlideCard(icon="star", title="Point 1", body="Body 1")],
        notes="Notes",
        content_materials=[
            ContentMaterial(
                action="generate_illustration",
                position="center",
                illustration_description="test illustration",
            )
        ],
    )

    renderer.render_slide(slide, material_path=img_path)

    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "test.pptx"
        renderer.save(out)
        assert out.exists()
        prs = Presentation(str(out))
        assert len(prs.slides) == 1

    img_path.unlink(missing_ok=True)
