"""Style resolver: dereference palette refs + resolve named intents to EMU.

Pure function: StyleSchema -> ResolvedStyle (all concrete values).
"""

from __future__ import annotations

import os

from loguru import logger

from edupptx.postprocess.style_linter import (
    Finding,
    StyleValidationError,
    lint_resolved_style,
    lint_style_schema,
)
from edupptx.style_schema import (
    CARD_SPACING_PRESETS,
    CONTENT_DENSITY_PRESETS,
    ICON_SIZE_PRESETS,
    MARGIN_PRESETS,
    PT,
    FontSpec,
    ResolvedStyle,
    StyleSchema,
)


def _resolve_ref(value: str, palette: dict[str, str]) -> str:
    """Resolve a palette reference like 'palette.accent' to a hex color.

    If the value doesn't start with 'palette.', return it as-is (already concrete).
    If the key is not found, log a warning and return the raw value.
    """
    if not value.startswith("palette."):
        return value
    key = value[len("palette."):]
    if key in palette:
        return palette[key]
    logger.warning("Unknown palette ref '{}', using raw value", value)
    return value


def resolve_style(schema: StyleSchema) -> ResolvedStyle:
    """Resolve a StyleSchema into a fully concrete ResolvedStyle.

    1. Dereferences all 'palette.xxx' color references
    2. Resolves named intents (comfortable/tight/spacious) to EMU values
    3. Returns a flat dataclass with no indirection
    """
    palette = schema.global_tokens.palette
    sem = schema.semantic
    lay = schema.layout

    # Resolve colors
    heading_color = _resolve_ref(sem.heading_color, palette)
    body_color = _resolve_ref(sem.body_color, palette)
    accent_color = _resolve_ref(sem.accent_color, palette)
    card_fill_color = _resolve_ref(sem.card_fill_color, palette)
    card_title_color = _resolve_ref(sem.card_title_color, palette)
    icon_color = _resolve_ref(sem.icon_color, palette)
    bg_overlay_color = _resolve_ref(sem.bg_overlay_color, palette)
    shadow_color = _resolve_ref(sem.card_shadow.color, palette)

    # Resolve shadow EMU values
    card_shadow_blur_emu = sem.card_shadow.blur_pt * PT
    card_shadow_dist_emu = sem.card_shadow.dist_pt * PT

    # Resolve named intents -> EMU
    margin_key = lay.margin
    if margin_key not in MARGIN_PRESETS:
        logger.warning("Unknown margin '{}', falling back to 'comfortable'", margin_key)
        margin_key = "comfortable"
    margins = MARGIN_PRESETS[margin_key]

    spacing_key = lay.card_spacing
    if spacing_key not in CARD_SPACING_PRESETS:
        logger.warning("Unknown card_spacing '{}', falling back to 'normal'", spacing_key)
        spacing_key = "normal"
    card_gap = CARD_SPACING_PRESETS[spacing_key]

    icon_key = lay.icon_size
    if icon_key not in ICON_SIZE_PRESETS:
        logger.warning("Unknown icon_size '{}', falling back to 'large'", icon_key)
        icon_key = "large"
    icon_size = ICON_SIZE_PRESETS[icon_key]

    density_key = lay.content_density
    if density_key not in CONTENT_DENSITY_PRESETS:
        logger.warning("Unknown content_density '{}', falling back to 'standard'", density_key)
        density_key = "standard"
    density = CONTENT_DENSITY_PRESETS[density_key]

    # Fonts
    heading_font = schema.global_tokens.fonts.get("heading", FontSpec())
    body_font = schema.global_tokens.fonts.get("body", FontSpec())

    # Background
    bg = schema.global_tokens.background
    bg_type = bg.get("type", "diagonal_gradient")
    bg_seed = bg.get("seed_extra", "")

    resolved = ResolvedStyle(
        heading_color=heading_color,
        body_color=body_color,
        accent_color=accent_color,
        card_fill_color=card_fill_color,
        card_title_color=card_title_color,
        icon_color=icon_color,
        bg_overlay_color=bg_overlay_color,
        bg_overlay_alpha=sem.bg_overlay_alpha,
        shadow_color=shadow_color,

        heading_font=heading_font,
        body_font=body_font,

        title_size_pt=sem.title_size_pt,
        subtitle_size_pt=sem.subtitle_size_pt,
        body_size_pt=sem.body_size_pt,
        card_title_size_pt=sem.card_title_size_pt,
        footer_size_pt=sem.footer_size_pt,
        formula_size_pt=sem.formula_size_pt,

        card_corner_radius=sem.card_corner_radius,
        card_shadow_blur_emu=card_shadow_blur_emu,
        card_shadow_dist_emu=card_shadow_dist_emu,
        card_shadow_color=shadow_color,
        card_shadow_alpha_pct=sem.card_shadow.alpha_pct,

        margin_left=margins["left"],
        margin_top=margins["top"],
        content_w=margins["content_w"],
        card_gap=card_gap,
        icon_size=icon_size,
        card_pad=density["card_pad"],
        icon_margin=density["icon_margin"],
        card_title_h=density["title_h"],

        title_position=lay.title_position,

        decorations=schema.decorations,

        bg_type=bg_type,
        bg_seed_extra=bg_seed,

        palette=palette,
    )

    _run_style_lint(schema, resolved)
    return resolved


def _run_style_lint(schema: StyleSchema, resolved: ResolvedStyle) -> None:
    """Run lint rules. Errors always raise; warnings raise only in strict mode.

    `StyleValidationError.findings` is always the concatenation `errors + warnings`
    regardless of which condition triggered the raise — callers can assume a
    stable shape.
    """
    strict = os.environ.get("EDUPPTX_LINT_STRICT") == "1"
    schema_findings = lint_style_schema(schema)
    resolved_findings = lint_resolved_style(resolved)
    all_findings: list[Finding] = schema_findings + resolved_findings

    errors = [f for f in all_findings if f.severity == "error"]
    warnings = [f for f in all_findings if f.severity == "warning"]

    for w in warnings:
        logger.warning("[style-lint] {} @ {}: {}", w.rule, w.path, w.message)

    combined = errors + warnings
    if errors:
        raise StyleValidationError(combined)
    if warnings and strict:
        raise StyleValidationError(combined)
