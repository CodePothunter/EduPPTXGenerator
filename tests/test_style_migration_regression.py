"""Layer 4 v2.1-CRITICAL regression: blue/emerald .json ↔ .md must resolve identically.

Hard gate: if any field in ResolvedStyle diverges between the two formats, the
.md migration is broken. This is the pixel-equivalent equivalence test
mandated by the DESIGN.md integration spec (Layer 4).
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from edupptx.style_resolver import resolve_style
from edupptx.style_schema import ResolvedStyle, load_style

STYLES_DIR = Path(__file__).resolve().parent.parent / "styles"


@pytest.mark.parametrize("name", ["blue", "emerald"])
def test_style_md_matches_json(name: str) -> None:
    """Loading {name}.json and {name}.md must produce identical ResolvedStyle pixels."""
    schema_json = load_style(STYLES_DIR / f"{name}.json")
    schema_md = load_style(STYLES_DIR / f"{name}.md")
    rs_json = resolve_style(schema_json)
    rs_md = resolve_style(schema_md)

    # Core color/EMU/font fields must be exactly equal.
    for f in fields(ResolvedStyle):
        if f.name == "decorations":
            continue  # compared separately
        actual = getattr(rs_md, f.name)
        expected = getattr(rs_json, f.name)
        assert actual == expected, (
            f"{name}: ResolvedStyle.{f.name} diverged: json={expected!r} md={actual!r}"
        )

    # Decorations is a sub-dataclass; pydantic equality covers it.
    assert rs_md.decorations == rs_json.decorations, (
        f"{name}: decorations diverged: json={rs_json.decorations} md={rs_md.decorations}"
    )
