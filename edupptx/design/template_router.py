"""Template routing, planner briefs, and template alignment helpers."""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from loguru import logger
from pydantic import BaseModel, Field

from edupptx.models import (
    ImageNeed,
    InputContext,
    PagePlan,
    PlanningDraft,
    StyleRouting,
    VisualPlan,
)


_PAGE_TEMPLATES_DIR = Path(__file__).parent / "page_templates"
_REFS_DIR = Path(__file__).parent / "references"
_METADATA_NAME = "metadata.xml"
_STYLE_GUIDE_NAME = "style_guide.md"
_PALETTE_ROUTING_REFERENCE_NAME = "palette-routing.xml"
_DEFAULT_STYLE_NAME = "shared_reusable_core"
_DEFAULT_TEMPLATE_FAMILY = "复用"
_SHARED_TEMPLATE_FAMILY = "复用"
_IGNORED_FAMILIES = {"old", "__pycache__"}
_PAGE_TYPES = (
    "cover",
    "toc",
    "section",
    "content",
    "data",
    "case",
    "closing",
    "timeline",
    "exercise",
    "summary",
    "quiz",
    "formula",
    "experiment",
)
_LAYOUT_ONLY_PAGE_TYPES = {"comparison", "relation"}
_STOP_VARIANT_TOKENS = set(_PAGE_TYPES) | _LAYOUT_ONLY_PAGE_TYPES
_TRUE_VALUES = {"1", "true", "yes", "y", "required"}


class ManifestKeywords(BaseModel):
    """Keyword buckets used for style routing."""

    strong: list[str] = Field(default_factory=list)
    weak: list[str] = Field(default_factory=list)
    negative: list[str] = Field(default_factory=list)


class PalettePreset(BaseModel):
    """A reusable color preset attached to one template family."""

    id: str = "default"
    label: str = "Default"
    keywords: list[str] = Field(default_factory=list)
    primary_color: str = "#1E40AF"
    secondary_color: str = "#3B82F6"
    accent_color: str = "#F59E0B"
    background_color_bias: str = ""
    card_bg_color: str = "#FFFFFF"
    secondary_bg_color: str = "#F8FAFC"
    text_color: str = "#1E293B"
    heading_color: str = "#0F172A"


class PaletteRoutingRule(BaseModel):
    """One global palette routing rule loaded from design references."""

    priority: int = 0
    match_terms: list[str] = Field(default_factory=list)
    apply_palette: str = ""


class ImageSlotSpec(BaseModel):
    """Structured image-slot contract used during template alignment."""

    slot_id: str = ""
    required: bool = False
    role: str = "illustration"
    aspect_ratio: str = "16:9"
    query_from: str = "topic"
    query_suffix: str = ""
    source: str = "search"
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    notes: str = ""


class PlannerPageSpec(BaseModel):
    """Planner-facing constraints for one page type."""

    page_type: str = "content"
    variant: str | None = None
    title_max_chars: int | None = None
    subtitle_max_chars: int | None = None
    body_max_chars: int | None = None
    toc_items_max: int | None = None
    preferred_layout_hints: list[str] = Field(default_factory=list)
    design_notes_hint: str = ""
    image_slots: list[ImageSlotSpec] = Field(default_factory=list)


class VariantSpec(BaseModel):
    """A soft planning reference for one concrete SVG variant."""

    stem: str
    page_type: str = "content"
    layout_hint: str = ""
    hit_keywords: list[str] = Field(default_factory=list)
    image_min: int | None = None
    image_max: int | None = None
    card_min: int | None = None
    card_max: int | None = None
    subcard_min: int | None = None
    subcard_max: int | None = None
    page_features: str = ""
    reference_rule: str = ""


class TemplateManifest(BaseModel):
    """Normalized metadata for one template family."""

    style_name: str
    label: str = ""
    description: str = ""
    template_family: str = ""
    keywords: ManifestKeywords = Field(default_factory=ManifestKeywords)
    preferred_layout_hints: list[str] = Field(default_factory=list)
    palette_presets: list[PalettePreset] = Field(default_factory=list)
    page_variants: dict[str, list[str]] = Field(default_factory=dict)
    planner_summary: str = ""
    planner_page_specs: dict[str, PlannerPageSpec] = Field(default_factory=dict)
    variant_specs: dict[str, VariantSpec] = Field(default_factory=dict)


def _default_palette_preset(palette_id: str = "default", label: str = "Default") -> PalettePreset:
    visual = VisualPlan()
    return PalettePreset(
        id=palette_id,
        label=label,
        primary_color=visual.primary_color,
        secondary_color=visual.secondary_color,
        accent_color=visual.accent_color,
        background_color_bias=visual.background_color_bias,
        card_bg_color=visual.card_bg_color,
        secondary_bg_color=visual.secondary_bg_color,
        text_color=visual.text_color,
        heading_color=visual.heading_color,
    )


def _append_if_text(parts: list[str], value: Any) -> None:
    if value is None:
        return
    text = str(value).strip()
    if text:
        parts.append(text)


def _flatten_content_points(value: Any) -> list[str]:
    flattened: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            flattened.extend(_flatten_content_points(item))
        return flattened
    if isinstance(value, (list, tuple)):
        for item in value:
            flattened.extend(_flatten_content_points(item))
        return flattened
    _append_if_text(flattened, value)
    return flattened


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _split_tokens(value: str) -> list[str]:
    tokens = [
        token.strip()
        for token in re.split(r"[,\n;/|，、]+", value or "")
        if token.strip()
    ]
    return _dedupe_keep_order(tokens)


def _relative_family_name(family_dir: Path) -> str:
    return family_dir.relative_to(_PAGE_TEMPLATES_DIR).as_posix()


def _normalize_page_type(value: str | None) -> str:
    page_type = str(value or "").strip()
    if page_type in _LAYOUT_ONLY_PAGE_TYPES:
        return "content"
    return page_type


def _is_backup_svg_stem(stem: str) -> bool:
    normalized = stem.casefold().strip()
    return bool(re.search(r"(?:^|[_\-\s])copy(?:\s*\d+)?$", normalized))


def _discover_page_variants(family_dir: Path) -> dict[str, list[str]]:
    variants: dict[str, list[str]] = {}
    for path in sorted(family_dir.glob("*.svg")):
        stem = path.stem
        if _is_backup_svg_stem(stem):
            continue
        matched = False
        for page_type in _PAGE_TYPES:
            if stem == page_type or stem.startswith(f"{page_type}_"):
                variants.setdefault(page_type, []).append(stem)
                matched = True
                break
        if matched:
            continue
        for layout_only_type in _LAYOUT_ONLY_PAGE_TYPES:
            if stem == layout_only_type or stem.startswith(f"{layout_only_type}_"):
                variants.setdefault("content", []).append(stem)
                break
    for page_type, stems in list(variants.items()):
        exact = [stem for stem in stems if stem == page_type]
        variants[page_type] = _dedupe_keep_order(exact + stems)
    return variants


