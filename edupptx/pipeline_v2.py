"""V2 rendering pipeline: StyleSchema JSON -> PPTX.

Standalone entry point that wires all three layers together.
Can be used from tests, CLI, or future agent code without modifying
the existing rendering pipeline.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from edupptx.layout_resolver import resolve_layout
from edupptx.models import PresentationPlan
from edupptx.pptx_writer import PptxWriter
from edupptx.style_resolver import resolve_style
from edupptx.style_schema import load_style
from edupptx.validator import validate_slides


def render_with_schema(
    plan: PresentationPlan,
    style_path: Path,
    bg_paths: list[Path] | None = None,
    material_paths: dict[int, Path] | None = None,
    diagram_specs: dict[int, tuple[str, dict]] | None = None,
    output_path: Path | str = "output_v2.pptx",
) -> Path:
    """Render a presentation using the v2 schema-driven pipeline.

    1. Load and validate the style schema (JSON)
    2. Resolve palette refs and named intents to concrete values
    3. Resolve layout: plan + style -> list[ResolvedSlide]
    4. Validate: bounds, overlap, min sizes
    5. Write PPTX

    material_paths: {slide_index: Path} for illustration/diagram images.
    diagram_specs: {slide_index: (diagram_type, diagram_data)} for native diagrams.
    """
    # Layer 1: Load schema
    schema = load_style(style_path)
    logger.info("V2 pipeline: loaded style '{}'", schema.meta.name)

    # Layer 2a: Resolve style
    resolved_style = resolve_style(schema)

    # Layer 2b: Resolve layout
    slides = resolve_layout(plan, resolved_style, bg_paths, material_paths, diagram_specs)
    logger.info("V2 pipeline: resolved {} slides", len(slides))

    # Layer 2c: Validate
    warnings = validate_slides(slides)
    if warnings:
        logger.warning("V2 pipeline: {} validation warnings", len(warnings))

    # Layer 3: Write PPTX
    writer = PptxWriter()
    writer.write_slides(slides, bg_paths, style=resolved_style)
    out = writer.save(output_path)
    logger.info("V2 pipeline: saved to {}", out)
    return out
