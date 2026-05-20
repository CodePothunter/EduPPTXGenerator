from edupptx.materials.ai_image_asset_db import _reuse_acceptance_reason
from edupptx.materials.reuse_policy import (
    MEDIUM_EMBEDDING_REVIEW_THRESHOLD,
    STRICT_EMBEDDING_REVIEW_THRESHOLD,
    candidate_extra_strong_constraints,
    compute_keyword_df_ratio,
    derive_reuse_level_from_constraints,
    evaluate_aspect_transform,
    evaluate_reuse_filter,
    extra_teaching_content_constraints,
    has_precision_signal,
    normalize_constraints,
    normalize_asset_metadata,
    normalize_reuse_policy_fields,
    reuse_threshold_for_target,
    subject_coverage_undercoverage,
)


def test_normalize_constraints_removes_legacy_constraint_fields():
    constraints = normalize_constraints(
        [
            {
                "kind": "entity",
                "value": "小蝌蚪",
                "importance": 2,
                "source": "visible",
                "match_mode": "exact",
                "filter_threshold": "high",
                "aliases": ["蝌蚪"],
                "confidence": 0.9,
                "evidence": "主体要求",
                "reason": "核心主体",
            }
        ]
    )

    assert constraints == [
        {
            "kind": "entity",
            "subtype": "",
            "value": "小蝌蚪",
            "importance": 2,
            "confidence": 0.9,
            "evidence": "主体要求",
            "reason": "核心主体",
        }
    ]


def test_normalize_constraints_drops_legacy_fields_and_defaults_optional_values():
    constraints = normalize_constraints(
        [
            {
                "kind": "entity",
                "value": "小蝌蚪",
                "importance": 2,
                "source": "visible",
                "match_mode": "exact",
                "filter_threshold": "high",
                "aliases": ["蝌蚪"],
            }
        ]
    )

    assert constraints == [
        {
            "kind": "entity",
            "subtype": "",
            "value": "小蝌蚪",
            "importance": 2,
            "confidence": 0.0,
            "evidence": "",
            "reason": "",
        }
    ]


def test_missing_importance_defaults_to_zero_and_is_inactive():
    constraints = normalize_constraints([{"kind": "entity", "value": "小蝌蚪"}])

    assert constraints[0]["importance"] == 0
    assert derive_reuse_level_from_constraints(constraints) == "loose"


def test_current_constraint_kinds_are_normalized_and_drive_policy():
    constraints = normalize_constraints(
        [
            {"kind": "scene", "value": "池塘边", "importance": 1},
            {"kind": "math", "value": "三只", "importance": 2},
            {"kind": "scene", "value": "左右相邻", "importance": 1},
        ]
    )

    assert [item["kind"] for item in constraints] == ["scene", "math", "scene"]
    assert [item["value"] for item in constraints] == ["池塘边", "三只", "左右相邻"]
    assert derive_reuse_level_from_constraints(constraints) == "strict"


def test_reuse_level_derived_from_constraint_importance_counts():
    loose = normalize_reuse_policy_fields(
        {"asset_kind": "page_image", "constraints": [{"kind": "action", "value": "读书", "importance": 0}]}
    )
    weak = normalize_reuse_policy_fields(
        {"asset_kind": "page_image", "constraints": [{"kind": "action", "value": "挥手告别", "importance": 1}]}
    )
    one_strong = normalize_reuse_policy_fields(
        {"asset_kind": "page_image", "constraints": [{"kind": "entity", "value": "小蝌蚪", "importance": 2}]}
    )
    two_strong = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "constraints": [
                {"kind": "entity", "value": "小蝌蚪", "importance": 2},
                {"kind": "action", "value": "举旗子", "importance": 2},
            ],
        }
    )
    three_strong = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "constraints": [
                {"kind": "entity", "subtype": "species_instance", "value": "小蝌蚪", "importance": 2},
                {"kind": "action", "value": "举旗子", "importance": 2},
                {"kind": "object", "value": "红色小旗子", "importance": 2},
            ],
        }
    )

    assert loose["reuse_level"] == "loose"
    assert loose["generic_support_allowed"] is True
    assert weak["reuse_level"] == "medium"
    assert weak["generic_support_allowed"] is False
    assert one_strong["reuse_level"] == "medium"
    assert one_strong["generic_support_allowed"] is False
    assert two_strong["reuse_level"] == "medium"
    assert three_strong["reuse_level"] == "strict"
    assert three_strong["generic_support_allowed"] is False
    assert all("focus_dimensions" not in policy for policy in (loose, weak, one_strong, two_strong, three_strong))


def test_text_math_physics_importance_two_force_strict():
    for kind, value in (("text", "比"), ("math", "a²+b²=c²"), ("physics", "F=ma")):
        policy = normalize_reuse_policy_fields(
            {"asset_kind": "page_image", "constraints": [{"kind": kind, "value": value, "importance": 2}]}
        )
        assert policy["reuse_level"] == "strict"
        assert policy["generic_support_allowed"] is False
        assert "focus_dimensions" not in policy


def test_focus_dimensions_removed_from_policy_metadata_and_background():
    page_asset = {
        "asset_kind": "page_image",
        "focus_dimensions": ["entity"],
        "constraints": [{"kind": "entity", "value": "小蝌蚪", "importance": 2}],
    }
    background_asset = {
        "asset_kind": "background",
        "focus_dimensions": ["scene"],
        "constraints": [{"kind": "scene", "value": "教室", "importance": 2}],
    }

    page_policy = normalize_reuse_policy_fields(page_asset)
    page_metadata = normalize_asset_metadata(page_asset).to_dict()
    background_policy = normalize_reuse_policy_fields(background_asset)

    assert "focus_dimensions" not in page_policy
    assert "focus_dimensions" not in page_metadata
    assert background_policy["constraints"] == []
    assert background_policy["generic_support_allowed"] is True
    assert "focus_dimensions" not in background_policy


def test_reuse_scores_do_not_override_explicit_constraint_policy():
    asset = {
        "asset_kind": "page_image",
        "reuse_scores": {
            "strict_score": 0.99,
            "loose_score": 0.0,
            "generic_support_score": 0.1,
            "readable_knowledge_score": 0.92,
            "unique_referent_score": 0.2,
            "exact_relation_score": 0.1,
            "category_scores": {"content_specific": 0.95, "concept_scene": 0.2},
            "constraint_scores": [
                {
                    "kind": "text",
                    "value": "character: bi",
                    "importance_score": 0.99,
                    "exactness_score": 0.99,
                }
            ],
        },
    }

    policy = normalize_reuse_policy_fields(
        {
            **asset,
            "constraints": [{"kind": "action", "value": "read", "importance": 0}],
        }
    )

    assert policy["reuse_level"] == "loose"
    assert policy["generic_support_allowed"] is True
    assert policy["constraints"][0]["importance"] == 0