def _iter_template_family_dirs() -> list[Path]:
    families: list[Path] = []
    for family_dir in sorted(_PAGE_TEMPLATES_DIR.rglob("*")):
        if not family_dir.is_dir():
            continue
        rel_parts = family_dir.relative_to(_PAGE_TEMPLATES_DIR).parts
        if not rel_parts:
            continue
        if any(part in _IGNORED_FAMILIES for part in rel_parts):
            continue
        has_metadata = (family_dir / _METADATA_NAME).exists()
        has_svgs = any(family_dir.glob("*.svg"))
        has_child_dirs = any(
            child.is_dir() and child.name not in _IGNORED_FAMILIES
            for child in family_dir.iterdir()
        )
        if has_metadata or (has_svgs and not has_child_dirs):
            families.append(family_dir)
    return families


def _build_implicit_manifest(family_dir: Path) -> TemplateManifest:
    family_name = _relative_family_name(family_dir)
    return TemplateManifest(
        style_name=family_name.replace("/", "_"),
        label=family_dir.name.replace("_", " ").title(),
        description="Implicit manifest generated from template files.",
        template_family=family_name,
        palette_presets=[_default_palette_preset()],
        page_variants=_discover_page_variants(family_dir),
    )


def _node_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def _child_text(parent: ET.Element | None, tag: str) -> str:
    if parent is None:
        return ""
    return _node_text(parent.find(tag))


def _text_list(parent: ET.Element | None, tag: str) -> list[str]:
    if parent is None:
        return []
    node = parent.find(tag)
    if node is None:
        return []
    children_text = [_node_text(child) for child in list(node) if _node_text(child)]
    if children_text:
        return _dedupe_keep_order(children_text)
    return _split_tokens(_node_text(node))


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().casefold() in _TRUE_VALUES


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _as_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_palette_node(node: ET.Element | None) -> PalettePreset | None:
    if node is None:
        return None
    palette_id = node.get("id") or _child_text(node, "id") or "default"
    return PalettePreset(
        id=palette_id,
        label=_child_text(node, "label") or palette_id.replace("_", " ").title(),
        keywords=_text_list(node, "keywords"),
        primary_color=_child_text(node, "primary") or "#1E40AF",
        secondary_color=_child_text(node, "secondary") or "#3B82F6",
        accent_color=_child_text(node, "accent") or "#F59E0B",
        card_bg_color=_child_text(node, "card_bg") or "#FFFFFF",
        secondary_bg_color=_child_text(node, "secondary_bg") or "#F8FAFC",
        text_color=_child_text(node, "text") or "#1E293B",
        heading_color=_child_text(node, "heading") or "#0F172A",
    )


def _parse_palette_routing_rule_node(node: ET.Element | None) -> PaletteRoutingRule | None:
    if node is None:
        return None
    apply_palette = node.get("apply_palette") or _child_text(node, "apply_palette")
    if not apply_palette:
        return None
    priority = _as_int(node.get("priority") or _child_text(node, "priority")) or 0
    return PaletteRoutingRule(
        priority=priority,
        match_terms=_split_tokens(_child_text(node, "match_terms")),
        apply_palette=apply_palette.strip(),
    )


def _parse_page_spec_node(node: ET.Element) -> PlannerPageSpec | None:
    page_type = node.get("type") or _child_text(node, "type")
    if not page_type:
        return None
    page_type = _normalize_page_type(page_type)

    image_slots: list[ImageSlotSpec] = []
    slots_parent = node.find("image_slots")
    if slots_parent is not None:
        for slot_node in slots_parent.findall("slot"):
            image_slots.append(ImageSlotSpec(
                slot_id=slot_node.get("id") or _child_text(slot_node, "id"),
                required=_as_bool(slot_node.get("required") or _child_text(slot_node, "required")),
                role=slot_node.get("role") or _child_text(slot_node, "role") or "illustration",
                aspect_ratio=slot_node.get("aspect_ratio") or _child_text(slot_node, "aspect_ratio") or "16:9",
                query_from=slot_node.get("query_from") or _child_text(slot_node, "query_from") or "topic",
                query_suffix=slot_node.get("query_suffix") or _child_text(slot_node, "query_suffix"),
                source=slot_node.get("source") or _child_text(slot_node, "source") or "search",
                x=_as_float(slot_node.get("x") or _child_text(slot_node, "x")),
                y=_as_float(slot_node.get("y") or _child_text(slot_node, "y")),
                width=_as_float(slot_node.get("width") or _child_text(slot_node, "width")),
                height=_as_float(slot_node.get("height") or _child_text(slot_node, "height")),
                notes=_child_text(slot_node, "notes"),
            ))

    return PlannerPageSpec(
        page_type=page_type,
        variant=node.get("variant") or _child_text(node, "variant") or None,
        title_max_chars=_as_int(_child_text(node, "title_max_chars")),
        subtitle_max_chars=_as_int(_child_text(node, "subtitle_max_chars")),
        body_max_chars=_as_int(_child_text(node, "body_max_chars")),
        toc_items_max=_as_int(_child_text(node, "toc_items_max")),
        preferred_layout_hints=_text_list(node, "preferred_layout_hints"),
        design_notes_hint=_child_text(node, "design_notes_hint"),
        image_slots=image_slots,
    )


def _parse_range_node(parent: ET.Element, tag: str) -> tuple[int | None, int | None]:
    node = parent.find(tag)
    if node is None:
        return None, None
    min_value = _as_int(node.get("min") or _child_text(node, "min"))
    max_value = _as_int(node.get("max") or _child_text(node, "max"))
    return min_value, max_value


def _parse_variant_spec_node(node: ET.Element) -> VariantSpec | None:
    stem = node.get("stem") or _child_text(node, "stem")
    if not stem:
        return None

    image_min, image_max = _parse_range_node(node, "image_range")
    card_min, card_max = _parse_range_node(node, "card_range")
    subcard_min, subcard_max = _parse_range_node(node, "subcard_range")
    return VariantSpec(
        stem=stem,
        page_type=_normalize_page_type(node.get("page_type") or _child_text(node, "page_type") or "content"),
        layout_hint=node.get("layout_hint") or _child_text(node, "layout_hint"),
        hit_keywords=_text_list(node, "hit_keywords"),
        image_min=image_min,
        image_max=image_max,
        card_min=card_min,
        card_max=card_max,
        subcard_min=subcard_min,
        subcard_max=subcard_max,
        page_features=_child_text(node, "page_features"),
        reference_rule=_child_text(node, "reference_rule"),
    )


