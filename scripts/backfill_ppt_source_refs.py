"""Backfill source_pptx_refs into existing PPT material split indexes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_LIBRARY_DIR = Path("materials_library_ppt")
DEFAULT_MANIFEST_FILENAME = "processed_pptx_manifest.backfilled.jsonl"
STRICT_REUSE_INDEX_DIRNAME = "strict_reuse_indexes"
INDEX_FILENAMES = (
    "background.json",
    "C00_strict_text_problem_skip.json",
    "C01_irreplaceable_entity_event_action.json",
    "C02_generic_subject_object.json",
    "C03_scene_decor_container.json",
)


def backfill_ppt_source_refs(
    *,
    library_dir: str | Path = DEFAULT_LIBRARY_DIR,
    manifest_path: str | Path | None = None,
    index_dir: str | Path | None = None,
) -> dict[str, Any]:
    library_root = Path(library_dir).expanduser().resolve()
    manifest = (
        Path(manifest_path).expanduser().resolve()
        if manifest_path
        else library_root / DEFAULT_MANIFEST_FILENAME
    )
    split_dir = Path(index_dir).expanduser().resolve() if index_dir else library_root / STRICT_REUSE_INDEX_DIRNAME
    refs_by_asset_id = _refs_by_asset_id_from_manifest(manifest)

    warnings: list[str] = []
    touched_files: list[str] = []
    updated_asset_count = 0
    source_ref_count = 0
    for filename in INDEX_FILENAMES:
        path = split_dir / filename
        if not path.exists():
            warnings.append(f"missing_index:{filename}")
            continue
        payload = _read_json_object(path)
        assets = payload.get("assets")
        if not isinstance(assets, list):
            warnings.append(f"index_assets_not_list:{filename}")
            continue
        file_changed = False
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            refs = refs_by_asset_id.get(_clean_text(asset.get("asset_id")))
            if not refs:
                continue
            before = len(asset.get("source_pptx_refs") if isinstance(asset.get("source_pptx_refs"), list) else [])
            asset["source_pptx_refs"] = _merge_source_refs(asset.get("source_pptx_refs"), refs)
            after = len(asset["source_pptx_refs"])
            if after > before:
                file_changed = True
                updated_asset_count += 1
                source_ref_count += after - before
        if file_changed:
            _write_json_atomic(path, payload)
            touched_files.append(str(path))

    return {
        "library_dir": str(library_root),
        "manifest_path": str(manifest),
        "index_dir": str(split_dir),
        "asset_id_with_ref_count": len(refs_by_asset_id),
        "updated_asset_count": updated_asset_count,
        "source_ref_count": source_ref_count,
        "touched_files": touched_files,
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def _refs_by_asset_id_from_manifest(path: Path) -> dict[str, list[dict[str, Any]]]:
    refs_by_asset_id: dict[str, list[dict[str, Any]]] = {}
    if not path.exists():
        return refs_by_asset_id
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            continue
        ref = {
            "pptx_id": _clean_text(row.get("pptx_id")),
            "period_id": _clean_text(row.get("period_id")),
            "file_path": _clean_text(row.get("file_path")),
            "file_name": _clean_text(row.get("file_name")),
            "absolute_path": _clean_text(row.get("absolute_path")),
            "source": "backfilled_by_asset_hash",
        }
        ref = {key: value for key, value in ref.items() if value}
        if not any(ref.get(key) for key in ("pptx_id", "file_path", "file_name", "absolute_path")):
            continue
        for asset_id in _asset_ids_from_manifest_row(row):
            refs_by_asset_id.setdefault(asset_id, []).append(ref)
    return {asset_id: _merge_source_refs([], refs) for asset_id, refs in refs_by_asset_id.items()}


def _asset_ids_from_manifest_row(row: dict[str, Any]) -> list[str]:
    raw = row.get("candidate_asset_ids")
    if not isinstance(raw, list):
        raw = row.get("matched_asset_ids")
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in raw:
        asset_id = _clean_text(value)
        if not asset_id or asset_id in seen:
            continue
        seen.add(asset_id)
        result.append(asset_id)
    return result


def _merge_source_refs(existing: Any, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs = [item for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
    seen = {_source_ref_key(item) for item in refs}
    for ref in incoming:
        key = _source_ref_key(ref)
        if key in seen:
            continue
        seen.add(key)
        refs.append(dict(ref))
    return refs


def _source_ref_key(ref: dict[str, Any]) -> tuple[str, ...]:
    return (
        _clean_text(ref.get("pptx_id")),
        _clean_text(ref.get("period_id")),
        _clean_text(ref.get("file_path")),
        _clean_text(ref.get("file_name")),
        _clean_text(ref.get("absolute_path")),
        _clean_text(ref.get("source")),
        _clean_text(ref.get("slide_no")),
        _clean_text(ref.get("shape_idx")),
        _clean_text(ref.get("source_media_path")),
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--manifest-path", type=Path, default=None)
    parser.add_argument("--index-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    report = backfill_ppt_source_refs(
        library_dir=args.library_dir,
        manifest_path=args.manifest_path,
        index_dir=args.index_dir,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
