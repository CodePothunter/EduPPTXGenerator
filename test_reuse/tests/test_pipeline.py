import json
from pathlib import Path

import pytest

from test_reuse.pipeline import (
    build_target_records,
    extract_plan_needs,
    flatten_candidate_collection,
    hard_filter_rows_for_target,
    prepare_run,
    read_json,
    read_jsonl,
    run_hard_filter_stage,
    run_eval,
    run_retrieve_stage,
    run_review_stage,
    run_summarize_stage,
    validate_enriched_targets,
    write_json,
    write_jsonl,
)


CHINESE_SUBJECT = "\u8bed\u6587"
MATH_SUBJECT = "\u6570\u5b66"


def _minimal_plan() -> dict:
    return {
        "meta": {
            "lesson_id": "lesson_one",
            "topic": "Tadpole lesson",
            "subject": CHINESE_SUBJECT,
            "grade": "\u4e8c\u5e74\u7ea7",
            "total_pages": 1,
        },
        "pages": [
            {
                "page_number": 1,
                "page_type": "content",
                "title": "Observe",
                "material_needs": {
                    "images": [
                        {
                            "query": "cartoon tadpole on a lotus leaf",
                            "source": "ai_generate",
                            "role": "illustration",
                            "aspect_ratio": "1:1",
                            "caption": "tadpole and lotus leaf",
                            "prompt_route": {"strict_reuse_group": "C02_generic_subject_object"},
                        },
                        {
                            "query": "real pond photo",
                            "source": "search",
                            "role": "illustration",
                            "aspect_ratio": "16:9",
                        },
                    ]
                },
            }
        ],
    }


def _install_fake_prepare_enrichment(monkeypatch):
    class FakeClient:
        pass

    def fake_keyword_client(env_file, *, allow_llm):
        if not allow_llm:
            return None
        return FakeClient()

    def fake_prewarm(targets, keyword_client, target_keyword_cache, **kwargs):
        from edupptx.materials.ai_image_asset_db import _target_keyword_cache_key

        assert isinstance(keyword_client, FakeClient)
        for target in targets:
            enriched = dict(target)
            enriched.update(
                {
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": CHINESE_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "general": False,
                    "match_text": enriched.get("caption") or enriched.get("query") or "",
                    "match_key": f"page_image|{enriched.get('caption') or enriched.get('query') or ''}",
                }
            )
            target_keyword_cache[_target_keyword_cache_key(target)] = enriched
        return len(targets)

    monkeypatch.setattr("test_reuse.pipeline._keyword_client", fake_keyword_client)
    monkeypatch.setattr("test_reuse.pipeline._prewarm_reuse_target_keywords", fake_prewarm)


def test_extract_plan_needs_reads_ai_generate_slots_without_generating_dataset(tmp_path: Path):
    plan_path = tmp_path / "lesson_one.json"
    plan_path.write_text(json.dumps(_minimal_plan(), ensure_ascii=False), encoding="utf-8")

    rows = extract_plan_needs(plan_path, run_id="run1")

    assert len(rows) == 1
    assert rows[0]["need_id"] == "lesson_one:p01:illustration_1"
    assert rows[0]["lesson_id"] == "lesson_one"
    assert rows[0]["subject"] == CHINESE_SUBJECT
    assert rows[0]["grade"] == "\u4e8c\u5e74\u7ea7"
    assert rows[0]["raw_query"] == "cartoon tadpole on a lotus leaf"
    assert rows[0]["caption"] == "tadpole and lotus leaf"


def test_extract_plan_needs_uses_output_session_dir_for_plan_json(tmp_path: Path):
    plan = _minimal_plan()
    plan["meta"].pop("lesson_id")
    plan_path = tmp_path / "output" / "session_20260603_150913" / "plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    rows = extract_plan_needs(plan_path, run_id="run1")

    assert rows[0]["lesson_id"] == "session_20260603_150913"
    assert rows[0]["need_id"] == "session_20260603_150913:p01:illustration_1"