@lru_cache(maxsize=1)
def _load_reference_palette_rules() -> list[PaletteRoutingRule]:
    path = _REFS_DIR / _PALETTE_ROUTING_REFERENCE_NAME
    if not path.exists():
        return []

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        logger.warning("Failed to parse palette routing reference {}: {}", path, exc)
        return []

    rules: list[PaletteRoutingRule] = []
    palette_routing = root.find("palette_routing")
    if palette_routing is not None:
        for node in palette_routing.findall("rule"):
            rule = _parse_palette_routing_rule_node(node)
            if rule is not None:
                rules.append(rule)
    rules.sort(key=lambda item: item.priority, reverse=True)
    return rules


@lru_cache(maxsize=64)
def _load_reference_palette_by_id(palette_id: str) -> PalettePreset | None:
    normalized = str(palette_id or "").strip()
    if not normalized:
        return None

    path = _REFS_DIR / _PALETTE_ROUTING_REFERENCE_NAME
    if not path.exists():
        return None

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        logger.warning("Failed to parse palette routing reference {}: {}", path, exc)
        return None

    palette: PalettePreset | None = None
    palette_library = root.find("palette_library")
    if palette_library is not None:
        for node in palette_library.findall("palette"):
            candidate_id = node.get("id") or _child_text(node, "id") or "default"
            if candidate_id == normalized:
                palette = _parse_palette_node(node)
                break
    if palette is None:
        return None

    palette_color_bias = root.find("palette_color_bias")
    if palette_color_bias is not None:
        for node in palette_color_bias.findall("palette"):
            candidate_id = node.get("id") or _child_text(node, "id")
            if candidate_id != normalized:
                continue
            color_bias = _child_text(node, "color_bias")
            if color_bias:
                palette.background_color_bias = color_bias
            break
    return palette


def _load_metadata_file(metadata_path: Path) -> TemplateManifest:
    root = ET.fromstring(metadata_path.read_text(encoding="utf-8"))
    family_name = _relative_family_name(metadata_path.parent)
    identity = root.find("identity")
    routing = root.find("routing")
    planner = root.find("planner")

    # Palette and background routing are now driven by global design references.
    # Template metadata only carries structural planning information.
    palettes = [_default_palette_preset()]

    page_specs: dict[str, PlannerPageSpec] = {}
    variant_specs: dict[str, VariantSpec] = {}
    if planner is not None:
        for page_node in planner.findall("page"):
            spec = _parse_page_spec_node(page_node)
            if spec is not None:
                if spec.page_type in page_specs:
                    page_specs[spec.page_type] = _merge_page_spec(page_specs[spec.page_type], spec)
                else:
                    page_specs[spec.page_type] = spec
        variant_catalog = planner.find("variant_catalog")
        if variant_catalog is not None:
            for variant_node in variant_catalog.findall("variant"):
                variant_spec = _parse_variant_spec_node(variant_node)
                if variant_spec is not None:
                    variant_specs[variant_spec.stem] = variant_spec

    preferred_layout_hints = _dedupe_keep_order([
        *(_text_list(root, "preferred_layout_hints")),
        *(
            hint
            for spec in page_specs.values()
            for hint in spec.preferred_layout_hints
        ),
        *(
            spec.layout_hint
            for spec in variant_specs.values()
            if spec.layout_hint
        ),
    ])

    strong_keywords = _dedupe_keep_order([
        *(_text_list(routing, "priority_keywords")),
        *(_text_list(identity, "tags")),
    ])
    weak_keywords = _dedupe_keep_order([
        *(_text_list(routing, "subjects")),
        *(_text_list(routing, "scenes")),
    ])
    negative_keywords = _text_list(routing, "negative_keywords")

    manifest = TemplateManifest(
        style_name=_child_text(identity, "name") or family_name.replace("/", "_"),
        label=_child_text(identity, "label") or metadata_path.parent.name,
        description=_child_text(identity, "intro"),
        template_family=family_name,
        keywords=ManifestKeywords(
            strong=strong_keywords,
            weak=weak_keywords,
            negative=negative_keywords,
        ),
        preferred_layout_hints=preferred_layout_hints,
        palette_presets=palettes,
        page_variants=_discover_page_variants(metadata_path.parent),
        planner_summary=_child_text(planner, "summary"),
        planner_page_specs=page_specs,
        variant_specs=variant_specs,
    )
    return manifest


def load_style_manifests() -> list[TemplateManifest]:
    """Load explicit metadata.xml files and synthesize defaults for bare folders."""

    manifests_by_family: dict[str, TemplateManifest] = {}
    for family_dir in _iter_template_family_dirs():
        metadata_path = family_dir / _METADATA_NAME
        try:
            if metadata_path.exists():
                manifest = _load_metadata_file(metadata_path)
            else:
                manifest = _build_implicit_manifest(family_dir)
        except Exception as exc:
            logger.warning("Failed to parse template metadata in {}: {}", family_dir, str(exc)[:120])
            manifest = _build_implicit_manifest(family_dir)
        manifests_by_family[manifest.template_family] = manifest
    return list(manifests_by_family.values())


def _combine_text_blocks(*values: str) -> str:
    parts = [value.strip() for value in values if str(value or "").strip()]
    return "\n".join(parts)


def _merge_image_slots(
    primary_slots: list[ImageSlotSpec],
    shared_slots: list[ImageSlotSpec],
) -> list[ImageSlotSpec]:
    merged = [slot.model_copy(deep=True) for slot in primary_slots]
    existing_ids = {slot.slot_id for slot in merged if slot.slot_id}
    for slot in shared_slots:
        if slot.slot_id and slot.slot_id in existing_ids:
            continue
        merged.append(slot.model_copy(deep=True))
    return merged


def _merge_page_spec(
    primary: PlannerPageSpec,
    shared: PlannerPageSpec,
) -> PlannerPageSpec:
    variant = primary.variant or shared.variant
    if primary.variant and shared.variant and primary.variant != shared.variant:
        variant = None
    return PlannerPageSpec(
        page_type=primary.page_type or shared.page_type,
        variant=variant,
        title_max_chars=primary.title_max_chars if primary.title_max_chars is not None else shared.title_max_chars,
        subtitle_max_chars=primary.subtitle_max_chars if primary.subtitle_max_chars is not None else shared.subtitle_max_chars,
        body_max_chars=primary.body_max_chars if primary.body_max_chars is not None else shared.body_max_chars,
        toc_items_max=primary.toc_items_max if primary.toc_items_max is not None else shared.toc_items_max,
        preferred_layout_hints=_dedupe_keep_order(primary.preferred_layout_hints + shared.preferred_layout_hints),
        design_notes_hint=_combine_text_blocks(shared.design_notes_hint, primary.design_notes_hint),
        image_slots=_merge_image_slots(primary.image_slots, shared.image_slots),
    )


