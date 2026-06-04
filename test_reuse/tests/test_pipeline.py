import csv
import json
import threading
import time
from pathlib import Path

import pytest

from test_reuse.pipeline import (
    _hard_filter_summary_payload,
    _write_retrieve_outputs,
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

    target = read_jsonl(run_dir / "01_prepare" / "targets.jsonl")[0]["target"]
    assert target["strict_reuse_group"] == "C02_generic_subject_object"
    assert target["match_text"] == "tadpole and lotus leaf"
    assert (run_dir / "01_prepare" / "target_enrichment.jsonl").exists()


def test_build_target_records_repairs_incomplete_prewarm_cache(tmp_path: Path, monkeypatch):
    plan_path = tmp_path / "lesson_one.json"
    plan_path.write_text(json.dumps(_minimal_plan(), ensure_ascii=False), encoding="utf-8")
    needs = extract_plan_needs(plan_path, run_id="run1")

    class FakeClient:
        pass

    def fake_prewarm(targets, keyword_client, target_keyword_cache, **kwargs):
        from edupptx.materials.ai_image_asset_db import _target_keyword_cache_key

        for target in targets:
            target_keyword_cache[_target_keyword_cache_key(target)] = dict(target)
        return len(targets)

    def fake_enrich_once(target, keyword_client, target_keyword_cache):
        from edupptx.materials.ai_image_asset_db import _target_keyword_cache_key

        enriched = dict(target)
        enriched.update(
            {
                "strict_reuse_group": "C02_generic_subject_object",
                "subject": CHINESE_SUBJECT,
                "grade_norm": "\u4e8c\u5e74\u7ea7",
                "grade_band": "\u4f4e\u5e74\u7ea7",
                "match_text": "tadpole and lotus leaf",
                "match_key": "page_image|tadpole and lotus leaf",
            }
        )
        target_keyword_cache[_target_keyword_cache_key(target)] = enriched
        return enriched

    monkeypatch.setattr("test_reuse.pipeline._prewarm_reuse_target_keywords", fake_prewarm)
    monkeypatch.setattr("test_reuse.pipeline._enrich_reuse_target_keywords_once", fake_enrich_once, raising=False)

    target_rows, enrichment_rows = build_target_records(
        needs,
        keyword_client=FakeClient(),
        require_enrichment=True,
    )

    assert target_rows[0]["target"]["strict_reuse_group"] == "C02_generic_subject_object"
    assert target_rows[0]["target"]["match_text"] == "tadpole and lotus leaf"
    assert enrichment_rows[0]["enriched"] is True


def test_build_target_records_falls_back_when_llm_repair_still_missing(tmp_path: Path, monkeypatch):
    plan_path = tmp_path / "lesson_one.json"
    plan_path.write_text(json.dumps(_minimal_plan(), ensure_ascii=False), encoding="utf-8")
    needs = extract_plan_needs(plan_path, run_id="run1")

    class FakeClient:
        pass

    def fake_prewarm(targets, keyword_client, target_keyword_cache, **kwargs):
        from edupptx.materials.ai_image_asset_db import _target_keyword_cache_key

        for target in targets:
            target_keyword_cache[_target_keyword_cache_key(target)] = dict(target)
        return len(targets)

    def fake_enrich_once(target, keyword_client, target_keyword_cache):
        return dict(target)

    monkeypatch.setattr("test_reuse.pipeline._prewarm_reuse_target_keywords", fake_prewarm)
    monkeypatch.setattr("test_reuse.pipeline._enrich_reuse_target_keywords_once", fake_enrich_once, raising=False)

    target_rows, enrichment_rows = build_target_records(
        needs,
        keyword_client=FakeClient(),
        require_enrichment=True,
    )

    target = target_rows[0]["target"]
    assert target["strict_reuse_group"] == "C03_scene_decor_container"
    assert target["match_text"] == "tadpole and lotus leaf"
    assert target["target_enrichment_fallback"] is True
    assert target_rows[0]["field_sources"]["target"] == "production_reuse_target_enrichment_fallback"
    assert "target_enrichment_fallback" in target_rows[0]["warnings"]
    assert enrichment_rows[0]["enriched"] is True
    assert enrichment_rows[0]["fallback_enriched"] is True


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
    plan["pages"][0]["material_needs"]["images"].append(
        {
            "query": "H\u2081\u53d7\u4f53\u7ed3\u6784\u56fe",
            "source": "ai_generate",
            "role": "illustration",
            "aspect_ratio": "1:1",
            "caption": "worksheet screenshot",
            "prompt_route": {"strict_reuse_group": "C00_strict_text_problem_skip"},
            "label_status": "labeled",
            "should_reuse": False,
            "acceptable_asset_ids": [],
            "best_asset_ids": [],
            "target_strict_reuse_group_gold": "C00_strict_text_problem_skip",
            "target_is_c00_skip": True,
            "gold_label_text": "H\u2081\u53d7\u4f53\u7ed3\u6784\u56fe",
            "gold_label_text_source": "query",
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

    prepare_dir = run_dir / "01_prepare"
    assert (prepare_dir / "target_classification_summary.json").exists()
    summary = read_json(prepare_dir / "target_classification_summary.json")
    assert summary["total_targets"] == 2
    mismatch_rows = list(csv.DictReader((prepare_dir / "target_class_mismatches_review.csv").open(encoding="utf-8-sig")))
    c00_rows = list(csv.DictReader((prepare_dir / "target_class_c00_cases_review.csv").open(encoding="utf-8-sig")))
    summary_rows = list(csv.DictReader((prepare_dir / "target_class_mismatch_summary.csv").open(encoding="utf-8-sig")))
    assert mismatch_rows
    assert c00_rows
    assert summary_rows
    assert mismatch_rows[0].keys() == {
        "gold_group",
        "pred_group",
        "query",
        "target_reason",
    }
    assert mismatch_rows[0]["query"] == "H\u2081\u53d7\u4f53\u7ed3\u6784\u56fe"
    assert "raw_query" not in mismatch_rows[0]
    assert "caption" not in mismatch_rows[0]
    assert "should_reuse" not in mismatch_rows[0]
    assert {row["gold_group"] for row in mismatch_rows} <= {"C00", "C01", "C02", "C03"}
    assert {row["pred_group"] for row in mismatch_rows} <= {"C00", "C01", "C02", "C03"}


def test_prepare_run_writes_prepare_artifacts_under_stage_directory(tmp_path: Path, monkeypatch):
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

    prepare_dir = run_dir / "01_prepare"
    assert len(read_jsonl(prepare_dir / "plan_needs.jsonl")) == 1
    assert len(read_jsonl(prepare_dir / "targets.jsonl")) == 1
    assert (prepare_dir / "target_enrichment.jsonl").exists()
    assert (prepare_dir / "target_enrichment_summary.json").exists()
    assert (prepare_dir / "target_classification_summary.json").exists()
    assert (prepare_dir / "target_class_mismatches_review.csv").exists()
    assert not (run_dir / "targets.jsonl").exists()
    assert not (run_dir / "target_classification_summary.json").exists()


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

    plan_need = read_jsonl(run_dir / "01_prepare" / "plan_needs.jsonl")[0]
    target = read_jsonl(run_dir / "01_prepare" / "targets.jsonl")[0]
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

    plan_need = read_jsonl(run_dir / "01_prepare" / "plan_needs.jsonl")[0]
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


def test_hard_filter_rows_include_independent_ablation_flags_for_reports():
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
        "acceptable_asset_ids": ["a_general_math"],
        "best_asset_ids": ["a_general_math"],
        "label_status": "labeled",
        "should_reuse": True,
    }
    rows = hard_filter_rows_for_target(
        target_record,
        [
            {
                "asset_id": "a_general_math",
                "asset_kind": "page_image",
                "strict_reuse_group": "C02_generic_subject_object",
                "subject": MATH_SUBJECT,
                "grade_norm": "\u4e8c\u5e74\u7ea7",
                "grade_band": "\u4f4e\u5e74\u7ea7",
                "general": True,
                "aspect_ratio": "16:9",
            },
            {
                "asset_id": "a_bad_group_and_size",
                "asset_kind": "page_image",
                "strict_reuse_group": "C03_scene_decor_container",
                "subject": CHINESE_SUBJECT,
                "grade_norm": "\u4e8c\u5e74\u7ea7",
                "grade_band": "\u4f4e\u5e74\u7ea7",
                "general": False,
                "aspect_ratio": "16:9",
            },
        ],
    )

    by_id = {row["asset_id"]: row for row in rows}
    assert by_id["a_general_math"]["subject_only_pass"] is True
    assert by_id["a_general_math"]["size_only_pass"] is False
    assert by_id["a_general_math"]["subject_size_pass"] is False
    assert by_id["a_bad_group_and_size"]["category_only_pass"] is False
    assert by_id["a_bad_group_and_size"]["size_only_pass"] is False


def test_hard_filter_rows_treat_other_subject_as_general_and_ignore_unknown_grade():
    target_record = {
        "run_id": "run1",
        "need_id": "n1",
        "target": {
            "asset_kind": "page_image",
            "strict_reuse_group": "C02_generic_subject_object",
            "subject": CHINESE_SUBJECT,
            "grade_norm": "其他",
            "grade_band": "其他",
            "aspect_ratio": "1:1",
            "caption": "classroom object",
        },
        "acceptable_asset_ids": ["a_other_non_general"],
        "best_asset_ids": ["a_other_non_general"],
        "label_status": "labeled",
        "should_reuse": True,
    }
    rows = hard_filter_rows_for_target(
        target_record,
        [
            {
                "asset_id": "a_other_non_general",
                "asset_kind": "page_image",
                "strict_reuse_group": "C02_generic_subject_object",
                "subject": "其他",
                "grade_norm": "其他",
                "grade_band": "其他",
                "general": False,
                "aspect_ratio": "1:1",
            },
            {
                "asset_id": "a_known_non_general_cross_subject",
                "asset_kind": "page_image",
                "strict_reuse_group": "C02_generic_subject_object",
                "subject": MATH_SUBJECT,
                "grade_norm": "其他",
                "grade_band": "其他",
                "general": False,
                "aspect_ratio": "1:1",
            },
        ],
    )

    by_id = {row["asset_id"]: row for row in rows}
    assert by_id["a_other_non_general"]["subject_only_pass"] is True
    assert by_id["a_other_non_general"]["all_hard_pass"] is True
    assert by_id["a_other_non_general"]["reject_reasons"] == []
    assert by_id["a_known_non_general_cross_subject"]["subject_only_pass"] is False
    assert by_id["a_known_non_general_cross_subject"]["reject_reasons"] == ["subject_mismatch"]


def test_hard_filter_rows_allow_enumerated_cross_aspect_size_pair():
    target_record = {
        "run_id": "run1",
        "need_id": "n1",
        "target": {
            "asset_kind": "page_image",
            "strict_reuse_group": "C02_generic_subject_object",
            "subject": CHINESE_SUBJECT,
            "grade_norm": "\u4e8c\u5e74\u7ea7",
            "grade_band": "\u4f4e\u5e74\u7ea7",
            "aspect_ratio": "4:3",
            "caption": "wide-friendly illustration",
        },
        "acceptable_asset_ids": ["a_wide"],
        "best_asset_ids": ["a_wide"],
        "label_status": "labeled",
        "should_reuse": True,
    }

    rows = hard_filter_rows_for_target(
        target_record,
        [
            {
                "asset_id": "a_wide",
                "asset_kind": "page_image",
                "strict_reuse_group": "C02_generic_subject_object",
                "subject": CHINESE_SUBJECT,
                "grade_norm": "\u4e8c\u5e74\u7ea7",
                "grade_band": "\u4f4e\u5e74\u7ea7",
                "general": False,
                "aspect_ratio": "16:9",
            }
        ],
    )

    assert rows[0]["size_only_pass"] is True
    assert rows[0]["aspect_pass"] is True
    assert rows[0]["all_hard_pass"] is True
    assert rows[0]["reject_reasons"] == []


def test_hard_filter_merge_c01_c03_allows_cross_group_category_pairs():
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
        "acceptable_asset_ids": ["a_c03"],
        "best_asset_ids": ["a_c03"],
        "label_status": "labeled",
        "should_reuse": True,
    }
    assets = [
        {
            "asset_id": "a_c03",
            "asset_kind": "page_image",
            "strict_reuse_group": "C03_scene_decor_container",
            "subject": CHINESE_SUBJECT,
            "grade_norm": "\u4e8c\u5e74\u7ea7",
            "grade_band": "\u4f4e\u5e74\u7ea7",
            "general": False,
            "aspect_ratio": "1:1",
        },
        {
            "asset_id": "a_c00",
            "asset_kind": "page_image",
            "strict_reuse_group": "C00_strict_text_problem_skip",
            "subject": CHINESE_SUBJECT,
            "grade_norm": "\u4e8c\u5e74\u7ea7",
            "grade_band": "\u4f4e\u5e74\u7ea7",
            "general": False,
            "aspect_ratio": "1:1",
        },
    ]

    baseline_rows = hard_filter_rows_for_target(target_record, assets)
    merge_rows = hard_filter_rows_for_target(
        target_record,
        assets,
        category_routing="merge-c01-c03",
    )
    baseline_by_id = {row["asset_id"]: row for row in baseline_rows}
    merge_by_id = {row["asset_id"]: row for row in merge_rows}

    assert baseline_by_id["a_c03"]["category_pass"] is False
    assert baseline_by_id["a_c03"]["all_hard_pass"] is False
    assert baseline_by_id["a_c03"]["reject_reasons"] == ["strict_reuse_group_mismatch"]
    assert merge_by_id["a_c03"]["category_pass"] is True
    assert merge_by_id["a_c03"]["category_only_pass"] is True
    assert merge_by_id["a_c03"]["all_hard_pass"] is True
    assert merge_by_id["a_c00"]["category_pass"] is False
    assert merge_by_id["a_c00"]["all_hard_pass"] is False


def test_hard_filter_summary_writes_filter_ablation_report(tmp_path: Path):
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
    write_jsonl(
        run_dir / "targets.jsonl",
        [
            {
                "run_id": "run1",
                "need_id": "n1",
                "target": target,
                "label_status": "labeled",
                "should_reuse": True,
                "acceptable_asset_ids": ["a_general_math"],
                "best_asset_ids": ["a_general_math"],
            }
        ],
    )
    write_json(
        split_dir / "C02_generic_subject_object.json",
        {
            "schema_version": 2,
            "strict_reuse_group": "C02_generic_subject_object",
            "asset_count": 2,
            "assets": [
                {
                    "asset_id": "a_general_math",
                    "asset_kind": "page_image",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": MATH_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "general": True,
                    "aspect_ratio": "16:9",
                },
                {
                    "asset_id": "a_wrong_subject",
                    "asset_kind": "page_image",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": MATH_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "general": False,
                    "aspect_ratio": "1:1",
                },
            ],
        },
    )

    run_hard_filter_stage(run_dir=run_dir, library_dirs=[library_dir])

    summary = read_json(run_dir / "02_hard_filter" / "hard_filter_summary.json")
    ablation = summary["filter_ablation"]
    assert set(ablation) == {"size_only", "subject_only", "category_only", "subject_size"}
    assert ablation["subject_only"]["candidate_hit_rate"] == 1.0
    assert ablation["subject_only"]["best_hit_rate"] == 1.0
    assert ablation["subject_only"]["pair_metrics"]["precision"] == 1.0
    assert ablation["size_only"]["candidate_hit_rate"] == 0.0
    assert ablation["subject_size"]["candidate_hit_rate"] == 0.0
    size_gold = summary["size_compatible_gold"]
    assert size_gold["gold_adjustment"]["removed_acceptable_pair_count"] == 1
    assert size_gold["filter_ablation"]["size_only"]["candidate_hit_rate"] == 0.0


def test_hard_filter_summary_reports_non_c00_target_match_counts():
    targets = [
        {
            "need_id": "n1",
            "label_status": "labeled",
            "should_reuse": True,
            "acceptable_asset_ids": ["a1", "a2"],
            "best_asset_ids": ["a1"],
            "target": {"strict_reuse_group": "C01_irreplaceable_entity_event_action"},
        },
        {
            "need_id": "n2",
            "label_status": "labeled",
            "should_reuse": True,
            "acceptable_asset_ids": ["b1"],
            "best_asset_ids": ["b1"],
            "target": {"strict_reuse_group": "C02_generic_subject_object"},
        },
        {
            "need_id": "n3",
            "label_status": "labeled",
            "should_reuse": False,
            "acceptable_asset_ids": [],
            "best_asset_ids": [],
            "target": {"strict_reuse_group": "C00_strict_text_problem_skip"},
        },
        {
            "need_id": "n4",
            "label_status": "labeled",
            "should_reuse": False,
            "acceptable_asset_ids": [],
            "best_asset_ids": [],
            "target": {"strict_reuse_group": "C03_scene_decor_container"},
        },
    ]
    hard_rows = [
        {
            "need_id": "n1",
            "asset_id": "a1",
            "label_status": "labeled",
            "target_strict_reuse_group": "C01_irreplaceable_entity_event_action",
            "all_hard_pass": True,
            "is_acceptable": True,
            "is_best": True,
        },
        {
            "need_id": "n1",
            "asset_id": "a2",
            "label_status": "labeled",
            "target_strict_reuse_group": "C01_irreplaceable_entity_event_action",
            "all_hard_pass": False,
            "is_acceptable": True,
            "is_best": False,
            "reject_reasons": ["aspect_ratio_too_far"],
        },
        {
            "need_id": "n2",
            "asset_id": "b1",
            "label_status": "labeled",
            "target_strict_reuse_group": "C02_generic_subject_object",
            "all_hard_pass": False,
            "is_acceptable": True,
            "is_best": True,
            "reject_reasons": ["subject_mismatch"],
        },
        {
            "need_id": "n4",
            "asset_id": "decor",
            "label_status": "labeled",
            "target_strict_reuse_group": "C03_scene_decor_container",
            "all_hard_pass": True,
            "is_acceptable": False,
            "is_best": False,
        },
    ]

    summary = _hard_filter_summary_payload(hard_rows, targets=targets)
    counts = summary["non_c00_target_match_counts"]

    assert "C00_strict_text_problem_skip" not in counts
    assert counts["C01_irreplaceable_entity_event_action"]["target_count"] == 1
    assert counts["C01_irreplaceable_entity_event_action"]["reusable_need_count"] == 1
    assert counts["C01_irreplaceable_entity_event_action"]["acceptable_gold_pair_count"] == 2
    assert counts["C01_irreplaceable_entity_event_action"]["best_gold_pair_count"] == 1
    assert counts["C01_irreplaceable_entity_event_action"]["candidate_pair_count"] == 2
    assert counts["C01_irreplaceable_entity_event_action"]["hard_pass_pair_count"] == 1
    assert counts["C01_irreplaceable_entity_event_action"]["candidate_hit_need_count"] == 1
    assert counts["C01_irreplaceable_entity_event_action"]["candidate_hit_rate"] == 1.0
    assert counts["C02_generic_subject_object"]["candidate_hit_need_count"] == 0
    assert counts["C03_scene_decor_container"]["reusable_need_count"] == 0


def test_hard_filter_stage_writes_only_size_gold_rejection_combo_table(tmp_path: Path):
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
    write_jsonl(
        run_dir / "targets.jsonl",
        [
            {
                "run_id": "run1",
                "need_id": "n1",
                "target": target,
                "label_status": "labeled",
                "should_reuse": True,
                "acceptable_asset_ids": ["a_wrong_size"],
                "best_asset_ids": ["a_wrong_size"],
            }
        ],
    )
    write_json(
        split_dir / "C02_generic_subject_object.json",
        {
            "schema_version": 2,
            "strict_reuse_group": "C02_generic_subject_object",
            "asset_count": 2,
            "assets": [
                {
                    "asset_id": "a_wrong_size",
                    "asset_kind": "page_image",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": CHINESE_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "general": False,
                    "aspect_ratio": "16:9",
                },
                {
                    "asset_id": "a_same_size",
                    "asset_kind": "page_image",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": CHINESE_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "general": False,
                    "aspect_ratio": "1:1",
                },
            ],
        },
    )

    stage_dir = run_dir / "02_hard_filter"
    stage_dir.mkdir(parents=True)
    obsolete_size_outputs = [
        "size_filter_rejections.csv",
        "size_filter_rejection_summary.csv",
        "size_filter_rejection_by_target.csv",
        "size_filter_target_stats_sorted.csv",
    ]
    for name in obsolete_size_outputs:
        (stage_dir / name).write_text("stale\n", encoding="utf-8")
    obsolete_examples = stage_dir / "size_padding_examples"
    obsolete_examples.mkdir()
    (obsolete_examples / "stale.png").write_bytes(b"stale")

    run_hard_filter_stage(run_dir=run_dir, library_dirs=[library_dir])

    combo_path = stage_dir / "size_filter_gold_rejection_by_aspect_combo.csv"
    assert combo_path.exists()
    assert all(not (stage_dir / name).exists() for name in obsolete_size_outputs)
    assert not obsolete_examples.exists()
    combo_rows = list(csv.DictReader(combo_path.open(encoding="utf-8-sig")))
    assert combo_rows == [
        {
            "target_aspect_ratio": "1:1",
            "candidate_aspect_ratio": "16:9",
            "acceptable_rejected_pair_count": "1",
            "best_rejected_pair_count": "1",
            "acceptable_affected_need_count": "1",
            "best_affected_need_count": "1",
            "rejected_pair_count": "1",
            "affected_need_count": "1",
        }
    ]


def test_hard_filter_stage_does_not_write_size_rejection_rates_by_target(tmp_path: Path):
    run_dir = tmp_path / "report" / "run1"
    library_dir = tmp_path / "library"
    split_dir = library_dir / "strict_reuse_indexes"
    run_dir.mkdir(parents=True)
    split_dir.mkdir(parents=True)
    write_jsonl(
        run_dir / "targets.jsonl",
        [
            {
                "run_id": "run1",
                "need_id": "n1",
                "raw_query": "方形青蛙插画",
                "target": {
                    "caption": "方形青蛙",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": CHINESE_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "match_text": "方形青蛙",
                    "asset_kind": "page_image",
                    "aspect_ratio": "1:1",
                },
                "label_status": "labeled",
                "should_reuse": True,
                "acceptable_asset_ids": ["a_wrong_size"],
                "best_asset_ids": ["a_wrong_size"],
            },
            {
                "run_id": "run1",
                "need_id": "n2",
                "raw_query": "宽屏荷叶插画",
                "target": {
                    "caption": "宽屏荷叶",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": CHINESE_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "match_text": "宽屏荷叶",
                    "asset_kind": "page_image",
                    "aspect_ratio": "16:9",
                },
                "label_status": "labeled",
                "should_reuse": True,
                "acceptable_asset_ids": ["a_wrong_size"],
                "best_asset_ids": ["a_wrong_size"],
            },
        ],
    )
    write_json(
        split_dir / "C02_generic_subject_object.json",
        {
            "schema_version": 2,
            "strict_reuse_group": "C02_generic_subject_object",
            "asset_count": 2,
            "assets": [
                {
                    "asset_id": "a_wrong_size",
                    "asset_kind": "page_image",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": CHINESE_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "general": False,
                    "aspect_ratio": "16:9",
                },
                {
                    "asset_id": "a_square",
                    "asset_kind": "page_image",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": CHINESE_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "general": False,
                    "aspect_ratio": "1:1",
                },
            ],
        },
    )

    run_hard_filter_stage(run_dir=run_dir, library_dirs=[library_dir])

    stage_dir = run_dir / "02_hard_filter"
    assert not (stage_dir / "size_filter_rejection_by_target.csv").exists()
    assert (stage_dir / "size_filter_gold_rejection_by_aspect_combo.csv").exists()


def test_hard_filter_stage_writes_subject_false_rejection_review_table(tmp_path: Path):
    run_dir = tmp_path / "report" / "run1"
    library_dir = tmp_path / "library"
    split_dir = library_dir / "strict_reuse_indexes"
    run_dir.mkdir(parents=True)
    split_dir.mkdir(parents=True)
    target = {
        "caption": "荷叶上的青蛙插图",
        "strict_reuse_group": "C02_generic_subject_object",
        "subject": CHINESE_SUBJECT,
        "grade_norm": "\u4e8c\u5e74\u7ea7",
        "grade_band": "\u4f4e\u5e74\u7ea7",
        "match_text": "荷叶上的青蛙插图",
        "asset_kind": "page_image",
        "aspect_ratio": "1:1",
    }
    write_jsonl(
        run_dir / "targets.jsonl",
        [
            {
                "run_id": "run1",
                "need_id": "n1",
                "raw_query": "一只青蛙蹲在荷叶上的卡通插画",
                "target": target,
                "label_status": "labeled",
                "should_reuse": True,
                "acceptable_asset_ids": ["a_math_labeled_ok"],
                "best_asset_ids": ["a_math_labeled_ok"],
            }
        ],
    )
    write_json(
        split_dir / "C02_generic_subject_object.json",
        {
            "schema_version": 2,
            "strict_reuse_group": "C02_generic_subject_object",
            "asset_count": 2,
            "assets": [
                {
                    "asset_id": "a_math_labeled_ok",
                    "asset_kind": "page_image",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": MATH_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "general": False,
                    "aspect_ratio": "1:1",
                    "caption": "荷叶上的青蛙卡通图",
                },
                {
                    "asset_id": "a_math_not_labeled",
                    "asset_kind": "page_image",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "subject": MATH_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "general": False,
                    "aspect_ratio": "1:1",
                    "caption": "数学计数青蛙图",
                },
            ],
        },
    )

    run_hard_filter_stage(run_dir=run_dir, library_dirs=[library_dir])

    legacy_review_path = run_dir / "02_hard_filter" / "subject_filter_false_rejections.csv"
    review_path = run_dir / "02_hard_filter" / "subject_only_false_rejections.csv"
    assert legacy_review_path.exists()
    assert review_path.exists()
    legacy_rows = list(csv.DictReader(legacy_review_path.open(encoding="utf-8-sig")))
    rows = list(csv.DictReader(review_path.open(encoding="utf-8-sig")))
    assert legacy_rows == rows
    assert len(rows) == 1
    assert rows[0]["need_id"] == "n1"
    assert rows[0]["asset_id"] == "a_math_labeled_ok"
    assert rows[0]["target_query"] == "一只青蛙蹲在荷叶上的卡通插画"
    assert rows[0]["target_caption"] == "荷叶上的青蛙插图"
    assert rows[0]["candidate_query"] == ""
    assert rows[0]["candidate_caption"] == "荷叶上的青蛙卡通图"
    assert rows[0]["target_subject"] == CHINESE_SUBJECT
    assert rows[0]["candidate_subject"] == MATH_SUBJECT
    assert rows[0]["candidate_general"] == "False"
    assert rows[0]["is_best"] == "True"


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

    rows = read_jsonl(run_dir / "02_hard_filter" / "hard_filter_pairs.jsonl")
    assert [row["asset_id"] for row in rows] == ["a_routed"]


def test_hard_filter_stage_merge_c01_c03_reads_three_split_groups(tmp_path: Path):
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
    write_jsonl(
        run_dir / "targets.jsonl",
        [
            {
                "run_id": "run1",
                "need_id": "n1",
                "target": target,
                "label_status": "labeled",
                "should_reuse": True,
                "acceptable_asset_ids": ["a_c03"],
                "best_asset_ids": ["a_c03"],
            }
        ],
    )
    for group, asset_id in (
        ("C01_irreplaceable_entity_event_action", "a_c01"),
        ("C02_generic_subject_object", "a_c02"),
        ("C03_scene_decor_container", "a_c03"),
    ):
        write_json(
            split_dir / f"{group}.json",
            {
                "schema_version": 2,
                "strict_reuse_group": group,
                "asset_count": 1,
                "assets": [
                    {
                        "asset_id": asset_id,
                        "asset_kind": "page_image",
                        "strict_reuse_group": group,
                        "subject": CHINESE_SUBJECT,
                        "grade_norm": "\u4e8c\u5e74\u7ea7",
                        "grade_band": "\u4f4e\u5e74\u7ea7",
                        "general": False,
                        "aspect_ratio": "1:1",
                    }
                ],
            },
        )
    write_json(
        split_dir / "C00_strict_text_problem_skip.json",
        {
            "schema_version": 2,
            "strict_reuse_group": "C00_strict_text_problem_skip",
            "asset_count": 1,
            "assets": [
                {
                    "asset_id": "a_c00",
                    "asset_kind": "page_image",
                    "strict_reuse_group": "C00_strict_text_problem_skip",
                    "subject": CHINESE_SUBJECT,
                    "grade_norm": "\u4e8c\u5e74\u7ea7",
                    "grade_band": "\u4f4e\u5e74\u7ea7",
                    "general": False,
                    "aspect_ratio": "1:1",
                }
            ],
        },
    )

    run_hard_filter_stage(
        run_dir=run_dir,
        library_dirs=[library_dir],
        category_routing="merge-c01-c03",
    )

    rows = read_jsonl(run_dir / "02_hard_filter" / "hard_filter_pairs.jsonl")
    by_id = {row["asset_id"]: row for row in rows}
    assert set(by_id) == {"a_c01", "a_c02", "a_c03"}
    assert by_id["a_c03"]["category_pass"] is True
    assert by_id["a_c03"]["all_hard_pass"] is True
    assert read_json(run_dir / "02_hard_filter" / "hard_filter_summary.json")["category_routing"] == "merge-c01-c03"
    comparison = read_json(run_dir / "02_hard_filter" / "category_routing_comparison.json")
    assert comparison["baseline"]["category_routing"] == "baseline"
    assert comparison["merge_no_llm"]["category_routing"] == "merge-c01-c03"
    assert comparison["baseline"]["candidate_pair_count"] == 1
    assert comparison["merge_no_llm"]["candidate_pair_count"] == 3
    assert comparison["delta"]["candidate_pair_count"] == 2


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

    assert read_jsonl(run_dir / "02_hard_filter" / "hard_filter_pairs.jsonl")[0]["asset_id"] == "a1"


def test_hard_filter_reads_legacy_prepare_artifacts_and_writes_stage_directory(tmp_path: Path):
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

    run_hard_filter_stage(run_dir=run_dir, library_dirs=[library_dir])

    assert read_jsonl(run_dir / "02_hard_filter" / "hard_filter_pairs.jsonl")[0]["asset_id"] == "a1"
    assert (run_dir / "02_hard_filter" / "hard_filter_summary.json").exists()
    assert not (run_dir / "hard_filter_pairs.jsonl").exists()


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
    (run_dir / "candidate_score_audit.jsonl").write_text("", encoding="utf-8")

    run_review_stage(run_dir=run_dir, review_enabled=False, allow_llm=False)

    assert (run_dir / "04_review" / "llm_review_summary.json").exists()


def test_review_stage_summary_counts_policy_and_llm_review_required_candidates(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "report" / "run1"
    run_dir.mkdir(parents=True)
    target = {
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
        "should_reuse": True,
        "acceptable_asset_ids": ["a1"],
        "best_asset_ids": ["a1"],
    }
    write_jsonl(run_dir / "targets.jsonl", [target])
    write_jsonl(
        run_dir / "candidate_collections.jsonl",
        [
            {
                "run_id": "run1",
                "need_id": "n1",
                "collection": {
                    "_reuse_candidate_collection": True,
                    "candidates": [
                        {
                            "asset": {"asset_id": "a1"},
                            "reuse_policy": {
                                "llm_review_required": True,
                                "llm_review_performed": True,
                                "llm_review": {"decision": "accept", "score": 0.8},
                            },
                        },
                        {
                            "asset": {"asset_id": "a2"},
                            "reuse_policy": {
                                "llm_review_required": True,
                                "llm_review_performed": False,
                            },
                        },
                        {
                            "asset": {"asset_id": "a3"},
                            "reuse_policy": {
                                "llm_review_required": False,
                                "llm_review_performed": True,
                                "llm_review": {"decision": "reject", "score": 0.2},
                            },
                        },
                    ],
                },
            }
        ],
    )
    write_jsonl(
        run_dir / "candidate_score_audit.jsonl",
        [
            {"need_id": "n1", "asset_id": "a1"},
            {"need_id": "n1", "asset_id": "a2"},
            {"need_id": "n1", "asset_id": "a3"},
        ],
    )

    monkeypatch.setattr("test_reuse.pipeline._finalize_reuse_candidate_collection", lambda *args, **kwargs: None)

    run_review_stage(run_dir=run_dir, review_enabled=False, allow_llm=False)

    summary = read_json(run_dir / "04_review" / "llm_review_summary.json")
    assert summary["policy_candidate_count"] == 3
    assert summary["review_candidate_count"] == 3
    assert summary["llm_review_required_count"] == 2
    assert summary["reviewed_count"] == 2
    assert summary["llm_review_required_rate"] == 2 / 3


def test_review_stage_requires_allow_llm_when_review_enabled(tmp_path: Path):
    run_dir = tmp_path / "report" / "run1"
    run_dir.mkdir(parents=True)
    write_jsonl(run_dir / "targets.jsonl", [])
    write_jsonl(run_dir / "candidate_collections.jsonl", [])
    write_jsonl(run_dir / "candidate_score_audit.jsonl", [])

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
    candidate_score_rows = []
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
        candidate_score_rows.append(
            {
                "run_id": "run1",
                "need_id": need_id,
                "asset_id": "strict_a",
                "policy_input": True,
                "is_acceptable": True,
                "is_best": True,
            }
        )
    write_jsonl(run_dir / "targets.jsonl", targets)
    write_jsonl(run_dir / "candidate_collections.jsonl", collections)
    write_jsonl(run_dir / "candidate_score_audit.jsonl", candidate_score_rows)

    def fake_finalize(collection, **kwargs):
        assert kwargs.get("vlm_client") is None
        assert kwargs.get("near_miss_vlm_state") is None
        return {
            "asset": dict(strict_asset),
            "keyword_score": 0.9,
            "hybrid_score": 0.9,
            "reuse_policy": {"decision": "direct_reuse"},
        }

    monkeypatch.setattr("test_reuse.pipeline._finalize_reuse_candidate_collection", fake_finalize)

    run_review_stage(run_dir=run_dir, review_enabled=False, allow_llm=False)

    final_rows = read_jsonl(run_dir / "04_review" / "final_matches.jsonl")
    assert [row["selected_asset_id"] for row in final_rows] == ["strict_a", "strict_a", ""]
    assert final_rows[2]["waterfall_stage"] == "policy_reject"


def test_review_stage_finalizes_targets_in_parallel_preserving_output_order(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "report" / "run1"
    run_dir.mkdir(parents=True)
    targets = []
    collections = []
    candidate_score_rows = []
    for index in range(3):
        need_id = f"n{index + 1}"
        asset_id = f"a{index + 1}"
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
                "acceptable_asset_ids": [asset_id],
                "best_asset_ids": [asset_id],
            }
        )
        collections.append(
            {
                "run_id": "run1",
                "need_id": need_id,
                "collection": {"_reuse_candidate_collection": True, "candidates": [{"asset": {"asset_id": asset_id}}]},
            }
        )
        candidate_score_rows.append(
            {
                "run_id": "run1",
                "need_id": need_id,
                "asset_id": asset_id,
                "policy_input": True,
                "is_acceptable": True,
                "is_best": True,
            }
        )
    write_jsonl(run_dir / "targets.jsonl", targets)
    write_jsonl(run_dir / "candidate_collections.jsonl", collections)
    write_jsonl(run_dir / "candidate_score_audit.jsonl", candidate_score_rows)

    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_finalize(collection, **kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        asset = collection["candidates"][0]["asset"]
        return {
            "asset": dict(asset),
            "keyword_score": 0.9,
            "hybrid_score": 0.9,
            "reuse_policy": {"decision": "direct_reuse"},
        }

    monkeypatch.setenv("EDUPPTX_REUSE_POLICY_WORKERS", "2")
    monkeypatch.setattr("test_reuse.pipeline._finalize_reuse_candidate_collection", fake_finalize)

    run_review_stage(run_dir=run_dir, review_enabled=False, allow_llm=False)

    final_rows = read_jsonl(run_dir / "04_review" / "final_matches.jsonl")
    assert max_active >= 2
    assert [row["need_id"] for row in final_rows] == ["n1", "n2", "n3"]
    assert [row["selected_asset_id"] for row in final_rows] == ["a1", "a2", "a3"]


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
                    "policy_score": 0.67,
                    "hybrid_score": 1.0,
                    "threshold_used": 0.55,
                }
            ],
            "bm25_ranked_candidates": [{"asset_id": "a_good", "keyword_score": 0.62}],
            "embedding_ranked_candidates": [{"asset_id": "a_good", "embedding_score": 0.81}],
            "substring_ranked_candidates": [{"asset_id": "a_good", "substring_score": 0.5}],
            "policy_input_candidates": [{"asset_id": "a_good", "keyword_score": 0.62}],
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

    rows = flattened["candidate_score_audit"]
    assert rows[0]["asset_id"] == "a_good"
    assert rows[0]["rank_hybrid"] == 1
    assert rows[0]["rank_bm25"] == 1
    assert rows[0]["is_acceptable"] is True
    assert rows[0]["policy_input"] is True


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
        assert kwargs["reuse_search_context"].query_embedding_cache_dir == run_dir / "03_retrieve"
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
                "policy_input_candidates": [],
            },
        }

    monkeypatch.setattr("test_reuse.pipeline._keyword_client", fail_keyword_client)
    monkeypatch.setattr("test_reuse.pipeline.find_reusable_ai_image_asset", fake_find_reusable_ai_image_asset)

    run_retrieve_stage(run_dir=run_dir, library_dirs=[library_dir], allow_llm=True)

    assert read_jsonl(run_dir / "03_retrieve" / "candidate_collections.jsonl")[0]["need_id"] == "n1"


