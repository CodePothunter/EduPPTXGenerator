"""DESIGN.md ⇄ StyleSchema parser and serializer (Layer 3a).

DESIGN.md is the user-facing source of truth: YAML frontmatter for tokens
plus 8 H2 prose sections for tone/intent. PPT-specific fields live under
``pptx-extensions:``; per the DESIGN.md spec, unknown keys round-trip cleanly.
"""

from __future__ import annotations

from typing import Any

import frontmatter
import mistune
import yaml

from edupptx.style_schema import (
    DecorationTokens,
    FontSpec,
    GlobalTokens,
    LayoutTokens,
    SchemaMeta,
    SemanticTokens,
    ShadowSpec,
    StyleSchema,
)

# ── Public API ────────────────────────────────────────────

PROSE_HEADINGS: tuple[str, ...] = (
    "Overview",
    "Colors",
    "Typography",
    "Layout",
    "Elevation",
    "Shapes",
    "Components",
    "Do's and Don'ts",
)

_VALID_MARGIN = ("comfortable", "tight", "spacious")
_VALID_CARD_GAP = ("tight", "normal", "wide")


def parse_design_md(text: str) -> StyleSchema:
    post = frontmatter.loads(text)
    yaml_data: dict[str, Any] = dict(post.metadata or {})
    body = post.content or ""

    palette = dict(yaml_data.get("colors", {}) or {})

    sections = _parse_h2_sections(body)
    description = "\n\n".join(f"## {h}\n{c}" for h, c in sections.items()).strip()

    spacing = yaml_data.get("spacing", {}) or {}
    margin = spacing.get("margin", "comfortable")
    if margin not in _VALID_MARGIN:
        margin = "comfortable"
    card_gap = spacing.get("card_gap", "normal")
    if card_gap not in _VALID_CARD_GAP:
        card_gap = "normal"

    ext: dict[str, Any] = yaml_data.get("pptx-extensions", {}) or {}
    bg_cfg = ext.get("background") or {"type": "diagonal_gradient", "seed_extra": ""}
    decorations_cfg = ext.get("decorations", {}) or {}
    shadow_cfg = ext.get("card_shadow", {}) or {}
    semantic_ext_cfg = ext.get("semantic", {}) or {}

    return StyleSchema(
        meta=SchemaMeta(
            schema_version=str(yaml_data.get("schema_version", "1.0")),
            name=str(yaml_data.get("name", "unnamed")),
            description=description,
        ),
        global_tokens=GlobalTokens(
            palette=palette,
            fonts=_parse_fonts(yaml_data.get("typography", {}) or {}),
            background=_normalize_bg(bg_cfg),
        ),
        semantic=_build_semantic(
            yaml_data.get("typography", {}) or {}, shadow_cfg, semantic_ext_cfg,
        ),
        layout=LayoutTokens(
            margin=margin,
            card_spacing=card_gap,
            icon_size="large",
            content_density="standard",
            title_position="top_left",
        ),
        decorations=_build_decorations(decorations_cfg),
    )


def serialize_style(
    schema: StyleSchema,
    prose_sections: dict[str, str] | None = None,
) -> str:
    yaml_data: dict[str, Any] = {
        "schema_version": schema.meta.schema_version or "1.0",
        "name": schema.meta.name,
        "colors": dict(schema.global_tokens.palette),
        "typography": _serialize_typography(schema.semantic),
        "spacing": {
            "margin": schema.layout.margin,
            "card_gap": schema.layout.card_spacing,
        },
        "rounded": {"sm": "4px", "md": "8px", "lg": "16px"},
        "pptx-extensions": {
            "decorations": _serialize_decorations(schema.decorations),
            "card_shadow": {
                "blur_pt": schema.semantic.card_shadow.blur_pt,
                "dist_pt": schema.semantic.card_shadow.dist_pt,
                "color": schema.semantic.card_shadow.color,
                "alpha_pct": schema.semantic.card_shadow.alpha_pct,
            },
            "background": dict(schema.global_tokens.background),
            "semantic": {
                "subtitle_size_pt": schema.semantic.subtitle_size_pt,
                "footer_size_pt": schema.semantic.footer_size_pt,
                "formula_size_pt": schema.semantic.formula_size_pt,
                "card_corner_radius": schema.semantic.card_corner_radius,
                "bg_overlay_alpha": schema.semantic.bg_overlay_alpha,
            },
        },
    }
    fm = yaml.safe_dump(yaml_data, allow_unicode=True, sort_keys=False)
    body = prose_sections if prose_sections is not None else _placeholder_prose()
    sections_md = "\n\n".join(f"## {h}\n{c}" for h, c in body.items())
    return f"---\n{fm}---\n\n{sections_md}\n"


# ── H2 section splitter (mistune AST) ─────────────────────


def _parse_h2_sections(body: str) -> dict[str, str]:
    md = mistune.create_markdown(renderer=None)
    tokens = md(body)
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for tok in tokens:
        if tok.get("type") == "heading" and tok.get("attrs", {}).get("level") == 2:
            if current is not None:
                sections[current] = "\n\n".join(b for b in buffer if b).strip()
            current = _extract_text(tok)
            buffer = []
        elif current is not None:
            rendered = _render_token(tok)
            if rendered:
                buffer.append(rendered)
    if current is not None:
        sections[current] = "\n\n".join(b for b in buffer if b).strip()
    return sections


def _extract_text(tok: dict[str, Any]) -> str:
    children = tok.get("children") or []
    parts: list[str] = []
    for c in children:
        if "raw" in c:
            parts.append(c["raw"])
        elif "children" in c:
            parts.append(_extract_text(c))
    return "".join(parts).strip()