def merge_template_manifests(
    primary: TemplateManifest,
    shared: TemplateManifest | None,
) -> TemplateManifest:
    if shared is None or primary.template_family == _SHARED_TEMPLATE_FAMILY:
        return primary

    merged_page_variants: dict[str, list[str]] = {}
    for page_type in set(shared.page_variants) | set(primary.page_variants):
        merged_page_variants[page_type] = _dedupe_keep_order(
            primary.page_variants.get(page_type, []) + shared.page_variants.get(page_type, [])
        )

    merged_page_specs: dict[str, PlannerPageSpec] = {}
    for page_type in set(shared.planner_page_specs) | set(primary.planner_page_specs):
        primary_spec = primary.planner_page_specs.get(page_type)
        shared_spec = shared.planner_page_specs.get(page_type)
        if primary_spec is not None and shared_spec is not None:
            merged_page_specs[page_type] = _merge_page_spec(primary_spec, shared_spec)
            if page_type == "content":
                merged_page_specs[page_type].variant = None
        elif primary_spec is not None:
            merged_page_specs[page_type] = primary_spec.model_copy(deep=True)
        elif shared_spec is not None:
            merged_page_specs[page_type] = shared_spec.model_copy(deep=True)

    merged_variant_specs = {
        stem: spec.model_copy(deep=True)
        for stem, spec in shared.variant_specs.items()
    }
    merged_variant_specs.update({
        stem: spec.model_copy(deep=True)
        for stem, spec in primary.variant_specs.items()
    })

    return TemplateManifest(
        style_name=primary.style_name,
        label=primary.label,
        description=primary.description or shared.description,
        template_family=primary.template_family,
        keywords=primary.keywords.model_copy(deep=True),
        preferred_layout_hints=_dedupe_keep_order(primary.preferred_layout_hints + shared.preferred_layout_hints),
        palette_presets=[preset.model_copy(deep=True) for preset in primary.palette_presets],
        page_variants=merged_page_variants,
        planner_summary=_combine_text_blocks(primary.planner_summary, shared.planner_summary),
        planner_page_specs=merged_page_specs,
        variant_specs=merged_variant_specs,
    )


def load_style_manifest(template_family: str, merge_shared: bool = True) -> TemplateManifest | None:
    """Load one manifest by template-family path."""

    normalized = template_family.replace("\\", "/").strip("/")
    raw_manifest: TemplateManifest | None = None
    for manifest in load_style_manifests():
        if manifest.template_family == normalized:
            raw_manifest = manifest
            break
    if raw_manifest is None:
        return None
    if not merge_shared or normalized == _SHARED_TEMPLATE_FAMILY:
        return raw_manifest
    shared_manifest = load_style_manifest(_SHARED_TEMPLATE_FAMILY, merge_shared=False)
    return merge_template_manifests(raw_manifest, shared_manifest)


def load_style_guide(template_family: str, include_shared: bool = True) -> str:
    """Load `style_guide.md` from one selected family, optionally merged with shared guidance."""

    normalized = template_family.replace("\\", "/").strip("/")
    guides: list[str] = []
    if include_shared and normalized != _SHARED_TEMPLATE_FAMILY:
        shared_path = _PAGE_TEMPLATES_DIR / _SHARED_TEMPLATE_FAMILY / _STYLE_GUIDE_NAME
        if shared_path.exists():
            guides.append(shared_path.read_text(encoding="utf-8").strip())
    guide_path = _PAGE_TEMPLATES_DIR / normalized / _STYLE_GUIDE_NAME
    if guide_path.exists():
        guides.append(guide_path.read_text(encoding="utf-8").strip())
    return "\n\n".join(part for part in guides if part)


def collect_template_routing_text(draft: PlanningDraft) -> str:
    """Collect deck-level text used for keyword and LLM routing."""

    parts: list[str] = []
    meta = getattr(draft, "meta", None)
    if meta is not None:
        _append_if_text(parts, getattr(meta, "topic", ""))
        _append_if_text(parts, getattr(meta, "style_direction", ""))
        _append_if_text(parts, getattr(meta, "audience", ""))
        _append_if_text(parts, getattr(meta, "purpose", ""))
    _append_if_text(parts, getattr(draft, "research_context", ""))

    for page in getattr(draft, "pages", []) or []:
        _append_if_text(parts, getattr(page, "page_type", ""))
        _append_if_text(parts, getattr(page, "layout_hint", ""))
        _append_if_text(parts, getattr(page, "title", ""))
        _append_if_text(parts, getattr(page, "subtitle", ""))
        _append_if_text(parts, getattr(page, "design_notes", ""))
        for point in _flatten_content_points(getattr(page, "content_points", [])):
            _append_if_text(parts, point)

    return "\n".join(parts)


def collect_input_routing_text(ctx: InputContext) -> str:
    """Collect routing text before planning is generated."""

    parts: list[str] = [ctx.topic]
    _append_if_text(parts, ctx.requirements)
    if ctx.source_text:
        _append_if_text(parts, ctx.source_text[:2000])
    if ctx.research_summary:
        _append_if_text(parts, ctx.research_summary)
    return "\n".join(parts)


def _score_manifest(
    manifest: TemplateManifest,
    routing_text: str,
    layout_hints: list[str] | tuple[str, ...] = (),
) -> int:
    normalized = routing_text.casefold()
    score = 0
    for keyword in manifest.keywords.strong:
        if keyword and keyword.casefold() in normalized:
            score += 4
    for keyword in manifest.keywords.weak:
        if keyword and keyword.casefold() in normalized:
            score += 2
    for keyword in manifest.keywords.negative:
        if keyword and keyword.casefold() in normalized:
            score -= 3
    if manifest.style_name.casefold() in normalized:
        score += 5
    if manifest.template_family.casefold() in normalized:
        score += 3
    if manifest.label and manifest.label.casefold() in normalized:
        score += 3
    for layout_hint in layout_hints:
        if layout_hint in manifest.preferred_layout_hints:
            score += 1
    return score


def score_style_manifests(
    draft: PlanningDraft,
    manifests: list[TemplateManifest] | None = None,
) -> dict[str, int]:
    """Return keyword-match scores per style manifest for one planning draft."""

    manifests = manifests or load_style_manifests()
    routing_text = collect_template_routing_text(draft)
    layout_hints = [page.layout_hint for page in draft.pages]
    return {
        manifest.style_name: _score_manifest(manifest, routing_text, layout_hints)
        for manifest in manifests
    }


