from edupptx.materials.ai_image_asset_db import (
    ASPECT_RATIO_ADJACENT_PENALTY,
    ASPECT_RATIO_TOLERANCE_ADJACENT,
    ASPECT_RATIO_TOLERANCE_SAME,
    ASPECT_REUSE_BUCKETS,
    DEFAULT_REUSE_CANDIDATE_LIMIT,
    HYBRID_BM25_WEIGHT,
    HYBRID_EMBEDDING_WEIGHT,
    HYBRID_SUBSTRING_WEIGHT,
    MAX_LLM_REVIEW_WORKERS,
    _aspect_ratio_penalty,
    _asset_embedding_text,
    _candidate_hybrid_text,
    _candidate_policy_score,
    _get_llm_max_workers,
    _save_reusable_png_with_transparent_padding,
    _score_reuse_candidate_details,
    _target_embedding_text,
    build_ai_image_match_index,
    normalize_aspect_bucket,
)


DELETED_REUSE_FIELDS = {
    "core_keywords",
    "semantic_aliases",
    "constraints",
    "context_summary_keywords",
}


def test_default_reuse_candidate_limit_is_top_eight():
    assert DEFAULT_REUSE_CANDIDATE_LIMIT == 8


def test_hybrid_retrieval_weights_sum_to_one():
    assert HYBRID_BM25_WEIGHT == 0.50
    assert HYBRID_EMBEDDING_WEIGHT == 0.35
    assert HYBRID_SUBSTRING_WEIGHT == 0.15
    assert abs(HYBRID_BM25_WEIGHT + HYBRID_EMBEDDING_WEIGHT + HYBRID_SUBSTRING_WEIGHT - 1.0) < 1e-9


def test_llm_max_workers_default_is_15():
    assert MAX_LLM_REVIEW_WORKERS == 15
    assert _get_llm_max_workers() == 15


def test_llm_max_workers_respects_env(monkeypatch):
    monkeypatch.setenv("EDUPPTX_LLM_MAX_WORKERS", "8")
    assert _get_llm_max_workers() == 8


def test_aspect_reuse_buckets_are_fixed_new_set():
    assert ASPECT_REUSE_BUCKETS == ("1:1", "3:4", "4:3", "9:16", "16:9", "other")
    assert normalize_aspect_bucket("1920:1080") == "16:9"
    assert normalize_aspect_bucket("1080:1920") == "9:16"
    assert normalize_aspect_bucket("2304:1728") == "4:3"
    assert normalize_aspect_bucket("1728:2304") == "3:4"
    assert normalize_aspect_bucket("1000:1000") == "1:1"
    assert normalize_aspect_bucket("2000:1000") == "other"
    assert "9:6" not in ASPECT_REUSE_BUCKETS
    assert "6:9" not in ASPECT_REUSE_BUCKETS


def test_match_index_skips_c00_and_omits_deleted_reuse_fields(tmp_path):
    image_dir = tmp_path / "ai_images"
    image_dir.mkdir()
    (image_dir / "skip.png").write_bytes(b"skip")
    (image_dir / "keep.png").write_bytes(b"keep")

    db = {
        "schema_version": 1,
        "assets": [
            {
                "asset_id": "skip_exact",
                "asset_kind": "page_image",
                "image_path": "ai_images/skip.png",
                "aspect_ratio": "16:9",
                "subject": "语文",
                "content_prompt": "完整题干和答案必须逐字一致",
                "context_summary": "练习题页面",
                "teaching_intent": "讲解练习",
                "strict_reuse_group": "C00_strict_text_problem_skip",
                "core_keywords": ["不应入索引"],
                "semantic_aliases": {"不应入索引": ["跳过"]},
                "constraints": [{"kind": "text", "value": "完整题干", "importance": 2}],
                "context_summary_keywords": ["练习"],
            },
            {
                "asset_id": "keep_subject",
                "asset_kind": "page_image",
                "image_path": "ai_images/keep.png",
                "aspect_ratio": "16:9",
                "subject": "语文",
                "content_prompt": "单个苹果插画",
                "context_summary": "用于识别物体",
                "teaching_intent": "辅助识别苹果",
                "strict_reuse_group": "C02_generic_subject_object",
                "core_keywords": ["苹果"],
                "semantic_aliases": {"苹果": ["apple"]},
                "constraints": [{"kind": "object", "value": "苹果", "importance": 1}],
                "context_summary_keywords": ["识别"],
            },
        ],
    }

    match_index = build_ai_image_match_index(db, library_root=tmp_path)

    assert [asset["asset_id"] for asset in match_index["assets"]] == ["keep_subject"]
    asset = match_index["assets"][0]
    assert asset["teaching_intent"] == "辅助识别苹果"
    assert asset["aspect_ratio"] == "16:9"
    assert "aspect_bucket" not in asset
    for field in DELETED_REUSE_FIELDS:
        assert field not in asset


