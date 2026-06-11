"""Rebuild embedding sidecars for an existing PPT materials library."""

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


def rebuild_ppt_materials_embeddings(
    *,
    library_dir: str | Path = DEFAULT_LIBRARY_DIR,
    env_file: str | Path = ".env",
) -> dict[str, Any]:
    from edupptx.config import Config
    from edupptx.materials.ai_image_asset_db import (
        DEFAULT_EMBEDDING_INDEX_FILENAME,
        DEFAULT_EMBEDDING_META_FILENAME,
        STRICT_REUSE_INDEX_DIRNAME,
        read_ai_image_split_match_index,
        write_ai_image_embedding_index,
    )

    Config.from_env(str(env_file))
    root = Path(library_dir).expanduser().resolve()
    split = read_ai_image_split_match_index(root)
    if split is None:
        return {
            "ok": False,
            "reason": "missing_split_index",
            "library_dir": str(root),
            "split_index_dir": str(root / STRICT_REUSE_INDEX_DIRNAME),
        }
    index, split_dir = split
    report = write_ai_image_embedding_index(index, root)
    return {
        "ok": bool(report.get("enabled")),
        "library_dir": str(root),
        "split_index_dir": str(split_dir),
        "embedding_index_path": str(root / DEFAULT_EMBEDDING_INDEX_FILENAME),
        "embedding_meta_path": str(root / DEFAULT_EMBEDDING_META_FILENAME),
        **report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)

    report = rebuild_ppt_materials_embeddings(library_dir=args.library_dir, env_file=args.env_file)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
