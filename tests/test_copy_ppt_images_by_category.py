from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def test_copy_ppt_images_by_category_groups_by_index_category(tmp_path):
    module = _load_module()
    library_dir = tmp_path / "materials_library_ppt"
    image_dir = library_dir / "pptx_images"
    index_dir = library_dir / "strict_reuse_indexes"
    output_dir = tmp_path / "grouped"
    image_dir.mkdir(parents=True)
    index_dir.mkdir(parents=True)
    (image_dir / "asset.png").write_bytes(b"image")
    _write_json(
        index_dir / "C02_generic_subject_object.json",
        {
            "strict_reuse_group": "C02_generic_subject_object",
            "assets": [
                {
                    "asset_id": "asset",
                    "image_path": "pptx_images/asset.png",
                }
            ],
        },
    )

    report = module.copy_ppt_images_by_category(library_dir, output_dir)

    copied = output_dir / "C02_generic_subject_object" / "asset.png"
    assert copied.read_bytes() == b"image"
    assert report["asset_count"] == 1
    assert report["copied_count"] == 1
    assert report["categories"] == {"C02_generic_subject_object": 1}


def test_copy_ppt_images_by_category_keeps_background_index_separate(tmp_path):
    module = _load_module()
    library_dir = tmp_path / "materials_library_ppt"
    image_dir = library_dir / "pptx_images"
    index_dir = library_dir / "strict_reuse_indexes"
    output_dir = tmp_path / "grouped"
    image_dir.mkdir(parents=True)
    index_dir.mkdir(parents=True)
    (image_dir / "bg.png").write_bytes(b"background")
    _write_json(
        index_dir / "background.json",
        {
            "strict_reuse_group": "background",
            "assets": [
                {
                    "asset_id": "bg",
                    "image_path": "pptx_images/bg.png",
                    "strict_reuse_group": "C03_scene_decor_container",
                }
            ],
        },
    )

    report = module.copy_ppt_images_by_category(library_dir, output_dir)

    assert (output_dir / "background" / "bg.png").read_bytes() == b"background"
    assert not (output_dir / "C03_scene_decor_container").exists()
    assert report["categories"] == {"background": 1}


def test_copy_ppt_images_by_category_dry_run_does_not_copy(tmp_path):
    module = _load_module()
    library_dir = tmp_path / "materials_library_ppt"
    image_dir = library_dir / "pptx_images"
    index_dir = library_dir / "strict_reuse_indexes"
    output_dir = tmp_path / "grouped"
    image_dir.mkdir(parents=True)
    index_dir.mkdir(parents=True)
    (image_dir / "asset.png").write_bytes(b"image")
    _write_json(
        index_dir / "C03_scene_decor_container.json",
        {
            "strict_reuse_group": "C03_scene_decor_container",
            "assets": [{"asset_id": "asset", "image_path": "pptx_images/asset.png"}],
        },
    )

    report = module.copy_ppt_images_by_category(library_dir, output_dir, dry_run=True)

    assert report["copied_count"] == 1
    assert not output_dir.exists()


def test_copy_ppt_images_by_category_reports_missing_image(tmp_path):
    module = _load_module()
    library_dir = tmp_path / "materials_library_ppt"
    index_dir = library_dir / "strict_reuse_indexes"
    index_dir.mkdir(parents=True)
    _write_json(
        index_dir / "C00_strict_text_problem_skip.json",
        {
            "strict_reuse_group": "C00_strict_text_problem_skip",
            "assets": [{"asset_id": "missing", "image_path": "pptx_images/missing.png"}],
        },
    )

    report = module.copy_ppt_images_by_category(library_dir, tmp_path / "grouped")

    assert report["copied_count"] == 0
    assert report["missing_image_count"] == 1
    assert report["warnings"] == [
        {
            "asset_id": "missing",
            "image_path": "pptx_images/missing.png",
            "reason": "missing_image",
        }
    ]


def test_copy_ppt_images_by_category_prefer_original(tmp_path):
    module = _load_module()
    library_dir = tmp_path / "materials_library_ppt"
    runtime_dir = library_dir / "pptx_images"
    original_dir = library_dir / "pptx_images_original"
    index_dir = library_dir / "strict_reuse_indexes"
    output_dir = tmp_path / "grouped"
    runtime_dir.mkdir(parents=True)
    original_dir.mkdir(parents=True)
    index_dir.mkdir(parents=True)
    (runtime_dir / "asset.png").write_bytes(b"runtime")
    (original_dir / "asset.png").write_bytes(b"original")
    _write_json(
        index_dir / "C02_generic_subject_object.json",
        {
            "strict_reuse_group": "C02_generic_subject_object",
            "assets": [
                {
                    "asset_id": "asset",
                    "image_path": "pptx_images/asset.png",
                    "original_image_path": "pptx_images_original/asset.png",
                }
            ],
        },
    )

    module.copy_ppt_images_by_category(library_dir, output_dir, prefer_original=True)

    assert (output_dir / "C02_generic_subject_object" / "asset.png").read_bytes() == b"original"


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "copy_ppt_images_by_category.py"
    spec = importlib.util.spec_from_file_location("copy_ppt_images_by_category", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