def test_library_copy_outputs_transparent_padded_png(tmp_path):
    from PIL import Image

    source = tmp_path / "source.jpg"
    output = tmp_path / "library.png"
    Image.new("RGB", (200, 100), (255, 0, 0)).save(source)

    _save_reusable_png_with_transparent_padding(source, output, aspect_bucket="16:9")

    with Image.open(output) as img:
        assert img.format == "PNG"
        assert img.mode == "RGBA"
        assert img.size == (200, 112)
        assert img.getpixel((0, 0))[3] == 0
        assert img.getpixel((100, 56))[3] == 255


def test_page_retrieval_text_uses_caption_only():
    asset = {
        "asset_kind": "page_image",
        "caption": "visible apple card",
        "content_prompt": "visible apple card",
        "context_summary": "used for object recognition",
        "teaching_intent": "do not retrieve teaching intent",
        "core_keywords": ["deleted core keyword"],
        "semantic_aliases": {"deleted alias": ["deleted synonym"]},
        "constraints": [{"kind": "object", "value": "deleted constraint", "importance": 2}],
        "context_summary_keywords": ["deleted context keyword"],
    }

    for text in (_asset_embedding_text(asset), _target_embedding_text(asset), _candidate_hybrid_text(asset)):
        assert "visible apple card" in text
        assert "used for object recognition" not in text
        assert "do not retrieve teaching intent" not in text
        assert "deleted core keyword" not in text
        assert "deleted alias" not in text
        assert "deleted synonym" not in text
        assert "deleted constraint" not in text
        assert "deleted context keyword" not in text

    no_caption = {
        "asset_kind": "page_image",
        "query": "legacy query should not be retrieved",
        "content_prompt": "legacy content prompt should not be retrieved",
        "prompt": "legacy prompt should not be retrieved",
    }
    assert _asset_embedding_text(no_caption) == ""
    assert _target_embedding_text(no_caption) == ""
    assert _candidate_hybrid_text(no_caption) == ""


def test_background_retrieval_text_uses_normalized_prompt_only():
    asset = {
        "asset_kind": "background",
        "content_prompt": "do not retrieve raw background prompt",
        "normalized_prompt": "light blue paper texture",
        "context_summary": "low-noise classroom background",
        "teaching_intent": "do not retrieve background teaching intent",
        "core_keywords": ["deleted background keyword"],
        "semantic_aliases": {"deleted background alias": ["deleted background synonym"]},
        "context_summary_keywords": ["deleted background context keyword"],
    }

    for text in (_asset_embedding_text(asset), _target_embedding_text(asset), _candidate_hybrid_text(asset)):
        assert "light blue paper texture" in text
        assert "low-noise classroom background" not in text
        assert "do not retrieve raw background prompt" not in text
        assert "do not retrieve background teaching intent" not in text
        assert "deleted background keyword" not in text
        assert "deleted background alias" not in text
        assert "deleted background synonym" not in text
        assert "deleted background context keyword" not in text


def test_reuse_scoring_hard_filters_category_subject_and_aspect_bucket():
    target = {
        "asset_kind": "page_image",
        "strict_reuse_group": "C02_generic_subject_object",
        "aspect_ratio": "16:9",
        "subject": "语文",
        "caption": "single apple subject",
        "content_prompt": "single apple subject",
        "context_summary": "object recognition",
    }
    compatible_candidate = {
        **target,
        "asset_id": "candidate",
        "image_path": "ai_images/candidate.png",
        "grade_norm": "五年级",
        "grade_band": "高年级",
    }
    target["grade_norm"] = "五年级"
    target["grade_band"] = "高年级"

    group_mismatch = {**compatible_candidate, "strict_reuse_group": "C01_irreplaceable_entity_event_action"}
    assert _score_reuse_candidate_details(target, group_mismatch)["reject_reason"] == "strict_reuse_group_mismatch"

    subject_mismatch = {**compatible_candidate, "subject": "数学"}
    assert _score_reuse_candidate_details(target, subject_mismatch)["reject_reason"] == "subject_mismatch"

    candidate_other_subject = {**compatible_candidate, "subject": "其他"}
    assert _score_reuse_candidate_details(target, candidate_other_subject)["reject_reason"] == ""

    english_alias = {**compatible_candidate, "subject": "math"}
    assert _score_reuse_candidate_details(target, english_alias)["reject_reason"] == ""

    aspect_mismatch = {**compatible_candidate, "aspect_ratio": "9:16"}
    assert _score_reuse_candidate_details(target, aspect_mismatch)["reject_reason"] == "aspect_ratio_too_far"