def test_retrieve_stage_collects_targets_in_parallel_preserving_output_order(tmp_path: Path, monkeypatch):
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
    targets = []
    for index in range(3):
        targets.append(
            {
                "run_id": "run1",
                "need_id": f"n{index + 1}",
                "raw_query": f"cartoon tadpole {index + 1}",
                "page_title": "Observe",
                "page_type": "content",
                "role": "illustration",
                "target": dict(target),
                "label_status": "labeled",
                "should_reuse": True,
                "acceptable_asset_ids": [f"a{index + 1}"],
                "best_asset_ids": [f"a{index + 1}"],
            }
        )
    write_jsonl(run_dir / "targets.jsonl", targets)

    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_find_reusable_ai_image_asset(**kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        asset_id = kwargs["prompt"].replace("cartoon tadpole ", "a")
        return {
            "_reuse_candidate_collection": True,
            "target": kwargs["_target_keyword_cache"][next(iter(kwargs["_target_keyword_cache"]))],
            "threshold": 0.55,
            "candidates": [{"asset": {"asset_id": asset_id}}],
            "debug_record": {
                "ranked_candidates": [{"asset_id": asset_id, "keyword_score": 0.9}],
                "bm25_ranked_candidates": [{"asset_id": asset_id, "keyword_score": 0.9}],
                "embedding_ranked_candidates": [],
                "substring_ranked_candidates": [],
                "policy_input_candidates": [{"asset_id": asset_id, "keyword_score": 0.9}],
            },
        }

    monkeypatch.setenv("EDUPPTX_REUSE_SEARCH_WORKERS", "2")
    monkeypatch.setattr("test_reuse.pipeline.find_reusable_ai_image_asset", fake_find_reusable_ai_image_asset)

    run_retrieve_stage(run_dir=run_dir, library_dirs=[library_dir], allow_llm=False)

    collection_rows = read_jsonl(run_dir / "03_retrieve" / "candidate_collections.jsonl")
    assert max_active >= 2
    assert [row["need_id"] for row in collection_rows] == ["n1", "n2", "n3"]
    assert [_asset["asset_id"] for _asset in [row["collection"]["candidates"][0]["asset"] for row in collection_rows]] == [
        "a1",
        "a2",
        "a3",
    ]


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
    assert len(read_jsonl(run_dir / "01_prepare" / "plan_needs.jsonl")) == 1
    assert len(read_jsonl(run_dir / "01_prepare" / "targets.jsonl")) == 1
    assert (run_dir / "02_hard_filter" / "hard_filter_pairs.jsonl").exists()
    assert (run_dir / "03_retrieve" / "candidate_score_audit.jsonl").exists()
    assert (run_dir / "03_retrieve" / "retrieve_summary.json").exists()
    assert (run_dir / "04_review" / "llm_reviews.jsonl").exists()
    assert (run_dir / "04_review" / "final_matches.jsonl").exists()
    assert (run_dir / "05_summarize" / "metrics.json").exists()
    assert (run_dir / "05_summarize" / "report.md").exists()
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
    assert (run_dir / "01_prepare" / "plan_needs.jsonl").exists()
    assert (run_dir / "01_prepare" / "targets.jsonl").exists()
    assert not (run_dir / "02_hard_filter" / "hard_filter_pairs.jsonl").exists()

    run_hard_filter_stage(run_dir=run_dir, library_dirs=[library_dir])

    assert (run_dir / "02_hard_filter" / "hard_filter_pairs.jsonl").exists()
    assert (run_dir / "02_hard_filter" / "hard_filter_summary.json").exists()
    assert not (run_dir / "03_retrieve" / "candidate_score_audit.jsonl").exists()

    run_retrieve_stage(run_dir=run_dir, library_dirs=[library_dir], allow_llm=False)

    assert (run_dir / "03_retrieve" / "candidate_collections.jsonl").exists()
    assert (run_dir / "03_retrieve" / "candidate_score_audit.jsonl").exists()
    assert (run_dir / "03_retrieve" / "retrieve_summary.json").exists()
    assert not (run_dir / "04_review" / "final_matches.jsonl").exists()

    run_review_stage(run_dir=run_dir, review_enabled=False, allow_llm=False)

    assert (run_dir / "04_review" / "llm_reviews.jsonl").exists()
    assert (run_dir / "04_review" / "final_matches.jsonl").exists()
    assert not (run_dir / "05_summarize" / "metrics.json").exists()

    run_summarize_stage(run_dir=run_dir)

    assert read_json(run_dir / "05_summarize" / "metrics.json")["target_count"] == 1
    assert (run_dir / "05_summarize" / "failure_cases.jsonl").exists()
    assert (run_dir / "05_summarize" / "prompt_issue_log.jsonl").exists()
    assert (run_dir / "05_summarize" / "report.md").exists()
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
    write_jsonl(run_dir / "candidate_score_audit.jsonl", [])
    write_json(run_dir / "retrieve_summary.json", {"ranking": {"candidate_hit_rate": 0.0}})
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
                "waterfall_stage": "retrieval_no_candidate",
                "failure_stage": "retrieval_no_candidate",
            }
        ],
    )

    def fail_if_stage_rewrites(*args, **kwargs):
        raise AssertionError("summarize must not rewrite stage artifacts")

    monkeypatch.setattr("test_reuse.pipeline._write_hard_filter_outputs", fail_if_stage_rewrites)
    monkeypatch.setattr("test_reuse.pipeline._write_retrieve_outputs", fail_if_stage_rewrites)

    run_summarize_stage(run_dir=run_dir)

    metrics = read_json(run_dir / "05_summarize" / "metrics.json")
    assert metrics["final"]["missed_reusable_count"] == 0
    assert metrics["final_raw_gold_audit"]["missed_reusable_count"] == 1


