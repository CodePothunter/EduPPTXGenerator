"""Tests for the new multi-subject coverage gate and conditional background threshold."""

from edupptx.materials.reuse_policy import (
    BACKGROUND_REUSE_THRESHOLD,
    BACKGROUND_SAME_THEME_HIGH_EMBEDDING_THRESHOLD,
    BACKGROUND_SAME_THEME_THRESHOLD,
    evaluate_reuse_filter,
    subject_coverage_undercoverage,
)


def _entity(value: str, importance: int = 1, subtype: str = "generic_class") -> dict:
    return {
        "kind": "entity",
        "subtype": subtype,
        "value": value,
        "importance": importance,
        "confidence": 0.9,
        "evidence": "",
        "reason": "",
    }


def test_subject_coverage_helper_flags_undercovered_group():
    target = [_entity(name, importance=1) for name in ("猴子", "兔子", "松鼠", "公鸡", "鸭子", "孔雀")]
    candidate = [_entity("兔子", importance=1), _entity("松鼠", importance=1)]
    undercovered = subject_coverage_undercoverage(target, candidate)
    assert len(undercovered) == 1
    group = undercovered[0]
    assert group["kind"] == "entity"
    assert group["matched_count"] == 2
    assert group["required_count"] == 3
    assert set(group["missing_values"]) == {"猴子", "公鸡", "鸭子", "孔雀"}


def test_subject_coverage_helper_passes_when_half_or_more_covered():
    target = [_entity(name, importance=1) for name in ("猴子", "兔子", "松鼠", "公鸡")]
    candidate = [_entity("猴子", importance=1), _entity("兔子", importance=1), _entity("松鼠", importance=1)]
    undercovered = subject_coverage_undercoverage(target, candidate)
    assert undercovered == []


def test_subject_coverage_helper_ignores_imp0_constraints():
    target = [_entity(name, importance=0) for name in ("猴子", "兔子", "松鼠")]
    candidate = []
    assert subject_coverage_undercoverage(target, candidate) == []


def test_subject_coverage_helper_ignores_single_entity_groups():
    target = [_entity("孔雀", importance=1)]
    candidate = []
    assert subject_coverage_undercoverage(target, candidate) == []


def test_evaluate_reuse_filter_rejects_undercovered_multi_subject_target():
    target = {
        "asset_kind": "page_image",
        "asset_category": "content_specific",
        "theme": "一年级语文《比尾巴》课文教学",
        "constraints": [_entity(name, importance=1) for name in ("猴子", "兔子", "松鼠", "公鸡", "鸭子", "孔雀")],
    }
    candidate = {
        "asset_kind": "page_image",
        "asset_category": "character_action",
        "theme": "一年级语文《比尾巴》课文教学",
        "constraints": [_entity("兔子", importance=1)],
    }
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.8, "embedding_score": 0.7},
        threshold=0.5,
    )
    assert result["decision"] == "reject"
    assert result["reason"] == "subject_coverage_undercoverage"
    conflict = result["conflicts"][0]
    assert conflict["matched_count"] == 1
    assert conflict["required_count"] == 3
    assert set(conflict["missing_values"]) == {"猴子", "松鼠", "公鸡", "鸭子", "孔雀"}


def test_evaluate_reuse_filter_keeps_llm_review_when_coverage_at_threshold():
    target = {
        "asset_kind": "page_image",
        "asset_category": "content_specific",
        "theme": "t",
        "constraints": [_entity(name, importance=1) for name in ("猴子", "兔子", "松鼠", "公鸡")],
    }
    candidate = {
        "asset_kind": "page_image",
        "asset_category": "character_action",
        "theme": "t",
        "constraints": [_entity("猴子", importance=1), _entity("兔子", importance=1)],
    }
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.8, "embedding_score": 0.7},
        threshold=0.5,
    )
    # Coverage = 2/4 ≥ ceil(4/2)=2, undercoverage gate does not trigger.
    # Existing per-constraint missing path still produces LLM review.
    assert result["decision"] == "llm_review"
    assert result["reason"] != "subject_coverage_undercoverage"


def test_background_same_theme_lowers_threshold_when_embedding_high():
    target = {
        "asset_kind": "background",
        "theme": "一年级语文《比尾巴》课文教学",
        "constraints": [],
    }
    candidate = {
        "asset_kind": "background",
        "theme": "一年级语文《比尾巴》课文教学",
        "constraints": [],
    }
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.31, "embedding_score": 0.75},
    )
    assert result["decision"] == "full_match"
    assert result["threshold_used"] <= BACKGROUND_SAME_THEME_HIGH_EMBEDDING_THRESHOLD + 1e-6
    assert result["reason"] == "background_score_above_threshold"


def test_background_same_theme_lowers_threshold_softly_when_embedding_low():
    target = {"asset_kind": "background", "theme": "t", "constraints": []}
    candidate = {"asset_kind": "background", "theme": "t", "constraints": []}
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.35, "embedding_score": 0.40},
    )
    assert result["decision"] == "full_match"
    assert result["threshold_used"] <= BACKGROUND_SAME_THEME_THRESHOLD + 1e-6
    assert result["threshold_used"] > BACKGROUND_SAME_THEME_HIGH_EMBEDDING_THRESHOLD


def test_background_cross_theme_keeps_strict_threshold():
    target = {"asset_kind": "background", "theme": "一年级语文《比尾巴》", "constraints": []}
    candidate = {"asset_kind": "background", "theme": "《秋天的雨》", "constraints": []}
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.35, "embedding_score": 0.75},
    )
    assert result["decision"] == "reject"
    assert abs(result["threshold_used"] - BACKGROUND_REUSE_THRESHOLD) < 1e-6
