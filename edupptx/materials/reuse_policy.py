"""Deterministic reuse policy for simplified AI image metadata."""

from __future__ import annotations

import re
from typing import Any

REUSE_LEVELS = {"loose", "medium", "strict"}
ASSET_CATEGORIES = {
    "learning_behavior",
    "generic_tool",
    "generic_diagram",
    "concept_scene",
    "content_specific",
    "character_action",
    "unknown",
}
CONSTRAINT_KINDS = {"text", "math", "physics", "entity", "object", "action", "relation"}

DEFAULT_POLICY = {
    "reuse_level": "medium",
    "asset_category": "unknown",
    "core_constraints": [],
    "generic_support_allowed": True,
}

CATEGORY_THRESHOLDS = {
    "learning_behavior": 0.42,
    "generic_tool": 0.45,
    "generic_diagram": 0.48,
    "concept_scene": 0.50,
    "content_specific": 0.58,
    "character_action": 0.60,
    "unknown": 0.55,
}
REUSE_LEVEL_DELTAS = {
    "loose": -0.05,
    "medium": 0.0,
    "strict": 0.08,
}

LOW_SCORE_REJECT_MARGIN = 0.08
CONFIDENT_LOOSE_MARGIN = 0.08

HIGH_RISK_KINDS = {"text", "math", "physics", "relation"}
STRICT_CATEGORIES = {"content_specific", "character_action"}
GENERIC_CATEGORIES = {"generic_tool", "generic_diagram"}
CONTENT_PRESERVING_CATEGORIES = {"generic_tool", "generic_diagram", "content_specific", "character_action"}

CATEGORY_COMPATIBILITY = {
    "learning_behavior": {
        "full": {"learning_behavior"},
        "support": {"concept_scene", "generic_diagram", "generic_tool"},
    },
    "generic_tool": {
        "full": {"generic_tool"},
        "support": {"generic_tool", "generic_diagram"},
    },
    "generic_diagram": {
        "full": {"generic_diagram"},
        "support": {"generic_diagram", "generic_tool"},
    },
    "concept_scene": {
        "full": {"concept_scene"},
        "support": {"learning_behavior", "generic_diagram", "generic_tool"},
    },
    "content_specific": {
        "full": {"content_specific"},
        "support": {"generic_tool", "generic_diagram", "concept_scene"},
    },
    "character_action": {
        "full": {"character_action"},
        "support": {"concept_scene", "learning_behavior"},
    },
    "unknown": {
        "full": set(),
        "support": {"learning_behavior", "generic_tool", "generic_diagram", "concept_scene"},
    },
}


def normalize_reuse_policy_fields(asset: dict[str, Any]) -> dict[str, Any]:
    """Return schema-valid simplified reuse metadata for an asset."""

    if _clean_text(asset.get("asset_kind")) == "background":
        return {
            "reuse_level": "loose",
            "asset_category": "unknown",
            "core_constraints": [],
            "generic_support_allowed": True,
        }

    reuse_level = _clean_text(asset.get("reuse_level"))
    if reuse_level not in REUSE_LEVELS:
        reuse_level = DEFAULT_POLICY["reuse_level"]

    asset_category = _clean_text(asset.get("asset_category"))
    if asset_category not in ASSET_CATEGORIES:
        asset_category = DEFAULT_POLICY["asset_category"]

    constraints = normalize_core_constraints(asset.get("core_constraints"))

    if _has_high_risk_exact_constraints(constraints):
        reuse_level = "strict"
    elif asset_category in GENERIC_CATEGORIES and not _has_high_risk_exact_constraints(constraints):
        reuse_level = "medium"
    elif asset_category in STRICT_CATEGORIES and constraints:
        reuse_level = "strict"

    if reuse_level == "strict":
        generic_support_allowed = False
    elif "generic_support_allowed" in asset and isinstance(asset.get("generic_support_allowed"), bool):
        generic_support_allowed = bool(asset.get("generic_support_allowed"))
    else:
        generic_support_allowed = True

    return {
        "reuse_level": reuse_level,
        "asset_category": asset_category,
        "core_constraints": constraints,
        "generic_support_allowed": generic_support_allowed,
    }


def apply_reuse_policy_defaults(asset: dict[str, Any]) -> dict[str, Any]:
    """Mutate asset with normalized reuse policy fields and return it."""

    asset.update(normalize_reuse_policy_fields(asset))
    return asset


