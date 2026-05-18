from edupptx.materials.ai_image_asset_db import _reuse_acceptance_reason
from edupptx.materials.reuse_policy import (
    MEDIUM_EMBEDDING_REVIEW_THRESHOLD,
    SCORE_DERIVED_REUSE_THRESHOLDS,
    STRICT_EMBEDDING_REVIEW_THRESHOLD,
    derive_reuse_policy_from_scores,
    evaluate_aspect_transform,
    evaluate_reuse_filter,
    normalize_reuse_policy_fields,
    normalize_reuse_score_fields,
    reuse_threshold_for_target,
)


def test_reuse_policy_derives_strict_metadata_from_scores():
    asset = {
        "asset_kind": "page_image",
        "reuse_scores": {
            "strict_score": SCORE_DERIVED_REUSE_THRESHOLDS["strict_score"],
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
                    "importance_score": SCORE_DERIVED_REUSE_THRESHOLDS["hard_constraint_score"],
                    "exactness_score": SCORE_DERIVED_REUSE_THRESHOLDS["exact_constraint_score"],
                }
            ],
        },
    }

    derived = derive_reuse_policy_from_scores(asset)
    policy = normalize_reuse_policy_fields(asset)

    assert derived["reuse_level"] == "strict"
    assert derived["asset_category"] == "content_specific"
    assert derived["generic_support_allowed"] is False
    assert derived["reuse_risk"]["readable_knowledge"]["required"] is True
    assert policy["core_constraints"] == [{"kind": "text", "value": "character: bi", "exact": True, "hard": True}]


def test_reuse_policy_derives_loose_metadata_without_core_constraints():
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
    assert policy["asset_category"] == "concept_scene"
    assert policy["generic_support_allowed"] is True
    assert policy["core_constraints"] == []


def test_reuse_policy_derives_medium_when_visual_constraints_need_filter():
    asset = {
        "asset_kind": "page_image",
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
                    "importance_score": SCORE_DERIVED_REUSE_THRESHOLDS["hard_constraint_score"],
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
    assert policy["core_constraints"] == [
        {"kind": "entity", "value": "cartoon tadpole", "exact": False, "aliases": ["tadpole larva"], "hard": True}
    ]


def test_dimension_scores_drive_strict_without_legacy_strict_score():
    asset = {
        "asset_kind": "page_image",
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

    scores = normalize_reuse_score_fields(asset["reuse_scores"])
    policy = normalize_reuse_policy_fields(asset)

    assert scores["factual_risk_score"] == 0.92
    assert scores["reuse_specificity_score"] == 0.92
    assert policy["reuse_level"] == "strict"
    assert policy["core_constraints"] == [{"kind": "text", "value": "character: bi", "exact": True, "hard": True}]


def test_dimension_scores_drive_medium_visual_guard_without_strict_score():
    asset = {
        "asset_kind": "page_image",
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

    scores = normalize_reuse_score_fields(asset["reuse_scores"])
    policy = normalize_reuse_policy_fields(asset)

    assert scores["visual_guard_score"] >= SCORE_DERIVED_REUSE_THRESHOLDS["visual_guard_score"]
    assert scores["reuse_specificity_score"] == scores["visual_guard_score"]
    assert scores["factual_risk_score"] == 0.0
    assert policy["reuse_level"] == "medium"
    assert {item["kind"] for item in policy["core_constraints"]} == {"entity", "action"}


def test_high_visual_specificity_without_factual_risk_stays_medium():
    asset = {
        "asset_kind": "page_image",
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

    scores = normalize_reuse_score_fields(asset["reuse_scores"])
    policy = normalize_reuse_policy_fields(asset)

    assert scores["reuse_specificity_score"] >= SCORE_DERIVED_REUSE_THRESHOLDS["strict_specificity_score"]
    assert scores["factual_risk_score"] == 0.0
    assert policy["reuse_level"] == "medium"
    assert {item["kind"] for item in policy["core_constraints"]} == {"entity", "action", "emotion"}


def test_dimension_scores_keep_pure_emotion_expression_loose():
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
    assert policy["core_constraints"] == []


def test_strict_high_embedding_candidate_enters_review_even_when_keyword_score_is_zero():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "core_constraints": [{"kind": "text", "value": "character: bi", "exact": True}],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "core_constraints": [{"kind": "text", "value": "character: bi", "exact": True}],
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
        "core_constraints": [],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "core_constraints": [],
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
        "core_constraints": [],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "core_constraints": [],
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
        "core_constraints": [{"kind": "subject", "value": "小蝌蚪", "importance": "high"}],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "core_constraints": [{"kind": "subject", "value": "小蝌蚪", "importance": "high"}],
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
        "core_constraints": [{"kind": "subject", "value": "小蝌蚪", "importance": "high"}],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "core_constraints": [{"kind": "subject", "value": "小蝌蚪", "importance": "high"}],
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
        "core_constraints": [],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "loose",
            "asset_category": "learning_behavior",
            "core_constraints": [],
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
        "core_constraints": [],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "core_constraints": [],
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
        "core_constraints": [],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "core_constraints": [],
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
        "core_constraints": [],
    }
    candidate = {
        "asset": {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "core_constraints": [],
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
            "core_constraints": [],
        },
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "core_constraints": [],
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
            "core_constraints": [],
        },
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "core_constraints": [],
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
            "core_constraints": [
                {"kind": "text", "value": "character: bi", "exact": True},
                {"kind": "invalid", "value": "ignored", "exact": True},
                {"kind": "math", "value": "", "exact": True},
            ],
            "generic_support_allowed": True,
        }
    )

    assert policy["reuse_level"] == "strict"
    assert policy["asset_category"] == "content_specific"
    assert policy["generic_support_allowed"] is False
    assert policy["core_constraints"] == [{"kind": "text", "value": "character: bi", "exact": True}]


def test_reuse_policy_rejects_strict_text_conflict():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "core_constraints": [{"kind": "text", "value": "character: bi", "exact": True}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "core_constraints": [{"kind": "text", "value": "character: bei", "exact": True}],
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
    assert result["reason"] == "strict_core_constraints_conflict"
    assert result["conflicts"][0]["kind"] == "text"


def test_reuse_policy_rejects_generic_tool_for_strict_content_target():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "core_constraints": [{"kind": "math", "value": "AB=AC", "exact": True}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "generic_tool",
        "core_constraints": [],
        "generic_support_allowed": True,
    }

    result = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.75}, threshold=0.6)

    assert result["decision"] == "llm_review"
    assert result["reason"] == "strict_core_constraints_require_llm_review"
    assert result["review_items"][0]["kind"] == "math"
    assert result["review_items"][0]["reason"] == "missing_same_kind"


