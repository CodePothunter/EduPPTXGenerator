"""Tests for the new multi-subject coverage gate and conditional background threshold."""

from edupptx.materials.reuse_policy import (
    BACKGROUND_REUSE_THRESHOLD,
    BACKGROUND_SAME_THEME_HIGH_EMBEDDING_THRESHOLD,
    BACKGROUND_SAME_THEME_THRESHOLD,
    GENERIC_CLASS_MIN_DF_RATIO,
    evaluate_reuse_filter,
    subject_coverage_undercoverage,
    target_narrative_undercoverage,
)


def _entity(value: str, importance: int = 1, subtype: str = "generic_class") -> dict:
    return {
        "kind": "entity",
        "subtype": subtype,
        "value": value,
        "importance": importance,
        "confidence": 0.9,
        "evidence": "",
        "reason": "",
    }


def test_subject_coverage_helper_flags_undercovered_group():
    target = [_entity(name, importance=1) for name in ("猴子", "兔子", "松鼠", "公鸡", "鸭子", "孔雀")]
    candidate = [_entity("兔子", importance=1), _entity("松鼠", importance=1)]
    undercovered = subject_coverage_undercoverage(target, candidate)
    assert len(undercovered) == 1
    group = undercovered[0]
    assert group["kind"] == "entity"
    assert group["matched_count"] == 2
    assert group["required_count"] == 3
    assert set(group["missing_values"]) == {"猴子", "公鸡", "鸭子", "孔雀"}


def test_subject_coverage_helper_passes_when_half_or_more_covered():
    target = [_entity(name, importance=1) for name in ("猴子", "兔子", "松鼠", "公鸡")]
    candidate = [_entity("猴子", importance=1), _entity("兔子", importance=1), _entity("松鼠", importance=1)]
    undercovered = subject_coverage_undercoverage(target, candidate)
    assert undercovered == []


def test_subject_coverage_helper_ignores_imp0_constraints():
    target = [_entity(name, importance=0) for name in ("猴子", "兔子", "松鼠")]
    candidate = []
    assert subject_coverage_undercoverage(target, candidate) == []


def test_subject_coverage_helper_ignores_single_entity_groups():
    target = [_entity("孔雀", importance=1)]
    candidate = []
    assert subject_coverage_undercoverage(target, candidate) == []


def test_evaluate_reuse_filter_rejects_undercovered_multi_subject_target():
    target = {
        "asset_kind": "page_image",
        "asset_category": "content_specific",
        "theme": "一年级语文《比尾巴》课文教学",
        "constraints": [_entity(name, importance=1) for name in ("猴子", "兔子", "松鼠", "公鸡", "鸭子", "孔雀")],
    }
    candidate = {
        "asset_kind": "page_image",
        "asset_category": "character_action",
        "theme": "一年级语文《比尾巴》课文教学",
        "constraints": [_entity("兔子", importance=1)],
    }
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.8, "embedding_score": 0.7},
        threshold=0.5,
    )
    assert result["decision"] == "reject"
    assert result["reason"] == "subject_coverage_undercoverage"
    conflict = result["conflicts"][0]
    assert conflict["matched_count"] == 1
    assert conflict["required_count"] == 3
    assert set(conflict["missing_values"]) == {"猴子", "松鼠", "公鸡", "鸭子", "孔雀"}


def test_evaluate_reuse_filter_keeps_llm_review_when_coverage_at_threshold():
    target = {
        "asset_kind": "page_image",
        "asset_category": "content_specific",
        "theme": "t",
        "constraints": [_entity(name, importance=1) for name in ("猴子", "兔子", "松鼠", "公鸡")],
    }
    candidate = {
        "asset_kind": "page_image",
        "asset_category": "character_action",
        "theme": "t",
        "constraints": [_entity("猴子", importance=1), _entity("兔子", importance=1)],
    }
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.8, "embedding_score": 0.7},
        threshold=0.5,
    )
    # Coverage = 2/4 ≥ ceil(4/2)=2, undercoverage gate does not trigger.
    # Existing per-constraint missing path still produces LLM review.
    assert result["decision"] == "llm_review"
    assert result["reason"] != "subject_coverage_undercoverage"


