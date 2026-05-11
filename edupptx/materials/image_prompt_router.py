"""Route AI image prompts by subject, grade, and theme."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from edupptx.models import ImageNeed, PagePlan, PlanningDraft

_PROFILE_PATH = Path(__file__).resolve().parent.parent / "design" / "references" / "image-prompt-profiles.json"


def _normalize_family(value: str | None) -> str:
    return (value or "").replace("\\", "/").strip("/")


def _resolve_image_style_family(value: str | None) -> str:
    """Shared page templates inherit the matched primary family style."""

    normalized = _normalize_family(value)
    if "低年级" in normalized:
        return "低年级"
    if "高年级" in normalized:
        return "高年级"
    if "复用" in normalized:
        return "复用"
    return normalized


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


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
    text = str(value or "").strip()
    if text:
        flattened.append(text)
    return flattened


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _route_prompt_text(route: dict[str, Any]) -> str:
    terms: list[str] = []
    for key in (
        "profile_prompt_terms",
        "role_prompt_terms",
        "page_type_prompt_terms",
        "aspect_ratio_prompt_terms",
        "quality_terms",
        "negative_terms",
    ):
        terms.extend(_as_text_list(route.get(key)))
    return ", ".join(_dedupe_keep_order(terms))


@lru_cache(maxsize=1)
def _load_profile_data() -> dict[str, Any]:
    if not _PROFILE_PATH.exists():
        return {"defaults": {}, "profiles": []}
    return json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))


def _build_routing_text(draft: PlanningDraft, page: PagePlan, need: ImageNeed) -> str:
    parts = [
        draft.meta.topic,
        draft.meta.style_direction,
        page.title,
        page.subtitle,
        page.design_notes,
        page.notes,
        need.query,
    ]
    parts.extend(_flatten_content_points(page.content_points))
    return "\n".join(str(part).strip() for part in parts if str(part).strip()).casefold()


def _match_profile(
    profile: dict[str, Any],
    template_family: str,
    page: PagePlan,
    need: ImageNeed,
    routing_text: str,
) -> tuple[bool, int]:
    match = profile.get("match", {})

    families = [_normalize_family(item) for item in _as_text_list(match.get("template_families"))]
    if families and template_family not in families:
        return False, 0

    page_types = _as_text_list(match.get("page_types"))
    if page_types and page.page_type not in page_types:
        return False, 0

    roles = _as_text_list(match.get("roles"))
    if roles and need.role not in roles:
        return False, 0

    keywords_any = [keyword.casefold() for keyword in _as_text_list(match.get("keywords_any"))]
    any_hits = sum(1 for keyword in keywords_any if keyword in routing_text)
    if keywords_any and any_hits == 0:
        return False, 0

    keywords_all = [keyword.casefold() for keyword in _as_text_list(match.get("keywords_all"))]
    if keywords_all and not all(keyword in routing_text for keyword in keywords_all):
        return False, 0

    return True, any_hits + len(keywords_all)


def _select_profiles(
    template_family: str,
    page: PagePlan,
    need: ImageNeed,
    routing_text: str,
) -> list[dict[str, Any]]:
    profiles = _load_profile_data().get("profiles", [])
    base_profiles: list[tuple[int, dict[str, Any]]] = []
    theme_profiles: list[tuple[int, int, dict[str, Any]]] = []

    for profile in profiles:
        matched, keyword_hits = _match_profile(profile, template_family, page, need, routing_text)
        if not matched:
            continue
        priority = int(profile.get("priority", 0))
        match = profile.get("match", {})
        has_theme_keywords = bool(match.get("keywords_any") or match.get("keywords_all"))
        if has_theme_keywords:
            theme_profiles.append((priority, keyword_hits, profile))
        else:
            base_profiles.append((priority, profile))

    ordered: list[dict[str, Any]] = [profile for _priority, profile in sorted(base_profiles, key=lambda item: item[0])]
    if theme_profiles:
        theme_profiles.sort(key=lambda item: (item[0], item[1]), reverse=True)
        ordered.append(theme_profiles[0][2])
    return ordered


def resolve_ai_image_prompt(
    draft: PlanningDraft,
    page: PagePlan,
    need: ImageNeed,
) -> str:
    """Build the final AI-image prompt from semantic query plus routed style layers."""

    route = resolve_ai_image_prompt_route(draft, page, need)
    return str(route.get("generation_prompt") or need.query).strip()


def resolve_ai_image_prompt_route(
    draft: PlanningDraft,
    page: PagePlan,
    need: ImageNeed,
) -> dict[str, Any]:
    """Return structured prompt-routing metadata plus the final generation prompt."""

    profile_data = _load_profile_data()
    defaults = profile_data.get("defaults", {})
    template_family = _resolve_image_style_family(draft.style_routing.template_family)
    routing_text = _build_routing_text(draft, page, need)
    selected_profiles = _select_profiles(template_family, page, need, routing_text)

    profile_prompt_terms: list[str] = []
    profile_negative_terms: list[str] = []
    selected_profile_payloads: list[dict[str, Any]] = []
    for profile in selected_profiles:
        prompt_terms = _as_text_list(profile.get("prompt_terms"))
        negative_terms = _as_text_list(profile.get("negative_terms"))
        profile_prompt_terms.extend(prompt_terms)
        profile_negative_terms.extend(negative_terms)
        selected_profile_payloads.append(
            {
                "id": str(profile.get("id") or "").strip(),
                "priority": int(profile.get("priority", 0)),
                "prompt_terms": prompt_terms,
                "negative_terms": negative_terms,
            }
        )

    role_prompts = defaults.get("role_prompts", {})
    role_prompt_terms = _as_text_list(role_prompts.get(need.role))

    page_type_prompts = defaults.get("page_type_prompts", {})
    page_type_prompt_terms = _as_text_list(page_type_prompts.get(page.page_type))

    aspect_ratio_prompts = defaults.get("aspect_ratio_prompts", {})
    aspect_ratio_prompt_terms = _as_text_list(aspect_ratio_prompts.get(need.aspect_ratio))

    quality_terms = _as_text_list(defaults.get("quality_terms"))
    negative_terms = _dedupe_keep_order([*profile_negative_terms, *_as_text_list(defaults.get("negative_terms"))])

    route = {
        "template_family": template_family,
        "profile_ids": [item["id"] for item in selected_profile_payloads if item["id"]],
        "profiles": selected_profile_payloads,
        "profile_prompt_terms": _dedupe_keep_order(profile_prompt_terms),
        "role_prompt_terms": role_prompt_terms,
        "page_type_prompt_terms": page_type_prompt_terms,
        "aspect_ratio_prompt_terms": aspect_ratio_prompt_terms,
        "quality_terms": quality_terms,
        "negative_terms": negative_terms,
    }
    route["style_prompt"] = _route_prompt_text(route)

    prompt_terms = _dedupe_keep_order(
        [
            need.query.strip(),
            *route["profile_prompt_terms"],
            *role_prompt_terms,
            *page_type_prompt_terms,
            *aspect_ratio_prompt_terms,
            *quality_terms,
            *negative_terms,
        ]
    )
    route["generation_prompt"] = ", ".join(prompt_terms)
    return route


def build_routed_image_needs(
    draft: PlanningDraft,
    page: PagePlan,
) -> list[ImageNeed]:
    """Copy image needs and attach routed AI-generation prompt metadata."""

    routed: list[ImageNeed] = []
    for need in page.material_needs.images or []:
        copied = need.model_copy(deep=True)
        if copied.source == "ai_generate":
            route = resolve_ai_image_prompt_route(draft, page, copied)
            copied.generation_prompt = str(route.pop("generation_prompt", "")).strip()
            copied.prompt_route = route
        routed.append(copied)
    return routed
