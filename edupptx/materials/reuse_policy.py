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
CONSTRAINT_KINDS = {
    "text",
    "math",
    "physics",
    "entity",
    "object",
    "action",
    "relation",
    "setting",
    "emotion",
    "count",
}

DEFAULT_POLICY = {
    "reuse_level": "medium",
    "asset_category": "unknown",
    "core_constraints": [],
    "generic_support_allowed": True,
}

CATEGORY_THRESHOLDS = {
    "learning_behavior": 0.40,
    "generic_tool": 0.40,
    "generic_diagram": 0.48,
    "concept_scene": 0.40,
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

HIGH_RISK_KINDS = {"text", "math", "physics", "count", "relation"}
LLM_REVIEW_REQUIRED_KINDS = {"text", "math", "physics", "count", "relation"}
VISUAL_SEMANTIC_KINDS = {"entity", "object", "action", "setting", "emotion"}
CONSTRAINT_EMBEDDING_THRESHOLDS = {
    "entity": (0.92, 0.80),
    "object": (0.92, 0.80),
    "action": (0.86, 0.74),
    "setting": (0.84, 0.72),
    "emotion": (0.84, 0.72),
    "text": (0.90, 0.78),
    "math": (0.90, 0.78),
    "physics": (0.90, 0.78),
    "count": (0.90, 0.78),
    "relation": (0.90, 0.78),
}
STRICT_CATEGORIES = {"content_specific", "character_action"}
MEDIUM_CATEGORIES = {"learning_behavior", "generic_tool", "generic_diagram", "concept_scene"}
GENERIC_CATEGORIES = {"generic_tool", "generic_diagram"}
CONTENT_PRESERVING_CATEGORIES = {"generic_tool", "generic_diagram", "content_specific", "character_action"}
SEMANTIC_SCENE_CATEGORIES = {"concept_scene", "character_action"}
SEMANTIC_SIGNAL_ACCEPT_REASONS = {"embedding_gray_zone", "substring_embedding_gray_zone"}
SEMANTIC_EMBEDDING_ACCEPT_THRESHOLD = 0.82
SEMANTIC_SCORE_FLOOR = 0.18
STRICT_CONTENT_CONTEXT_MARKERS = (
    "故事情节",
    "课文情节",
    "中间情节",
    "情节节点",
    "事件节点",
    "故事结局",
    "课文结局",
    "成长结局",
    "最终场景",
    "具体情节",
    "梳理故事",
    "梳理情节",
)
STRICT_CONTENT_VISUAL_MARKERS = (
    "对话",
    "遇到",
    "找到",
    "变成",
    "一起",
    "结局",
)
STRICT_CHARACTER_CONTEXT_MARKERS = (
    "情节时间线",
    "课文情节",
    "情节发展",
    "课文结局",
    "主人公",
    "人物情绪",
    "人物状态",
    "情绪转变",
    "母亲心意",
    "关系缓和",
    "经典场景",
)
STRICT_CHARACTER_VISUAL_MARKERS = (
    "摔",
    "说话",
    "对话",
    "坐在",
    "独自",
    "望",
    "站在",
    "轮椅",
    "瘫痪",
    "碎",
    "生气",
    "激动",
    "平静",
    "缓和",
    "压抑",
    "暴怒",
    "痛苦",
    "绝望",
    "妹妹",
    "母亲",
    "儿子",
)
STRICT_EMOTION_TERMS = (
    "生气",
    "激动",
    "平静",
    "缓和",
    "压抑",
    "暴怒",
    "痛苦",
    "绝望",
    "沉静",
)

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
        "full": {"concept_scene", "character_action"},
        "support": {"learning_behavior", "generic_diagram", "generic_tool"},
    },
    "content_specific": {
        "full": {"content_specific"},
        "support": {"generic_tool", "generic_diagram", "concept_scene", "character_action"},
    },
    "character_action": {
        "full": {"character_action", "concept_scene"},
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
    reuse_risk = normalize_reuse_risk_fields(asset)

    strict_downgraded = False
    if not constraints:
        constraints = _infer_strict_core_constraints(asset, asset_category)

    if asset_category in MEDIUM_CATEGORIES and not _has_high_risk_kind_constraints(constraints):
        # Medium-pool categories are intentionally threshold based. LLMs often
        # mark ordinary visible subjects/actions as hard constraints because a
        # page "needs" that subject, but those signals belong in keywords.
        strict_downgraded = (
            reuse_level == "strict"
            or any(reuse_risk.values())
            or any(item.get("kind") in VISUAL_SEMANTIC_KINDS for item in constraints)
        )
        reuse_level = "medium"
        constraints = []
    elif _has_strict_reuse_risk(constraints, reuse_risk):
        reuse_level = "strict"
    elif reuse_level == "strict" and constraints and asset_category in STRICT_CATEGORIES:
        # Strict category assets may rely on visible subjects, actions, or
        # relations that must survive reuse. Medium categories stay threshold
        # based unless metadata marks a high-risk exact constraint above.
        reuse_level = "strict"
    elif asset_category in MEDIUM_CATEGORIES and not _has_high_risk_exact_constraints(constraints):
        strict_downgraded = reuse_level == "strict"
        reuse_level = "medium"
    elif reuse_level == "strict":
        # A strict LLM label without any structured constraint is advisory.
        # Backgrounds are handled above; other visual assets fall back to the
        # normal medium threshold path.
        reuse_level = "medium"
        strict_downgraded = True

    if reuse_level == "strict":
        generic_support_allowed = False
    elif strict_downgraded:
        generic_support_allowed = True
    elif "generic_support_allowed" in asset and isinstance(asset.get("generic_support_allowed"), bool):
        generic_support_allowed = bool(asset.get("generic_support_allowed"))
    else:
        generic_support_allowed = True

    if reuse_level != "strict":
        constraints = []

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


def _infer_strict_core_constraints(asset: dict[str, Any], asset_category: str) -> list[dict[str, Any]]:
    if asset_category not in STRICT_CATEGORIES:
        return []

    prompt = _clean_text(asset.get("normalized_prompt")) or _clean_text(asset.get("content_prompt"))
    if not prompt:
        return []

    context_text = _join_policy_text(
        asset.get("context_summary"),
        asset.get("teaching_intent"),
        _source_field(asset, "content_points"),
        _source_field(asset, "page_title"),
    )
    prompt_text = _join_policy_text(prompt, asset.get("content_prompt"))

    if asset_category == "content_specific" and _has_any(
        context_text,
        STRICT_CONTENT_CONTEXT_MARKERS,
    ) and _has_any(prompt_text, STRICT_CONTENT_VISUAL_MARKERS):
        return _strict_relation_constraints(prompt)

    if asset_category == "character_action" and _has_any(
        context_text,
        STRICT_CHARACTER_CONTEXT_MARKERS,
    ) and _has_any(prompt_text, STRICT_CHARACTER_VISUAL_MARKERS):
        constraints = _strict_relation_constraints(prompt)
        emotion = _first_marker(prompt_text, STRICT_EMOTION_TERMS)
        if emotion:
            constraints.append({"kind": "emotion", "value": emotion, "exact": False, "hard": True})
        return normalize_core_constraints(constraints)

    return []


def _strict_relation_constraints(value: str) -> list[dict[str, Any]]:
    return normalize_core_constraints(
        [{"kind": "relation", "value": value, "exact": False, "hard": True}]
    )


def _join_policy_text(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        if isinstance(value, list):
            parts.extend(_clean_text(item) for item in value)
        else:
            parts.append(_clean_text(value))
    return " ".join(part for part in parts if part)


def _source_field(asset: dict[str, Any], key: str) -> Any:
    source = asset.get("source")
    return source.get(key) if isinstance(source, dict) else None


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker and marker in text for marker in markers)


def _first_marker(text: str, markers: tuple[str, ...]) -> str:
    for marker in markers:
        if marker and marker in text:
            return marker
    return ""


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
        constraint = {"kind": kind, "value": raw_value, "exact": exact}
        aliases = _clean_alias_list(item.get("aliases"))
        if aliases:
            constraint["aliases"] = aliases
        if isinstance(item.get("hard"), bool) and item.get("hard"):
            constraint["hard"] = True
        constraints.append(constraint)
        if len(constraints) >= max_items:
            break
    return constraints


def normalize_reuse_risk_fields(asset: dict[str, Any]) -> dict[str, bool]:
    risk = asset.get("reuse_risk")
    risk = risk if isinstance(risk, dict) else {}
    return {
        "readable_knowledge": _risk_required(
            risk.get("readable_knowledge", asset.get("readable_knowledge"))
        ),
        "unique_referent": _risk_required(risk.get("unique_referent", asset.get("unique_referent"))),
        "exact_relation": _risk_required(risk.get("exact_relation", asset.get("exact_relation"))),
    }


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

    target_policy = normalize_reuse_policy_fields(target)
    candidate_policy = normalize_reuse_policy_fields(candidate)
    target_category = target_policy["asset_category"]
    candidate_category = candidate_policy["asset_category"]
    target_level = target_policy["reuse_level"]
    candidate_level = candidate_policy["reuse_level"]
    semantic_signal = _has_semantic_reuse_signal(score_details, score)

    if target_level != "strict" and candidate_level != "strict":
        if score_gap >= 0 or semantic_signal:
            return _result(
                "full_match",
                "medium_similarity_threshold_match",
                confidence=0.8 if score_gap >= 0 else 0.7,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        return _result(
            "reject",
            "similarity_below_threshold",
            confidence=0.85,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

    if target_level == "strict" or candidate_level == "strict":
        if score_gap < 0 and not semantic_signal:
            return _result(
                "reject",
                "strict_similarity_below_threshold",
                confidence=0.9,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if not target_policy["core_constraints"] and not candidate_policy["core_constraints"]:
            return _result(
                "full_match",
                "strict_unconstrained_similarity_match",
                confidence=0.75 if score_gap >= 0 else 0.65,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        strict_missing: list[dict[str, Any]] = []
        strict_conflicts: list[dict[str, Any]] = []
        strict_reviews: list[dict[str, Any]] = []
        if target_level == "strict":
            missing, conflicts, reviews = compare_strict_core_constraints(
                target_policy["core_constraints"],
                candidate_policy["core_constraints"],
                score_details=score_details,
            )
            strict_missing.extend(missing)
            strict_conflicts.extend(conflicts)
            strict_reviews.extend(reviews)
        if candidate_level == "strict":
            missing, conflicts, reviews = compare_strict_core_constraints(
                candidate_policy["core_constraints"],
                target_policy["core_constraints"],
                score_details=score_details,
                side="candidate",
            )
            strict_missing.extend(
                {"kind": item["kind"], "value": item["value"], "exact": item.get("exact", True), "side": "candidate"}
                for item in missing
            )
            strict_conflicts.extend(conflicts)
            strict_reviews.extend(reviews)
        strict_conflicts = _dedupe_conflicts(strict_conflicts)
        strict_reviews = _dedupe_review_items(strict_reviews)
        if strict_conflicts:
            return _result(
                "reject",
                "strict_core_constraints_conflict",
                conflicts=strict_conflicts,
                confidence=0.95,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if strict_reviews:
            return _result(
                "llm_review",
                "strict_core_constraints_require_llm_review",
                review_items=strict_reviews,
                confidence=0.55,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if strict_missing:
            return _result(
                "reject",
                "strict_core_constraints_missing",
                missing=strict_missing,
                confidence=0.9,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        return _result(
            "full_match",
            "strict_core_constraints_covered",
            confidence=0.9,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

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

    hard_missing = [item for item in missing if _is_specific_constraint(item)]
    categories_semantically_compatible = _semantic_categories_compatible(
        target_policy,
        candidate_policy,
        full_compatible=full_compatible,
        support_compatible=support_compatible,
    )

    if score < threshold_used - LOW_SCORE_REJECT_MARGIN and not (
        semantic_signal and categories_semantically_compatible and not hard_missing
    ):
        return _result(
            "reject",
            "score_far_below_policy_threshold",
            missing=missing,
            confidence=0.9,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

    if target_level == "loose":
        if full_compatible and (score_gap >= 0 or semantic_signal):
            return _result(
                "full_match",
                "loose_category_match",
                confidence=0.85 if score_gap >= CONFIDENT_LOOSE_MARGIN else 0.7,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if candidate_policy["generic_support_allowed"] and (
            support_compatible or score_gap >= 0 or (semantic_signal and categories_semantically_compatible)
        ):
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
        if categories_semantically_compatible and not hard_missing and (score_gap >= 0 or semantic_signal):
            return _result(
                "full_match",
                "medium_semantic_match",
                confidence=0.8,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if candidate_policy["generic_support_allowed"] and (
            support_compatible
            or candidate_category in GENERIC_CATEGORIES
            or (semantic_signal and categories_semantically_compatible and not hard_missing)
        ):
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

    if not full_compatible and not (semantic_signal and categories_semantically_compatible and not hard_missing):
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

    if not hard_missing:
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
        missing=hard_missing,
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

    if role == "hero" and loss > 0.25:
        return _transform_result("reject", "copy", loss, 0.18, source_label, target_label, "hero_aspect_mismatch_too_large")
    if role == "hero" and loss > 0.12:
        return _transform_result("penalize", "contain_pad", loss, 0.10, source_label, target_label, "hero_content_preserving_pad")

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
        if loss <= 0.25 and not reversed_orientation:
            return _transform_result("penalize", "contain_pad", loss, 0.10, source_label, target_label, "strict_content_preserving_medium_pad")
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
            return _transform_result("penalize", "contain_pad", loss, 0.08, source_label, target_label, "concept_scene_medium_pad")
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


def compare_strict_core_constraints(
    required_constraints: list[dict[str, Any]],
    available_constraints: list[dict[str, Any]],
    *,
    score_details: dict[str, Any] | None = None,
    side: str = "target",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Require strict constraints to be covered, rejected, or LLM-reviewed."""

    missing: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    for required in required_constraints:
        same_kind = [
            available
            for available in available_constraints
            if available["kind"] == required["kind"]
        ]
        if not same_kind:
            reviews.append(_constraint_review_item(required, [], "missing_same_kind", side=side))
            continue
        match_results = [
            _strict_constraint_match_result(required, available, score_details or {}, side=side)
            for available in same_kind
        ]
        if any(item["decision"] == "matched" for item in match_results):
            continue
        review_results = [item for item in match_results if item["decision"] == "llm_review"]
        if review_results:
            reviews.append(_best_review_result(required, review_results, side=side))
            continue
        conflicts.append(
            {
                "kind": required["kind"],
                "target": required["value"],
                "candidate_values": [available["value"] for available in same_kind],
            }
        )
    return missing, _dedupe_conflicts(conflicts), _dedupe_review_items(reviews)


def _constraints_equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["kind"] != right["kind"]:
        return False
    if _light_constraint_match_method(left, right):
        return True
    left_value = _normalize_constraint_value(left["kind"], left["value"])
    right_value = _normalize_constraint_value(right["kind"], right["value"])
    return _soft_equivalent(left_value, right_value)


def _strict_constraint_match_result(
    required: dict[str, Any],
    available: dict[str, Any],
    score_details: dict[str, Any],
    *,
    side: str,
) -> dict[str, Any]:
    kind = _clean_text(required.get("kind"))
    light_method = _light_constraint_match_method(required, available)
    if light_method:
        if kind in LLM_REVIEW_REQUIRED_KINDS:
            return _constraint_review_item(
                required,
                [available],
                light_method,
                side=side,
                decision="llm_review",
            )
        return {"decision": "matched", "match_method": light_method}

    embedding_score = _constraint_embedding_score(required, available, score_details)
    if embedding_score is None:
        return _constraint_review_item(
            required,
            [available],
            "missing_constraint_embedding",
            side=side,
            decision="llm_review",
        )

    high, low = CONSTRAINT_EMBEDDING_THRESHOLDS.get(kind, (0.88, 0.76))
    if embedding_score >= high:
        method = "embedding_high"
        if kind in LLM_REVIEW_REQUIRED_KINDS:
            return _constraint_review_item(
                required,
                [available],
                method,
                side=side,
                decision="llm_review",
                embedding_score=embedding_score,
            )
        return {"decision": "matched", "match_method": method, "embedding_score": embedding_score}
    if embedding_score >= low:
        return _constraint_review_item(
            required,
            [available],
            "embedding_gray",
            side=side,
            decision="llm_review",
            embedding_score=embedding_score,
        )
    return {"decision": "failed", "match_method": "embedding_low", "embedding_score": embedding_score}


def _light_constraint_match_method(left: dict[str, Any], right: dict[str, Any]) -> str:
    kind = _clean_text(left.get("kind"))
    if kind != _clean_text(right.get("kind")):
        return ""
    left_value = _normalize_constraint_value(kind, left.get("value"))
    right_value = _normalize_constraint_value(kind, right.get("value"))
    if not left_value or not right_value:
        return ""
    if left_value == right_value:
        return "exact"
    if min(len(left_value), len(right_value)) >= 2 and (left_value in right_value or right_value in left_value):
        return "contains"
    left_aliases = {
        _normalize_constraint_value(kind, alias)
        for alias in _clean_alias_list(left.get("aliases"))
    }
    right_aliases = {
        _normalize_constraint_value(kind, alias)
        for alias in _clean_alias_list(right.get("aliases"))
    }
    left_terms = {left_value, *left_aliases}
    right_terms = {right_value, *right_aliases}
    if left_terms & right_terms:
        return "alias"
    return ""


def _constraint_embedding_score(
    required: dict[str, Any],
    available: dict[str, Any],
    score_details: dict[str, Any],
) -> float | None:
    kind = _clean_text(required.get("kind"))
    required_value = _normalize_constraint_value(kind, required.get("value"))
    available_value = _normalize_constraint_value(kind, available.get("value"))
    if not kind or not required_value or not available_value:
        return None
    items = score_details.get("constraint_embedding_scores")
    if not isinstance(items, list):
        return None
    best: float | None = None
    for item in items:
        if not isinstance(item, dict) or _clean_text(item.get("kind")) != kind:
            continue
        left = _normalize_constraint_value(kind, item.get("target"))
        right = _normalize_constraint_value(kind, item.get("candidate"))
        if {left, right} != {required_value, available_value}:
            continue
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError):
            continue
        best = score if best is None else max(best, score)
    return best


def _constraint_review_item(
    required: dict[str, Any],
    available_constraints: list[dict[str, Any]],
    reason: str,
    *,
    side: str,
    decision: str = "llm_review",
    embedding_score: float | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "decision": decision,
        "kind": required.get("kind"),
        "value": required.get("value"),
        "exact": required.get("exact", True),
        "hard": bool(required.get("hard")),
        "side": side,
        "reason": reason,
        "candidate_values": [available.get("value") for available in available_constraints],
    }
    if embedding_score is not None:
        item["embedding_score"] = round(float(embedding_score), 4)
    return item


def _best_review_result(required: dict[str, Any], reviews: list[dict[str, Any]], *, side: str) -> dict[str, Any]:
    if not reviews:
        return _constraint_review_item(required, [], "missing_same_kind", side=side)
    def rank(item: dict[str, Any]) -> tuple[int, float]:
        reason = _clean_text(item.get("reason"))
        reason_rank = {"exact": 4, "contains": 3, "alias": 3, "embedding_high": 2, "embedding_gray": 1}.get(reason, 0)
        try:
            score = float(item.get("embedding_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        return reason_rank, score

    return max(reviews, key=rank)


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
    return bool(constraint.get("hard") or kind in HIGH_RISK_KINDS)


def _has_high_risk_exact_constraints(constraints: list[dict[str, Any]]) -> bool:
    return any(item.get("kind") in HIGH_RISK_KINDS or item.get("hard") for item in constraints)


def _has_high_risk_kind_constraints(constraints: list[dict[str, Any]]) -> bool:
    return any(item.get("kind") in HIGH_RISK_KINDS for item in constraints)


def _has_strict_reuse_risk(constraints: list[dict[str, Any]], reuse_risk: dict[str, bool]) -> bool:
    return bool(
        _has_high_risk_exact_constraints(constraints)
        or reuse_risk.get("readable_knowledge")
        or reuse_risk.get("unique_referent")
        or reuse_risk.get("exact_relation")
    )


def _risk_required(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("required"))
    return bool(value)


def _has_semantic_reuse_signal(score_details: dict[str, Any], score: float) -> bool:
    accepted_by = _clean_text(score_details.get("accepted_by"))
    if accepted_by in SEMANTIC_SIGNAL_ACCEPT_REASONS:
        return True
    try:
        embedding_score = float(score_details.get("embedding_score") or 0.0)
    except (TypeError, ValueError):
        embedding_score = 0.0
    try:
        substring_score = float(score_details.get("substring_score") or 0.0)
    except (TypeError, ValueError):
        substring_score = 0.0
    return embedding_score >= SEMANTIC_EMBEDDING_ACCEPT_THRESHOLD and (
        score >= SEMANTIC_SCORE_FLOOR or substring_score > 0.0
    )


def _semantic_categories_compatible(
    target_policy: dict[str, Any],
    candidate_policy: dict[str, Any],
    *,
    full_compatible: bool,
    support_compatible: bool,
) -> bool:
    if full_compatible or support_compatible:
        return True
    target_category = target_policy["asset_category"]
    candidate_category = candidate_policy["asset_category"]
    if target_category in SEMANTIC_SCENE_CATEGORIES and candidate_category in SEMANTIC_SCENE_CATEGORIES:
        return True
    target_has_hard = any(_is_specific_constraint(item) for item in target_policy["core_constraints"])
    candidate_has_hard = any(_is_specific_constraint(item) for item in candidate_policy["core_constraints"])
    if target_has_hard or candidate_has_hard:
        return False
    return bool(
        target_category == "content_specific" and candidate_category in SEMANTIC_SCENE_CATEGORIES
        or candidate_category == "content_specific" and target_category in SEMANTIC_SCENE_CATEGORIES
    )


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
    review_items: list[dict[str, Any]] | None = None,
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
        "review_items": review_items or [],
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


def _dedupe_review_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, tuple[str, ...], str]] = set()
    for item in items:
        values = tuple(_clean_text(value) for value in item.get("candidate_values") or [])
        key = (
            _clean_text(item.get("side")),
            _clean_text(item.get("kind")),
            _clean_text(item.get("value")),
            values,
            _clean_text(item.get("reason")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _clean_alias_list(value: Any, *, max_items: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    aliases: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_text(item)
        if not text or _looks_like_style_or_quality_value(text):
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        aliases.append(text)
        if len(aliases) >= max_items:
            break
    return aliases


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clamp(value: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))
