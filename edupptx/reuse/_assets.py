"""复用层 asset/文本特征提取 helper：从 asset/target dict 抽取 caption/query/
page_type/style/topic-refs/subject 等检索特征，以及关键词/topic-ref 文本清洗。

零对 ai_image_asset_db 的依赖（仅 _util/_constants/stdlib），是 retrieve/decide/build
等上层的共同基础。函数体与原 ai_image_asset_db.py 中逐字一致。
"""

from __future__ import annotations

import re
from typing import Any

from edupptx.reuse._util import _clean_keyword, _clean_text, _dedupe_terms
from edupptx.reuse._constants import (
    _ALLOWED_SUBJECTS,
    _OTHER_SUBJECT,
    _PROMPT_ROUTE_LIST_FIELDS,
    _TOPIC_REF_LEADING_NOISE_RE,
    _TOPIC_REF_SUBJECT_PREFIXES,
    _TOPIC_REF_TRAILING_NOISE,
    _TOPIC_REF_WRAPPER_RE,
)

def _normalize_subject_value(value: Any) -> str:
    text = _clean_text(value)
    return text if text in _ALLOWED_SUBJECTS else _OTHER_SUBJECT


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def extract_topic_refs(*texts: Any) -> list[str]:
    """Extract compact lesson/knowledge-topic anchors from theme-like text."""

    wrapped: list[str] = []
    for text in texts:
        clean = _clean_text(text)
        if not clean:
            continue
        wrapped.extend(_clean_topic_ref(match.group(1)) for match in _TOPIC_REF_WRAPPER_RE.finditer(clean))
    wrapped = _dedupe_terms([item for item in wrapped if item])
    if wrapped:
        return wrapped[:6]

    fallback: list[str] = []
    for text in texts:
        topic = _clean_topic_ref(text)
        if topic:
            fallback.append(topic)
    return _dedupe_terms(fallback)[:6]


def _topic_refs_for_asset(asset: dict[str, Any]) -> list[str]:
    explicit = _keyword_list(asset.get("topic_refs"), max_items=6)
    if explicit:
        return explicit
    return extract_topic_refs(asset.get("theme"))


def _unit_ref_for_asset(asset: dict[str, Any]) -> str:
    return _clean_topic_ref(asset.get("unit_ref") or asset.get("unit"))


def _clean_topic_ref(value: Any) -> str:
    text = _clean_text(value).strip("《》〈〉「」『』“”\"'()（）[]【】 ")
    if not text:
        return ""
    text = _TOPIC_REF_LEADING_NOISE_RE.sub("", text).strip()
    changed = True
    while changed:
        changed = False
        for subject in _TOPIC_REF_SUBJECT_PREFIXES:
            if text.startswith(subject) and len(text) > len(subject):
                text = text[len(subject):].strip()
                changed = True
                break
    changed = True
    while changed:
        changed = False
        for suffix in _TOPIC_REF_TRAILING_NOISE:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)].strip()
                changed = True
                break
    text = text.strip("：:，,。；;、-_/ ")
    compact = text.replace(" ", "")
    if not compact or len(compact) > 40:
        return ""
    return text


def _keyword_list(value: Any, *, max_items: int, exclude: set[str] | None = None) -> list[str]:
    if isinstance(value, str):
        raw_items: list[Any] = re.split(r"[,;\n、，；]+", value)
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        raw_items = []

    terms: list[str] = []
    seen: set[str] = set()
    excluded = exclude or set()
    for item in raw_items:
        term = _clean_keyword(item)
        if not term or term in seen or _is_excluded_keyword(term, excluded):
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= max_items:
            break
    return terms


def _is_excluded_keyword(term: str, excluded: set[str]) -> bool:
    normalized = term.replace(" ", "")
    for value in excluded:
        blocked = value.replace(" ", "")
        if normalized == blocked:
            return True
        if len(blocked) >= 4 and blocked in normalized:
            return True
    return False


def _page_retrieval_text(asset: dict[str, Any]) -> str:
    return _asset_caption(asset)


def _background_retrieval_text(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("normalized_prompt"))


def _asset_embedding_text(asset: dict[str, Any]) -> str:
    if _is_background_asset(asset):
        return _background_retrieval_text(asset)

    return _page_retrieval_text(asset)


