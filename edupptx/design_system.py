"""Color palettes, font definitions, and design tokens."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DesignTokens:
    """All visual parameters for a single presentation theme."""

    # Background overlay
    bg_overlay: str
    bg_overlay_alpha: float

    # Text colors
    text_primary: str
    text_secondary: str

    # Accent colors
    accent: str
    accent_light: str

    # Component colors
    icon_color: str
    shadow_color: str
    card_bg: str

    # Fonts
    font_primary: str = "Noto Sans SC"
    font_fallback: str = "Arial"

    # Font sizes (pt)
    size_title: int = 36
    size_subtitle: int = 20
    size_card_title: int = 16
    size_card_body: int = 13
    size_footer: int = 14
    size_formula: int = 18


PALETTES: dict[str, DesignTokens] = {
    "emerald": DesignTokens(
        bg_overlay="#F0FDF4",
        bg_overlay_alpha=0.75,
        text_primary="#1F2937",
        text_secondary="#4B5563",
        accent="#059669",
        accent_light="#D1FAE5",
        icon_color="#10B981",
        shadow_color="#6EE7B7",
        card_bg="#F0FDF4",
    ),
    "blue": DesignTokens(
        bg_overlay="#EFF6FF",
        bg_overlay_alpha=0.75,
        text_primary="#1E293B",
        text_secondary="#475569",
        accent="#2563EB",
        accent_light="#DBEAFE",
        icon_color="#3B82F6",
        shadow_color="#93C5FD",
        card_bg="#EFF6FF",
    ),
    "violet": DesignTokens(
        bg_overlay="#F5F3FF",
        bg_overlay_alpha=0.75,
        text_primary="#1E1B4B",
        text_secondary="#4C1D95",
        accent="#7C3AED",
        accent_light="#EDE9FE",
        icon_color="#8B5CF6",
        shadow_color="#C4B5FD",
        card_bg="#F5F3FF",
    ),
    "amber": DesignTokens(
        bg_overlay="#FFFBEB",
        bg_overlay_alpha=0.75,
        text_primary="#1C1917",
        text_secondary="#57534E",
        accent="#D97706",
        accent_light="#FEF3C7",
        icon_color="#F59E0B",
        shadow_color="#FCD34D",
        card_bg="#FFFBEB",
    ),
    "rose": DesignTokens(
        bg_overlay="#FFF1F2",
        bg_overlay_alpha=0.75,
        text_primary="#1C1917",
        text_secondary="#57534E",
        accent="#E11D48",
        accent_light="#FFE4E6",
        icon_color="#FB7185",
        shadow_color="#FDA4AF",
        card_bg="#FFF1F2",
    ),
    "slate": DesignTokens(
        bg_overlay="#F8FAFC",
        bg_overlay_alpha=0.75,
        text_primary="#0F172A",
        text_secondary="#475569",
        accent="#334155",
        accent_light="#E2E8F0",
        icon_color="#64748B",
        shadow_color="#94A3B8",
        card_bg="#F8FAFC",
    ),
}


def get_design_tokens(palette_name: str) -> DesignTokens:
    if palette_name not in PALETTES:
        palette_name = "emerald"
    return PALETTES[palette_name]