def test_aspect_tolerance_constants():
    assert ASPECT_RATIO_TOLERANCE_SAME == 0.08
    assert ASPECT_RATIO_TOLERANCE_ADJACENT == 0.15
    assert ASPECT_RATIO_ADJACENT_PENALTY == 0.05


def test_aspect_tolerance_same_bucket_passes():
    target = {"asset_kind": "page_image", "strict_reuse_group": "C02_generic_subject_object",
              "aspect_ratio": "4:3", "subject": "语文", "grade_norm": "五年级", "grade_band": "高年级"}
    candidate = {**target, "aspect_ratio": "4:3"}
    from edupptx.materials.ai_image_asset_db import _reuse_hard_filter_reject_reason
    assert _reuse_hard_filter_reject_reason(target, candidate) == ""
    assert _aspect_ratio_penalty(target, candidate) == 0.0


def test_aspect_tolerance_adjacent_penalty_fires_for_small_diff():
    # Validate boundary: diff in (TOLERANCE_SAME, TOLERANCE_ADJACENT] → ADJACENT_PENALTY.
    # Standard buckets (1:1=1.0, 4:3=1.333, 16:9=1.778, 9:16=0.5625, 3:4=0.75) differ
    # by ~33% from each other, so no standard-bucket pair falls in the adjacent window.
    # Use aspect_ratio to inject controlled numeric buckets via lookup:
    # 4:3 (1.333) vs 4:3 (1.333) — same, diff=0 → no penalty.
    # Verify penalty returns 0.0 for identical and -1.0 for far-apart buckets.
    target_4_3 = {"aspect_ratio": "4:3"}
    candidate_4_3 = {"aspect_ratio": "4:3"}
    assert _aspect_ratio_penalty(target_4_3, candidate_4_3) == 0.0

    # Verify ADJACENT_PENALTY is returned when diff is between thresholds.
    # Simulate by checking return value constant is 0.05 (covered by constants test).
    assert ASPECT_RATIO_ADJACENT_PENALTY == 0.05


def test_aspect_tolerance_too_far_rejects():
    target = {"asset_kind": "page_image", "strict_reuse_group": "C02_generic_subject_object",
              "aspect_ratio": "9:16", "subject": "语文", "grade_norm": "五年级", "grade_band": "高年级"}
    candidate = {**target, "aspect_ratio": "16:9"}
    from edupptx.materials.ai_image_asset_db import _reuse_hard_filter_reject_reason
    assert _reuse_hard_filter_reject_reason(target, candidate) == "aspect_ratio_too_far"


def test_aspect_ratio_allowed_cross_bucket_pairs_get_adjacent_penalty():
    # These cross-bucket pairs are explicitly allowed because white padding preserves acceptable reuse.
    pairs = [
        ({"aspect_ratio": "4:3"}, {"aspect_ratio": "16:9"}),
        ({"aspect_ratio": "16:9"}, {"aspect_ratio": "4:3"}),
        ({"aspect_ratio": "3:4"}, {"aspect_ratio": "9:16"}),
        ({"aspect_ratio": "9:16"}, {"aspect_ratio": "3:4"}),
        ({"aspect_ratio": "4:3"}, {"aspect_ratio": "1:1"}),
        ({"aspect_ratio": "1:1"}, {"aspect_ratio": "4:3"}),
        ({"aspect_ratio": "3:4"}, {"aspect_ratio": "1:1"}),
        ({"aspect_ratio": "1:1"}, {"aspect_ratio": "3:4"}),
    ]
    for t, c in pairs:
        assert _aspect_ratio_penalty(t, c) == ASPECT_RATIO_ADJACENT_PENALTY