def test_normalize_asset_metadata_accepts_new_constraints_schema():
    metadata = normalize_asset_metadata(
        {
            "asset_kind": "page_image",
            "asset_category": "character_action",
            "constraints": [
                {
                    "kind": "entity",
                    "value": "小猴子",
                    "importance": 2,
                    "match_mode": "exact",
                    "filter_threshold": "low",
                    "confidence": 0.86,
                    "source": "visible",
                    "evidence": "画面主体是小猴子",
                    "reason": "特定角色需要过滤",
                }
            ],
        }
    )

    assert metadata.constraints[0]["kind"] == "entity"
    assert metadata.constraints[0]["importance"] == 2
    assert metadata.constraints[0]["confidence"] == 0.86
    assert "source" not in metadata.constraints[0]
    assert "match_mode" not in metadata.constraints[0]
    assert "filter_threshold" not in metadata.constraints[0]


def test_normalize_asset_metadata_ignores_removed_core_constraints_field():
    metadata = normalize_asset_metadata(
        {
            "asset_kind": "page_image",
            "core_constraints": [
                {"kind": "entity", "value": "小猴子", "exact": True},
                {"kind": "action", "value": "挥手告别", "exact": False},
            ],
        }
    )

    assert metadata.constraints == []


def test_normalize_asset_metadata_uses_constraints_when_removed_field_is_present():
    metadata = normalize_asset_metadata(
        {
            "asset_kind": "page_image",
            "constraints": [
                {"kind": "object", "value": "田字格", "importance": 2, "match_mode": "exact"}
            ],
            "core_constraints": [{"kind": "entity", "value": "旧主体", "exact": True}],
        }
    )
    policy = normalize_reuse_policy_fields(metadata.to_dict())

    assert [item["value"] for item in metadata.constraints] == ["田字格"]
    assert all(item["value"] != "旧主体" for item in policy["constraints"])


def test_normalize_asset_metadata_defaults_empty_constraints():
    metadata = normalize_asset_metadata({"asset_kind": "page_image"})

    assert metadata.constraints == []
    assert "focus_dimensions" not in metadata.to_dict()


def test_weak_constraints_filter_without_focus_dimensions():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "constraints": [
                {"kind": "action", "value": "瀛︿範", "importance": 1, "match_mode": "semantic"}
            ],
        }
    )

    assert policy["constraints"][0]["importance"] == 1
    assert policy["reuse_level"] == "medium"
    assert "focus_dimensions" not in policy


def test_focus_dimensions_are_removed_from_metadata():
    metadata = normalize_asset_metadata(
        {
            "asset_kind": "page_image",
            "constraints": [
                {"kind": "entity", "value": "小猴子", "importance": 2, "match_mode": "exact"},
                {"kind": "action", "value": "挥手", "importance": 1, "match_mode": "semantic"},
                {"kind": "emotion", "value": "开心", "importance": 0, "match_mode": "semantic"},
            ],
        }
    )

    assert "focus_dimensions" not in metadata.to_dict()


def test_reuse_policy_derives_loose_metadata_without_constraints():
    asset = {
        "asset_kind": "page_image",
        "reuse_scores": {
            "strict_score": 0.2,
            "loose_score": 0.1,
            "generic_support_score": 0.8,
            "readable_knowledge_score": 0.0,
            "unique_referent_score": 0.0,
            "exact_relation_score": 0.0,
            "category_scores": {"concept_scene": 0.78, "content_specific": 0.1},
            "constraint_scores": [
                {
                    "kind": "entity",
                    "value": "ordinary animal",
                    "importance_score": 0.4,
                    "exactness_score": 0.2,
                }
            ],
        },
    }

    policy = normalize_reuse_policy_fields(asset)

    assert policy["reuse_level"] == "loose"
    assert policy["asset_category"] == "unknown"
    assert policy["generic_support_allowed"] is True
    assert policy["constraints"] == []


def test_explicit_visual_constraints_drive_medium_filter():
    asset = {
        "asset_kind": "page_image",
        "asset_category": "character_action",
        "constraints": [{"kind": "entity", "value": "cartoon tadpole", "importance": 1}],
        "reuse_scores": {
            "strict_score": 0.35,
            "loose_score": 0.1,
            "generic_support_score": 0.2,
            "readable_knowledge_score": 0.0,
            "unique_referent_score": 0.0,
            "exact_relation_score": 0.0,
            "category_scores": {"character_action": 0.82},
            "constraint_scores": [
                {
                    "kind": "entity",
                    "value": "cartoon tadpole",
                    "importance_score": 0.9,
                    "exactness_score": 0.5,
                    "aliases": ["tadpole larva"],
                }
            ],
        },
    }

    policy = normalize_reuse_policy_fields(asset)

    assert policy["reuse_level"] == "medium"
    assert policy["asset_category"] == "character_action"
    assert policy["generic_support_allowed"] is False
    assert policy["constraints"][0]["kind"] == "entity"
    assert policy["constraints"][0]["importance"] == 1
    assert "filter_threshold" not in policy["constraints"][0]


def test_old_dimension_scores_do_not_drive_strict_policy():
    asset = {
        "asset_kind": "page_image",
        "constraints": [{"kind": "text", "value": "character: bi", "importance": 2}],
        "reuse_scores": {
            "dimension_scores": {
                "readable_text_score": 0.92,
                "teaching_object_importance_score": 0.88,
                "generic_support_score": 0.1,
            },
            "category_scores": {"generic_tool": 0.8},
            "constraint_scores": [
                {
                    "kind": "text",
                    "value": "character: bi",
                    "importance_score": 0.9,
                    "exactness_score": 0.95,
                }
            ],
        },
    }

    policy = normalize_reuse_policy_fields(asset)

    assert policy["reuse_level"] == "strict"
    assert policy["constraints"][0]["kind"] == "text"
    assert policy["constraints"][0]["importance"] == 2


def test_old_dimension_scores_do_not_create_or_override_constraints():
    asset = {
        "asset_kind": "page_image",
        "constraints": [
            {"kind": "entity", "value": "mother", "importance": 1},
            {"kind": "action", "value": "persuade", "importance": 1},
        ],
        "reuse_scores": {
            "dimension_scores": {
                "subject_importance_score": 0.85,
                "action_importance_score": 0.8,
                "emotion_importance_score": 0.5,
            },
            "category_scores": {"character_action": 0.82},
            "constraint_scores": [
                {"kind": "entity", "value": "mother", "importance_score": 0.85, "exactness_score": 0.5},
                {"kind": "action", "value": "persuade", "importance_score": 0.8, "exactness_score": 0.5},
            ],
        },
    }

    policy = normalize_reuse_policy_fields(asset)

    assert policy["reuse_level"] == "medium"
    assert {item["kind"] for item in policy["constraints"]} == {"entity", "action"}


