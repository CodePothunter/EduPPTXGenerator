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


# ----------------- candidate.padding_capacity gates -----------------


def _aspect_target(reuse_level: str = "medium", role: str = "") -> dict:
    """Build a page_image target. ``medium`` w/o constraints exercises the
    unknown-reuse branch; pass ``reuse_level='strict'`` or set ``role`` to
    hit the role-specific or strict branches.
    """
    target: dict = {
        "asset_kind": "page_image",
        "aspect_ratio": "16:9",
        "asset_category": "concept_scene",
        "constraints": [],
    }
    if role:
        target["role"] = role
    if reuse_level == "strict":
        target["constraints"] = [
            {"kind": "entity", "value": "鲁迅", "importance": 2, "subtype": "named_individual"}
        ]
    return target


def _aspect_candidate(aspect: str, capacity: str = "") -> dict:
    cand: dict = {"asset_kind": "page_image", "aspect_ratio": aspect}
    if capacity:
        cand["padding_capacity"] = capacity
    return cand


def test_aspect_transform_high_capacity_widens_unknown_medium_pad_ceiling():
    """4:3 → 16:9 has loss≈0.4375 — without capacity info this is rejected.
    With padding_capacity=high (transparent edges), the medium-pad ceiling
    widens to 0.25×1.3 = 0.325 — still rejected at 0.4375. But 16:9 → 5:4
    (loss≈0.30) was rejected under the old rule, and is now padded."""

    # 16:9 → 5:4: loss = 1 - (5/4)/(16/9) = 1 - 0.703 = 0.297
    cand = _aspect_candidate("5:4", capacity="high")
    result = evaluate_aspect_transform(_aspect_target(reuse_level="medium"), cand)

    assert result["decision"] == "penalize"
    assert result["mode"] == "contain_pad"
    assert result["reason"] == "unknown_medium_pad"
    assert result["padding_capacity"] == "high"


def test_aspect_transform_low_capacity_tightens_unknown_medium_pad_ceiling():
    """4:3 → 1:1 has loss=0.25 — under the old rule this is medium-pad
    accepted. With padding_capacity=low (colored painted-in edges), the
    medium-pad ceiling tightens to 0.25×0.7 = 0.175, so 0.25 is now
    rejected."""

    cand = _aspect_candidate("1:1", capacity="low")
    target = _aspect_target(reuse_level="medium")
    target["aspect_ratio"] = "4:3"
    result = evaluate_aspect_transform(target, cand)

    assert result["decision"] == "reject"
    assert result["reason"] == "unknown_aspect_mismatch_too_large"


def test_aspect_transform_low_capacity_swaps_contain_pad_for_cover_crop():
    """Low-capacity images get cover_crop instead of contain_pad in the
    light-pad zone, since a hard seam looks worse than a centered crop."""

    # 4:3 → 5:4: loss = 1 - (5/4)/(4/3) = 1 - 0.9375 = 0.0625
    cand = _aspect_candidate("5:4", capacity="low")
    target = _aspect_target(reuse_level="medium")
    target["aspect_ratio"] = "4:3"
    result = evaluate_aspect_transform(target, cand)

    assert result["decision"] == "penalize"
    assert result["mode"] == "cover_crop"
    assert result["reason"] == "unknown_light_pad"


def test_aspect_transform_high_capacity_drops_penalty():
    """High-capacity images get half the transform penalty since pad is
    invisible."""

    # 4:3 → 5:4: loss=0.0625, falls into unknown_light_pad (base penalty 0.05)
    cand_default = _aspect_candidate("5:4")
    cand_high = _aspect_candidate("5:4", capacity="high")
    target = _aspect_target(reuse_level="medium")
    target["aspect_ratio"] = "4:3"

    default_result = evaluate_aspect_transform(target, cand_default)
    high_result = evaluate_aspect_transform(target, cand_high)

    assert default_result["transform_penalty"] == 0.05
    assert high_result["transform_penalty"] == round(0.05 * 0.5, 4)


