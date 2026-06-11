from edupptx.materials.ai_image_asset_db import (
    MAX_LLM_REVIEWS_PER_QUERY,
    REUSE_REVIEW_ACCEPT_SCORE_THRESHOLD,
    _llm_review_priority,
    _normalize_reuse_review_score_response,
    _reuse_review_accept_score_threshold,
)


def _target(level):
    return {"asset_kind": "page_image", "strict_reuse_group": level}


def test_single_threshold_regardless_of_category():
    assert REUSE_REVIEW_ACCEPT_SCORE_THRESHOLD == 0.60
    for grp in (
        "C01_irreplaceable_entity_event_action",
        "C02_generic_subject_object",
        "C03_scene_decor_container",
    ):
        assert _reuse_review_accept_score_threshold(_target(grp)) == 0.60


def test_transform_reject_still_caps_at_one():
    pr = {"transform_policy": {"decision": "reject"}}
    assert (
        _reuse_review_accept_score_threshold(
            _target("C02_generic_subject_object"),
            policy_result=pr,
        )
        == 1.0
    )


def test_priority_orders_by_embedding_first():
    a = {"score_details": {"policy_score": 0.30, "embedding_score": 0.80}}
    b = {"score_details": {"policy_score": 0.50, "embedding_score": 0.40}}
    assert _llm_review_priority(a) > _llm_review_priority(b)


def test_review_budget_raised():
    assert MAX_LLM_REVIEWS_PER_QUERY == 5


def test_minimal_response_parses_without_constraints():
    r = _normalize_reuse_review_score_response(
        {"score": 0.72, "brief_reason": "同主体不同风格"},
        accept_threshold=0.60,
    )
    assert r["decision"] == "accept"
    assert r["score"] == 0.72
    assert r["threshold"] == 0.60

    r2 = _normalize_reuse_review_score_response({"score": 0.55}, accept_threshold=0.60)
    assert r2["decision"] == "reject"
