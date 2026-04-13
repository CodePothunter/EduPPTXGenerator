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
from edupptx.style_schema import ResolvedStyle

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


def _make_smooth_gradient(
    w: int, h: int,
    stops: list[tuple[tuple[int, int, int], float]],
    angle: float = 0,
) -> Image.Image:
    """Create a smooth multi-stop gradient via tiny-image upscale.

    Works at 1/30 resolution then upscales with LANCZOS for buttery smooth
    color transitions — 900x faster than pixel-by-pixel rendering.

    stops: list of (rgb_tuple, position_0_to_1)
    angle: degrees — 0=top-to-bottom, 90=left-to-right, 30=diagonal
    """
    sw, sh = max(w // 30, 4), max(h // 30, 4)
    small = Image.new("RGB", (sw, sh))
    px = small.load()

    sorted_stops = sorted(stops, key=lambda s: s[1])
    rad = math.radians(angle)
    sin_a, cos_a = math.sin(rad), math.cos(rad)

    for y in range(sh):
        for x in range(sw):
            t = (x / sw) * sin_a + (y / sh) * cos_a
            t = max(0.0, min(1.0, t))

            # Interpolate between surrounding color stops
            color = sorted_stops[-1][0]
            for j in range(len(sorted_stops) - 1):
                if sorted_stops[j][1] <= t <= sorted_stops[j + 1][1]:
                    span = sorted_stops[j + 1][1] - sorted_stops[j][1]
                    lt = (t - sorted_stops[j][1]) / span if span > 0 else 0
                    # Smooth-step for less banding
                    lt = lt * lt * (3 - 2 * lt)
                    color = _blend(sorted_stops[j][0], sorted_stops[j + 1][0], lt)
                    break

            px[x, y] = color

    return small.resize((w, h), Image.LANCZOS)


def _add_soft_circle(
    base: Image.Image,
    cx: int, cy: int, radius: int,
    color: tuple[int, int, int], alpha: int,
    blur: int = 40,
) -> Image.Image:
    """Add a single soft glowing circle overlay to the base image."""
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=(*color, alpha),
    )
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=blur))
    return Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")


