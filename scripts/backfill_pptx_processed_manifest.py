"""Backfill a PPTX processing manifest from an existing PPT material library.

This script does not call VLM/LLM and does not modify existing indexes. It
re-opens source PPTX files, computes the same image-hash asset IDs used by the
builder, and compares them with asset IDs already present in split indexes.
Uncertain rows are written to debug.json for manual review or rerun.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.build_ppt_materials_library import (
    _asset_id_for_sha,
    _classify_exclusion,
    _extract_raw_ppt_images,
    _repeated_wide_hashes,
)
from scripts.report_missing_pptx_materials import (
    DEFAULT_LIBRARY_DIR,
    DEFAULT_TEACH_KB_PPTX_ROOT,
    STRICT_REUSE_INDEX_DIRNAME,
    _absolute_pptx_path,
    _clean_text,
    _infer_teach_kb_db_path,
    _read_db_pptx_rows,
)


DEFAULT_MANIFEST_FILENAME = "processed_pptx_manifest.backfilled.jsonl"
DEFAULT_DEBUG_FILENAME = "debug.json"
INDEX_FILENAMES = (
    "background.json",
    "C00_strict_text_problem_skip.json",
    "C01_irreplaceable_entity_event_action.json",
    "C02_generic_subject_object.json",
    "C03_scene_decor_container.json",
)
ASSET_ID_FILE_DIRS = ("pptx_images", "pptx_images_original", "skip_image", "skip_images")
CONFIRMED_STATUSES = {"confirmed_by_asset_hash", "no_candidate_images"}
UNCERTAIN_STATUSES = {"partial_asset_hash_match", "unconfirmed", "extract_failed", "pptx_missing"}


def backfill_pptx_processed_manifest(
    *,
    library_dir: str | Path = DEFAULT_LIBRARY_DIR,
    teach_kb_root: str | Path = DEFAULT_TEACH_KB_PPTX_ROOT,
    db_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    debug_path: str | Path | None = None,
) -> dict[str, Any]:
    library_root = Path(library_dir).expanduser().resolve()
    pptx_root = Path(teach_kb_root).expanduser().resolve()
    resolved_db_path = Path(db_path).expanduser().resolve() if db_path else _infer_teach_kb_db_path(pptx_root)
    output_manifest = (
        Path(manifest_path).expanduser().resolve()
        if manifest_path
        else library_root / DEFAULT_MANIFEST_FILENAME
    )
    output_debug = Path(debug_path).expanduser().resolve() if debug_path else library_root / DEFAULT_DEBUG_FILENAME

    warnings: list[str] = []
    db_rows = _read_db_pptx_rows(resolved_db_path, warnings)
    indexed_asset_ids, index_summary = _read_indexed_asset_ids(library_root / STRICT_REUSE_INDEX_DIRNAME, warnings)
    file_asset_ids = _read_asset_ids_from_library_files(library_root)
    known_asset_ids = indexed_asset_ids | file_asset_ids

    manifest_rows: list[dict[str, Any]] = []
    uncertain_rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    for row in db_rows:
        entry = _backfill_single_pptx(
            row,
            pptx_root=pptx_root,
            known_asset_ids=known_asset_ids,
        )
        manifest_rows.append(entry)
        status = _clean_text(entry.get("status"))
        status_counts[status] = status_counts.get(status, 0) + 1
        if status in UNCERTAIN_STATUSES:
            uncertain_rows.append(entry)

    report: dict[str, Any] = {
        "library_dir": str(library_root),
        "teach_kb_root": str(pptx_root),
        "db_path": str(resolved_db_path),
        "index_dir": str(library_root / STRICT_REUSE_INDEX_DIRNAME),
        "index_files": list(INDEX_FILENAMES),
        "asset_id_file_dirs": list(ASSET_ID_FILE_DIRS),
        "manifest_path": str(output_manifest),
        "debug_path": str(output_debug),
        "db_pptx_count": len(db_rows),
        "indexed_asset_id_count": len(indexed_asset_ids),
        "file_asset_id_count": len(file_asset_ids),
        "known_asset_id_count": len(known_asset_ids),
        "indexed_asset_count": index_summary["indexed_asset_count"],
        "warning_count": 0,
        "warnings": warnings,
    }
    for status in [*sorted(CONFIRMED_STATUSES), *sorted(UNCERTAIN_STATUSES)]:
        report[f"{status}_count"] = status_counts.get(status, 0)
    report["confirmed_count"] = sum(status_counts.get(status, 0) for status in CONFIRMED_STATUSES)
    report["uncertain_count"] = len(uncertain_rows)
    report["warning_count"] = len(warnings)

    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in manifest_rows),
        encoding="utf-8",
    )
    output_debug.parent.mkdir(parents=True, exist_ok=True)
    output_debug.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "manifest_path": str(output_manifest),
                "uncertain_count": len(uncertain_rows),
                "uncertain_statuses": sorted(UNCERTAIN_STATUSES),
                "uncertain_pptx": uncertain_rows,
                "warnings": warnings,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return report


def _backfill_single_pptx(
    row: dict[str, Any],
    *,
    pptx_root: Path,
    known_asset_ids: set[str],
) -> dict[str, Any]:
    file_path = _clean_text(row.get("file_path"))
    file_name = _clean_text(row.get("file_name"))
    absolute_path = _absolute_pptx_path(pptx_root, file_path, file_name)
    base = {
        "pptx_id": _clean_text(row.get("id")),
        "period_id": _clean_text(row.get("period_id")),
        "file_path": file_path,
        "file_name": file_name,
        "absolute_path": str(absolute_path),
        "theme": _clean_text(row.get("theme")),
        "raw_picture_count": 0,
        "candidate_asset_count": 0,
        "matched_asset_count": 0,
        "candidate_asset_ids": [],
        "matched_asset_ids": [],
        "missing_asset_ids": [],
    }
    if not absolute_path.exists():
        return {**base, "status": "pptx_missing", "error": "pptx_missing"}
    try:
        raw_items = _extract_raw_ppt_images(absolute_path)
        wide_repeated = _repeated_wide_hashes(raw_items)
    except Exception as exc:
        return {**base, "status": "extract_failed", "error": f"{type(exc).__name__}: {exc}"}

    candidate_ids: list[str] = []
    skipped_reasons: dict[str, int] = {}
    for item in raw_items:
        reason = _classify_exclusion(item, wide_repeated)
        if reason:
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
            continue
        candidate_ids.append(_asset_id_for_sha(item.sha256))
    candidate_ids = _dedupe_keep_order(candidate_ids)
    matched_ids = [asset_id for asset_id in candidate_ids if asset_id in known_asset_ids]
    missing_ids = [asset_id for asset_id in candidate_ids if asset_id not in known_asset_ids]

    if not candidate_ids:
        status = "no_candidate_images"
    elif len(matched_ids) == len(candidate_ids):
        status = "confirmed_by_asset_hash"
    elif matched_ids:
        status = "partial_asset_hash_match"
    else:
        status = "unconfirmed"
    return {
        **base,
        "status": status,
        "raw_picture_count": len(raw_items),
        "candidate_asset_count": len(candidate_ids),
        "matched_asset_count": len(matched_ids),
        "candidate_asset_ids": candidate_ids,
        "matched_asset_ids": matched_ids,
        "missing_asset_ids": missing_ids,
        "skipped_picture_count": len(raw_items) - len(candidate_ids),
        "skipped_reasons": skipped_reasons,
        "error": None,
    }


def _read_indexed_asset_ids(index_dir: Path, warnings: list[str]) -> tuple[set[str], dict[str, int]]:
    asset_ids: set[str] = set()
    indexed_asset_count = 0
    for filename in INDEX_FILENAMES:
        path = index_dir / filename
        if not path.exists():
            warnings.append(f"missing_index:{filename}")
            continue
        try:
            payload = _read_json_object(path)
        except Exception as exc:
            warnings.append(f"index_read_failed:{filename}:{type(exc).__name__}: {exc}")
            continue
        assets = payload.get("assets")
        if not isinstance(assets, list):
            warnings.append(f"index_assets_not_list:{filename}")
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            indexed_asset_count += 1
            asset_id = _clean_text(asset.get("asset_id"))
            if asset_id:
                asset_ids.add(asset_id)
    return asset_ids, {"indexed_asset_count": indexed_asset_count}


def _read_asset_ids_from_library_files(library_root: Path) -> set[str]:
    asset_ids: set[str] = set()
    for dirname in ASSET_ID_FILE_DIRS:
        path = library_root / dirname
        if not path.exists():
            continue
        for item in path.iterdir():
            if not item.is_file():
                continue
            asset_id = _asset_id_from_file_stem(item.stem)
            if asset_id:
                asset_ids.add(asset_id)
    return asset_ids


def _asset_id_from_file_stem(stem: str) -> str:
    text = _clean_text(stem)
    match = re.match(r"^(kbpptx_[0-9a-fA-F]{20})", text)
    return match.group(1) if match else ""


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--teach-kb-root", type=Path, default=DEFAULT_TEACH_KB_PPTX_ROOT)
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--db", type=Path, default=None, help="Override teach-kb SQLite DB path.")
    parser.add_argument("--manifest-path", type=Path, default=None)
    parser.add_argument("--debug-path", type=Path, default=None)
    args = parser.parse_args(argv)

    report = backfill_pptx_processed_manifest(
        library_dir=args.library_dir,
        teach_kb_root=args.teach_kb_root,
        db_path=args.db,
        manifest_path=args.manifest_path,
        debug_path=args.debug_path,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