def _render_token(tok: dict[str, Any]) -> str:
    t = tok.get("type")
    if t == "paragraph":
        return _extract_text(tok)
    if t == "block_code":
        raw = tok.get("raw", "")
        info = (tok.get("attrs") or {}).get("info", "")
        return f"```{info}\n{raw}```"
    if t == "list":
        bullet = tok.get("bullet", "-")
        ordered = (tok.get("attrs") or {}).get("ordered", False)
        lines: list[str] = []
        for i, item in enumerate(tok.get("children") or [], start=1):
            inner = _extract_text(item)
            prefix = f"{i}." if ordered else bullet
            lines.append(f"{prefix} {inner}")
        return "\n".join(lines)
    if t == "blank_line":
        return ""
    if t == "heading":
        # Non-H2 heading inside a section — preserve level.
        level = (tok.get("attrs") or {}).get("level", 3)
        return f"{'#' * level} {_extract_text(tok)}"
    return tok.get("raw", "") or ""


# ── YAML → schema helpers ─────────────────────────────────


def _parse_fonts(typography: dict[str, Any]) -> dict[str, FontSpec]:
    def _font(role: str) -> FontSpec:
        cfg = typography.get(role, {}) or {}
        family = cfg.get("fontFamily", "Noto Sans SC")
        return FontSpec(family=family, fallback="Arial")

    return {"heading": _font("title"), "body": _font("body")}


def _normalize_bg(bg_cfg: dict[str, Any]) -> dict[str, str]:
    return {
        "type": str(bg_cfg.get("type", "diagonal_gradient")),
        "seed_extra": str(bg_cfg.get("seed_extra", "")),
    }


def _parse_pt(value: Any, default: int) -> int:
    if value is None:
        return default
    s = str(value).strip().lower().rstrip("pt").strip()
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return default


def _build_semantic(
    typography: dict[str, Any],
    shadow_cfg: dict[str, Any],
    semantic_ext_cfg: dict[str, Any] | None = None,
) -> SemanticTokens:
    title_cfg = typography.get("title", {}) or {}
    card_title_cfg = typography.get("card-title", {}) or {}
    body_cfg = typography.get("body", {}) or {}
    ext = semantic_ext_cfg or {}
    defaults = SemanticTokens()

    shadow = ShadowSpec(
        blur_pt=int(shadow_cfg.get("blur_pt", 30)),
        dist_pt=int(shadow_cfg.get("dist_pt", 8)),
        color=str(shadow_cfg.get("color", "palette.shadow")),
        alpha_pct=int(shadow_cfg.get("alpha_pct", 14)),
    )

    return SemanticTokens(
        title_size_pt=_parse_pt(title_cfg.get("fontSize"), defaults.title_size_pt),
        card_title_size_pt=_parse_pt(card_title_cfg.get("fontSize"), defaults.card_title_size_pt),
        body_size_pt=_parse_pt(body_cfg.get("fontSize"), defaults.body_size_pt),
        subtitle_size_pt=int(ext.get("subtitle_size_pt", defaults.subtitle_size_pt)),
        footer_size_pt=int(ext.get("footer_size_pt", defaults.footer_size_pt)),
        formula_size_pt=int(ext.get("formula_size_pt", defaults.formula_size_pt)),
        card_corner_radius=int(ext.get("card_corner_radius", defaults.card_corner_radius)),
        bg_overlay_alpha=float(ext.get("bg_overlay_alpha", defaults.bg_overlay_alpha)),
        card_shadow=shadow,
    )


def _build_decorations(cfg: dict[str, Any]) -> DecorationTokens:
    defaults = DecorationTokens()
    return DecorationTokens(
        title_underline=bool(cfg.get("title_underline", defaults.title_underline)),
        content_panel=bool(cfg.get("content_panel", defaults.content_panel)),
        panel_alpha_pct=int(cfg.get("panel_alpha_pct", defaults.panel_alpha_pct)),
        footer_separator=bool(cfg.get("footer_separator", defaults.footer_separator)),
        quote_bar=bool(cfg.get("quote_bar", defaults.quote_bar)),
        section_diamond=bool(cfg.get("section_diamond", defaults.section_diamond)),
        closing_circle=bool(cfg.get("closing_circle", defaults.closing_circle)),
    )


# ── schema → YAML helpers ─────────────────────────────────


def _serialize_typography(semantic: SemanticTokens) -> dict[str, dict[str, Any]]:
    return {
        "title": {
            "fontFamily": "Noto Sans SC",
            "fontSize": f"{semantic.title_size_pt}pt",
            "fontWeight": 700,
        },
        "card-title": {
            "fontFamily": "Noto Sans SC",
            "fontSize": f"{semantic.card_title_size_pt}pt",
            "fontWeight": 600,
        },
        "body": {
            "fontFamily": "Noto Sans SC",
            "fontSize": f"{semantic.body_size_pt}pt",
        },
    }


def _serialize_decorations(d: DecorationTokens) -> dict[str, Any]:
    return {
        "title_underline": d.title_underline,
        "content_panel": d.content_panel,
        "panel_alpha_pct": d.panel_alpha_pct,
        "footer_separator": d.footer_separator,
        "quote_bar": d.quote_bar,
        "section_diamond": d.section_diamond,
        "closing_circle": d.closing_circle,
    }


def _placeholder_prose() -> dict[str, str]:
    return {h: "" for h in PROSE_HEADINGS}