def test_high_visual_specificity_without_factual_risk_stays_medium():
    asset = {
        "asset_kind": "page_image",
        "constraints": [
            {"kind": "entity", "value": "boy", "importance": 1},
            {"kind": "action", "value": "throws things", "importance": 1},
            {"kind": "emotion", "value": "angry", "importance": 1},
        ],
        "reuse_scores": {
            "dimension_scores": {
                "subject_importance_score": 1.0,
                "action_importance_score": 1.0,
                "emotion_importance_score": 1.0,
                "generic_support_score": 0.1,
            },
            "category_scores": {"character_action": 0.9},
            "constraint_scores": [
                {"kind": "entity", "value": "boy", "importance_score": 0.95, "exactness_score": 0.5},
                {"kind": "action", "value": "throws things", "importance_score": 0.95, "exactness_score": 0.5},
                {"kind": "emotion", "value": "angry", "importance_score": 0.95, "exactness_score": 0.5},
            ],
        },
    }

    policy = normalize_reuse_policy_fields(asset)

    assert policy["reuse_level"] == "medium"
    assert {item["kind"] for item in policy["constraints"]} == {"entity", "action", "emotion"}


def test_score_only_emotion_expression_stays_loose_without_explicit_constraints():
    asset = {
        "asset_kind": "page_image",
        "reuse_scores": {
            "dimension_scores": {
                "emotion_importance_score": 0.95,
                "generic_support_score": 0.65,
            },
            "category_scores": {"character_action": 0.7},
            "constraint_scores": [
                {"kind": "emotion", "value": "surprised joy", "importance_score": 0.95, "exactness_score": 0.5}
            ],
        },
    }

    policy = normalize_reuse_policy_fields(asset)

    assert policy["reuse_level"] == "loose"
    assert policy["constraints"] == []


def test_strict_high_embedding_candidate_enters_review_even_when_keyword_score_is_zero():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [{"kind": "text", "value": "character: bi", "importance": 2, "match_mode": "exact"}],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "constraints": [{"kind": "text", "value": "character: bi", "importance": 2, "match_mode": "exact"}],
        },
        "keyword_score": 0.0,
        "embedding_score": STRICT_EMBEDDING_REVIEW_THRESHOLD,
        "substring_score": 0.0,
    }

    assert _reuse_acceptance_reason(candidate, threshold=0.68, target=target) == "strict_embedding_review"


def test_medium_high_embedding_candidate_enters_review_even_when_keyword_score_is_zero():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "constraints": [],
        },
        "keyword_score": 0.0,
        "embedding_score": MEDIUM_EMBEDDING_REVIEW_THRESHOLD,
        "substring_score": 0.0,
    }

    assert _reuse_acceptance_reason(candidate, threshold=0.40, target=target) == "embedding_high_review"


def test_keyword_high_but_embedding_not_high_enters_review():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "constraints": [],
        },
        "keyword_score": 0.62,
        "embedding_score": 0.71,
        "substring_score": 0.2,
    }

    assert _reuse_acceptance_reason(candidate, threshold=0.70, target=target) == "keyword_high_review"


def test_keyword_and_embedding_both_high_uses_keyword_high_review_below_original_threshold():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [{"kind": "entity", "value": "小蝌蚪", "importance": 1, "match_mode": "synonym"}],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "constraints": [{"kind": "entity", "value": "小蝌蚪", "importance": 1, "match_mode": "synonym"}],
        },
        "keyword_score": 0.62,
        "embedding_score": 0.91,
        "substring_score": 0.2,
    }

    assert _reuse_acceptance_reason(candidate, threshold=0.70, target=target) == "keyword_high_review"
    result = evaluate_reuse_filter(
        target,
        candidate["asset"],
        {
            "keyword_score": candidate["keyword_score"],
            "embedding_score": candidate["embedding_score"],
            "accepted_by": "keyword_high_review",
        },
        threshold=0.40,
    )

    assert result["decision"] == "llm_review"
    assert result["reason"] == "keyword_high_review"


def test_original_threshold_pass_bypasses_score_gate_review():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [{"kind": "entity", "value": "小蝌蚪", "importance": 1, "match_mode": "synonym"}],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "constraints": [{"kind": "entity", "value": "小蝌蚪", "importance": 1, "match_mode": "synonym"}],
        },
        "keyword_score": 0.92,
        "embedding_score": 0.91,
        "substring_score": 0.2,
    }

    assert _reuse_acceptance_reason(candidate, threshold=0.40, target=target) == "bm25_threshold"


def test_keyword_threshold_pass_with_low_embedding_does_not_auto_accept():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "loose",
        "asset_category": "learning_behavior",
        "constraints": [],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "loose",
            "asset_category": "learning_behavior",
            "constraints": [],
        },
        "keyword_score": 0.45,
        "embedding_score": 0.20,
        "substring_score": 0.0,
    }

    assert _reuse_acceptance_reason(candidate, threshold=0.40, target=target) == ""


def test_keyword_threshold_pass_with_low_embedding_can_enter_keyword_high_rescue():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "constraints": [],
        },
        "keyword_score": 0.62,
        "embedding_score": 0.20,
        "substring_score": 0.0,
    }

    assert _reuse_acceptance_reason(candidate, threshold=0.40, target=target) == "keyword_high_review"


def test_gray_score_candidate_enters_review_only_after_dual_threshold_reject():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "constraints": [],
        },
        "keyword_score": 0.32,
        "embedding_score": 0.61,
        "substring_score": 0.0,
    }

    assert _reuse_acceptance_reason(candidate, threshold=0.40, target=target) == "keyword_led_gray_review"


def test_transform_reject_blocks_reuse_acceptance_reason():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "constraints": [],
        },
        "keyword_score": 0.95,
        "embedding_score": 0.95,
        "substring_score": 0.5,
        "transform_policy": {"decision": "reject", "mode": "copy", "reason": "hero_aspect_mismatch_too_large"},
    }

    assert _reuse_acceptance_reason(candidate, threshold=0.40, target=target) == ""


def test_reuse_filter_hard_rejects_transform_reject():
    result = evaluate_reuse_filter(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "constraints": [],
        },
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "constraints": [],
        },
        {
            "keyword_score": 0.95,
            "embedding_score": 0.95,
            "transform_policy": {"decision": "reject", "reason": "hero_aspect_mismatch_too_large"},
        },
        threshold=0.40,
    )

    assert result["decision"] == "reject"
    assert result["reason"] == "aspect_transform_rejected"


def test_score_gate_review_reason_forces_llm_review():
    result = evaluate_reuse_filter(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "constraints": [],
        },
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "constraints": [],
        },
        {
            "keyword_score": 0.62,
            "embedding_score": 0.71,
            "accepted_by": "keyword_high_review",
        },
        threshold=0.40,
    )

    assert result["decision"] == "llm_review"
    assert result["reason"] == "keyword_high_review"


def test_normalize_reuse_policy_fields_forces_strict_for_high_risk_constraints():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "loose",
            "asset_category": "content_specific",
            "constraints": [
                {"kind": "text", "value": "character: bi", "importance": 2, "match_mode": "exact"},
                {"kind": "invalid", "value": "ignored", "importance": 2, "match_mode": "exact"},
                {"kind": "math", "value": "", "importance": 2, "match_mode": "exact"},
            ],
            "generic_support_allowed": True,
        }
    )

    assert policy["reuse_level"] == "strict"
    assert policy["asset_category"] == "content_specific"
    assert policy["generic_support_allowed"] is False
    assert policy["constraints"][0]["kind"] == "text"
    assert policy["constraints"][0]["importance"] == 2


