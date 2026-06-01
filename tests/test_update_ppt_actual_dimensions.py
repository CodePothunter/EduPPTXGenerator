import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "update_ppt_actual_dimensions.py"
SPEC = importlib.util.spec_from_file_location("update_ppt_actual_dimensions", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_update_ppt_actual_dimensions_adds_fields_without_touching_aspect_ratio(tmp_path):
    library_dir = tmp_path / "materials_library_ppt"
    image_dir = library_dir / "pptx_images"
    split_dir = library_dir / "strict_reuse_indexes"
    image_dir.mkdir(parents=True)
    split_dir.mkdir(parents=True)

    Image.new("RGBA", (321, 123), (255, 0, 0, 255)).save(image_dir / "asset.png")
    index_path = split_dir / "general_reuse.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": 13,
                "strict_reuse_group": "general_reuse",
                "asset_count": 1,
                "assets": [
                    {
                        "asset_id": "asset",
                        "asset_kind": "page_image",
                        "image_path": "pptx_images/asset.png",
                        "aspect_ratio": "3:2",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    report = MODULE.update_ppt_actual_dimensions(library_dir)

    updated = json.loads(index_path.read_text(encoding="utf-8"))
    asset = updated["assets"][0]
    assert asset["actual_width"] == 321
    assert asset["actual_height"] == 123
    assert asset["aspect_ratio"] == "3:2"
    assert "aspect_bucket" not in asset
    assert report["asset_count"] == 1
    assert report["updated_count"] == 1
    assert report["missing_image_count"] == 0


def test_update_ppt_actual_dimensions_reports_missing_images_without_mutating_asset(tmp_path):
    library_dir = tmp_path / "materials_library_ppt"
    split_dir = library_dir / "strict_reuse_indexes"
    split_dir.mkdir(parents=True)

    index_path = split_dir / "content_reuse.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": 13,
                "strict_reuse_group": "content_reuse",
                "asset_count": 1,
                "assets": [
                    {
                        "asset_id": "missing",
                        "asset_kind": "page_image",
                        "image_path": "pptx_images/missing.png",
                        "aspect_ratio": "21:9",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    report = MODULE.update_ppt_actual_dimensions(library_dir)

    updated = json.loads(index_path.read_text(encoding="utf-8"))
    asset = updated["assets"][0]
    assert asset == {
        "asset_id": "missing",
        "asset_kind": "page_image",
        "image_path": "pptx_images/missing.png",
        "aspect_ratio": "21:9",
    }
    assert report["asset_count"] == 1
    assert report["updated_count"] == 0
    assert report["missing_image_count"] == 1
    assert report["warnings"] == [
        {
            "asset_id": "missing",
            "image_path": "pptx_images/missing.png",
            "reason": "missing_image",
        }
    ]


def test_update_ppt_actual_dimensions_writes_transparent_padded_derivative(tmp_path):
    library_dir = tmp_path / "materials_library_ppt"
    image_dir = library_dir / "pptx_images"
    split_dir = library_dir / "strict_reuse_indexes"
    image_dir.mkdir(parents=True)
    split_dir.mkdir(parents=True)

    Image.new("RGBA", (120, 100), (20, 80, 160, 255)).save(image_dir / "asset.png")
    index_path = split_dir / "C03_scene_decor_container.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": 13,
                "strict_reuse_group": "C03_scene_decor_container",
                "asset_count": 1,
                "assets": [
                    {
                        "asset_id": "asset",
                        "asset_kind": "page_image",
                        "image_path": "pptx_images/asset.png",
                        "aspect_ratio": "3:2",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    report = MODULE.update_ppt_actual_dimensions(library_dir, write_padded=True)

    updated = json.loads(index_path.read_text(encoding="utf-8"))
    asset = updated["assets"][0]
    assert asset["image_path"] == "pptx_images/asset.png"
    assert asset["original_image_path"] == "pptx_images_original/asset.png"
    assert asset["actual_width"] == 120
    assert asset["actual_height"] == 100
    assert asset["aspect_ratio"] == "4:3"
    assert asset["padded_width"] == 136
    assert asset["padded_height"] == 102
    assert "padded_image_path" not in asset

    with Image.open(library_dir / asset["original_image_path"]) as original:
        assert original.size == (120, 100)
    with Image.open(library_dir / asset["image_path"]) as padded:
        assert padded.size == (136, 102)
        assert padded.mode == "RGBA"
        assert padded.getpixel((0, 0))[3] == 0
        assert padded.getpixel((8, 1)) == (20, 80, 160, 255)

    assert report["asset_count"] == 1
    assert report["updated_count"] == 1
    assert report["original_created_count"] == 1
    assert report["padded_written_count"] == 1


def test_update_ppt_actual_dimensions_keeps_other_images_unpadded(tmp_path):
    library_dir = tmp_path / "materials_library_ppt"
    image_dir = library_dir / "pptx_images"
    split_dir = library_dir / "strict_reuse_indexes"
    image_dir.mkdir(parents=True)
    split_dir.mkdir(parents=True)

    Image.new("RGBA", (100, 1200), (200, 40, 40, 255)).save(image_dir / "tall.png")
    index_path = split_dir / "C03_scene_decor_container.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": 13,
                "strict_reuse_group": "C03_scene_decor_container",
                "asset_count": 1,
                "assets": [
                    {
                        "asset_id": "tall",
                        "asset_kind": "page_image",
                        "image_path": "pptx_images/tall.png",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    MODULE.update_ppt_actual_dimensions(library_dir, write_padded=True)

    updated = json.loads(index_path.read_text(encoding="utf-8"))
    asset = updated["assets"][0]
    assert asset["aspect_ratio"] == "other"
    assert asset["actual_width"] == 100
    assert asset["actual_height"] == 1200
    assert asset["padded_width"] == 100
    assert asset["padded_height"] == 1200
    with Image.open(library_dir / asset["image_path"]) as runtime:
        assert runtime.size == (100, 1200)


def test_update_ppt_actual_dimensions_dry_run_does_not_write_padded_files_or_json(tmp_path):
    library_dir = tmp_path / "materials_library_ppt"
    image_dir = library_dir / "pptx_images"
    split_dir = library_dir / "strict_reuse_indexes"
    image_dir.mkdir(parents=True)
    split_dir.mkdir(parents=True)

    Image.new("RGBA", (120, 100), (20, 80, 160, 255)).save(image_dir / "asset.png")
    index_path = split_dir / "C03_scene_decor_container.json"
    original_payload = {
        "schema_version": 13,
        "strict_reuse_group": "C03_scene_decor_container",
        "asset_count": 1,
        "assets": [
            {
                "asset_id": "asset",
                "asset_kind": "page_image",
                "image_path": "pptx_images/asset.png",
            }
        ],
    }
    index_path.write_text(json.dumps(original_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    report = MODULE.update_ppt_actual_dimensions(library_dir, write_padded=True, dry_run=True)

    assert json.loads(index_path.read_text(encoding="utf-8")) == original_payload
    assert not (library_dir / "pptx_images_original").exists()
    with Image.open(image_dir / "asset.png") as image:
        assert image.size == (120, 100)
    assert report["updated_count"] == 1
    assert report["original_created_count"] == 1
    assert report["padded_written_count"] == 1
