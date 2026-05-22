"""Run the LLM strict_reuse_group classification on existing page_image assets.

Reads materials_library/strict_reuse_indexes/, deep-copies the page_image
assets, re-runs the LLM keyword/classification pass on the copy, and writes a
diff report to report/llm_classify_dryrun_<timestamp>/.

By default this is a dry-run and the on-disk material library is not modified.
Pass --apply to update the split material-library indexes with the reclassified
assets.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edupptx.config import Config
from edupptx.llm_client import create_llm_client
from edupptx.materials.ai_image_asset_db import (
    enrich_ai_image_asset_db_keywords,
    read_ai_image_split_match_index,
    write_ai_image_match_index,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", default="materials_library")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--keyword-batch-size", type=int, default=8)
    parser.add_argument(
        "--report-dir",
        default=None,
        help="Output directory. Defaults to report/llm_classify_dryrun_<timestamp>/.",
    )
    parser.add_argument(
        "--asset-ids",
        nargs="*",
        default=None,
        help="Optional asset_id allow-list; if omitted, all page_image assets are tested.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the reclassified assets back into the material library split indexes.",
    )
    parser.add_argument(
        "--skip-embedding-rebuild",
        action="store_true",
        help="When --apply is used, skip rebuilding ai_image_embedding_index.npz sidecars.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    library_dir = Path(args.library_dir).expanduser().resolve()
    split = read_ai_image_split_match_index(library_dir)
    if split is None:
        raise FileNotFoundError(f"Split indexes not found under: {library_dir}")
    db, split_dir = split

    allow_ids = set(args.asset_ids or ()) or None
    page_assets = [
        deepcopy(asset)
        for asset in db.get("assets", [])
        if isinstance(asset, dict)
        and asset.get("asset_kind") == "page_image"
        and (allow_ids is None or asset.get("asset_id") in allow_ids)
    ]
    if not page_assets:
        print(f"No page_image assets to test under {split_dir}")
        return 0

    originals_by_id = {asset["asset_id"]: deepcopy(asset) for asset in page_assets}
    original_assets_by_id = {
        asset.get("asset_id"): deepcopy(asset)
        for asset in db.get("assets", [])
        if isinstance(asset, dict) and asset.get("asset_id")
    }

    config = Config.from_env(args.env_file)
    if not config.llm_api_key or not config.llm_model:
        raise RuntimeError("GEN_APIKEY/GEN_MODEL not configured")
    client = create_llm_client(config, web_search=False)

    subset_db = {
        "schema_version": int(db.get("schema_version") or 1),
        "assets": page_assets,
        "warnings": [],
    }
    enrich_ai_image_asset_db_keywords(
        subset_db,
        client,
        batch_size=max(1, args.keyword_batch_size),
        include_match_keywords=False,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path(args.report_dir) if args.report_dir else REPO_ROOT / "report" / f"llm_classify_dryrun_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=True)

    diff_rows: list[dict] = []
    changed = 0
    metadata_changed = 0
    direction_counts = {"general_to_content": 0, "content_to_general": 0, "unchanged": 0}
    for asset in page_assets:
        asset_id = asset.get("asset_id")
        before = originals_by_id.get(asset_id, {})
        before_group = before.get("strict_reuse_group")
        after_group = asset.get("strict_reuse_group")
        asset_metadata_changed = before != asset
        row = {
            "asset_id": asset_id,
            "content_prompt": before.get("content_prompt"),
            "asset_category": before.get("asset_category"),
            "before_group": before_group,
            "after_group": after_group,
            "before_reason": before.get("strict_reuse_reason"),
            "after_reason": asset.get("strict_reuse_reason"),
            "before_confidence": before.get("strict_reuse_confidence"),
            "after_confidence": asset.get("strict_reuse_confidence"),
            "metadata_changed": asset_metadata_changed,
        }
        if asset_metadata_changed:
            metadata_changed += 1
        if before_group != after_group:
            changed += 1
            if before_group == "general_reuse" and after_group == "content_reuse":
                direction_counts["general_to_content"] += 1
            elif before_group == "content_reuse" and after_group == "general_reuse":
                direction_counts["content_to_general"] += 1
        else:
            direction_counts["unchanged"] += 1
        diff_rows.append(row)

    diff_path = report_dir / "diff.jsonl"
    diff_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in diff_rows) + "\n",
        encoding="utf-8",
    )

    full_after_path = report_dir / "would_be_assets.json"
    full_after_path.write_text(json.dumps(page_assets, ensure_ascii=False, indent=2), encoding="utf-8")

    before_path = report_dir / "before_assets.json"
    before_path.write_text(
        json.dumps(
            [original_assets_by_id[asset_id] for asset_id in originals_by_id if asset_id in original_assets_by_id],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (report_dir / "before_index_snapshot.json").write_text(
        json.dumps(db, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    applied_index_path = None
    embedding_report = None
    if args.apply:
        updated_by_id = {asset.get("asset_id"): deepcopy(asset) for asset in page_assets if asset.get("asset_id")}
        merged_assets: list[dict] = []
        for asset in db.get("assets", []):
            if not isinstance(asset, dict):
                continue
            asset_id = asset.get("asset_id")
            merged_assets.append(deepcopy(updated_by_id.get(asset_id, asset)))

        updated_db = deepcopy(db)
        updated_db["assets"] = merged_assets
        updated_db["asset_count"] = len(merged_assets)
        updated_db["input_asset_count"] = len(merged_assets)
        existing_warnings = db.get("warnings") if isinstance(db.get("warnings"), list) else []
        new_warnings = subset_db.get("warnings") if isinstance(subset_db.get("warnings"), list) else []
        updated_db["warnings"] = list(dict.fromkeys([*existing_warnings, *new_warnings]))
        if allow_ids is None:
            for key in ("keyword_builder", "keyword_built_at"):
                if key in subset_db:
                    updated_db[key] = deepcopy(subset_db[key])
        else:
            updated_db["warnings"].append(
                f"partial LLM classify update applied to {len(page_assets)} page_image assets"
            )

        applied_index, applied_index_path = write_ai_image_match_index(
            updated_db,
            library_dir,
            write_embedding_index=not args.skip_embedding_rebuild,
        )
        embedding_report = applied_index.get("embedding_index")
        (report_dir / "applied_index_snapshot.json").write_text(
            json.dumps(applied_index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    summary_lines = [
        f"# LLM strict_reuse_group {'apply' if args.apply else 'dry-run'} @ {timestamp}",
        "",
        f"- Library: `{library_dir}`",
        f"- Model: `{config.llm_model}`",
        f"- Assets tested: {len(page_assets)}",
        f"- Batch size: {args.keyword_batch_size}",
        f"- Applied to library: {'yes' if args.apply else 'no'}",
        f"- Group changed: {changed} (general→content {direction_counts['general_to_content']}, content→general {direction_counts['content_to_general']})",
        f"- Metadata changed: {metadata_changed}",
        f"- Unchanged: {direction_counts['unchanged']}",
    ]
    if applied_index_path is not None:
        summary_lines.append(f"- Updated split indexes: `{applied_index_path}`")
    if embedding_report:
        summary_lines.append(f"- Embedding rebuild: `{json.dumps(embedding_report, ensure_ascii=False)}`")
    elif args.apply and args.skip_embedding_rebuild:
        summary_lines.append("- Embedding rebuild: skipped")
    summary_lines.extend(
        [
            "",
            "## Changed assets",
            "",
            "| asset_id | before | after | content_prompt |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in diff_rows:
        if row["before_group"] != row["after_group"]:
            summary_lines.append(
                f"| `{row['asset_id']}` | {row['before_group']} | {row['after_group']} | {row['content_prompt']} |"
            )
    if changed == 0:
        summary_lines.append("| _(none)_ | | | |")
    summary_lines.append("")
    if subset_db.get("warnings"):
        summary_lines.append("## Warnings")
        summary_lines.append("")
        for w in subset_db["warnings"]:
            summary_lines.append(f"- {w}")
        summary_lines.append("")
    (report_dir / "summary.md").write_text("\n".join(summary_lines), encoding="utf-8")

    if args.apply:
        print(f"Apply complete. Library updated.")
        print(f"  Updated split indexes: {applied_index_path}")
        if args.skip_embedding_rebuild:
            print(f"  Embedding rebuild: skipped")
        elif embedding_report:
            print(f"  Embedding rebuild: {embedding_report}")
    else:
        print(f"Dry-run complete. Library untouched.")
    print(f"  Tested: {len(page_assets)} page_image assets")
    print(f"  Group changed: {changed} (general→content {direction_counts['general_to_content']}, content→general {direction_counts['content_to_general']})")
    print(f"  Metadata changed: {metadata_changed}")
    print(f"  Report: {report_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