def test_reuse_policy_rejects_strict_text_conflict():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [{"kind": "text", "value": "character: bi", "importance": 2, "match_mode": "exact"}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [{"kind": "text", "value": "character: bei", "importance": 2, "match_mode": "exact"}],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {
            "keyword_score": 0.9,
            "constraint_embedding_scores": [
                {"kind": "text", "target": "character: bi", "candidate": "character: bei", "score": 0.2}
            ],
        },
        threshold=0.6,
    )

    assert result["decision"] == "reject"
    assert result["reason"] == "strict_constraints_conflict"
    assert result["conflicts"][0]["kind"] == "text"


def test_reuse_policy_rejects_generic_tool_for_strict_content_target():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [{"kind": "math", "value": "AB=AC", "importance": 2, "match_mode": "exact"}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "generic_tool",
        "constraints": [],
        "generic_support_allowed": True,
    }

    result = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.75}, threshold=0.6)

    assert result["decision"] == "llm_review"
    assert result["reason"] == "strict_constraints_require_llm_review"
    assert result["review_items"][0]["kind"] == "math"
    assert result["review_items"][0]["reason"] == "missing_same_kind"


def test_normalize_reuse_policy_downgrades_visual_constraints_to_medium():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "constraints": [
                {"kind": "entity", "value": "visible subject", "importance": 1, "match_mode": "exact"},
                {"kind": "action", "value": "visible action", "importance": 1, "match_mode": "exact"},
            ],
            "generic_support_allowed": False,
        }
    )

    assert policy["reuse_level"] == "medium"
    assert policy["generic_support_allowed"] is False


def test_normalize_reuse_policy_keeps_medium_categories_threshold_based_without_high_risk_constraints():
    expected_constraints = [
        {"kind": "entity", "value": "visible subject", "importance": 1, "match_mode": "semantic"},
        {"kind": "action", "value": "visible action", "importance": 1, "match_mode": "semantic"},
    ]
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "strict",
            "asset_category": "concept_scene",
            "reuse_risk": {
                "readable_knowledge": {"required": False, "evidence": []},
                "unique_referent": {"required": False, "evidence": []},
                "exact_relation": {"required": False, "evidence": []},
            },
                "constraints": expected_constraints,
            "generic_support_allowed": False,
        }
    )

    assert policy["reuse_level"] == "medium"
    assert policy["generic_support_allowed"] is False
    assert {item["kind"] for item in policy["constraints"]} == {"entity", "action"}
    assert all(item["importance"] == 1 for item in policy["constraints"])


def test_normalize_reuse_policy_preserves_high_risk_constraints_in_medium_categories():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "constraints": [{"kind": "text", "value": "character: bi", "importance": 2, "match_mode": "exact"}],
            "generic_support_allowed": True,
        }
    )

    assert policy["reuse_level"] == "strict"
    assert policy["generic_support_allowed"] is False
    assert policy["constraints"][0]["kind"] == "text"
    assert policy["constraints"][0]["importance"] == 2


def test_normalize_reuse_policy_preserves_constraints_for_medium_assets():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "content_specific",
            "constraints": [{"kind": "entity", "value": "ordinary subject", "importance": 1, "match_mode": "semantic"}],
        }
    )

    assert policy["reuse_level"] == "medium"
    assert policy["constraints"][0]["kind"] == "entity"
    assert policy["constraints"][0]["importance"] == 1


def test_normalize_reuse_policy_does_not_infer_constraints_from_story_context():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "content_specific",
            "content_prompt": "story scene prompt",
            "context_summary": "specific story context",
            "teaching_intent": "understand story event",
            "constraints": [],
        }
    )

    assert policy["reuse_level"] == "loose"
    assert policy["generic_support_allowed"] is True
    assert policy["constraints"] == []


def test_normalize_reuse_policy_does_not_infer_constraints_from_character_state():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "content_prompt": "angry character scene",
            "context_summary": "anchored character state",
            "teaching_intent": "understand emotion shift",
            "constraints": [],
        }
    )

    assert policy["reuse_level"] == "loose"
    assert policy["generic_support_allowed"] is True
    assert policy["constraints"] == []


def test_normalize_reuse_policy_makes_unconstrained_decorative_character_action_loose():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "content_prompt": "卡通小蝌蚪举着小旗子引导学习路线",
            "context_summary": "用于目录页的装饰插图，引导学生了解学习环节顺序",
            "teaching_intent": "降低目录页的枯燥感",
            "constraints": [],
        }
    )

    assert policy["reuse_level"] == "loose"
    assert policy["constraints"] == []


def test_normalize_reuse_policy_keeps_hard_visual_subject_in_medium_character_action():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "content_prompt": "卡通小蝌蚪举着小旗子带路的插图",
            "constraints": [
                {"kind": "entity", "value": "小蝌蚪", "importance": 1, "match_mode": "synonym", "aliases": ["蝌蚪幼体"]}
            ],
        }
    )

    assert policy["reuse_level"] == "medium"
    assert policy["constraints"][0]["kind"] == "entity"
    assert policy["constraints"][0]["importance"] == 1


def test_normalize_reuse_policy_keeps_unique_visual_referent_medium():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "content_specific",
            "reuse_risk": {
                "readable_knowledge": {"required": False, "evidence": []},
                "unique_referent": {"required": True, "evidence": ["specific identity required"]},
                "exact_relation": {"required": False, "evidence": []},
            },
            "constraints": [{"kind": "entity", "value": "specific identity", "importance": 1, "match_mode": "exact"}],
        }
    )

    assert policy["reuse_level"] == "medium"
    assert policy["generic_support_allowed"] is False


def test_literary_family_closing_scene_score_fields_do_not_drive_policy():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "reuse_scores": {
                "strict_score": 0.6,
                "unique_referent_score": 0.65,
                "exact_relation_score": 0.55,
                "category_scores": {"content_specific": 0.7, "character_action": 0.6},
                "constraint_scores": [
                    {"kind": "entity", "value": "mother", "importance_score": 0.8, "exactness_score": 0.6},
                    {"kind": "entity", "value": "child", "importance_score": 0.8, "exactness_score": 0.6},
                    {"kind": "emotion", "value": "warm", "importance_score": 0.75, "exactness_score": 0.4},
                ],
            },
        }
    )

    assert policy["reuse_level"] == "loose"
    assert policy["asset_category"] == "content_specific"
    assert policy["generic_support_allowed"] is True
    assert policy["constraints"] == []


def test_generic_emotion_expression_is_loose_not_strict():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "strict",
            "asset_category": "character_action",
            "reuse_scores": {
                "strict_score": 0.5,
                "generic_support_score": 0.6,
                "readable_knowledge_score": 0.2,
                "unique_referent_score": 0.0,
                "exact_relation_score": 0.4,
                "category_scores": {"character_action": 0.6, "content_specific": 0.5},
                "constraint_scores": [
                    {"kind": "emotion", "value": "喜出望外", "importance_score": 0.9, "exactness_score": 0.6}
                ],
            },
            "constraints": [{"kind": "emotion", "value": "喜出望外", "importance": 0, "match_mode": "semantic"}],
        }
    )

    assert policy["reuse_level"] == "loose"
    assert {item["kind"] for item in policy["constraints"]} == {"emotion"}