def test_background_same_theme_lowers_threshold_when_embedding_high():
    target = {
        "asset_kind": "background",
        "theme": "一年级语文《比尾巴》课文教学",
        "constraints": [],
    }
    candidate = {
        "asset_kind": "background",
        "theme": "一年级语文《比尾巴》课文教学",
        "constraints": [],
    }
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.31, "embedding_score": 0.75},
    )
    assert result["decision"] == "full_match"
    assert result["threshold_used"] <= BACKGROUND_SAME_THEME_HIGH_EMBEDDING_THRESHOLD + 1e-6
    assert result["reason"] == "background_score_above_threshold"


def test_background_same_theme_lowers_threshold_softly_when_embedding_low():
    target = {"asset_kind": "background", "theme": "t", "constraints": []}
    candidate = {"asset_kind": "background", "theme": "t", "constraints": []}
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.35, "embedding_score": 0.40},
    )
    assert result["decision"] == "full_match"
    assert result["threshold_used"] <= BACKGROUND_SAME_THEME_THRESHOLD + 1e-6
    assert result["threshold_used"] > BACKGROUND_SAME_THEME_HIGH_EMBEDDING_THRESHOLD


def test_background_cross_theme_keeps_strict_threshold():
    target = {"asset_kind": "background", "theme": "一年级语文《比尾巴》", "constraints": []}
    candidate = {"asset_kind": "background", "theme": "《秋天的雨》", "constraints": []}
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.35, "embedding_score": 0.75},
    )
    assert result["decision"] == "reject"
    assert abs(result["threshold_used"] - BACKGROUND_REUSE_THRESHOLD) < 1e-6


# ---- Plan A: target imp=1 narrative reflux gate ----------------------------


def _strong_entity(value: str, subtype: str = "species_instance") -> dict:
    return {
        "kind": "entity",
        "subtype": subtype,
        "value": value,
        "importance": 2,
        "confidence": 0.95,
        "evidence": "",
        "reason": "",
    }


def _scene(value: str, subtype: str = "story_scene", importance: int = 1) -> dict:
    return {
        "kind": "scene",
        "subtype": subtype,
        "value": value,
        "importance": importance,
        "confidence": 0.9,
        "evidence": "",
        "reason": "",
    }


def test_target_narrative_undercoverage_flags_missing_story_scene():
    target = [
        _strong_entity("青蛙"),
        _scene("稻田"),
        {"kind": "action", "subtype": "teaching_fact", "value": "捉害虫",
         "importance": 1, "confidence": 0.9, "evidence": "", "reason": ""},
    ]
    candidate = [_strong_entity("青蛙")]
    missing = target_narrative_undercoverage(target, candidate)
    missing_kinds = sorted(m["kind"] for m in missing)
    assert missing_kinds == ["action", "scene"]


def test_target_narrative_undercoverage_ignores_imp0_narrative():
    target = [
        _strong_entity("青蛙"),
        _scene("稻田", importance=0),  # imp=0 → not gated even if narrative subtype
    ]
    candidate = [_strong_entity("青蛙")]
    assert target_narrative_undercoverage(target, candidate) == []


def test_target_narrative_undercoverage_ignores_non_narrative_subtype():
    target = [
        _strong_entity("青蛙"),
        # generic_class subtype is not narrative-binding
        {"kind": "entity", "subtype": "generic_class", "value": "动物", "importance": 1,
         "confidence": 0.9, "evidence": "", "reason": ""},
    ]
    candidate = [_strong_entity("青蛙")]
    assert target_narrative_undercoverage(target, candidate) == []


def test_target_narrative_undercoverage_satisfied_by_light_match():
    target = [_strong_entity("青蛙"), _scene("稻田")]
    candidate = [_strong_entity("青蛙"), _scene("稻田")]
    assert target_narrative_undercoverage(target, candidate) == []


def test_strong_cover_short_circuit_blocked_by_missing_imp1_narrative():
    # P4 case: candidate has imp=2 frog covered, but target's imp=1 scene
    # ("稻田") and teaching_fact ("捉害虫") are missing — short-circuit
    # should NOT fire as full_match.
    target = {
        "asset_kind": "page_image",
        "asset_category": "content_specific",
        "theme": "t",
        "constraints": [
            _strong_entity("青蛙"),
            _scene("稻田"),
            {"kind": "action", "subtype": "teaching_fact", "value": "捉害虫",
             "importance": 1, "confidence": 0.9, "evidence": "", "reason": ""},
        ],
    }
    candidate = {
        "asset_kind": "page_image",
        "asset_category": "content_specific",
        "theme": "t",
        "constraints": [_strong_entity("青蛙")],
    }
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.65, "embedding_score": 0.6},
        threshold=0.5,
    )
    assert result["decision"] == "llm_review"
    assert result["reason"] == "target_narrative_undercoverage"