def test_normalize_reuse_policy_downgrades_visual_constraints_to_medium():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "core_constraints": [
                {"kind": "entity", "value": "visible subject", "exact": True},
                {"kind": "action", "value": "visible action", "exact": True},
            ],
            "generic_support_allowed": False,
        }
    )

    assert policy["reuse_level"] == "medium"
    assert policy["generic_support_allowed"] is False


def test_normalize_reuse_policy_keeps_medium_categories_threshold_based_without_high_risk_constraints():
    expected_constraints = [
        {"kind": "entity", "value": "visible subject", "exact": False, "hard": True},
        {"kind": "action", "value": "visible action", "exact": False, "hard": True},
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
            "core_constraints": expected_constraints,
            "generic_support_allowed": False,
        }
    )

    assert policy["reuse_level"] == "medium"
    assert policy["generic_support_allowed"] is False
    assert policy["core_constraints"] == expected_constraints


def test_normalize_reuse_policy_preserves_high_risk_constraints_in_medium_categories():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "generic_tool",
            "core_constraints": [{"kind": "text", "value": "character: bi", "exact": True}],
            "generic_support_allowed": True,
        }
    )

    assert policy["reuse_level"] == "strict"
    assert policy["generic_support_allowed"] is False
    assert policy["core_constraints"] == [{"kind": "text", "value": "character: bi", "exact": True}]


def test_normalize_reuse_policy_preserves_constraints_for_medium_assets():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "content_specific",
            "core_constraints": [{"kind": "entity", "value": "ordinary subject", "exact": False}],
        }
    )

    assert policy["reuse_level"] == "medium"
    assert policy["core_constraints"] == [{"kind": "entity", "value": "ordinary subject", "exact": False}]


