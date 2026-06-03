from __future__ import annotations

import json
from pathlib import Path

from scripts.backfill_ppt_source_refs import backfill_ppt_source_refs


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_backfills_source_refs_into_existing_split_indexes(tmp_path):
    library = tmp_path / "materials_library_ppt"
    manifest_path = library / "processed_pptx_manifest.backfilled.jsonl"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "pptx_id": "ppt-a",
                "period_id": "period-a",
                "file_path": "pptx/a.pptx",
                "file_name": "a.pptx",
                "absolute_path": "/srv/teach-kb/data/uploads/pptx/a.pptx",
                "status": "confirmed_by_asset_hash",
                "candidate_asset_ids": ["kbpptx_11111111111111111111"],
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "pptx_id": "ppt-b",
                "period_id": "period-b",
                "file_path": "pptx/b.pptx",
                "file_name": "b.pptx",
                "absolute_path": "/srv/teach-kb/data/uploads/pptx/b.pptx",
                "status": "partial_asset_hash_match",
                "candidate_asset_ids": ["kbpptx_11111111111111111111", "kbpptx_missing"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_json(
        library / "strict_reuse_indexes" / "C03_scene_decor_container.json",
        {
            "assets": [
                {
                    "asset_id": "kbpptx_11111111111111111111",
                    "asset_kind": "page_image",
                    "image_path": "pptx_images/kbpptx_11111111111111111111.png",
                }
            ]
        },
    )

    report = backfill_ppt_source_refs(library_dir=library, manifest_path=manifest_path)

    payload = json.loads(
        (library / "strict_reuse_indexes" / "C03_scene_decor_container.json").read_text(encoding="utf-8")
    )
    refs = payload["assets"][0]["source_pptx_refs"]
    assert report["updated_asset_count"] == 1
    assert report["source_ref_count"] == 2
    assert refs == [
        {
            "pptx_id": "ppt-a",
            "period_id": "period-a",
            "file_path": "pptx/a.pptx",
            "file_name": "a.pptx",
            "absolute_path": "/srv/teach-kb/data/uploads/pptx/a.pptx",
            "source": "backfilled_by_asset_hash",
        },
        {
            "pptx_id": "ppt-b",
            "period_id": "period-b",
            "file_path": "pptx/b.pptx",
            "file_name": "b.pptx",
            "absolute_path": "/srv/teach-kb/data/uploads/pptx/b.pptx",
            "source": "backfilled_by_asset_hash",
        },
    ]