def test_strong_cover_short_circuit_still_fires_when_narrative_covered():
    target = {
        "asset_kind": "page_image",
        "asset_category": "content_specific",
        "theme": "t",
        "constraints": [_strong_entity("青蛙"), _scene("稻田")],
    }
    candidate = {
        "asset_kind": "page_image",
        "asset_category": "content_specific",
        "theme": "t",
        "constraints": [_strong_entity("青蛙"), _scene("稻田")],
    }
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.65, "embedding_score": 0.6},
        threshold=0.5,
    )
    assert result["decision"] == "full_match"
    assert result["reason"] == "strong_constraints_exact_covered"


# ---- Plan 5: DF-ratio cross-validation in light_match -----------------------


def _generic_target(value: str) -> dict:
    return {
        "kind": "entity",
        "subtype": "generic_class",
        "value": value,
        "importance": 1,
        "confidence": 0.9,
        "evidence": "",
        "reason": "",
    }


def test_generic_specific_match_blocked_when_target_value_absent_from_df():
    # P2 case: target labelled generic_class but value ("小猴子") is absent
    # from the library's keyword DF map → DF gate denies generic_specific
    # and the candidate (small tadpole, species_instance) cannot bridge.
    # The downstream effect is that compare_constraints sees the target
    # entity un-matched and reports a medium_constraints_conflict reject,
    # which is the correct cross-species block.
    target = {
        "asset_kind": "page_image",
        "asset_category": "character_action",
        "theme": "t",
        "constraints": [_generic_target("小猴子")],
        "core_keywords": ["小猴子"],
    }
    candidate = {
        "asset_kind": "page_image",
        "asset_category": "character_action",
        "theme": "t",
        "constraints": [_strong_entity("小蝌蚪")],
        "core_keywords": ["小蝌蚪"],
    }
    result = evaluate_reuse_filter(
        target,
        candidate,
        {
            "keyword_score": 0.65,
            "embedding_score": 0.55,
            "df_ratio_lookup": {"其他词": 0.5},  # 小猴子 absent → DF gate fires
        },
        threshold=0.55,
    )
    assert result["decision"] == "reject"
    # Either medium_constraints_conflict (conflict path) or no_precision_signal
    # (if precision_signal is also passed false) is an acceptable block reason —
    # both mean "cross-species not allowed". The key invariant is reject.
    assert result["reason"] in {"medium_constraints_conflict", "no_precision_signal"}


def test_generic_specific_match_allowed_when_target_value_has_high_df():
    target = {
        "asset_kind": "page_image",
        "asset_category": "character_action",
        "theme": "t",
        "constraints": [_generic_target("小动物")],
        "core_keywords": ["小动物"],
    }
    candidate = {
        "asset_kind": "page_image",
        "asset_category": "character_action",
        "theme": "t",
        "constraints": [_strong_entity("小蝌蚪")],
        "core_keywords": ["小蝌蚪"],
    }
    df_lookup = {"小动物": GENERIC_CLASS_MIN_DF_RATIO + 0.05, "小蝌蚪": 0.07}
    result = evaluate_reuse_filter(
        target,
        candidate,
        {
            "keyword_score": 0.65,
            "embedding_score": 0.70,  # above strict candidate's auto-accept floor
            "df_ratio_lookup": df_lookup,
            "precision_signal": True,
        },
        threshold=0.55,
    )
    # High DF on the generic target value preserves legacy generic→specific
    # bridge: short-circuit / similarity match continues to fire.
    assert result["decision"] == "full_match"


def test_df_gate_inactive_when_lookup_missing():
    # Without df_ratio_lookup in score_details the gate must not engage,
    # preserving exact legacy behavior for callers that haven't been wired.
    target = {
        "asset_kind": "page_image",
        "asset_category": "character_action",
        "theme": "t",
        "constraints": [_generic_target("小猴子")],
    }
    candidate = {
        "asset_kind": "page_image",
        "asset_category": "character_action",
        "theme": "t",
        "constraints": [_strong_entity("小蝌蚪")],
    }
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.65, "embedding_score": 0.70},
        threshold=0.55,
    )
    # No df lookup → no DF gate → legacy generic_specific match → full_match.
    assert result["decision"] == "full_match"