def _choose_manifest_by_keywords(
    routing_text: str,
    manifests: list[TemplateManifest],
    layout_hints: list[str] | tuple[str, ...] = (),
) -> tuple[TemplateManifest | None, str]:
    if not manifests:
        return None, "fallback"

    scored = [
        (manifest, _score_manifest(manifest, routing_text, layout_hints))
        for manifest in manifests
    ]
    ranked = sorted(scored, key=lambda item: item[1], reverse=True)
    top_manifest, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0
    if top_score > 0 and top_score - second_score >= 2:
        return top_manifest, "keyword"
    return None, "fallback"


def _summarize_manifest(manifest: TemplateManifest) -> str:
    layout_hints = ", ".join(manifest.preferred_layout_hints[:4]) or "none"
    palette_ids = ", ".join(preset.id for preset in manifest.palette_presets[:4]) or "default"
    return (
        f"- {manifest.style_name}: template_family={manifest.template_family}; "
        f"layouts={layout_hints}; palettes={palette_ids}; description={manifest.description or manifest.label}"
    )


def _choose_manifest_with_llm(
    client: Any,
    routing_text: str,
    manifests: list[TemplateManifest],
    layout_hints: list[str] | tuple[str, ...] = (),
) -> TemplateManifest | None:
    if client is None or not manifests:
        return None

    scores = {
        manifest.style_name: _score_manifest(manifest, routing_text, layout_hints)
        for manifest in manifests
    }
    ranked = sorted(
        manifests,
        key=lambda manifest: scores.get(manifest.style_name, 0),
        reverse=True,
    )
    candidates = ranked[: min(3, len(ranked))]
    messages = [
        {
            "role": "system",
            "content": (
                "You choose one PPT style manifest for an educational deck.\n"
                "Return exactly one style_name from the allowed list.\n"
                "Do not explain."
            ),
        },
        {
            "role": "user",
            "content": (
                "Candidates:\n"
                + "\n".join(_summarize_manifest(manifest) for manifest in candidates)
                + "\n\nAllowed outputs: "
                + ", ".join(manifest.style_name for manifest in candidates)
                + "\n\nDeck content:\n"
                + routing_text
            ),
        },
    ]
    try:
        response = str(client.chat(messages=messages, temperature=0.0, max_tokens=32)).strip()
    except Exception as exc:
        logger.warning("Style manifest LLM routing failed: {}", str(exc)[:120])
        return None
    for manifest in candidates:
        if manifest.style_name in response:
            return manifest
    return None


def _default_manifest_from_list(manifests: list[TemplateManifest]) -> TemplateManifest:
    if not manifests:
        family_dir = _PAGE_TEMPLATES_DIR / _DEFAULT_TEMPLATE_FAMILY
        if family_dir.exists():
            return _build_implicit_manifest(family_dir)
        raise FileNotFoundError(f"No template families available in {_PAGE_TEMPLATES_DIR}")
    for manifest in manifests:
        if manifest.style_name == _DEFAULT_STYLE_NAME or manifest.template_family == _DEFAULT_TEMPLATE_FAMILY:
            return manifest
    return manifests[0]


def _find_palette_by_id(manifest: TemplateManifest, palette_id: str | None) -> PalettePreset | None:
    normalized = str(palette_id or "").strip()
    if not normalized:
        return None
    for preset in manifest.palette_presets:
        if preset.id == normalized:
            return preset
    return _load_reference_palette_by_id(normalized)


def _choose_reference_palette(routing_text: str) -> tuple[PalettePreset | None, PaletteRoutingRule | None]:
    rules = _load_reference_palette_rules()
    if not rules:
        return None, None

    normalized = routing_text.casefold()
    scored: list[tuple[int, int, int, PaletteRoutingRule]] = []
    for rule in rules:
        if rule.match_terms:
            hits = sum(1 for term in rule.match_terms if term and term.casefold() in normalized)
            if hits == 0:
                continue
        else:
            hits = 0
        scored.append((rule.priority, hits, len(rule.match_terms), rule))

    if not scored:
        return None, None
    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    for _priority, _hits, _term_count, rule in scored:
        palette = _load_reference_palette_by_id(rule.apply_palette)
        if palette is not None:
            return palette, rule
    return None, scored[0][3]


def resolve_palette_preset(
    manifest: TemplateManifest,
    routing_text: str = "",
    preferred_palette_id: str | None = None,
) -> PalettePreset:
    preferred = _find_palette_by_id(manifest, preferred_palette_id)
    if preferred is not None:
        return preferred

    reference_palette, reference_rule = _choose_reference_palette(routing_text)
    if reference_palette is not None and reference_rule is not None:
        return reference_palette

    return _default_palette_preset()


def _choose_palette_preset(manifest: TemplateManifest, routing_text: str) -> PalettePreset:
    return resolve_palette_preset(manifest, routing_text)


def apply_palette_preset(visual: VisualPlan, preset: PalettePreset) -> None:
    """Apply one selected palette preset onto a `VisualPlan`."""

    visual.primary_color = preset.primary_color
    visual.secondary_color = preset.secondary_color
    visual.accent_color = preset.accent_color
    visual.card_bg_color = preset.card_bg_color
    visual.secondary_bg_color = preset.secondary_bg_color
    visual.text_color = preset.text_color
    visual.heading_color = preset.heading_color
    if preset.background_color_bias:
        visual.background_color_bias = preset.background_color_bias


def _page_routing_text(page: PagePlan) -> str:
    parts: list[str] = []
    _append_if_text(parts, page.page_type)
    _append_if_text(parts, page.layout_hint)
    _append_if_text(parts, page.title)
    _append_if_text(parts, page.subtitle)
    _append_if_text(parts, page.design_notes)
    for point in _flatten_content_points(page.content_points):
        _append_if_text(parts, point)
    return "\n".join(parts)


def _variant_tokens(stem: str) -> list[str]:
    return [
        token
        for token in re.split(r"[_\-\s]+", stem.casefold())
        if token and token not in _STOP_VARIANT_TOKENS
    ]