def test_literary_action_scene_with_overstated_risk_is_medium():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "reuse_risk": {
                "unique_referent": {"required": True},
                "exact_relation": {"required": True},
                "readable_knowledge": {"required": False},
            },
            "constraints": [
                {"kind": "entity", "value": "男孩", "importance": 1, "match_mode": "semantic"},
                {"kind": "action", "value": "摔东西", "importance": 1, "match_mode": "semantic"},
                {"kind": "action", "value": "拒绝出门", "importance": 1, "match_mode": "exact"},
                {"kind": "emotion", "value": "暴怒", "importance": 1, "match_mode": "semantic"},
            ],
        }
    )

    assert policy["reuse_level"] == "medium"
    assert {item["kind"] for item in policy["constraints"]} == {"entity", "action", "emotion"}


def test_medium_semantic_reuse_accepts_embedding_signal_and_missing_soft_constraints():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [
            {"kind": "entity", "value": "visible subject", "importance": 0, "match_mode": "exact"},
            {"kind": "action", "value": "visible action", "importance": 0, "match_mode": "exact"},
        ],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.34, "embedding_score": 0.86, "accepted_by": "embedding_gray_zone"},
        threshold=0.5,
    )

    assert result["decision"] == "full_match"
    assert result["reason"] == "medium_similarity_threshold_match"


def test_medium_hard_core_subject_conflict_rejects_even_when_scores_pass():
    result = evaluate_reuse_filter(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "constraints": [{"kind": "entity", "value": "小蝌蚪", "importance": 2}],
        },
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "constraints": [{"kind": "entity", "value": "小松鼠", "importance": 2, "match_mode": "synonym"}],
        },
        {
            "keyword_score": 0.92,
            "embedding_score": 0.91,
            "accepted_by": "bm25_threshold",
            "constraint_embedding_scores": [
                {"kind": "entity", "target": "小蝌蚪", "candidate": "小松鼠", "score": 0.2}
            ],
        },
        threshold=0.40,
    )

    assert result["decision"] == "reject"
    assert result["reason"] == "medium_constraints_conflict"


def test_medium_hard_core_subject_missing_requires_llm_review():
    result = evaluate_reuse_filter(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "constraints": [
                {"kind": "entity", "value": "小蝌蚪", "importance": 2, "match_mode": "synonym", "aliases": ["蝌蚪幼体"]}
            ],
        },
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "constraints": [],
        },
        {"keyword_score": 0.92, "embedding_score": 0.91, "accepted_by": "bm25_threshold"},
        threshold=0.40,
    )

    assert result["decision"] == "llm_review"
    assert result["reason"] == "medium_constraints_require_llm_review"


def test_target_imp2_value_covered_by_candidate_imp0_same_value_is_not_conflict():
    """Regression for case 3.1 (笔 / 圈出生字词): target requires '笔' imp=2,
    candidate has '笔' imp=0 plus other imp=2 objects. The candidate's imp=0
    entry must not be filtered out before comparison; the value '笔' is
    present, so this is a covered match, not a conflict."""

    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "learning_behavior",
        "constraints": [{"kind": "object", "value": "笔", "importance": 2}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "learning_behavior",
        "constraints": [
            {"kind": "object", "value": "笔", "importance": 0},
            {"kind": "object", "value": "课文", "importance": 2},
            {"kind": "object", "value": "生字词", "importance": 2},
        ],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.8, "embedding_score": 0.8, "accepted_by": "bm25_threshold"},
        threshold=0.5,
    )

    assert result["decision"] == "full_match"
    assert "笔" not in str(result.get("conflicts") or [])


def test_medium_hard_core_subject_match_allows_threshold_accept():
    result = evaluate_reuse_filter(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "constraints": [
                {"kind": "entity", "value": "小蝌蚪", "importance": 2, "match_mode": "synonym", "aliases": ["蝌蚪幼体"]}
            ],
        },
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "constraints": [{"kind": "entity", "value": "小蝌蚪", "importance": 2}],
        },
        {"keyword_score": 0.92, "embedding_score": 0.91, "accepted_by": "bm25_threshold"},
        threshold=0.40,
    )

    assert result["decision"] == "full_match"


def test_medium_filter_requires_llm_when_embedding_high_but_keyword_score_is_zero():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {
            "keyword_score": 0.0,
            "embedding_score": MEDIUM_EMBEDDING_REVIEW_THRESHOLD,
            "accepted_by": "medium_embedding_review",
        },
        threshold=0.40,
    )

    assert result["decision"] == "llm_review"
    assert result["reason"] == "medium_embedding_high_keyword_below_threshold"
    assert result["review_items"][0]["threshold"] == MEDIUM_EMBEDDING_REVIEW_THRESHOLD


def test_character_action_and_concept_scene_are_semantically_compatible():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "character_action",
        "constraints": [{"kind": "action", "value": "visible action", "importance": 1, "match_mode": "exact"}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [{"kind": "action", "value": "visible action", "importance": 1, "match_mode": "exact"}],
    }

    result = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.55, "embedding_score": 0.7}, threshold=0.5)

    assert result["decision"] == "full_match"


def test_reuse_threshold_uses_derived_level_with_category_forced_loose():
    """Decorative categories (learning_behavior/generic_tool/generic_diagram) force loose
    threshold; other categories derive from constraints."""

    learning = reuse_threshold_for_target(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "learning_behavior",
            "constraints": [],
        }
    )
    generic_tool = reuse_threshold_for_target(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "generic_tool",
            "constraints": [],
        }
    )
    concept_scene = reuse_threshold_for_target(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "constraints": [],
        }
    )
    loose_concept_scene = reuse_threshold_for_target(
        {
            "asset_kind": "page_image",
            "reuse_level": "loose",
            "asset_category": "concept_scene",
            "constraints": [],
        }
    )
    learning_behavior_with_weak_constraint = reuse_threshold_for_target(
        {
            "asset_kind": "page_image",
            "asset_category": "learning_behavior",
            "constraints": [{"kind": "action", "value": "read", "importance": 1}],
        }
    )
    medium = reuse_threshold_for_target(
        {
            "asset_kind": "page_image",
            "asset_category": "concept_scene",
            "constraints": [{"kind": "action", "value": "read", "importance": 1}],
        }
    )
    strict = reuse_threshold_for_target(
        {
            "asset_kind": "page_image",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "constraints": [{"kind": "math", "value": "x+2=5", "importance": 2, "match_mode": "exact"}],
        }
    )

    assert learning == generic_tool == concept_scene == loose_concept_scene == 0.5
    # learning_behavior forces loose even when constraints exist
    assert learning_behavior_with_weak_constraint == 0.5
    assert medium == 0.55
    assert strict == 0.63


