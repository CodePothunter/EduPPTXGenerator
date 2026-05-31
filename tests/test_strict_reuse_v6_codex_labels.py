import json

import pytest

from scripts.strict_reuse_v6_codex_labels import (
    CATEGORY_ORDER,
    aggregate_split_assets,
    build_review_payload,
    read_label_jsonl,
    validate_review_payload,
    write_items_for_codex,
)


def _write_split(path, *, group, assets):
    path.write_text(
        json.dumps(
            {
                "schema_version": 14,
                "strict_reuse_group": group,
                "asset_root": str(path.parent.parent),
                "assets": assets,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_aggregate_reads_all_split_files_before_labeling(tmp_path):
    split_dir = tmp_path / "library" / "strict_reuse_indexes"
    split_dir.mkdir(parents=True)
    _write_split(
        split_dir / "C00_strict_text_problem_skip.json",
        group="C00_strict_text_problem_skip",
        assets=[
            {
                "asset_id": "a_text",
                "asset_kind": "page_image",
                "content_prompt": "田字格中的8个生字示例",
                "strict_reuse_group": "wrong_old_group",
                "image_path": "ai_images/a.png",
            }
        ],
    )
    _write_split(
        split_dir / "general_reuse.json",
        group="general_reuse",
        assets=[
            {
                "asset_id": "b_scene",
                "asset_kind": "page_image",
                "content_prompt": "雨中街道场景",
                "strict_reuse_group": "general_reuse",
                "image_path": "ai_images/b.png",
            }
        ],
    )

    result = aggregate_split_assets(split_dir)

    assert [asset["asset_id"] for asset in result.assets] == ["a_text", "b_scene"]
    assert result.assets[0]["source_file"] == "C00_strict_text_problem_skip.json"
    assert result.assets[0]["original_strict_reuse_group"] == "wrong_old_group"
    assert result.assets[1]["source_file"] == "general_reuse.json"
    assert result.warnings == []


def test_write_items_for_codex_excludes_original_classification_metadata(tmp_path):
    output_path = tmp_path / "items.json"
    assets = [
        {
            "asset_id": "a",
            "content_prompt": "米字格中的红色汉字“你”",
            "source_file": "content_reuse.json",
            "original_strict_reuse_group": "content_reuse",
            "theme": "一年级语文",
        }
    ]

    write_items_for_codex(assets, output_path)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload == {
        "decision_basis": "content_prompt_only",
        "asset_count": 1,
        "items": [
            {
                "ordinal": 1,
                "asset_id": "a",
                "content_prompt": "米字格中的红色汉字“你”",
            }
        ],
    }


def test_read_label_jsonl_rejects_invalid_category(tmp_path):
    labels_path = tmp_path / "labels.jsonl"
    labels_path.write_text(
        json.dumps(
            {
                "asset_id": "a",
                "assigned_category": "bad_category",
                "decision_reason": "invalid",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid category"):
        read_label_jsonl(labels_path)


def test_build_payload_groups_by_category_order_and_keeps_audit_fields():
    assets = [
        {
            "asset_id": "scene",
            "asset_kind": "page_image",
            "content_prompt": "雨中街道场景",
            "source_file": "general_reuse.json",
            "original_strict_reuse_group": "general_reuse",
            "image_path": "pptx_images/scene.png",
            "theme": "审核定位",
            "subject": "语文",
            "grade_norm": "三年级",
        },
        {
            "asset_id": "glyph",
            "asset_kind": "page_image",
            "content_prompt": "米字格中的红色汉字“你”",
            "source_file": "content_reuse.json",
            "original_strict_reuse_group": "content_reuse",
            "image_path": "pptx_images/glyph.png",
        },
    ]
    labels = {
        "scene": {
            "assigned_category": "C05_scene_decor_container",
            "decision_reason": "风景/场景/氛围图",
            "review_flags": [],
        },
        "glyph": {
            "assigned_category": "C01_language_glyph_visual",
            "decision_reason": "1个汉字符号本身是教学核心",
            "review_flags": [],
        },
    }

    payload = build_review_payload(
        assets,
        labels,
        source_dir="materials_library_ppt/strict_reuse_indexes",
        warnings=["source warning"],
    )

    assert payload["category_order"] == CATEGORY_ORDER
    assert payload["counts"]["C01_language_glyph_visual"] == 1
    assert payload["counts"]["C05_scene_decor_container"] == 1
    assert payload["warnings"] == ["source warning"]
    assert payload["categories"]["C01_language_glyph_visual"][0]["asset_id"] == "glyph"
    scene = payload["categories"]["C05_scene_decor_container"][0]
    assert scene["source_file"] == "general_reuse.json"
    assert scene["original_strict_reuse_group"] == "general_reuse"
    assert scene["theme"] == "审核定位"
    validate_review_payload(payload, expected_asset_count=2)


def test_build_payload_fails_when_label_is_missing():
    assets = [{"asset_id": "a", "content_prompt": "一只虎斑幼猫"}]

    with pytest.raises(ValueError, match="missing manual labels"):
        build_review_payload(
            assets,
            labels={},
            source_dir="materials_library/strict_reuse_indexes",
            warnings=[],
        )


def test_missing_prompt_goes_to_c00_with_review_flag():
    assets = [
        {
            "asset_id": "blank_prompt",
            "content_prompt": "",
            "source_file": "background.json",
            "original_strict_reuse_group": "background",
        }
    ]

    payload = build_review_payload(
        assets,
        labels={},
        source_dir="materials_library/strict_reuse_indexes",
        warnings=[],
    )

    entry = payload["categories"]["C00_strict_text_problem_skip"][0]
    assert entry["asset_id"] == "blank_prompt"
    assert entry["review_flags"] == ["missing_or_insufficient_content_prompt"]
    assert payload["counts"]["C00_strict_text_problem_skip"] == 1
    validate_review_payload(payload, expected_asset_count=1)
