"""Backfill caption fields in material-library indexes from legacy content_prompt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edupptx.config import Config
from edupptx.llm_client import create_llm_client
from edupptx.materials.caption_rules import summarize_records

STRICT_REUSE_INDEX_DIRNAME = "strict_reuse_indexes"
DEFAULT_MATCH_INDEX_FILENAME = "ai_image_match_index.json"


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _load_index(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"index must be a JSON object: {path}")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ValueError(f"index must contain an assets array: {path}")
    return payload


def _caption_candidates(
    index: dict[str, Any], *, only_missing: bool, source_field: str = "content_prompt"
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for asset in index.get("assets", []):
        if not isinstance(asset, dict):
            continue
        if only_missing and _clean_text(asset.get("caption")):
            continue
        if not _clean_text(asset.get(source_field)):
            continue
        result.append(asset)
    return result


def count_caption_candidates_in_index(
    index_path: str | Path, *, only_missing: bool = True, source_field: str = "content_prompt"
) -> int:
    index = _load_index(Path(index_path))
    return len(_caption_candidates(index, only_missing=only_missing, source_field=source_field))


def backfill_caption_in_index(
    index_path: str | Path,
    client: Any,
    *,
    batch_size: int = 50,
    only_missing: bool = True,
    source_field: str = "content_prompt",
) -> int:
    """Add caption to assets by summarizing the configured source field."""
    path = Path(index_path)
    index = _load_index(path)
    assets = _caption_candidates(index, only_missing=only_missing, source_field=source_field)
    if not assets:
        return 0

    summarized = summarize_records(
        assets,
        client,
        query_field=source_field,
        caption_field="caption",
        batch_size=batch_size,
    )
    for asset, generated in zip(assets, summarized):
        caption = _clean_text(generated.get("caption"))
        if caption:
            asset["caption"] = caption

    path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(assets)


def iter_caption_index_paths(library_dir: str | Path) -> list[Path]:
    root = Path(library_dir)
    candidates: list[Path] = []
    split_dir = root / STRICT_REUSE_INDEX_DIRNAME
    if split_dir.exists():
        candidates.extend(sorted(split_dir.glob("*.json")))
    match_index = root / DEFAULT_MATCH_INDEX_FILENAME
    if match_index.exists():
        candidates.append(match_index)
    paths: list[Path] = []
    for path in dict.fromkeys(candidates):
        try:
            _load_index(path)
        except ValueError:
            continue
        paths.append(path)
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", default="materials_library_ppt")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing captions too.")
    parser.add_argument("--source-field", default="content_prompt")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    library_dir = Path(args.library_dir).expanduser().resolve()
    index_paths = iter_caption_index_paths(library_dir)
    if not index_paths:
        raise FileNotFoundError(f"No material indexes found under: {library_dir}")

    only_missing = not args.overwrite
    if args.dry_run:
        total = sum(
            count_caption_candidates_in_index(
                path,
                only_missing=only_missing,
                source_field=args.source_field,
            )
            for path in index_paths
        )
        print(f"dry-run: {total} assets would receive captions across {len(index_paths)} indexes")
        return 0

    config = Config.from_env(args.env_file)
    if not config.llm_api_key or not config.llm_model:
        raise RuntimeError("GEN_APIKEY/GEN_MODEL not configured")
    client = create_llm_client(config, web_search=False)

    total = 0
    for path in index_paths:
        updated = backfill_caption_in_index(
            path,
            client,
            batch_size=max(1, int(args.batch_size or 50)),
            only_missing=only_missing,
            source_field=args.source_field,
        )
        total += updated
        print(f"{path}: {updated} captions")
    print(f"updated {total} captions across {len(index_paths)} indexes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
