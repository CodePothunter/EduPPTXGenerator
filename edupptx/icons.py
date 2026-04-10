"""Lucide SVG icon management — load, recolor, and convert to PNG."""

from __future__ import annotations

import io
import re
from pathlib import Path

import cairosvg

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"

# Default fallback: a simple filled circle
_FALLBACK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
    'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="12" cy="12" r="10"/>'
    "</svg>"
)


def list_icons() -> list[str]:
    """Return all available icon names."""
    return sorted(p.stem for p in _ASSETS_DIR.glob("*.svg"))


def get_icon_svg(name: str, color: str = "currentColor") -> str:
    """Load an SVG icon and recolor it.

    Args:
        name: Lucide icon name (e.g. 'triangle').
        color: Hex color like '#10B981' or 'currentColor'.

    Returns:
        SVG string with stroke/fill colors replaced.
    """
    path = _ASSETS_DIR / f"{name}.svg"
    if not path.exists():
        return _FALLBACK_SVG.format(color=color)

    svg = path.read_text(encoding="utf-8")
    # Replace stroke color
    svg = re.sub(r'stroke="[^"]*"', f'stroke="{color}"', svg)
    # Replace fill for filled elements (but keep fill="none")
    svg = re.sub(r'fill="(?!none)[^"]*"', f'fill="{color}"', svg)
    return svg


def get_icon_png(name: str, color: str = "#000000", size: int = 48) -> bytes:
    """Render an SVG icon to PNG bytes at the specified size."""
    svg_str = get_icon_svg(name, color)
    return cairosvg.svg2png(
        bytestring=svg_str.encode("utf-8"),
        output_width=size,
        output_height=size,
    )


def save_icon_pair(
    name: str, color: str, size: int, dest_dir: Path
) -> tuple[Path, Path]:
    """Save both SVG and PNG versions of an icon. Returns (svg_path, png_path)."""
    svg_path = dest_dir / f"{name}.svg"
    png_path = dest_dir / f"{name}.png"

    svg_str = get_icon_svg(name, color)
    svg_path.write_text(svg_str, encoding="utf-8")

    png_bytes = get_icon_png(name, color, size)
    png_path.write_bytes(png_bytes)

    return svg_path, png_path