def test_summarize_writes_asset_kind_bucket_metrics(tmp_path: Path):
    run_dir = tmp_path / "report" / "run1"
    run_dir.mkdir(parents=True)
    write_jsonl(
        run_dir / "targets.jsonl",
        [
            {
                "need_id": "n1",
                "label_status": "labeled",
                "should_reuse": True,
                "acceptable_asset_ids": ["page_gold"],
                "best_asset_ids": ["page_gold"],
                "acceptable_asset_metadata": [
                    {"asset_id": "page_gold", "asset_kind": "page_image"},
                ],
                "best_asset_metadata": [
                    {"asset_id": "page_gold", "asset_kind": "page_image"},
                ],
            },
            {
                "need_id": "n2",
                "label_status": "labeled",
                "should_reuse": True,
                "acceptable_asset_ids": ["bg_gold"],
                "best_asset_ids": ["bg_gold"],
                "acceptable_asset_metadata": [
                    {"asset_id": "bg_gold", "asset_kind": "background"},
                ],
                "best_asset_metadata": [
                    {"asset_id": "bg_gold", "asset_kind": "background"},
                ],
            },
        ],
    )
    write_jsonl(run_dir / "hard_filter_pairs.jsonl", [])
    write_json(run_dir / "hard_filter_summary.json", {})
    write_jsonl(
        run_dir / "candidate_score_audit.jsonl",
        [
            {
                "need_id": "n1",
                "asset_id": "page_gold",
                "asset_kind": "page_image",
                "policy_input": True,
                "rank_hybrid": 1,
            },
            {
                "need_id": "n2",
                "asset_id": "bg_gold",
                "asset_kind": "background",
                "policy_input": False,
                "rank_hybrid": 1,
            },
        ],
    )
    write_json(run_dir / "retrieve_summary.json", {})
    write_jsonl(run_dir / "final_matches.jsonl", [])

    run_summarize_stage(run_dir=run_dir)

    metrics = read_json(run_dir / "05_summarize" / "metrics.json")
    buckets = metrics["asset_kind_buckets"]["retrieval"]
    assert buckets["page_image"]["candidate_hit_rate"] == 1.0
    assert buckets["background"]["candidate_hit_rate"] == 0.0