def _score_hero_with_microcards_variant(spec: VariantSpec, page: PagePlan) -> int:
    if page.layout_hint != "hero_with_microcards":
        return 0
    if not spec.stem.startswith("content_hero_with_microcards_"):
        return 0

    points = [text.strip() for text in _flatten_content_points(page.content_points) if str(text).strip()]
    if not points:
        return 0

    point_count = len(points)
    lengths = [len(text) for text in points]
    avg_len = sum(lengths) / point_count
    max_len = max(lengths)
    has_subtitle = bool(str(page.subtitle or "").strip())

    if spec.stem.endswith("_1"):
        score = 0
        if 2 <= point_count <= 6:
            score += 2
        if avg_len <= 18:
            score += 2
        if max_len <= 28:
            score += 1
        if has_subtitle and point_count >= 4:
            score -= 1
        return score

    if spec.stem.endswith("_2"):
        score = 0
        if has_subtitle and point_count >= 4:
            score += 5
        elif has_subtitle:
            score += 2
        if 1 <= point_count <= 8:
            score += 1
        if avg_len >= 8 or max_len >= 14:
            score += 1
        return score

    return 0


def _score_toc_variant(spec: VariantSpec, page: PagePlan) -> int:
    if page.page_type != "toc" or spec.page_type != "toc":
        return 0

    point_count = len(_flatten_content_points(page.content_points))
    if point_count <= 0:
        return 0

    if spec.stem == "toc_1":
        return 6 if point_count <= 5 else -3

    if spec.stem == "toc_2":
        return 6 if point_count > 5 else -3

    return 0


def _estimate_top_level_card_count(page: PagePlan) -> int | None:
    """Estimate the number of outer cards, excluding nested micro/subcards."""

    point_count = len(_flatten_content_points(page.content_points))
    layout_hint = page.layout_hint

    if page.page_type in {"cover", "section", "closing"}:
        return 1
    if page.page_type == "toc":
        return point_count or None

    if layout_hint in {"center_hero", "full_image", "hero_with_microcards", "relation"}:
        return 1
    if layout_hint == "comparison":
        routing_text = _page_routing_text(page)
        if any(keyword in routing_text for keyword in ("先总后分", "概述", "总结", "结论")):
            return 2
        return 1
    if layout_hint in {"bento_2col_equal", "bento_2col_asymmetric"}:
        return 2
    if layout_hint == "bento_3col":
        return 3
    if layout_hint in {"hero_top_cards_bottom", "cards_top_hero_bottom"}:
        if point_count <= 0:
            return 3
        return min(max(point_count, 2), 4)
    if layout_hint == "mixed_grid":
        if point_count <= 0:
            return None
        return min(max(point_count, 2), 5)
    if page.page_type in {"exercise", "quiz", "summary"}:
        if point_count <= 0:
            return None
        return min(max(point_count, 2), 4)

    return point_count or None


def _estimate_subcard_count(page: PagePlan) -> int | None:
    """Estimate nested card-like units such as microcards, nodes, or table rows."""

    point_count = len(_flatten_content_points(page.content_points))
    if point_count <= 0:
        return None

    if page.layout_hint in {"hero_with_microcards", "relation", "comparison"}:
        return point_count

    return None


def _score_variant_spec(spec: VariantSpec, page: PagePlan) -> int:
    normalized = _page_routing_text(page).casefold()
    score = 0

    if _normalize_page_type(spec.page_type) == _normalize_page_type(page.page_type):
        score += 2
    if spec.layout_hint:
        if spec.layout_hint == page.layout_hint:
            score += 5
        else:
            score -= 1

    keyword_hits = 0
    for keyword in spec.hit_keywords:
        if keyword and keyword.casefold() in normalized:
            keyword_hits += 1
    score += keyword_hits * 2

    for token in _variant_tokens(spec.stem):
        if token in normalized:
            score += 1

    top_level_card_count = _estimate_top_level_card_count(page)
    if top_level_card_count is not None and spec.card_min is not None and spec.card_max is not None:
        if spec.card_min <= top_level_card_count <= spec.card_max:
            score += 2
        elif top_level_card_count < max(1, spec.card_min - 1) or top_level_card_count > spec.card_max + 1:
            score -= 1

    subcard_count = _estimate_subcard_count(page)
    if subcard_count is not None and spec.subcard_min is not None and spec.subcard_max is not None:
        if spec.subcard_min <= subcard_count <= spec.subcard_max:
            score += 2
        elif subcard_count < max(1, spec.subcard_min - 1) or subcard_count > spec.subcard_max + 1:
            score -= 1

    score += _score_toc_variant(spec, page)
    score += _score_hero_with_microcards_variant(spec, page)

    return score


def _stable_variant_tiebreaker(page: PagePlan, stem: str) -> int:
    """Break equal-fit template ties without favoring the primary family order."""

    seed = "|".join([
        str(page.page_number),
        page.page_type or "",
        page.layout_hint or "",
        page.title or "",
        stem,
    ])
    return int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8], 16)


def _choose_page_variant_with_llm(client: Any, page: PagePlan, candidates: list[str]) -> str | None:
    if client is None or len(candidates) <= 1:
        return None
    messages = [
        {
            "role": "system",
            "content": (
                "You choose one SVG template variant stem for a PPT page.\n"
                "Return exactly one candidate stem.\n"
                "Do not explain."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Page type: {page.page_type}\n"
                f"Layout hint: {page.layout_hint}\n"
                f"Title: {page.title}\n"
                f"Subtitle: {page.subtitle or ''}\n"
                f"Design notes: {page.design_notes or ''}\n"
                f"Candidates: {', '.join(candidates)}\n"
            ),
        },
    ]
    try:
        response = str(client.chat(messages=messages, temperature=0.0, max_tokens=32)).strip()
    except Exception as exc:
        logger.warning("Template variant LLM routing failed: {}", str(exc)[:120])
        return None
    for candidate in candidates:
        if candidate in response:
            return candidate
    return None


