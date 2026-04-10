"""Main orchestrator: topic + requirements → .pptx file."""

from __future__ import annotations

import logging
from pathlib import Path

from edupptx.backgrounds import generate_background
from edupptx.config import Config
from edupptx.content_planner import ContentPlanner
from edupptx.design_system import get_design_tokens
from edupptx.llm_client import LLMClient
from edupptx.models import PresentationPlan
from edupptx.renderer import PresentationRenderer

log = logging.getLogger(__name__)


def generate(
    topic: str,
    requirements: str = "",
    output_path: str | Path | None = None,
    palette: str | None = None,
    config: Config | None = None,
    env_path: str | Path | None = None,
) -> Path:
    """Generate a complete educational presentation.

    Args:
        topic: The subject of the presentation (e.g. "勾股定理").
        requirements: Additional requirements (e.g. "适合初中生").
        output_path: Where to save the .pptx file. Defaults to "{topic}.pptx".
        palette: Color palette name. If None, LLM auto-selects.
        config: Configuration object. If None, loads from env.
        env_path: Path to .env file. Used only if config is None.

    Returns:
        Path to the generated .pptx file.
    """
    if config is None:
        config = Config.from_env(env_path)

    llm = LLMClient(config)

    # Phase 1: Content planning (LLM)
    log.info("Phase 1: Planning content for '%s'", topic)
    planner = ContentPlanner(llm)
    plan = planner.plan(topic, requirements, palette)
    log.info("Content plan: %d slides, palette=%s", len(plan.slides), plan.palette)

    # Phase 2: Design tokens
    design = get_design_tokens(plan.palette)

    # Phase 3: Background images
    log.info("Phase 3: Generating backgrounds")
    bg_path = generate_background(design, output_dir=config.cache_dir)
    backgrounds = [bg_path] * len(plan.slides)

    # Phase 4: Render
    log.info("Phase 4: Rendering slides")
    renderer = PresentationRenderer(design)
    renderer.render(plan, backgrounds)

    # Save
    out = Path(output_path or f"{topic}.pptx")
    renderer.save(out)
    log.info("Done! Saved to %s", out)
    return out


def generate_from_plan(
    plan: PresentationPlan,
    output_path: str | Path | None = None,
    config: Config | None = None,
    env_path: str | Path | None = None,
) -> Path:
    """Generate a presentation from a pre-built PresentationPlan.

    Useful for agents that want to construct/modify the plan before rendering.
    """
    if config is None:
        config = Config.from_env(env_path)

    design = get_design_tokens(plan.palette)

    bg_path = generate_background(design, output_dir=config.cache_dir)
    backgrounds = [bg_path] * len(plan.slides)

    renderer = PresentationRenderer(design)
    renderer.render(plan, backgrounds)

    out = Path(output_path or f"{plan.topic}.pptx")
    renderer.save(out)
    return out
