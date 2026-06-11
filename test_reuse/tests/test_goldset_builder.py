import csv
import json
from pathlib import Path

import pytest

from test_reuse.goldset_builder import (
    GoldLabelError,
    MAX_ACCEPTABLE_ASSET_IDS,
    extract_plan_image_needs,
    load_reusable_asset_ids,
    validate_goldset_rows,
    write_goldset_artifacts,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "reuse_caption_goldset_20260603"
MANUAL_C02_CLASS_FIX_IDS = {
    "session_20260603_150935:p14:hero_1",
    "session_20260603_150935:p16:illustration_1",
    "session_20260603_151144:p13:illustration_1",
    "session_20260603_151216:p15:illustration_1",
    "session_20260603_151238:p14:hero_1",
    "session_20260603_151238:p15:hero_1",
    "session_20260603_151300:p15:illustration_1",
    "session_20260603_151300:p16:illustration_1",
    "session_20260603_151300:p24:hero_1",
    "session_20260603_151403:p03:illustration_2",
    "session_20260603_151451:p03:illustration_1",
    "session_20260603_151517:p07:illustration_1",
    "session_20260603_151613:p01:hero_1",
    "session_20260603_151613:p08:illustration_1",
    "session_20260603_151700:p11:illustration_1",
    "session_20260603_151844:p12:illustration_2",
    "session_20260603_151844:p12:illustration_4",
}


def _write_plan(path: Path) -> None:
    payload = {
        "meta": {"topic": "Bridge lesson", "audience": "grade 8", "total_pages": 1},
        "pages": [
            {
                "page_number": 1,
                "page_type": "content",
                "title": "Observe",
                "material_needs": {
                    "background": None,
                    "images": [
                        {
                            "query": "Chinese stone arch bridge watercolor long shot",
                            "source": "ai_generate",
                            "role": "illustration",
                            "aspect_ratio": "16:9",
                            "generation_prompt": "Chinese stone arch bridge watercolor long shot, teaching illustration",
                            "caption": "",
                            "prompt_route": {"template_family": "upper_grade"},
                        },
                        {
                            "query": "search-only photo",
                            "source": "search",
                            "role": "illustration",
                            "aspect_ratio": "16:9",
                        },
                    ],
                },
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_extract_plan_image_needs_uses_query_when_caption_is_empty(tmp_path: Path):
    plan_path = tmp_path / "output" / "session_one" / "plan.json"
    _write_plan(plan_path)

    rows = extract_plan_image_needs([plan_path])

    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == "session_one"
    assert row["need_id"] == "session_one:p01:illustration_1"
    assert row["query"] == "Chinese stone arch bridge watercolor long shot"
    assert row["caption"] == ""
    assert row["gold_label_text"] == "Chinese stone arch bridge watercolor long shot"
    assert row["gold_label_text_source"] == "query"
    assert row["prompt_route"] == {"template_family": "upper_grade"}


def test_validate_goldset_rejects_best_not_in_acceptable():
    rows = [
        {
            "need_id": "n1",
            "target_strict_reuse_group_gold": "C02_generic_subject_object",
            "target_is_c00_skip": False,
            "should_reuse": True,
            "acceptable_asset_ids": ["a1"],
            "best_asset_ids": ["a2"],
        }
    ]

    with pytest.raises(GoldLabelError, match="best asset must be acceptable"):
        validate_goldset_rows(rows, reusable_asset_ids={"a1", "a2"})


def test_validate_goldset_rejects_c00_with_candidates():
    rows = [
        {
            "need_id": "n1",
            "target_strict_reuse_group_gold": "C00_strict_text_problem_skip",
            "target_is_c00_skip": True,
            "should_reuse": False,
            "acceptable_asset_ids": ["a1"],
            "best_asset_ids": [],
        }
    ]

    with pytest.raises(GoldLabelError, match="C00 target cannot have candidates"):
        validate_goldset_rows(rows, reusable_asset_ids={"a1"})


def test_validate_goldset_allows_confirmed_manual_candidate_count():
    acceptable = [f"a{i}" for i in range(MAX_ACCEPTABLE_ASSET_IDS)]
    rows = [
        {
            "need_id": "n1",
            "target_strict_reuse_group_gold": "C02_generic_subject_object",
            "target_is_c00_skip": False,
            "should_reuse": True,
            "acceptable_asset_ids": acceptable,
            "best_asset_ids": ["a0"],
        }
    ]

    validate_goldset_rows(rows, reusable_asset_ids=set(acceptable))


def test_load_reusable_asset_ids_excludes_c00(tmp_path: Path):
    index_dir = tmp_path / "library" / "strict_reuse_indexes"
    index_dir.mkdir(parents=True)
    (index_dir / "C02_generic_subject_object.json").write_text(
        json.dumps({"assets": [{"asset_id": "keep"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (index_dir / "C00_strict_text_problem_skip.json").write_text(
        json.dumps({"assets": [{"asset_id": "skip"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    ids = load_reusable_asset_ids(index_dir)

    assert ids == {"keep"}


def test_write_goldset_artifacts_does_not_export_labeled_plans(tmp_path: Path):
    plan_path = tmp_path / "output" / "session_one" / "plan.json"
    _write_plan(plan_path)
    rows = extract_plan_image_needs([plan_path])
    rows[0].update(
        {
            "label_status": "labeled",
            "should_reuse": True,
            "acceptable_asset_ids": ["asset_1"],
            "best_asset_ids": ["asset_1"],
            "label_notes": "semantic bridge match",
            "target_strict_reuse_group_gold": "C01_irreplaceable_entity_event_action",
            "target_is_c00_skip": False,
        }
    )

    index_dir = tmp_path / "library" / "strict_reuse_indexes"
    index_dir.mkdir(parents=True)
    (index_dir / "C01_irreplaceable_entity_event_action.json").write_text(
        json.dumps({"assets": [{"asset_id": "asset_1"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    output_dir = tmp_path / "goldset"
    write_goldset_artifacts(rows=rows, output_dir=output_dir, index_dir=index_dir)

    assert (output_dir / "goldset.json").exists()
    assert not (output_dir / "labeled_plans").exists()


def test_manual_class_fix_fixture_promotes_selected_targets_to_c02():
    csv_path = FIXTURE_DIR / "gold_group_query_review_after_class_fix.csv"
    goldset_path = FIXTURE_DIR / "goldset.json"
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        csv_by_need = {row["need_id"]: row for row in csv.DictReader(handle)}
    goldset = json.loads(goldset_path.read_text(encoding="utf-8"))
    items_by_need = {row["need_id"]: row for row in goldset["items"]}

    assert MANUAL_C02_CLASS_FIX_IDS <= set(csv_by_need)
    assert MANUAL_C02_CLASS_FIX_IDS <= set(items_by_need)
    for need_id in sorted(MANUAL_C02_CLASS_FIX_IDS):
        assert csv_by_need[need_id]["gold_group"] == "C02"
        assert items_by_need[need_id]["target_strict_reuse_group_gold"] == "C02_generic_subject_object"
        assert items_by_need[need_id]["target_is_c00_skip"] is False


def _write_index(path: Path, assets: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"assets": assets}, ensure_ascii=False), encoding="utf-8")


def test_load_semantic_rebuild_assets_reads_background_and_c01_to_c03_only(tmp_path: Path):
    index_dir = tmp_path / "strict_reuse_indexes"
    _write_index(
        index_dir / "background.json",
        [{"asset_id": "bg1", "asset_kind": "background", "caption": "荷塘背景", "query": "夏日荷塘"}],
    )
    _write_index(
        index_dir / "C01_irreplaceable_entity_event_action.json",
        [{"asset_id": "c01", "asset_kind": "page_image", "caption": "赵州桥", "query": "赵州桥远景"}],
    )
    _write_index(
        index_dir / "C02_generic_subject_object.json",
        [{"asset_id": "c02", "asset_kind": "page_image", "caption": "小学生读书", "query": "儿童阅读"}],
    )
    _write_index(
        index_dir / "C03_scene_decor_container.json",
        [{"asset_id": "c03", "asset_kind": "page_image", "caption": "教室场景", "query": "空教室"}],
    )
    _write_index(
        index_dir / "C00_strict_text_problem_skip.json",
        [{"asset_id": "bad", "asset_kind": "page_image", "caption": "拼音题卡", "query": "带拼音文字"}],
    )

    from test_reuse.goldset_builder import load_semantic_rebuild_assets

    assets = load_semantic_rebuild_assets(index_dir)

    assert [asset["asset_id"] for asset in assets] == ["bg1", "c01", "c02", "c03"]
    assert {asset["_source_index_file"] for asset in assets} == {
        "background.json",
        "C01_irreplaceable_entity_event_action.json",
        "C02_generic_subject_object.json",
        "C03_scene_decor_container.json",
    }


def test_semantic_text_uses_only_caption_and_query():
    from test_reuse.goldset_builder import material_semantic_text, target_semantic_text

    target_row = {
        "caption": "行级错误 caption",
        "raw_query": "行级错误 query",
        "target": {
            "caption": "石拱桥远景水墨画",
            "query": "水墨风格中国石拱桥",
            "context_summary": "不应出现的课文背景",
            "teaching_intent": "不应出现的教学意图",
        },
    }
    asset = {
        "caption": "石拱桥插画",
        "query": "中国石拱桥远景",
        "context_summary": "不应出现的素材上下文",
        "teaching_intent": "不应出现的素材教学意图",
        "theme": "不应出现的主题",
    }

    assert target_semantic_text(target_row) == "石拱桥远景水墨画 水墨风格中国石拱桥"
    assert material_semantic_text(asset) == "石拱桥插画 中国石拱桥远景"


def test_validate_semantic_rebuild_rows_allows_c00_target_with_candidates():
    from test_reuse.goldset_builder import validate_semantic_rebuild_rows

    rows = [
        {
            "need_id": "n1",
            "target_strict_reuse_group_gold": "C00_strict_text_problem_skip",
            "target_is_c00_skip": True,
            "should_reuse": True,
            "acceptable_asset_ids": ["asset_1"],
            "best_asset_ids": ["asset_1"],
            "acceptable_asset_metadata": [{"asset_id": "asset_1", "asset_kind": "page_image"}],
            "best_asset_metadata": [{"asset_id": "asset_1", "asset_kind": "page_image"}],
        }
    ]

    validate_semantic_rebuild_rows(rows, reusable_asset_ids={"asset_1"})


def test_validate_semantic_rebuild_rows_rejects_metadata_mismatch():
    from test_reuse.goldset_builder import validate_semantic_rebuild_rows

    rows = [
        {
            "need_id": "n1",
            "should_reuse": True,
            "acceptable_asset_ids": ["asset_1"],
            "best_asset_ids": ["asset_1"],
            "acceptable_asset_metadata": [{"asset_id": "wrong", "asset_kind": "page_image"}],
            "best_asset_metadata": [{"asset_id": "asset_1", "asset_kind": "page_image"}],
        }
    ]

    with pytest.raises(GoldLabelError, match="acceptable metadata mismatch"):
        validate_semantic_rebuild_rows(rows, reusable_asset_ids={"asset_1"})


def test_apply_semantic_decisions_writes_candidate_metadata():
    from test_reuse.goldset_builder import apply_semantic_decisions

    targets = [
        {
            "need_id": "n1",
            "caption": "石拱桥远景水墨画",
            "raw_query": "水墨风格中国石拱桥",
            "target_strict_reuse_group_gold": "C00_strict_text_problem_skip",
            "target_is_c00_skip": True,
        }
    ]
    assets_by_id = {
        "asset_1": {
            "asset_id": "asset_1",
            "asset_kind": "page_image",
            "caption": "石拱桥插画",
            "query": "中国石拱桥远景",
            "strict_reuse_group": "C02_generic_subject_object",
            "subject": "语文",
            "general": False,
            "aspect_ratio": "16:9",
            "_source_index_file": "C02_generic_subject_object.json",
        }
    }
    decisions = {
        "n1": {
            "acceptable_asset_ids": ["asset_1"],
            "best_asset_ids": ["asset_1"],
            "label_notes": "caption/query 语义匹配石拱桥远景",
        }
    }

    rows = apply_semantic_decisions(targets, decisions, assets_by_id=assets_by_id)

    assert rows[0]["should_reuse"] is True
    assert rows[0]["acceptable_asset_ids"] == ["asset_1"]
    assert rows[0]["best_asset_ids"] == ["asset_1"]
    assert rows[0]["acceptable_asset_metadata"] == [
        {
            "asset_id": "asset_1",
            "asset_kind": "page_image",
            "strict_reuse_group": "C02_generic_subject_object",
            "subject": "语文",
            "general": False,
            "aspect_ratio": "16:9",
            "caption": "石拱桥插画",
            "query": "中国石拱桥远景",
            "source_index_file": "C02_generic_subject_object.json",
        }
    ]