def resolve_page_template_variant(
    page: PagePlan,
    manifest: TemplateManifest,
    client: Any = None,
) -> str | None:
    """Resolve the preferred template stem for one page."""

    page_type = _normalize_page_type(page.page_type)
    variant_specs = [
        spec
        for spec in manifest.variant_specs.values()
        if _normalize_page_type(spec.page_type) == page_type
    ]
    if variant_specs:
        scored_specs = sorted(
            ((spec, _score_variant_spec(spec, page)) for spec in variant_specs),
            key=lambda item: (-item[1], _stable_variant_tiebreaker(page, item[0].stem), item[0].stem),
        )
        top_spec, top_score = scored_specs[0]
        second_score = scored_specs[1][1] if len(scored_specs) > 1 else -999
        if top_score >= 5 and top_score - second_score >= 2:
            return top_spec.stem
        llm_candidates = [spec.stem for spec, score in scored_specs[:3] if score >= 4]
        if llm_candidates:
            llm_choice = _choose_page_variant_with_llm(client, page, llm_candidates)
            if llm_choice:
                return llm_choice
        if top_score >= 6:
            return top_spec.stem
        if page_type == "content":
            return None

    candidates = list(manifest.page_variants.get(page_type, []))
    if not candidates and page_type == "content":
        candidates = list(manifest.page_variants.get("content", []))

    spec = manifest.planner_page_specs.get(page_type)
    if spec is None and page_type == "content":
        spec = manifest.planner_page_specs.get("content")
    if spec and spec.variant:
        candidates = _dedupe_keep_order([spec.variant] + candidates)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    normalized = _page_routing_text(page).casefold()
    scored: list[tuple[str, int]] = []
    for candidate in candidates:
        score = 0
        if candidate == page_type:
            score += 2
        if candidate.startswith(f"{page_type}_"):
            score += 1
        for token in _variant_tokens(candidate):
            if token in normalized:
                score += 2
        scored.append((candidate, score))
    scored.sort(key=lambda item: (-item[1], _stable_variant_tiebreaker(page, item[0]), item[0]))
    top_candidate, top_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else -999
    if top_score > second_score:
        return top_candidate
    llm_choice = _choose_page_variant_with_llm(client, page, [candidate for candidate, _ in scored[:3]])
    return llm_choice or top_candidate


def resolve_style_routing_from_text(
    routing_text: str,
    client: Any = None,
    layout_hints: list[str] | tuple[str, ...] = (),
) -> tuple[StyleRouting, TemplateManifest, PalettePreset]:
    """Resolve style routing from free text plus optional layout hints."""

    manifests = load_style_manifests()
    primary_manifests = [
        manifest
        for manifest in manifests
        if manifest.template_family != _SHARED_TEMPLATE_FAMILY
    ] or manifests

    manifest, resolved_by = _choose_manifest_by_keywords(routing_text, primary_manifests, layout_hints)
    if manifest is None:
        has_primary_signal = any(
            _score_manifest(candidate, routing_text, layout_hints) > 0
            for candidate in primary_manifests
        )
        if has_primary_signal:
            manifest = _choose_manifest_with_llm(client, routing_text, primary_manifests, layout_hints)
            if manifest is not None:
                resolved_by = "llm"
    if manifest is None:
        manifest = _default_manifest_from_list(manifests)
        resolved_by = "fallback"

    effective_manifest = merge_template_manifests(
        manifest,
        load_style_manifest(_SHARED_TEMPLATE_FAMILY, merge_shared=False),
    )
    palette = _choose_palette_preset(effective_manifest, routing_text)
    routing = StyleRouting(
        style_name=manifest.style_name,
        template_family=manifest.template_family,
        palette_id=palette.id,
        resolved_by=resolved_by,
    )
    return routing, effective_manifest, palette


def resolve_style_routing_from_input(
    ctx: InputContext,
    client: Any = None,
) -> tuple[StyleRouting, TemplateManifest, PalettePreset]:
    """Resolve template routing before the planning phase."""

    return resolve_style_routing_from_text(collect_input_routing_text(ctx), client=client)


def resolve_style_routing(
    draft: PlanningDraft,
    client: Any = None,
) -> tuple[StyleRouting, TemplateManifest, PalettePreset]:
    """Resolve deck-level style routing and palette preset."""

    return resolve_style_routing_from_text(
        collect_template_routing_text(draft),
        client=client,
        layout_hints=[page.layout_hint for page in draft.pages],
    )


def assign_page_template_variants(
    draft: PlanningDraft,
    manifest: TemplateManifest,
    client: Any = None,
) -> PlanningDraft:
    """Assign page-level template variants for the selected template family."""

    page_lookup = {page.page_number: page for page in draft.pages}
    for page in sorted(draft.pages, key=lambda item: item.page_number):
        if page.reveal_from_page is not None:
            source = page_lookup.get(page.reveal_from_page)
            if source is not None and source.template_variant:
                page.template_variant = source.template_variant
                continue
        page.template_variant = resolve_page_template_variant(page, manifest, client)
    return draft


def build_planning_brief(
    manifest: TemplateManifest,
    palette: PalettePreset | None = None,
) -> str:
    """Build a compact planner-facing brief from selected template metadata."""

    lines = [
        f"Selected Template: {manifest.label or manifest.style_name}",
        f"- style_name: {manifest.style_name}",
        f"- template_family: {manifest.template_family}",
    ]
    if manifest.description:
        lines.append(f"- intro: {manifest.description}")
    if manifest.preferred_layout_hints:
        lines.append(f"- preferred_layouts: {', '.join(manifest.preferred_layout_hints)}")
    if palette is None and manifest.palette_presets:
        palette = manifest.palette_presets[0]
    if palette is not None:
        lines.append(
            "- palette_hint: "
            f"primary={palette.primary_color}, secondary={palette.secondary_color}, accent={palette.accent_color}"
        )
    if manifest.planner_summary:
        lines.append(f"- planner_summary: {manifest.planner_summary}")

    if manifest.planner_page_specs:
        lines.append("")
        lines.append("Page Contracts:")
        for page_type in _PAGE_TYPES:
            spec = manifest.planner_page_specs.get(page_type)
            if spec is None:
                continue
            parts = [f"* {page_type}"]
            if spec.variant:
                parts.append(f"variant={spec.variant}")
            if spec.title_max_chars is not None:
                parts.append(f"title<={spec.title_max_chars}")
            if spec.subtitle_max_chars is not None:
                parts.append(f"subtitle<={spec.subtitle_max_chars}")
            if spec.toc_items_max is not None:
                parts.append(f"toc_items<={spec.toc_items_max}")
            if spec.preferred_layout_hints:
                parts.append(f"layouts={', '.join(spec.preferred_layout_hints)}")
            if spec.image_slots:
                slots = ", ".join(
                    (
                        f"{slot.slot_id or slot.role}:"
                        f"{slot.aspect_ratio}:"
                        f"{'required' if slot.required else 'optional'}"
                        + (
                            f"@({int(slot.x)},{int(slot.y)},{int(slot.width)}x{int(slot.height)})"
                            if None not in (slot.x, slot.y, slot.width, slot.height)
                            else ""
                        )
                    )
                    for slot in spec.image_slots
                )
                parts.append(f"image_slots=[{slots}]")
            if spec.design_notes_hint:
                parts.append(f"hint={spec.design_notes_hint}")
            lines.append("  " + "; ".join(parts))

    return "\n".join(lines)


