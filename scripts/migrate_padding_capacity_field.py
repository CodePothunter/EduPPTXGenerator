"""One-shot migration: lift ``transform_advice.padding_capacity`` to top-level.

The library used to store ``{"transform_advice": {"padding_capacity": "high"}}``.
The canonical shape is now a top-level scalar: ``{"padding_capacity": "high"}``.
This script walks one or more JSON library files (asset DB and / or match
index) and rewrites each ``page_image`` asset in place.

Behavior per asset:
  * if ``padding_capacity`` (top-level) is present → leave as-is.
  * else if ``transform_advice`` is a dict with a ``padding_capacity`` key →
    lift the value, drop the wrapper.
  * else → leave as-is (no value to migrate).

A backup of each file is written next to it as ``<filename>.pre-migrate.bak``
before rewriting. The script is idempotent — re-running on a migrated file is
a no-op.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any


_VALID_CAPACITIES = {"high", "mid", "low"}


def _normalize_capacity(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip().casefold()
    if text in _VALID_CAPACITIES:
        return text
    if text in {"medium", "中", "中等"}:
        return "mid"
    if text in {"高"}:
        return "high"
    if text in {"低", "none", "no", "avoid"}:
        return "low"
    return ""


def migrate_asset(asset: dict[str, Any]) -> str:
    """Return the migration action taken: 'noop' / 'lifted' / 'dropped'."""

    if not isinstance(asset, dict):
        return "noop"
    if asset.get("padding_capacity"):
        # Already migrated. If transform_advice still lingers, drop it.
        if "transform_advice" in asset:
            del asset["transform_advice"]
            return "dropped"
        return "noop"

    advice = asset.get("transform_advice")
    if isinstance(advice, dict):
        capacity = _normalize_capacity(advice.get("padding_capacity"))
        if capacity:
            asset["padding_capacity"] = capacity
            del asset["transform_advice"]
            return "lifted"
        # transform_advice dict carried no usable padding_capacity → drop wrapper.
        del asset["transform_advice"]
        return "dropped"
    return "noop"


def migrate_file(path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "status": "missing"}
    data = json.loads(path.read_text(encoding="utf-8"))
    assets = data.get("assets")
    if not isinstance(assets, list):
        return {"path": str(path), "status": "no_assets_field"}

    actions: Counter[str] = Counter()
    distribution: Counter[str] = Counter()
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        action = migrate_asset(asset)
        actions[action] += 1
        if asset.get("asset_kind") == "page_image":
            distribution[asset.get("padding_capacity") or "absent"] += 1

    result = {
        "path": str(path),
        "status": "dry_run" if dry_run else "written",
        "lifted": actions["lifted"],
        "dropped_wrappers": actions["dropped"],
        "no_op": actions["noop"],
        "page_image_distribution": dict(distribution),
    }
    if dry_run:
        return result

    backup = path.with_suffix(path.suffix + ".pre-migrate.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
        result["backup"] = str(backup)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[
            Path("materials_library_ppt/ai_image_match_index.json"),
            Path("materials_library/ai_image_match_index.json"),
        ],
        help="JSON library files to migrate (default: the two known match indexes).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    args = parser.parse_args(argv)

    for path in args.paths:
        report = migrate_file(path, dry_run=args.dry_run)
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
