"""Reuse-policy tests for the Plan-A material-category flow.

The current policy derives reuse behavior from ``strict_reuse_group`` and score
details. Legacy fields such as ``constraints`` / ``core_keywords`` are accepted
as inert input only; they must not drive policy decisions.
"""

from edupptx.materials.reuse_policy import (
    BACKGROUND_REUSE_THRESHOLD,
    CLUSTER_MAX,
    MATERIAL_CATEGORY_REUSE_LEVEL,
    PAGE_IMAGE_REUSE_THRESHOLDS,
    T_GAP,
    T_HIGH,
    T_LOW,
    decide_reuse,
    evaluate_reuse_filter,
    normalize_asset_metadata,
    normalize_reuse_policy_fields,
    reuse_threshold_for_target,
)


EXPECTED_CATEGORY_LEVELS = {
    "C00_strict_text_problem_skip": "skip",
    "C01_language_glyph_visual": "strict",
    "C02_structure_diagram_visual": "strict",
    "C03_irreplaceable_entity_event_action": "strict",
    "C04_generic_subject_object": "medium",
    "C05_scene_decor_container": "loose",
    "C03_specific_event_interaction": "strict",
    "C04_teaching_bound_entity": "strict",
    "C04_single_subject_asset": "medium",
    "C05_generic_subject_asset": "medium",
    "C05_decor_layout_container": "loose",
    "C06_scene_decor_container": "loose",
    "C06_generic_scene_activity": "loose",
}


def _page(group: str = "C04_generic_subject_object", **extra) -> dict:
    return {
        "asset_kind": "page_image",
        "strict_reuse_group": group,
        **extra,
    }


def _background(**extra) -> dict:
    return {
        "asset_kind": "background",
        **extra,
    }


def test_plan_a_material_category_tables_are_the_policy_source():
    assert MATERIAL_CATEGORY_REUSE_LEVEL == EXPECTED_CATEGORY_LEVELS
    assert len(MATERIAL_CATEGORY_REUSE_LEVEL) == 13  # 6 active + 7 legacy aliases


def test_normalize_policy_derives_from_strict_reuse_group_only():
    asset = _page(
        "C04_generic_subject_object",
        reuse_level="strict",
        generic_support_allowed=True,
    )

    policy = normalize_reuse_policy_fields(asset)

    assert policy == {
        "reuse_level": "medium",
        "generic_support_allowed": False,
    }


def test_loose_material_categories_enable_generic_support():
    for group in ("C05_scene_decor_container",):
        policy = normalize_reuse_policy_fields(_page(group))
        assert policy["reuse_level"] == "loose"
        assert policy["generic_support_allowed"] is True


def test_missing_or_unknown_group_defaults_to_medium_policy():
    for asset in (
        {"asset_kind": "page_image"},
        _page("not_a_known_group"),
    ):
        policy = normalize_reuse_policy_fields(asset)
        assert policy["reuse_level"] == "medium"
        assert policy["generic_support_allowed"] is False


def test_asset_metadata_derives_from_material_category():
    metadata = normalize_asset_metadata(
        _page("C01_language_glyph_visual")
    )

    assert metadata.reuse_level == "strict"
    assert metadata.generic_support_allowed is False
    result_dict = metadata.to_dict()
    assert "asset_category" not in result_dict
    assert "constraints" not in result_dict


def test_reuse_thresholds_follow_material_category_level():
    for group, level in EXPECTED_CATEGORY_LEVELS.items():
        if level == "skip":
            continue
        assert reuse_threshold_for_target(_page(group)) == PAGE_IMAGE_REUSE_THRESHOLDS[level]

    assert reuse_threshold_for_target(_background()) == BACKGROUND_REUSE_THRESHOLD


def test_explicit_reuse_threshold_is_clamped():
    target = _page("C04_generic_subject_object")

    assert reuse_threshold_for_target(target, explicit_threshold=0.42) == 0.42
    assert reuse_threshold_for_target(target, explicit_threshold=-2) == 0.0
    assert reuse_threshold_for_target(target, explicit_threshold=2) == 1.0


