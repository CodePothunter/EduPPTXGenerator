from __future__ import annotations

import json
from pathlib import Path

from scripts.write_rerun_from_pptx_debug import write_rerun_from_pptx_debug


def _write_debug(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "uncertain_pptx": [
                    {
                        "status": "unconfirmed",
                        "absolute_path": "/srv/teach-kb/data/uploads/pptx/a.pptx",
                    },
                    {
                        "status": "partial_asset_hash_match",
                        "absolute_path": "/srv/teach-kb/data/uploads/pptx/b.pptx",
                    },
                    {
                        "status": "extract_failed",
                        "absolute_path": "/srv/teach-kb/data/uploads/pptx/bad.pptx",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_writes_default_rerun_script_for_all_uncertain_statuses(tmp_path):
    library = tmp_path / "materials_library_ppt"
    debug_path = library / "debug.json"
    _write_debug(debug_path)

    report = write_rerun_from_pptx_debug(
        debug_json=debug_path,
        library_dir=library,
        teach_kb_root="/srv/teach-kb/data/uploads/pptx",
    )

    script = (library / "rerun_debug_pptx.sh").read_text(encoding="utf-8")
    paths = (library / "rerun_debug_pptx_paths.txt").read_text(encoding="utf-8").splitlines()

    assert report["selected_count"] == 3
    assert report["status_counts"] == {
        "extract_failed": 1,
        "partial_asset_hash_match": 1,
        "unconfirmed": 1,
    }
    assert paths == [
        "/srv/teach-kb/data/uploads/pptx/a.pptx",
        "/srv/teach-kb/data/uploads/pptx/b.pptx",
        "/srv/teach-kb/data/uploads/pptx/bad.pptx",
    ]
    assert "--pptx \"/srv/teach-kb/data/uploads/pptx/a.pptx\"" in script
    assert "--flush-every 1" in script
    assert "bad.pptx" in script


def test_can_include_extract_failed_when_requested(tmp_path):
    library = tmp_path / "materials_library_ppt"
    debug_path = library / "debug.json"
    _write_debug(debug_path)

    report = write_rerun_from_pptx_debug(
        debug_json=debug_path,
        library_dir=library,
        teach_kb_root="/srv/teach-kb/data/uploads/pptx",
        statuses=["unconfirmed", "extract_failed"],
    )

    paths = (library / "rerun_debug_pptx_paths.txt").read_text(encoding="utf-8").splitlines()

    assert report["selected_count"] == 2
    assert paths == [
        "/srv/teach-kb/data/uploads/pptx/a.pptx",
        "/srv/teach-kb/data/uploads/pptx/bad.pptx",
    ]