def test_aspect_transform_strict_high_capacity_extends_medium_pad():
    """Strict + named_individual would reject 4:3 → 1:1 (loss=0.25 is
    exactly the legacy ceiling, then > ceiling for any further mismatch).
    With high capacity, the strict medium-pad ceiling becomes 0.325, so a
    16:9 → 4:3 case (loss=0.25) stays in pad territory even when there's
    a tighter strict bar."""

    # 16:9 → 5:4: loss=0.297. Without capacity (factor=1.0), 0.297 > 0.25 → reject.
    # With high (factor=1.3), 0.297 <= 0.325 → penalize contain_pad.
    cand = _aspect_candidate("5:4", capacity="high")
    result = evaluate_aspect_transform(_aspect_target(reuse_level="strict"), cand)

    assert result["decision"] == "penalize"
    assert result["mode"] == "contain_pad"
    assert result["reason"] == "strict_content_preserving_medium_pad"

    cand_default = _aspect_candidate("5:4")
    default_result = evaluate_aspect_transform(_aspect_target(reuse_level="strict"), cand_default)
    assert default_result["decision"] == "reject"


def test_aspect_transform_unknown_capacity_keeps_legacy_behavior():
    """Missing padding_capacity (e.g. an asset registered before the pixel
    snapshot, or one where edge analysis returned nothing) must not change
    any current outcome — factor=1.0, mode unchanged, penalty unchanged."""

    # 4:3 → 1:1: loss=0.25, medium reuse with no constraints → unknown branch.
    cand = _aspect_candidate("1:1")
    target = _aspect_target(reuse_level="medium")
    target["aspect_ratio"] = "4:3"
    result = evaluate_aspect_transform(target, cand)

    assert result["decision"] == "penalize"
    assert result["mode"] == "contain_pad"
    assert result["transform_penalty"] == 0.10
    assert "padding_capacity" not in result


def test_aspect_transform_reads_legacy_nested_transform_advice():
    """Back-compat: a candidate carrying the old nested
    ``transform_advice.padding_capacity`` shape (un-migrated library record)
    must still apply the capacity factor. This lets the reuse policy keep
    working against library JSON that hasn't been rewritten yet."""

    cand = {
        "asset_kind": "page_image",
        "aspect_ratio": "5:4",
        "transform_advice": {"padding_capacity": "high"},
    }
    target = _aspect_target(reuse_level="medium")
    target["aspect_ratio"] = "16:9"
    result = evaluate_aspect_transform(target, cand)

    # 16:9 → 5:4, loss=0.297. With high (factor=1.3), 0.297 <= 0.325 → pad.
    assert result["decision"] == "penalize"
    assert result["mode"] == "contain_pad"
    assert result["padding_capacity"] == "high"


def test_aspect_transform_background_ignores_padding_capacity():
    """Backgrounds are full-bleed by design — padding_capacity on a
    background asset is meaningless and must not modulate thresholds."""

    bg_cand = {
        "asset_kind": "background",
        "aspect_ratio": "4:3",
        "padding_capacity": "low",
    }
    result = evaluate_aspect_transform(
        {"asset_kind": "background", "aspect_ratio": "16:9"},
        bg_cand,
    )

    assert result["decision"] == "penalize"
    assert result["mode"] == "blur_pad"
    assert result["transform_penalty"] == 0.06
    assert "padding_capacity" not in result


