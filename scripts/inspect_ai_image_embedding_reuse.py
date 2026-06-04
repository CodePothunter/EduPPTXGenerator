"""Inspect why an AI-image embedding sidecar is or is not reusable."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_read_error": str(exc)}
    return payload if isinstance(payload, dict) else {"_read_error": "json_root_is_not_object"}


def _npz_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    try:
        import numpy as np

        data = np.load(path, allow_pickle=False)
        try:
            info: dict[str, Any] = {"exists": True, "files": list(data.files)}
            if "asset_ids" in data.files:
                info["asset_id_count"] = len(data["asset_ids"].tolist())
            if "vectors" in data.files:
                vectors = data["vectors"]
                info["vector_shape"] = list(vectors.shape)
            if "background_color_bias_asset_ids" in data.files:
                info["background_color_bias_asset_id_count"] = len(
                    data["background_color_bias_asset_ids"].tolist()
                )
            if "background_color_bias_vectors" in data.files:
                info["background_color_bias_vector_shape"] = list(
                    data["background_color_bias_vectors"].shape
                )
            if "keys" in data.files:
                info["key_count"] = len(data["keys"].tolist())
            return info
        finally:
            data.close()
    except Exception as exc:
        return {"exists": True, "read_error": str(exc)}


def _query_cache_dir_from_env() -> Path | None:
    run_dir = os.environ.get("RUN_DIR")
    if not run_dir:
        return None
    return Path(run_dir).expanduser().resolve() / "03_retrieve"


def inspect_library(library_dir: Path, query_cache_dir: Path | None) -> dict[str, Any]:
    from edupptx.materials.ai_image_asset_db import (
        DEFAULT_EMBEDDING_INDEX_FILENAME,
        DEFAULT_EMBEDDING_META_FILENAME,
        DEFAULT_QUERY_EMBEDDING_CACHE_FILENAME,
        DEFAULT_QUERY_EMBEDDING_CACHE_META_FILENAME,
        EMBEDDING_INDEX_SCHEMA_VERSION,
        QUERY_EMBEDDING_CACHE_SCHEMA_VERSION,
        _asset_embedding_text,
        _background_color_bias,
        _clean_text,
        _embedding_disabled,
        _embedding_model_name,
        _is_background_asset,
        read_ai_image_split_match_index,
    )

    root = library_dir.expanduser().resolve()
    current_model = _embedding_model_name()
    split = read_ai_image_split_match_index(root)
    if split is None:
        return {
            "library_dir": str(root),
            "error": "missing_split_match_index",
            "current_model": current_model,
        }

    index, split_dir = split
    assets = index.get("assets") if isinstance(index, dict) else []
    assets = assets if isinstance(assets, list) else []
    embeddable: list[dict[str, Any]] = []
    background_color_bias_assets: list[dict[str, Any]] = []
    non_embeddable: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_id = _clean_text(asset.get("asset_id"))
        text = _asset_embedding_text(asset)
        if asset_id and text:
            embeddable.append(asset)
        else:
            non_embeddable.append(asset)
        if asset_id and _is_background_asset(asset) and _background_color_bias(asset):
            background_color_bias_assets.append(asset)

    embedding_index_path = root / DEFAULT_EMBEDDING_INDEX_FILENAME
    embedding_meta_path = root / DEFAULT_EMBEDDING_META_FILENAME
    meta = _read_json(embedding_meta_path)
    meta_asset_count = int(meta.get("asset_count") or -1) if meta else -1
    meta_model = _clean_text(meta.get("model")) if meta else ""
    meta_schema = int(meta.get("schema_version") or 0) if meta else 0

    current_code_would_reuse = (
        not _embedding_disabled()
        and embedding_index_path.exists()
        and embedding_meta_path.exists()
        and meta_schema == EMBEDDING_INDEX_SCHEMA_VERSION
        and meta_model == current_model
        and meta_asset_count == len(embeddable)
        and int(meta.get("background_color_bias_asset_count") or 0) == len(background_color_bias_assets)
    )
    reasons: list[str] = []
    if _embedding_disabled():
        reasons.append("embedding_disabled_by_environment")
    if not embedding_index_path.exists():
        reasons.append("missing_embedding_index_npz")
    if not embedding_meta_path.exists():
        reasons.append("missing_embedding_meta_json")
    if meta_schema != EMBEDDING_INDEX_SCHEMA_VERSION:
        reasons.append("schema_version_mismatch")
    if meta_model != current_model:
        reasons.append("model_mismatch")
    if meta_asset_count != len(embeddable):
        reasons.append("asset_count_mismatch_expected_embeddable_asset_count")
    if int(meta.get("background_color_bias_asset_count") or 0) != len(background_color_bias_assets):
        reasons.append("background_color_bias_asset_count_mismatch")

    result: dict[str, Any] = {
        "library_dir": str(root),
        "split_index_dir": str(split_dir),
        "current_model": current_model,
        "embedding_disabled": _embedding_disabled(),
        "index_asset_count": len(assets),
        "embeddable_asset_count": len(embeddable),
        "background_color_bias_asset_count": len(background_color_bias_assets),
        "non_embeddable_asset_count": len(non_embeddable),
        "embedding_meta_path": str(embedding_meta_path),
        "embedding_index_path": str(embedding_index_path),
        "meta_schema_version": meta.get("schema_version"),
        "expected_schema_version": EMBEDDING_INDEX_SCHEMA_VERSION,
        "meta_model": meta.get("model"),
        "meta_match_asset_count": meta.get("match_asset_count"),
        "meta_asset_count": meta.get("asset_count"),
        "meta_non_embeddable_asset_count": meta.get("non_embeddable_asset_count"),
        "meta_background_color_bias_asset_count": meta.get("background_color_bias_asset_count"),
        "meta_vector_dim": meta.get("vector_dim"),
        "embedding_npz": _npz_info(embedding_index_path),
        "current_code_would_reuse_embedding_sidecar": current_code_would_reuse,
        "reuse_blocking_reasons": reasons,
        "first_non_embeddable_assets": [
            {
                "asset_id": asset.get("asset_id"),
                "asset_kind": asset.get("asset_kind"),
                "strict_reuse_group": asset.get("strict_reuse_group"),
                "image_path": asset.get("image_path"),
                "caption": asset.get("caption"),
                "content_prompt": asset.get("content_prompt"),
            }
            for asset in non_embeddable[:20]
        ],
    }

    if query_cache_dir is not None:
        query_root = query_cache_dir.expanduser().resolve()
        query_meta_path = query_root / DEFAULT_QUERY_EMBEDDING_CACHE_META_FILENAME
        query_index_path = query_root / DEFAULT_QUERY_EMBEDDING_CACHE_FILENAME
        query_meta = _read_json(query_meta_path)
        result["query_cache"] = {
            "cache_dir": str(query_root),
            "meta_path": str(query_meta_path),
            "index_path": str(query_index_path),
            "meta_schema_version": query_meta.get("schema_version"),
            "expected_schema_version": QUERY_EMBEDDING_CACHE_SCHEMA_VERSION,
            "meta_model": query_meta.get("model"),
            "meta_entry_count": query_meta.get("entry_count"),
            "npz": _npz_info(query_index_path),
            "model_matches_current_model": _clean_text(query_meta.get("model")) == current_model,
        }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--library-dir",
        default=os.environ.get("LIB", "materials_library_ppt"),
        help="PPT materials library path. Defaults to $LIB or materials_library_ppt.",
    )
    parser.add_argument(
        "--query-cache-dir",
        default=None,
        help="Optional query embedding cache dir. Defaults to $RUN_DIR/03_retrieve when RUN_DIR is set.",
    )
    args = parser.parse_args(argv)

    query_cache_dir = Path(args.query_cache_dir) if args.query_cache_dir else _query_cache_dir_from_env()
    report = inspect_library(Path(args.library_dir), query_cache_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if not report.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