def test_normalize_reuse_policy_infers_medium_for_specific_story_event():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "content_specific",
            "content_prompt": "小蝌蚪和鲤鱼妈妈对话",
            "context_summary": "呈现课文中间情节，辅助学生梳理故事脉络",
            "teaching_intent": "帮助学生理解故事情节节点",
            "core_constraints": [],
        }
    )

    assert policy["reuse_level"] == "medium"
    assert policy["generic_support_allowed"] is False
    assert policy["core_constraints"][0]["kind"] == "relation"
    assert "鲤鱼妈妈" in policy["core_constraints"][0]["value"]


def test_normalize_reuse_policy_infers_medium_for_anchored_character_state():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "content_prompt": "青年生气摔东西的场景，情绪激动",
            "context_summary": "作为课文情节时间线的插图，展现主人公暴怒状态",
            "teaching_intent": "帮助学生理解人物情绪转变",
            "core_constraints": [],
        }
    )

    assert policy["reuse_level"] == "medium"
    assert {item["kind"] for item in policy["core_constraints"]} == {"relation", "emotion"}


def test_normalize_reuse_policy_makes_unconstrained_decorative_character_action_loose():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "content_prompt": "卡通小蝌蚪举着小旗子引导学习路线",
            "context_summary": "用于目录页的装饰插图，引导学生了解学习环节顺序",
            "teaching_intent": "降低目录页的枯燥感",
            "core_constraints": [],
        }
    )

    assert policy["reuse_level"] == "loose"
    assert policy["core_constraints"] == []


def test_normalize_reuse_policy_keeps_hard_visual_subject_in_medium_character_action():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "content_prompt": "卡通小蝌蚪举着小旗子带路的插图",
            "core_constraints": [
                {"kind": "entity", "value": "小蝌蚪", "exact": False, "hard": True, "aliases": ["蝌蚪幼体"]}
            ],
        }
    )

    assert policy["reuse_level"] == "medium"
    assert policy["core_constraints"] == [
        {"kind": "entity", "value": "小蝌蚪", "exact": False, "aliases": ["蝌蚪幼体"], "hard": True}
    ]


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
            "core_constraints": [{"kind": "entity", "value": "specific identity", "exact": True, "hard": True}],
        }
    )

    assert policy["reuse_level"] == "medium"
    assert policy["generic_support_allowed"] is False


def test_literary_family_closing_scene_is_medium_not_strict():
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
                    {"kind": "entity", "value": "母亲", "importance_score": 0.8, "exactness_score": 0.6},
                    {"kind": "entity", "value": "孩子", "importance_score": 0.8, "exactness_score": 0.6},
                    {"kind": "emotion", "value": "温暖", "importance_score": 0.75, "exactness_score": 0.4},
                ],
            },
        }
    )

    assert policy["reuse_level"] == "medium"
    assert {item["kind"] for item in policy["core_constraints"]} == {"entity", "emotion"}


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
            "core_constraints": [{"kind": "emotion", "value": "喜出望外", "exact": False, "hard": True}],
        }
    )

    assert policy["reuse_level"] == "loose"
    assert policy["core_constraints"] == []


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
            "core_constraints": [
                {"kind": "entity", "value": "男孩", "exact": False, "hard": True},
                {"kind": "action", "value": "摔东西", "exact": False, "hard": True},
                {"kind": "action", "value": "拒绝出门", "exact": True, "hard": True},
                {"kind": "emotion", "value": "暴怒", "exact": False, "hard": True},
            ],
        }
    )

    assert policy["reuse_level"] == "medium"
    assert all(item["kind"] != "relation" for item in policy["core_constraints"])


def test_medium_semantic_reuse_accepts_embedding_signal_and_missing_soft_constraints():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "core_constraints": [
            {"kind": "entity", "value": "visible subject", "exact": True},
            {"kind": "action", "value": "visible action", "exact": True},
        ],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "core_constraints": [],
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
            "core_constraints": [
                {"kind": "entity", "value": "小蝌蚪", "exact": False, "hard": True, "aliases": ["蝌蚪幼体"]}
            ],
        },
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "core_constraints": [{"kind": "entity", "value": "小松鼠", "exact": False, "hard": True}],
        },
        {"keyword_score": 0.92, "embedding_score": 0.91, "accepted_by": "bm25_threshold"},
        threshold=0.40,
    )

    assert result["decision"] == "reject"
    assert result["reason"] == "medium_core_constraints_conflict"


