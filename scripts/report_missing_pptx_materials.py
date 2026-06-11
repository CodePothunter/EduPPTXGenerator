"""Report teach-kb PPTX files that have no reusable material-library assets.

The report compares teach-kb PPTX rows against ``source_pptx_refs`` found in
background/C01/C02/C03 split indexes. Legacy top-level ``file_name`` and theme
matching are retained only as fallbacks for older libraries.
It can also write a shell script that reruns the material-library builder once
per missing PPTX, so one corrupt PPTX does not hide the rest of the queue.
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
KEEP_INDEX_FILENAMES = (
    "background.json",
    "C01_irreplaceable_entity_event_action.json",
    "C02_generic_subject_object.json",
    "C03_scene_decor_container.json",
)


def report_missing_pptx_materials(
    *,
    library_dir: str | Path = DEFAULT_LIBRARY_DIR,
    teach_kb_root: str | Path = DEFAULT_TEACH_KB_PPTX_ROOT,
    db_path: str | Path | None = None,
    report_path: str | Path | None = None,
    write_rerun_script: bool = False,
    rerun_script_path: str | Path | None = None,
) -> dict[str, Any]:
    library_root = Path(library_dir).expanduser().resolve()
    pptx_root = Path(teach_kb_root).expanduser().resolve()
    resolved_db_path = Path(db_path).expanduser().resolve() if db_path else _infer_teach_kb_db_path(pptx_root)
    index_dir = library_root / STRICT_REUSE_INDEX_DIRNAME

    warnings: list[str] = []
    db_rows = _read_db_pptx_rows(resolved_db_path, warnings)
    index_summary = _read_indexed_coverage(index_dir, warnings)
    indexed_pptx_ids = index_summary["indexed_pptx_ids"]
    indexed_file_paths = index_summary["indexed_file_paths"]
    indexed_absolute_paths = index_summary["indexed_absolute_paths"]
    indexed_file_names = index_summary["indexed_file_names"]
    indexed_themes = index_summary["indexed_themes"]
    has_source_refs = bool(indexed_pptx_ids or indexed_file_paths or indexed_absolute_paths)
    match_mode = "source_pptx_refs" if has_source_refs else "file_name" if indexed_file_names else "theme"
    if match_mode == "theme" and indexed_themes:
        warnings.append("file_name_absent_in_indexes: falling back to theme matching")
    missing_pptx = []
    for row in db_rows:
        if match_mode == "source_pptx_refs":
            if _row_matches_source_refs(
                row,
                pptx_root=pptx_root,
                indexed_pptx_ids=indexed_pptx_ids,
                indexed_file_paths=indexed_file_paths,
                indexed_absolute_paths=indexed_absolute_paths,
                indexed_file_names=indexed_file_names,
            ):
                continue
            missing_pptx.append(_missing_entry(row, pptx_root, reason="source_pptx_ref_not_found_in_background_c01_c02_c03"))
        elif match_mode == "file_name":
            if _clean_text(row.get("file_name")) in indexed_file_names:
                continue
            missing_pptx.append(_missing_entry(row, pptx_root, reason="file_name_not_found_in_background_c01_c02_c03"))
        else:
            if _clean_text(row.get("theme")) in indexed_themes:
                continue
            missing_pptx.append(_missing_entry(row, pptx_root, reason="theme_not_found_in_background_c01_c02_c03"))

    report: dict[str, Any] = {
        "library_dir": str(library_root),
        "teach_kb_root": str(pptx_root),
        "db_path": str(resolved_db_path),
        "index_dir": str(index_dir),
        "keep_index_files": list(KEEP_INDEX_FILENAMES),
        "db_pptx_count": len(db_rows),
        "indexed_asset_count": index_summary["indexed_asset_count"],
        "indexed_source_ref_count": index_summary["indexed_source_ref_count"],
        "indexed_pptx_id_count": len(indexed_pptx_ids),
        "indexed_file_path_count": len(indexed_file_paths),
        "indexed_absolute_path_count": len(indexed_absolute_paths),
        "indexed_file_name_count": len(indexed_file_names),
        "indexed_file_names": sorted(indexed_file_names),
        "indexed_theme_count": len(indexed_themes),
        "indexed_themes": sorted(indexed_themes),
        "match_mode": match_mode,
        "missing_pptx_count": len(missing_pptx),
        "missing_pptx": missing_pptx,
        "warnings": warnings,
    }

    output_path = Path(report_path).expanduser().resolve() if report_path else library_root / "missing_pptx_report.json"
    report["report_path"] = str(output_path)

    if write_rerun_script:
        script_path = (
            Path(rerun_script_path).expanduser().resolve()
            if rerun_script_path
            else library_root / "rerun_missing_pptx.sh"
        )
        _write_rerun_script(
            script_path,
            missing_pptx=missing_pptx,
            pptx_root=pptx_root,
            library_root=library_root,
        )
        report["rerun_script_path"] = str(script_path)

    report["warning_count"] = len(warnings)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _read_db_pptx_rows(db_path: Path, warnings: list[str]) -> list[dict[str, Any]]:
    if not db_path.exists():
        warnings.append(f"db_missing:{db_path}")
        return []
    try:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        try:
            try:
                rows = con.execute(
                    """
                    SELECT p.id, p.period_id, p.file_path, p.file_name,
                           h.subject, h.name AS period,
                           l.name AS lesson, u.name AS unit, s.name AS semester, g.name AS grade
                    FROM pptx_files p
                    LEFT JOIN hierarchy h ON h.id = p.period_id
                    LEFT JOIN hierarchy l ON l.id = h.parent_id
                    LEFT JOIN hierarchy u ON u.id = l.parent_id
                    LEFT JOIN hierarchy s ON s.id = u.parent_id
                    LEFT JOIN hierarchy g ON g.id = s.parent_id
                    ORDER BY p.file_name, p.file_path, p.id
                    """
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if "hierarchy" not in str(exc):
                    raise
                warnings.append("hierarchy_table_missing: theme matching disabled for DB rows")
                rows = con.execute(
                    """
                    SELECT id, period_id, file_path, file_name
                    FROM pptx_files
                    ORDER BY file_name, file_path, id
                    """
                ).fetchall()
        finally:
            con.close()
    except Exception as exc:
        warnings.append(f"db_read_failed:{type(exc).__name__}: {exc}")
        return []
    result = []
    for row in rows:
        item = dict(row)
        item["theme"] = _theme_from_db_row(item)
        result.append(item)
    return result


def _read_indexed_coverage(index_dir: Path, warnings: list[str]) -> dict[str, Any]:
    indexed_pptx_ids: set[str] = set()
    indexed_file_paths: set[str] = set()
    indexed_absolute_paths: set[str] = set()
    indexed_file_names: set[str] = set()
    indexed_themes: set[str] = set()
    indexed_asset_count = 0
    indexed_source_ref_count = 0
    for filename in KEEP_INDEX_FILENAMES:
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
            for ref in _source_pptx_refs(asset):
                indexed_source_ref_count += 1
                pptx_id = _clean_text(ref.get("pptx_id") or ref.get("id"))
                if pptx_id:
                    indexed_pptx_ids.add(pptx_id)
                file_path = _coverage_path_key(ref.get("file_path"))
                if file_path:
                    indexed_file_paths.add(file_path)
                absolute_path = _coverage_path_key(ref.get("absolute_path"))
                if absolute_path:
                    indexed_absolute_paths.add(absolute_path)
                ref_file_name = _clean_text(ref.get("file_name"))
                if ref_file_name:
                    indexed_file_names.add(ref_file_name)
            file_name = _clean_text(asset.get("file_name"))
            if file_name:
                indexed_file_names.add(file_name)
            theme = _clean_text(asset.get("theme"))
            if theme:
                indexed_themes.add(theme)
    return {
        "indexed_asset_count": indexed_asset_count,
        "indexed_source_ref_count": indexed_source_ref_count,
        "indexed_pptx_ids": indexed_pptx_ids,
        "indexed_file_paths": indexed_file_paths,
        "indexed_absolute_paths": indexed_absolute_paths,
        "indexed_file_names": indexed_file_names,
        "indexed_themes": indexed_themes,
    }


def _source_pptx_refs(asset: dict[str, Any]) -> list[dict[str, Any]]:
    refs = asset.get("source_pptx_refs")
    if not isinstance(refs, list):
        return []
    return [ref for ref in refs if isinstance(ref, dict)]


def _coverage_path_key(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    return text.replace("\\", "/")


def _row_matches_source_refs(
    row: dict[str, Any],
    *,
    pptx_root: Path,
    indexed_pptx_ids: set[str],
    indexed_file_paths: set[str],
    indexed_absolute_paths: set[str],
    indexed_file_names: set[str],
) -> bool:
    row_id = _clean_text(row.get("id"))
    if row_id and row_id in indexed_pptx_ids:
        return True
    file_path = _coverage_path_key(row.get("file_path"))
    if file_path and file_path in indexed_file_paths:
        return True
    absolute_path = _coverage_path_key(_absolute_pptx_path(
        pptx_root,
        _clean_text(row.get("file_path")),
        _clean_text(row.get("file_name")),
    ))
    if absolute_path and absolute_path in indexed_absolute_paths:
        return True
    file_name = _clean_text(row.get("file_name"))
    return bool(file_name and file_name in indexed_file_names)


def _missing_entry(row: dict[str, Any], pptx_root: Path, *, reason: str) -> dict[str, str]:
    file_path = _clean_text(row.get("file_path"))
    file_name = _clean_text(row.get("file_name"))
    return {
        "id": _clean_text(row.get("id")),
        "period_id": _clean_text(row.get("period_id")),
        "file_path": file_path,
        "file_name": file_name,
        "theme": _clean_text(row.get("theme")),
        "absolute_path": str(_absolute_pptx_path(pptx_root, file_path, file_name)),
        "reason": reason,
    }


def _absolute_pptx_path(pptx_root: Path, file_path: str, file_name: str) -> Path:
    rel = file_path or file_name
    if rel.startswith("pptx/"):
        rel = rel[len("pptx/") :]
    path = Path(rel)
    if path.is_absolute():
        return path.resolve()
    return (pptx_root / path).resolve()


def _write_rerun_script(
    path: Path,
    *,
    missing_pptx: list[dict[str, str]],
    pptx_root: Path,
    library_root: Path,
) -> None:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
    ]
    for item in missing_pptx:
        pptx_path = item["absolute_path"]
        lines.extend(
            [
                "uv run python scripts/build_ppt_materials_library.py \\",
                f"  --teach-kb-root {_sh_quote(str(pptx_root))} \\",
                f"  --library-dir {_sh_quote(str(library_root))} \\",
                f"  --pptx {_sh_quote(pptx_path)} \\",
                "  --flush-every 1",
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    try:
        mode = path.stat().st_mode
        path.chmod(mode | 0o111)
    except OSError:
        pass


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


def _theme_from_db_row(row: dict[str, Any]) -> str:
    parts = [
        _clean_text(row.get("grade")),
        _clean_text(row.get("subject")),
        _clean_text(row.get("lesson")),
        _clean_text(row.get("period")),
    ]
    return _compact_theme_text("".join(part for part in parts if part))


def _compact_theme_text(value: Any) -> str:
    return "".join(_clean_text(value).split())


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _sh_quote(value: str) -> str:
    # The generated file is for the Linux server. Keep paths readable and only
    # escape embedded double quotes for the simple absolute paths this script emits.
    return '"' + value.replace('"', '\\"') + '"'


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--teach-kb-root", type=Path, default=DEFAULT_TEACH_KB_PPTX_ROOT)
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--db", type=Path, default=None, help="Override teach-kb SQLite DB path.")
    parser.add_argument("--report-path", type=Path, default=None, help="Where to write the JSON report.")
    parser.add_argument(
        "--write-rerun-script",
        action="store_true",
        help="Write <library-dir>/rerun_missing_pptx.sh with one builder command per missing PPTX.",
    )
    parser.add_argument("--rerun-script-path", type=Path, default=None)
    args = parser.parse_args(argv)

    report = report_missing_pptx_materials(
        library_dir=args.library_dir,
        teach_kb_root=args.teach_kb_root,
        db_path=args.db,
        report_path=args.report_path,
        write_rerun_script=args.write_rerun_script,
        rerun_script_path=args.rerun_script_path,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
