"""Write rerun shell commands from PPTX backfill debug.json."""

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
DEFAULT_TEACH_KB_PPTX_ROOT = Path("/srv/teach-kb/data/uploads/pptx")
DEFAULT_STATUSES = ("unconfirmed", "partial_asset_hash_match", "extract_failed", "pptx_missing")
DEFAULT_SCRIPT_FILENAME = "rerun_debug_pptx.sh"
DEFAULT_PATHS_FILENAME = "rerun_debug_pptx_paths.txt"
DEFAULT_FAILED_FILENAME = "rerun_debug_pptx_failed.txt"


def write_rerun_from_pptx_debug(
    *,
    debug_json: str | Path = DEFAULT_LIBRARY_DIR / "debug.json",
    library_dir: str | Path = DEFAULT_LIBRARY_DIR,
    teach_kb_root: str | Path = DEFAULT_TEACH_KB_PPTX_ROOT,
    output_script: str | Path | None = None,
    output_paths: str | Path | None = None,
    statuses: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    library_root = Path(library_dir).expanduser().resolve()
    debug_path = Path(debug_json).expanduser().resolve()
    script_path = (
        Path(output_script).expanduser().resolve()
        if output_script
        else library_root / DEFAULT_SCRIPT_FILENAME
    )
    paths_path = (
        Path(output_paths).expanduser().resolve()
        if output_paths
        else library_root / DEFAULT_PATHS_FILENAME
    )
    selected_statuses = tuple(statuses or DEFAULT_STATUSES)
    selected_status_set = set(selected_statuses)

    payload = _read_json_object(debug_path)
    rows = payload.get("uncertain_pptx")
    if not isinstance(rows, list):
        rows = []

    selected: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    status_counts: dict[str, int] = {}
    skipped_missing_path = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = _clean_text(row.get("status"))
        if status not in selected_status_set:
            continue
        pptx_path = _clean_text(row.get("absolute_path"))
        if not pptx_path:
            skipped_missing_path += 1
            continue
        if pptx_path in seen_paths:
            continue
        seen_paths.add(pptx_path)
        selected.append(row)
        status_counts[status] = status_counts.get(status, 0) + 1

    paths = [_clean_text(row.get("absolute_path")) for row in selected]
    _write_paths(paths_path, paths)
    _write_script(
        script_path,
        paths=paths,
        teach_kb_root=Path(teach_kb_root).expanduser(),
        library_root=library_root,
    )
    report = {
        "debug_json": str(debug_path),
        "library_dir": str(library_root),
        "teach_kb_root": str(Path(teach_kb_root).expanduser()),
        "output_script": str(script_path),
        "output_paths": str(paths_path),
        "selected_statuses": list(selected_statuses),
        "selected_count": len(paths),
        "status_counts": dict(sorted(status_counts.items())),
        "skipped_missing_path_count": skipped_missing_path,
    }
    return report


def _write_paths(path: Path, paths: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{item}\n" for item in paths), encoding="utf-8")


def _write_script(path: Path, *, paths: list[str], teach_kb_root: Path, library_root: Path) -> None:
    failed_path = library_root / DEFAULT_FAILED_FILENAME
    lines = [
        "#!/usr/bin/env bash",
        "set -uo pipefail",
        f"FAILED_LOG={_sh_quote(str(failed_path))}",
        ": > \"$FAILED_LOG\"",
        "",
    ]
    for pptx_path in paths:
        lines.extend(
            [
                f"echo {_sh_quote('RUN: ' + pptx_path)}",
                "if ! uv run python scripts/build_ppt_materials_library.py \\",
                f"  --teach-kb-root {_sh_quote(str(teach_kb_root))} \\",
                f"  --library-dir {_sh_quote(str(library_root))} \\",
                f"  --pptx {_sh_quote(pptx_path)} \\",
                "  --flush-every 1; then",
                f"  echo {_sh_quote('FAILED: ' + pptx_path)} | tee -a \"$FAILED_LOG\"",
                "fi",
                "",
            ]
        )
    lines.extend(
        [
            "if [ -s \"$FAILED_LOG\" ]; then",
            "  echo \"Some PPTX failed. See: $FAILED_LOG\"",
            "else",
            "  echo \"All PPTX rerun commands completed.\"",
            "fi",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    try:
        path.chmod(path.stat().st_mode | 0o111)
    except OSError:
        pass


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _sh_quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--debug-json", type=Path, default=DEFAULT_LIBRARY_DIR / "debug.json")
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--teach-kb-root", type=Path, default=DEFAULT_TEACH_KB_PPTX_ROOT)
    parser.add_argument("--output-script", type=Path, default=None)
    parser.add_argument("--output-paths", type=Path, default=None)
    parser.add_argument(
        "--status",
        action="append",
        default=[],
        help=(
            "Uncertain status to include; can be repeated. "
            "Default: all uncertain statuses."
        ),
    )
    args = parser.parse_args(argv)

    report = write_rerun_from_pptx_debug(
        debug_json=args.debug_json,
        library_dir=args.library_dir,
        teach_kb_root=args.teach_kb_root,
        output_script=args.output_script,
        output_paths=args.output_paths,
        statuses=args.status or None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