def test_aspect_transform_hero_low_capacity_rejects_what_high_would_pad():
    """Hero with loss=0.20: without capacity → penalize/contain_pad.
    With low → still in `loss > 0.12 * 0.7 = 0.084` AND `loss <= 0.25 * 0.7 = 0.175`?
    Actually 0.20 > 0.175 so falls past → reject. With high → 0.20 <= 0.325
    so penalize. This test pins both directions in one branch."""

    # 4:3 → 1:1: loss=0.25.
    target = _aspect_target(reuse_level="medium", role="hero")
    target["aspect_ratio"] = "4:3"

    low = evaluate_aspect_transform(target, _aspect_candidate("1:1", capacity="low"))
    high = evaluate_aspect_transform(target, _aspect_candidate("1:1", capacity="high"))

    # loss=0.25 > 0.25*0.7=0.175 → reject for low.
    assert low["decision"] == "reject"
    # loss=0.25 > 0.12*1.3=0.156 AND <= 0.25*1.3=0.325 → penalize for high.
    assert high["decision"] == "penalize"
    assert high["mode"] == "contain_pad"


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


# ---- forced-loose target-strong-undercoverage (建议 1) ----


def test_forced_loose_target_teaching_carrier_missing_goes_to_llm_review():
    """Regression for "标自然段序号" vs "圈词语": when a learning_behavior
    target carries an imp=2 teaching_fact / teaching_carrier that the
    candidate does not cover, the forced-loose short-circuit must hand the
    decision to the LLM instead of silently full_match-ing."""

    target = {
        "asset_kind": "page_image",
        "reuse_level": "loose",
        "asset_category": "learning_behavior",
        "constraints": [
            {"kind": "action", "subtype": "teaching_fact",
             "value": "标自然段序号", "importance": 2},
        ],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "loose",
        "asset_category": "learning_behavior",
        "constraints": [
            {"kind": "action", "subtype": "teaching_fact",
             "value": "圈词语", "importance": 2},
        ],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.7, "embedding_score": 0.7, "precision_signal": True},
        threshold=0.5,
    )

    assert result["decision"] == "llm_review"
    assert result["reason"] == "forced_loose_target_teaching_missing"
    assert any(item.get("value") == "标自然段序号" for item in result["review_items"])


def test_forced_loose_target_teaching_carrier_covered_still_full_match():
    """Companion to the above: when the candidate DOES cover the target's
    imp=2 teaching_carrier (田字格), forced-loose should still accept via
    decorative_loose_match — the new guard must not over-reject."""

    target = {
        "asset_kind": "page_image",
        "reuse_level": "loose",
        "asset_category": "learning_behavior",
        "constraints": [
            {"kind": "object", "subtype": "teaching_carrier",
             "value": "田字格", "importance": 2},
        ],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "loose",
        "asset_category": "learning_behavior",
        "constraints": [
            {"kind": "object", "subtype": "teaching_carrier",
             "value": "田字格", "importance": 2},
        ],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.7, "embedding_score": 0.7, "precision_signal": True},
        threshold=0.5,
    )

    assert result["decision"] == "full_match"
    # Either reason is acceptable — when the imp=2 teaching_carrier is
    # covered, the strong-cover short-circuit (strong_constraints_exact_covered)
    # may fire before the forced-loose branch even runs. The point of this
    # test is that the new guard does NOT downgrade a properly-covered match.
    assert result["reason"] in {
        "decorative_loose_match",
        "strong_constraints_exact_covered",
    }


# ---- narrative reflux scene_prop (建议 2) ----


def test_narrative_reflux_blocks_strong_cover_when_scene_prop_missing():
    """Regression for 《比尾巴》"长尾巴的猴子" vs "猴子的卡通头像":
    imp=2 species_instance "猴子" is covered on both sides, but imp=1
    scene_prop "长尾巴" is missing on candidate. The strong-cover short-
    circuit must now drop into target_narrative_undercoverage rather than
    full_match — scene_prop is page-defining for the lesson."""

    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [
            {"kind": "entity", "subtype": "species_instance",
             "value": "猴子", "importance": 2},
            {"kind": "object", "subtype": "scene_prop",
             "value": "长尾巴", "importance": 1},
        ],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "constraints": [
            {"kind": "entity", "subtype": "species_instance",
             "value": "猴子", "importance": 2},
        ],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.8, "embedding_score": 0.8, "precision_signal": True},
        threshold=0.63,
    )

    assert result["decision"] == "llm_review"
    assert result["reason"] == "target_narrative_undercoverage"
    assert any(item.get("value") == "长尾巴" for item in result["review_items"])


