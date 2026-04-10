"""Background image generation — programmatic (Pillow) and AI-based."""

from __future__ import annotations

import hashlib
import math
import random
import tempfile
import urllib.request
from pathlib import Path

from loguru import logger
from PIL import Image, ImageDraw, ImageFilter

from edupptx.config import Config
from edupptx.design_system import DesignTokens

BG_WIDTH = 1920
BG_HEIGHT = 1080


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _blend(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def generate_background(
    design: DesignTokens,
    style: str = "diagonal_gradient",
    output_dir: Path | None = None,
) -> Path:
    """Generate a single background image. Returns path to saved file.

    style: diagonal_gradient | radial_gradient | geometric_circles | geometric_triangles
    output_dir: Where to save. Defaults to a temp directory.
    """
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp())
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    seed = hashlib.md5(f"{style}-{design.accent}".encode()).hexdigest()[:8]
    filename = f"bg_prog_{style}_{seed}.jpeg"
    out_path = output_dir / filename

    if out_path.exists():
        return out_path

    img = Image.new("RGB", (BG_WIDTH, BG_HEIGHT))
    draw = ImageDraw.Draw(img)

    base = _hex_to_rgb(design.bg_overlay)
    accent = _hex_to_rgb(design.accent_light)
    highlight = _hex_to_rgb(design.accent)

    if style == "diagonal_gradient":
        for y in range(BG_HEIGHT):
            for x in range(BG_WIDTH):
                t = (x / BG_WIDTH * 0.5 + y / BG_HEIGHT * 0.5)
                c = _blend(base, accent, t * 0.6)
                img.putpixel((x, y), c)

    elif style == "radial_gradient":
        cx, cy = BG_WIDTH * 0.3, BG_HEIGHT * 0.4
        max_dist = math.sqrt(BG_WIDTH ** 2 + BG_HEIGHT ** 2)
        for y in range(BG_HEIGHT):
            for x in range(BG_WIDTH):
                dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
                t = min(dist / max_dist, 1.0)
                c = _blend(accent, base, t)
                img.putpixel((x, y), c)

    elif style == "geometric_circles":
        img.paste(base, (0, 0, BG_WIDTH, BG_HEIGHT))
        rng = random.Random(seed)
        for _ in range(15):
            cx = rng.randint(0, BG_WIDTH)
            cy = rng.randint(0, BG_HEIGHT)
            r = rng.randint(80, 300)
            alpha = rng.randint(15, 40)
            overlay = Image.new("RGBA", (BG_WIDTH, BG_HEIGHT), (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            fill = (*accent, alpha)
            overlay_draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
            overlay = overlay.filter(ImageFilter.GaussianBlur(radius=30))
            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    elif style == "geometric_triangles":
        img.paste(base, (0, 0, BG_WIDTH, BG_HEIGHT))
        rng = random.Random(seed)
        for _ in range(10):
            cx = rng.randint(0, BG_WIDTH)
            cy = rng.randint(0, BG_HEIGHT)
            size = rng.randint(100, 400)
            alpha = rng.randint(10, 30)
            overlay = Image.new("RGBA", (BG_WIDTH, BG_HEIGHT), (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            points = [
                (cx, cy - size),
                (cx - size, cy + size // 2),
                (cx + size, cy + size // 2),
            ]
            fill = (*highlight, alpha)
            overlay_draw.polygon(points, fill=fill)
            overlay = overlay.filter(ImageFilter.GaussianBlur(radius=20))
            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    # Slight blur for smoothness
    img = img.filter(ImageFilter.GaussianBlur(radius=2))
    img.save(out_path, "JPEG", quality=85)

    logger.info("Generated programmatic background: {}", filename)
    return out_path


def generate_ai_background(topic: str, design: DesignTokens, config: Config) -> Path | None:
    """Generate a background using the AI image API. Returns path or None on failure."""
    if not config.image_api_key:
        logger.warning("No image API configured, skipping AI background generation")
        return None

    try:
        from edupptx.llm_client import ImageClient
        client = ImageClient(config)
        prompt = (
            f"Abstract minimalist academic background illustration for '{topic}'. "
            f"Soft {design.accent} tones, clean, professional, suitable as a "
            f"presentation slide background. No text, no diagrams."
        )
        urls = client.generate(prompt, size="1792x1024", n=1)
        if not urls:
            return None

        seed = hashlib.md5(topic.encode()).hexdigest()[:8]
        output_dir = Path(tempfile.mkdtemp())
        filename = f"bg_ai_{seed}.jpeg"
        out_path = output_dir / filename

        urllib.request.urlretrieve(urls[0], str(out_path))
        logger.info("Generated AI background: {}", filename)
        return out_path

    except Exception as e:
        logger.warning("AI background generation failed: {}", e)
        return None
