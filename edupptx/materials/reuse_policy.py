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


def evaluate_aspect_transform(target: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Choose a safe transform mode and score penalty for aspect-ratio mismatch.

    Candidate-side ``padding_capacity`` (``"high" | "mid" | "low"``) is a
    pixel-derived property of the candidate image's edges. It is set at the
    earliest moment the image lands on disk (annotation / registration) and
    is independent of any VLM call. The value modulates pad-zone loss
    thresholds, the preferred pad mode, and the penalty:

      * high (transparent edges): pad is invisible → widen thresholds, prefer
        contain_pad, drop penalty.
      * mid (near-white edges): pad blends → keep thresholds, mild penalty cut.
      * low (colored edges): pad shows a hard seam → tighten thresholds, fall
        back to cover_crop instead of contain_pad/blur_pad.
      * missing / unknown: legacy behavior (factor=1.0, mode unchanged).

    The accept/copy boundary (loss <= 0.05) is **not** widened — that bar
    encodes "PowerPoint can stretch this much without visible distortion",
    which is a property of the slot not the image. Backgrounds skip
    modulation entirely (they are full-bleed by design — edges are content).
    """

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
    asset_kind = _clean_text(target.get("asset_kind"))

    capacity = "" if asset_kind == "background" else _candidate_padding_capacity(candidate)
    loss_factor = _CAPACITY_LOSS_FACTOR.get(capacity, 1.0)
    penalty_factor = _CAPACITY_PENALTY_FACTOR.get(capacity, 1.0)

    if loss <= 0.02:
        return _transform_result(
            "accept",
            "copy",
            loss,
            0.0,
            candidate_label,
            target_label,
            "aspect_ratio_aligned",
            padding_capacity=capacity,
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

    if role == "hero" and loss > 0.25 * loss_factor:
        return _transform_result("reject", "copy", loss, 0.18, candidate_label, target_label, "hero_aspect_mismatch_too_large", padding_capacity=capacity)
    if role == "hero" and loss > 0.12 * loss_factor:
        return _transform_result(
            "penalize",
            _preferred_pad_mode("contain_pad", capacity),
            loss,
            0.10 * penalty_factor,
            candidate_label,
            target_label,
            "hero_content_preserving_pad",
            padding_capacity=capacity,
        )

    if role == "icon":
        if loss <= 0.12 * loss_factor:
            return _transform_result(
                "penalize",
                _preferred_pad_mode("contain_pad", capacity),
                loss,
                0.04 * penalty_factor,
                candidate_label,
                target_label,
                "icon_content_preserving_pad",
                padding_capacity=capacity,
            )
        if loss <= 0.25 * loss_factor and not reversed_orientation:
            return _transform_result(
                "penalize",
                _preferred_pad_mode("contain_pad", capacity),
                loss,
                0.09 * penalty_factor,
                candidate_label,
                target_label,
                "icon_medium_pad",
                padding_capacity=capacity,
            )
        return _transform_result("reject", "copy", loss, 0.18, candidate_label, target_label, "icon_aspect_mismatch_too_large", padding_capacity=capacity)

    if reuse_level == "strict":
        if loss <= 0.05:
            return _transform_result("accept", "copy", loss, 0.0, candidate_label, target_label, "strict_small_mismatch", padding_capacity=capacity)
        if loss <= 0.12 * loss_factor and not reversed_orientation:
            return _transform_result(
                "penalize",
                _preferred_pad_mode("contain_pad", capacity),
                loss,
                0.05 * penalty_factor,
                candidate_label,
                target_label,
                "strict_content_preserving_pad",
                padding_capacity=capacity,
            )
        if loss <= 0.25 * loss_factor and not reversed_orientation:
            return _transform_result(
                "penalize",
                _preferred_pad_mode("contain_pad", capacity),
                loss,
                0.10 * penalty_factor,
                candidate_label,
                target_label,
                "strict_content_preserving_medium_pad",
                padding_capacity=capacity,
            )
        # Letterbox recovery: when the candidate has no visible text/math
        # annotation that could be cropped *and* its edges blend cleanly,
        # padding bars are acceptable even with reversed orientation. The
        # check is structural (pixel-derived padding_capacity) — no subject
        # vocabulary is involved.
        if _letterbox_safe(candidate=candidate, padding_capacity=capacity, loss=loss):
            return _transform_result(
                "penalize",
                _preferred_pad_mode("contain_pad", capacity),
                loss,
                0.14 * penalty_factor,
                candidate_label,
                target_label,
                "strict_letterbox_recovery",
                padding_capacity=capacity,
            )
        return _transform_result("reject", "copy", loss, 0.18, candidate_label, target_label, "strict_aspect_mismatch_too_large", padding_capacity=capacity)

    if loss <= 0.05:
        return _transform_result("accept", "copy", loss, 0.0, candidate_label, target_label, "unknown_small_mismatch", padding_capacity=capacity)
    if loss <= 0.12 * loss_factor:
        return _transform_result(
            "penalize",
            _preferred_pad_mode("contain_pad", capacity),
            loss,
            0.05 * penalty_factor,
            candidate_label,
            target_label,
            "unknown_light_pad",
            padding_capacity=capacity,
        )
    if loss <= 0.25 * loss_factor and not reversed_orientation:
        return _transform_result(
            "penalize",
            _preferred_pad_mode("contain_pad", capacity),
            loss,
            0.10 * penalty_factor,
            candidate_label,
            target_label,
            "unknown_medium_pad",
            padding_capacity=capacity,
        )
    # Letterbox recovery: applies the same structural safety check as the
    # strict path. This is the path that catches portrait→landscape and
    # landscape→portrait conversions for generic scene/activity assets whose pixel
    # edges are clean enough to pad.
    if _letterbox_safe(candidate=candidate, padding_capacity=capacity, loss=loss):
        return _transform_result(
            "penalize",
            _preferred_pad_mode("contain_pad", capacity),
            loss,
            0.12 * penalty_factor,
            candidate_label,
            target_label,
            "unknown_letterbox_recovery",
            padding_capacity=capacity,
        )
    return _transform_result("reject", "copy", loss, 0.18, candidate_label, target_label, "unknown_aspect_mismatch_too_large", padding_capacity=capacity)


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


# Capacity factors for loss-threshold widening / penalty scaling. Applied in
# evaluate_aspect_transform. Missing capacity ("" — assets registered before
# the pixel snapshot, or images where edge analysis returned nothing) keeps
# factor 1.0, so any call site that omits the field produces identical
# behavior to the pre-capacity rules.
#
# Numbers chosen so:
#   * high capacity widens the medium-pad ceiling from 0.25 to ~0.325, which
#     covers the 4:3 → 16:9 / 1:1 → 4:3 conversions that today get rejected
#     even when edges are transparent.
#   * low capacity tightens the same ceiling to ~0.175, so an image with a
#     colored painted-in border is no longer padded into an obvious seam.
_CAPACITY_LOSS_FACTOR = {"high": 1.3, "mid": 1.0, "low": 0.7, "": 1.0}
_CAPACITY_PENALTY_FACTOR = {"high": 0.5, "mid": 0.8, "low": 1.0, "": 1.0}


def _candidate_padding_capacity(candidate: dict[str, Any]) -> str:
    """Return candidate's pixel-derived padding capacity, or "" if absent.

    Only the candidate side is considered — the transform happens to the
    candidate image, so target-side metadata is irrelevant to whether the
    pad will look clean.

    Canonical shape: top-level ``candidate["padding_capacity"]`` is the string
    ``"high"``/``"mid"``/``"low"``. The legacy nested ``transform_advice``
    dict is still accepted as a read-only fallback for any library JSON
    that hasn't yet been migrated.
    """

    if not isinstance(candidate, dict):
        return ""
    raw = candidate.get("padding_capacity")
    if not raw:
        advice = candidate.get("transform_advice")
        if isinstance(advice, dict):
            raw = advice.get("padding_capacity")
    value = _clean_text(raw).casefold()
    if value in {"high", "mid", "low"}:
        return value
    return ""


def _preferred_pad_mode(default_mode: str, capacity: str) -> str:
    """Override pad-mode selection for low-capacity candidates.

    ``contain_pad`` and ``blur_pad`` both rely on the image's edge color
    blending with the synthesized pad fill. When ``padding_capacity`` is
    ``low`` (colored edges), the seam shows; prefer ``cover_crop`` instead —
    sacrificing composition is less ugly than a hard pad line.
    """

    if capacity != "low":
        return default_mode
    if default_mode in {"contain_pad", "blur_pad"}:
        return "cover_crop"
    return default_mode


# Structural cap on letterbox recovery: only kick in below this loss to
# avoid producing tiny content surrounded by huge bars. 0.45 corresponds
# roughly to 16:9 → 1:1 conversion, which is the worst typical case where
# the picture is still readable after letterboxing.
_LETTERBOX_LOSS_CEILING = 0.45


def _letterbox_safe(
    *,
    candidate: dict[str, Any],
    padding_capacity: str,
    loss: float,
) -> bool:
    """Return True when reversed-orientation letterbox is structurally safe.

    Letterbox (``contain_pad``) adds bars *around* the image rather than
    cropping it, so the image's content — including any text / math /
    physics annotations — is always preserved. The only risk is whether
    the bars look acceptable, which is determined entirely by the
    candidate's pixel-derived ``padding_capacity``:

    * ``high`` (transparent edges): pad blends invisibly → safe.
    * ``mid``  (near-white edges):  pad blends well → safe.
    * ``low``  (colored edges):     pad seam shows → never letterbox.
    * ``""``   (unknown):           treat as ``mid``.

    The previous constraint-kind check has been removed: it was over-
    restrictive (letterbox doesn't crop), and the capacity gate already
    captures every real risk.
    """

    if loss > _LETTERBOX_LOSS_CEILING:
        return False
    capacity = padding_capacity or "mid"
    return capacity != "low"


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


def _transform_result(
    decision: str,
    mode: str,
    crop_loss: float,
    penalty: float,
    candidate_aspect_ratio: str,
    target_aspect_ratio: str,
    reason: str,
    *,
    padding_capacity: str = "",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "decision": decision,
        "mode": mode,
        "crop_loss": round(_clamp(crop_loss), 4),
        "transform_penalty": round(_clamp(penalty), 4),
        "candidate_aspect_ratio": candidate_aspect_ratio,
        "target_aspect_ratio": target_aspect_ratio,
        "reason": reason,
    }
    if padding_capacity:
        result["padding_capacity"] = padding_capacity
    return result


# (Argument-coercion helper used by the policy module.)
def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_value(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clamp(value: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))
