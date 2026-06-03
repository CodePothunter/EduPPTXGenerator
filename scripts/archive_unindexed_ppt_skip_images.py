"""Archive PPT images that are not referenced by reusable split indexes.

Runtime images under ``pptx_images`` are deleted. Original images under
``pptx_images_original`` are moved into ``skip_image``. The keep set is built
from background/C01/C02/C03 split indexes only; C00 is intentionally ignored.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_LIBRARY_DIR = Path("materials_library_ppt")
DEFAULT_TEACH_KB_PPTX_ROOT = Path("data/uploads/pptx")
STRICT_REUSE_INDEX_DIRNAME = "strict_reuse_indexes"
RUNTIME_IMAGE_DIRNAME = "pptx_images"
ORIGINAL_IMAGE_DIRNAME = "pptx_images_original"
SKIP_IMAGE_DIRNAME = "skip_image"

KEEP_INDEX_FILENAMES = (
    "background.json",
    "C01_irreplaceable_entity_event_action.json",
    "C02_generic_subject_object.json",
    "C03_scene_decor_container.json",
)


def archive_unindexed_ppt_skip_images(
    *,
    library_dir: str | Path = DEFAULT_LIBRARY_DIR,
    teach_kb_root: str | Path = DEFAULT_TEACH_KB_PPTX_ROOT,
    db_path: str | Path | None = None,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    library_root = Path(library_dir).expanduser().resolve()
    index_dir = library_root / STRICT_REUSE_INDEX_DIRNAME
    runtime_dir = library_root / RUNTIME_IMAGE_DIRNAME
    original_dir = library_root / ORIGINAL_IMAGE_DIRNAME
    skip_dir = library_root / SKIP_IMAGE_DIRNAME
    resolved_db_path = Path(db_path).expanduser().resolve() if db_path else _infer_teach_kb_db_path(teach_kb_root)

    kept_paths, extracted_themes, index_report = _read_keep_indexes(library_root, index_dir)
    report: dict[str, Any] = {
        "library_dir": str(library_root),
        "index_dir": str(index_dir),
        "keep_index_files": list(KEEP_INDEX_FILENAMES),
        "kept_reference_count": len(kept_paths),
        "runtime_image_count": 0,
        "original_image_count": 0,
        "deleted_runtime_count": 0,
        "moved_original_count": 0,
        "missing_index_count": index_report["missing_index_count"],
        "warnings": [*index_report["warnings"]],
        "deleted_runtime": [],
        "moved_original": [],
        "extracted_theme_count": len(extracted_themes),
        "extracted_themes": sorted(extracted_themes),
        "db_path": str(resolved_db_path),
    }

    if runtime_dir.exists():
        for image_path in sorted(path for path in runtime_dir.iterdir() if path.is_file()):
            report["runtime_image_count"] += 1
            rel = _relative_library_key(library_root, image_path)
            if rel in kept_paths:
                continue
            try:
                _assert_inside(library_root, image_path)
                image_path.unlink()
                report["deleted_runtime_count"] += 1
                report["deleted_runtime"].append(rel)
            except Exception as exc:
                report["warnings"].append(f"runtime_delete_failed:{rel}:{type(exc).__name__}: {exc}")

    if original_dir.exists():
        for image_path in sorted(path for path in original_dir.iterdir() if path.is_file()):
            report["original_image_count"] += 1
            rel = _relative_library_key(library_root, image_path)
            if rel in kept_paths:
                continue
            dest = _unique_destination(skip_dir / image_path.name)
            try:
                _assert_inside(library_root, image_path)
                _assert_inside(library_root, dest.parent)
                dest.parent.mkdir(parents=True, exist_ok=True)
                image_path.replace(dest)
                report["moved_original_count"] += 1
                report["moved_original"].append(
                    {
                        "source": rel,
                        "destination": _relative_library_key(library_root, dest),
                    }
                )
            except Exception as exc:
                report["warnings"].append(f"original_move_failed:{rel}:{type(exc).__name__}: {exc}")

    db_count = _count_db_pptx(resolved_db_path, report["warnings"])
    report["db_pptx_count"] = db_count
    report["missing_ppt_count"] = max(0, db_count - len(extracted_themes)) if db_count is not None else None
    report["warning_count"] = len(report["warnings"])

    output_path = Path(report_path).expanduser().resolve() if report_path else library_root / "skip_image_report.json"
    report["report_path"] = str(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _read_keep_indexes(library_root: Path, index_dir: Path) -> tuple[set[str], set[str], dict[str, Any]]:
    kept_paths: set[str] = set()
    extracted_themes: set[str] = set()
    report = {"missing_index_count": 0, "warnings": []}

    for filename in KEEP_INDEX_FILENAMES:
        index_path = index_dir / filename
        if not index_path.exists():
            report["missing_index_count"] += 1
            report["warnings"].append(f"missing_index:{filename}")
            continue
        try:
            payload = _read_json_object(index_path)
        except Exception as exc:
            report["warnings"].append(f"index_read_failed:{filename}:{type(exc).__name__}: {exc}")
            continue
        assets = payload.get("assets")
        if not isinstance(assets, list):
            report["warnings"].append(f"index_assets_not_list:{filename}")
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            for field in ("image_path", "original_image_path"):
                key = _library_key_from_value(library_root, asset.get(field))
                if key:
                    kept_paths.add(key)
            theme = _clean_text(asset.get("theme"))
            if theme:
                extracted_themes.add(theme)

    return kept_paths, extracted_themes, report


def _infer_teach_kb_db_path(teach_kb_root: str | Path) -> Path:
    pptx_root = Path(teach_kb_root).expanduser().resolve()
    if (
        pptx_root.name == "pptx"
        and pptx_root.parent.name == "uploads"
        and pptx_root.parent.parent.name == "data"
    ):
        teach_root = pptx_root.parent.parent.parent
    else:
        teach_root = pptx_root
    return teach_root / "data" / "db" / "teach_kb.db"


def _count_db_pptx(db_path: Path, warnings: list[Any]) -> int | None:
    if not db_path.exists():
        warnings.append(f"db_missing:{db_path}")
        return None
    try:
        con = sqlite3.connect(str(db_path))
        try:
            value = con.execute("SELECT COUNT(*) FROM pptx_files").fetchone()
        finally:
            con.close()
        return int(value[0]) if value is not None else 0
    except Exception as exc:
        warnings.append(f"db_count_failed:{type(exc).__name__}: {exc}")
        return None


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


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


def _assert_inside(library_root: Path, path: Path) -> None:
    path.resolve().relative_to(library_root)


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--teach-kb-root", type=Path, default=DEFAULT_TEACH_KB_PPTX_ROOT)
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--db", type=Path, default=None, help="Override teach-kb SQLite DB path.")
    parser.add_argument("--report-path", type=Path, default=None, help="Where to write the JSON report.")
    args = parser.parse_args(argv)

    report = archive_unindexed_ppt_skip_images(
        library_dir=args.library_dir,
        teach_kb_root=args.teach_kb_root,
        db_path=args.db,
        report_path=args.report_path,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