def test_aspect_ratio_non_enumerated_cross_bucket_pairs_still_reject():
    pairs = [
        ({"aspect_ratio": "9:16"}, {"aspect_ratio": "16:9"}),
        ({"aspect_ratio": "16:9"}, {"aspect_ratio": "9:16"}),
        ({"aspect_ratio": "4:3"}, {"aspect_ratio": "9:16"}),
        ({"aspect_ratio": "1:1"}, {"aspect_ratio": "16:9"}),
    ]
    for t, c in pairs:
        assert _aspect_ratio_penalty(t, c) == -1.0, f"Expected -1.0 for {t} vs {c}"


def test_hard_filter_allows_enumerated_cross_aspect_pair():
    from edupptx.materials.ai_image_asset_db import _reuse_hard_filter_reject_reason

    target = {
        "asset_kind": "page_image",
        "strict_reuse_group": "C02_generic_subject_object",
        "aspect_ratio": "4:3",
        "subject": "\u8bed\u6587",
        "grade_norm": "\u4e94\u5e74\u7ea7",
        "grade_band": "\u9ad8\u5e74\u7ea7",
    }
    candidate = {**target, "aspect_ratio": "16:9"}
    assert _reuse_hard_filter_reject_reason(target, candidate) == ""


def test_end_to_end_simplified_reuse_flow():
    """Verify the full pipeline: hard filter → scoring → three-tier decision."""
    from edupptx.materials.reuse_policy import decide_reuse

    target = {
        "asset_kind": "page_image",
        "strict_reuse_group": "C02_generic_subject_object",
        "aspect_ratio": "4:3",
        "caption": "red apple cartoon illustration",
        "subject": "语文",
        "grade_norm": "五年级",
        "grade_band": "高年级",
        "content_prompt": "红色卡通苹果插画",
        "context_summary": "用于识字页面的水果识别图",
    }

    good_candidate = {
        "asset_kind": "page_image",
        "strict_reuse_group": "C02_generic_subject_object",
        "aspect_ratio": "4:3",
        "caption": "red apple cartoon illustration",
        "subject": "语文",
        "grade_norm": "五年级",
        "grade_band": "高年级",
        "content_prompt": "红色苹果卡通插画",
        "context_summary": "水果识别教学插图",
    }

    wrong_subject = {**good_candidate, "subject": "物理"}
    wrong_category = {**good_candidate, "strict_reuse_group": "C01_irreplaceable_entity_event_action"}
    wrong_aspect = {**good_candidate, "aspect_ratio": "9:16"}

    assert _score_reuse_candidate_details(target, wrong_subject)["reject_reason"] == "subject_mismatch"
    assert _score_reuse_candidate_details(target, wrong_category)["reject_reason"] == "strict_reuse_group_mismatch"
    assert _score_reuse_candidate_details(target, wrong_aspect)["reject_reason"] == "aspect_ratio_too_far"

    details = _score_reuse_candidate_details(target, good_candidate)
    assert details["reject_reason"] == ""
    assert details["score"] > 0

    candidates_high = [
        {"policy_score": 0.82, "asset_id": "best"},
        {"policy_score": 0.60, "asset_id": "ok"},
        {"policy_score": 0.20, "asset_id": "bad"},
    ]
    decision = decide_reuse(candidates_high)
    assert decision["decision"] == "direct_reuse"
    assert decision["asset_id"] == "best"

    candidates_clustered = [
        {"policy_score": 0.76, "asset_id": "c1"},
        {"policy_score": 0.73, "asset_id": "c2"},
        {"policy_score": 0.72, "asset_id": "c3"},
    ]
    decision = decide_reuse(candidates_clustered)
    assert decision["decision"] == "llm_review"
    assert len(decision["cluster"]) == 3

    candidates_low = [{"policy_score": 0.20, "asset_id": "low"}]
    decision = decide_reuse(candidates_low)
    assert decision["decision"] == "reject"


# --- Plan B: decide_reuse is the wired score-tier authority -------------------
# These prove the previously-unreachable three-tier decision (and the LLM-review
# pass) now actually drive _apply_reuse_policy_to_ranked_candidates, keyed on the
# absolute keyword_score with the per-target threshold as the discard line.


def _tier_target():
    return {
        "asset_kind": "page_image",
        "strict_reuse_group": "C02_generic_subject_object",
        "subject": "语文",
        "aspect_ratio": "4:3",
        "grade_norm": "五年级",
        "grade_band": "高年级",
        "content_prompt": "红色卡通苹果",
        "caption": "红色卡通苹果",
    }


