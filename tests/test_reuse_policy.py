from edupptx.materials.reuse_policy import (
    evaluate_aspect_transform,
    evaluate_reuse_filter,
    normalize_reuse_policy_fields,
    reuse_threshold_for_target,
)


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


def test_normalize_reuse_policy_preserves_strict_structured_constraints():
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

    assert policy["reuse_level"] == "strict"
    assert policy["generic_support_allowed"] is False


def test_normalize_reuse_policy_keeps_medium_categories_threshold_based_without_high_risk_constraints():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "strict",
            "asset_category": "concept_scene",
            "reuse_risk": {
                "readable_knowledge": {"required": True, "evidence": ["ordinary concept explanation"]},
                "unique_referent": {"required": True, "evidence": ["ordinary subject needed"]},
                "exact_relation": {"required": False, "evidence": []},
            },
            "core_constraints": [
                {"kind": "entity", "value": "visible subject", "exact": True, "hard": True},
                {"kind": "action", "value": "visible action", "exact": True, "hard": True},
            ],
            "generic_support_allowed": False,
        }
    )

    assert policy["reuse_level"] == "medium"
    assert policy["generic_support_allowed"] is True
    assert policy["core_constraints"] == []


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


def test_normalize_reuse_policy_clears_constraints_for_non_strict_assets():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "medium",
            "asset_category": "content_specific",
            "core_constraints": [{"kind": "entity", "value": "ordinary subject", "exact": False}],
        }
    )

    assert policy["reuse_level"] == "medium"
    assert policy["core_constraints"] == []


def test_normalize_reuse_policy_infers_strict_for_specific_story_event():
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

    assert policy["reuse_level"] == "strict"
    assert policy["generic_support_allowed"] is False
    assert policy["core_constraints"][0]["kind"] == "relation"
    assert "鲤鱼妈妈" in policy["core_constraints"][0]["value"]


def test_normalize_reuse_policy_infers_strict_for_anchored_character_state():
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

    assert policy["reuse_level"] == "strict"
    assert {item["kind"] for item in policy["core_constraints"]} == {"relation", "emotion"}


def test_normalize_reuse_policy_keeps_decorative_character_action_medium():
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

    assert policy["reuse_level"] == "medium"
    assert policy["core_constraints"] == []


def test_normalize_reuse_policy_keeps_strict_when_reuse_risk_requires_exactness():
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

    assert policy["reuse_level"] == "strict"
    assert policy["generic_support_allowed"] is False


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

    result = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.55}, threshold=0.5)

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
    strict = reuse_threshold_for_target(
        {
            "asset_kind": "page_image",
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "core_constraints": [{"kind": "math", "value": "x+2=5", "exact": True}],
        }
    )

    assert learning == generic_tool == concept_scene == 0.40
    assert strict == 0.66


def test_strict_without_constraints_falls_back_to_similarity_threshold():
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

    accepted = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.7}, threshold=0.6)
    rejected = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.5}, threshold=0.6)

    assert accepted["decision"] == "full_match"
    assert accepted["reason"] == "strict_unconstrained_similarity_match"
    assert rejected["decision"] == "reject"
    assert rejected["reason"] == "strict_similarity_below_threshold"


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
        "asset_category": "character_action",
        "core_constraints": [
            {"kind": "entity", "value": "specific subject", "exact": True, "hard": True},
            {"kind": "action", "value": "specific action", "exact": True, "hard": True},
        ],
    }

    result = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.9}, threshold=0.5)

    assert result["decision"] == "llm_review"
    assert result["reason"] == "strict_core_constraints_require_llm_review"
    assert {item["kind"] for item in result["review_items"]} == {"entity", "action"}


def test_strict_candidate_accepts_when_target_covers_structured_constraints():
    target = {
        "asset_kind": "page_image",
        "reuse_level": "medium",
        "asset_category": "character_action",
        "core_constraints": [
            {"kind": "entity", "value": "specific subject", "exact": True, "hard": True},
            {"kind": "action", "value": "specific action", "exact": True, "hard": True},
        ],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_level": "strict",
        "asset_category": "character_action",
        "core_constraints": [
            {"kind": "entity", "value": "specific subject", "exact": True, "hard": True},
            {"kind": "action", "value": "specific action", "exact": True, "hard": True},
        ],
    }

    result = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.9}, threshold=0.5)

    assert result["decision"] == "full_match"
    assert result["reason"] == "strict_core_constraints_covered"


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

    result = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.9}, threshold=0.6)

    assert result["decision"] == "full_match"
    assert result["reason"] == "strict_core_constraints_covered"


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