def test_retrieve_outputs_include_size_compatible_gold_metrics(tmp_path: Path):
    run_dir = tmp_path / "report" / "run1"
    targets = [
        {
            "need_id": "n1",
            "label_status": "labeled",
            "should_reuse": True,
            "acceptable_asset_ids": ["size_bad"],
            "best_asset_ids": ["size_bad"],
        }
    ]
    candidate_score_rows = [
        {
            "need_id": "n1",
            "asset_id": "size_bad",
            "label_status": "labeled",
            "policy_input": True,
            "rank_hybrid": 1,
            "is_acceptable": True,
            "is_best": True,
        }
    ]
    hard_rows = [
        {
            "need_id": "n1",
            "asset_id": "size_bad",
            "label_status": "labeled",
            "is_acceptable": True,
            "is_best": True,
            "size_only_pass": False,
        }
    ]

    _write_retrieve_outputs(run_dir, candidate_score_rows, targets=targets, hard_rows=hard_rows)

    summary = read_json(run_dir / "03_retrieve" / "retrieve_summary.json")
    assert summary["ranking"]["reusable_need_count"] == 1
    assert summary["size_compatible_gold"]["ranking"]["reusable_need_count"] == 0
    assert summary["size_compatible_gold"]["gold_adjustment"]["removed_acceptable_pair_count"] == 1