def test_extract_plan_needs_preserves_gold_classification_fields(tmp_path: Path):
    plan = _minimal_plan()
    plan["pages"][0]["material_needs"]["images"][0].update(
        {
            "label_status": "labeled",
            "should_reuse": False,
            "target_strict_reuse_group_gold": "C00_strict_text_problem_skip",
            "target_is_c00_skip": True,
            "gold_label_text": "带拼音的课文段落",
            "gold_label_text_source": "query",
        }
    )
    plan_path = tmp_path / "lesson_one.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    rows = extract_plan_needs(plan_path, run_id="run1")

    assert rows[0]["target_strict_reuse_group_gold"] == "C00_strict_text_problem_skip"
    assert rows[0]["target_is_c00_skip"] is True
    assert rows[0]["gold_label_text"] == "带拼音的课文段落"
    assert rows[0]["gold_label_text_source"] == "query"


def test_build_target_records_uses_existing_reuse_target_builder(tmp_path: Path):
    plan_path = tmp_path / "lesson_one.json"
    plan_path.write_text(json.dumps(_minimal_plan(), ensure_ascii=False), encoding="utf-8")
    needs = extract_plan_needs(plan_path, run_id="run1")

    targets, enrichment_rows = build_target_records(needs)

    assert len(targets) == 1
    assert len(enrichment_rows) == 1
    row = targets[0]
    assert row["need_id"] == "lesson_one:p01:illustration_1"
    assert row["asset_kind"] == "page_image"
    assert row["raw_query"] == "cartoon tadpole on a lotus leaf"
    assert row["caption"] == "tadpole and lotus leaf"
    assert row["subject"] == CHINESE_SUBJECT
    assert row["aspect_ratio"] == "1:1"
    assert row["label_status"] == "unlabeled"
    assert row["acceptable_asset_ids"] == []
    assert row["target"]["asset_id"].startswith("target_")
    assert enrichment_rows[0]["enriched"] is False


def test_validate_enriched_targets_rejects_missing_group():
    rows = [
        {
            "need_id": "n1",
            "target": {
                "caption": "tadpole and lotus leaf",
                "subject": CHINESE_SUBJECT,
                "grade_norm": "\u4e8c\u5e74\u7ea7",
                "grade_band": "\u4f4e\u5e74\u7ea7",
                "match_text": "tadpole and lotus leaf",
            },
        }
    ]

    with pytest.raises(ValueError, match="strict_reuse_group"):
        validate_enriched_targets(rows, stage="prepare")


def test_validate_enriched_targets_accepts_production_fields():
    rows = [
        {
            "need_id": "n1",
            "target": {
                "caption": "tadpole and lotus leaf",
                "strict_reuse_group": "C02_generic_subject_object",
                "subject": CHINESE_SUBJECT,
                "grade_norm": "\u4e8c\u5e74\u7ea7",
                "grade_band": "\u4f4e\u5e74\u7ea7",
                "match_text": "tadpole and lotus leaf",
            },
        }
    ]

    assert validate_enriched_targets(rows, stage="prepare") == {
        "target_count": 1,
        "missing_required_field_count": 0,
        "missing_required_fields": {},
    }


