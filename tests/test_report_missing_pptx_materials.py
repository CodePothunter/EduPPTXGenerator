from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.report_missing_pptx_materials import report_missing_pptx_materials


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _asset(asset_id: str, file_name: str, *, theme: str = "Theme") -> dict:
    return {
        "asset_id": asset_id,
        "file_name": file_name,
        "image_path": f"pptx_images/{asset_id}.png",
        "original_image_path": f"pptx_images_original/{asset_id}.png",
        "theme": theme,
    }


def _create_teach_kb_db(pptx_root: Path) -> Path:
    teach_root = pptx_root.parent.parent.parent
    db_path = teach_root / "data" / "db" / "teach_kb.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "CREATE TABLE pptx_files (id TEXT, period_id TEXT, file_path TEXT, file_name TEXT, file_size INTEGER, description TEXT)"
        )
        con.executemany(
            "INSERT INTO pptx_files (id, period_id, file_path, file_name, file_size, description) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("ppt1", "period1", "pptx/one.pptx", "one.pptx", 10, ""),
                ("ppt2", "period2", "pptx/two.pptx", "two.pptx", 20, ""),
                ("ppt3", "period3", "nested/three.pptx", "three.pptx", 30, ""),
            ],
        )
        con.commit()
    finally:
        con.close()
    return db_path


def _create_teach_kb_db_with_hierarchy(pptx_root: Path) -> Path:
    teach_root = pptx_root.parent.parent.parent
    db_path = teach_root / "data" / "db" / "teach_kb.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.execute("CREATE TABLE hierarchy (id TEXT, parent_id TEXT, name TEXT, subject TEXT)")
        con.execute(
            "CREATE TABLE pptx_files (id TEXT, period_id TEXT, file_path TEXT, file_name TEXT, file_size INTEGER, description TEXT)"
        )
        con.executemany(
            "INSERT INTO hierarchy (id, parent_id, name, subject) VALUES (?, ?, ?, ?)",
            [
                ("grade1", "", "Grade 1", ""),
                ("semester1", "grade1", "Spring", ""),
                ("unit1", "semester1", "Unit 1", ""),
                ("lesson1", "unit1", "Lesson One", ""),
                ("period1", "lesson1", "Period A", "Math"),
                ("grade2", "", "Grade 2", ""),
                ("semester2", "grade2", "Spring", ""),
                ("unit2", "semester2", "Unit 2", ""),
                ("lesson2", "unit2", "Lesson Two", ""),
                ("period2", "lesson2", "Period B", "Chinese"),
            ],
        )
        con.executemany(
            "INSERT INTO pptx_files (id, period_id, file_path, file_name, file_size, description) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("ppt1", "period1", "pptx/one.pptx", "one.pptx", 10, ""),
                ("ppt2", "period2", "pptx/two.pptx", "two.pptx", 20, ""),
            ],
        )
        con.commit()
    finally:
        con.close()
    return db_path


def test_reports_missing_pptx_by_file_name(tmp_path):
    pptx_root = tmp_path / "teach-kb" / "data" / "uploads" / "pptx"
    pptx_root.mkdir(parents=True)
    db_path = _create_teach_kb_db(pptx_root)

    library = tmp_path / "materials_library_ppt"
    _write_json(
        library / "strict_reuse_indexes" / "C02_generic_subject_object.json",
        {
            "strict_reuse_group": "C02_generic_subject_object",
            "assets": [
                _asset("a", "one.pptx", theme="ThemeOne"),
                _asset("b", "one.pptx", theme="ThemeOne"),
                _asset("c", "two.pptx", theme="ThemeTwo"),
            ],
        },
    )

    report = report_missing_pptx_materials(library_dir=library, teach_kb_root=pptx_root)

    assert report["db_path"] == str(db_path)
    assert report["db_pptx_count"] == 3
    assert report["indexed_file_name_count"] == 2
    assert report["missing_pptx_count"] == 1
    assert report["missing_pptx"] == [
        {
            "id": "ppt3",
            "period_id": "period3",
            "file_path": "nested/three.pptx",
            "file_name": "three.pptx",
            "theme": "",
            "absolute_path": str((pptx_root / "nested/three.pptx").resolve()),
            "reason": "file_name_not_found_in_background_c01_c02_c03",
        }
    ]
    written = json.loads((library / "missing_pptx_report.json").read_text(encoding="utf-8"))
    assert written["missing_pptx_count"] == 1