def test_unconstrained_specific_metadata_falls_back_to_similarity_threshold():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "reuse_risk": {"unique_referent": {"required": True, "evidence": ["specific content required"]}},
        "constraints": [],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "reuse_risk": {"unique_referent": {"required": True, "evidence": ["specific content required"]}},
        "constraints": [],
    }

    accepted = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.7, "embedding_score": 0.7}, threshold=0.6)
    rejected = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.5}, threshold=0.6)

    assert accepted["decision"] == "full_match"
    assert accepted["reason"] == "medium_similarity_threshold_match"
    assert rejected["decision"] == "reject"
    assert rejected["reason"] == "similarity_below_threshold"


def test_strict_filter_requires_llm_when_embedding_high_but_keyword_score_below_threshold():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [{"kind": "text", "value": "character: bi", "importance": 2, "match_mode": "exact"}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [{"kind": "text", "value": "character: bi", "importance": 2, "match_mode": "exact"}],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {
            "keyword_score": 0.0,
            "embedding_score": STRICT_EMBEDDING_REVIEW_THRESHOLD,
            "accepted_by": "strict_embedding_review",
        },
        threshold=0.68,
    )

    assert result["decision"] == "llm_review"
    # When target's imp>=2 text/math/physics constraints are exact-covered by
    # candidate metadata, the short-circuit downgrades to a distinguished
    # llm_review reason so the reviewer threshold can be relaxed for visual
    # confirmation only (see _reuse_review_accept_score_threshold).
    assert result["reason"] == "strict_text_exact_covered_review"


def test_medium_category_no_longer_adds_embedding_floor_review():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "generic_diagram",
        "constraints": [],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "generic_diagram",
        "constraints": [],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.55, "embedding_score": 0.0, "accepted_by": "bm25_threshold"},
        threshold=0.48,
    )

    # generic_diagram is in FORCED_LOOSE_CATEGORIES, so the match goes
    # through the decorative_loose branch instead of the medium branch.
    assert result["decision"] == "full_match"
    assert result["reason"] == "decorative_loose_match"


def test_old_strict_metadata_without_constraints_does_not_add_embedding_floor_review():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "reuse_risk": {"unique_referent": {"required": True, "evidence": ["specific content required"]}},
        "constraints": [],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "reuse_risk": {"unique_referent": {"required": True, "evidence": ["specific content required"]}},
        "constraints": [],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.7, "embedding_score": 0.2, "accepted_by": "bm25_threshold"},
        threshold=0.66,
    )

    assert result["decision"] == "full_match"
    assert result["reason"] == "medium_similarity_threshold_match"


def test_strict_candidate_against_medium_target_uses_target_strictness():
    """Under target-only strictness, a strict candidate matched against a medium
    target follows the medium path. The candidate's stricter labels are
    informational metadata, not a gate on this match."""

    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [
            {"kind": "text", "value": "character: bi", "importance": 2, "match_mode": "exact"},
        ],
    }

    result = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.9, "embedding_score": 0.7}, threshold=0.5)

    assert result["decision"] == "full_match"
    assert result["reason"] == "medium_similarity_threshold_match"


def test_strict_candidate_accepts_when_target_covers_structured_constraints():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "character_action",
        "constraints": [
            {"kind": "text", "value": "character: bi", "importance": 2, "match_mode": "exact"},
        ],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [
            {"kind": "text", "value": "character: bi", "importance": 2, "match_mode": "exact"},
        ],
    }

    result = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.9, "embedding_score": 0.7}, threshold=0.5)

    assert result["decision"] == "llm_review"
    # Strong text constraint exact-cover routes to the relaxed-threshold
    # review path; the candidate's stricter own labels don't change that.
    assert result["reason"] == "strict_text_exact_covered_review"


def test_visual_constraint_accepts_exact_match():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [{"kind": "action", "value": "read aloud", "importance": 2}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [{"kind": "action", "value": "read aloud", "importance": 2}],
    }

    result = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.9, "embedding_score": 0.7}, threshold=0.6)

    assert result["decision"] == "full_match"
    # Non-text strong constraints exact-cover short-circuits directly to
    # full_match without going through LLM review.
    assert result["reason"] == "strong_constraints_exact_covered"


def test_strict_visual_constraint_accepts_high_embedding_match():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [{"kind": "action", "value": "read aloud", "importance": 2, "match_mode": "semantic"}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [{"kind": "action", "value": "oral reading", "importance": 2, "match_mode": "semantic"}],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {
            "keyword_score": 0.9,
            "embedding_score": 0.9,
            "constraint_embedding_scores": [
                {"kind": "action", "target": "read aloud", "candidate": "oral reading", "score": 0.87}
            ],
        },
        threshold=0.6,
    )

    assert result["decision"] == "full_match"


def test_scene_constraint_embedding_match():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [{"kind": "scene", "value": "subject acts on object", "importance": 2, "match_mode": "semantic"}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [{"kind": "scene", "value": "actor performs action on object", "importance": 2, "match_mode": "semantic"}],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {
            "keyword_score": 0.9,
            "embedding_score": 0.9,
            "constraint_embedding_scores": [
                {
                    "kind": "scene",
                    "target": "subject acts on object",
                    "candidate": "actor performs action on object",
                    "score": 0.94,
                }
            ],
        },
        threshold=0.6,
    )

    assert result["decision"] == "full_match"


def test_aspect_transform_rejects_strict_content_large_mismatch():
    result = evaluate_aspect_transform(
        {
            "asset_kind": "page_image",
            "aspect_ratio": "16:9",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "constraints": [{"kind": "text", "value": "character: bi", "importance": 2, "match_mode": "exact"}],
        },
        {
            "asset_kind": "page_image",
            "aspect_ratio": "3:4",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "constraints": [{"kind": "text", "value": "character: bi", "importance": 2, "match_mode": "exact"}],
        },
    )

    assert result["decision"] == "reject"
    assert result["reason"] == "strict_aspect_mismatch_too_large"


def test_aspect_transform_rejects_square_to_wide_hero():
    result = evaluate_aspect_transform(
        {
            "asset_kind": "page_image",
            "role": "hero",
            "aspect_ratio": "16:9",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "constraints": [],
        },
        {
            "asset_kind": "page_image",
            "aspect_ratio": "1:1",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "constraints": [],
        },
    )

    assert result["decision"] == "reject"
    assert result["reason"] == "hero_aspect_mismatch_too_large"


def test_aspect_transform_allows_strict_content_square_to_standard_padding():
    result = evaluate_aspect_transform(
        {
            "asset_kind": "page_image",
            "aspect_ratio": "4:3",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "constraints": [{"kind": "text", "value": "character: bi", "importance": 2, "match_mode": "exact"}],
        },
        {
            "asset_kind": "page_image",
            "aspect_ratio": "1:1",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "constraints": [{"kind": "text", "value": "character: bi", "importance": 2, "match_mode": "exact"}],
        },
    )

    assert result["decision"] == "penalize"
    assert result["mode"] == "contain_pad"
    assert result["reason"] == "strict_content_preserving_medium_pad"


