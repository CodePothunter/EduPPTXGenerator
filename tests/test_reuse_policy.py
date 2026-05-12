from edupptx.materials.reuse_policy import (
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