def normalize_core_constraints(value: Any, *, max_items: int = 12) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    constraints: list[dict[str, Any]] = []
    seen: set[tuple[str, str, bool]] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = _clean_text(item.get("kind"))
        raw_value = _clean_text(item.get("value"))
        if kind not in CONSTRAINT_KINDS or not raw_value:
            continue
        if _looks_like_style_or_quality_value(raw_value):
            continue
        exact = item.get("exact")
        if not isinstance(exact, bool):
            exact = True
        normalized_value = _normalize_constraint_value(kind, raw_value)
        key = (kind, normalized_value, exact)
        if key in seen:
            continue
        seen.add(key)
        constraints.append({"kind": kind, "value": raw_value, "exact": exact})
        if len(constraints) >= max_items:
            break
    return constraints


def reuse_threshold_for_target(target: dict[str, Any], explicit_threshold: float | None = None) -> float:
    if explicit_threshold is not None:
        try:
            return _clamp(float(explicit_threshold))
        except (TypeError, ValueError):
            pass

    policy = normalize_reuse_policy_fields(target)
    category = policy["asset_category"]
    reuse_level = policy["reuse_level"]
    threshold = CATEGORY_THRESHOLDS.get(category, CATEGORY_THRESHOLDS["unknown"])
    threshold += REUSE_LEVEL_DELTAS.get(reuse_level, 0.0)
    return round(_clamp(threshold, minimum=0.30, maximum=0.75), 4)


