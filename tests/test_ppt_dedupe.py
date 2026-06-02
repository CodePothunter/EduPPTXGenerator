from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from edupptx.materials.ppt_dedupe import dedupe_ppt_split_index_library


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
