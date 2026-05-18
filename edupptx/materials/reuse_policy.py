"""Deterministic reuse policy for simplified AI image metadata."""

from __future__ import annotations

from dataclasses import dataclass
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
    "emotion_scene",
    "symbolic_material",
    "unknown",
}
FORCED_LOOSE_CATEGORIES = {"learning_behavior", "generic_tool", "generic_diagram"}
CONSTRAINT_KINDS = {
    "entity",
    "object",
    "action",
    "scene",
    "emotion",
    "text",
    "math",
    "physics",
}
STRICT_KNOWLEDGE_CONSTRAINT_KINDS = {"text", "math", "physics"}
CONSTRAINT_SUBTYPES = {
    "named_individual",
    "species_instance",
    "role",
    "generic_class",
    "teaching_carrier",
    "scene_prop",
    "decorative",
    "teaching_fact",
    "generic_motion",
    "teaching_content",
    "decorative_text",
    "story_scene",
    "generic_ambient",
    "narrative_emotion",
}
NAMED_INDIVIDUAL_SUBTYPES = {"named_individual", "species_instance"}
ROLE_HARDCAP_TERMS = {
    "爸爸", "妈妈", "爹", "娘", "父亲", "母亲", "妈", "爸",
    "爷爷", "奶奶", "外公", "外婆", "姥爷", "姥姥",
    "叔叔", "阿姨", "伯伯", "舅舅", "姑姑", "姨妈",
    "哥哥", "姐姐", "弟弟", "妹妹",
    "儿子", "女儿", "孙子", "孙女", "外孙", "宝宝", "宝贝",
    "老师", "教师", "学生", "同学", "医生", "护士", "警察",
    "消防员", "农民", "工人", "司机", "厨师", "服务员", "售货员",
    "运动员", "舞蹈家", "画家", "音乐家", "科学家", "工程师",
    "律师", "法官", "记者", "园丁", "清洁工", "邮递员", "教练",
    "男孩", "女孩", "小朋友", "孩子", "小孩",
    "男人", "女人", "人物", "人", "卡通人物", "动漫人物",
    "动物", "植物",
}

DEFAULT_POLICY = {
    "reuse_level": "medium",
    "asset_category": "unknown",
    "constraints": [],
    "generic_support_allowed": True,
}

PAGE_IMAGE_REUSE_THRESHOLDS = {
    "loose": 0.50,
    "medium": 0.55,
    "strict": 0.63,
}
BACKGROUND_REUSE_THRESHOLD = 0.38
LLM_REVIEW_REQUIRED_KINDS = {"text", "math", "physics"}
CONSTRAINT_EMBEDDING_THRESHOLDS = {
    "entity": (0.92, 0.80),
    "object": (0.92, 0.80),
    "action": (0.86, 0.74),
    "scene": (0.84, 0.72),
    "emotion": (0.84, 0.72),
    "text": (0.90, 0.78),
    "math": (0.90, 0.78),
    "physics": (0.90, 0.78),
}
SEMANTIC_SIGNAL_ACCEPT_REASONS = {"embedding_gray_zone", "substring_embedding_gray_zone"}
SCORE_GATE_LLM_REVIEW_REASONS = {
    "keyword_high_review",
    "embedding_high_review",
    "text_overlap_embedding_review",
    "keyword_led_gray_review",
    "embedding_led_gray_review",
}
SEMANTIC_EMBEDDING_ACCEPT_THRESHOLD = 0.82
SEMANTIC_SCORE_FLOOR = 0.18
STRICT_EMBEDDING_REVIEW_THRESHOLD = 0.78
STRICT_SEMANTIC_GRAY_REVIEW_THRESHOLD = 0.70
STRICT_SEMANTIC_GRAY_BM25_THRESHOLD = 0.20
MEDIUM_EMBEDDING_REVIEW_THRESHOLD = 0.80
AUTO_ACCEPT_EMBEDDING_FLOORS = {"strict": 0.58}
@dataclass(frozen=True)
class ReuseConstraint:
    """Normalized post-retrieval reuse constraint metadata."""

    kind: str
    value: str
    importance: int
    confidence: float = 0.0
    evidence: str = ""
    reason: str = ""
    subtype: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "subtype": self.subtype,
            "value": self.value,
            "importance": self.importance,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AssetMetadata:
    """Schema-normalized metadata view for page-image reuse assets."""

    raw: dict[str, Any]
    asset_category: str
    constraints: list[dict[str, Any]]
    reuse_level: str
    generic_support_allowed: bool
    duplicate_asset_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_category": self.asset_category,
            "constraints": [_copy_constraint_dict(item) for item in self.constraints],
            "reuse_level": self.reuse_level,
            "generic_support_allowed": self.generic_support_allowed,
            "duplicate_asset_ids": list(self.duplicate_asset_ids),
        }


