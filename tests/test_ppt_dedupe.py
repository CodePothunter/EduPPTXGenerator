from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from edupptx.materials.ppt_dedupe import (
    BUCKET_RULES,
    PptDedupeInfo,
    VISUAL_IDENTITY_DISTANCE,
    _visual_pair_reason,
    dedupe_ppt_split_index_library,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _draw_box(path: Path, *, color=(230, 240, 255), label=False) -> None:
    img = Image.new("RGBA", (320, 180), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (20, 20, 300, 160),
        radius=12,
        fill=color + (255,),
        outline=(80, 100, 130, 255),
        width=3,
    )
    if label:
        draw.rectangle((60, 70, 260, 100), fill=(50, 70, 90, 255))
    img.save(path)


def _asset(
    asset_id: str,
    image_path: str,
    *,
    caption: str,
    query: str,
    group: str = "C03_scene_decor_container",
    general=True,
) -> dict:
    return {
        "asset_id": asset_id,
        "asset_kind": "page_image",
        "image_path": image_path,
        "original_image_path": image_path,
        "aspect_ratio": "16:9",
        "caption": caption,
        "query": query,
        "normalized_prompt": query,
        "context_summary": "课堂素材",
        "subject": "语文",
        "topic_refs": ["示例"],
        "strict_reuse_group": group,
        "general": general,
        "duplicate_asset_ids": [],
    }


def test_split_index_dry_run_reports_bucketed_merge_without_modifying_files(tmp_path):
    library = tmp_path / "materials_library_ppt"
    image_dir = library / "pptx_images"
    image_dir.mkdir(parents=True)
    _draw_box(image_dir / "a.png")
    _draw_box(image_dir / "b.png")
    _write_json(
        library / "strict_reuse_indexes" / "C03_scene_decor_container.json",
        {
            "strict_reuse_group": "C03_scene_decor_container",
            "asset_count": 2,
            "assets": [
                _asset("a", "pptx_images/a.png", caption="浅蓝空白边框底图", query="浅蓝空白边框底图"),
                _asset("b", "pptx_images/b.png", caption="浅蓝色空白边框底图", query="浅蓝色空白边框底图"),
            ],
        },
    )

    report = dedupe_ppt_split_index_library(library, apply=False)

    assert report["mode"] == "dry_run"
    assert report["mergeable_group_count"] == 1
    assert report["groups"][0]["bucket"] == "C03"
    assert report["groups"][0]["status"] == "mergeable"
    assert _read_json(library / "strict_reuse_indexes" / "C03_scene_decor_container.json")["asset_count"] == 2
    assert (image_dir / "a.png").exists()
    assert (image_dir / "b.png").exists()
    assert Path(report["report_path"]).exists()


def test_split_index_apply_updates_one_bucket_and_deletes_safe_files(tmp_path):
    library = tmp_path / "materials_library_ppt"
    image_dir = library / "pptx_images"
    image_dir.mkdir(parents=True)
    _draw_box(image_dir / "small.png")
    _draw_box(image_dir / "large.png")
    _write_json(
        library / "strict_reuse_indexes" / "C03_scene_decor_container.json",
        {
            "strict_reuse_group": "C03_scene_decor_container",
            "asset_count": 2,
            "assets": [
                _asset("small", "pptx_images/small.png", caption="浅蓝空白边框底图", query="浅蓝空白边框底图"),
                _asset("large", "pptx_images/large.png", caption="浅蓝色空白边框底图", query="浅蓝色空白边框底图"),
            ],
        },
    )

    report = dedupe_ppt_split_index_library(library, apply=True)

    payload = _read_json(library / "strict_reuse_indexes" / "C03_scene_decor_container.json")
    assert report["mode"] == "apply"
    assert report["applied_removed_count"] == 1
    assert payload["asset_count"] == 1
    kept = payload["assets"][0]
    assert kept["asset_id"] in {"small", "large"}
    assert kept["duplicate_asset_ids"]
    removed_id = kept["duplicate_asset_ids"][0]
    assert not (image_dir / f"{removed_id}.png").exists()
    assert (library / "debug" / "ppt_dedupe_report.json").exists()


def test_c00_is_not_loaded_into_pairs_or_report(tmp_path):
    library = tmp_path / "materials_library_ppt"
    image_dir = library / "skip_images"
    image_dir.mkdir(parents=True)
    _draw_box(image_dir / "a.png", label=True)
    _draw_box(image_dir / "b.png", label=True)
    _write_json(
        library / "strict_reuse_indexes" / "C00_strict_text_problem_skip.json",
        {
            "strict_reuse_group": "C00_strict_text_problem_skip",
            "asset_count": 2,
            "assets": [
                _asset("a", "skip_images/a.png", caption="坐标题", query="坐标题", group="C00_strict_text_problem_skip"),
                _asset("b", "skip_images/b.png", caption="坐标题", query="坐标题", group="C00_strict_text_problem_skip"),
            ],
        },
    )

    report = dedupe_ppt_split_index_library(library, apply=True)

    assert report["asset_count"] == 0
    assert report["groups"] == []
    assert _read_json(library / "strict_reuse_indexes" / "C00_strict_text_problem_skip.json")["asset_count"] == 2
    assert (image_dir / "a.png").exists()
    assert (image_dir / "b.png").exists()


def test_secondary_projection_is_passthrough_and_never_deletes_shared_image(tmp_path):
    library = tmp_path / "materials_library_ppt"
    image_dir = library / "pptx_images"
    image_dir.mkdir(parents=True)
    _draw_box(image_dir / "primary.png")
    _draw_box(image_dir / "duplicate.png")
    projection = _asset(
        "primary",
        "pptx_images/primary.png",
        caption="科研场所晴天外景",
        query="科研场所晴天外景",
    )
    projection["secondary_projection"] = True
    projection["secondary_projection_of"] = "primary"
    _write_json(
        library / "strict_reuse_indexes" / "C03_scene_decor_container.json",
        {
            "strict_reuse_group": "C03_scene_decor_container",
            "asset_count": 2,
            "assets": [
                projection,
                _asset(
                    "duplicate",
                    "pptx_images/duplicate.png",
                    caption="科研场所晴天外景",
                    query="科研场所晴天外景",
                ),
            ],
        },
    )

    report = dedupe_ppt_split_index_library(library, apply=True)

    payload = _read_json(library / "strict_reuse_indexes" / "C03_scene_decor_container.json")
    assert report["asset_count"] == 1
    assert report["mergeable_group_count"] == 0
    assert payload["asset_count"] == 2
    assert {item["asset_id"] for item in payload["assets"]} == {"primary", "duplicate"}
    assert (image_dir / "primary.png").exists()
    assert (image_dir / "duplicate.png").exists()


def test_background_and_c03_do_not_cross_merge(tmp_path):
    library = tmp_path / "materials_library_ppt"
    image_dir = library / "pptx_images"
    image_dir.mkdir(parents=True)
    _draw_box(image_dir / "bg.png")
    _draw_box(image_dir / "c03.png")
    background_asset = _asset(
        "bg",
        "pptx_images/bg.png",
        caption="",
        query="浅蓝空白边框底图",
        group="C03_scene_decor_container",
    )
    background_asset["asset_kind"] = "background"
    _write_json(
        library / "strict_reuse_indexes" / "background.json",
        {"strict_reuse_group": "background", "asset_count": 1, "assets": [background_asset]},
    )
    _write_json(
        library / "strict_reuse_indexes" / "C03_scene_decor_container.json",
        {
            "strict_reuse_group": "C03_scene_decor_container",
            "asset_count": 1,
            "assets": [
                _asset(
                    "c03",
                    "pptx_images/c03.png",
                    caption="浅蓝空白边框底图",
                    query="浅蓝空白边框底图",
                )
            ],
        },
    )

    report = dedupe_ppt_split_index_library(library, apply=True)

    assert report["mergeable_group_count"] == 0
    assert _read_json(library / "strict_reuse_indexes" / "background.json")["asset_count"] == 1
    assert _read_json(library / "strict_reuse_indexes" / "C03_scene_decor_container.json")["asset_count"] == 1


def test_general_field_does_not_change_dedupe_result(tmp_path):
    library = tmp_path / "materials_library_ppt"
    image_dir = library / "pptx_images"
    image_dir.mkdir(parents=True)
    _draw_box(image_dir / "a.png")
    _draw_box(image_dir / "b.png")
    _write_json(
        library / "strict_reuse_indexes" / "C02_generic_subject_object.json",
        {
            "strict_reuse_group": "C02_generic_subject_object",
            "asset_count": 2,
            "assets": [
                _asset(
                    "a",
                    "pptx_images/a.png",
                    caption="蓝色书本",
                    query="蓝色书本",
                    group="C02_generic_subject_object",
                    general=False,
                ),
                _asset(
                    "b",
                    "pptx_images/b.png",
                    caption="蓝色书本",
                    query="蓝色书本",
                    group="C02_generic_subject_object",
                    general=True,
                ),
            ],
        },
    )

    report = dedupe_ppt_split_index_library(library, apply=False)

    assert report["mergeable_group_count"] == 1


def test_c01_visual_match_text_mismatch_does_not_merge(tmp_path):
    library = tmp_path / "materials_library_ppt"
    image_dir = library / "pptx_images"
    image_dir.mkdir(parents=True)
    _draw_box(image_dir / "a.png")
    _draw_box(image_dir / "b.png")
    _write_json(
        library / "strict_reuse_indexes" / "C01_irreplaceable_entity_event_action.json",
        {
            "strict_reuse_group": "C01_irreplaceable_entity_event_action",
            "asset_count": 2,
            "assets": [
                _asset(
                    "a",
                    "pptx_images/a.png",
                    caption="西湖水景",
                    query="西湖水景",
                    group="C01_irreplaceable_entity_event_action",
                ),
                _asset(
                    "b",
                    "pptx_images/b.png",
                    caption="黄鹤楼水墨建筑",
                    query="黄鹤楼水墨建筑",
                    group="C01_irreplaceable_entity_event_action",
                ),
            ],
        },
    )

    report = dedupe_ppt_split_index_library(library, apply=True)

    assert report["mergeable_group_count"] == 0
    assert _read_json(library / "strict_reuse_indexes" / "C01_irreplaceable_entity_event_action.json")["asset_count"] == 2


def test_dedupe_script_cli_reads_split_indexes_only(tmp_path, capsys):
    import importlib.util

    module_path = Path(__file__).resolve().parents[1] / "scripts" / "dedupe_ppt_materials_library.py"
    spec = importlib.util.spec_from_file_location("dedupe_ppt_materials_library", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    library = tmp_path / "materials_library_ppt"
    image_dir = library / "pptx_images"
    image_dir.mkdir(parents=True)
    _draw_box(image_dir / "a.png")
    _draw_box(image_dir / "b.png")
    _write_json(
        library / "strict_reuse_indexes" / "C03_scene_decor_container.json",
        {
            "strict_reuse_group": "C03_scene_decor_container",
            "asset_count": 2,
            "assets": [
                _asset("a", "pptx_images/a.png", caption="浅蓝空白边框底图", query="浅蓝空白边框底图"),
                _asset("b", "pptx_images/b.png", caption="浅蓝色空白边框底图", query="浅蓝色空白边框底图"),
            ],
        },
    )

    module.main(["--library-dir", str(library)])

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["mode"] == "dry_run"
    assert output["mergeable_group_count"] == 1
    assert output["report_path"].endswith("ppt_dedupe_report.json")
    assert not (library / "ai_image_match_index.json").exists()


# ── M-3 修复：感知去重不再被文本门槛架空 ──────────────────────────────

C02_RULE = BUCKET_RULES["C02"]  # visual_threshold=6, text_threshold=0.70


def _info(asset_id: str, phash: str, *, caption: str, query: str, color=(0, 0, 0)) -> PptDedupeInfo:
    asset = {
        "asset_id": asset_id,
        "asset_kind": "page_image",
        "aspect_ratio": "16:9",
        "caption": caption,
        "query": query,
        "normalized_prompt": query,
        "strict_reuse_group": "C02_generic_subject_object",
    }
    return PptDedupeInfo(
        bucket="C02",
        source_index="C02_generic_subject_object",
        asset=asset,
        asset_id=asset_id,
        image_path=f"{asset_id}.png",
        original_image_path=f"{asset_id}.png",
        resolved_image_path=None,
        resolved_original_image_path=None,
        image_exists=True,
        perceptual_hash=phash,
        color_signature=color,
    )


def test_visual_identity_bypasses_text_gate():
    # distance 1 (<= VISUAL_IDENTITY_DISTANCE)，caption 文本几乎无重叠（远低于 0.70 门槛）
    a = _info("a", "0000000000000000", caption="青蛙在荷叶上观察蝌蚪", query="青蛙在荷叶上观察蝌蚪")
    b = _info("b", "0000000000000001", caption="草原上奔跑的棕色骏马", query="草原上奔跑的棕色骏马")
    pair = _visual_pair_reason("C02", a, b, C02_RULE)
    assert pair is not None  # 旧逻辑会因文本门槛返回 None
    assert pair["reason"] == "visual_identity_near_duplicate"
    assert pair["distance"] <= VISUAL_IDENTITY_DISTANCE


def test_gray_zone_still_requires_text_corroboration():
    # distance 4：IDENTITY < 4 <= visual_threshold(6)，灰区仍按 AND 逻辑
    low_a = _info("la", "0000000000000000", caption="青蛙", query="青蛙")
    low_b = _info("lb", "000000000000000f", caption="骏马奔腾在草原", query="骏马奔腾在草原")
    assert _visual_pair_reason("C02", low_a, low_b, C02_RULE) is None  # 文本低 → 不合并

    hi_a = _info("ha", "0000000000000000", caption="浅蓝空白边框底图", query="浅蓝空白边框底图")
    hi_b = _info("hb", "000000000000000f", caption="浅蓝空白边框底图", query="浅蓝空白边框底图")
    merged = _visual_pair_reason("C02", hi_a, hi_b, C02_RULE)
    assert merged is not None and merged["reason"] == "visual_and_text_near_duplicate"


def test_distance_over_threshold_never_merges_even_with_identical_text():
    # distance 7 > visual_threshold(6)，即使文本完全相同也不合并
    a = _info("a", "0000000000000000", caption="同样的描述", query="同样的描述")
    b = _info("b", "000000000000007f", caption="同样的描述", query="同样的描述")
    assert _visual_pair_reason("C02", a, b, C02_RULE) is None


def test_identity_bypass_requires_similar_color_not_just_dhash():
    # dHash 颜色盲点：两块纯色都哈希成全 0（distance 0），但平均色差很大 → 不应误并。
    blue = _info("blue", "0000000000000000", caption="纯蓝底块", query="纯蓝底块", color=(120, 180, 220))
    red = _info("red", "0000000000000000", caption="纯红底块", query="纯红底块", color=(220, 60, 40))
    assert _visual_pair_reason("C02", blue, red, C02_RULE) is None  # 颜色门拦住，文本也低
    # 同色（同图 resize）则仍走同一性短路合并
    same = _info("same", "0000000000000000", caption="池塘边的小动物", query="池塘边的小动物", color=(120, 180, 220))
    merged = _visual_pair_reason("C02", blue, same, C02_RULE)
    assert merged is not None and merged["reason"] == "visual_identity_near_duplicate"


def test_split_index_merges_same_image_different_size_via_identity(tmp_path):
    # 端到端：同一图片不同尺寸 + 不同 caption。dHash 距离=0，旧逻辑因文本门槛不合并，
    # 新同一性短路合并为 1（治用户报告的"同一图片不同尺寸重复入库"）。
    library = tmp_path / "materials_library_ppt"
    image_dir = library / "pptx_images"
    image_dir.mkdir(parents=True)
    _draw_box(image_dir / "full.png")
    Image.open(image_dir / "full.png").resize((160, 90), Image.LANCZOS).save(image_dir / "small.png")
    _write_json(
        library / "strict_reuse_indexes" / "C02_generic_subject_object.json",
        {
            "strict_reuse_group": "C02_generic_subject_object",
            "asset_count": 2,
            "assets": [
                _asset(
                    "full",
                    "pptx_images/full.png",
                    caption="青蛙在荷叶上观察蝌蚪",
                    query="青蛙在荷叶上观察蝌蚪",
                    group="C02_generic_subject_object",
                ),
                _asset(
                    "small",
                    "pptx_images/small.png",
                    caption="草原上奔跑的棕色骏马",
                    query="草原上奔跑的棕色骏马",
                    group="C02_generic_subject_object",
                ),
            ],
        },
    )

    report = dedupe_ppt_split_index_library(library, apply=True)

    payload = _read_json(library / "strict_reuse_indexes" / "C02_generic_subject_object.json")
    assert payload["asset_count"] == 1
    assert report["applied_removed_count"] == 1
    assert report["groups"][0]["reason"] == "visual_identity_near_duplicate"