def test_writes_rerun_script_for_missing_pptx(tmp_path):
    pptx_root = tmp_path / "teach-kb" / "data" / "uploads" / "pptx"
    pptx_root.mkdir(parents=True)
    _create_teach_kb_db(pptx_root)

    library = tmp_path / "materials_library_ppt"
    _write_json(
        library / "strict_reuse_indexes" / "background.json",
        {
            "strict_reuse_group": "background",
            "assets": [_asset("a", "one.pptx", theme="ThemeOne")],
        },
    )

    report = report_missing_pptx_materials(
        library_dir=library,
        teach_kb_root=pptx_root,
        write_rerun_script=True,
    )

    script_path = Path(report["rerun_script_path"])
    text = script_path.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert "--flush-every 1" in text
    assert f'--teach-kb-root "{pptx_root.resolve()}"' in text
    assert f'--library-dir "{library.resolve()}"' in text
    assert f'--pptx "{(pptx_root / "two.pptx").resolve()}"' in text
    assert f'--pptx "{(pptx_root / "nested/three.pptx").resolve()}"' in text


def test_matches_pptx_coverage_from_source_pptx_refs_before_theme_fallback(tmp_path):
    pptx_root = tmp_path / "teach-kb" / "data" / "uploads" / "pptx"
    pptx_root.mkdir(parents=True)
    _create_teach_kb_db(pptx_root)

    library = tmp_path / "materials_library_ppt"
    _write_json(
        library / "strict_reuse_indexes" / "C02_generic_subject_object.json",
        {
            "strict_reuse_group": "C02_generic_subject_object",
            "assets": [
                {
                    "asset_id": "a",
                    "image_path": "pptx_images/a.png",
                    "original_image_path": "pptx_images_original/a.png",
                    "theme": "",
                    "source_pptx_refs": [
                        {
                            "pptx_id": "ppt1",
                            "file_path": "pptx/one.pptx",
                            "file_name": "one.pptx",
                            "absolute_path": str((pptx_root / "one.pptx").resolve()),
                        }
                    ],
                },
                {
                    "asset_id": "b",
                    "image_path": "pptx_images/b.png",
                    "original_image_path": "pptx_images_original/b.png",
                    "theme": "",
                    "source_pptx_refs": [
                        {
                            "file_path": "pptx/two.pptx",
                            "file_name": "two.pptx",
                        }
                    ],
                },
            ],
        },
    )

    report = report_missing_pptx_materials(library_dir=library, teach_kb_root=pptx_root)

    assert report["match_mode"] == "source_pptx_refs"
    assert report["indexed_source_ref_count"] == 2
    assert report["missing_pptx_count"] == 1
    assert report["missing_pptx"][0]["file_name"] == "three.pptx"
    assert report["missing_pptx"][0]["reason"] == "source_pptx_ref_not_found_in_background_c01_c02_c03"


def test_falls_back_to_theme_when_index_assets_have_no_file_name(tmp_path):
    pptx_root = tmp_path / "teach-kb" / "data" / "uploads" / "pptx"
    pptx_root.mkdir(parents=True)
    _create_teach_kb_db_with_hierarchy(pptx_root)

    library = tmp_path / "materials_library_ppt"
    _write_json(
        library / "strict_reuse_indexes" / "C03_scene_decor_container.json",
        {
            "strict_reuse_group": "C03_scene_decor_container",
            "assets": [
                {
                    "asset_id": "a",
                    "image_path": "pptx_images/a.png",
                    "original_image_path": "pptx_images_original/a.png",
                    "theme": "Grade1MathLessonOnePeriodA",
                }
            ],
        },
    )

    report = report_missing_pptx_materials(library_dir=library, teach_kb_root=pptx_root)

    assert report["match_mode"] == "theme"
    assert report["indexed_file_name_count"] == 0
    assert report["indexed_theme_count"] == 1
    assert report["missing_pptx_count"] == 1
    assert report["missing_pptx"][0]["file_name"] == "two.pptx"
    assert report["missing_pptx"][0]["reason"] == "theme_not_found_in_background_c01_c02_c03"