def normalize_constraints(value: Any, *, max_items: int = 12) -> list[dict[str, Any]]:
    """Normalize page-image constraints and discard unsupported constraint fields."""

    if not isinstance(value, list):
        return []

    constraints: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for item in value:
        constraint = _normalize_constraint_item(item)
        if constraint is None:
            continue
        key = (
            constraint.kind,
            _normalize_constraint_value(constraint.kind, constraint.value),
            constraint.importance,
        )
        if key in seen:
            continue
        seen.add(key)
        constraints.append(constraint.to_dict())
        if len(constraints) >= max_items:
            break
    return constraints


def normalize_asset_metadata(raw: dict[str, Any]) -> AssetMetadata:
    """Return a unified metadata view using the current ``constraints`` schema."""

    asset = raw if isinstance(raw, dict) else {}
    constraints = normalize_constraints(asset.get("constraints"))

    asset_category = _clean_text(asset.get("asset_category"))
    if asset_category not in ASSET_CATEGORIES:
        asset_category = DEFAULT_POLICY["asset_category"]

    reuse_level = derive_reuse_level_from_constraints(constraints, asset_category)
    generic_support_allowed = reuse_level == "loose"
    return AssetMetadata(
        raw=asset,
        asset_category=asset_category,
        constraints=constraints,
        reuse_level=reuse_level,
        generic_support_allowed=generic_support_allowed,
        duplicate_asset_ids=_clean_string_list(asset.get("duplicate_asset_ids")),
    )


def _normalize_constraint_item(item: Any) -> ReuseConstraint | None:
    if not isinstance(item, dict):
        return None
    kind = _normalize_constraint_kind(item.get("kind"))
    raw_value = _clean_text(item.get("value"))
    if kind not in CONSTRAINT_KINDS or not raw_value:
        return None
    if _looks_like_style_or_quality_value(raw_value):
        return None
    subtype = _normalize_constraint_subtype(item.get("subtype"))
    importance = _coerce_importance(item.get("importance"), default=0)
    subtype, importance = _apply_role_hardcap(kind, raw_value, subtype, importance)
    return ReuseConstraint(
        kind=kind,
        value=raw_value,
        importance=importance,
        confidence=_coerce_confidence(item.get("confidence"), default=0.0),
        evidence=_clean_text(item.get("evidence")),
        reason=_clean_text(item.get("reason")),
        subtype=subtype,
    )


def _normalize_constraint_subtype(value: Any) -> str:
    text = _clean_text(value).casefold()
    if text in CONSTRAINT_SUBTYPES:
        return text
    return ""


def _apply_role_hardcap(
    kind: str,
    value: str,
    subtype: str,
    importance: int,
) -> tuple[str, int]:
    """Force role/generic_class subtype + importance<=1 for words in ROLE_HARDCAP_TERMS.

    The exception is when value clearly carries a full proper name (姓+名),
    in which case it stays as named_individual (e.g. '史铁生'). This protects
    against the LLM upgrading common roles like '妈妈' to imp=2.
    """

    if kind != "entity":
        return subtype, importance
    normalized = _clean_text(value)
    if normalized in ROLE_HARDCAP_TERMS:
        if subtype not in {"role", "generic_class"}:
            subtype = "role" if importance >= 1 else "generic_class"
        if importance > 1:
            importance = 1
    return subtype, importance