def evaluate_reuse_filter(
    target: dict[str, Any],
    candidate: dict[str, Any],
    score_details: dict[str, Any] | None = None,
    *,
    threshold: float | None = None,
) -> dict[str, Any]:
    score_details = score_details or {}
    target_kind = _clean_text(target.get("asset_kind"))
    candidate_kind = _clean_text(candidate.get("asset_kind"))
    score = _score_from_details(score_details)
    threshold_used = reuse_threshold_for_target(target, explicit_threshold=threshold)
    score_gap = score - threshold_used

    if target_kind != candidate_kind:
        return _result(
            "reject",
            "asset_kind_mismatch",
            confidence=1.0,
            threshold=threshold_used,
            score_gap=score_gap,
        )

    if target_kind == "background":
        if score >= threshold_used:
            return _result(
                "full_match",
                "background_score_above_threshold",
                confidence=0.9,
                threshold=threshold_used,
                score_gap=score_gap,
            )
        return _result(
            "reject",
            "background_score_below_threshold",
            confidence=0.9,
            threshold=threshold_used,
            score_gap=score_gap,
        )

    if score < threshold_used - LOW_SCORE_REJECT_MARGIN:
        return _result(
            "reject",
            "score_far_below_policy_threshold",
            confidence=0.9,
            threshold=threshold_used,
            score_gap=score_gap,
        )

    target_policy = normalize_reuse_policy_fields(target)
    candidate_policy = normalize_reuse_policy_fields(candidate)
    target_category = target_policy["asset_category"]
    candidate_category = candidate_policy["asset_category"]
    target_level = target_policy["reuse_level"]

    if (
        target_category == "unknown"
        and candidate_category == "unknown"
        and not target_policy["core_constraints"]
        and not candidate_policy["core_constraints"]
        and score_gap >= CONFIDENT_LOOSE_MARGIN
    ):
        return _result(
            "full_match",
            "legacy_unconstrained_metadata_score_match",
            confidence=0.7,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

    compatibility = CATEGORY_COMPATIBILITY.get(target_category, CATEGORY_COMPATIBILITY["unknown"])
    full_compatible = candidate_category in compatibility["full"]
    support_compatible = candidate_category in compatibility["support"]

    missing, conflicts = compare_core_constraints(
        target_policy["core_constraints"],
        candidate_policy["core_constraints"],
        strict_target=target_level == "strict",
    )
    if conflicts:
        return _result(
            "reject",
            "candidate_core_constraints_conflict",
            conflicts=conflicts,
            confidence=0.95,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

    if target_level == "loose":
        if full_compatible and score_gap >= 0:
            return _result(
                "full_match",
                "loose_category_match",
                confidence=0.85 if score_gap >= CONFIDENT_LOOSE_MARGIN else 0.7,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if candidate_policy["generic_support_allowed"] and (support_compatible or score_gap >= 0):
            return _result(
                "generic_support",
                "loose_candidate_allowed_as_support",
                confidence=0.7,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        return _result(
            "uncertain",
            "loose_candidate_not_clearly_compatible",
            missing=missing,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

    if target_level == "medium":
        if full_compatible and not missing:
            return _result(
                "full_match",
                "medium_category_and_constraints_match",
                confidence=0.8,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if candidate_policy["generic_support_allowed"] and (support_compatible or candidate_category in GENERIC_CATEGORIES):
            return _result(
                "generic_support",
                "medium_candidate_allowed_as_generic_support",
                missing=missing,
                confidence=0.75,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        return _result(
            "uncertain",
            "medium_constraints_or_category_not_confirmed",
            missing=missing,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

    if not full_compatible:
        if candidate_policy["generic_support_allowed"] and support_compatible:
            return _result(
                "generic_support",
                "strict_target_candidate_only_supports_context",
                missing=missing,
                confidence=0.7,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        return _result(
            "reject",
            "strict_target_category_incompatible",
            missing=missing,
            confidence=0.9,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

    if not missing:
        return _result(
            "full_match",
            "strict_core_constraints_covered",
            confidence=0.9,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

    return _result(
        "uncertain",
        "strict_core_constraints_missing",
        missing=missing,
        confidence=0.4,
        threshold=threshold_used,
        score_gap=score_gap,
        target_policy=target_policy,
        candidate_policy=candidate_policy,
    )


def evaluate_aspect_transform(target: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Choose a safe transform mode and score penalty for aspect-ratio mismatch."""

    source_label = _clean_text(candidate.get("aspect_ratio"))
    target_label = _clean_text(target.get("aspect_ratio"))
    source_ratio = _parse_aspect_ratio(source_label)
    target_ratio = _parse_aspect_ratio(target_label)
    if source_ratio <= 0 or target_ratio <= 0:
        return {
            "decision": "accept",
            "mode": "copy",
            "crop_loss": 0.0,
            "transform_penalty": 0.0,
            "source_aspect_ratio": source_label,
            "target_aspect_ratio": target_label,
            "reason": "missing_or_invalid_aspect_ratio",
        }

    loss = _crop_loss(source_ratio, target_ratio)
    reversed_orientation = _orientation(source_ratio) != _orientation(target_ratio) and "square" not in {
        _orientation(source_ratio),
        _orientation(target_ratio),
    }
    target_policy = normalize_reuse_policy_fields(target)
    category = target_policy["asset_category"]
    reuse_level = target_policy["reuse_level"]
    role = _clean_text(target.get("role") or candidate.get("role"))
    has_constraints = bool(target_policy["core_constraints"])
    asset_kind = _clean_text(target.get("asset_kind"))

    if loss <= 0.02:
        return _transform_result(
            "accept",
            "copy",
            loss,
            0.0,
            source_label,
            target_label,
            "aspect_ratio_aligned",
        )

    if asset_kind == "background":
        if loss <= 0.05:
            return _transform_result("accept", "micro_stretch", loss, 0.01, source_label, target_label, "background_micro_stretch")
        if loss <= 0.12:
            return _transform_result("penalize", "cover_crop", loss, 0.02, source_label, target_label, "background_light_crop")
        if loss <= 0.25:
            return _transform_result("penalize", "blur_pad", loss, 0.06, source_label, target_label, "background_blur_pad")
        if loss <= 0.35 and not reversed_orientation:
            return _transform_result("penalize", "blur_pad", loss, 0.10, source_label, target_label, "background_high_pad")
        return _transform_result("reject", "copy", loss, 0.18, source_label, target_label, "background_aspect_mismatch_too_large")

    if role == "hero" and loss > 0.12:
        return _transform_result("reject", "copy", loss, 0.18, source_label, target_label, "hero_aspect_mismatch_too_large")

    if role == "icon":
        if loss <= 0.12:
            return _transform_result("penalize", "contain_pad", loss, 0.04, source_label, target_label, "icon_content_preserving_pad")
        if loss <= 0.25 and not reversed_orientation:
            return _transform_result("penalize", "contain_pad", loss, 0.09, source_label, target_label, "icon_medium_pad")
        return _transform_result("reject", "copy", loss, 0.18, source_label, target_label, "icon_aspect_mismatch_too_large")

    if reuse_level == "strict" or category in {"content_specific", "character_action"} or has_constraints:
        if loss <= 0.05:
            return _transform_result("accept", "copy", loss, 0.0, source_label, target_label, "strict_small_mismatch")
        if loss <= 0.12 and not reversed_orientation:
            return _transform_result("penalize", "contain_pad", loss, 0.05, source_label, target_label, "strict_content_preserving_pad")
        return _transform_result("reject", "copy", loss, 0.18, source_label, target_label, "strict_aspect_mismatch_too_large")

    if category in {"generic_tool", "generic_diagram"}:
        if loss <= 0.05:
            return _transform_result("accept", "copy", loss, 0.0, source_label, target_label, "generic_structure_small_mismatch")
        if loss <= 0.12:
            return _transform_result("penalize", "contain_pad", loss, 0.04, source_label, target_label, "generic_structure_light_pad")
        if loss <= 0.25 and not reversed_orientation:
            return _transform_result("penalize", "contain_pad", loss, 0.09, source_label, target_label, "generic_structure_medium_pad")
        return _transform_result("reject", "copy", loss, 0.18, source_label, target_label, "generic_structure_aspect_mismatch_too_large")

    if category == "learning_behavior":
        if loss <= 0.05:
            return _transform_result("accept", "copy", loss, 0.0, source_label, target_label, "learning_behavior_small_mismatch")
        if loss <= 0.12:
            return _transform_result("penalize", "cover_crop", loss, 0.03, source_label, target_label, "learning_behavior_light_crop")
        if loss <= 0.25 and not reversed_orientation:
            return _transform_result("penalize", "contain_pad", loss, 0.08, source_label, target_label, "learning_behavior_medium_pad")
        return _transform_result("reject", "copy", loss, 0.18, source_label, target_label, "learning_behavior_aspect_mismatch_too_large")

    if category == "concept_scene":
        if loss <= 0.05:
            return _transform_result("accept", "copy", loss, 0.0, source_label, target_label, "concept_scene_small_mismatch")
        if loss <= 0.12:
            return _transform_result("penalize", "cover_crop", loss, 0.03, source_label, target_label, "concept_scene_light_crop")
        if loss <= 0.25 and not reversed_orientation:
            return _transform_result("penalize", "blur_pad", loss, 0.08, source_label, target_label, "concept_scene_medium_blur_pad")
        return _transform_result("reject", "copy", loss, 0.18, source_label, target_label, "concept_scene_aspect_mismatch_too_large")

    if loss <= 0.05:
        return _transform_result("accept", "copy", loss, 0.0, source_label, target_label, "unknown_small_mismatch")
    if loss <= 0.12:
        return _transform_result("penalize", "contain_pad", loss, 0.05, source_label, target_label, "unknown_light_pad")
    if loss <= 0.25 and not reversed_orientation:
        return _transform_result("penalize", "contain_pad", loss, 0.10, source_label, target_label, "unknown_medium_pad")
    return _transform_result("reject", "copy", loss, 0.18, source_label, target_label, "unknown_aspect_mismatch_too_large")


def compare_core_constraints(
    target_constraints: list[dict[str, Any]],
    candidate_constraints: list[dict[str, Any]],
    *,
    strict_target: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    missing: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for target in target_constraints:
        same_kind = [candidate for candidate in candidate_constraints if candidate["kind"] == target["kind"]]
        if not same_kind:
            missing.append(target)
            continue
        if any(_constraints_equivalent(target, candidate) for candidate in same_kind):
            continue
        if _is_specific_constraint(target) or any(_is_specific_constraint(candidate) for candidate in same_kind):
            conflicts.append(
                {
                    "kind": target["kind"],
                    "target": target["value"],
                    "candidate_values": [candidate["value"] for candidate in same_kind],
                }
            )
        else:
            missing.append(target)

    if strict_target and target_constraints:
        target_kinds = {item["kind"] for item in target_constraints if _is_specific_constraint(item)}
        for candidate in candidate_constraints:
            if candidate["kind"] not in target_kinds or not _is_specific_constraint(candidate):
                continue
            if any(
                target["kind"] == candidate["kind"] and _constraints_equivalent(target, candidate)
                for target in target_constraints
            ):
                continue
            conflicts.append(
                {
                    "kind": candidate["kind"],
                    "target": "",
                    "candidate_values": [candidate["value"]],
                }
            )

    return missing, _dedupe_conflicts(conflicts)


def _constraints_equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["kind"] != right["kind"]:
        return False
    left_value = _normalize_constraint_value(left["kind"], left["value"])
    right_value = _normalize_constraint_value(right["kind"], right["value"])
    if not left_value or not right_value:
        return False
    if left.get("exact", True) or right.get("exact", True):
        return left_value == right_value
    return _soft_equivalent(left_value, right_value)


def _parse_aspect_ratio(value: Any) -> float:
    text = _clean_text(value).lower()
    if not text:
        return 0.0
    parts = re.split(r"[:/x×]", text)
    if len(parts) == 2:
        try:
            width = float(parts[0])
            height = float(parts[1])
        except ValueError:
            return 0.0
        return width / height if width > 0 and height > 0 else 0.0
    try:
        value_float = float(text)
    except ValueError:
        return 0.0
    return value_float if value_float > 0 else 0.0


def _crop_loss(source_ratio: float, target_ratio: float) -> float:
    if source_ratio <= 0 or target_ratio <= 0:
        return 0.25
    return 1.0 - min(source_ratio, target_ratio) / max(source_ratio, target_ratio)


def _orientation(ratio: float) -> str:
    if ratio <= 0:
        return ""
    if abs(ratio - 1.0) <= 0.03:
        return "square"
    return "landscape" if ratio > 1.0 else "portrait"


def _transform_result(
    decision: str,
    mode: str,
    crop_loss: float,
    penalty: float,
    source_aspect_ratio: str,
    target_aspect_ratio: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "mode": mode,
        "crop_loss": round(_clamp(crop_loss), 4),
        "transform_penalty": round(_clamp(penalty), 4),
        "source_aspect_ratio": source_aspect_ratio,
        "target_aspect_ratio": target_aspect_ratio,
        "reason": reason,
    }


def _soft_equivalent(left: str, right: str) -> bool:
    if left == right:
        return True
    if min(len(left), len(right)) >= 3 and (left in right or right in left):
        return True
    left_terms = set(re.findall(r"[a-z0-9]+", left))
    right_terms = set(re.findall(r"[a-z0-9]+", right))
    if not left_terms or not right_terms:
        return False
    return len(left_terms & right_terms) / max(1, len(left_terms | right_terms)) >= 0.6


def _normalize_constraint_value(kind: str, value: Any) -> str:
    text = _clean_text(value).casefold()
    text = re.sub(r"^[a-z_ -]{1,24}[:：]\s*", "", text)
    if kind in {"math", "physics", "text", "relation"}:
        text = re.sub(r"\s+", "", text)
    else:
        text = re.sub(r"\s+", " ", text)
    return text.strip(" ,;:()[]{}<>")


def _is_specific_constraint(constraint: dict[str, Any]) -> bool:
    kind = constraint.get("kind")
    return bool(constraint.get("exact", True) or kind in HIGH_RISK_KINDS)


def _has_high_risk_exact_constraints(constraints: list[dict[str, Any]]) -> bool:
    return any(item.get("exact", True) and item.get("kind") in HIGH_RISK_KINDS for item in constraints)


def _looks_like_style_or_quality_value(value: str) -> bool:
    normalized = _clean_text(value).casefold().replace(" ", "")
    if not normalized:
        return True
    style_markers = (
        "style",
        "prompt",
        "highquality",
        "hd",
        "4k",
        "16:9",
        "1:1",
        "watercolor",
        "cartoon",
        "illustration",
        "composition",
        "palette",
        "color",
    )
    return normalized in style_markers


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
    missing: list[dict[str, Any]] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    confidence: float = 0.5,
    threshold: float = 0.0,
    score_gap: float = 0.0,
    target_policy: dict[str, Any] | None = None,
    candidate_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "decision": decision,
        "reason": reason,
        "missing": missing or [],
        "conflicts": conflicts or [],
        "confidence": _clamp(confidence),
        "threshold_used": round(float(threshold or 0.0), 4),
        "score_gap": round(float(score_gap or 0.0), 4),
    }
    if target_policy is not None:
        payload["target_policy"] = target_policy
    if candidate_policy is not None:
        payload["candidate_policy"] = candidate_policy
    return payload


def _dedupe_conflicts(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for item in conflicts:
        values = tuple(_clean_text(value) for value in item.get("candidate_values") or [])
        key = (_clean_text(item.get("kind")), _clean_text(item.get("target")), values)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clamp(value: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))
