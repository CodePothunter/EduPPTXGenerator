"""复用层三路召回+融合：BM25(_rank_reuse_candidates)、Qwen embedding(_rank_embedding_candidates)、substring 召回，RRF 合池(_rank_hybrid_reuse_candidates)。依赖 _scoring/_store/_embedding/_backend 等。函数体逐字一致。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger as PROGRESS_LOGGER

from edupptx.reuse import _embedding as _reuse_embedding
from edupptx.reuse._util import (
    _clean_text,
    _dedupe_terms,
    _dict,
    _read_json_if_exists,
)
from edupptx.reuse._constants import (
    DEFAULT_HYBRID_RETRIEVAL_POOL_SIZE,
    DEFAULT_QUERY_EMBEDDING_CACHE_FILENAME,
    DEFAULT_QUERY_EMBEDDING_CACHE_META_FILENAME,
    DEFAULT_REUSE_CANDIDATE_LIMIT,
    DEFAULT_RRF_K,
    HYBRID_BM25_WEIGHT,
    HYBRID_EMBEDDING_WEIGHT,
    HYBRID_SUBSTRING_WEIGHT,
    QUERY_EMBEDDING_CACHE_SCHEMA_VERSION,
    _EMBEDDING_QUERY_FAILURE_WARNED,
)
from edupptx.reuse._assets import (
    _background_retrieval_text,
    _is_background_asset,
    _page_retrieval_text,
)
from edupptx.reuse._scoring import (
    _background_color_bias,
    _background_prompt_query_terms,
    _bm25_tokens_from_values,
    _cached_base_reuse_score_details,
    _candidate_policy_score,
    _score_background_reuse_candidate_details,
    _term_in_text,
)
from edupptx.reuse._embedding import (
    _embedding_model_name,
)
from edupptx.reuse._store import (
    _resolve_asset_image_path,
)


def _target_embedding_text(asset: dict[str, Any]) -> str:
    if _is_background_asset(asset):
        return _background_retrieval_text(asset)

    return _page_retrieval_text(asset)


def _rank_reuse_candidates(
    target: dict[str, Any],
    assets: list[Any],
    *,
    library_root: Path,
    limit: int,
    score_details_cache: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        image_path = _resolve_asset_image_path(library_root, item.get("image_path"))
        if image_path is None or not image_path.exists():
            continue
        score_details = _cached_base_reuse_score_details(target, item, score_details_cache)
        score = float(score_details.get("score") or 0.0)
        if score <= 0:
            continue
        scored.append(
            {
                "asset": item,
                "candidate_image_path": image_path,
                "keyword_score": round(score, 4),
                "transform_policy": score_details.get("transform_policy") or {},
                "score_details": _debug_score_details(score_details),
            }
        )
    scored.sort(key=lambda item: item["keyword_score"], reverse=True)
    return scored[: max(1, int(limit or DEFAULT_REUSE_CANDIDATE_LIMIT))]


def _query_embedding_cache_paths(cache_dir: str | Path) -> tuple[Path, Path]:
    root = Path(cache_dir).expanduser().resolve()
    return (
        root / DEFAULT_QUERY_EMBEDDING_CACHE_FILENAME,
        root / DEFAULT_QUERY_EMBEDDING_CACHE_META_FILENAME,
    )


def _load_query_embedding_disk_cache(cache_dir: str | Path, *, model_name: str) -> dict[str, Any]:
    index_path, meta_path = _query_embedding_cache_paths(cache_dir)
    if not index_path.exists() or not meta_path.exists():
        return {}
    meta = _read_json_if_exists(meta_path)
    if (
        int(meta.get("schema_version") or 0) != QUERY_EMBEDDING_CACHE_SCHEMA_VERSION
        or _clean_text(meta.get("model")) != _clean_text(model_name)
    ):
        return {}
    try:
        import numpy as np

        data = np.load(index_path, allow_pickle=False)
        try:
            keys = [str(item) for item in data["keys"].tolist()]
            vectors = np.asarray(data["vectors"], dtype="float32")
        finally:
            data.close()
    except Exception as exc:
        PROGRESS_LOGGER.warning(
            "AI image query embedding cache ignored: path={}, reason={}",
            index_path,
            str(exc)[:180],
        )
        return {}
    if len(vectors.shape) == 1:
        vectors = vectors.reshape(1, -1)
    count = min(len(keys), int(vectors.shape[0]))
    return {
        keys[index]: vectors[index]
        for index in range(count)
        if keys[index]
    }


def _write_query_embedding_disk_cache(
    cache_dir: str | Path,
    cache: dict[str, Any],
    *,
    model_name: str,
) -> None:
    model_prefix = f"{model_name}:"
    rows = [
        (key, value)
        for key, value in sorted(cache.items())
        if isinstance(key, str) and key.startswith(model_prefix)
    ]
    if not rows:
        return
    try:
        import numpy as np

        keys: list[str] = []
        vectors: list[Any] = []
        expected_dim: int | None = None
        for key, value in rows:
            vector = np.asarray(value, dtype="float32")
            if len(vector.shape) != 1:
                continue
            dim = int(vector.shape[0])
            if expected_dim is None:
                expected_dim = dim
            if dim != expected_dim:
                continue
            keys.append(key)
            vectors.append(vector)
        if not keys:
            return
        index_path, meta_path = _query_embedding_cache_paths(cache_dir)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        temp_index_path = index_path.with_name(f"{index_path.name}.tmp")
        temp_meta_path = meta_path.with_name(f"{meta_path.name}.tmp")
        with temp_index_path.open("wb") as handle:
            np.savez_compressed(
                handle,
                keys=np.asarray(keys, dtype=str),
                vectors=np.vstack(vectors).astype("float32"),
            )
        meta = {
            "schema_version": QUERY_EMBEDDING_CACHE_SCHEMA_VERSION,
            "model": model_name,
            "entry_count": len(keys),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        temp_meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_index_path, index_path)
        os.replace(temp_meta_path, meta_path)
    except Exception as exc:
        PROGRESS_LOGGER.warning(
            "AI image query embedding cache write skipped: dir={}, reason={}",
            cache_dir,
            str(exc)[:180],
        )


def _rank_embedding_candidates(
    target: dict[str, Any],
    assets: list[Any],
    *,
    library_root: Path,
    embedding_index: dict[str, Any],
    limit: int,
    query_embedding_cache: dict[str, Any] | None = None,
    query_embedding_cache_dir: str | Path | None = None,
    status_sink: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    vectors = embedding_index.get("vectors")
    asset_ids = embedding_index.get("asset_ids")
    if vectors is None or not isinstance(asset_ids, list) or not asset_ids:
        return []

    try:
        import numpy as np

        model_name = _embedding_model_name()
        if query_embedding_cache is None:
            query_embedding_cache = {}
        if query_embedding_cache_dir is not None:
            query_embedding_cache.update(
                {
                    key: value
                    for key, value in _load_query_embedding_disk_cache(
                        query_embedding_cache_dir,
                        model_name=model_name,
                    ).items()
                    if key not in query_embedding_cache
                }
            )
        disk_cache_dirty = False

        def query_vector_for(text: str, purpose: str):
            nonlocal disk_cache_dirty
            cache_key = f"{model_name}:{purpose}:{text}"
            if cache_key in query_embedding_cache:
                return query_embedding_cache[cache_key]
            encoded = _reuse_embedding._encode_embedding_texts([text], query=True)[0]
            query_embedding_cache[cache_key] = encoded
            disk_cache_dirty = True
            return encoded

        query_vector = query_vector_for(_target_embedding_text(target), "target")
        scores = np.asarray(vectors).dot(query_vector)
        background_color_bias_scores_by_id: dict[str, float] = {}
        color_bias_vectors = embedding_index.get("background_color_bias_vectors")
        color_bias_asset_ids = embedding_index.get("background_color_bias_asset_ids")
        target_color_bias = _background_color_bias(target)
        if (
            _is_background_asset(target)
            and target_color_bias
            and color_bias_vectors is not None
            and isinstance(color_bias_asset_ids, list)
            and color_bias_asset_ids
        ):
            color_query_vector = query_vector_for(target_color_bias, "background_color_bias")
            color_scores = np.asarray(color_bias_vectors).dot(color_query_vector)
            background_color_bias_scores_by_id = {
                _clean_text(asset_id): float(color_scores[idx])
                for idx, asset_id in enumerate(color_bias_asset_ids)
            }
        if disk_cache_dirty and query_embedding_cache_dir is not None:
            _write_query_embedding_disk_cache(
                query_embedding_cache_dir,
                query_embedding_cache,
                model_name=model_name,
            )
    except Exception as exc:
        # H-1: query-side embedding 编码/模型加载失败不能静默吞掉——否则复用会
        # 无声退化为几乎全拒，状态却仍报 enabled。大声记录（进程级 once-guard
        # 防刷屏）并把失败信号写进 embedding_status，让运维在 debug 输出可见。
        global _EMBEDDING_QUERY_FAILURE_WARNED
        message = (
            f"AI image reuse embedding query encode FAILED ({type(exc).__name__}: "
            f"{str(exc)[:200]}); reuse degrades to text-only recall. "
            f"Check EDUPPTX_AI_IMAGE_EMBEDDING_MODEL (model='{_embedding_model_name()}')."
        )
        if not _EMBEDDING_QUERY_FAILURE_WARNED:
            PROGRESS_LOGGER.warning(message)
            _EMBEDDING_QUERY_FAILURE_WARNED = True
        else:
            PROGRESS_LOGGER.debug(message)
        if status_sink is not None:
            status_sink["query_encode_failed"] = True
            status_sink["query_encode_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            status_sink.setdefault("reason", "embedding_query_failed")
        return []

    assets_by_id = {
        _clean_text(item.get("asset_id")): item
        for item in assets
        if isinstance(item, dict) and _clean_text(item.get("asset_id"))
    }
    scored: list[dict[str, Any]] = []
    for idx, asset_id in enumerate(asset_ids):
        asset = assets_by_id.get(_clean_text(asset_id))
        if not asset:
            continue
        if _clean_text(target.get("asset_kind")) != _clean_text(asset.get("asset_kind")):
            continue
        image_path = _resolve_asset_image_path(library_root, asset.get("image_path"))
        if image_path is None or not image_path.exists():
            continue
        row = {
            "asset": asset,
            "candidate_image_path": image_path,
            "embedding_score": round(float(scores[idx]), 4),
        }
        clean_asset_id = _clean_text(asset_id)
        if clean_asset_id in background_color_bias_scores_by_id:
            row["background_color_bias_embedding_score"] = round(
                float(background_color_bias_scores_by_id[clean_asset_id]),
                4,
            )
        scored.append(row)
    scored.sort(key=lambda item: float(item.get("embedding_score") or 0.0), reverse=True)
    return scored[: max(1, int(limit or DEFAULT_HYBRID_RETRIEVAL_POOL_SIZE))]


def _rank_substring_candidates(
    target: dict[str, Any],
    assets: list[Any],
    *,
    library_root: Path,
    limit: int,
) -> list[dict[str, Any]]:
    if _is_background_asset(target):
        terms = _background_prompt_query_terms(target)
    else:
        terms = _bm25_tokens_from_values([_page_retrieval_text(target)])
    terms = [term for term in terms if len(term.replace(" ", "")) >= 2]
    if not terms:
        return []

    scored: list[dict[str, Any]] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        if _clean_text(target.get("asset_kind")) != _clean_text(item.get("asset_kind")):
            continue
        image_path = _resolve_asset_image_path(library_root, item.get("image_path"))
        if image_path is None or not image_path.exists():
            continue
        text = _candidate_hybrid_text(item)
        hits = [term for term in terms if _term_in_text(term, text)]
        if not hits:
            continue
        scored.append(
            {
                "asset": item,
                "candidate_image_path": image_path,
                "substring_score": round(len(hits) / max(1, len(terms)), 4),
                "substring_hits": hits[:16],
            }
        )
    scored.sort(key=lambda item: float(item.get("substring_score") or 0.0), reverse=True)
    return scored[: max(1, int(limit or DEFAULT_HYBRID_RETRIEVAL_POOL_SIZE))]


def _rank_hybrid_reuse_candidates(
    target: dict[str, Any],
    assets: list[Any],
    *,
    library_root: Path,
    bm25_ranked: list[dict[str, Any]],
    embedding_ranked: list[dict[str, Any]],
    substring_ranked: list[dict[str, Any]],
    threshold: float,
    limit: int,
    score_details_cache: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    candidate_by_id: dict[str, dict[str, Any]] = {}
    rrf_scores: dict[str, float] = {}

    def add_ranked(items: list[dict[str, Any]], score_key: str, weight: float) -> None:
        for rank, item in enumerate(items, start=1):
            asset = _dict(item.get("asset"))
            asset_id = _clean_text(asset.get("asset_id"))
            if not asset_id:
                continue
            candidate = candidate_by_id.setdefault(
                asset_id,
                {
                    "asset": asset,
                    "candidate_image_path": item.get("candidate_image_path"),
                    "keyword_score": 0.0,
                    "embedding_score": 0.0,
                    "substring_score": 0.0,
                    "substring_hits": [],
                    "retrieval_ranks": {},
                },
            )
            candidate["candidate_image_path"] = candidate.get("candidate_image_path") or item.get("candidate_image_path")
            candidate[score_key] = max(float(candidate.get(score_key) or 0.0), float(item.get(score_key) or 0.0))
            if "background_color_bias_embedding_score" in item:
                candidate["background_color_bias_embedding_score"] = max(
                    float(candidate.get("background_color_bias_embedding_score") or 0.0),
                    float(item.get("background_color_bias_embedding_score") or 0.0),
                )
            if score_key == "substring_score":
                candidate["substring_hits"] = _dedupe_terms(
                    [*(candidate.get("substring_hits") or []), *(item.get("substring_hits") or [])]
                )[:16]
            retrieval_name = {
                "keyword_score": "bm25",
                "embedding_score": "embedding",
                "substring_score": "substring",
            }.get(score_key, score_key)
            candidate["retrieval_ranks"][retrieval_name] = rank
            rrf_scores[asset_id] = rrf_scores.get(asset_id, 0.0) + weight / (DEFAULT_RRF_K + rank)

    add_ranked(bm25_ranked, "keyword_score", HYBRID_BM25_WEIGHT)
    add_ranked(embedding_ranked, "embedding_score", HYBRID_EMBEDDING_WEIGHT)
    add_ranked(substring_ranked, "substring_score", HYBRID_SUBSTRING_WEIGHT)

    if not candidate_by_id:
        return []

    max_rrf = max(rrf_scores.values()) if rrf_scores else 1.0
    results: list[dict[str, Any]] = []
    for asset_id, candidate in candidate_by_id.items():
        asset = _dict(candidate.get("asset"))
        if _is_background_asset(target):
            retrieval_ranks = _dict(candidate.get("retrieval_ranks"))
            score_details = _score_background_reuse_candidate_details(
                target,
                asset,
                prompt_embedding_score=(
                    float(candidate.get("embedding_score") or 0.0) if "embedding" in retrieval_ranks else None
                ),
                prompt_substring_score=(
                    float(candidate.get("substring_score") or 0.0) if "substring" in retrieval_ranks else None
                ),
                color_bias_embedding_score=(
                    float(candidate.get("background_color_bias_embedding_score") or 0.0)
                    if "background_color_bias_embedding_score" in candidate
                    else None
                ),
            )
            candidate["keyword_score"] = round(float(score_details.get("score") or 0.0), 4)
            candidate["background_reuse_score"] = candidate["keyword_score"]
        else:
            # Reuse the first-pass score (cached by _rank_reuse_candidates) instead
            # of recomputing it — _score_reuse_candidate_details is deterministic, so
            # the cache value is identical. Copy before the .update() below so the
            # shared cached dict is never mutated. A cache miss falls back to a fresh
            # compute (the prior behaviour), so this is purely a redundant-work cut.
            score_details = dict(
                _cached_base_reuse_score_details(target, asset, score_details_cache)
            )
            bm25_score = float(score_details.get("score") or 0.0)
            candidate["keyword_score"] = round(max(float(candidate.get("keyword_score") or 0.0), bm25_score), 4)
        candidate["rrf_score"] = round(rrf_scores.get(asset_id, 0.0), 6)
        candidate["hybrid_score"] = round(rrf_scores.get(asset_id, 0.0) / max(max_rrf, 1e-9), 4)
        candidate["transform_policy"] = score_details.get("transform_policy") or {}
        policy_score = _candidate_policy_score(candidate, score_details)
        candidate["policy_score"] = policy_score
        score_details.update(
            {
                "embedding_score": candidate.get("embedding_score"),
                "substring_score": candidate.get("substring_score"),
                "substring_hits": candidate.get("substring_hits"),
                "background_color_bias_embedding_score": candidate.get("background_color_bias_embedding_score"),
                "rrf_score": candidate.get("rrf_score"),
                "hybrid_score": candidate.get("hybrid_score"),
                "policy_score": policy_score,
                "retrieval_ranks": candidate.get("retrieval_ranks"),
            }
        )
        candidate["score_details"] = _debug_score_details(score_details)
        results.append(candidate)

    # R2: ranking is driven by policy_score (the single adjudication score), with
    # keyword/embedding as deterministic tie-breakers. The RRF-normalized hybrid_score
    # was only a 4th tie-breaker (the decision tier already bypasses it via policy_score,
    # see the "hybrid_score ... unusable" note in _apply_reuse_policy_to_ranked_candidates),
    # so it is dropped from ranking. rrf_score/hybrid_score remain as audit-only metrics.
    results.sort(
        key=lambda item: (
            float(item.get("policy_score") or 0.0),
            float(item.get("keyword_score") or 0.0),
            float(item.get("embedding_score") or 0.0),
            float(item.get("substring_score") or 0.0),
        ),
        reverse=True,
    )
    return results[: max(1, int(limit or DEFAULT_REUSE_CANDIDATE_LIMIT))]


def _debug_score_details(details: dict[str, Any]) -> dict[str, Any]:
    score = float(details.get("score") or 0.0)
    return {
        "score": round(score, 4),
        "reject_reason": _clean_text(details.get("reject_reason")),
        "subject_filter": details.get("subject_filter") or {},
        "keyword_score": round(float(details.get("keyword_score") or 0.0), 4),
        "content_match_score": round(float(details.get("content_match_score") or 0.0), 4),
        "route_score": round(float(details.get("route_score") or 0.0), 4),
        "route_hits": details.get("route_hits") or [],
        "route_grade_family_match": round(float(details.get("route_grade_family_match") or 0.0), 4),
        "route_page_type_match": round(float(details.get("route_page_type_match") or 0.0), 4),
        "target_route_grade_family": _clean_text(details.get("target_route_grade_family")),
        "candidate_route_grade_family": _clean_text(details.get("candidate_route_grade_family")),
        "target_route_page_type": _clean_text(details.get("target_route_page_type")),
        "candidate_route_page_type": _clean_text(details.get("candidate_route_page_type")),
        "core_score": round(float(details.get("core_score") or 0.0), 4),
        "core_hits": details.get("core_hits") or [],
        "missing_core_groups": details.get("missing_core_groups") or [],
        "aspect_score": round(float(details.get("aspect_score") or 0.0), 4),
        "transform_policy": details.get("transform_policy") or {},
        "raw_score_before_transform_penalty": round(
            float(details.get("raw_score_before_transform_penalty") or 0.0),
            4,
        ),
        "embedding_score": round(float(details.get("embedding_score") or 0.0), 4),
        "substring_score": round(float(details.get("substring_score") or 0.0), 4),
        "substring_hits": details.get("substring_hits") or [],
        "rrf_score": round(float(details.get("rrf_score") or 0.0), 6),
        "hybrid_score": round(float(details.get("hybrid_score") or 0.0), 4),
        "policy_score": round(float(details.get("policy_score") or 0.0), 4),
        "retrieval_ranks": details.get("retrieval_ranks") or {},
        "background_reuse_score": round(float(details.get("background_reuse_score") or 0.0), 4),
        "background_prompt_match_score": round(float(details.get("background_prompt_match_score") or 0.0), 4),
        "background_prompt_bm25_score": round(float(details.get("background_prompt_bm25_score") or 0.0), 4),
        "background_prompt_bm25_hits": details.get("background_prompt_bm25_hits") or [],
        "background_prompt_embedding_score": round(float(details.get("background_prompt_embedding_score") or 0.0), 4),
        "background_prompt_substring_score": round(float(details.get("background_prompt_substring_score") or 0.0), 4),
        "background_prompt_substring_hits": details.get("background_prompt_substring_hits") or [],
        "background_color_bias_used": bool(details.get("background_color_bias_used")),
        "background_color_bias_match_score": round(
            float(details.get("background_color_bias_match_score") or 0.0),
            4,
        ),
        "background_color_bias_bm25_score": round(
            float(details.get("background_color_bias_bm25_score") or 0.0),
            4,
        ),
        "background_color_bias_bm25_hits": details.get("background_color_bias_bm25_hits") or [],
        "background_color_bias_embedding_score": round(
            float(details.get("background_color_bias_embedding_score") or 0.0),
            4,
        ),
        "background_color_bias_substring_score": round(
            float(details.get("background_color_bias_substring_score") or 0.0),
            4,
        ),
        "background_color_bias_substring_hits": details.get("background_color_bias_substring_hits") or [],
    }


def _candidate_hybrid_text(asset: dict[str, Any]) -> str:
    if _is_background_asset(asset):
        return _background_retrieval_text(asset)

    return _page_retrieval_text(asset)
