"""Extract PPT original images that are not referenced by reusable indexes.

The keep set is built from background/C01/C02/C03 split indexes. Unindexed
runtime images under ``pptx_images`` are deleted, while unindexed originals
under ``pptx_images_original`` are moved to a new rerun library containing only
``pptx_images_original``.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_LIBRARY_DIR = Path("materials_library_ppt")
DEFAULT_OUTPUT_DIR = Path("materials_library_ppt_c00_rerun")
STRICT_REUSE_INDEX_DIRNAME = "strict_reuse_indexes"
RUNTIME_IMAGE_DIRNAME = "pptx_images"
ORIGINAL_IMAGE_DIRNAME = "pptx_images_original"
DEFAULT_REPORT_FILENAME = "rerun_manifest.json"

KEEP_INDEX_FILENAMES = (
    "background.json",
    "C01_irreplaceable_entity_event_action.json",
    "C02_generic_subject_object.json",
    "C03_scene_decor_container.json",
)


def extract_unindexed_pptx_originals(
    *,
    library_dir: str | Path = DEFAULT_LIBRARY_DIR,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    report_path: str | Path | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    library_root = Path(library_dir).expanduser().resolve()
    output_root = Path(output_dir).expanduser().resolve()
    index_dir = library_root / STRICT_REUSE_INDEX_DIRNAME
    runtime_dir = library_root / RUNTIME_IMAGE_DIRNAME
    original_dir = library_root / ORIGINAL_IMAGE_DIRNAME
    output_original_dir = output_root / ORIGINAL_IMAGE_DIRNAME
    resolved_report_path = (
        Path(report_path).expanduser().resolve()
        if report_path
        else output_root / DEFAULT_REPORT_FILENAME
    )

    _validate_roots(
        library_root=library_root,
        output_root=output_root,
        runtime_dir=runtime_dir,
        original_dir=original_dir,
        index_dir=index_dir,
    )
    kept_paths, kept_asset_ids, index_report = _read_keep_indexes(library_root, index_dir)

    original_files = _iter_files(original_dir)
    runtime_files = _iter_files(runtime_dir)
    unindexed_originals = [
        path for path in original_files
        if _is_unindexed(library_root, path, kept_paths, kept_asset_ids)
    ]
    unindexed_runtime = [
        path for path in runtime_files
        if _is_unindexed(library_root, path, kept_paths, kept_asset_ids)
    ]

    destination_by_source: dict[Path, Path] = {}
    warnings: list[str] = [*index_report["warnings"]]
    for path in unindexed_originals:
        destination = output_original_dir / path.name
        if destination.exists() and path.resolve() != destination.resolve():
            warnings.append(f"destination_exists:{_display_path(destination)}")
        destination_by_source[path] = destination

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "applied": bool(apply),
        "library_dir": str(library_root),
        "output_dir": str(output_root),
        "index_dir": str(index_dir),
        "keep_index_files": list(KEEP_INDEX_FILENAMES),
        "kept_reference_count": len(kept_paths),
        "kept_asset_id_count": len(kept_asset_ids),
        "runtime_image_count": len(runtime_files),
        "original_image_count": len(original_files),
        "unindexed_runtime_count": len(unindexed_runtime),
        "unindexed_original_count": len(unindexed_originals),
        "deleted_runtime_count": 0,
        "moved_original_count": 0,
        "missing_index_count": index_report["missing_index_count"],
        "deleted_runtime": [],
        "moved_original": [],
        "warnings": warnings,
    }

    if any(str(warning).startswith("destination_exists:") for warning in warnings):
        report["warning_count"] = len(warnings)
        _write_report(resolved_report_path, report)
        if apply:
            raise FileExistsError("one or more destination files already exist; see report warnings")
        return report

    if apply:
        output_original_dir.mkdir(parents=True, exist_ok=True)

    for image_path in unindexed_runtime:
        rel = _relative_library_key(library_root, image_path)
        if apply:
            _assert_inside(library_root, image_path)
            image_path.unlink()
        report["deleted_runtime_count"] += 1
        report["deleted_runtime"].append(rel)

    for image_path in unindexed_originals:
        rel = _relative_library_key(library_root, image_path)
        dest = destination_by_source[image_path]
        if apply:
            _assert_inside(library_root, image_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            image_path.replace(dest)
        report["moved_original_count"] += 1
        report["moved_original"].append(
            {
                "asset_id": image_path.stem,
                "source": rel,
                "destination": dest.relative_to(output_root).as_posix(),
            }
        )

    report["warning_count"] = len(warnings)
    _write_report(resolved_report_path, report)
    return report


def _validate_roots(
    *,
    library_root: Path,
    output_root: Path,
    runtime_dir: Path,
    original_dir: Path,
    index_dir: Path,
) -> None:
    if not library_root.exists():
        raise FileNotFoundError(f"library directory not found: {library_root}")
    if output_root == library_root:
        raise ValueError("output_dir must not be the same as library_dir")
    forbidden = {runtime_dir.resolve(), original_dir.resolve(), index_dir.resolve()}
    if output_root in forbidden:
        raise ValueError("output_dir must not be an existing library subdirectory")


def _read_keep_indexes(library_root: Path, index_dir: Path) -> tuple[set[str], set[str], dict[str, Any]]:
    kept_paths: set[str] = set()
    kept_asset_ids: set[str] = set()
    report = {"missing_index_count": 0, "warnings": []}
    if not index_dir.exists():
        raise FileNotFoundError(f"strict reuse index directory not found: {index_dir}")

    for filename in KEEP_INDEX_FILENAMES:
        index_path = index_dir / filename
        if not index_path.exists():
            report["missing_index_count"] += 1
            report["warnings"].append(f"missing_index:{filename}")
            continue
        payload = _read_json_object(index_path)
        assets = payload.get("assets")
        if not isinstance(assets, list):
            report["warnings"].append(f"index_assets_not_list:{filename}")
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            asset_id = _clean_text(asset.get("asset_id"))
            if asset_id:
                kept_asset_ids.add(asset_id)
            for field in ("image_path", "original_image_path"):
                key = _library_key_from_value(library_root, asset.get(field))
                if key:
                    kept_paths.add(key)
                    kept_asset_ids.add(Path(key).stem)

    if report["missing_index_count"]:
        missing = ", ".join(w for w in report["warnings"] if str(w).startswith("missing_index:"))
        raise FileNotFoundError(f"required keep indexes are missing: {missing}")
    return kept_paths, kept_asset_ids, report


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _is_unindexed(library_root: Path, image_path: Path, kept_paths: set[str], kept_asset_ids: set[str]) -> bool:
    rel = _relative_library_key(library_root, image_path)
    return rel not in kept_paths and image_path.stem not in kept_asset_ids


def _iter_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(path for path in directory.iterdir() if path.is_file())


def _library_key_from_value(library_root: Path, value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(library_root).as_posix()
    except ValueError:
        return ""


def _relative_library_key(library_root: Path, path: Path) -> str:
    return path.resolve().relative_to(library_root).as_posix()


def _assert_inside(root: Path, path: Path) -> None:
    path.resolve().relative_to(root)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    report["report_path"] = str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-path", type=Path, default=None)
    parser.add_argument("--apply", action="store_true", help="Move/delete files. Without this, only write a report.")
    args = parser.parse_args(argv)

    report = extract_unindexed_pptx_originals(
        library_dir=args.library_dir,
        output_dir=args.output_dir,
        report_path=args.report_path,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
