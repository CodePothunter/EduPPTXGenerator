from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from PIL import Image

from scripts.archive_unindexed_ppt_skip_images import archive_unindexed_ppt_skip_images


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _draw(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (120, 160, 200)).save(path)


def _asset(asset_id: str, image_path: str, original_image_path: str, *, theme: str, group: str) -> dict:
    return {
        "asset_id": asset_id,
        "image_path": image_path,
        "original_image_path": original_image_path,
        "theme": theme,
        "strict_reuse_group": group,
    }


def test_archives_unindexed_originals_and_deletes_unindexed_runtime_images(tmp_path):
    library = tmp_path / "materials_library_ppt"
    runtime_dir = library / "pptx_images"
    original_dir = library / "pptx_images_original"

    _draw(runtime_dir / "keep_c01.png")
    _draw(original_dir / "keep_c01.png")
    _draw(runtime_dir / "keep_bg.png")
    _draw(original_dir / "keep_bg.png")
    _draw(runtime_dir / "skip_me.png")
    _draw(original_dir / "skip_me.png")

    _write_json(
        library / "strict_reuse_indexes" / "C01_irreplaceable_entity_event_action.json",
        {
            "strict_reuse_group": "C01_irreplaceable_entity_event_action",
            "assets": [
                _asset(
                    "keep_c01",
                    "pptx_images/keep_c01.png",
                    "pptx_images_original/keep_c01.png",
                    theme="Grade1ChineseLesson1",
                    group="C01_irreplaceable_entity_event_action",
                )
            ],
        },
    )
    _write_json(
        library / "strict_reuse_indexes" / "background.json",
        {
            "strict_reuse_group": "background",
            "assets": [
                _asset(
                    "keep_bg",
                    "pptx_images/keep_bg.png",
                    "pptx_images_original/keep_bg.png",
                    theme="Grade2MathLesson1",
                    group="background",
                )
            ],
        },
    )

    report = archive_unindexed_ppt_skip_images(library_dir=library, teach_kb_root=tmp_path / "teach-kb" / "data" / "uploads" / "pptx")

    assert (runtime_dir / "keep_c01.png").exists()
    assert (original_dir / "keep_c01.png").exists()
    assert (runtime_dir / "keep_bg.png").exists()
    assert (original_dir / "keep_bg.png").exists()
    assert not (runtime_dir / "skip_me.png").exists()
    assert not (original_dir / "skip_me.png").exists()
    assert (library / "skip_image" / "skip_me.png").exists()
    assert report["deleted_runtime_count"] == 1
    assert report["moved_original_count"] == 1


def test_reports_ppt_progress_from_inferred_teach_kb_db(tmp_path):
    teach_root = tmp_path / "teach-kb"
    pptx_root = teach_root / "data" / "uploads" / "pptx"
    db_dir = teach_root / "data" / "db"
    db_dir.mkdir(parents=True)
    pptx_root.mkdir(parents=True)

    con = sqlite3.connect(db_dir / "teach_kb.db")
    try:
        con.execute("CREATE TABLE pptx_files (id TEXT, period_id TEXT, file_path TEXT, file_name TEXT)")
        con.executemany(
            "INSERT INTO pptx_files (id, period_id, file_path, file_name) VALUES (?, ?, ?, ?)",
            [
                ("ppt1", "period1", "pptx/one.pptx", "one.pptx"),
                ("ppt2", "period2", "pptx/two.pptx", "two.pptx"),
                ("ppt3", "period3", "pptx/three.pptx", "three.pptx"),
            ],
        )
        con.commit()
    finally:
        con.close()

    library = tmp_path / "materials_library_ppt"
    _write_json(
        library / "strict_reuse_indexes" / "C02_generic_subject_object.json",
        {
            "strict_reuse_group": "C02_generic_subject_object",
            "assets": [
                _asset("a", "pptx_images/a.png", "pptx_images_original/a.png", theme="ThemeOne", group="C02_generic_subject_object"),
                _asset("b", "pptx_images/b.png", "pptx_images_original/b.png", theme="ThemeTwo", group="C02_generic_subject_object"),
                _asset("c", "pptx_images/c.png", "pptx_images_original/c.png", theme="ThemeTwo", group="C02_generic_subject_object"),
            ],
        },
    )

    report = archive_unindexed_ppt_skip_images(library_dir=library, teach_kb_root=pptx_root)

    assert report["db_path"] == str(db_dir / "teach_kb.db")
    assert report["db_pptx_count"] == 3
    assert report["extracted_theme_count"] == 2
    assert report["missing_ppt_count"] == 1