# ---- Plan 4: precision_signal gating ---------------------------------------


def test_medium_target_with_false_precision_signal_rejects_without_llm():
    target = {
        "asset_kind": "page_image",
        "asset_category": "content_specific",
        "theme": "t",
        "constraints": [
            {"kind": "entity", "subtype": "generic_class", "value": "动物",
             "importance": 1, "confidence": 0.9, "evidence": "", "reason": ""},
            {"kind": "entity", "subtype": "generic_class", "value": "小朋友",
             "importance": 1, "confidence": 0.9, "evidence": "", "reason": ""},
        ],
    }
    candidate = {
        "asset_kind": "page_image",
        "asset_category": "content_specific",
        "theme": "t",
        "constraints": [
            {"kind": "entity", "subtype": "generic_class", "value": "动物",
             "importance": 1, "confidence": 0.9, "evidence": "", "reason": ""},
            {"kind": "entity", "subtype": "generic_class", "value": "小朋友",
             "importance": 1, "confidence": 0.9, "evidence": "", "reason": ""},
        ],
    }
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.6, "embedding_score": 0.6, "precision_signal": False},
        threshold=0.55,
    )
    assert result["decision"] == "reject"
    assert result["reason"] == "no_precision_signal"


def test_loose_target_with_false_precision_signal_still_reviews():
    # Forced-loose decorative slots retain LLM rescue path even on
    # precision_signal=False — the LLM can confirm decorative suitability.
    target = {
        "asset_kind": "page_image",
        "asset_category": "learning_behavior",
        "theme": "t",
        "constraints": [],
    }
    candidate = {
        "asset_kind": "page_image",
        "asset_category": "learning_behavior",
        "theme": "t",
        "constraints": [],
    }
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.6, "embedding_score": 0.6, "precision_signal": False},
        threshold=0.5,
    )
    assert result["decision"] == "llm_review"
    assert result["reason"] == "no_precision_signal"


def test_precision_signal_absent_preserves_legacy_full_match():
    # precision_signal=None (legacy callers) → no gate, full_match goes through.
    target = {
        "asset_kind": "page_image",
        "asset_category": "content_specific",
        "theme": "t",
        "constraints": [
            {"kind": "entity", "subtype": "species_instance", "value": "小蝌蚪",
             "importance": 2, "confidence": 0.95, "evidence": "", "reason": ""},
        ],
    }
    candidate = {
        "asset_kind": "page_image",
        "asset_category": "content_specific",
        "theme": "t",
        "constraints": [
            {"kind": "entity", "subtype": "species_instance", "value": "小蝌蚪",
             "importance": 2, "confidence": 0.95, "evidence": "", "reason": ""},
        ],
    }
    result = evaluate_reuse_filter(
        target,
        candidate,
        {"keyword_score": 0.65, "embedding_score": 0.6},  # no precision_signal key
        threshold=0.55,
    )
    assert result["decision"] == "full_match"


# ---- Plan D / 3C: LLM review budget integration test -----------------------