# ---- background cross-theme color_temperature filter (建议 4) ----


def _bg(theme, color_temp):
    asset = {
        "asset_kind": "background",
        "theme": theme,
        "aspect_ratio": "16:9",
    }
    if color_temp is not None:
        asset["color_temperature"] = color_temp
    return asset


def test_background_cross_theme_warm_cool_rejected():
    """Regression for 《秋天的怀念》(暖) reused for 《秋天的雨》(冷):
    score above threshold but color temperature conflicts — must reject."""

    target = _bg("三年级语文《秋天的雨》课文教学", "冷")
    candidate = _bg("七年级语文《秋天的怀念》课文教学", "暖")

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.42, "embedding_score": 0.55},
        threshold=0.38,
    )

    assert result["decision"] == "reject"
    assert result["reason"] == "background_color_temperature_conflict"


def test_background_cross_theme_neutral_not_rejected():
    """Neutral on either side is not a conflict — only warm vs cool gets
    filtered. Neutral backgrounds remain reusable across themes."""

    target = _bg("Topic A", "中性")
    candidate = _bg("Topic B", "暖")

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.42, "embedding_score": 0.55},
        threshold=0.38,
    )

    assert result["decision"] == "full_match"
    assert result["reason"] == "background_score_above_threshold"


def test_background_cross_theme_missing_temperature_not_rejected():
    """Backgrounds without color_temperature metadata should not be
    blocked — the filter only fires on explicit warm↔cool clash."""

    target = _bg("Topic A", None)
    candidate = _bg("Topic B", "暖")

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.42, "embedding_score": 0.55},
        threshold=0.38,
    )

    assert result["decision"] == "full_match"
    assert result["reason"] == "background_score_above_threshold"


def test_role_hardcap_doc_and_code_in_sync():
    """The ROLE_HARDCAP_TERMS code constant and its mirror in
    metadata_rules.md must enumerate exactly the same words. Either side
    drifting silently would mean the LLM sees different rules than the
    code enforces — exactly the kind of double-source-of-truth bug the
    refactor is trying to prevent. If you change one, change both."""

    import re
    from pathlib import Path
    from edupptx.materials.reuse_policy import ROLE_HARDCAP_TERMS

    doc_path = Path(__file__).resolve().parent.parent / (
        "edupptx/materials/Reference/ai_image_reuse_metadata_rules.md"
    )
    doc = doc_path.read_text(encoding="utf-8")
    block = re.search(
        r"### 角色/亲缘/职业硬性兜底词表.*?```\n(.+?)```",
        doc,
        re.S,
    )
    assert block is not None, "metadata_rules.md missing 角色词表 block"
    raw = block.group(1)
    # Drop the 亲缘称谓 / 职业角色 / 泛类指代 section labels (label + ：)
    cleaned = re.sub(r"[一-鿿]+：", "", raw)
    doc_terms = {w for w in cleaned.split() if w}

    assert doc_terms == set(ROLE_HARDCAP_TERMS), (
        f"ROLE_HARDCAP drift detected:\n"
        f"  code only: {sorted(set(ROLE_HARDCAP_TERMS) - doc_terms)}\n"
        f"  doc only : {sorted(doc_terms - set(ROLE_HARDCAP_TERMS))}"
    )


def test_background_same_theme_warm_cool_ignored():
    """Within the same theme, color temperature can legitimately vary
    (e.g. day vs evening scenes of the same lesson). The filter applies
    only to cross-theme reuse."""

    target = _bg("Same Topic", "冷")
    candidate = _bg("Same Topic", "暖")

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.5, "embedding_score": 0.5},
        threshold=0.38,
    )

    assert result["decision"] == "full_match"
    assert result["reason"] == "background_score_above_threshold"

