import json

from edupptx.materials.strict_reuse_classifier import (
    C01_IRREPLACEABLE_ENTITY_EVENT_ACTION,
    C02_GENERIC_SUBJECT_OBJECT,
    C03_SCENE_DECOR_CONTAINER,
    classify_strict_reuse_groups,
    normalize_secondary_reuse_group,
    write_strict_reuse_group_indexes,
)


def test_secondary_only_valid_as_c03_under_c01_primary():
    assert (
        normalize_secondary_reuse_group(
            C03_SCENE_DECOR_CONTAINER, primary=C01_IRREPLACEABLE_ENTITY_EVENT_ACTION
        )
        == C03_SCENE_DECOR_CONTAINER
    )
    # 非 C01 主类不携带副标签。
    assert normalize_secondary_reuse_group(C03_SCENE_DECOR_CONTAINER, primary=C02_GENERIC_SUBJECT_OBJECT) == ""
    # 副标签只能是 C03。
    assert normalize_secondary_reuse_group(C02_GENERIC_SUBJECT_OBJECT, primary=C01_IRREPLACEABLE_ENTITY_EVENT_ACTION) == ""
    # 垃圾值被丢弃。
    assert normalize_secondary_reuse_group("garbage", primary=C01_IRREPLACEABLE_ENTITY_EVENT_ACTION) == ""


def test_classify_preserves_secondary_for_c01_drops_otherwise():
    index = {
        "assets": [
            {
                "asset_id": "keep",
                "asset_kind": "page_image",
                "strict_reuse_group": C01_IRREPLACEABLE_ENTITY_EVENT_ACTION,
                "strict_reuse_secondary_group": C03_SCENE_DECOR_CONTAINER,
            },
            {
                "asset_id": "drop",
                "asset_kind": "page_image",
                "strict_reuse_group": C02_GENERIC_SUBJECT_OBJECT,
                "strict_reuse_secondary_group": C03_SCENE_DECOR_CONTAINER,
            },
        ]
    }
    classify_strict_reuse_groups(index)
    keep, drop = index["assets"]
    assert keep["strict_reuse_secondary_group"] == C03_SCENE_DECOR_CONTAINER
    assert "strict_reuse_secondary_group" not in drop


def test_secondary_projected_into_c03_split(tmp_path):
    # C01 具名地标带 C03 副标签时，会作为「投影」写入 C03 split（dual-use），
    # 同时仍保留在 C01 split。投影体 strict_reuse_group=C03 且带 secondary_projection 标记。
    index = {
        "schema_version": 1,
        "assets": [
            {
                "asset_id": "a1",
                "asset_kind": "page_image",
                "image_path": "x.png",
                "aspect_ratio": "1:1",
                "strict_reuse_group": C01_IRREPLACEABLE_ENTITY_EVENT_ACTION,
                "strict_reuse_secondary_group": C03_SCENE_DECOR_CONTAINER,
            }
        ],
    }
    write_strict_reuse_group_indexes(index, tmp_path, split_dir="strict_reuse_indexes")
    split = tmp_path / "strict_reuse_indexes"
    c01 = json.loads((split / "C01_irreplaceable_entity_event_action.json").read_text(encoding="utf-8"))
    c03 = json.loads((split / "C03_scene_decor_container.json").read_text(encoding="utf-8"))
    ids01 = [a["asset_id"] for a in c01["assets"]]
    ids03 = [a["asset_id"] for a in c03["assets"]]
    assert "a1" in ids01
    assert "a1" in ids03  # dual-use 投影进 C03。
    primary = next(a for a in c01["assets"] if a["asset_id"] == "a1")
    assert primary["strict_reuse_group"] == C01_IRREPLACEABLE_ENTITY_EVENT_ACTION
    assert primary["strict_reuse_secondary_group"] == C03_SCENE_DECOR_CONTAINER
    projection = next(a for a in c03["assets"] if a["asset_id"] == "a1")
    assert projection["strict_reuse_group"] == C03_SCENE_DECOR_CONTAINER
    assert projection.get("secondary_projection") is True