def test_aspect_transform_allows_medium_content_shape_padding():
    result = evaluate_aspect_transform(
        {
            "asset_kind": "page_image",
            "aspect_ratio": "4:3",
            "reuse_level": "medium",
            "asset_category": "content_specific",
            "constraints": [{"kind": "entity", "value": "visible subject", "importance": 1, "match_mode": "exact"}],
        },
        {
            "asset_kind": "page_image",
            "aspect_ratio": "1:1",
            "reuse_level": "medium",
            "asset_category": "content_specific",
            "constraints": [],
        },
    )

    assert result["decision"] == "penalize"
    assert result["mode"] == "contain_pad"
    assert result["reason"] == "strict_content_preserving_medium_pad"


def test_aspect_transform_pads_generic_tool_medium_mismatch():
    result = evaluate_aspect_transform(
        {
            "asset_kind": "page_image",
            "aspect_ratio": "4:3",
            "reuse_level": "medium",
            "asset_category": "generic_tool",
            "constraints": [],
        },
        {
            "asset_kind": "page_image",
            "aspect_ratio": "1:1",
            "reuse_level": "medium",
            "asset_category": "generic_tool",
            "constraints": [],
        },
    )

    assert result["decision"] == "penalize"
    assert result["mode"] == "contain_pad"
    assert result["transform_penalty"] == 0.1


def test_aspect_transform_allows_background_blur_pad():
    result = evaluate_aspect_transform(
        {"asset_kind": "background", "aspect_ratio": "16:9"},
        {"asset_kind": "background", "aspect_ratio": "4:3"},
    )

    assert result["decision"] == "penalize"
    assert result["mode"] == "blur_pad"
    assert result["transform_penalty"] == 0.06


# ------------------------- P0-B' precision signal -------------------------


def test_precision_signal_requires_shared_imp1_constraint_or_keyword():
    """Target with only imp=0 constraints and no shared discriminative
    keyword has no precision signal — regression for case 2.1."""

    target = {
        "core_keywords": ["教室"],
        "constraints": [{"kind": "scene", "value": "教室", "importance": 0}],
    }
    candidate = {
        "core_keywords": ["鸟", "马", "鱼"],
        "constraints": [{"kind": "entity", "value": "鸟", "importance": 1}],
    }

    assert has_precision_signal(target, candidate) is False


def test_precision_signal_passes_on_shared_imp1_constraint():
    target = {
        "core_keywords": [],
        "constraints": [{"kind": "entity", "value": "母亲", "importance": 1}],
    }
    candidate = {
        "core_keywords": [],
        "constraints": [{"kind": "entity", "value": "母亲", "importance": 1}],
    }

    assert has_precision_signal(target, candidate) is True


def test_precision_signal_passes_on_shared_discriminative_keyword():
    """A shared core_keyword passes the gate when its library df_ratio is
    at or below the threshold (here: 1/3 = 0.33 fails, 1/5 = 0.20 passes)."""

    target = {"core_keywords": ["田字格"], "constraints": []}
    candidate = {"core_keywords": ["田字格", "笔顺"], "constraints": []}

    # No df_ratio table → falls back to stopword-only filtering; '田字格'
    # is not a stopword, so the shared term passes.
    assert has_precision_signal(target, candidate) is True

    # With df_ratio table: '田字格' is rare (low ratio) → passes.
    assert has_precision_signal(
        target,
        candidate,
        keyword_df_ratio={"田字格": 0.10, "笔顺": 0.50},
    ) is True


def test_precision_signal_blocked_by_high_df_ratio_keyword():
    """A keyword shared across most of the library is too common to anchor
    precision — gate stays False."""

    target = {"core_keywords": ["插画", "教室"], "constraints": []}
    candidate = {"core_keywords": ["插画", "鸟"], "constraints": []}

    assert has_precision_signal(
        target,
        candidate,
        keyword_df_ratio={"插画": 0.95, "教室": 0.05, "鸟": 0.05},
    ) is False


def test_compute_keyword_df_ratio_basic():
    assets = [
        {"asset_kind": "page_image", "core_keywords": ["插画", "教室"]},
        {"asset_kind": "page_image", "core_keywords": ["插画", "卧室"]},
        {"asset_kind": "page_image", "core_keywords": ["教室"]},
        {"asset_kind": "background", "core_keywords": ["插画"]},  # filtered
    ]
    ratios = compute_keyword_df_ratio(assets)

    assert ratios["插画"] == 2 / 3  # 2 of 3 page_image assets
    assert ratios["教室"] == 2 / 3
    assert ratios["卧室"] == 1 / 3


def test_precision_gate_downgrades_full_match_to_llm_review():
    """End-to-end: full_match gets downgraded to llm_review when score_details
    explicitly carries precision_signal=False — regression for case 2.1."""

    target = {
        "asset_kind": "page_image",
        "reuse_level": "loose",
        "asset_category": "learning_behavior",
        "constraints": [],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "loose",
        "asset_category": "learning_behavior",
        "constraints": [{"kind": "entity", "value": "鸟", "importance": 1}],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {
            "keyword_score": 0.56,
            "embedding_score": 0.56,
            "accepted_by": "bm25_threshold",
            "precision_signal": False,
        },
        threshold=0.5,
    )

    assert result["decision"] == "llm_review"
    assert result["reason"] == "no_precision_signal"


def test_precision_gate_allows_full_match_when_signal_true():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [{"kind": "entity", "value": "母亲", "importance": 1}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [{"kind": "entity", "value": "母亲", "importance": 1}],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {
            "keyword_score": 0.7,
            "embedding_score": 0.7,
            "accepted_by": "bm25_threshold",
            "precision_signal": True,
        },
        threshold=0.5,
    )

    assert result["decision"] == "full_match"


# ------------------------- P1-A candidate extras -------------------------


def test_candidate_extra_strong_constraints_flags_uncovered_named_individual():
    """Regression for case 2.2 (北海菊花 vs 史铁生+轮椅): candidate has a
    named_individual the target never asked for → flagged as extra."""

    target = [{"kind": "scene", "value": "北海公园", "importance": 1}]
    candidate = [
        {"kind": "scene", "value": "北海公园", "importance": 1},
        {"kind": "entity", "subtype": "named_individual", "value": "史铁生", "importance": 2},
    ]

    extras = candidate_extra_strong_constraints(target, candidate)

    assert len(extras) == 1
    assert extras[0]["value"] == "史铁生"


def test_candidate_extra_strong_constraints_ignores_covered_values():
    target = [{"kind": "entity", "subtype": "named_individual", "value": "史铁生", "importance": 2}]
    candidate = [{"kind": "entity", "subtype": "named_individual", "value": "史铁生", "importance": 2}]

    assert candidate_extra_strong_constraints(target, candidate) == []


def test_candidate_extra_strong_constraints_ignores_role_subtype():
    """Roles/generics are not narrative-binding extras — they don't trigger
    the candidate-extras gate even when target doesn't cover them."""

    target = [{"kind": "scene", "value": "公园", "importance": 1}]
    candidate = [
        {"kind": "scene", "value": "公园", "importance": 1},
        {"kind": "entity", "subtype": "role", "value": "妈妈", "importance": 1},
    ]

    assert candidate_extra_strong_constraints(target, candidate) == []


