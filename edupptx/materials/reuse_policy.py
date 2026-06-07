"""Deterministic reuse policy for simplified AI image metadata."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

REUSE_LEVELS = {"loose", "medium", "strict", "skip"}

# --- Current C00-C03 material category → reuse_level mapping ---
# String literals to avoid circular import (strict_reuse_classifier → ai_image_asset_db → reuse_policy).
MATERIAL_CATEGORY_REUSE_LEVEL: dict[str, str] = {
    "C00_strict_text_problem_skip": "skip",
    "C01_irreplaceable_entity_event_action": "strict",
    "C02_generic_subject_object": "medium",
    "C03_scene_decor_container": "loose",
}

FORCED_LOOSE_MATERIAL_CATEGORIES = frozenset(
    cat for cat, level in MATERIAL_CATEGORY_REUSE_LEVEL.items() if level == "loose"
)

DEFAULT_POLICY = {
    "reuse_level": "medium",
    "generic_support_allowed": True,
}

PAGE_IMAGE_REUSE_THRESHOLDS = {
    "loose": 0.50,
    "medium": 0.55,
    "strict": 0.63,
}
BACKGROUND_REUSE_THRESHOLD = 0.38
BACKGROUND_SAME_THEME_THRESHOLD = 0.34
BACKGROUND_SAME_THEME_HIGH_EMBEDDING_THRESHOLD = 0.30
BACKGROUND_SAME_THEME_HIGH_EMBEDDING_FLOOR = 0.70
# Color-temperature normalization for background reuse.
# Library writes Chinese tokens — see metadata_rules.md "background.normalized_prompt 写法":
# `color_temperature` 只允许 `冷/暖/中性`. Any other value is treated as
# missing (returns ""), which makes the cross-theme warm/cool filter skip
# the candidate rather than crash.
_COLOR_TEMPERATURE_NORMALIZE = {
    "暖": "warm",
    "冷": "cool",
    "中性": "neutral",
}


def _normalize_color_temperature(value: Any) -> str:
    return _COLOR_TEMPERATURE_NORMALIZE.get(_clean_text(value), "")


# --- Three-tier decision constants (spec §4) ---
T_DIRECT = 0.75
T_REJECT = 0.35
T_GAP = 0.02
CLUSTER_MAX = 3


def decide_reuse(
    candidates: list[dict[str, Any]],
    *,
    score_key: str = "policy_score",
    t_direct: float = T_DIRECT,
    t_reject: float = T_REJECT,
    t_gap: float = T_GAP,
    cluster_max: int = CLUSTER_MAX,
) -> dict[str, Any]:
    """Spec §4 three-tier decision over a ranked candidate list.

    ``candidates`` must be sorted best-first on ``score_key``. The default
    ``score_key="policy_score"`` is an absolute score in [0, 1]. Production
    ``hybrid_score`` is RRF-normalized retrieval evidence and must not drive
    the high/low cut.
    """
    if not candidates:
        return {"decision": "reject", "reason": "empty_candidates"}

    top1 = candidates[0]
    top1_score = float(top1.get(score_key) or 0.0)
    top1_id = top1.get("asset_id", "")
    top2_score = float(candidates[1].get(score_key) or 0.0) if len(candidates) > 1 else None
    gap = None if top2_score is None else top1_score - top2_score

    if top1_score < t_reject:
        return {"decision": "reject", "reason": "score_below_t_reject", "score": top1_score}

    if top1_score >= t_direct and (gap is None or gap >= t_gap):
        return {
            "decision": "direct_reuse",
            "asset_id": top1_id,
            "reason": "score_above_t_direct_with_gap",
            "score": top1_score,
            "gap": gap,
        }

    cluster = [c for c in candidates if top1_score - float(c.get(score_key) or 0.0) <= t_gap]

    cluster = cluster[:cluster_max]
    return {
        "decision": "llm_review",
        "reason": "gray_zone_or_close_leader",
        "score": top1_score,
        "gap": gap,
        "cluster": cluster,
    }


@dataclass(frozen=True)
class AssetMetadata:
    """Schema-normalized metadata view for page-image reuse assets."""

    raw: dict[str, Any]
    reuse_level: str
    generic_support_allowed: bool
    duplicate_asset_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reuse_level": self.reuse_level,
            "generic_support_allowed": self.generic_support_allowed,
            "duplicate_asset_ids": list(self.duplicate_asset_ids),
        }


def normalize_asset_metadata(raw: dict[str, Any]) -> AssetMetadata:
    """Return the reusable metadata view derived from the material category."""

    asset = raw if isinstance(raw, dict) else {}
    material_category = _clean_text(asset.get("strict_reuse_group"))

    reuse_level = reuse_level_from_material_category(material_category) or DEFAULT_POLICY["reuse_level"]
    generic_support_allowed = reuse_level == "loose"
    return AssetMetadata(
        raw=asset,
        reuse_level=reuse_level,
        generic_support_allowed=generic_support_allowed,
        duplicate_asset_ids=_clean_string_list(asset.get("duplicate_asset_ids")),
    )


def reuse_level_from_material_category(material_category: str | None) -> str | None:
    """Return reuse_level if material_category is a current C00-C03 category, else None."""
    cat = _clean_text(material_category)
    return MATERIAL_CATEGORY_REUSE_LEVEL.get(cat)


def normalize_reuse_policy_fields(asset: dict[str, Any]) -> dict[str, Any]:
    """Return schema-valid simplified reuse metadata for an asset."""
    mat_cat = _clean_text(asset.get("strict_reuse_group"))
    reuse_level = reuse_level_from_material_category(mat_cat) or "medium"
    return {
        "reuse_level": reuse_level,
        "generic_support_allowed": reuse_level == "loose",
    }


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        text = _clean_text(value)
        return [text] if text else []
    results: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        results.append(text)
    return results


def reuse_threshold_for_target(target: dict[str, Any], explicit_threshold: float | None = None) -> float:
    if explicit_threshold is not None:
        try:
            return _clamp(float(explicit_threshold))
        except (TypeError, ValueError):
            pass
    if _clean_text(target.get("asset_kind")) == "background":
        return BACKGROUND_REUSE_THRESHOLD
    mat_cat = _clean_text(target.get("strict_reuse_group"))
    reuse_level = reuse_level_from_material_category(mat_cat) or "medium"
    threshold = PAGE_IMAGE_REUSE_THRESHOLDS.get(reuse_level, PAGE_IMAGE_REUSE_THRESHOLDS["medium"])
    return round(_clamp(threshold, minimum=0.30, maximum=0.75), 4)


def evaluate_reuse_filter(
    target: dict[str, Any],
    candidate: dict[str, Any],
    score_details: dict[str, Any] | None = None,
    *,
    threshold: float | None = None,
) -> dict[str, Any]:
    target_level = reuse_level_from_material_category(
        _clean_text(target.get("strict_reuse_group"))
    )
    if target_level == "skip":
        return _result("reject", "material_category_skip", confidence=1.0, threshold=0.0, score_gap=0.0)
    candidate_level = reuse_level_from_material_category(
        _clean_text(candidate.get("strict_reuse_group"))
    )
    if candidate_level == "skip":
        return _result("reject", "candidate_material_category_skip", confidence=1.0, threshold=0.0, score_gap=0.0)

    score_details = score_details or {}
    target_kind = _clean_text(target.get("asset_kind"))
    candidate_kind = _clean_text(candidate.get("asset_kind"))
    score = _score_from_details(score_details)

    if target_kind != candidate_kind:
        return _result("reject", "asset_kind_mismatch", confidence=1.0, threshold=0.0, score_gap=0.0)

    if target_kind == "background":
        threshold_used = BACKGROUND_REUSE_THRESHOLD
        score_gap = score - threshold_used
        if score >= threshold_used:
            return _result("full_match", "background_score_above_threshold",
                           confidence=0.9, threshold=threshold_used, score_gap=score_gap)
        return _result("reject", "background_score_below_threshold",
                        confidence=0.9, threshold=threshold_used, score_gap=score_gap)

    threshold_used = T_DIRECT if threshold is None else float(threshold)
    score_gap = score - threshold_used

    return _result("eligible", "hard_filters_passed",
                    confidence=0.0, threshold=threshold_used, score_gap=score_gap)


def _score_from_details(score_details: dict[str, Any]) -> float:
    for key in ("keyword_score", "score", "background_reuse_score"):
        try:
            value = float(score_details.get(key) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return _clamp(value)
    return 0.0


def _result(
    decision: str,
    reason: str,
    *,
    confidence: float = 0.5,
    threshold: float = 0.0,
    score_gap: float = 0.0,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "reason": reason,
        "confidence": _clamp(confidence),
        "threshold_used": round(float(threshold or 0.0), 4),
        "score_gap": round(float(score_gap or 0.0), 4),
    }


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clamp(value: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))
