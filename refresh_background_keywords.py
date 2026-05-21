"""Refresh background metadata in an existing AI image library.

One-off maintenance script for rerunning only background keyword metadata
after prompt/rule changes. It does not generate images and does not change the
main CLI surface.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edupptx.config import Config
from edupptx.llm_client import create_llm_client
from edupptx.materials.ai_image_asset_db import (
    DEFAULT_DB_FILENAME,
    DEFAULT_MATCH_INDEX_FILENAME,
    enrich_ai_image_asset_db_keywords,
    write_ai_image_match_index,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh only background LLM metadata in an existing asset library."
    )
    parser.add_argument(
        "--library-dir",
        default="materials_library",
        help="Directory containing ai_image_asset_db.json.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Environment file containing GEN_APIKEY/GEN_MODEL.",
    )
    parser.add_argument(
        "--keyword-batch-size",
        type=int,
        default=3,
        help="Number of background assets per LLM batch.",
    )
    parser.add_argument(
        "--db-filename",
        default=DEFAULT_DB_FILENAME,
        help="Asset DB filename inside --library-dir.",
    )
    parser.add_argument(
        "--skip-index",
        action="store_true",
        help="Only rewrite the DB JSON; do not rebuild match/embedding index.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    library_dir = Path(args.library_dir).expanduser().resolve()
    db_path = library_dir / args.db_filename
    if not db_path.exists():
        raise FileNotFoundError(f"Asset DB not found: {db_path}")

    config = Config.from_env(args.env_file)
    if not config.llm_api_key or not config.llm_model:
        raise RuntimeError("GEN_APIKEY/GEN_MODEL not configured")
    client = create_llm_client(config, web_search=False)

    db = json.loads(db_path.read_text(encoding="utf-8"))
    assets = db.get("assets")
    if not isinstance(assets, list):
        raise ValueError(f"Asset DB has no assets array: {db_path}")

    backgrounds = [
        asset
        for asset in assets
        if isinstance(asset, dict) and str(asset.get("asset_kind") or "") == "background"
    ]
    if not backgrounds:
        print(f"No background assets found in {db_path}")
        return 0

    subset_db = {
        "schema_version": int(db.get("schema_version") or 1),
        "assets": backgrounds,
        "warnings": [],
    }
    enrich_ai_image_asset_db_keywords(
        subset_db,
        client,
        batch_size=max(1, int(args.keyword_batch_size or 1)),
        include_match_keywords=False,
    )

    warnings = db.setdefault("warnings", [])
    if isinstance(warnings, list):
        warnings.extend(str(item) for item in subset_db.get("warnings", []) if item)
    db["schema_version"] = max(int(db.get("schema_version") or 0), int(subset_db.get("schema_version") or 0))
    db["keyword_built_at"] = subset_db.get("keyword_built_at")
    db["keyword_builder"] = {
        **(subset_db.get("keyword_builder") or {}),
        "refreshed_asset_kind": "background",
        "refreshed_count": len(backgrounds),
    }
    db["asset_count"] = len(assets)
    db_path.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")

    index_path = library_dir / DEFAULT_MATCH_INDEX_FILENAME
    if not args.skip_index:
        _index, index_path = write_ai_image_match_index(db, library_dir)

    print(f"Refreshed background assets: {len(backgrounds)}")
    print(f"Asset DB: {db_path}")
    if not args.skip_index:
        print(f"Match index: {index_path}")
    if subset_db.get("warnings"):
        print(f"Warnings: {len(subset_db['warnings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
