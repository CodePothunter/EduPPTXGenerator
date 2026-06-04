from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


MODULE = importlib.import_module("scripts.extract_unindexed_pptx_originals")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_keep_indexes(library: Path, assets: list[dict]) -> None:
    split = library / "strict_reuse_indexes"
    groups = {
        "background.json": [],
        "C01_irreplaceable_entity_event_action.json": [],
        "C02_generic_subject_object.json": [],
        "C03_scene_decor_container.json": [],
    }
    groups["C02_generic_subject_object.json"] = assets
    for filename, group_assets in groups.items():
        _write_json(
            split / filename,
            {
                "schema_version": 1,
                "strict_reuse_group": filename.removesuffix(".json"),
                "assets": group_assets,
            },
        )


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image")


def test_extract_unindexed_pptx_originals_dry_run_leaves_files_in_place(tmp_path):
    library = tmp_path / "materials_library_ppt"
    output = tmp_path / "materials_library_ppt_c00_rerun"
    _write_keep_indexes(
        library,
        [
            {
                "asset_id": "keep",
                "image_path": "pptx_images/keep.png",
                "original_image_path": "pptx_images_original/keep.png",
            }
        ],
    )
    _touch(library / "pptx_images" / "keep.png")
    _touch(library / "pptx_images_original" / "keep.png")
    _touch(library / "pptx_images" / "missing.png")
    _touch(library / "pptx_images_original" / "missing.png")

    report = MODULE.extract_unindexed_pptx_originals(
        library_dir=library,
        output_dir=output,
        apply=False,
    )

    assert report["applied"] is False
    assert report["deleted_runtime_count"] == 1
    assert report["moved_original_count"] == 1
    assert (library / "pptx_images" / "missing.png").exists()
    assert (library / "pptx_images_original" / "missing.png").exists()
    assert not (output / "pptx_images_original" / "missing.png").exists()
    assert (output / "rerun_manifest.json").exists()


def test_extract_unindexed_pptx_originals_apply_moves_originals_and_deletes_runtime(tmp_path):
    library = tmp_path / "materials_library_ppt"
    output = tmp_path / "materials_library_ppt_c00_rerun"
    _write_keep_indexes(
        library,
        [
            {
                "asset_id": "keep",
                "image_path": "pptx_images/keep.png",
            }
        ],
    )
    _touch(library / "pptx_images" / "keep.png")
    _touch(library / "pptx_images_original" / "keep.png")
    _touch(library / "pptx_images" / "missing.png")
    _touch(library / "pptx_images_original" / "missing.png")
    _touch(library / "pptx_images" / "runtime_only.png")
    _touch(library / "pptx_images_original" / "original_only.png")

    report = MODULE.extract_unindexed_pptx_originals(
        library_dir=library,
        output_dir=output,
        apply=True,
    )

    assert report["applied"] is True
    assert report["deleted_runtime_count"] == 2
    assert report["moved_original_count"] == 2
    assert (library / "pptx_images" / "keep.png").exists()
    assert (library / "pptx_images_original" / "keep.png").exists()
    assert not (library / "pptx_images" / "missing.png").exists()
    assert not (library / "pptx_images_original" / "missing.png").exists()
    assert not (library / "pptx_images" / "runtime_only.png").exists()
    assert not (library / "pptx_images_original" / "original_only.png").exists()
    assert (output / "pptx_images_original" / "missing.png").exists()
    assert (output / "pptx_images_original" / "original_only.png").exists()
    assert not (output / "pptx_images").exists()


def test_extract_unindexed_pptx_originals_requires_all_keep_indexes(tmp_path):
    library = tmp_path / "materials_library_ppt"
    (library / "strict_reuse_indexes").mkdir(parents=True)
    _write_json(
        library / "strict_reuse_indexes" / "background.json",
        {"schema_version": 1, "assets": []},
    )

    with pytest.raises(FileNotFoundError):
        MODULE.extract_unindexed_pptx_originals(
            library_dir=library,
            output_dir=tmp_path / "out",
            apply=False,
        )
