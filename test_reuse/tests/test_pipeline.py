import json
from pathlib import Path

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
                            "prompt_route": {"strict_reuse_group": "C04_generic_subject_object"},
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


def test_build_target_records_uses_existing_reuse_target_builder(tmp_path: Path):
    plan_path = tmp_path / "lesson_one.json"
    plan_path.write_text(json.dumps(_minimal_plan(), ensure_ascii=False), encoding="utf-8")
    needs = extract_plan_needs(plan_path, run_id="run1")

    targets = build_target_records(needs)

    assert len(targets) == 1
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


def test_hard_filter_rows_for_target_checks_full_library_candidates():
    target_record = {
        "run_id": "run1",
        "need_id": "n1",
        "target": {
            "asset_kind": "page_image",
            "strict_reuse_group": "C04_generic_subject_object",
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
            "strict_reuse_group": "C04_generic_subject_object",
            "subject": CHINESE_SUBJECT,
            "grade_norm": "\u4e8c\u5e74\u7ea7",
            "grade_band": "\u4f4e\u5e74\u7ea7",
            "general": False,
            "aspect_ratio": "1:1",
        },
        {
            "asset_id": "a_bad_group",
            "asset_kind": "page_image",
            "strict_reuse_group": "C05_scene_decor_container",
            "subject": CHINESE_SUBJECT,
            "grade_norm": "\u4e8c\u5e74\u7ea7",
            "grade_band": "\u4f4e\u5e74\u7ea7",
            "general": False,
            "aspect_ratio": "1:1",
        },
        {
            "asset_id": "a_bad_subject",
            "asset_kind": "page_image",
            "strict_reuse_group": "C04_generic_subject_object",
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


def test_flatten_candidate_collection_writes_stage_rows():
    collection = {
        "target": {"strict_reuse_group": "C04_generic_subject_object", "subject": CHINESE_SUBJECT},
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


def test_run_eval_writes_stage_files_without_generating_dataset(tmp_path: Path):
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
        allow_llm=False,
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


def test_stage_functions_write_outputs_incrementally(tmp_path: Path):
    plan_path = tmp_path / "lesson_one.json"
    plan_path.write_text(json.dumps(_minimal_plan(), ensure_ascii=False), encoding="utf-8")
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    output_dir = tmp_path / "report"

    run_dir = prepare_run(
        plan_paths=[plan_path],
        output_dir=output_dir,
        run_id="reuse_eval_step",
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