def build_page_variant_briefs(
    draft: PlanningDraft,
    manifest: TemplateManifest,
) -> str:
    """Build per-page soft template references for stage-2 planning refinement."""

    sections: list[str] = []
    for page in sorted(draft.pages, key=lambda item: item.page_number):
        spec = manifest.variant_specs.get(page.template_variant or "")
        if spec is None:
            sections.append(
                f"Page {page.page_number}: no matched variant. "
                "Finish this page with general planning logic; do not force a template."
            )
            continue

        range_parts: list[str] = []
        if spec.card_min is not None or spec.card_max is not None:
            range_parts.append(
                f"top_level_card_range={spec.card_min if spec.card_min is not None else '?'}-"
                f"{spec.card_max if spec.card_max is not None else '?'}"
            )
        if spec.subcard_min is not None or spec.subcard_max is not None:
            range_parts.append(
                f"subcard_range={spec.subcard_min if spec.subcard_min is not None else '?'}-"
                f"{spec.subcard_max if spec.subcard_max is not None else '?'}"
            )
        if spec.image_min is not None or spec.image_max is not None:
            range_parts.append(
                f"image_range={spec.image_min if spec.image_min is not None else '?'}-"
                f"{spec.image_max if spec.image_max is not None else '?'}"
            )

        lines = [
            f"Page {page.page_number}: matched `{spec.stem}.svg`",
            f"- page_type: {spec.page_type}",
            f"- layout_hint: {spec.layout_hint or page.layout_hint}",
        ]
        if spec.hit_keywords:
            lines.append(f"- hit_keywords: {', '.join(spec.hit_keywords)}")
        if range_parts:
            lines.append(f"- soft_ranges: {', '.join(range_parts)}")
        if spec.page_features:
            lines.append(f"- page_features: {spec.page_features}")
        if spec.reference_rule:
            lines.append(f"- reference_rule: {spec.reference_rule}")
        lines.append(
            "- use_rule: 模板只提供版式方向和区间参考，不要机械复制示例中的 card 数量、图片数量、位置或尺寸。"
        )
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _trim_text(value: str | None, max_chars: int | None) -> str | None:
    if value is None or max_chars is None or max_chars <= 0:
        return value
    if len(value) <= max_chars:
        return value
    if max_chars <= 1:
        return value[:max_chars]
    return value[: max_chars - 1].rstrip() + "…"


def _flatten_page_text_to_snippet(content_points: list[Any], max_chars: int = 24) -> str:
    snippet = " ".join(_flatten_content_points(content_points)).strip()
    if len(snippet) <= max_chars:
        return snippet
    return snippet[:max_chars].rstrip()


def _build_image_query(draft: PlanningDraft, page: PagePlan, slot: ImageSlotSpec) -> str:
    tokens = [token.strip().casefold() for token in slot.query_from.split("+") if token.strip()]
    parts: list[str] = []
    for token in tokens:
        if token == "topic":
            _append_if_text(parts, draft.meta.topic)
        elif token in {"title", "page_title"}:
            _append_if_text(parts, page.title)
        elif token in {"content", "page_content"}:
            _append_if_text(parts, _flatten_page_text_to_snippet(page.content_points, max_chars=28))
    if not parts:
        _append_if_text(parts, page.title)
    if not parts:
        _append_if_text(parts, draft.meta.topic)
    if slot.query_suffix:
        parts.append(slot.query_suffix)
    return " ".join(part for part in parts if part)


def align_draft_to_template(
    draft: PlanningDraft,
    manifest: TemplateManifest,
) -> PlanningDraft:
    """Mutate a draft so it respects template contracts before materials/svg generation."""

    page_lookup = {page.page_number: page for page in draft.pages}
    for page in sorted(draft.pages, key=lambda item: item.page_number):
        if page.reveal_from_page is not None:
            source = page_lookup.get(page.reveal_from_page)
            if source is not None:
                page.layout_hint = source.layout_hint
                page.material_needs = source.material_needs.model_copy(deep=True)
                if source.template_variant:
                    page.template_variant = source.template_variant
            continue

        page_type = _normalize_page_type(page.page_type)
        spec = manifest.planner_page_specs.get(page_type)
        if spec is None and page_type == "content":
            spec = manifest.planner_page_specs.get("content")
        if spec is None:
            continue
        variant_spec = manifest.variant_specs.get(page.template_variant or "")

        if spec.preferred_layout_hints and page.layout_hint not in spec.preferred_layout_hints:
            page.layout_hint = spec.preferred_layout_hints[0]

        page.title = _trim_text(page.title, spec.title_max_chars) or page.title
        page.subtitle = _trim_text(page.subtitle, spec.subtitle_max_chars)

        toc_items_max = spec.toc_items_max
        if page.page_type == "toc" and variant_spec is not None and variant_spec.card_max is not None:
            toc_items_max = variant_spec.card_max
        if page.page_type == "toc" and toc_items_max is not None and len(page.content_points) > toc_items_max:
            page.content_points = page.content_points[:toc_items_max]

        if spec.design_notes_hint and not page.design_notes.strip():
            page.design_notes = spec.design_notes_hint

        current_images = list(page.material_needs.images or [])
        if page.page_type == "toc":
            if page.template_variant == "toc_2":
                current_images = []
            elif page.template_variant == "toc_1" and not current_images and spec.image_slots:
                slot = spec.image_slots[0]
                current_images.append(ImageNeed(
                    query=_build_image_query(draft, page, slot),
                    source=slot.source,  # type: ignore[arg-type]
                    role=slot.role,      # type: ignore[arg-type]
                    aspect_ratio=slot.aspect_ratio,
                ))
        for index, slot in enumerate(spec.image_slots):
            if index < len(current_images):
                current_images[index].aspect_ratio = slot.aspect_ratio
                current_images[index].role = slot.role  # type: ignore[assignment]
                if not current_images[index].query.strip():
                    current_images[index].query = _build_image_query(draft, page, slot)
                continue
            if not slot.required:
                continue
            current_images.append(ImageNeed(
                query=_build_image_query(draft, page, slot),
                source=slot.source,  # type: ignore[arg-type]
                role=slot.role,      # type: ignore[arg-type]
                aspect_ratio=slot.aspect_ratio,
            ))
        page.material_needs.images = current_images

    return draft


def apply_style_routing(draft: PlanningDraft, client: Any = None) -> PlanningDraft:
    """Backwards-compatible wrapper: route deck and assign variants."""

    routing, manifest, _palette = resolve_style_routing(draft, client)
    draft.style_routing = routing
    assign_page_template_variants(draft, manifest, client)
    return draft


def resolve_template_family(draft: PlanningDraft, client: Any = None) -> str:
    """Backwards-compatible helper returning only the resolved family."""

    if getattr(draft, "style_routing", None) and draft.style_routing.template_family:
        return draft.style_routing.template_family
    routing, _manifest, _palette = resolve_style_routing(draft, client)
    return routing.template_family