def test_medium_hard_core_subject_missing_requires_llm_review():
    result = evaluate_reuse_filter(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "core_constraints": [
                {"kind": "entity", "value": "小蝌蚪", "exact": False, "hard": True, "aliases": ["蝌蚪幼体"]}
            ],
        },
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "core_constraints": [],
        },
        {"keyword_score": 0.92, "embedding_score": 0.91, "accepted_by": "bm25_threshold"},
        threshold=0.40,
    )

    assert result["decision"] == "llm_review"
    assert result["reason"] == "medium_core_constraints_require_llm_review"


def test_medium_hard_core_subject_match_allows_threshold_accept():
    result = evaluate_reuse_filter(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "core_constraints": [
                {"kind": "entity", "value": "小蝌蚪", "exact": False, "hard": True, "aliases": ["蝌蚪幼体"]}
            ],
        },
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "character_action",
            "core_constraints": [{"kind": "entity", "value": "蝌蚪幼体", "exact": False, "hard": True}],
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
        "core_constraints": [],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "core_constraints": [],
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
        "core_constraints": [{"kind": "action", "value": "visible action", "exact": True}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "core_constraints": [{"kind": "action", "value": "visible action", "exact": True}],
    }

    result = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.55, "embedding_score": 0.7}, threshold=0.5)

    assert result["decision"] == "full_match"


def test_reuse_threshold_uses_category_and_level():
    learning = reuse_threshold_for_target(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "learning_behavior",
            "core_constraints": [],
        }
    )
    generic_tool = reuse_threshold_for_target(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "generic_tool",
            "core_constraints": [],
        }
    )
    concept_scene = reuse_threshold_for_target(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "core_constraints": [],
        }
    )
    loose_concept_scene = reuse_threshold_for_target(
        {
            "asset_kind": "page_image",
            "reuse_level": "loose",
            "asset_category": "concept_scene",
            "core_constraints": [],
        }
    )
    strict = reuse_threshold_for_target(
        {
            "asset_kind": "page_image",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "core_constraints": [{"kind": "math", "value": "x+2=5", "exact": True}],
        }
    )

    assert learning == generic_tool == concept_scene == 0.40
    assert loose_concept_scene == concept_scene
    assert strict == 0.66


def test_unconstrained_specific_metadata_falls_back_to_similarity_threshold():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "reuse_risk": {"unique_referent": {"required": True, "evidence": ["specific content required"]}},
        "core_constraints": [],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "reuse_risk": {"unique_referent": {"required": True, "evidence": ["specific content required"]}},
        "core_constraints": [],
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
        "core_constraints": [{"kind": "text", "value": "character: bi", "exact": True, "hard": True}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "core_constraints": [{"kind": "text", "value": "character: bi", "exact": True, "hard": True}],
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
    assert result["reason"] == "strict_embedding_high_keyword_below_threshold"


def test_medium_score_match_requires_llm_when_embedding_below_auto_accept_floor():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "generic_diagram",
        "core_constraints": [],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "generic_diagram",
        "core_constraints": [],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.55, "embedding_score": 0.0, "accepted_by": "bm25_threshold"},
        threshold=0.48,
    )

    assert result["decision"] == "llm_review"
    assert result["reason"] == "embedding_below_auto_accept_floor"
    assert result["review_items"][0]["threshold"] == 0.6


def test_strict_score_match_requires_llm_when_embedding_below_auto_accept_floor():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "reuse_risk": {"unique_referent": {"required": True, "evidence": ["specific content required"]}},
        "core_constraints": [],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "reuse_risk": {"unique_referent": {"required": True, "evidence": ["specific content required"]}},
        "core_constraints": [],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.7, "embedding_score": 0.2, "accepted_by": "bm25_threshold"},
        threshold=0.66,
    )

    assert result["decision"] == "llm_review"
    assert result["reason"] == "embedding_below_auto_accept_floor"
    assert result["review_items"][0]["threshold"] == 0.58


