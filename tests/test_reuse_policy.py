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

    result = evaluate_reuse_filter(target, candidate, {"keyword_score": 0.9}, threshold=0.6)

    assert result["decision"] == "reject"
    assert result["reason"] == "candidate_core_constraints_conflict"
    assert result["conflicts"][0]["kind"] == "text"


def test_reuse_policy_downgrades_generic_tool_for_strict_content_target():
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

    assert result["decision"] == "generic_support"
    assert result["reason"] == "strict_target_candidate_only_supports_context"
    assert result["missing"] == [{"kind": "math", "value": "AB=AC", "exact": True}]


def test_normalize_reuse_policy_downgrades_soft_semantic_constraints_without_strict_risk():
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "reuse_level": "strict",
            "asset_category": "concept_scene",
            "core_constraints": [
                {"kind": "entity", "value": "visible subject", "exact": True},
                {"kind": "action", "value": "visible action", "exact": True},
            ],
            "generic_support_allowed": False,
        }
    )

    assert policy["reuse_level"] == "medium"
    assert policy["generic_support_allowed"] is True


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
    assert result["reason"] == "medium_semantic_match"


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
    loose = reuse_threshold_for_target(
        {
            "asset_kind": "page_image",
            "reuse_level": "loose",
            "asset_category": "learning_behavior",
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

    assert loose == 0.37
    assert strict == 0.66


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