def test_prepare_run_requires_llm_when_enrichment_cache_is_missing(tmp_path: Path):
    plan_path = tmp_path / "lesson_one.json"
    plan_path.write_text(json.dumps(_minimal_plan(), ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="prepare requires --allow-llm"):
        prepare_run(
            plan_paths=[plan_path],
            output_dir=tmp_path / "report",
            run_id="run1",
            allow_llm=False,
        )


def test_prepare_run_persists_enriched_targets_from_prewarm(tmp_path: Path, monkeypatch):
    plan_path = tmp_path / "lesson_one.json"
    plan_path.write_text(json.dumps(_minimal_plan(), ensure_ascii=False), encoding="utf-8")

    class FakeClient:
        pass

    def fake_keyword_client(env_file, *, allow_llm):
        assert allow_llm is True
        return FakeClient()

    def fake_prewarm(targets, keyword_client, target_keyword_cache, **kwargs):
        from edupptx.materials.ai_image_asset_db import _target_keyword_cache_key

        assert isinstance(keyword_client, FakeClient)
        for target in targets:
            enriched = dict(target)
            enriched.update(
                {
                    "caption": enriched["caption"],
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": CHINESE_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "general": False,
                    "match_text": "tadpole and lotus leaf",
                    "match_key": "page_image|tadpole and lotus leaf",
                }
            )
            target_keyword_cache[_target_keyword_cache_key(target)] = enriched
        return len(targets)

    monkeypatch.setattr("test_reuse.pipeline._keyword_client", fake_keyword_client)
    monkeypatch.setattr("test_reuse.pipeline._prewarm_reuse_target_keywords", fake_prewarm)

    run_dir = prepare_run(
        plan_paths=[plan_path],
        output_dir=tmp_path / "report",
        run_id="run1",
        allow_llm=True,
    )

    target = read_jsonl(run_dir / "targets.jsonl")[0]["target"]
    assert target["strict_reuse_group"] == "C02_generic_subject_object"
    assert target["match_text"] == "tadpole and lotus leaf"
    assert (run_dir / "target_enrichment.jsonl").exists()


def test_prepare_run_writes_target_classification_summary(tmp_path: Path, monkeypatch):
    _install_fake_prepare_enrichment(monkeypatch)
    plan = _minimal_plan()
    plan["pages"][0]["material_needs"]["images"][0].update(
        {
            "label_status": "labeled",
            "should_reuse": True,
            "acceptable_asset_ids": ["a1"],
            "best_asset_ids": ["a1"],
            "target_strict_reuse_group_gold": "C02_generic_subject_object",
            "target_is_c00_skip": False,
            "gold_label_text": "tadpole and lotus leaf",
            "gold_label_text_source": "caption",
        }
    )
    plan_path = tmp_path / "lesson_one.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    run_dir = prepare_run(
        plan_paths=[plan_path],
        output_dir=tmp_path / "report",
        run_id="run1",
        allow_llm=True,
    )

    assert (run_dir / "target_classification_summary.json").exists()
    summary = read_json(run_dir / "target_classification_summary.json")
    assert summary["total_targets"] == 1