def test_retrieve_outputs_write_candidate_score_audit(tmp_path: Path):
    run_dir = tmp_path / "report" / "run1"
    targets = [
        {
            "need_id": "n1",
            "label_status": "labeled",
            "should_reuse": True,
            "acceptable_asset_ids": ["gold_rejected", "gold_accepted"],
            "best_asset_ids": ["gold_rejected"],
        }
    ]
    candidate_score_rows = [
        {
            "need_id": "n1",
            "asset_id": "gold_rejected",
            "label_status": "labeled",
            "policy_input": True,
            "policy_score": 0.44,
            "rank_hybrid": 1,
            "is_acceptable": True,
            "is_best": True,
        },
        {
            "need_id": "n1",
            "asset_id": "gold_accepted",
            "label_status": "labeled",
            "policy_input": True,
            "policy_score": 0.82,
            "rank_hybrid": 2,
            "is_acceptable": True,
            "is_best": False,
        },
        {
            "need_id": "n1",
            "asset_id": "wrong_accepted",
            "label_status": "labeled",
            "policy_input": True,
            "policy_score": 0.79,
            "rank_hybrid": 3,
            "is_acceptable": False,
            "is_best": False,
        },
        {
            "need_id": "n1",
            "asset_id": "wrong_rejected",
            "label_status": "labeled",
            "policy_input": True,
            "policy_score": 0.20,
            "rank_hybrid": 4,
            "is_acceptable": False,
            "is_best": False,
        },
    ]
    hard_rows = [
        {
            "need_id": "n1",
            "asset_id": "gold_accepted",
            "label_status": "labeled",
            "size_only_pass": True,
            "is_acceptable": True,
            "is_best": False,
        }
    ]

    _write_retrieve_outputs(run_dir, candidate_score_rows, targets=targets, hard_rows=hard_rows)

    summary = read_json(run_dir / "03_retrieve" / "retrieve_summary.json")
    audit = summary["candidate_score_audit"]
    assert audit["candidate_pair_count"] == 4
    assert audit["acceptable_pair_count"] == 2
    assert audit["best_pair_count"] == 1
    assert audit["max_policy_score"] == 0.82

    audit_rows = list(csv.DictReader((run_dir / "03_retrieve" / "candidate_score_audit.csv").open(encoding="utf-8-sig")))
    assert [row["asset_id"] for row in audit_rows] == [
        "gold_rejected",
        "gold_accepted",
        "wrong_accepted",
        "wrong_rejected",
    ]


