"""Deduplicate current split-index PPT material libraries.

The script reads only ``materials_library_ppt/strict_reuse_indexes/*.json``.
It does not support the old top-level ``ai_image_match_index.json`` layout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edupptx.materials.ppt_dedupe import dedupe_ppt_split_index_library


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", type=Path, default=Path("materials_library_ppt"))
    parser.add_argument("--report-path", type=Path, default=None)
    parser.add_argument("--apply", action="store_true", help="Apply bucketed split-index dedupe")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    report = dedupe_ppt_split_index_library(
        args.library_dir,
        apply=args.apply,
        report_path=args.report_path,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "mode": report["mode"],
                "library_dir": report["library_dir"],
                "asset_count": report["asset_count"],
                "buckets": report["buckets"],
                "mergeable_group_count": report["mergeable_group_count"],
                "applied_removed_count": report["applied_removed_count"],
                "report_path": report["report_path"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