def _coerce_importance(value: Any, *, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(0, min(2, number))


def _coerce_confidence(value: Any, *, default: float) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = default
    return round(_clamp(score), 4)


def _normalize_constraint_kind(value: Any) -> str:
    return _clean_text(value)


def derive_reuse_level_from_constraints(
    constraints: list[dict[str, Any]],
    asset_category: str | None = None,
) -> str:
    """Derive reuse_level from asset_category + constraints.

    Strictness comes from two narrow signals only:
      * teaching content (text/math/physics imp=2)
      * named individual entity (subtype=named_individual/species_instance, imp=2)

    Generic decorative categories are forced to loose. The legacy
    'three strong constraints -> strict' rule is removed because it
    over-penalizes role/scene/emotion combinations that are not
    actually unsafe to reuse.
    """

    category = _clean_text(asset_category).casefold()
    if category in FORCED_LOOSE_CATEGORIES:
        return "loose"

    normalized = normalize_constraints(constraints)
    strong_constraints = [
        item for item in normalized if _constraint_importance(item) >= 2
    ]
    has_strict_knowledge_constraint = any(
        _clean_text(item.get("kind")) in STRICT_KNOWLEDGE_CONSTRAINT_KINDS
        for item in strong_constraints
    )
    has_named_individual = any(
        _clean_text(item.get("kind")) == "entity"
        and _clean_text(item.get("subtype")).casefold() in NAMED_INDIVIDUAL_SUBTYPES
        for item in strong_constraints
    )
    if has_strict_knowledge_constraint or has_named_individual:
        return "strict"
    if strong_constraints:
        return "medium"

    if any(_constraint_importance(item) >= 1 for item in normalized):
        return "medium"
    return "loose"


def _copy_constraint_dict(item: dict[str, Any]) -> dict[str, Any]:
    return dict(item)


def _constraint_importance(constraint: dict[str, Any]) -> int:
    return _coerce_importance(_dict_value(constraint, "importance"), default=0)


def _active_constraints(value: Any) -> list[dict[str, Any]]:
    return [
        item for item in normalize_constraints(value)
        if _constraint_importance(item) >= 1
    ]


def _strong_constraints(value: Any) -> list[dict[str, Any]]:
    return [
        item for item in normalize_constraints(value)
        if _constraint_importance(item) >= 2
    ]


def _dict_value(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def normalize_reuse_policy_fields(asset: dict[str, Any]) -> dict[str, Any]:
    """Return schema-valid simplified reuse metadata for an asset."""

    if _clean_text(asset.get("asset_kind")) == "background":
        return {
            "reuse_level": "loose",
            "asset_category": "unknown",
            "constraints": [],
            "generic_support_allowed": True,
        }

    metadata = normalize_asset_metadata(asset)
    constraints = metadata.constraints
    asset_category = _clean_text(asset.get("asset_category")) or metadata.asset_category
    if asset_category not in ASSET_CATEGORIES:
        asset_category = DEFAULT_POLICY["asset_category"]

    reuse_level = derive_reuse_level_from_constraints(constraints, asset_category)
    generic_support_allowed = reuse_level == "loose"

    return {
        "reuse_level": reuse_level,
        "asset_category": asset_category,
        "constraints": normalize_constraints(constraints),
        "generic_support_allowed": generic_support_allowed,
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

    policy = normalize_reuse_policy_fields(target)
    reuse_level = policy["reuse_level"]
    threshold = PAGE_IMAGE_REUSE_THRESHOLDS.get(reuse_level, PAGE_IMAGE_REUSE_THRESHOLDS["medium"])
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

    transform_policy = score_details.get("transform_policy") if isinstance(score_details.get("transform_policy"), dict) else {}
    if _clean_text(transform_policy.get("decision")) == "reject":
        return _result(
            "reject",
            "aspect_transform_rejected",
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
    target_level = target_policy["reuse_level"]
    target_constraints = _active_constraints(target_policy.get("constraints"))
    candidate_constraints = _active_constraints(candidate_policy.get("constraints"))
    target_strong_constraints = _strong_constraints(target_policy.get("constraints"))
    semantic_signal = _has_semantic_reuse_signal(score_details, score)
    embedding_score = _embedding_score_from_details(score_details)
    accepted_by = _clean_text(score_details.get("accepted_by"))

    def high_embedding_review_result(reason: str, threshold_value: float) -> dict[str, Any]:
        return _result(
            "llm_review",
            reason,
            review_items=[
                {
                    "decision": "llm_review",
                    "kind": "embedding",
                    "reason": reason,
                    "embedding_score": round(float(embedding_score), 4),
                    "threshold": round(float(threshold_value), 4),
                }
            ],
            confidence=0.5,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

    def embedding_floor_review_result(reason: str) -> dict[str, Any]:
        floor = _auto_accept_embedding_floor(target_policy, candidate_policy)
        return high_embedding_review_result(reason, floor)

    if accepted_by in SCORE_GATE_LLM_REVIEW_REASONS:
        return high_embedding_review_result(accepted_by, threshold_used)

    if target_level != "strict":
        medium_missing, medium_conflicts = compare_constraints(
            target_constraints,
            candidate_constraints,
            strict_target=False,
        )
        if medium_conflicts:
            _missing, unresolved_conflicts, conflict_reviews = compare_strong_constraints(
                target_strong_constraints,
                candidate_constraints,
                score_details=score_details,
            )
            actionable_reviews = [
                item for item in conflict_reviews
                if _clean_text(item.get("reason")) != "missing_constraint_embedding"
            ]
            if actionable_reviews:
                return _result(
                    "llm_review",
                    "medium_constraints_require_llm_review",
                    review_items=actionable_reviews,
                    confidence=0.55,
                    threshold=threshold_used,
                    score_gap=score_gap,
                    target_policy=target_policy,
                    candidate_policy=candidate_policy,
                )
            if unresolved_conflicts:
                return _result(
                    "reject",
                    "medium_constraints_conflict",
                    conflicts=unresolved_conflicts,
                    confidence=0.95,
                    threshold=threshold_used,
                    score_gap=score_gap,
                    target_policy=target_policy,
                    candidate_policy=candidate_policy,
                )
            if conflict_reviews:
                return _result(
                    "reject",
                    "medium_constraints_conflict",
                    conflicts=medium_conflicts,
                    confidence=0.95,
                    threshold=threshold_used,
                    score_gap=score_gap,
                    target_policy=target_policy,
                    candidate_policy=candidate_policy,
                )
            if not target_strong_constraints:
                return _result(
                    "reject",
                    "medium_constraints_conflict",
                    conflicts=medium_conflicts,
                    confidence=0.85,
                    threshold=threshold_used,
                    score_gap=score_gap,
                    target_policy=target_policy,
                    candidate_policy=candidate_policy,
                )
        medium_strong_missing = [item for item in medium_missing if _is_specific_constraint(item)]
        if medium_missing and not (semantic_signal and not medium_strong_missing):
            return _result(
                "llm_review",
                "medium_constraints_require_llm_review",
                review_items=[
                    _constraint_review_item(item, [], "missing_same_kind", side="target")
                    for item in medium_missing
                ],
                confidence=0.55,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if (
            score_gap < 0
            and embedding_score >= MEDIUM_EMBEDDING_REVIEW_THRESHOLD
            and (accepted_by in {"medium_embedding_review", "embedding_high_review"} or score <= 0)
        ):
            return high_embedding_review_result(
                "medium_embedding_high_keyword_below_threshold",
                MEDIUM_EMBEDDING_REVIEW_THRESHOLD,
            )
        if score_gap >= 0 or semantic_signal:
            if score_gap >= 0 and _requires_embedding_floor_review(target_policy, candidate_policy, embedding_score):
                return embedding_floor_review_result("embedding_below_auto_accept_floor")
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

    if target_level == "strict":
        if score_gap < 0 and not semantic_signal:
            if accepted_by == "strict_semantic_gray_review":
                return high_embedding_review_result(
                    "strict_semantic_gray_review",
                    STRICT_SEMANTIC_GRAY_REVIEW_THRESHOLD,
                )
            if embedding_score >= STRICT_EMBEDDING_REVIEW_THRESHOLD:
                return high_embedding_review_result(
                    "strict_embedding_high_keyword_below_threshold",
                    STRICT_EMBEDDING_REVIEW_THRESHOLD,
                )
            return _result(
                "reject",
                "strict_similarity_below_threshold",
                confidence=0.9,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if not target_constraints and not candidate_constraints:
            if score_gap >= 0 and _requires_embedding_floor_review(target_policy, candidate_policy, embedding_score):
                return embedding_floor_review_result("embedding_below_auto_accept_floor")
            return _result(
                "full_match",
                "strict_unconstrained_similarity_match",
                confidence=0.75 if score_gap >= 0 else 0.65,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        missing, conflicts, reviews = compare_strong_constraints(
            target_strong_constraints,
            candidate_constraints,
            score_details=score_details,
        )
        strict_missing: list[dict[str, Any]] = list(missing)
        strict_conflicts: list[dict[str, Any]] = _dedupe_conflicts(conflicts)
        strict_reviews: list[dict[str, Any]] = _dedupe_review_items(reviews)
        if strict_conflicts:
            return _result(
                "reject",
                "strict_constraints_conflict",
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
                "strict_constraints_require_llm_review",
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
                "strict_constraints_missing",
                missing=strict_missing,
                confidence=0.9,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if score_gap >= 0 and _requires_embedding_floor_review(target_policy, candidate_policy, embedding_score):
            return embedding_floor_review_result("embedding_below_auto_accept_floor")
        return _result(
            "full_match",
            "strict_constraints_covered",
            confidence=0.9,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

    return _result(
        "full_match" if score_gap >= 0 or semantic_signal else "reject",
        "constraint_similarity_match" if score_gap >= 0 or semantic_signal else "similarity_below_threshold",
        confidence=0.75 if score_gap >= 0 or semantic_signal else 0.85,
        threshold=threshold_used,
        score_gap=score_gap,
        target_policy=target_policy,
        candidate_policy=candidate_policy,
    )


def evaluate_aspect_transform(target: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Choose a safe transform mode and score penalty for aspect-ratio mismatch."""

    candidate_label = _clean_text(candidate.get("aspect_ratio"))
    target_label = _clean_text(target.get("aspect_ratio"))
    candidate_ratio = _parse_aspect_ratio(candidate_label)
    target_ratio = _parse_aspect_ratio(target_label)
    if candidate_ratio <= 0 or target_ratio <= 0:
        return {
            "decision": "accept",
            "mode": "copy",
            "crop_loss": 0.0,
            "transform_penalty": 0.0,
            "candidate_aspect_ratio": candidate_label,
            "target_aspect_ratio": target_label,
            "reason": "missing_or_invalid_aspect_ratio",
        }

    loss = _crop_loss(candidate_ratio, target_ratio)
    reversed_orientation = _orientation(candidate_ratio) != _orientation(target_ratio) and "square" not in {
        _orientation(candidate_ratio),
        _orientation(target_ratio),
    }
    target_policy = normalize_reuse_policy_fields(target)
    reuse_level = target_policy["reuse_level"]
    role = _clean_text(target.get("role") or candidate.get("role"))
    has_constraints = bool(_active_constraints(target_policy.get("constraints")))
    asset_kind = _clean_text(target.get("asset_kind"))

    if loss <= 0.02:
        return _transform_result(
            "accept",
            "copy",
            loss,
            0.0,
            candidate_label,
            target_label,
            "aspect_ratio_aligned",
        )

    if asset_kind == "background":
        if loss <= 0.05:
            return _transform_result("accept", "micro_stretch", loss, 0.01, candidate_label, target_label, "background_micro_stretch")
        if loss <= 0.12:
            return _transform_result("penalize", "cover_crop", loss, 0.02, candidate_label, target_label, "background_light_crop")
        if loss <= 0.25:
            return _transform_result("penalize", "blur_pad", loss, 0.06, candidate_label, target_label, "background_blur_pad")
        if loss <= 0.35 and not reversed_orientation:
            return _transform_result("penalize", "blur_pad", loss, 0.10, candidate_label, target_label, "background_high_pad")
        return _transform_result("reject", "copy", loss, 0.18, candidate_label, target_label, "background_aspect_mismatch_too_large")

    if role == "hero" and loss > 0.25:
        return _transform_result("reject", "copy", loss, 0.18, candidate_label, target_label, "hero_aspect_mismatch_too_large")
    if role == "hero" and loss > 0.12:
        return _transform_result("penalize", "contain_pad", loss, 0.10, candidate_label, target_label, "hero_content_preserving_pad")

    if role == "icon":
        if loss <= 0.12:
            return _transform_result("penalize", "contain_pad", loss, 0.04, candidate_label, target_label, "icon_content_preserving_pad")
        if loss <= 0.25 and not reversed_orientation:
            return _transform_result("penalize", "contain_pad", loss, 0.09, candidate_label, target_label, "icon_medium_pad")
        return _transform_result("reject", "copy", loss, 0.18, candidate_label, target_label, "icon_aspect_mismatch_too_large")

    if reuse_level == "strict" or has_constraints:
        if loss <= 0.05:
            return _transform_result("accept", "copy", loss, 0.0, candidate_label, target_label, "strict_small_mismatch")
        if loss <= 0.12 and not reversed_orientation:
            return _transform_result("penalize", "contain_pad", loss, 0.05, candidate_label, target_label, "strict_content_preserving_pad")
        if loss <= 0.25 and not reversed_orientation:
            return _transform_result("penalize", "contain_pad", loss, 0.10, candidate_label, target_label, "strict_content_preserving_medium_pad")
        return _transform_result("reject", "copy", loss, 0.18, candidate_label, target_label, "strict_aspect_mismatch_too_large")

    if loss <= 0.05:
        return _transform_result("accept", "copy", loss, 0.0, candidate_label, target_label, "unknown_small_mismatch")
    if loss <= 0.12:
        return _transform_result("penalize", "contain_pad", loss, 0.05, candidate_label, target_label, "unknown_light_pad")
    if loss <= 0.25 and not reversed_orientation:
        return _transform_result("penalize", "contain_pad", loss, 0.10, candidate_label, target_label, "unknown_medium_pad")
    return _transform_result("reject", "copy", loss, 0.18, candidate_label, target_label, "unknown_aspect_mismatch_too_large")


def compare_constraints(
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


def compare_strong_constraints(
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
    light_method = _light_constraint_match_method(left, right)
    if light_method:
        return True
    if _constraint_importance(left) >= 2:
        return False
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

    threshold = _constraint_embedding_threshold(required)
    if embedding_score >= threshold:
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
    high, low = CONSTRAINT_EMBEDDING_THRESHOLDS.get(kind, (0.88, 0.76))
    if _constraint_importance(required) == 1 and embedding_score >= min(high, max(low, threshold - 0.04)):
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
    return ""


def _constraint_embedding_threshold(constraint: dict[str, Any]) -> float:
    kind = _clean_text(constraint.get("kind"))
    importance = _constraint_importance(constraint)
    if importance <= 0:
        return 0.0
    high, low = CONSTRAINT_EMBEDDING_THRESHOLDS.get(kind, (0.88, 0.76))
    if importance >= 2:
        return high
    return low


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
        if not isinstance(item, dict) or _normalize_constraint_kind(item.get("kind")) != kind:
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
        "importance": _constraint_importance(required),
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
        reason_rank = {"exact": 4, "contains": 3, "embedding_high": 2, "embedding_gray": 1}.get(reason, 0)
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


def _crop_loss(candidate_ratio: float, target_ratio: float) -> float:
    if candidate_ratio <= 0 or target_ratio <= 0:
        return 0.25
    return 1.0 - min(candidate_ratio, target_ratio) / max(candidate_ratio, target_ratio)


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
    candidate_aspect_ratio: str,
    target_aspect_ratio: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "mode": mode,
        "crop_loss": round(_clamp(crop_loss), 4),
        "transform_penalty": round(_clamp(penalty), 4),
        "candidate_aspect_ratio": candidate_aspect_ratio,
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
    if kind in {"math", "physics", "text"}:
        text = re.sub(r"\s+", "", text)
    else:
        text = re.sub(r"\s+", " ", text)
    return text.strip(" ,;:()[]{}<>")


def _is_specific_constraint(constraint: dict[str, Any]) -> bool:
    return _constraint_importance(constraint) >= 2


def _embedding_score_from_details(score_details: dict[str, Any]) -> float:
    try:
        return float(score_details.get("embedding_score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _auto_accept_embedding_floor(target_policy: dict[str, Any], candidate_policy: dict[str, Any]) -> float:
    if target_policy.get("reuse_level") == "strict" or candidate_policy.get("reuse_level") == "strict":
        return float(AUTO_ACCEPT_EMBEDDING_FLOORS["strict"])
    return 0.0


def _requires_embedding_floor_review(
    target_policy: dict[str, Any],
    candidate_policy: dict[str, Any],
    embedding_score: float,
) -> bool:
    levels = {target_policy.get("reuse_level"), candidate_policy.get("reuse_level")}
    if not ({"loose", "medium", "strict"} & levels):
        return False
    floor = _auto_accept_embedding_floor(target_policy, candidate_policy)
    return floor > 0 and embedding_score < floor


def _has_semantic_reuse_signal(score_details: dict[str, Any], score: float) -> bool:
    accepted_by = _clean_text(score_details.get("accepted_by"))
    if accepted_by in SEMANTIC_SIGNAL_ACCEPT_REASONS:
        return True
    embedding_score = _embedding_score_from_details(score_details)
    try:
        substring_score = float(score_details.get("substring_score") or 0.0)
    except (TypeError, ValueError):
        substring_score = 0.0
    return embedding_score >= SEMANTIC_EMBEDDING_ACCEPT_THRESHOLD and (
        score >= SEMANTIC_SCORE_FLOOR or substring_score > 0.0
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
        "插画",
        "教学插画",
        "教学配图",
        "配图",
        "课堂导入",
        "适合课堂导入",
        "页面功能",
        "高清",
        "高质量",
        "画风",
        "风格",
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


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clamp(value: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))
