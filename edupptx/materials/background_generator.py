"""Generate a unified background image for the entire deck via Seedream API."""

from __future__ import annotations

import shutil
from pathlib import Path

from loguru import logger

from edupptx.config import Config
from edupptx.models import VisualPlan
from edupptx.session import Session


def build_background_prompt(visual: VisualPlan) -> str:
    """Compose final background prompt from the original prompt plus an optional color-bias sentence."""

    prompt = visual.background_prompt.strip()
    if not prompt:
        prompt = (
            f"淡雅抽象渐变背景，轻微偏向 {visual.primary_color} 色调，"
            "简洁几何纹理，画面干净明亮，适合教学演示承载文字，"
            "16:9 横版，高分辨率"
        )

    color_bias = visual.background_color_bias.strip()
    if not color_bias:
        return prompt

    return f"{prompt} 配色偏向：{color_bias}".strip()


async def generate_background(
    visual: VisualPlan,
    config: Config,
    session: Session,
) -> Path | None:
    """Generate one background image using Seedream and save to session.

    Returns the local path or None on failure.
    """
    if not config.image_api_key or not config.image_model:
        logger.warning("Seedream API not configured, skipping background generation")
        return None

    prompt = build_background_prompt(visual)

    logger.info("Generating background image: {}...", prompt[:60])

    from edupptx.materials.seedream import SeedreamProvider

    provider = SeedreamProvider(config)
    results = await provider.generate(prompt, size="2848x1600")

    if not results or not results[0].local_path:
        logger.warning("Background generation returned no results")
        return None

    # Copy to session materials
    materials_dir = session.dir / "materials"
    materials_dir.mkdir(exist_ok=True)
    dest = materials_dir / "background.png"
    shutil.copy2(results[0].local_path, dest)
    logger.info("Background saved: {} ({:.0f} KB)", dest, dest.stat().st_size / 1024)
    return dest