def test_find_reusable_caps_llm_reviews_per_query(tmp_path, monkeypatch):
    """End-to-end: a candidate pool that would trigger many LLM reviews
    must respect the per-query budget. The first MAX_LLM_REVIEWS_PER_QUERY
    candidates may invoke the LLM; subsequent candidates with the same
    policy reason are short-circuited to ``llm_review_budget_exhausted``
    without calling the LLM. The budget cap is the documented
    ``MAX_LLM_REVIEWS_PER_QUERY`` constant.
    """
    import json
    from edupptx.materials import ai_image_asset_db as db_mod
    from edupptx.materials.ai_image_asset_db import find_reusable_ai_image_asset

    review_calls = {"count": 0}

    class StubClient:
        _model = "stub"

        def chat_json(self, messages, **kwargs):
            raw = messages[1]["content"]
            req = json.loads(raw[raw.index("{"):])
            if req.get("reuse_review"):
                review_calls["count"] += 1
                # Always reject so we exercise the full candidate pool
                return {"score": 0.05, "brief_reason": "stub low", "evidence": [],
                        "risk_factors": [], "matched_constraints": [],
                        "mismatched_constraints": [], "missing_constraints": []}
            # keyword enrichment for the target only
            target_id = req["assets"][0]["asset_id"]
            return {"assets": [{
                "asset_id": target_id,
                "normalized_prompt": "stub",
                "context_summary": "stub",
                "teaching_intent": "teach exact stub",
                "asset_category": "content_specific",
                "constraints": [{"kind": "text", "subtype": "teaching_content",
                                 "value": "stub_char", "importance": 2}],
                "core_keywords": ["stub_char"],
                "semantic_aliases": {},
                "context_summary_keywords": ["stub_char"],
            }]}

    library_dir = tmp_path / "lib"
    image_dir = library_dir / "ai_images"
    image_dir.mkdir(parents=True)

    assets = []
    # Build 6 assets that ALL trigger strict_text_exact_covered_review
    # (matching teaching_content text imp=2), forcing the LLM review path.
    for i in range(6):
        (image_dir / f"a{i}.png").write_bytes(b"x" + str(i).encode())
        assets.append({
            "asset_id": f"a{i}",
            "asset_kind": "page_image",
            "image_path": f"ai_images/a{i}.png",
            "aspect_ratio": "1:1",
            "content_prompt": "stub_char teaching card",
            "normalized_prompt": "stub_char teaching card",
            "core_keywords": ["stub_char"],
            "semantic_aliases": {},
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "constraints": [{"kind": "text", "subtype": "teaching_content",
                             "value": "stub_char", "importance": 2}],
            "generic_support_allowed": False,
        })
    db_payload = {
        "schema_version": 6,
        "output_root": str(library_dir),
        "asset_count": len(assets),
        "assets": assets,
        "warnings": [],
    }
    (library_dir / "ai_image_asset_db.json").write_text(
        json.dumps(db_payload, ensure_ascii=False), encoding="utf-8"
    )

    match = find_reusable_ai_image_asset(
        library_dir=library_dir,
        asset_kind="page_image",
        prompt="stub_char teaching card",
        subject="语文",
        grade="2",
        page_title="Stub",
        role="illustration",
        aspect_ratio="1:1",
        keyword_client=StubClient(),
        debug_path=library_dir / "debug.json",
        reuse_debug_mode="full",
    )
    # All candidates are rejected → no match
    assert match is None
    # The LLM was invoked at most MAX_LLM_REVIEWS_PER_QUERY times,
    # the rest were short-circuited by the budget.
    assert review_calls["count"] <= db_mod.MAX_LLM_REVIEWS_PER_QUERY
    debug = json.loads((library_dir / "debug.json").read_text(encoding="utf-8"))
    cand_policies = debug["queries"][0]["policy_candidates"]
    skipped = [
        c for c in cand_policies
        if c.get("reuse_policy", {}).get("reason", "").endswith("budget_exhausted")
    ]
    # At least one candidate skipped via budget gate
    assert len(skipped) >= 1