def test_strict_candidate_requires_target_to_cover_candidate_constraints():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "concept_scene",
        "core_constraints": [],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "core_constraints": [
            {"kind": "text", "value": "character: bi", "exact": True, "hard": True},
        ],
    }

    result = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.9, "embedding_score": 0.7}, threshold=0.5)

    assert result["decision"] == "llm_review"
    assert result["reason"] == "strict_core_constraints_require_llm_review"
    assert {item["kind"] for item in result["review_items"]} == {"text"}


def test_strict_candidate_accepts_when_target_covers_structured_constraints():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "character_action",
        "core_constraints": [
            {"kind": "text", "value": "character: bi", "exact": True, "hard": True},
        ],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "core_constraints": [
            {"kind": "text", "value": "character: bi", "exact": True, "hard": True},
        ],
    }

    result = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.9, "embedding_score": 0.7}, threshold=0.5)

    assert result["decision"] == "llm_review"
    assert result["reason"] == "strict_core_constraints_require_llm_review"


def test_strict_visual_constraint_accepts_alias_match():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "core_constraints": [{"kind": "action", "value": "read aloud", "exact": False, "hard": True, "aliases": ["朗读"]}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "core_constraints": [{"kind": "action", "value": "朗读", "exact": False, "hard": True}],
    }

    result = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.9, "embedding_score": 0.7}, threshold=0.6)

    assert result["decision"] == "full_match"
    assert result["reason"] == "medium_similarity_threshold_match"


def test_strict_visual_constraint_accepts_high_embedding_match():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "core_constraints": [{"kind": "action", "value": "read aloud", "exact": False, "hard": True}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "core_constraints": [{"kind": "action", "value": "oral reading", "exact": False, "hard": True}],
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


def test_strict_relation_constraint_requires_llm_after_high_embedding_match():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "core_constraints": [{"kind": "relation", "value": "subject acts on object", "exact": False, "hard": True}],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "content_specific",
        "core_constraints": [{"kind": "relation", "value": "actor performs action on object", "exact": False, "hard": True}],
    }

    result = evaluate_reuse_filter(
        target,
        candidate,
        {
            "keyword_score": 0.9,
            "embedding_score": 0.9,
            "constraint_embedding_scores": [
                {
                    "kind": "relation",
                    "target": "subject acts on object",
                    "candidate": "actor performs action on object",
                    "score": 0.94,
                }
            ],
        },
        threshold=0.6,
    )

    assert result["decision"] == "llm_review"
    assert result["review_items"][0]["reason"] == "embedding_high"


def test_aspect_transform_rejects_strict_content_large_mismatch():
    result = evaluate_aspect_transform(
        {
            "asset_kind": "page_image",
            "aspect_ratio": "16:9",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "core_constraints": [{"kind": "text", "value": "character: bi", "exact": True}],
        },
        {
            "asset_kind": "page_image",
            "aspect_ratio": "3:4",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "core_constraints": [{"kind": "text", "value": "character: bi", "exact": True}],
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
            "core_constraints": [],
        },
        {
            "asset_kind": "page_image",
            "aspect_ratio": "1:1",
            "reuse_level": "medium",
            "asset_category": "concept_scene",
            "core_constraints": [],
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
            "core_constraints": [{"kind": "text", "value": "character: bi", "exact": True}],
        },
        {
            "asset_kind": "page_image",
            "aspect_ratio": "1:1",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "core_constraints": [{"kind": "text", "value": "character: bi", "exact": True}],
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
            "core_constraints": [{"kind": "entity", "value": "visible subject", "exact": True}],
        },
        {
            "asset_kind": "page_image",
            "aspect_ratio": "1:1",
            "reuse_level": "medium",
            "asset_category": "content_specific",
            "core_constraints": [],
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
            "core_constraints": [],
        },
        {
            "asset_kind": "page_image",
            "aspect_ratio": "1:1",
            "reuse_level": "medium",
            "asset_category": "generic_tool",
            "core_constraints": [],
        },
    )

    assert result["decision"] == "penalize"
    assert result["mode"] == "contain_pad"
    assert result["transform_penalty"] == 0.09


def test_aspect_transform_allows_background_blur_pad():
    result = evaluate_aspect_transform(
        {"asset_kind": "background", "aspect_ratio": "16:9"},
        {"asset_kind": "background", "aspect_ratio": "4:3"},
    )

    assert result["decision"] == "penalize"
    assert result["mode"] == "blur_pad"
    assert result["transform_penalty"] == 0.06