def test_c00_target_is_rejected_before_similarity():
    result = evaluate_reuse_filter(
        _page("C00_strict_text_problem_skip"),
        _page("C04_generic_subject_object"),
        {"keyword_score": 1.0, "embedding_score": 1.0, "accepted_by": "bm25_threshold"},
    )

    assert result["decision"] == "reject"
    assert result["reason"] == "material_category_skip"
    assert result["threshold_used"] == 0.0


def test_c00_candidate_is_rejected_before_similarity():
    result = evaluate_reuse_filter(
        _page("C04_generic_subject_object"),
        _page("C00_strict_text_problem_skip"),
        {"keyword_score": 1.0, "embedding_score": 1.0, "accepted_by": "bm25_threshold"},
    )

    assert result["decision"] == "reject"
    assert result["reason"] == "candidate_material_category_skip"
    assert result["threshold_used"] == 0.0


def test_asset_kind_mismatch_rejects():
    result = evaluate_reuse_filter(
        _page("C04_generic_subject_object"),
        _background(),
        {"keyword_score": 1.0},
    )

    assert result["decision"] == "reject"
    assert result["reason"] == "asset_kind_mismatch"


def test_background_cross_theme_uses_base_threshold():
    target = _background(theme="topic-a")
    candidate = _background(theme="topic-b")

    reject = evaluate_reuse_filter(target, candidate, {"keyword_score": BACKGROUND_REUSE_THRESHOLD - 0.01})
    accept = evaluate_reuse_filter(target, candidate, {"keyword_score": BACKGROUND_REUSE_THRESHOLD})

    assert reject["decision"] == "reject"
    assert reject["reason"] == "background_score_below_threshold"
    assert reject["threshold_used"] == BACKGROUND_REUSE_THRESHOLD
    assert accept["decision"] == "full_match"
    assert accept["reason"] == "background_score_above_threshold"
    assert accept["threshold_used"] == BACKGROUND_REUSE_THRESHOLD


# --- Three-tier decide_reuse tests ---


def test_three_tier_constants():
    assert T_HIGH == 0.70
    assert T_LOW == 0.35
    assert T_GAP == 0.05
    assert CLUSTER_MAX == 3


def test_decide_reuse_no_candidates():
    result = decide_reuse([])
    assert result["decision"] == "no_match"


def test_decide_reuse_high_score_direct():
    candidates = [{"hybrid_score": 0.75, "asset_id": "a1"}]
    result = decide_reuse(candidates)
    assert result["decision"] == "direct_reuse"
    assert result["asset_id"] == "a1"


def test_decide_reuse_low_score_reject():
    candidates = [{"hybrid_score": 0.30, "asset_id": "a1"}]
    result = decide_reuse(candidates)
    assert result["decision"] == "no_match"


def test_decide_reuse_mid_score_single_leader():
    candidates = [
        {"hybrid_score": 0.55, "asset_id": "a1"},
        {"hybrid_score": 0.40, "asset_id": "a2"},
    ]
    result = decide_reuse(candidates)
    assert result["decision"] == "direct_reuse"
    assert result["asset_id"] == "a1"


def test_decide_reuse_mid_score_cluster_triggers_llm():
    candidates = [
        {"hybrid_score": 0.55, "asset_id": "a1"},
        {"hybrid_score": 0.52, "asset_id": "a2"},
        {"hybrid_score": 0.51, "asset_id": "a3"},
    ]
    result = decide_reuse(candidates)
    assert result["decision"] == "llm_review"
    assert len(result["cluster"]) == 3


def test_decide_reuse_cluster_capped_at_cluster_max():
    candidates = [
        {"hybrid_score": 0.55, "asset_id": f"a{i}"}
        for i in range(5)
    ]
    result = decide_reuse(candidates)
    assert result["decision"] == "llm_review"
    assert len(result["cluster"]) == CLUSTER_MAX