def test_find_reusable_skips_after_first_accept(tmp_path):
    """When an earlier candidate has been accepted, later candidates should
    NOT invoke the LLM (early-stop). They are recorded as
    ``llm_review_skipped_after_accept`` rejects in debug.
    """
    import json
    from edupptx.materials.ai_image_asset_db import find_reusable_ai_image_asset

    review_calls = {"count": 0}

    class StubClient:
        _model = "stub"

        def chat_json(self, messages, **kwargs):
            raw = messages[1]["content"]
            req = json.loads(raw[raw.index("{"):])
            if req.get("reuse_review"):
                review_calls["count"] += 1
                # accept everything we're asked about (so the first
                # candidate gets accepted and the rest must be skipped).
                return {"score": 0.95, "brief_reason": "stub accept",
                        "evidence": [], "risk_factors": [],
                        "matched_constraints": [], "mismatched_constraints": [],
                        "missing_constraints": []}
            target_id = req["assets"][0]["asset_id"]
            return {"assets": [{
                "asset_id": target_id,
                "normalized_prompt": "stub",
                "context_summary": "stub",
                "teaching_intent": "teach exact stub",
                "asset_category": "content_specific",
                "constraints": [{"kind": "text", "subtype": "teaching_content",
                                 "value": "stub_char", "importance": 2}],
                "core_keywords": ["stub_char"],
                "semantic_aliases": {},
                "context_summary_keywords": ["stub_char"],
            }]}

    library_dir = tmp_path / "lib"
    image_dir = library_dir / "ai_images"
    image_dir.mkdir(parents=True)
    assets = []
    for i in range(4):
        (image_dir / f"b{i}.png").write_bytes(b"y" + str(i).encode())
        assets.append({
            "asset_id": f"b{i}",
            "asset_kind": "page_image",
            "image_path": f"ai_images/b{i}.png",
            "aspect_ratio": "1:1",
            "content_prompt": "stub_char teaching card",
            "normalized_prompt": "stub_char teaching card",
            "core_keywords": ["stub_char"],
            "semantic_aliases": {},
            "reuse_level": "strict",
            "asset_category": "content_specific",
            "constraints": [{"kind": "text", "subtype": "teaching_content",
                             "value": "stub_char", "importance": 2}],
            "generic_support_allowed": False,
        })
    db_payload = {
        "schema_version": 6, "output_root": str(library_dir),
        "asset_count": len(assets), "assets": assets, "warnings": [],
    }
    (library_dir / "ai_image_asset_db.json").write_text(
        json.dumps(db_payload, ensure_ascii=False), encoding="utf-8"
    )

    match = find_reusable_ai_image_asset(
        library_dir=library_dir,
        asset_kind="page_image",
        prompt="stub_char teaching card",
        subject="语文",
        grade="2",
        page_title="Stub",
        role="illustration",
        aspect_ratio="1:1",
        keyword_client=StubClient(),
        debug_path=library_dir / "debug.json",
        reuse_debug_mode="full",
    )
    assert match is not None
    # Exactly one LLM review: the first candidate accepted, the other 3
    # candidates short-circuited via the after-accept gate.
    assert review_calls["count"] == 1
    debug = json.loads((library_dir / "debug.json").read_text(encoding="utf-8"))
    cand_policies = debug["queries"][0]["policy_candidates"]
    skipped = [
        c for c in cand_policies
        if c.get("reuse_policy", {}).get("reason", "").endswith("skipped_after_accept")
    ]
    assert len(skipped) >= 1


# ---- LLM-skip / threshold-override contract between policy and dispatcher ----


def test_llm_review_threshold_uses_policy_override_when_present():
    """The dispatcher prefers ``policy_result.llm_accept_threshold_override``
    over the profile default. This is the contract the producer side
    (reuse_policy._result for forced_loose_target_teaching_missing,
    strict_text_exact_covered_review) relies on."""

    from edupptx.materials.ai_image_asset_db import (
        _reuse_review_accept_score_threshold,
    )

    target = {
        "asset_kind": "page_image",
        "reuse_level": "loose",
        "asset_category": "learning_behavior",
        "constraints": [],
    }
    candidate = {"asset_kind": "page_image"}
    # Override present → dispatcher uses it verbatim.
    assert _reuse_review_accept_score_threshold(
        target,
        candidate,
        policy_result={"llm_accept_threshold_override": 0.65},
    ) == 0.65
    # Override absent → fall through to profile default (loose = 0.55).
    assert _reuse_review_accept_score_threshold(
        target,
        candidate,
        policy_result={},
    ) == 0.55


def test_forced_loose_teaching_missing_sets_override_in_policy_result():
    """End-to-end: when evaluate_reuse_filter detects forced-loose teaching
    undercoverage, the policy_result must carry an override of 0.65 that
    the dispatcher will then honor."""

    from edupptx.materials.reuse_policy import (
        FORCED_LOOSE_TEACHING_MISSING_LLM_THRESHOLD,
        evaluate_reuse_filter,
    )

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
    assert result["llm_accept_threshold_override"] == FORCED_LOOSE_TEACHING_MISSING_LLM_THRESHOLD


def test_candidate_extra_named_individual_marks_llm_skip_safe():
    """End-to-end: when the candidate carries an extra imp=2 named_individual
    the target did not request, the policy must mark llm_skip_safe=True so
    the dispatcher can short-circuit without invoking the LLM."""

    from edupptx.materials.reuse_policy import evaluate_reuse_filter

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
            {"kind": "entity", "subtype": "named_individual",
             "value": "史铁生", "importance": 2},
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
    assert result["llm_skip_safe"] is True


def test_candidate_extra_teaching_carrier_does_not_mark_llm_skip_safe():
    """When the extra is a teaching_carrier (synonym-replaceable), the LLM
    must still be invoked — llm_skip_safe must remain False."""

    from edupptx.materials.reuse_policy import evaluate_reuse_filter

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
            {"kind": "object", "subtype": "teaching_carrier",
             "value": "汉字结构示意图", "importance": 2},
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
    assert result["llm_skip_safe"] is False