def test_prepare_run_applies_external_goldset_to_new_output_plan(tmp_path: Path, monkeypatch):
    _install_fake_prepare_enrichment(monkeypatch)
    plan = _minimal_plan()
    plan["meta"].pop("lesson_id")
    plan_path = tmp_path / "output" / "session_20260603_150913" / "plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    goldset_path = tmp_path / "goldset.json"
    goldset_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {
                        "need_id": "session_20260603_150913:p01:illustration_1",
                        "label_status": "labeled",
                        "should_reuse": True,
                        "acceptable_asset_ids": ["asset_1", "asset_2"],
                        "best_asset_ids": ["asset_1"],
                        "label_notes": "external gold label",
                        "target_strict_reuse_group_gold": "C02_generic_subject_object",
                        "target_is_c00_skip": False,
                        "gold_label_text": "tadpole and lotus leaf",
                        "gold_label_text_source": "caption",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    run_dir = prepare_run(
        plan_paths=[plan_path],
        goldset_paths=[goldset_path],
        output_dir=tmp_path / "report",
        run_id="run1",
        allow_llm=True,
    )

    plan_need = read_jsonl(run_dir / "plan_needs.jsonl")[0]
    target = read_jsonl(run_dir / "targets.jsonl")[0]
    assert plan_need["label_status"] == "labeled"
    assert plan_need["acceptable_asset_ids"] == ["asset_1", "asset_2"]
    assert plan_need["best_asset_ids"] == ["asset_1"]
    assert target["label_status"] == "labeled"
    assert target["acceptable_asset_ids"] == ["asset_1", "asset_2"]
    assert target["target_strict_reuse_group_gold"] == "C02_generic_subject_object"
    assert read_json(run_dir / "manifest.json")["goldset_fingerprints"][0]["exists"] is True


def test_prepare_run_applies_goldset_when_slot_key_shifted_but_query_matches(tmp_path: Path, monkeypatch):
    _install_fake_prepare_enrichment(monkeypatch)
    plan = _minimal_plan()
    plan["meta"].pop("lesson_id")
    plan_path = tmp_path / "output" / "session_20260603_150935" / "plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    goldset_path = tmp_path / "goldset.json"
    goldset_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {
                        "need_id": "session_20260603_150935:p01:illustration_2",
                        "session_id": "session_20260603_150935",
                        "page_number": 1,
                        "role": "illustration",
                        "query": "cartoon tadpole on a lotus leaf",
                        "caption": "tadpole and lotus leaf",
                        "label_status": "labeled",
                        "should_reuse": True,
                        "acceptable_asset_ids": ["asset_1"],
                        "best_asset_ids": ["asset_1"],
                        "target_strict_reuse_group_gold": "C02_generic_subject_object",
                        "target_is_c00_skip": False,
                        "gold_label_text": "tadpole and lotus leaf",
                        "gold_label_text_source": "caption",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    run_dir = prepare_run(
        plan_paths=[plan_path],
        goldset_paths=[goldset_path],
        output_dir=tmp_path / "report",
        run_id="run1",
        allow_llm=True,
    )

    plan_need = read_jsonl(run_dir / "plan_needs.jsonl")[0]
    assert plan_need["need_id"] == "session_20260603_150935:p01:illustration_1"
    assert plan_need["label_status"] == "labeled"
    assert plan_need["best_asset_ids"] == ["asset_1"]


def test_hard_filter_rows_for_target_checks_full_library_candidates():
    target_record = {
        "run_id": "run1",
        "need_id": "n1",
        "target": {
            "asset_kind": "page_image",
            "strict_reuse_group": "C02_generic_subject_object",
            "subject": CHINESE_SUBJECT,
            "grade_norm": "\u4e8c\u5e74\u7ea7",
            "grade_band": "\u4f4e\u5e74\u7ea7",
            "aspect_ratio": "1:1",
            "caption": "tadpole and lotus leaf",
        },
        "acceptable_asset_ids": ["a_good"],
        "best_asset_ids": ["a_good"],
        "label_status": "labeled",
        "should_reuse": True,
    }
    assets = [
        {
            "asset_id": "a_good",
            "asset_kind": "page_image",
            "strict_reuse_group": "C02_generic_subject_object",
            "subject": CHINESE_SUBJECT,
            "grade_norm": "\u4e8c\u5e74\u7ea7",
            "grade_band": "\u4f4e\u5e74\u7ea7",
            "general": False,
            "aspect_ratio": "1:1",
        },
        {
            "asset_id": "a_bad_group",
            "asset_kind": "page_image",
            "strict_reuse_group": "C03_scene_decor_container",
            "subject": CHINESE_SUBJECT,
            "grade_norm": "\u4e8c\u5e74\u7ea7",
            "grade_band": "\u4f4e\u5e74\u7ea7",
            "general": False,
            "aspect_ratio": "1:1",
        },
        {
            "asset_id": "a_bad_subject",
            "asset_kind": "page_image",
            "strict_reuse_group": "C02_generic_subject_object",
            "subject": MATH_SUBJECT,
            "grade_norm": "\u4e8c\u5e74\u7ea7",
            "grade_band": "\u4f4e\u5e74\u7ea7",
            "general": False,
            "aspect_ratio": "1:1",
        },
    ]

    rows = hard_filter_rows_for_target(target_record, assets)
    by_id = {row["asset_id"]: row for row in rows}

    assert by_id["a_good"]["all_hard_pass"] is True
    assert by_id["a_bad_group"]["category_pass"] is False
    assert by_id["a_bad_subject"]["subject_pass"] is False
    assert by_id["a_good"]["is_acceptable"] is True


def test_hard_filter_stage_uses_routed_split_assets(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "report" / "run1"
    library_dir = tmp_path / "library"
    split_dir = library_dir / "strict_reuse_indexes"
    run_dir.mkdir(parents=True)
    split_dir.mkdir(parents=True)
    target = {
        "caption": "tadpole and lotus leaf",
        "strict_reuse_group": "C02_generic_subject_object",
        "subject": CHINESE_SUBJECT,
        "grade_norm": "\u4e8c\u5e74\u7ea7",
        "grade_band": "\u4f4e\u5e74\u7ea7",
        "match_text": "tadpole and lotus leaf",
        "asset_kind": "page_image",
        "aspect_ratio": "1:1",
    }
    routed_asset = {
        "asset_id": "a_routed",
        "asset_kind": "page_image",
        "strict_reuse_group": "C02_generic_subject_object",
        "subject": CHINESE_SUBJECT,
        "grade_norm": "\u4e8c\u5e74\u7ea7",
        "grade_band": "\u4f4e\u5e74\u7ea7",
        "general": False,
        "aspect_ratio": "1:1",
    }
    full_only_asset = {
        **routed_asset,
        "asset_id": "a_full_only",
    }
    write_jsonl(
        run_dir / "targets.jsonl",
        [
            {
                "run_id": "run1",
                "need_id": "n1",
                "target": target,
                "label_status": "labeled",
                "should_reuse": True,
                "acceptable_asset_ids": ["a_routed"],
                "best_asset_ids": ["a_routed"],
            }
        ],
    )
    write_json(
        split_dir / "C02_generic_subject_object.json",
        {
            "schema_version": 2,
            "strict_reuse_group": "C02_generic_subject_object",
            "asset_count": 1,
            "assets": [routed_asset],
        },
    )

    def fake_load_library(library_root, reuse_search_context):
        return {
            "library_root": library_root,
            "index": {"assets": [full_only_asset, routed_asset]},
            "match_index_path": split_dir,
        }

    monkeypatch.setattr("test_reuse.pipeline._load_reuse_library_for_search", fake_load_library)

    run_hard_filter_stage(run_dir=run_dir, library_dirs=[library_dir])

    rows = read_jsonl(run_dir / "hard_filter_pairs.jsonl")
    assert [row["asset_id"] for row in rows] == ["a_routed"]


def test_hard_filter_stage_reads_split_index_without_loading_search_library(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "report" / "run1"
    library_dir = tmp_path / "library"
    split_dir = library_dir / "strict_reuse_indexes"
    run_dir.mkdir(parents=True)
    split_dir.mkdir(parents=True)
    target = {
        "caption": "x",
        "strict_reuse_group": "C02_generic_subject_object",
        "subject": CHINESE_SUBJECT,
        "grade_norm": "\u4e8c\u5e74\u7ea7",
        "grade_band": "\u4f4e\u5e74\u7ea7",
        "match_text": "x",
        "asset_kind": "page_image",
        "aspect_ratio": "1:1",
    }
    write_jsonl(
        run_dir / "targets.jsonl",
        [
            {
                "run_id": "run1",
                "need_id": "n1",
                "target": target,
                "label_status": "labeled",
                "should_reuse": True,
                "acceptable_asset_ids": ["a1"],
                "best_asset_ids": ["a1"],
            }
        ],
    )
    write_json(
        split_dir / "C02_generic_subject_object.json",
        {
            "schema_version": 2,
            "strict_reuse_group": "C02_generic_subject_object",
            "asset_count": 1,
            "assets": [
                {
                    "asset_id": "a1",
                    "asset_kind": "page_image",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": CHINESE_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "general": False,
                    "aspect_ratio": "1:1",
                }
            ],
        },
    )

    def fail_load_search_library(*args, **kwargs):
        raise AssertionError("hard-filter must not build/load embedding search library when split index exists")

    monkeypatch.setattr("test_reuse.pipeline._load_reuse_library_for_search", fail_load_search_library)

    run_hard_filter_stage(run_dir=run_dir, library_dirs=[library_dir])

    assert read_jsonl(run_dir / "hard_filter_pairs.jsonl")[0]["asset_id"] == "a1"


def test_review_stage_writes_llm_review_summary(tmp_path: Path):
    run_dir = tmp_path / "report" / "run1"
    run_dir.mkdir(parents=True)
    write_jsonl_path = run_dir / "targets.jsonl"
    write_jsonl_path.write_text(
        json.dumps(
            {
                "run_id": "run1",
                "need_id": "n1",
                "target": {
                    "caption": "x",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": CHINESE_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "match_text": "x",
                },
                "label_status": "labeled",
                "should_reuse": False,
                "acceptable_asset_ids": [],
                "best_asset_ids": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "candidate_collections.jsonl").write_text(
        json.dumps(
            {
                "run_id": "run1",
                "need_id": "n1",
                "collection": {"candidates": []},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "threshold_candidates.jsonl").write_text("", encoding="utf-8")

    run_review_stage(run_dir=run_dir, review_enabled=False, allow_llm=False)

    assert (run_dir / "llm_review_summary.json").exists()


def test_review_stage_requires_allow_llm_when_review_enabled(tmp_path: Path):
    run_dir = tmp_path / "report" / "run1"
    run_dir.mkdir(parents=True)
    write_jsonl(run_dir / "targets.jsonl", [])
    write_jsonl(run_dir / "candidate_collections.jsonl", [])
    write_jsonl(run_dir / "threshold_candidates.jsonl", [])

    with pytest.raises(ValueError, match="review requires --allow-llm"):
        run_review_stage(run_dir=run_dir, review_enabled=True, allow_llm=False)


def test_review_stage_applies_serial_strict_reuse_occupancy(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "report" / "run1"
    run_dir.mkdir(parents=True)
    strict_asset = {
        "asset_id": "strict_a",
        "asset_kind": "page_image",
        "strict_reuse_group": "C01_irreplaceable_entity_event_action",
    }
    targets = []
    collections = []
    threshold_rows = []
    for index in range(3):
        need_id = f"n{index + 1}"
        targets.append(
            {
                "run_id": "run1",
                "need_id": need_id,
                "target": {
                    "caption": "x",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": CHINESE_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "match_text": "x",
                },
                "label_status": "labeled",
                "should_reuse": True,
                "acceptable_asset_ids": ["strict_a"],
                "best_asset_ids": ["strict_a"],
            }
        )
        collections.append(
            {
                "run_id": "run1",
                "need_id": need_id,
                "collection": {"_reuse_candidate_collection": True, "candidates": [{"asset": strict_asset}]},
            }
        )
        threshold_rows.append(
            {
                "run_id": "run1",
                "need_id": need_id,
                "asset_id": "strict_a",
                "threshold_pass": True,
            }
        )
    write_jsonl(run_dir / "targets.jsonl", targets)
    write_jsonl(run_dir / "candidate_collections.jsonl", collections)
    write_jsonl(run_dir / "threshold_candidates.jsonl", threshold_rows)

    def fake_finalize(collection, **kwargs):
        assert kwargs.get("vlm_client") is None
        assert kwargs.get("near_miss_vlm_state") is None
        return {
            "asset": dict(strict_asset),
            "keyword_score": 0.9,
            "hybrid_score": 0.9,
            "reuse_policy": {"decision": "full_match"},
        }

    monkeypatch.setattr("test_reuse.pipeline._finalize_reuse_candidate_collection", fake_finalize)

    run_review_stage(run_dir=run_dir, review_enabled=False, allow_llm=False)

    final_rows = read_jsonl(run_dir / "final_matches.jsonl")
    assert [row["selected_asset_id"] for row in final_rows] == ["strict_a", "strict_a", ""]
    assert final_rows[2]["failure_stage"] == "strict_reuse_occupancy"


def test_flatten_candidate_collection_writes_stage_rows():
    collection = {
        "target": {"strict_reuse_group": "C02_generic_subject_object", "subject": CHINESE_SUBJECT},
        "debug_record": {
            "ranked_candidates": [
                {
                    "asset_id": "a_good",
                    "keyword_score": 0.62,
                    "embedding_score": 0.81,
                    "substring_score": 0.5,
                    "hybrid_score": 1.0,
                    "accepted_by": "bm25_threshold",
                    "threshold_used": 0.55,
                }
            ],
            "bm25_ranked_candidates": [{"asset_id": "a_good", "keyword_score": 0.62}],
            "embedding_ranked_candidates": [{"asset_id": "a_good", "embedding_score": 0.81}],
            "substring_ranked_candidates": [{"asset_id": "a_good", "substring_score": 0.5}],
            "thresholded_candidates": [{"asset_id": "a_good", "keyword_score": 0.62}],
        },
    }
    target_record = {
        "need_id": "n1",
        "acceptable_asset_ids": ["a_good"],
        "best_asset_ids": ["a_good"],
        "label_status": "labeled",
        "should_reuse": True,
    }

    flattened = flatten_candidate_collection(run_id="run1", target_record=target_record, collection=collection)

    assert flattened["scored_candidates"][0]["asset_id"] == "a_good"
    assert flattened["scored_candidates"][0]["rank_hybrid"] == 1
    assert flattened["scored_candidates"][0]["rank_bm25"] == 1
    assert flattened["scored_candidates"][0]["is_acceptable"] is True
    assert flattened["threshold_candidates"][0]["threshold_pass"] is True


def test_retrieve_stage_reuses_prepare_target_cache_without_llm(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "report" / "run1"
    library_dir = tmp_path / "library"
    run_dir.mkdir(parents=True)
    library_dir.mkdir()
    target = {
        "asset_kind": "page_image",
        "query": "cartoon tadpole on a lotus leaf",
        "caption": "tadpole and lotus leaf",
        "theme": "Tadpole lesson",
        "prompt_route": {},
        "background_route": {},
        "normalized_prompt": "cartoon tadpole on a lotus leaf",
        "topic_refs": ["Tadpole lesson"],
        "strict_reuse_group": "C02_generic_subject_object",
        "subject": CHINESE_SUBJECT,
        "grade_norm": "\u4e8c\u5e74\u7ea7",
        "grade_band": "\u4f4e\u5e74\u7ea7",
        "match_text": "tadpole and lotus leaf",
        "aspect_ratio": "1:1",
        "page_type": "content",
    }
    write_jsonl(
        run_dir / "targets.jsonl",
        [
            {
                "run_id": "run1",
                "need_id": "n1",
                "raw_query": "cartoon tadpole on a lotus leaf",
                "page_title": "Observe",
                "page_type": "content",
                "role": "illustration",
                "target": target,
                "label_status": "labeled",
                "should_reuse": True,
                "acceptable_asset_ids": ["a1"],
                "best_asset_ids": ["a1"],
            }
        ],
    )

    def fail_keyword_client(env_file, *, allow_llm):
        raise AssertionError("retrieve must not initialize target LLM client")

    def fake_find_reusable_ai_image_asset(**kwargs):
        from edupptx.materials.ai_image_asset_db import _target_keyword_cache_key

        assert kwargs["keyword_client"] is None
        cache = kwargs["_target_keyword_cache"]
        assert cache[_target_keyword_cache_key(target)]["strict_reuse_group"] == "C02_generic_subject_object"
        return {
            "_reuse_candidate_collection": True,
            "target": target,
            "threshold": 0.55,
            "candidates": [],
            "debug_record": {
                "ranked_candidates": [],
                "bm25_ranked_candidates": [],
                "embedding_ranked_candidates": [],
                "substring_ranked_candidates": [],
                "thresholded_candidates": [],
            },
        }

    monkeypatch.setattr("test_reuse.pipeline._keyword_client", fail_keyword_client)
    monkeypatch.setattr("test_reuse.pipeline.find_reusable_ai_image_asset", fake_find_reusable_ai_image_asset)

    run_retrieve_stage(run_dir=run_dir, library_dirs=[library_dir], allow_llm=True)

    assert read_jsonl(run_dir / "candidate_collections.jsonl")[0]["need_id"] == "n1"


def test_run_eval_writes_stage_files_without_generating_dataset(tmp_path: Path, monkeypatch):
    _install_fake_prepare_enrichment(monkeypatch)
    plan_path = tmp_path / "lesson_one.json"
    plan_path.write_text(json.dumps(_minimal_plan(), ensure_ascii=False), encoding="utf-8")
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    output_dir = tmp_path / "report"

    run_dir = run_eval(
        plan_paths=[plan_path],
        library_dirs=[library_dir],
        output_dir=output_dir,
        run_id="reuse_eval_test",
        review_enabled=False,
        allow_llm=True,
    )

    assert run_dir == output_dir / "reuse_eval_test"
    assert read_json(run_dir / "manifest.json")["run_id"] == "reuse_eval_test"
    assert len(read_jsonl(run_dir / "plan_needs.jsonl")) == 1
    assert len(read_jsonl(run_dir / "targets.jsonl")) == 1
    assert (run_dir / "hard_filter_pairs.jsonl").exists()
    assert (run_dir / "scored_candidates.jsonl").exists()
    assert (run_dir / "threshold_candidates.jsonl").exists()
    assert (run_dir / "llm_reviews.jsonl").exists()
    assert (run_dir / "final_matches.jsonl").exists()
    assert (run_dir / "metrics.json").exists()
    assert (run_dir / "report.md").exists()
    assert not (run_dir / "test_set.json").exists()


def test_stage_functions_write_outputs_incrementally(tmp_path: Path, monkeypatch):
    _install_fake_prepare_enrichment(monkeypatch)
    plan_path = tmp_path / "lesson_one.json"
    plan_path.write_text(json.dumps(_minimal_plan(), ensure_ascii=False), encoding="utf-8")
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    output_dir = tmp_path / "report"

    run_dir = prepare_run(
        plan_paths=[plan_path],
        output_dir=output_dir,
        run_id="reuse_eval_step",
        allow_llm=True,
    )

    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "plan_needs.jsonl").exists()
    assert (run_dir / "targets.jsonl").exists()
    assert not (run_dir / "hard_filter_pairs.jsonl").exists()

    run_hard_filter_stage(run_dir=run_dir, library_dirs=[library_dir])

    assert (run_dir / "hard_filter_pairs.jsonl").exists()
    assert (run_dir / "hard_filter_summary.json").exists()
    assert not (run_dir / "scored_candidates.jsonl").exists()

    run_retrieve_stage(run_dir=run_dir, library_dirs=[library_dir], allow_llm=False)

    assert (run_dir / "candidate_collections.jsonl").exists()
    assert (run_dir / "scored_candidates.jsonl").exists()
    assert (run_dir / "threshold_candidates.jsonl").exists()
    assert not (run_dir / "final_matches.jsonl").exists()

    run_review_stage(run_dir=run_dir, review_enabled=False, allow_llm=False)

    assert (run_dir / "llm_reviews.jsonl").exists()
    assert (run_dir / "final_matches.jsonl").exists()
    assert not (run_dir / "metrics.json").exists()

    run_summarize_stage(run_dir=run_dir)

    assert read_json(run_dir / "metrics.json")["target_count"] == 1
    assert (run_dir / "failure_cases.jsonl").exists()
    assert (run_dir / "prompt_issue_log.jsonl").exists()
    assert (run_dir / "report.md").exists()
    assert not (run_dir / "test_set.json").exists()


def test_summarize_uses_existing_artifacts_without_rewriting_stage_rows(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "report" / "run1"
    run_dir.mkdir(parents=True)
    write_jsonl(
        run_dir / "targets.jsonl",
        [
            {
                "need_id": "n1",
                "label_status": "labeled",
                "should_reuse": True,
                "acceptable_asset_ids": ["a1"],
                "best_asset_ids": ["a1"],
                "target": {
                    "caption": "x",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": CHINESE_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "match_text": "x",
                },
            }
        ],
    )
    write_jsonl(run_dir / "hard_filter_pairs.jsonl", [])
    write_json(run_dir / "hard_filter_summary.json", {"stage": {"candidate_hit_rate": 0.0}})
    write_jsonl(run_dir / "scored_candidates.jsonl", [])
    write_jsonl(run_dir / "threshold_candidates.jsonl", [])
    write_json(run_dir / "threshold_summary.json", {"stage": {"candidate_hit_rate": 0.0}})
    write_jsonl(
        run_dir / "final_matches.jsonl",
        [
            {
                "need_id": "n1",
                "label_status": "labeled",
                "should_reuse": True,
                "selected_asset_id": "",
                "selected_is_acceptable": False,
                "selected_is_best": False,
                "failure_stage": "threshold_filter",
            }
        ],
    )

    def fail_if_stage_rewrites(*args, **kwargs):
        raise AssertionError("summarize must not rewrite stage artifacts")

    monkeypatch.setattr("test_reuse.pipeline._write_hard_filter_outputs", fail_if_stage_rewrites)
    monkeypatch.setattr("test_reuse.pipeline._write_threshold_outputs", fail_if_stage_rewrites)

    run_summarize_stage(run_dir=run_dir)

    metrics = read_json(run_dir / "metrics.json")
    assert metrics["final"]["missed_reusable_count"] == 1


def test_report_md_is_written_in_chinese(tmp_path: Path):
    run_dir = tmp_path / "report" / "run1"
    run_dir.mkdir(parents=True)
    write_jsonl(run_dir / "targets.jsonl", [])
    write_json(run_dir / "hard_filter_summary.json", {})
    write_json(run_dir / "threshold_summary.json", {})
    write_json(run_dir / "llm_review_summary.json", {})
    write_jsonl(run_dir / "hard_filter_pairs.jsonl", [])
    write_jsonl(run_dir / "scored_candidates.jsonl", [])
    write_jsonl(run_dir / "threshold_candidates.jsonl", [])
    write_jsonl(run_dir / "final_matches.jsonl", [])

    run_summarize_stage(run_dir=run_dir)

    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "# 复用评估报告" in report
    assert "最终准确率" in report
