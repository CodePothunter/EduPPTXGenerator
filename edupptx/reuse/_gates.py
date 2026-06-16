"""复用层残余 helper：语义重叠/覆盖、subject scope、grade/metadata、三档 review-gate 阈值与候选判定（注：gate 三档多为架构审查标记的 dead config，主裁决走 policy_score+LLM review，此处仅结构归位、删除留待 goldset-gated 清理）。函数体逐字一致。"""

from __future__ import annotations

from typing import Any


from edupptx.reuse._util import (
    _clean_keyword,
    _clean_text,
    _dedupe_terms,
    _dict,
)
from edupptx.reuse._constants import (
    BM25_GRAY_REUSE_THRESHOLD,
    EMBEDDING_GRAY_REUSE_THRESHOLD,
    TEXT_OVERLAP_EMBEDDING_THRESHOLD,
    TEXT_OVERLAP_REVIEW_THRESHOLD,
    VISUAL_GENERIC_REUSE_THRESHOLD,
    _BACKGROUND_LIKE_ROLE_TOKENS,
    _CORE_STYLE_MARKERS,
    _CORE_USAGE_MARKERS,
    _NOISE_TOKENS,
    _OTHER_GRADE,
    _OTHER_SUBJECT,
    _STYLE_DESCRIPTOR_MARKERS,
    _VISUAL_FORM_MARKERS,
)
from edupptx.reuse._assets import (
    _asset_aspect_ratio_label,
    _keyword_list,
    _normalize_subject_value,
    _unit_ref_for_asset,
)
from edupptx.reuse._normalize import (
    _normalize_binary_reuse_group,
    _normalize_grade_band_value,
    _normalize_grade_norm_value,
    grade_band_from_norm,
)
from edupptx.reuse._scoring import (
    _aspect_ratio_value,
    _subject_scope_decision,
    normalize_aspect_bucket,
)
from edupptx.reuse._store import (
    _grade_info_from_asset,
    _ratio_orientation,
)
from edupptx.reuse._decide import (
    _transform_rejects_candidate,
)
from edupptx.reuse._decide import (
    _transform_rejects_candidate,
)


