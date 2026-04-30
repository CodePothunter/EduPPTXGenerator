"""Style linter — WCAG contrast + broken-ref checks for resolved/raw styles.

Pure functions; the caller (style_resolver) owns side effects (logging, raise,
strict-mode env handling). Two rules ported from Google's design.md tooling:

  - contrast-ratio : WCAG AA 4.5:1 between foreground/background pairs
  - broken-ref    : palette / token reference must exist in palette
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from edupptx.style_schema import ResolvedStyle, StyleSchema

Severity = Literal["error", "warning", "info"]


class StyleValidationError(Exception):
    """Raised when style lint produces error-level findings (or strict mode)."""

    def __init__(self, findings: list["Finding"]):
        self.findings = findings
        lines = [f"  [{f.severity}] {f.rule} @ {f.path}: {f.message}" for f in findings]
        super().__init__("Style validation failed:\n" + "\n".join(lines))


@dataclass
class Finding:
    severity: Severity
    rule: str
    path: str
    message: str


def wcag_relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    rgb = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1 = wcag_relative_luminance(fg_hex)
    l2 = wcag_relative_luminance(bg_hex)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def _is_valid_hex(value: str | None) -> bool:
    if not value or not isinstance(value, str):
        return False
    if not value.startswith("#"):
        return False
    if len(value) not in (4, 7):  # #fff or #ffffff
        return False
    try:
        int(value.lstrip("#"), 16)
        return True
    except ValueError:
        return False


# WCAG AA thresholds:
#   - 4.5:1 for normal text  (heading 38pt regular, body 12pt regular)
#   - 3.0:1 for large text (≥18pt regular or ≥14pt bold) and non-text
#         graphical objects (icon decoration, accent strokes).
#   card_title is 16pt bold — qualifies as large text (3.0).
WCAG_AA_TEXT = 4.5
WCAG_AA_LARGE_OR_GRAPHIC = 3.0


def _build_contrast_pairs(
    style: ResolvedStyle,
) -> list[tuple[str, str, str, float]]:
    """Enumerate (pair_name, fg, bg, threshold) tuples.

    Generated automatically from ResolvedStyle attributes — adding a new color
    field above and listing it here is the only change needed.
    """
    bg_candidates = {
        "card_fill": style.card_fill_color,
        "bg_overlay": style.bg_overlay_color,
    }
    # Field name -> (value, WCAG threshold).
    # heading/body are normal-weight text → 4.5:1.
    # card_title is bold large text and accent/icon are graphical → 3.0:1.
    fg_fields: dict[str, tuple[str, float]] = {
        "heading":    (style.heading_color,    WCAG_AA_TEXT),
        "body":       (style.body_color,       WCAG_AA_TEXT),
        "card_title": (style.card_title_color, WCAG_AA_LARGE_OR_GRAPHIC),
        "accent":     (style.accent_color,     WCAG_AA_LARGE_OR_GRAPHIC),
        "icon":       (style.icon_color,       WCAG_AA_LARGE_OR_GRAPHIC),
    }
    return [
        (f"{fg_name}_on_{bg_name}", fg_val, bg_val, threshold)
        for fg_name, (fg_val, threshold) in fg_fields.items()
        for bg_name, bg_val in bg_candidates.items()
    ]


def lint_resolved_style(style: ResolvedStyle) -> list[Finding]:
    findings: list[Finding] = []
    for name, fg, bg, threshold in _build_contrast_pairs(style):
        if not (_is_valid_hex(fg) and _is_valid_hex(bg)):
            continue  # skip non-hex (rgba/gradient/None)
        ratio = contrast_ratio(fg, bg)
        if ratio < threshold:
            findings.append(Finding(
                severity="warning",
                rule="contrast-ratio",
                path=f"contrast.{name}",
                message=f"{fg} on {bg} = {ratio:.2f}:1 (< WCAG AA {threshold}:1)",
            ))
    return findings


def lint_style_schema(schema: StyleSchema) -> list[Finding]:
    findings: list[Finding] = []
    palette = schema.global_tokens.palette
    fields = [
        ("semantic.heading_color",     schema.semantic.heading_color),
        ("semantic.body_color",        schema.semantic.body_color),
        ("semantic.accent_color",      schema.semantic.accent_color),
        ("semantic.card_fill_color",   schema.semantic.card_fill_color),
        ("semantic.card_title_color",  schema.semantic.card_title_color),
        ("semantic.icon_color",        schema.semantic.icon_color),
        ("semantic.bg_overlay_color",  schema.semantic.bg_overlay_color),
        ("semantic.card_shadow.color", schema.semantic.card_shadow.color),
    ]
    for field_name, value in fields:
        if isinstance(value, str) and value.startswith("palette."):
            key = value[len("palette."):]
            if key not in palette:
                findings.append(Finding(
                    severity="error",
                    rule="broken-ref",
                    path=field_name,
                    message=f"reference '{value}' not in palette: {sorted(palette.keys())}",
                ))
    return findings