def test_evaluate_reuse_filter_downgrades_on_candidate_extra_named_individual():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "constraints": [{"kind": "scene", "value": "北海公园", "importance": 1}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [
            {"kind": "scene", "value": "北海公园", "importance": 1},
            {
                "kind": "entity",
                "subtype": "named_individual",
                "value": "史铁生",
                "importance": 2,
            },
        ],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.8, "embedding_score": 0.8, "precision_signal": True},
        threshold=0.5,
    )

    assert result["decision"] == "llm_review"
    assert result["reason"] == "candidate_extra_strong_constraints"
    assert any(item.get("value") == "史铁生" for item in result["review_items"])


# ------------------------- P1-D teaching content set -------------------------


def test_extra_teaching_content_constraints_flags_extra_character():
    """Regression for case 2.3 (爽 → 枚+爽): candidate has an additional
    teaching character the target never asked for."""

    target = [
        {"kind": "text", "subtype": "teaching_content", "value": "爽", "importance": 2},
    ]
    candidate = [
        {"kind": "text", "subtype": "teaching_content", "value": "爽", "importance": 2},
        {"kind": "text", "subtype": "teaching_content", "value": "枚", "importance": 2},
    ]

    extras = extra_teaching_content_constraints(target, candidate)

    assert len(extras) == 1
    assert extras[0]["value"] == "枚"


def test_extra_teaching_content_constraints_ignores_non_teaching_content():
    """Decorative text (subtype=decorative_text) doesn't count as a
    teaching-fact extra."""

    target = [
        {"kind": "text", "subtype": "teaching_content", "value": "爽", "importance": 2},
    ]
    candidate = [
        {"kind": "text", "subtype": "teaching_content", "value": "爽", "importance": 2},
        {"kind": "text", "subtype": "decorative_text", "value": "练习册", "importance": 0},
    ]

    assert extra_teaching_content_constraints(target, candidate) == []


def test_extra_teaching_content_constraints_skips_when_target_has_no_teaching_content():
    """Only applies when target itself carries teaching_content — otherwise
    candidate's teaching_content is governed by other rules."""

    target = [{"kind": "entity", "value": "小蝌蚪", "importance": 2}]
    candidate = [
        {"kind": "text", "subtype": "teaching_content", "value": "字", "importance": 2},
    ]

    assert extra_teaching_content_constraints(target, candidate) == []


# ------------------------- Issue 1 forced_loose path -------------------------


def test_forced_loose_target_skips_constraint_comparison():
    """Regression for case 3.1: a learning_behavior target with an imp=2
    constraint that is not covered by candidate's imp>=1 constraints used to
    trigger medium_constraints_conflict. Under the new decorative_loose
    branch, the constraint comparison is skipped entirely — the asset's
    constraints are descriptive metadata, not gating requirements."""

    target = {
        "asset_kind": "page_image",
        "reuse_level": "loose",
        "asset_category": "learning_behavior",
        "constraints": [
            {"kind": "object", "value": "笔", "importance": 2},
        ],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "loose",
        "asset_category": "learning_behavior",
        "constraints": [
            {"kind": "object", "value": "课文", "importance": 1},
            {"kind": "object", "value": "生字词", "importance": 1},
        ],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {
            "keyword_score": 0.7,
            "embedding_score": 0.7,
            "accepted_by": "bm25_threshold",
            "precision_signal": True,
        },
        threshold=0.5,
    )

    assert result["decision"] == "full_match"
    assert result["reason"] == "decorative_loose_match"


def test_forced_loose_target_still_rejects_candidate_named_individual():
    """The decorative_loose branch keeps candidate_extra_strong active —
    a learning_behavior target must not accept a candidate that smuggles
    in a named_individual the target didn't request."""

    target = {
        "asset_kind": "page_image",
        "reuse_level": "loose",
        "asset_category": "learning_behavior",
        "constraints": [],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [
            {
                "kind": "entity",
                "subtype": "named_individual",
                "value": "史铁生",
                "importance": 2,
            },
        ],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.7, "embedding_score": 0.7, "precision_signal": True},
        threshold=0.5,
    )

    assert result["decision"] == "llm_review"
    assert result["reason"] == "candidate_extra_strong_constraints"


def test_forced_loose_target_rejects_below_threshold():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "loose",
        "asset_category": "generic_tool",
        "constraints": [{"kind": "object", "value": "笔", "importance": 2}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "loose",
        "asset_category": "generic_tool",
        "constraints": [],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.3, "embedding_score": 0.3, "precision_signal": True},
        threshold=0.5,
    )

    assert result["decision"] == "reject"
    assert result["reason"] == "similarity_below_threshold"


# ------------------------- Issue 2 layout_container -------------------------


def test_layout_container_subtype_is_accepted():
    """layout_container is a valid subtype; normalize_constraints preserves it."""

    constraints = normalize_constraints(
        [
            {
                "kind": "object",
                "subtype": "layout_container",
                "value": "卡片",
                "importance": 0,
            }
        ]
    )

    assert len(constraints) == 1
    assert constraints[0]["subtype"] == "layout_container"
    assert constraints[0]["importance"] == 0


def test_layout_container_imp0_does_not_trigger_candidate_extra_strong():
    """A candidate with layout_container (any value) must not surface as an
    extra-strong constraint, since layout_container is by definition
    interchangeable and never reaches imp=2."""

    target = [{"kind": "scene", "value": "教室", "importance": 1}]
    candidate = [
        {"kind": "scene", "value": "教室", "importance": 1},
        {"kind": "object", "subtype": "layout_container", "value": "卡片", "importance": 0},
        {"kind": "object", "subtype": "layout_container", "value": "边框", "importance": 0},
    ]

    assert candidate_extra_strong_constraints(target, candidate) == []


def test_teaching_carrier_still_triggers_candidate_extra_strong():
    """Narrowed teaching_carrier (hard carrier like 田字格) still surfaces
    as candidate-extra when target doesn't ask for it."""

    target = [{"kind": "text", "value": "比", "importance": 2, "subtype": "teaching_content"}]
    candidate = [
        {"kind": "text", "value": "比", "importance": 2, "subtype": "teaching_content"},
        {"kind": "object", "subtype": "teaching_carrier", "value": "田字格", "importance": 2},
    ]

    extras = candidate_extra_strong_constraints(target, candidate)
    assert len(extras) == 1
    assert extras[0]["value"] == "田字格"


def test_evaluate_reuse_filter_downgrades_on_extra_teaching_content():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [
            {"kind": "text", "subtype": "teaching_content", "value": "爽", "importance": 2},
        ],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [
            {"kind": "text", "subtype": "teaching_content", "value": "爽", "importance": 2},
            {"kind": "text", "subtype": "teaching_content", "value": "枚", "importance": 2},
        ],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {
            "keyword_score": 0.8,
            "embedding_score": 0.8,
            "constraint_embedding_scores": [
                {"kind": "text", "target": "爽", "candidate": "爽", "score": 0.99},
            ],
        },
        threshold=0.5,
    )

    assert result["decision"] == "llm_review"
    assert result["reason"] == "candidate_extra_teaching_content"
    assert any(item.get("value") == "枚" for item in result["review_items"])