def test_summarize_writes_size_compatible_gold_comparison_metrics(tmp_path: Path):
    run_dir = tmp_path / "report" / "run1"
    run_dir.mkdir(parents=True)
    write_jsonl(
        run_dir / "targets.jsonl",
        [
            {
                "need_id": "n1",
                "label_status": "labeled",
                "should_reuse": True,
                "acceptable_asset_ids": ["size_bad"],
                "best_asset_ids": ["size_bad"],
            }
        ],
    )
    write_jsonl(
        run_dir / "hard_filter_pairs.jsonl",
        [
            {
                "need_id": "n1",
                "asset_id": "size_bad",
                "label_status": "labeled",
                "is_acceptable": True,
                "is_best": True,
                "size_only_pass": False,
                "all_hard_pass": False,
            }
        ],
    )
    write_json(run_dir / "hard_filter_summary.json", {})
    write_jsonl(
        run_dir / "candidate_score_audit.jsonl",
        [
            {
                "need_id": "n1",
                "asset_id": "size_bad",
                "label_status": "labeled",
                "policy_input": True,
                "rank_hybrid": 1,
                "is_acceptable": True,
                "is_best": True,
            }
        ],
    )
    write_json(run_dir / "retrieve_summary.json", {})
    write_json(run_dir / "llm_review_summary.json", {})
    write_jsonl(
        run_dir / "final_matches.jsonl",
        [
            {
                "need_id": "n1",
                "label_status": "labeled",
                "should_reuse": True,
                "selected_asset_id": "size_bad",
                "selected_is_acceptable": True,
                "selected_is_best": True,
            }
        ],
    )

    run_summarize_stage(run_dir=run_dir)

    metrics = read_json(run_dir / "05_summarize" / "metrics.json")
    assert metrics["final_raw_gold_audit"]["correct_selected_count"] == 1
    assert metrics["final"]["correct_selected_count"] == 0
    assert metrics["final"]["wrong_selected_count"] == 1
    assert metrics["ranking_size_compatible_gold"]["reusable_need_count"] == 0
    assert metrics["retrieval_size_compatible_gold"]["ranking"]["reusable_need_count"] == 0
    assert metrics["size_compatible_gold_adjustment"]["removed_acceptable_pair_count"] == 1


def test_report_md_is_written_in_chinese(tmp_path: Path):
    run_dir = tmp_path / "report" / "run1"
    run_dir.mkdir(parents=True)
    write_jsonl(run_dir / "targets.jsonl", [])
    write_json(run_dir / "hard_filter_summary.json", {})
    write_json(run_dir / "retrieve_summary.json", {})
    write_json(run_dir / "llm_review_summary.json", {})
    write_jsonl(run_dir / "hard_filter_pairs.jsonl", [])
    write_jsonl(run_dir / "candidate_score_audit.jsonl", [])
    write_jsonl(run_dir / "final_matches.jsonl", [])

    run_summarize_stage(run_dir=run_dir)

    report = (run_dir / "05_summarize" / "report.md").read_text(encoding="utf-8")
    assert "# 复用评估报告" in report
    assert "最终准确率" in report
