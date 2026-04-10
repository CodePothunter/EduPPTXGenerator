"""Three-tier background manager: cache → programmatic → AI generation."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from edupptx.config import Config
from edupptx.design_system import DesignTokens

log = logging.getLogger(__name__)

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


class BackgroundManager:
    """Manages background images with a 3-tier fallback strategy."""

    def __init__(self, cache_dir: Path, config: Config | None = None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.cache_dir / "index.json"
        self.config = config
        self._index: dict = self._load_index()

    def _load_index(self) -> dict:
        if self.index_path.exists():
            return json.loads(self.index_path.read_text())
        return {}

    def _save_index(self) -> None:
        self.index_path.write_text(json.dumps(self._index, ensure_ascii=False, indent=2))

    def _add_to_index(self, filename: str, tags: list[str], source: str, palette: str) -> None:
        self._index[filename] = {
            "tags": tags,
            "source": source,
            "palette": palette,
            "resolution": f"{BG_WIDTH}x{BG_HEIGHT}",
        }
        self._save_index()

    def get_backgrounds(
        self, topic: str, palette: str, count: int, design: DesignTokens | None = None
    ) -> list[Path]:
        """Get `count` background images using the 3-tier strategy."""
        results: list[Path] = []
        topic_tags = topic.lower().split()

        # Tier 1: cache lookup
        cached = self._search_cache(topic_tags, palette)
        results.extend(cached[:count])

        if len(results) >= count:
            return results[:count]

        # Tier 2: programmatic generation
        remaining = count - len(results)
        if design is None:
            from edupptx.design_system import get_design_tokens
            design = get_design_tokens(palette)

        styles = ["diagonal_gradient", "radial_gradient", "geometric_circles", "geometric_triangles"]
        for i in range(remaining):
            style = styles[i % len(styles)]
            bg = self.generate_programmatic(design, style, topic_tags)
            results.append(bg)

        return results[:count]

    def _search_cache(self, tags: list[str], palette: str) -> list[Path]:
        """Search cached backgrounds by tags and palette."""
        matches: list[tuple[int, Path]] = []
        for filename, meta in self._index.items():
            path = self.cache_dir / filename
            if not path.exists():
                continue
            score = sum(1 for t in tags if t in meta.get("tags", []))
            if meta.get("palette") == palette:
                score += 2
            if score > 0:
                matches.append((score, path))
        matches.sort(key=lambda x: -x[0])
        return [p for _, p in matches]

    def generate_programmatic(
        self, design: DesignTokens, style: str = "diagonal_gradient", tags: list[str] | None = None
    ) -> Path:
        """Generate a background image using Pillow."""
        seed = hashlib.md5(f"{style}-{design.accent}".encode()).hexdigest()[:8]
        filename = f"bg_prog_{style}_{seed}.jpeg"
        out_path = self.cache_dir / filename

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
            # Soft base with scattered circles
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

        self._add_to_index(filename, tags or [], "programmatic", design.accent)
        log.info("Generated programmatic background: %s", filename)
        return out_path

    def generate_ai(self, topic: str, design: DesignTokens) -> Path | None:
        """Generate a background using the image generation API."""
        if not self.config or not self.config.image_api_key:
            log.warning("No image API configured, skipping AI background generation")
            return None

        try:
            from edupptx.llm_client import ImageClient
            client = ImageClient(self.config)
            prompt = (
                f"Abstract minimalist academic background illustration for '{topic}'. "
                f"Soft {design.accent} tones, clean, professional, suitable as a "
                f"presentation slide background. No text, no diagrams."
            )
            urls = client.generate(prompt, size="1792x1024", n=1)
            if not urls:
                return None

            # Download and save
            seed = hashlib.md5(topic.encode()).hexdigest()[:8]
            filename = f"bg_ai_{seed}.jpeg"
            out_path = self.cache_dir / filename

            urllib.request.urlretrieve(urls[0], str(out_path))
            self._add_to_index(
                filename, topic.lower().split(), "ai_generated", design.accent
            )
            log.info("Generated AI background: %s", filename)
            return out_path

        except Exception as e:
            log.warning("AI background generation failed: %s", e)
            return None