def generate_background(
    resolved: ResolvedStyle,
    style: str = "diagonal_gradient",
    output_dir: Path | None = None,
    seed_extra: str = "",
) -> Path:
    """Generate a single background image. Returns path to saved file.

    style: diagonal_gradient | radial_gradient | geometric_circles | geometric_triangles
    """
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp())
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    seed = hashlib.md5(f"{style}-{resolved.accent_color}-{seed_extra}".encode()).hexdigest()[:8]
    filename = f"bg_prog_{style}_{seed}.jpeg"
    out_path = output_dir / filename

    if out_path.exists():
        return out_path

    base = _hex_to_rgb(resolved.bg_overlay_color)
    accent = _hex_to_rgb(resolved.palette.get("accent_light", "#E0E0E0"))
    highlight = _hex_to_rgb(resolved.accent_color)
    rng = random.Random(seed)

    if style == "diagonal_gradient":
        # Multi-stop diagonal gradient with warm glow spots (stronger saturation)
        img = _make_smooth_gradient(BG_WIDTH, BG_HEIGHT, [
            (_blend(base, accent, 0.10), 0.0),
            (_blend(base, accent, 0.40), 0.35),
            (_blend(base, accent, 0.50), 0.65),
            (_blend(base, accent, 0.15), 1.0),
        ], angle=30)

        # Soft warm glows for depth
        for _ in range(rng.randint(2, 3)):
            cx = rng.randint(BG_WIDTH // 4, BG_WIDTH * 3 // 4)
            cy = rng.randint(BG_HEIGHT // 4, BG_HEIGHT * 3 // 4)
            r = rng.randint(250, 500)
            img = _add_soft_circle(img, cx, cy, r, accent, rng.randint(22, 40), blur=80)

    elif style == "radial_gradient":
        # Off-center radial glow with secondary highlight (stronger)
        img = _make_smooth_gradient(BG_WIDTH, BG_HEIGHT, [
            (base, 0.0),
            (_blend(base, accent, 0.08), 1.0),
        ], angle=0)

        # Main glow (off-center, stronger)
        cx = int(BG_WIDTH * rng.uniform(0.2, 0.4))
        cy = int(BG_HEIGHT * rng.uniform(0.25, 0.45))
        img = _add_soft_circle(img, cx, cy, 650, accent, 55, blur=120)
        img = _add_soft_circle(img, cx, cy, 320, accent, 35, blur=60)

        # Secondary glow — opposite corner for balance
        cx2 = int(BG_WIDTH * rng.uniform(0.65, 0.85))
        cy2 = int(BG_HEIGHT * rng.uniform(0.55, 0.75))
        mixed = _blend(accent, highlight, 0.3)
        img = _add_soft_circle(img, cx2, cy2, 400, mixed, 28, blur=80)

    elif style == "geometric_circles":
        # Gradient base + bokeh-style floating circles (more saturated)
        img = _make_smooth_gradient(BG_WIDTH, BG_HEIGHT, [
            (_blend(base, accent, 0.06), 0.0),
            (_blend(base, accent, 0.20), 0.5),
            (_blend(base, accent, 0.06), 1.0),
        ], angle=15)

        # Large background circles (soft, atmospheric)
        for _ in range(3):
            cx = rng.randint(-200, BG_WIDTH + 200)
            cy = rng.randint(-200, BG_HEIGHT + 200)
            r = rng.randint(350, 600)
            img = _add_soft_circle(img, cx, cy, r, accent, rng.randint(14, 25), blur=60)

        # Medium bokeh circles (more visible)
        for _ in range(5):
            cx = rng.randint(0, BG_WIDTH)
            cy = rng.randint(0, BG_HEIGHT)
            r = rng.randint(100, 220)
            img = _add_soft_circle(img, cx, cy, r, accent, rng.randint(20, 38), blur=28)

    elif style == "geometric_triangles":
        # Gradient base + abstract triangular facets (more color)
        img = _make_smooth_gradient(BG_WIDTH, BG_HEIGHT, [
            (_blend(base, accent, 0.10), 0.0),
            (_blend(base, accent, 0.05), 0.5),
            (_blend(base, accent, 0.12), 1.0),
        ], angle=55)

        overlay = Image.new("RGBA", (BG_WIDTH, BG_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        for _ in range(5):
            cx = rng.randint(-50, BG_WIDTH + 50)
            cy = rng.randint(-50, BG_HEIGHT + 50)
            size = rng.randint(200, 500)
            alpha = rng.randint(10, 25)
            rotation = rng.uniform(0, math.pi * 2)

            points = []
            for k in range(3):
                a = rotation + k * (2 * math.pi / 3)
                px = cx + int(size * math.cos(a))
                py = cy + int(size * math.sin(a))
                points.append((px, py))

            draw.polygon(points, fill=(*highlight, alpha))

        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=22))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

        # Soft highlight spots
        for _ in range(rng.randint(2, 3)):
            cx = rng.randint(BG_WIDTH // 4, BG_WIDTH * 3 // 4)
            cy = rng.randint(BG_HEIGHT // 4, BG_HEIGHT * 3 // 4)
            r = rng.randint(200, 400)
            img = _add_soft_circle(img, cx, cy, r, accent, rng.randint(16, 28), blur=50)

    # Final gentle blur for smoothness
    img = img.filter(ImageFilter.GaussianBlur(radius=1))
    img.save(out_path, "JPEG", quality=92)

    logger.info("Generated background: {}", filename)
    return out_path


def generate_ai_background(topic: str, resolved: ResolvedStyle, config: Config) -> Path | None:
    """Generate a background using the AI image API. Returns path or None on failure."""
    if not config.image_api_key:
        logger.warning("No image API configured, skipping AI background generation")
        return None

    try:
        from edupptx.llm_client import ImageClient
        client = ImageClient(config)
        prompt = (
            f"Abstract minimalist academic background illustration for '{topic}'. "
            f"Soft {resolved.accent_color} tones, clean, professional, suitable as a "
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