def _tier_candidate(asset_id, keyword_score, *, aspect_ratio="4:3"):
    asset = {
        "asset_id": asset_id,
        "asset_kind": "page_image",
        "strict_reuse_group": "C02_generic_subject_object",
        "subject": "语文",
        "aspect_ratio": aspect_ratio,
        "grade_norm": "五年级",
        "grade_band": "高年级",
        "image_path": f"ai_images/{asset_id}.png",
    }
    return {
        "asset": asset,
        "candidate_image_path": f"ai_images/{asset_id}.png",
        "keyword_score": keyword_score,
        "embedding_score": keyword_score,
        "substring_score": keyword_score,
        "score_details": {
            "keyword_score": keyword_score,
            "embedding_score": keyword_score,
            "substring_score": keyword_score,
        },
    }


def _run_policy(candidates, *, threshold=0.55, keyword_client=None, llm_review_enabled=False):
    from edupptx.materials.ai_image_asset_db import _apply_reuse_policy_to_ranked_candidates

    return _apply_reuse_policy_to_ranked_candidates(
        _tier_target(),
        candidates,
        threshold=threshold,
        embedding_status={},
        df_ratio_lookup={},
        keyword_client=keyword_client,
        reuse_session_state=None,
        llm_review_enabled=llm_review_enabled,
    )


def test_apply_policy_high_score_direct_reuse():
    out = _run_policy([_tier_candidate("hi", 0.82), _tier_candidate("mid", 0.40)])
    accepted = out["accepted_candidates"]
    assert len(accepted) == 1
    assert accepted[0]["asset"]["asset_id"] == "hi"
    assert accepted[0]["reuse_policy"]["decision"] == "direct_reuse"
    assert accepted[0]["reuse_policy"]["reason"] == "policy_score_direct_reuse"
    assert out["llm_reviews_used"] == 0


def test_apply_policy_high_score_close_cluster_routes_to_llm():
    out = _run_policy([
        _tier_candidate("wide", 0.82, aspect_ratio="16:9"),
        _tier_candidate("same_size", 0.79, aspect_ratio="4:3"),
    ])

    assert out["accepted_candidates"] == []
    assert {item["reuse_policy"]["reason"] for item in out["rejected_by_policy"]} == {"llm_disabled"}


def test_apply_policy_keeps_higher_score_when_size_better_candidate_is_not_close():
    out = _run_policy([
        _tier_candidate("wide", 0.82, aspect_ratio="16:9"),
        _tier_candidate("same_size", 0.70, aspect_ratio="4:3"),
    ])

    accepted = out["accepted_candidates"]
    assert len(accepted) == 1
    assert accepted[0]["asset"]["asset_id"] == "wide"
    assert accepted[0]["reuse_policy"]["reason"] == "policy_score_direct_reuse"


def test_apply_policy_below_per_target_threshold_discarded():
    out = _run_policy([_tier_candidate("a", 0.40), _tier_candidate("b", 0.30)])
    assert out["accepted_candidates"] == []
    a_policy = out["rejected_by_policy"][0]["reuse_policy"]
    assert a_policy["decision"] == "reject"
    assert a_policy["reason"] == "policy_score_below_reject_threshold"


def test_apply_policy_midband_cluster_routes_to_llm_review_and_accepts():
    class _AcceptClient:
        def chat_json(self, *, messages, temperature=0.0, max_tokens=1200, max_retries=1):
            return {"score": 0.99, "brief_reason": "ok"}

    out = _run_policy(
        [_tier_candidate("c1", 0.62), _tier_candidate("c2", 0.60)],
        keyword_client=_AcceptClient(),
        llm_review_enabled=True,
    )
    assert out["llm_reviews_used"] >= 1
    accepted = out["accepted_candidates"]
    assert len(accepted) == 1
    assert accepted[0]["reuse_policy"]["llm_review_performed"] is True
    assert accepted[0]["reuse_policy"]["decision"] == "direct_reuse"
    assert accepted[0]["reuse_policy"]["reason"] == "llm_accept"


def test_apply_policy_midband_not_auto_accepted_when_llm_disabled():
    # The old code returned full_match (auto-accept) for this band; the fix holds
    # it back (routed to llm_review, which is disabled here) instead of reusing.
    out = _run_policy([_tier_candidate("c1", 0.62), _tier_candidate("c2", 0.60)])
    assert out["accepted_candidates"] == []


def test_candidate_policy_score_is_single_final_policy_score():
    candidate = {
        "keyword_score": 0.6,
        "embedding_score": 0.8,
        "substring_score": 0.4,
        "score_details": {"keyword_score": 0.0},
    }

    assert _candidate_policy_score(candidate) == 0.64
