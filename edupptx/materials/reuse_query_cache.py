"""Per-session persistent cache for reuse target keywords + embedding vectors.

`ReuseSearchContext` caches target keyword payloads (LLM output) and target
query and target-constraint embeddings (embedding model output) in memory for
the duration of a single generation. This module persists those caches to disk so an offline
replay of BM25/embedding scoring against the library can skip every LLM /
embedding-model call.

Layout: one JSON file per session, e.g.::

    <session_dir>/reuse_query_cache.json
    {
      "schema_version": 1,
      "target_keyword_cache": { "target:<hash>": { ...enriched target... } },
      "query_embedding_cache": { "<model>:<purpose>:<text>": [floats] }
    }

The keyword payload is the enriched target dict produced by
``_enrich_reuse_target_keywords_once``. The embedding vector may be a retrieval
query embedding or a constraint-comparison embedding. It is whatever
``_encode_embedding_texts`` returns (a 1-D numpy array), stored as a plain float
list so the file is human-inspectable; numpy arrays are re-hydrated on load.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

REUSE_QUERY_CACHE_FILENAME = "reuse_query_cache.json"
SCHEMA_VERSION = 1


def _vector_to_jsonable(vec: Any) -> list[float] | None:
    if vec is None:
        return None
    if hasattr(vec, "tolist"):
        try:
            return list(vec.tolist())
        except Exception:
            return None
    if isinstance(vec, (list, tuple)):
        try:
            return [float(x) for x in vec]
        except Exception:
            return None
    return None


def _jsonable_to_vector(payload: Any):
    if payload is None:
        return None
    try:
        import numpy as np
        return np.asarray(payload, dtype="float32")
    except Exception:
        return None


def save_reuse_query_cache(
    session_dir: str | Path,
    *,
    target_keyword_cache: dict[str, Any] | None,
    query_embedding_cache: dict[str, Any] | None,
    filename: str = REUSE_QUERY_CACHE_FILENAME,
) -> Path | None:
    """Merge in-memory caches into <session_dir>/<filename>. Never raises.

    Returns the written path, or None on failure / empty input.
    """

    session_dir = Path(session_dir)
    if not session_dir.exists():
        logger.warning("Reuse query cache: session dir missing, skipping save: {}", session_dir)
        return None

    target_keyword_cache = target_keyword_cache or {}
    query_embedding_cache = query_embedding_cache or {}
    if not target_keyword_cache and not query_embedding_cache:
        return None

    path = session_dir / filename
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.warning(
                "Reuse query cache: existing file unreadable, overwriting: {}",
                str(exc)[:160],
            )
            existing = {}

    merged_keywords = dict(existing.get("target_keyword_cache") or {})
    for key, value in target_keyword_cache.items():
        if isinstance(value, dict):
            merged_keywords[key] = value

    merged_embeddings = dict(existing.get("query_embedding_cache") or {})
    for key, vec in query_embedding_cache.items():
        serial = _vector_to_jsonable(vec)
        if serial is not None:
            merged_embeddings[key] = serial

    payload = {
        "schema_version": SCHEMA_VERSION,
        "target_keyword_cache": merged_keywords,
        "query_embedding_cache": merged_embeddings,
    }
    try:
        temp_path = path.with_name(f"{path.name}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)
    except Exception as exc:
        logger.warning("Reuse query cache: write failed: {}", str(exc)[:160])
        return None

    logger.info(
        "Reuse query cache saved: {} (keywords={}, embeddings={})",
        path,
        len(merged_keywords),
        len(merged_embeddings),
    )
    return path


def load_reuse_query_cache(
    session_dir: str | Path,
    *,
    filename: str = REUSE_QUERY_CACHE_FILENAME,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load persisted caches. Returns (target_keyword_cache, query_embedding_cache).

    Embedding vectors are restored as numpy float32 arrays so downstream code
    can treat them identically to fresh encoder outputs. Missing file → two
    empty dicts.
    """

    session_dir = Path(session_dir)
    path = session_dir / filename
    if not path.exists():
        return {}, {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("Reuse query cache: load failed: {}", str(exc)[:160])
        return {}, {}

    keyword_cache = dict(payload.get("target_keyword_cache") or {})

    raw_embeddings = payload.get("query_embedding_cache") or {}
    embedding_cache: dict[str, Any] = {}
    for key, raw in raw_embeddings.items():
        vec = _jsonable_to_vector(raw)
        if vec is not None:
            embedding_cache[key] = vec

    return keyword_cache, embedding_cache