def _select_best_library_reuse_match(matches: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not matches:
        return None

    def rank(match: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
        policy = _dict(match.get("reuse_policy"))
        decision = _clean_text(policy.get("decision"))
        decision_rank = 2.0 if decision in {"direct_reuse", "full_match"} else 1.0 if decision == "generic_support" else 0.0
        score_details = _dict(match.get("score_details"))
        return (
            decision_rank,
            float(match.get("policy_score") or score_details.get("policy_score") or 0.0),
            float(match.get("keyword_score") or 0.0),
            float(match.get("hybrid_score") or score_details.get("hybrid_score") or 0.0),
            float(match.get("embedding_score") or score_details.get("embedding_score") or 0.0),
            -float(match.get("library_search_order") or 0),
        )

    return max(matches, key=rank)


def _effective_grade_band(asset: dict[str, Any]) -> str:
    """存量资产 band=其他 但 grade_norm 已知时，派生出有效 band（避免幽灵 unknown 拦截）。"""
    band = _normalize_grade_band_value(asset.get("grade_band"))
    if band != _OTHER_GRADE:
        return band
    return grade_band_from_norm(asset.get("grade_norm"))


def _target_metadata_unknown_fields(asset: dict[str, Any]) -> list[str]:
    unknown: list[str] = []
    if _normalize_subject_value(asset.get("subject")) == _OTHER_SUBJECT:
        unknown.append("subject")
    if _normalize_grade_norm_value(asset.get("grade_norm")) == _OTHER_GRADE:
        unknown.append("grade_norm")
    if _effective_grade_band(asset) == _OTHER_GRADE:
        unknown.append("grade_band")
    return unknown


def _clean_semantic_aliases(value: Any) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    if not isinstance(value, dict):
        return aliases
    for raw_key, raw_values in value.items():
        key = _clean_keyword(raw_key)
        if not key:
            continue
        terms = _keyword_list(raw_values, max_items=6)
        if terms:
            aliases[key] = terms
    return aliases


def _merge_semantic_aliases(*items: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for aliases in items:
        for key, values in aliases.items():
            clean_key = _clean_keyword(key)
            if not clean_key:
                continue
            merged[clean_key] = _dedupe_terms([*merged.get(clean_key, []), *values])[:8]
    return merged


def _context_exclusions(asset: dict[str, Any]) -> set[str]:
    grade = _clean_text(asset.get("grade"))
    subject = _clean_text(asset.get("subject"))
    grade_info = _grade_info_from_asset(asset)
    exclusions = {
        grade,
        _clean_text(asset.get("grade_norm")),
        _clean_text(asset.get("grade_band")),
        _clean_text(grade_info.get("grade_norm")),
        _clean_text(grade_info.get("grade_band")),
        subject,
        _unit_ref_for_asset(asset),
    }
    if grade and subject:
        exclusions.add(f"{grade}{subject}")
        exclusions.add(f"{grade} {subject}")
    grade_norm = _clean_text(grade_info.get("grade_norm"))
    if grade_norm and subject:
        exclusions.add(f"{grade_norm}{subject}")
        exclusions.add(f"{grade_norm} {subject}")
    return {item for item in exclusions if item}


def _clean_core_keyword_terms(terms: list[str]) -> tuple[list[str], list[str]]:
    core_terms: list[str] = []
    style_terms: list[str] = []
    for term in terms:
        if _is_generic_core_term(term):
            continue
        if _looks_like_style_or_usage_term(term):
            style_terms.append(term)
            extracted = _extract_entity_from_visual_style_term(term)
            if extracted and not _is_generic_core_term(extracted):
                core_terms.append(extracted)
            continue
        core_terms.append(term)
    return _dedupe_terms(core_terms), _dedupe_terms(style_terms)


def _is_generic_core_term(term: str) -> bool:
    normalized = _clean_keyword(term).casefold().replace(" ", "")
    if not normalized:
        return True
    return normalized in {item.casefold().replace(" ", "") for item in _NOISE_TOKENS}


def _looks_like_style_or_usage_term(term: str) -> bool:
    normalized = _clean_keyword(term).casefold().replace(" ", "")
    if not normalized:
        return False
    if any(marker.casefold() in normalized for marker in _CORE_USAGE_MARKERS):
        return True
    if any(marker.casefold() in normalized for marker in _CORE_STYLE_MARKERS):
        return True
    if any(form.casefold() in normalized for form in _VISUAL_FORM_MARKERS) and any(
        marker.casefold() in normalized for marker in _STYLE_DESCRIPTOR_MARKERS
    ):
        return True
    return False


def _extract_entity_from_visual_style_term(term: str) -> str:
    cleaned = _clean_keyword(term)
    if not cleaned:
        return ""
    compact = cleaned.replace(" ", "")
    for marker in _STYLE_DESCRIPTOR_MARKERS:
        compact = compact.replace(marker, "")
    for marker in _CORE_STYLE_MARKERS:
        compact = compact.replace(marker, "")
    for marker in _VISUAL_FORM_MARKERS:
        if compact.endswith(marker):
            compact = compact[: -len(marker)]
    return _clean_keyword(compact)


def _subject_scope_compatible(target_subject: Any, candidate_subject: Any) -> bool:
    return bool(_subject_scope_decision(target_subject, candidate_subject)["compatible"])


def _semantic_terms(
    asset: dict[str, Any],
    field: str,
    *,
    fallback_fields: tuple[str, ...] = (),
    max_items: int = 12,
) -> list[str]:
    terms = _keyword_list(asset.get(field), max_items=max_items)
    if not terms:
        for fallback in fallback_fields:
            terms.extend(_keyword_list(asset.get(fallback), max_items=max_items))
            if terms:
                break
    return _dedupe_terms(terms)[:max_items]


def _semantic_coverage(
    target_terms: list[str],
    candidate_terms: list[str],
    *,
    neutral: float,
) -> tuple[float, list[dict[str, str]], list[str]]:
    if not target_terms:
        return neutral, [], []
    if not candidate_terms:
        return 0.0, [], target_terms
    score, hits = _overlap_score_with_hits(target_terms, candidate_terms)
    matched = {_clean_keyword(item.get("target")) for item in hits}
    missing = [term for term in target_terms if _clean_keyword(term) not in matched]
    return score, hits, missing


def _overlap_score(target_terms: list[str], candidate_terms: list[str]) -> float:
    score, _hits = _overlap_score_with_hits(target_terms, candidate_terms)
    return score


def _overlap_score_with_hits(
    target_terms: list[str],
    candidate_terms: list[str],
) -> tuple[float, list[dict[str, str]]]:
    if not target_terms or not candidate_terms:
        return 0.0, []
    hits: list[dict[str, str]] = []
    for target in target_terms:
        matched = next((candidate for candidate in candidate_terms if _terms_match(target, candidate)), "")
        if matched:
            hits.append({"target": target, "candidate": matched})
    return len(hits) / len(target_terms), hits


def _terms_match(left: str, right: str) -> bool:
    left = _clean_keyword(left)
    right = _clean_keyword(right)
    if not left or not right:
        return False
    if left == right:
        return True
    return min(len(left), len(right)) >= 2 and (left in right or right in left)


def _target_is_background_like(target: dict[str, Any]) -> bool:
    """True iff the target's page_type declares it as a backdrop slot.

    Single helper so the "background-like" classification has one place to
    maintain. Matches exact token equality (after casefold), not substring
    containment, to avoid false-positives like "background_decoration".
    """

    value = _clean_text(_dict(target).get("page_type")).casefold()
    return bool(value and value in _BACKGROUND_LIKE_ROLE_TOKENS)



def _aspect_ratio_score(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    target_ratio = normalize_aspect_bucket(_asset_aspect_ratio_label(target))
    candidate_ratio = normalize_aspect_bucket(_asset_aspect_ratio_label(candidate))
    if not target_ratio or not candidate_ratio:
        return 0.5
    if target_ratio == candidate_ratio:
        return 1.0
    target_orientation = _ratio_orientation(target_ratio)
    candidate_orientation = _ratio_orientation(candidate_ratio)
    return 0.6 if target_orientation and target_orientation == candidate_orientation else 0.2


def _aspect_ratio_diff(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    t = _aspect_ratio_value(target)
    c = _aspect_ratio_value(candidate)
    if t <= 0 or c <= 0:
        return 1.0
    return abs(t - c) / t


def _target_unknown_fields_for_reuse(asset: dict[str, Any]) -> list[str]:
    ignored = {"subject", "grade_norm", "grade_band"}
    return [field for field in _target_metadata_unknown_fields(asset) if field not in ignored]


def _candidate_unknown_fields_for_reuse(
    asset: dict[str, Any],
    subject_decision: dict[str, Any],
) -> list[str]:
    unknown = _target_metadata_unknown_fields(asset)
    ignored = {"subject", "grade_norm", "grade_band"}
    return [field for field in unknown if field not in ignored]
