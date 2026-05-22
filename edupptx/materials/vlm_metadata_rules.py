"""Shared VLM metadata rules for reusable image assets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image


def normalize_padding_capacity(value: Any, *, image_path: str | Path | None = None) -> str:
    """Return ``"high" | "mid" | "low" | ""`` for an asset.

    Padding capacity is a property of the candidate image's actual edges
    (transparent / near-white / colored), derived purely from pixels. The VLM
    contributes nothing here — it never saw or returned this field. Callers
    pass ``image_path`` at the earliest point the file is on disk
    (annotation time for library builds, registration time at runtime).

    When ``image_path`` is omitted (re-normalizing stored metadata at load
    time), the value carried on ``value`` is preserved. ``value`` may be:

      * a plain string (the canonical top-level shape — what we write going
        forward),
      * a legacy ``{"padding_capacity": "..."}`` dict (read-only back-compat
        for libraries that haven't been migrated yet).
    """

    if image_path is not None:
        return infer_padding_capacity_from_image(image_path) or ""
    if isinstance(value, dict):
        value = value.get("padding_capacity")
    return _normalize_padding_capacity(value)


def infer_padding_capacity_from_image(image_path: str | Path | None) -> str:
    if image_path is None:
        return ""
    try:
        with Image.open(image_path) as img:
            rgba = img.convert("RGBA")
            rgba.thumbnail((160, 160), Image.LANCZOS)
            width, height = rgba.size
            if width <= 0 or height <= 0:
                return ""
            pixels = rgba.load()
            edge_pixels: list[tuple[int, int, int, int]] = []
            for x in range(width):
                edge_pixels.append(pixels[x, 0])
                edge_pixels.append(pixels[x, height - 1])
            for y in range(1, max(1, height - 1)):
                edge_pixels.append(pixels[0, y])
                edge_pixels.append(pixels[width - 1, y])
    except Exception:
        return ""

    if not edge_pixels:
        return ""
    transparent_count = sum(1 for _r, _g, _b, alpha in edge_pixels if alpha <= 16)
    if transparent_count / len(edge_pixels) >= 0.5:
        return "high"

    opaque = [(r, g, b) for r, g, b, alpha in edge_pixels if alpha > 16]
    if not opaque:
        return "high"
    lumas = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in opaque]
    chromas = [max(r, g, b) - min(r, g, b) for r, g, b in opaque]
    avg_luma = sum(lumas) / len(lumas)
    avg_chroma = sum(chromas) / len(chromas)
    light_ratio = sum(
        1
        for luma, chroma in zip(lumas, chromas)
        if luma >= 215 and chroma <= 55
    ) / len(opaque)
    if avg_luma >= 225 and avg_chroma <= 55 and light_ratio >= 0.65:
        return "mid"
    return "low"


def _normalize_padding_capacity(value: Any) -> str:
    text = _clean_text(value).casefold()
    if text in {"high", "高"}:
        return "high"
    if text in {"mid", "medium", "中", "中等"}:
        return "mid"
    if text in {"low", "none", "no", "avoid", "低"}:
        return "low"
    return ""


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split())
