from edupptx.materials.ai_image_asset_db import (
    HYBRID_BM25_WEIGHT,
    HYBRID_EMBEDDING_WEIGHT,
    HYBRID_SUBSTRING_WEIGHT,
    _candidate_policy_score,
)
from edupptx.materials.reuse_policy import T_DIRECT, T_GAP, T_REJECT, decide_reuse


def _candidate(score_details):
    return {"asset_id": "asset-1", "score_details": score_details}


def test_phase4_policy_calibration_constants():
    assert HYBRID_BM25_WEIGHT == 0.25
    assert HYBRID_EMBEDDING_WEIGHT == 0.55
    assert HYBRID_SUBSTRING_WEIGHT == 0.20
    assert round(HYBRID_BM25_WEIGHT + HYBRID_EMBEDDING_WEIGHT + HYBRID_SUBSTRING_WEIGHT, 4) == 1.0
    assert T_DIRECT == 0.75
    assert T_REJECT == 0.35
    assert T_GAP == 0.02


def test_multi_signal_embedding_match_reaches_direct():
    candidate = _candidate(
        {
            "keyword_score": 0.50,
            "embedding_score": 0.90,
            "substring_score": 0.80,
        }
    )
    score = _candidate_policy_score(candidate)

    assert score >= T_DIRECT
    assert (
        decide_reuse(
            [{"asset_id": "asset-1", "policy_score": score}],
        )["decision"]
        == "direct_reuse"
    )


def test_close_high_policy_scores_stay_in_review():
    assert (
        decide_reuse(
            [
                {"asset_id": "asset-1", "policy_score": 0.82},
                {"asset_id": "asset-2", "policy_score": 0.81},
            ],
        )["decision"]
        == "llm_review"
    )


def test_embedding_only_weak_text_match_stays_in_review():
    candidate = _candidate(
        {
            "keyword_score": 0.03,
            "embedding_score": 0.80,
            "substring_score": 0.10,
        }
    )
    score = _candidate_policy_score(candidate)

    assert T_REJECT <= score < T_DIRECT
    assert (
        decide_reuse(
            [{"asset_id": "asset-1", "policy_score": score}],
        )["decision"]
        == "llm_review"
    )