def _source_pptx_refs_for_asset(asset: dict[str, Any]) -> list[dict[str, Any]]:
    raw_refs = asset.get("source_pptx_refs")
    if not isinstance(raw_refs, list):
        return []
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for raw in raw_refs:
        if not isinstance(raw, dict):
            continue
        ref: dict[str, Any] = {
            "pptx_id": _clean_text(raw.get("pptx_id")),
            "period_id": _clean_text(raw.get("period_id")),
            "file_path": _clean_text(raw.get("file_path")),
            "file_name": _clean_text(raw.get("file_name")),
            "absolute_path": _clean_text(raw.get("absolute_path")),
            "source": _clean_text(raw.get("source")),
        }
        slide_no = _clean_text(raw.get("slide_no"))
        shape_idx = _clean_text(raw.get("shape_idx"))
        source_media_path = _clean_text(raw.get("source_media_path"))
        if slide_no:
            try:
                ref["slide_no"] = int(slide_no)
            except ValueError:
                ref["slide_no"] = slide_no
        if shape_idx:
            try:
                ref["shape_idx"] = int(shape_idx)
            except ValueError:
                ref["shape_idx"] = shape_idx
        if source_media_path:
            ref["source_media_path"] = source_media_path
        ref = {key: value for key, value in ref.items() if value not in ("", None)}
        if not any(ref.get(key) for key in ("pptx_id", "file_path", "file_name", "absolute_path")):
            continue
        key = (
            _clean_text(ref.get("pptx_id")),
            _clean_text(ref.get("file_path")),
            _clean_text(ref.get("absolute_path")),
            _clean_text(ref.get("slide_no")),
            _clean_text(ref.get("shape_idx")),
            _clean_text(ref.get("source_media_path")),
            _clean_text(ref.get("source")),
        )
        if key in seen:
            continue
        seen.add(key)
        refs.append(ref)
    return refs


def _clean_prompt_route(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    route: dict[str, Any] = {}
    template_family = _clean_text(value.get("template_family"))
    if template_family:
        route["template_family"] = template_family

    profiles: list[dict[str, Any]] = []
    raw_profiles = value.get("profiles")
    if isinstance(raw_profiles, list):
        for profile in raw_profiles:
            if not isinstance(profile, dict):
                continue
            item: dict[str, Any] = {}
            profile_id = _clean_text(profile.get("id"))
            if profile_id:
                item["id"] = profile_id
            try:
                item["priority"] = int(profile.get("priority", 0))
            except (TypeError, ValueError):
                pass
            prompt_terms = _as_string_list(profile.get("prompt_terms"))
            negative_terms = _as_string_list(profile.get("negative_terms"))
            if prompt_terms:
                item["prompt_terms"] = prompt_terms
            if negative_terms:
                item["negative_terms"] = negative_terms
            if item:
                profiles.append(item)
    if profiles:
        route["profiles"] = profiles

    for key in _PROMPT_ROUTE_LIST_FIELDS:
        terms = _as_string_list(value.get(key))
        if terms:
            route[key] = _dedupe_terms(terms)

    style_prompt = _clean_text(value.get("style_prompt"))
    if style_prompt:
        route["style_prompt"] = style_prompt

    return route


def _route_style_prompt(route: dict[str, Any]) -> str:
    explicit = _clean_text(route.get("style_prompt"))
    if explicit:
        return explicit

    terms: list[str] = []
    for key in (
        "profile_prompt_terms",
        "role_prompt_terms",
        "page_type_prompt_terms",
        "aspect_ratio_prompt_terms",
        "quality_terms",
        "negative_terms",
    ):
        terms.extend(_as_string_list(route.get(key)))
    return " ".join(_dedupe_terms(terms))


def _asset_content_prompt(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("content_prompt")) or _clean_text(asset.get("prompt"))


def _asset_caption(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("caption"))


def _asset_query(asset: dict[str, Any]) -> str:
    """Verbose classification text. Falls back to legacy verbose fields so
    pre-rebuild libraries stay classifiable."""
    return (
        _clean_text(asset.get("query"))
        or _clean_text(asset.get("detail_prompt"))
        or _asset_content_prompt(asset)
    )


def _is_background_asset(asset: dict[str, Any]) -> bool:
    return _clean_text(asset.get("asset_kind")) == "background"


def _asset_generation_prompt(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("generation_prompt")) or _clean_text(asset.get("normalized_prompt"))


def _asset_style_prompt(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("style_prompt")) or _route_style_prompt(_clean_prompt_route(asset.get("prompt_route")))


def _asset_page_type(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("page_type"))


def _asset_general_value(asset_or_value: Any) -> bool | None:
    if isinstance(asset_or_value, dict):
        return _optional_bool(asset_or_value.get("general"))
    return None


def _asset_subject_value(asset_or_value: Any) -> str:
    if isinstance(asset_or_value, dict):
        return _normalize_subject_value(asset_or_value.get("subject"))
    return _normalize_subject_value(asset_or_value)


def _asset_aspect_ratio_label(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("aspect_ratio")) or _clean_text(asset.get("aspect_bucket"))


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_clean_text(item) for item in value if _clean_text(item)]
    text = _clean_text(value)
    return [text] if text else []
