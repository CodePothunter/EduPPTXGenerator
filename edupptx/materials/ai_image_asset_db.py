"""Offline builder for the generated AI image asset database."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from loguru import logger as PROGRESS_LOGGER

from edupptx.materials.reuse_policy import (
    BACKGROUND_REUSE_THRESHOLD,
    T_GAP,
    T_DIRECT,
    T_REJECT,
    decide_reuse,
    evaluate_reuse_filter,
    normalize_reuse_policy_fields,
    reuse_threshold_for_target as policy_reuse_threshold_for_target,
)
from edupptx.materials.vlm_metadata_rules import (
    normalize_padding_capacity,
)
from edupptx.reuse._util import (
    _as_int,
    _clean_keyword,
    _clean_text,
    _client_model_name,
    _dedupe_terms,
    _dict,
    _join_texts,
    _read_existing_db,
    _read_json_if_exists,
)
from edupptx.reuse._constants import (
    ALLOWED_CROSS_ASPECT_RATIO_REUSE_PAIRS,
    ASPECT_RATIO_ADJACENT_PENALTY,
    ASPECT_RATIO_TOLERANCE_ADJACENT,
    ASPECT_RATIO_TOLERANCE_SAME,
    ASPECT_REUSE_BUCKETS,
    ASPECT_REUSE_WEIGHT,
    BACKGROUND_COLOR_BIAS_REUSE_WEIGHT,
    BACKGROUND_CONTENT_PROMPT_REUSE_WEIGHT,
    BACKGROUND_REUSE_GATE_THRESHOLDS,
    BACKGROUND_REUSE_INDEX_FILENAME,
    BACKGROUND_REUSE_INDEX_GROUP,
    BM25_GRAY_REUSE_THRESHOLD,
    CONTENT_PROMPT_REUSE_WEIGHT,
    CONTENT_REUSE_GROUP,
    DEFAULT_DB_FILENAME,
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_INDEX_FILENAME,
    DEFAULT_EMBEDDING_META_FILENAME,
    DEFAULT_EMBEDDING_MISSING_CAPTION_REVIEW_FILENAME,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_HYBRID_RETRIEVAL_POOL_SIZE,
    DEFAULT_KEYWORD_BATCH_SIZE,
    DEFAULT_LIBRARY_IMAGE_DIR,
    DEFAULT_MATCH_INDEX_FILENAME,
    DEFAULT_MIN_REUSE_KEYWORD_SCORE,
    DEFAULT_QUERY_EMBEDDING_CACHE_FILENAME,
    DEFAULT_QUERY_EMBEDDING_CACHE_META_FILENAME,
    DEFAULT_REUSE_CANDIDATE_LIMIT,
    DEFAULT_REUSE_MAX_WORKERS,
    DEFAULT_RRF_K,
    EMBEDDING_GRAY_REUSE_THRESHOLD,
    EMBEDDING_INDEX_SCHEMA_VERSION,
    EMBEDDING_KEYWORD_GAP_REJECT_THRESHOLD,
    EMBEDDING_LED_LLM_REVIEW_MIN_KEYWORD,
    EMBEDDING_LED_LLM_REVIEW_MIN_SUBSTRING,
    GENERAL_REUSE_GROUP,
    HYBRID_BM25_WEIGHT,
    HYBRID_EMBEDDING_WEIGHT,
    HYBRID_SUBSTRING_WEIGHT,
    KEYWORD_LED_LLM_REVIEW_MIN_EMBEDDING,
    KEYWORD_LED_LLM_REVIEW_MIN_KEYWORD,
    KEYWORD_SCHEMA_VERSION,
    LEGACY_STRICT_REUSE_GROUPS,
    LIGHT_CONTEXT_REUSE_WEIGHT,
    MATCH_INDEX_SCHEMA_VERSION,
    MAX_LLM_REVIEWS_PER_QUERY,
    MAX_LLM_REVIEW_WORKERS,
    PAGE_IMAGE_REUSE_GATE_THRESHOLDS,
    PREWARM_KEYWORD_BATCH_SIZE,
    PREWARM_KEYWORD_MAX_WORKERS,
    QUERY_EMBEDDING_CACHE_SCHEMA_VERSION,
    R5_MAX_VLM_CALLS_PER_SESSION,
    R5_NEAR_MISS_EPSILON,
    R5_SESSION_VLM_COUNT_KEY,
    REUSE_DEBUG_FILENAME,
    REUSE_MANIFEST_FILENAME,
    REUSE_REVIEW_ACCEPT_SCORE_THRESHOLD,
    SCHEMA_VERSION,
    STRICT_REUSE_GROUPS,
    STRICT_REUSE_INDEX_DIRNAME,
    STRICT_REUSE_MAX_PER_SESSION,
    TEXT_OVERLAP_EMBEDDING_THRESHOLD,
    TEXT_OVERLAP_REVIEW_THRESHOLD,
    VISUAL_GENERIC_REUSE_THRESHOLD,
    _ALLOWED_GRADE_BANDS,
    _ALLOWED_GRADE_NORMS,
    _ALLOWED_SUBJECTS,
    _ASPECT_BUCKET_MAX_LOSS,
    _ASPECT_REUSE_BUCKET_VALUES,
    _BACKGROUND_LIKE_ROLE_TOKENS,
    _BACKGROUND_REUSE_TARGET_METADATA_FIELDS,
    _BACKGROUND_ROUTE_FIELDS,
    _BACKGROUND_ROUTE_MATCH_FIELDS,
    _CONTENT_REUSE_GROUP,
    _CORE_STYLE_MARKERS,
    _CORE_USAGE_MARKERS,
    _EMBEDDING_QUERY_FAILURE_WARNED,
    _GENERAL_REUSE_GROUP,
    _GRADE_ARABIC_TO_CN,
    _HIGH_GRADE_BAND,
    _IMAGE_SUFFIXES,
    _JUNIOR_ALIASES,
    _KNOWN_SUBJECTS,
    _LOW_GRADE_BAND,
    _LOW_GRADE_NORMS,
    _METADATA_PASSTHROUGH_FIELDS,
    _NOISE_TOKENS,
    _OTHER_GRADE,
    _OTHER_SUBJECT,
    _OUTPUT_PATH_MARKERS,
    _PAGE_REUSE_TARGET_METADATA_FIELDS,
    _PAGE_TYPE_CONTEXT_SUMMARIES,
    _PPT_COMPARISON_PASSTHROUGH_FIELDS,
    _PRECISION_SIGNAL_STOPWORDS,
    _PROJECT_ROOT,
    _PROMPT_ROUTE_LIST_FIELDS,
    _REUSE_TARGET_METADATA_SEEDED_FIELD,
    _REVIEW_PASSTHROUGH_FIELDS,
    _SENIOR_ALIASES,
    _STRICT_REUSE_PASSTHROUGH_FIELDS,
    _STRICT_REUSE_READ_GROUPS,
    _STYLE_DESCRIPTOR_MARKERS,
    _TOPIC_REF_LEADING_NOISE_RE,
    _TOPIC_REF_SUBJECT_PREFIXES,
    _TOPIC_REF_TRAILING_NOISE,
    _TOPIC_REF_WRAPPER_RE,
    _VISUAL_FORM_MARKERS,
)
from edupptx.reuse._assets import (
    _as_string_list,
    _asset_aspect_ratio_label,
    _asset_caption,
    _asset_content_prompt,
    _asset_embedding_text,
    _asset_general_value,
    _asset_generation_prompt,
    _asset_page_type,
    _asset_query,
    _asset_style_prompt,
    _asset_subject_value,
    _background_retrieval_text,
    _clean_prompt_route,
    _clean_topic_ref,
    _is_background_asset,
    _is_excluded_keyword,
    _keyword_list,
    _normalize_subject_value,
    _optional_bool,
    _page_retrieval_text,
    _route_style_prompt,
    _source_pptx_refs_for_asset,
    _topic_refs_for_asset,
    _unit_ref_for_asset,
    extract_topic_refs,
)
from edupptx.reuse._normalize import (
    _build_meta_grade_subject_normalizer_messages,
    _call_meta_grade_subject_normalizer,
    _extract_grade_token,
    _extract_subject_token,
    _is_standard_grade_band_value,
    _is_standard_grade_norm_value,
    _is_standard_subject_value,
    _load_json_response,
    _meta_grade_subject_fields_are_standard,
    _normalize_binary_reuse_group,
    _normalize_grade_band_value,
    _normalize_grade_norm_value,
    _normalize_meta_grade_subject_payload,
    _normalize_subject_scope,
    _strip_fences,
    grade_band_from_norm,
    infer_grade,
    infer_grade_band,
    infer_subject,
    resolve_meta_grade_subject,
)
from edupptx.reuse._scoring import (
    _aspect_ratio_loss,
    _aspect_ratio_penalty,
    _aspect_ratio_value,
    _background_color_bias,
    _background_prompt_doc_tokens,
    _background_prompt_query_terms,
    _background_prompt_query_tokens,
    _background_substring_similarity,
    _background_text_terms,
    _bm25_score,
    _bm25_similarity_with_hits,
    _bm25_tokens_from_values,
    _cached_base_reuse_score_details,
    _candidate_policy_score,
    _candidate_score_component,
    _clean_background_route,
    _copy_transform_policy,
    _embedding_disabled,
    _optional_int,
    _optional_score,
    _ratio_value,
    _reuse_hard_filter_reject_reason,
    _reuse_transform_policy,
    _score_background_reuse_candidate_details,
    _score_reuse_candidate_details,
    _subject_scope_decision,
    _target_transform_size,
    _term_in_text,
    _weighted_hybrid_signal,
    normalize_aspect_bucket,
)

# KEYWORD_REUSE_RULES_REFERENCE moved to reuse/_keywords.py;
# REUSE_REVIEW_SCORE_RULES_REFERENCE moved to reuse/_review.py;
# EMBED_RESCUE_FLOOR moved to reuse/_decide.py (env-driven, reload-sensitive). Both re-imported below.
# Per-query LLM review budget. Caps the number of llm_review calls made
# for a single target so a noisy candidate pool can't burn the LLM on a
# long tail of equivalent-quality candidates after the top contender has
# already been judged. K=5 gives embedding-first ordering enough room to
# recover strong semantic matches without opening the full candidate tail.








# Q1/P7: embedding-keyword consistency gate.
#
# A candidate where the dense semantic score (embedding) sits far above the
# lexical score (keyword/BM25) is the structural signature of "same topic,
# different content". In session_20260523_012722 every LLM rejection of an
# allegedly-relevant 凸透镜 candidate fit this pattern: e≥0.7, k≤0.3.
#
# The threshold is intentionally one number per gate, not per subject. The
# gate is bypassed when target/candidate retrieval text shares at least one
# normalized token. That catches cases where the lexical mismatch is accidental
# while still treating a large embedding-vs-keyword gap as a "wrong content"
# signal without burning an LLM call.

# R5: near-miss VLM image verification.
#
# When the text-only LLM reviewer gives a candidate a score that *just*
# misses the accept threshold (within R5_NEAR_MISS_EPSILON), the metadata
# cannot decide between "the picture is actually fine" and "the metadata
# accidentally omits a discriminating tag". The fix is to look at the
# actual image with a VLM. Trade-off: one VLM call (~5-10s) per session
# is far cheaper than the 30s+ image regeneration the rejection would
# otherwise trigger.
#
# Per-session budget is enforced through ``reuse_session_state`` so the
# fallback cannot run away on a session with many near-misses.
# Deterministic LLM reject is now signalled directly by the policy via the
# ``llm_skip_safe`` field on policy_result.
LOGGER = logging.getLogger(__name__)





# ReuseSearchContext moved to reuse/_context.py (re-imported below).
from edupptx.reuse._context import ReuseSearchContext



# Embedding rescue floor. Keyword-sparse candidates can fall below T_REJECT
# even with strong semantic similarity; route those to LLM review instead of
# silently hard-rejecting them.


# BACKGROUND_REUSE_THRESHOLD imported from reuse_policy — single source of truth.

# Single source of truth for "style / form / usage / quality noise tokens
# that saturate the library". Two assets sharing any of these does not
# constitute precision evidence — only sharing a more discriminative
# keyword does.




def update_ai_image_asset_library(
    session_dir: str | Path,
    library_dir: str | Path,
    *,
    db_filename: str = DEFAULT_MATCH_INDEX_FILENAME,
    keyword_client: Any | None = None,
    keyword_batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
    vlm_client: Any | None = None,
    vlm_review: bool = False,
) -> tuple[dict[str, Any], Path]:
    """Copy a session's AI-generated images into the reusable library and merge metadata."""

    session_root = Path(session_dir).expanduser().resolve()
    library_root = Path(library_dir).expanduser().resolve()
    index_path = library_root / db_filename
    library_root.mkdir(parents=True, exist_ok=True)

    existing_db, _existing_path = _read_existing_asset_index(library_root, index_path)
    existing_ids = _asset_ids(existing_db)
    session_db = build_ai_image_asset_db(session_root)
    if existing_ids:
        session_assets = session_db.get("assets")
        if isinstance(session_assets, list):
            fresh_assets = [
                asset
                for asset in session_assets
                if not (isinstance(asset, dict) and _clean_text(asset.get("asset_id")) in existing_ids)
            ]
            skipped_count = len(session_assets) - len(fresh_assets)
            if skipped_count:
                session_db.setdefault("warnings", []).append(
                    f"library ingest skipped {skipped_count} existing asset ids"
                )
            session_db["assets"] = fresh_assets
            session_db["asset_count"] = len(fresh_assets)
    if keyword_client is not None:
        _enrich_unseeded_asset_metadata(
            session_db,
            keyword_client,
            batch_size=keyword_batch_size,
        )

    ingested_db = _copy_db_assets_to_library(
        session_db,
        session_root=session_root,
        library_root=library_root,
    )
    if vlm_review and vlm_client is not None:
        vlm_report = _enrich_split_reuse_groups_with_vlm(
            ingested_db,
            vlm_client,
            keyword_client=keyword_client,
            keyword_batch_size=keyword_batch_size,
            library_root=library_root,
        )
        ingested_db["vlm_review_report"] = vlm_report
    elif vlm_review:
        ingested_db.setdefault("warnings", []).append("VLM review skipped: no VLM client configured")
    merged_db = _merge_asset_library_db(
        existing_db,
        ingested_db,
        library_root=library_root,
    )
    index, index_path = write_ai_image_match_index(
        merged_db,
        library_root,
        index_filename=index_path.name,
    )
    return index, index_path


def ingest_ai_image_asset_job(
    job_payload: dict[str, Any],
    *,
    library_dir: str | Path | None = None,
    db_filename: str = DEFAULT_MATCH_INDEX_FILENAME,
    keyword_client: Any | None = None,
    keyword_batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
    vlm_client: Any | None = None,
    vlm_review: bool | None = None,
) -> tuple[dict[str, Any], Path]:
    """Ingest generated assets described by an asynchronous job payload."""

    payload = job_payload.get("payload") if isinstance(job_payload.get("payload"), dict) else job_payload
    session_root = Path(payload.get("session_dir") or "").expanduser().resolve()
    library_root = Path(library_dir or payload.get("library_dir") or "").expanduser().resolve()
    index_path = library_root / db_filename
    library_root.mkdir(parents=True, exist_ok=True)

    raw_assets = payload.get("assets")
    assets = [dict(asset) for asset in raw_assets if isinstance(asset, dict)] if isinstance(raw_assets, list) else []
    session_db: dict[str, Any] = {
        "schema_version": max(SCHEMA_VERSION, KEYWORD_SCHEMA_VERSION),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "output_root": str(session_root),
        "asset_count": len(assets),
        "assets": assets,
        "warnings": [],
    }

    existing_db, _existing_path = _read_existing_asset_index(library_root, index_path)
    existing_ids = _asset_ids(existing_db)
    if existing_ids:
        fresh_assets = [
            asset
            for asset in assets
            if _clean_text(asset.get("asset_id")) not in existing_ids
        ]
        skipped_count = len(assets) - len(fresh_assets)
        if skipped_count:
            session_db.setdefault("warnings", []).append(
                f"library ingest skipped {skipped_count} existing asset ids"
            )
        session_db["assets"] = fresh_assets
        session_db["asset_count"] = len(fresh_assets)

    if keyword_client is not None:
        _enrich_unseeded_asset_metadata(
            session_db,
            keyword_client,
            batch_size=keyword_batch_size,
        )

    ingested_db = _copy_db_assets_to_library(
        session_db,
        session_root=session_root,
        library_root=library_root,
    )
    should_vlm_review = bool(payload.get("vlm_review")) if vlm_review is None else bool(vlm_review)
    if should_vlm_review and vlm_client is not None:
        vlm_report = _enrich_split_reuse_groups_with_vlm(
            ingested_db,
            vlm_client,
            keyword_client=keyword_client,
            keyword_batch_size=keyword_batch_size,
            library_root=library_root,
        )
        ingested_db["vlm_review_report"] = vlm_report
    elif should_vlm_review:
        ingested_db.setdefault("warnings", []).append("VLM review skipped: no VLM client configured")

    merged_db = _merge_asset_library_db(
        existing_db,
        ingested_db,
        library_root=library_root,
    )
    index, index_path = write_ai_image_match_index(
        merged_db,
        library_root,
        index_filename=index_path.name,
    )
    return index, index_path


def _enrich_unseeded_asset_metadata(
    db: dict[str, Any],
    client: Any,
    *,
    batch_size: int,
) -> dict[str, Any]:
    assets = db.get("assets")
    if not isinstance(assets, list) or not assets:
        return db

    pending_assets = [
        asset
        for asset in assets
        if isinstance(asset, dict) and _asset_needs_library_llm_metadata(asset)
    ]
    if not pending_assets:
        db["schema_version"] = max(int(db.get("schema_version") or 0), KEYWORD_SCHEMA_VERSION)
        db["keyword_built_at"] = datetime.now(timezone.utc).isoformat()
        db["keyword_builder"] = {
            "method": "reuse_target_metadata_seed",
            "batch_size": 0,
            "model": _client_model_name(client),
        }
        return db

    pending_db = {
        **db,
        "assets": pending_assets,
        "asset_count": len(pending_assets),
        "warnings": db.setdefault("warnings", []),
    }
    enrich_ai_image_asset_db_keywords(
        pending_db,
        client,
        batch_size=batch_size,
    )
    for key in ("schema_version", "keyword_built_at", "keyword_builder"):
        if key in pending_db:
            db[key] = pending_db[key]
    return db


def _asset_needs_library_llm_metadata(asset: dict[str, Any]) -> bool:
    if not asset.get(_REUSE_TARGET_METADATA_SEEDED_FIELD):
        return True
    if _is_background_asset(asset):
        required = (
            "normalized_prompt",
            "context_summary",
            "teaching_intent",
            "subject",
            "grade_norm",
            "grade_band",
            "strict_reuse_group",
        )
    else:
        required = (
            "caption",
            "context_summary",
            "teaching_intent",
            "subject",
            "grade_norm",
            "grade_band",
            "strict_reuse_group",
        )
    if any(not _clean_text(asset.get(key)) for key in required):
        return True
    return not isinstance(asset.get("general"), bool)


def ingest_ai_image_asset_library_from_output(
    output_root: str | Path,
    library_dir: str | Path,
    *,
    db_filename: str = DEFAULT_MATCH_INDEX_FILENAME,
    keyword_client: Any | None = None,
    keyword_batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
    vlm_client: Any | None = None,
    vlm_review: bool = False,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    """Ingest all output sessions into the reusable AI image asset library.

    This copies images into the central library image directory and writes the
    slim match index plus embedding sidecars when embedding is available.
    """

    root = Path(output_root).expanduser().resolve()
    library_root = Path(library_dir).expanduser().resolve()
    index_path = library_root / db_filename
    library_root.mkdir(parents=True, exist_ok=True)

    sessions = list(_iter_session_dirs(root))
    report: dict[str, Any] = {
        "output_root": _relative_output_path(root),
        "library_dir": _relative_output_path(library_root),
        "asset_root": _relative_output_path(library_root),
        "match_index_path": _relative_output_path(library_root / STRICT_REUSE_INDEX_DIRNAME),
        "session_count": len(sessions),
        "processed_sessions": [],
        "failed_sessions": [],
        "warnings": [],
    }
    merged_db, _merged_path = _read_existing_asset_index(library_root, index_path)

    for session_dir in sessions:
        try:
            merged_db, index_path = update_ai_image_asset_library(
                session_dir,
                library_root,
                db_filename=db_filename,
                keyword_client=keyword_client,
                keyword_batch_size=keyword_batch_size,
                vlm_client=vlm_client,
                vlm_review=vlm_review,
            )
        except Exception as exc:
            message = f"{_relative_output_path(session_dir)}: {exc}"
            report["failed_sessions"].append(message)
            report["warnings"].append(f"session ingest failed: {message}")
            continue

        session_asset_count = int(merged_db.get("asset_count") or 0)
        report["processed_sessions"].append(
            {
                "session_dir": _relative_output_path(session_dir),
                "asset_count": session_asset_count,
            }
        )

    split_dir = library_root / STRICT_REUSE_INDEX_DIRNAME
    if not split_dir.exists():
        merged_db = _merge_asset_library_db(
            {},
            {"schema_version": SCHEMA_VERSION, "assets": [], "warnings": []},
            library_root=library_root,
        )
        merged_db, index_path = write_ai_image_match_index(
            merged_db,
            library_root,
            index_filename=index_path.name,
        )

    report["asset_count"] = int(merged_db.get("asset_count") or 0)
    report["warning_count"] = len(_as_string_list(merged_db.get("warnings"))) + len(report["warnings"])
    return merged_db, index_path, report


def _enrich_split_reuse_groups_with_vlm(
    db: dict[str, Any],
    vlm_client: Any,
    *,
    keyword_client: Any | None,
    keyword_batch_size: int,
    library_root: Path,
) -> dict[str, Any]:
    from edupptx.materials.vlm_asset_enricher import enrich_assets_with_vlm

    raw_assets = db.get("assets")
    assets = raw_assets if isinstance(raw_assets, list) else []
    grouped: dict[str, list[dict[str, Any]]] = {group: [] for group in STRICT_REUSE_GROUPS}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        group = _normalize_binary_reuse_group(asset.get("strict_reuse_group"), default=_GENERAL_REUSE_GROUP)
        asset["strict_reuse_group"] = group
        grouped[group].append(asset)

    report: dict[str, Any] = {
        "processed_count": 0,
        "failed_count": 0,
        "skipped_reviewed_count": 0,
        "missing_image_count": 0,
        "manual_review_count": 0,
        "auto_rewrite_count": 0,
        "accepted_count": 0,
        "keyword_rewrite_count": 0,
        "group_reports": {},
    }
    for group in STRICT_REUSE_GROUPS:
        group_db = {**db, "assets": grouped[group], "asset_count": len(grouped[group])}
        group_report = enrich_assets_with_vlm(
            group_db,
            vlm_client,
            image_root=library_root,
            debug_dir=library_root / "debug" / group,
            review_index_path=library_root / "debug" / f"ai_image_vlm_review_{group}.json",
            keyword_client=keyword_client,
            keyword_batch_size=keyword_batch_size,
        )
        report["group_reports"][group] = group_report
        for key in (
            "processed_count",
            "failed_count",
            "skipped_reviewed_count",
            "missing_image_count",
            "manual_review_count",
            "auto_rewrite_count",
            "accepted_count",
            "keyword_rewrite_count",
        ):
            report[key] += int(group_report.get(key) or 0)
    return report


































def _select_best_library_reuse_match(matches: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not matches:
        return None

    def rank(match: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
        policy = _dict(match.get("reuse_policy"))
        decision = _clean_text(policy.get("decision"))
        decision_rank = 2.0 if decision in {"direct_reuse", "full_match"} else 1.0 if decision == "generic_support" else 0.0
        score_details = _dict(match.get("score_details"))
        return (
            decision_rank,
            float(match.get("policy_score") or score_details.get("policy_score") or 0.0),
            float(match.get("keyword_score") or 0.0),
            float(match.get("hybrid_score") or score_details.get("hybrid_score") or 0.0),
            float(match.get("embedding_score") or score_details.get("embedding_score") or 0.0),
            -float(match.get("library_search_order") or 0),
        )

    return max(matches, key=rank)
















# Default batch size for the prewarm. Kept aligned with the canonical keyword
# batch size so replay, live generation, and library ingest use the same
# throughput/latency trade-off unless a caller explicitly overrides it.
# Previous experiments used many short batches running in parallel: each LLM
# round-trip is wall-clock bound, so total time ≈ (longest batch latency).

# Concurrency cap for the prewarm thread pool. Tuned so a typical 16-need
# plan fits in 3-4 parallel batches without saturating the upstream API.












# _R5_VLM_BUDGET_LOCK + VLM review moved to reuse/_vlm.py (re-imported below).






















def mark_reused_ai_image_asset_in_session(
    match: dict[str, Any],
    reuse_session_state: dict[str, Any] | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record an accepted match in the current in-memory reuse session state."""

    if reuse_session_state is None:
        return {}
    asset = _dict(match.get("asset"))
    if not _is_strict_reuse_limited_asset(asset):
        return {
            "enabled": True,
            "max_per_session": STRICT_REUSE_MAX_PER_SESSION,
            "limited": False,
            "decision": "not_limited",
        }

    counts = reuse_session_state.setdefault("strict_asset_use_counts", {})
    used_by = reuse_session_state.setdefault("strict_asset_used_by", {})
    ids = _strict_reuse_occupancy_ids(asset)
    used_count_before = max([int(_dict(counts).get(asset_id) or 0) for asset_id in ids] or [0])
    context_payload = context or {}
    for asset_id in ids:
        counts[asset_id] = int(counts.get(asset_id) or 0) + 1
        used_by.setdefault(asset_id, []).append(context_payload)
    used_count_after = max([int(_dict(counts).get(asset_id) or 0) for asset_id in ids] or [0])
    occupancy = {
        "enabled": True,
        "max_per_session": STRICT_REUSE_MAX_PER_SESSION,
        "limited": True,
        "asset_ids": ids,
        "used_count_before": used_count_before,
        "used_count_after": used_count_after,
        "decision": "accepted_within_limit",
    }
    match["strict_reuse_occupancy"] = occupancy
    return occupancy


def materialize_reused_ai_image_asset(
    *,
    session_dir: str | Path,
    session_image_path: str | Path,
    match: dict[str, Any],
) -> None:
    """Copy or derive a reusable image according to its aspect transform policy."""

    dest = Path(session_image_path).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    reuse_image_path = Path(_clean_text(match.get("candidate_image_path"))).expanduser()
    transform_policy = _match_transform_policy(match)
    if _clean_text(transform_policy.get("decision")) == "reject":
        reason = _clean_text(transform_policy.get("reason")) or "aspect_transform_rejected"
        raise ValueError(f"refusing to materialize rejected AI image reuse match: {reason}")
    mode = _clean_text(transform_policy.get("mode")) or "copy"

    try:
        if mode == "copy":
            shutil.copy2(reuse_image_path, dest)
        else:
            _write_transformed_reuse_image(reuse_image_path, dest, transform_policy)
    except Exception:
        if mode == "transparent_pad":
            raise
        shutil.copy2(reuse_image_path, dest)

    record_reused_ai_image_asset(
        session_dir=session_dir,
        session_image_path=dest,
        match=match,
    )


def evaluate_ai_image_reuse_matches_from_plan(
    *,
    plan_path: str | Path,
    library_dir: str | Path | list[str | Path] | tuple[str | Path, ...],
    keyword_client: Any | None = None,
    debug_path: str | Path | None = None,
    include_background: bool = True,
    materialize_matches: bool = False,
    llm_review_enabled: bool = True,
    reuse_debug_mode: str = "full",
    reuse_search_concurrency: int = DEFAULT_REUSE_MAX_WORKERS,
    target_keyword_batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
) -> dict[str, Any]:
    """Evaluate reuse matches from a plan without generating or ingesting assets.

    When ``materialize_matches`` is true, accepted reusable-library matches are
    copied into the plan session's ``materials/`` directory. This still does not
    generate new images or update the central asset library.
    """

    from edupptx.materials.background_generator import build_background_content_prompt
    from edupptx.materials.image_prompt_router import build_routed_image_needs
    from edupptx.models import PlanningDraft, iter_image_slot_keys

    plan_file = Path(plan_path).expanduser().resolve()
    library_roots = _normalize_reuse_library_dirs(library_dir)
    data = json.loads(plan_file.read_text(encoding="utf-8"))
    draft = PlanningDraft.model_validate(data)
    plan_data = draft.model_dump()
    context = {
        "theme": _clean_text(draft.meta.topic),
        "grade": _clean_text(getattr(draft.meta, "grade", "")),
        "subject": _clean_text(getattr(draft.meta, "subject", "")),
        "grade_band": _clean_text(getattr(draft.meta, "grade_band", "")),
    }
    reuse_session_state: dict[str, Any] = {
        "strict_asset_use_counts": {},
        "strict_asset_used_by": {},
    }
    reuse_search_context = ReuseSearchContext()
    reuse_debug_mode = _normalize_reuse_debug_mode(reuse_debug_mode)
    checks: list[dict[str, Any]] = []
    materialized_count = 0
    specs: list[dict[str, Any]] = []
    if include_background:
        background_prompt = build_background_content_prompt(draft.visual)
        specs.append(
            {
                "asset_kind": "background",
                "page_number": None,
                "slot_key": "background",
                "need": None,
                "prompt": background_prompt,
                "prompt_route": None,
                "background_route": _build_background_route(plan_data),
                "page_title": "",
                "page_type": "",
                "role": "",
                "aspect_ratio": "16:9",
                "debug_context": {"check_type": "plan_reuse_match", "asset_kind": "background"},
            }
        )
    for page in draft.pages:
        routed_needs = build_routed_image_needs(draft, page)
        for slot_key, need in iter_image_slot_keys(routed_needs):
            if need.source == "ai_generate":
                specs.append(
                    {
                        "asset_kind": "page_image",
                        "page_number": page.page_number,
                        "slot_key": slot_key,
                        "need": need,
                        "prompt": need.query,
                        "prompt_route": need.prompt_route,
                        "background_route": None,
                        "page_title": page.title,
                        "page_type": page.page_type,
                        "role": need.role,
                        "aspect_ratio": need.aspect_ratio,
                        "debug_context": {
                            "check_type": "plan_reuse_match",
                            "asset_kind": "page_image",
                            "page_number": page.page_number,
                            "slot_key": slot_key,
                            "aspect_ratio": need.aspect_ratio,
                        },
                    }
                )

    total_checks = len(specs)
    page_image_count = sum(1 for spec in specs if spec["asset_kind"] == "page_image")
    reuse_search_concurrency = max(1, int(reuse_search_concurrency or 1))
    PROGRESS_LOGGER.info(
        "AI image reuse plan check start: plan={}, checks={}, background={}, page_images={}, libraries={}, "
        "keywords={}, materialize={}, search_concurrency={}",
        plan_file,
        total_checks,
        bool(include_background),
        page_image_count,
        [str(root) for root in library_roots],
        bool(keyword_client),
        bool(materialize_matches),
        reuse_search_concurrency,
    )

    for root in library_roots:
        _load_reuse_library_for_search(root, reuse_search_context)

    targets = [
        _build_reuse_target_asset(
            asset_kind=spec["asset_kind"],
            prompt=spec["prompt"],
            prompt_route=spec["prompt_route"],
            background_route=spec["background_route"],
            theme=context["theme"],
            grade=context["grade"],
            subject=context["subject"],
            grade_band=context["grade_band"],
            page_title=spec["page_title"],
            page_type=spec["page_type"],
            role=spec["role"],
            aspect_ratio=spec["aspect_ratio"],
        )
        for spec in specs
    ]
    _prewarm_reuse_target_keywords(
        targets,
        keyword_client,
        reuse_search_context.target_keyword_cache,
        batch_size=target_keyword_batch_size,
    )

    def collect_candidates(spec: dict[str, Any], ordinal: int) -> dict[str, Any] | None:
        if spec["asset_kind"] == "background":
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} candidate search start: background prompt={}",
                ordinal,
                total_checks,
                _log_snippet(spec["prompt"], 96),
            )
        else:
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} candidate search start: page={}, slot={}, role={}, aspect={}, query={}",
                ordinal,
                total_checks,
                spec["page_number"],
                spec["slot_key"],
                _clean_text(spec["role"]) or "unknown",
                _clean_text(spec["aspect_ratio"]) or "unknown",
                _log_snippet(spec["prompt"], 96),
            )
        collection = find_reusable_ai_image_asset(
            library_dir=library_dir,
            asset_kind=spec["asset_kind"],
            prompt=spec["prompt"],
            prompt_route=spec["prompt_route"],
            background_route=spec["background_route"],
            theme=context["theme"],
            grade=context["grade"],
            subject=context["subject"],
            grade_band=context["grade_band"],
            page_title=spec["page_title"],
            page_type=spec["page_type"],
            role=spec["role"],
            aspect_ratio=spec["aspect_ratio"],
            keyword_client=None,
            debug_path=None,
            debug_context=spec["debug_context"],
            reuse_session_state=None,
            llm_review_enabled=llm_review_enabled,
            reuse_debug_mode=reuse_debug_mode,
            reuse_search_context=reuse_search_context,
            _collect_candidates_only=True,
        )
        candidate_count = (
            len(collection.get("candidates") or [])
            if isinstance(collection, dict)
            else 0
        )
        PROGRESS_LOGGER.info(
            "AI image reuse check {}/{} candidate search done: asset_kind={}, candidates={}",
            ordinal,
            total_checks,
            spec["asset_kind"],
            candidate_count,
        )
        return collection

    collected: list[dict[str, Any] | None] = [None] * len(specs)
    if specs and reuse_search_concurrency > 1:
        max_workers = min(reuse_search_concurrency, len(specs))
        PROGRESS_LOGGER.info(
            "AI image reuse candidate searches parallel start: checks={}, workers={}",
            len(specs),
            max_workers,
        )
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(collect_candidates, spec, index + 1): index
                for index, spec in enumerate(specs)
            }
            for future in as_completed(futures):
                index = futures[future]
                collected[index] = future.result()
        PROGRESS_LOGGER.info("AI image reuse candidate searches parallel done: checks={}", len(specs))
    else:
        for index, spec in enumerate(specs):
            collected[index] = collect_candidates(spec, index + 1)

    for index, spec in enumerate(specs):
        current_check = index + 1
        if spec["asset_kind"] == "background":
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} policy start: background",
                current_check,
                total_checks,
            )
        else:
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} policy start: page={}, slot={}",
                current_check,
                total_checks,
                spec["page_number"],
                spec["slot_key"],
            )
        match = _finalize_reuse_candidate_collection(
            collected[index],
            debug_path=debug_path,
            keyword_client=keyword_client,
            reuse_session_state=reuse_session_state,
            llm_review_enabled=llm_review_enabled,
            reuse_debug_mode=reuse_debug_mode,
        )
        session_image_path: Path | None = None
        if match:
            if materialize_matches:
                session_image_path = _materialize_plan_reuse_match(
                    session_dir=plan_file.parent,
                    asset_kind=spec["asset_kind"],
                    page_number=spec["page_number"],
                    slot_key=spec["slot_key"],
                    match=match,
                )
                materialized_count += 1
            mark_context = dict(spec["debug_context"])
            mark_context["session_image_path"] = str(session_image_path or "")
            mark_reused_ai_image_asset_in_session(match, reuse_session_state, mark_context)
        checks.append(
            _plan_reuse_check_record(
                spec["asset_kind"],
                spec["page_number"],
                spec["slot_key"],
                spec["need"].model_dump() if spec["need"] is not None else None,
                match,
                session_image_path=session_image_path,
            )
        )
        if spec["asset_kind"] == "background":
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} done: background matched={}, asset_id={}, reason={}, materialized={}",
                current_check,
                total_checks,
                bool(match),
                _match_asset_id(match),
                _match_decision_reason(match),
                bool(session_image_path),
            )
        else:
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} done: page={}, slot={}, matched={}, asset_id={}, score={}, reason={}, "
                "materialized={}",
                current_check,
                total_checks,
                spec["page_number"],
                spec["slot_key"],
                bool(match),
                _match_asset_id(match),
                _match_score(match),
                _match_decision_reason(match),
                bool(session_image_path),
            )

    matched = [item for item in checks if item["matched"]]
    PROGRESS_LOGGER.info(
        "AI image reuse plan check complete: matched={}/{}, materialized={}, debug_path={}",
        len(matched),
        len(checks),
        materialized_count,
        debug_path or "",
    )
    return {
        "schema_version": 1,
        "asset_root": _relative_output_path(library_roots[0]),
        "asset_roots": [_relative_output_path(root) for root in library_roots],
        "generated_images": False,
        "updated_asset_store": False,
        "materialize_matches": materialize_matches,
        "materialized_count": materialized_count,
        "reuse_search_concurrency": reuse_search_concurrency,
        "target_keyword_batch_size": target_keyword_batch_size,
        "materials_dir": _relative_output_path(plan_file.parent / "materials") if materialize_matches else "",
        "check_count": len(checks),
        "matched_count": len(matched),
        "unmatched_count": len(checks) - len(matched),
        "strict_asset_use_counts": reuse_session_state["strict_asset_use_counts"],
        "checks": checks,
    }










def _materialize_plan_reuse_match(
    *,
    session_dir: Path,
    asset_kind: str,
    page_number: int | None,
    slot_key: str,
    match: dict[str, Any],
) -> Path:
    materials_dir = session_dir / "materials"
    if asset_kind == "background":
        dest = materials_dir / "background.png"
    else:
        suffix = Path(_clean_text(match.get("candidate_image_path"))).suffix.lower() or ".img"
        dest = materials_dir / f"page_{int(page_number or 0):02d}_{slot_key}{suffix}"
    materialize_reused_ai_image_asset(
        session_dir=session_dir,
        session_image_path=dest,
        match=match,
    )
    return dest


def _plan_reuse_check_record(
    asset_kind: str,
    page_number: int | None,
    slot_key: str,
    need: dict[str, Any] | None,
    match: dict[str, Any] | None,
    *,
    session_image_path: str | Path | None = None,
) -> dict[str, Any]:
    asset = _dict(match.get("asset")) if match else {}
    return {
        "asset_kind": asset_kind,
        "page_number": page_number,
        "slot_key": slot_key,
        "need": _plan_need_debug_payload(need),
        "matched": match is not None,
        "asset_id": asset.get("asset_id", ""),
        "candidate_image_path": _relative_output_path(match.get("candidate_image_path")) if match else "",
        "reuse_library_dir": _relative_output_path(match.get("library_dir") or match.get("asset_root")) if match else "",
        "session_image_path": _relative_output_path(session_image_path) if session_image_path else "",
        "keyword_score": match.get("keyword_score") if match else None,
        "policy_score": match.get("policy_score") if match else None,
        "reuse_policy": match.get("reuse_policy") if match else {},
        "reuse_audit": match.get("reuse_audit") if match else {},
        "llm_reuse_review_performed": _match_llm_reuse_review_performed(match) if match else False,
        "transform_policy": _match_transform_policy(match) if match else {},
        "strict_reuse_occupancy": match.get("strict_reuse_occupancy") if match else {},
    }


def _plan_need_debug_payload(need: dict[str, Any] | None) -> dict[str, Any]:
    data = _dict(need)
    return {
        key: data.get(key)
        for key in ("query", "role", "aspect_ratio", "prompt_route")
        if key in data
    }




def _match_asset_id(match: dict[str, Any] | None) -> str:
    if not match:
        return ""
    return _clean_text(_dict(match.get("asset")).get("asset_id"))


def _match_score(match: dict[str, Any] | None) -> float | str:
    if not match:
        return ""
    score = match.get("policy_score")
    if score is None:
        score = _dict(match.get("score_details")).get("policy_score")
    if score is None:
        score = match.get("keyword_score")
    if score is None:
        score = _dict(match.get("score_details")).get("score")
    try:
        return round(float(score), 4)
    except (TypeError, ValueError):
        return ""


def _match_decision_reason(match: dict[str, Any] | None) -> str:
    if not match:
        return "no_match"
    policy = _dict(match.get("reuse_policy"))
    return (
        _clean_text(policy.get("reason"))
        or _clean_text(match.get("multi_library_reuse_reason"))
        or "matched"
    )








def _write_transformed_reuse_image(input_path: Path, dest: Path, transform_policy: dict[str, Any]) -> None:
    from PIL import Image

    mode = _clean_text(transform_policy.get("mode")) or "copy"
    target_ratio = _ratio_value(_clean_text(transform_policy.get("target_aspect_ratio")))
    with Image.open(input_path) as img:
        image = img.convert("RGBA") if img.mode not in {"RGB", "RGBA"} else img.copy()
        if target_ratio <= 0:
            image.save(dest)
            return

        if mode == "cover_crop":
            result = _cover_crop_image(image, target_ratio)
        elif mode == "transparent_pad":
            result = _transparent_pad_image(image, target_ratio, _target_size_from_transform_policy(transform_policy))
        elif mode == "contain_pad":
            result = _contain_pad_image(image, target_ratio)
        elif mode == "blur_pad":
            result = _blur_pad_image(image, target_ratio)
        elif mode == "micro_stretch":
            result = _micro_stretch_image(image, target_ratio)
        else:
            result = image

        if dest.suffix.lower() in {".jpg", ".jpeg"} and result.mode == "RGBA":
            background = Image.new("RGB", result.size, _average_rgb(result))
            background.paste(result, mask=result.getchannel("A"))
            result = background
        result.save(dest)


def _target_size_from_transform_policy(transform_policy: dict[str, Any]) -> tuple[int, int] | None:
    width = _optional_int(transform_policy.get("target_width"))
    height = _optional_int(transform_policy.get("target_height"))
    if width and height and width > 0 and height > 0:
        return width, height
    return None


def _cover_crop_image(image: Any, target_ratio: float) -> Any:
    width, height = image.size
    image_ratio = width / max(1, height)
    if image_ratio > target_ratio:
        crop_width = max(1, int(round(height * target_ratio)))
        left = max(0, (width - crop_width) // 2)
        return image.crop((left, 0, left + crop_width, height))
    crop_height = max(1, int(round(width / target_ratio)))
    top = max(0, (height - crop_height) // 2)
    return image.crop((0, top, width, top + crop_height))


def _transparent_pad_image(image: Any, target_ratio: float, target_size: tuple[int, int] | None = None) -> Any:
    from PIL import Image

    source = image.convert("RGBA")
    width, height = source.size
    if target_size is None:
        canvas_width, canvas_height = _contain_canvas_size(width, height, target_ratio)
    else:
        canvas_width, canvas_height = target_size

    scale = min(canvas_width / max(1, width), canvas_height / max(1, height))
    scaled_width = max(1, int(round(width * scale)))
    scaled_height = max(1, int(round(height * scale)))
    if (scaled_width, scaled_height) != source.size:
        source = source.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    left = (canvas_width - scaled_width) // 2
    top = (canvas_height - scaled_height) // 2
    canvas.paste(source, (left, top), source)
    return canvas


def _contain_pad_image(image: Any, target_ratio: float) -> Any:
    from PIL import Image

    width, height = image.size
    canvas_width, canvas_height = _contain_canvas_size(width, height, target_ratio)
    canvas = Image.new(image.mode, (canvas_width, canvas_height), _average_rgba(image))
    left = (canvas_width - width) // 2
    top = (canvas_height - height) // 2
    canvas.paste(image, (left, top), image if image.mode == "RGBA" else None)
    return canvas


def _blur_pad_image(image: Any, target_ratio: float) -> Any:
    from PIL import ImageFilter

    width, height = image.size
    canvas_width, canvas_height = _contain_canvas_size(width, height, target_ratio)
    background = image.convert("RGB").resize((canvas_width, canvas_height))
    background = background.filter(ImageFilter.GaussianBlur(radius=max(8, min(canvas_width, canvas_height) // 24)))
    foreground = image.convert("RGBA")
    background = background.convert("RGBA")
    left = (canvas_width - width) // 2
    top = (canvas_height - height) // 2
    background.paste(foreground, (left, top), foreground)
    return background


def _micro_stretch_image(image: Any, target_ratio: float) -> Any:
    width, height = image.size
    area = max(1, width * height)
    target_width = max(1, int(round(math.sqrt(area * target_ratio))))
    target_height = max(1, int(round(target_width / target_ratio)))
    return image.resize((target_width, target_height))


def _contain_canvas_size(width: int, height: int, target_ratio: float) -> tuple[int, int]:
    image_ratio = width / max(1, height)
    if image_ratio > target_ratio:
        return width, max(height, int(round(width / target_ratio)))
    return max(width, int(round(height * target_ratio))), height


def _average_rgba(image: Any) -> tuple[int, int, int, int]:
    rgb = _average_rgb(image)
    return rgb[0], rgb[1], rgb[2], 255


def _average_rgb(image: Any) -> tuple[int, int, int]:
    from PIL import ImageStat

    stat = ImageStat.Stat(image.convert("RGB").resize((1, 1)))
    return tuple(int(value) for value in stat.mean[:3])












# _REUSE_DEBUG_LOCK + debug-record writers moved to reuse/_debug.py (re-imported below).














































def _effective_grade_band(asset: dict[str, Any]) -> str:
    """存量资产 band=其他 但 grade_norm 已知时，派生出有效 band（避免幽灵 unknown 拦截）。"""
    band = _normalize_grade_band_value(asset.get("grade_band"))
    if band != _OTHER_GRADE:
        return band
    return grade_band_from_norm(asset.get("grade_norm"))








def _target_metadata_unknown_fields(asset: dict[str, Any]) -> list[str]:
    unknown: list[str] = []
    if _normalize_subject_value(asset.get("subject")) == _OTHER_SUBJECT:
        unknown.append("subject")
    if _normalize_grade_norm_value(asset.get("grade_norm")) == _OTHER_GRADE:
        unknown.append("grade_norm")
    if _effective_grade_band(asset) == _OTHER_GRADE:
        unknown.append("grade_band")
    return unknown


def _target_unknown_fields_for_reuse(asset: dict[str, Any]) -> list[str]:
    ignored = {"subject", "grade_norm", "grade_band"}
    return [field for field in _target_metadata_unknown_fields(asset) if field not in ignored]


def _candidate_unknown_fields_for_reuse(
    asset: dict[str, Any],
    subject_decision: dict[str, Any],
) -> list[str]:
    unknown = _target_metadata_unknown_fields(asset)
    ignored = {"subject", "grade_norm", "grade_band"}
    return [field for field in unknown if field not in ignored]
















def _clean_semantic_aliases(value: Any) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    if not isinstance(value, dict):
        return aliases
    for raw_key, raw_values in value.items():
        key = _clean_keyword(raw_key)
        if not key:
            continue
        terms = _keyword_list(raw_values, max_items=6)
        if terms:
            aliases[key] = terms
    return aliases


def _merge_semantic_aliases(*items: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for aliases in items:
        for key, values in aliases.items():
            clean_key = _clean_keyword(key)
            if not clean_key:
                continue
            merged[clean_key] = _dedupe_terms([*merged.get(clean_key, []), *values])[:8]
    return merged










def _context_exclusions(asset: dict[str, Any]) -> set[str]:
    grade = _clean_text(asset.get("grade"))
    subject = _clean_text(asset.get("subject"))
    grade_info = _grade_info_from_asset(asset)
    exclusions = {
        grade,
        _clean_text(asset.get("grade_norm")),
        _clean_text(asset.get("grade_band")),
        _clean_text(grade_info.get("grade_norm")),
        _clean_text(grade_info.get("grade_band")),
        subject,
        _unit_ref_for_asset(asset),
    }
    if grade and subject:
        exclusions.add(f"{grade}{subject}")
        exclusions.add(f"{grade} {subject}")
    grade_norm = _clean_text(grade_info.get("grade_norm"))
    if grade_norm and subject:
        exclusions.add(f"{grade_norm}{subject}")
        exclusions.add(f"{grade_norm} {subject}")
    return {item for item in exclusions if item}




















from edupptx.reuse._backend import (
    _ASSET_STORE_CACHE,
    _ASSET_STORE_LOCK,
    _get_asset_store,
    _reuse_backend,
    _use_sqlite_backend,
)
from edupptx.reuse import _embedding as _reuse_embedding
from edupptx.reuse._embedding import (
    _EMBEDDING_MODEL_CACHE,
    _EMBEDDING_MODEL_LOCK,
    _embedding_missing_caption_review_item,
    _embedding_model_name,
    _embedding_model_sidecar_matches,
    _embedding_query_text,
    _embedding_refs_match,
    _embedding_sidecar_model_name,
    _embedding_text_hash,
    _encode_embedding_texts,
    _ensure_ai_image_embedding_index,
    _load_embedding_model,
    _read_ai_image_embedding_index,
    _read_npz_embedding_index,
    _relative_output_path,
    _write_embedding_missing_caption_review,
    write_ai_image_embedding_index,
)
from edupptx.reuse._store import (
    _are_match_assets_duplicates,
    _assemble_match_index_from_group_payloads,
    _background_route_match_terms,
    _background_route_terms,
    _c01_secondary_c03_projections,
    _dedupe_bucket_key,
    _dedupe_match_assets,
    _dedupe_warnings,
    _default_context_summary,
    _default_normalized_prompt,
    _default_teaching_intent,
    _fallback_context_summary,
    _file_sha256,
    _grade_info_from_asset,
    _image_dimension_fields,
    _is_skip_reuse_group,
    _match_asset_quality_score,
    _match_asset_similarity,
    _match_background_route,
    _match_prompt_route,
    _merge_skip_group_assets,
    _normalize_asset_for_match,
    _normalize_rich_asset_fields,
    _preserve_review_fields,
    _ratio_orientation,
    _read_match_index_or_build,
    _read_split_group_assets,
    _resolve_asset_image_path,
    _route_grade_family,
    _route_match_index_for_target,
    _route_match_text,
    _strip_background_color_bias_from_prompt,
    _strip_empty_match_fields,
    build_ai_image_match_index,
    read_ai_image_split_match_index,
    write_ai_image_split_match_indexes,
)
from edupptx.reuse._retrieve import (
    _candidate_hybrid_text,
    _debug_score_details,
    _load_query_embedding_disk_cache,
    _query_embedding_cache_paths,
    _rank_embedding_candidates,
    _rank_hybrid_reuse_candidates,
    _rank_reuse_candidates,
    _rank_substring_candidates,
    _target_embedding_text,
    _write_query_embedding_disk_cache,
)
from edupptx.reuse._review import (
    REUSE_REVIEW_SCORE_RULES_REFERENCE,
    _build_reuse_review_messages,
    _clamp_score,
    _load_reuse_review_score_rules_reference,
    _log_snippet,
    _normalize_reuse_review_score_response,
    _reuse_debug_asset_payload,
    _reuse_review_accept_score_threshold,
    _review_reuse_candidate_with_llm,
)
from edupptx.reuse._build import (
    _apply_reuse_target_metadata_seed,
    _build_background_asset,
    _build_background_route,
    _build_reuse_target_asset,
    _extract_context,
    _find_page_image_path,
    _is_reused_image_path,
    _iter_page_image_assets,
    _iter_session_dirs,
    _load_reused_image_paths,
    _load_session_reuse_target_keyword_cache,
    _make_asset,
    _metadata_seed_from_reuse_target,
    _read_json,
    _relative_path,
    _target_keyword_cache_key,
    build_ai_image_asset_db,
    normalize_grade_info,
    write_ai_image_match_index,
)
from edupptx.reuse._debug import (
    _REUSE_DEBUG_LOCK,
    _append_reuse_debug_record,
    _collect_reuse_candidate_debug,
    _flat_reuse_audit_fields,
    _new_reuse_debug_record,
    _optional_float,
    _relative_output_context,
    _reuse_debug_candidate_payload,
    _reuse_debug_candidate_summary,
    _reuse_debug_record_for_mode,
    _reuse_no_match_top_candidate_summaries,
)
from edupptx.reuse._keywords import (
    _apply_general_from_payload,
    _apply_keyword_payload,
    _apply_strict_reuse_group_from_payload,
    _build_keyword_messages,
    _build_match_key,
    _build_match_text,
    _call_keyword_llm,
    _enrich_reuse_target_keywords_once,
    _grade_info_from_payload,
    _keyword_payload_by_asset_id,
    _load_keyword_reuse_rules_reference,
    _prewarm_reuse_target_keywords,
    _reuse_target_keyword_batch_size,
    _reuse_target_keyword_workers,
    enrich_ai_image_asset_db_keywords,
)
from edupptx.reuse._vlm import (
    _R5_VLM_BUDGET_LOCK,
    _r5_try_reserve_session_vlm_budget,
    _review_reuse_candidate_with_vlm,
)
from edupptx.reuse._decide import (
    EMBED_RESCUE_FLOOR,
    _apply_reuse_policy_to_ranked_candidates,
    _build_reuse_library_payload,
    _eligible_reuse_assets,
    _embedding_keyword_gap_reject,
    _embedding_rescue_decision,
    _finalize_reuse_candidate_collection,
    _get_llm_max_workers,
    _global_reuse_candidate_rank,
    _has_structural_evidence,
    _is_strict_reuse_limited_asset,
    _llm_review_priority,
    _load_reuse_library_for_search,
    _match_llm_reuse_review_performed,
    _match_transform_policy,
    _maybe_float_score,
    _normalize_reuse_debug_mode,
    _normalize_reuse_library_dirs,
    _reuse_accept_reason,
    _reuse_audit_payload,
    _reuse_collection_empty_reason,
    _reuse_review_accepts,
    _reuse_route_key_for_target,
    _reuse_size_distance,
    _reuse_static_filter_reject_reason,
    _reuse_threshold_for_target,
    _review_keyword_score,
    _review_score_value,
    _review_worker_count,
    _route_match_index_for_target_cached,
    _strict_reuse_occupancy_ids,
    _strict_reuse_occupancy_status,
    _transform_rejects_candidate,
    find_reusable_ai_image_asset,
    record_reused_ai_image_asset,
)
























def _clean_core_keyword_terms(terms: list[str]) -> tuple[list[str], list[str]]:
    core_terms: list[str] = []
    style_terms: list[str] = []
    for term in terms:
        if _is_generic_core_term(term):
            continue
        if _looks_like_style_or_usage_term(term):
            style_terms.append(term)
            extracted = _extract_entity_from_visual_style_term(term)
            if extracted and not _is_generic_core_term(extracted):
                core_terms.append(extracted)
            continue
        core_terms.append(term)
    return _dedupe_terms(core_terms), _dedupe_terms(style_terms)


def _is_generic_core_term(term: str) -> bool:
    normalized = _clean_keyword(term).casefold().replace(" ", "")
    if not normalized:
        return True
    return normalized in {item.casefold().replace(" ", "") for item in _NOISE_TOKENS}


def _looks_like_style_or_usage_term(term: str) -> bool:
    normalized = _clean_keyword(term).casefold().replace(" ", "")
    if not normalized:
        return False
    if any(marker.casefold() in normalized for marker in _CORE_USAGE_MARKERS):
        return True
    if any(marker.casefold() in normalized for marker in _CORE_STYLE_MARKERS):
        return True
    if any(form.casefold() in normalized for form in _VISUAL_FORM_MARKERS) and any(
        marker.casefold() in normalized for marker in _STYLE_DESCRIPTOR_MARKERS
    ):
        return True
    return False


def _extract_entity_from_visual_style_term(term: str) -> str:
    cleaned = _clean_keyword(term)
    if not cleaned:
        return ""
    compact = cleaned.replace(" ", "")
    for marker in _STYLE_DESCRIPTOR_MARKERS:
        compact = compact.replace(marker, "")
    for marker in _CORE_STYLE_MARKERS:
        compact = compact.replace(marker, "")
    for marker in _VISUAL_FORM_MARKERS:
        if compact.endswith(marker):
            compact = compact[: -len(marker)]
    return _clean_keyword(compact)


















































































def _subject_scope_compatible(target_subject: Any, candidate_subject: Any) -> bool:
    return bool(_subject_scope_decision(target_subject, candidate_subject)["compatible"])






















def _semantic_terms(
    asset: dict[str, Any],
    field: str,
    *,
    fallback_fields: tuple[str, ...] = (),
    max_items: int = 12,
) -> list[str]:
    terms = _keyword_list(asset.get(field), max_items=max_items)
    if not terms:
        for fallback in fallback_fields:
            terms.extend(_keyword_list(asset.get(fallback), max_items=max_items))
            if terms:
                break
    return _dedupe_terms(terms)[:max_items]


def _semantic_coverage(
    target_terms: list[str],
    candidate_terms: list[str],
    *,
    neutral: float,
) -> tuple[float, list[dict[str, str]], list[str]]:
    if not target_terms:
        return neutral, [], []
    if not candidate_terms:
        return 0.0, [], target_terms
    score, hits = _overlap_score_with_hits(target_terms, candidate_terms)
    matched = {_clean_keyword(item.get("target")) for item in hits}
    missing = [term for term in target_terms if _clean_keyword(term) not in matched]
    return score, hits, missing










def _overlap_score(target_terms: list[str], candidate_terms: list[str]) -> float:
    score, _hits = _overlap_score_with_hits(target_terms, candidate_terms)
    return score


def _overlap_score_with_hits(
    target_terms: list[str],
    candidate_terms: list[str],
) -> tuple[float, list[dict[str, str]]]:
    if not target_terms or not candidate_terms:
        return 0.0, []
    hits: list[dict[str, str]] = []
    for target in target_terms:
        matched = next((candidate for candidate in candidate_terms if _terms_match(target, candidate)), "")
        if matched:
            hits.append({"target": target, "candidate": matched})
    return len(hits) / len(target_terms), hits


def _terms_match(left: str, right: str) -> bool:
    left = _clean_keyword(left)
    right = _clean_keyword(right)
    if not left or not right:
        return False
    if left == right:
        return True
    return min(len(left), len(right)) >= 2 and (left in right or right in left)










# Page type values that mark a page_image slot as serving an ambience
# purpose rather than precise content. Used by ``_target_is_background_like``
# instead of substring matching against arbitrary slot strings.


def _target_is_background_like(target: dict[str, Any]) -> bool:
    """True iff the target's page_type declares it as a backdrop slot.

    Single helper so the "background-like" classification has one place to
    maintain. Matches exact token equality (after casefold), not substring
    containment, to avoid false-positives like "background_decoration".
    """

    value = _clean_text(_dict(target).get("page_type")).casefold()
    return bool(value and value in _BACKGROUND_LIKE_ROLE_TOKENS)


def _reuse_gate_profile(target: dict[str, Any] | None) -> str:
    if target is None:
        return "medium"
    if _clean_text(target.get("asset_kind")) == "background":
        return "background"
    policy = normalize_reuse_policy_fields(_dict(target))
    group = _normalize_binary_reuse_group(_dict(target).get("strict_reuse_group"), default="")
    has_strict_knowledge = group in {
        "C01_irreplaceable_entity_event_action",
    }
    # Background-like page_image slot: declared via page_type by
    # ``_target_is_background_like``. Treated as ambience (loose) rather
    # than precise content for LLM-review purposes. Guarded by absence of
    # strict knowledge so a "background_1 with 写字" slot keeps strict.
    if _target_is_background_like(_dict(target)) and not has_strict_knowledge:
        return "loose"

    level = _clean_text(policy.get("reuse_level")) or "medium"
    if level == "strict":
        return "strict_knowledge"
    return level if level in {"loose", "medium"} else "medium"



def _reuse_gate_thresholds_for_target(target: dict[str, Any] | None) -> dict[str, float]:
    profile = _reuse_gate_profile(target)
    if profile == "background":
        return BACKGROUND_REUSE_GATE_THRESHOLDS
    return PAGE_IMAGE_REUSE_GATE_THRESHOLDS.get(profile, PAGE_IMAGE_REUSE_GATE_THRESHOLDS["medium"])


def _is_text_overlap_review_slot(target: dict[str, Any] | None, candidate: dict[str, Any]) -> bool:
    candidate_asset = _dict(candidate.get("asset")) or _dict(candidate)
    groups = {
        _normalize_binary_reuse_group(_dict(target).get("strict_reuse_group"), default="") if target is not None else "",
        _normalize_binary_reuse_group(candidate_asset.get("strict_reuse_group"), default=""),
    }
    return bool(groups & {
        "C01_irreplaceable_entity_event_action",
    })


def _reuse_gate_reason(
    *,
    target: dict[str, Any] | None,
    candidate: dict[str, Any],
    keyword_score: float,
    embedding_score: float,
    substring_score: float,
) -> str:
    if _transform_rejects_candidate(candidate):
        return ""
    thresholds = _reuse_gate_thresholds_for_target(target)
    if keyword_score < thresholds["keyword_min"] and embedding_score < thresholds["embedding_min"]:
        return ""
    if keyword_score >= thresholds["keyword_high"]:
        return "keyword_high_review"
    if embedding_score >= thresholds["embedding_high"]:
        return "embedding_high_review"
    if (
        _is_text_overlap_review_slot(target, candidate)
        and substring_score >= TEXT_OVERLAP_REVIEW_THRESHOLD
        and embedding_score >= TEXT_OVERLAP_EMBEDDING_THRESHOLD
    ):
        return "text_overlap_embedding_review"
    if keyword_score >= thresholds["keyword_gray_high"] and embedding_score >= thresholds["embedding_gray_low"]:
        return "keyword_led_gray_review"
    if embedding_score >= thresholds["embedding_gray_high"] and keyword_score >= thresholds["keyword_gray_low"]:
        return "embedding_led_gray_review"
    return ""




def _reuse_acceptance_reason(
    candidate: dict[str, Any],
    threshold: float | None = None,
    *,
    target: dict[str, Any] | None = None,
) -> str:
    threshold = VISUAL_GENERIC_REUSE_THRESHOLD if threshold is None else float(threshold)
    if _transform_rejects_candidate(candidate):
        return ""
    if candidate.get("background_reuse_score") is not None:
        keyword_score = float(candidate.get("background_reuse_score") or candidate.get("keyword_score") or 0.0)
        embedding_score = float(candidate.get("embedding_score") or 0.0)
        substring_score = float(candidate.get("substring_score") or 0.0)
        return (
            "background_threshold"
            if _reuse_gate_reason(
                target=target,
                candidate=candidate,
                keyword_score=keyword_score,
                embedding_score=embedding_score,
                substring_score=substring_score,
            )
            else ""
        )

    bm25_score = float(candidate.get("keyword_score") or 0.0)
    embedding_score = float(candidate.get("embedding_score") or 0.0)
    substring_score = float(candidate.get("substring_score") or 0.0)
    thresholds = _reuse_gate_thresholds_for_target(target)
    if bm25_score >= threshold and embedding_score >= thresholds["embedding_min"]:
        return "bm25_threshold"
    gate_reason = _reuse_gate_reason(
        target=target,
        candidate=candidate,
        keyword_score=bm25_score,
        embedding_score=embedding_score,
        substring_score=substring_score,
    )
    if gate_reason:
        return gate_reason
    if _is_strict_embedding_review_candidate(target, candidate, embedding_score):
        return "strict_embedding_review"
    if _is_strict_semantic_gray_review_candidate(
        target,
        candidate,
        bm25_score=bm25_score,
        embedding_score=embedding_score,
        substring_score=substring_score,
    ):
        return "strict_semantic_gray_review"
    if bm25_score >= BM25_GRAY_REUSE_THRESHOLD and embedding_score >= EMBEDDING_GRAY_REUSE_THRESHOLD:
        return "embedding_gray_zone"
    if bm25_score >= max(0.0, threshold - 0.03) and substring_score >= 0.35 and embedding_score >= 0.62:
        return "substring_embedding_gray_zone"
    if _is_medium_embedding_review_candidate(target, candidate, embedding_score):
        return "medium_embedding_review"
    return ""


def _is_strict_embedding_review_candidate(
    target: dict[str, Any] | None,
    candidate: dict[str, Any],
    embedding_score: float,
) -> bool:
    # Threshold inlined from former reuse_policy.STRICT_EMBEDDING_REVIEW_THRESHOLD
    if embedding_score < 0.78:
        return False
    asset = _dict(candidate.get("asset"))
    if _clean_text(asset.get("asset_kind")) == "background":
        return False
    policies = [normalize_reuse_policy_fields(asset)]
    if target is not None:
        policies.append(normalize_reuse_policy_fields(_dict(target)))
    return any(policy.get("reuse_level") == "strict" for policy in policies)


def _is_strict_semantic_gray_review_candidate(
    target: dict[str, Any] | None,
    candidate: dict[str, Any],
    *,
    bm25_score: float,
    embedding_score: float,
    substring_score: float,
) -> bool:
    # Thresholds inlined from former reuse_policy constants
    if target is None:
        return False
    if embedding_score < 0.70:  # STRICT_SEMANTIC_GRAY_REVIEW_THRESHOLD
        return False
    if bm25_score < 0.20 and substring_score < 0.25:  # STRICT_SEMANTIC_GRAY_BM25_THRESHOLD
        return False

    asset = _dict(candidate.get("asset"))
    if _clean_text(asset.get("asset_kind")) == "background":
        return False

    target_theme = _clean_text(target.get("theme"))
    candidate_theme = _clean_text(asset.get("theme"))
    if not (target_theme and candidate_theme and target_theme == candidate_theme):
        return False

    policies = [
        normalize_reuse_policy_fields(asset),
        normalize_reuse_policy_fields(_dict(target)),
    ]
    return any(policy.get("reuse_level") == "strict" for policy in policies)


def _is_medium_embedding_review_candidate(
    target: dict[str, Any] | None,
    candidate: dict[str, Any],
    embedding_score: float,
) -> bool:
    # Threshold inlined from former reuse_policy.MEDIUM_EMBEDDING_REVIEW_THRESHOLD
    if embedding_score < 0.80:
        return False
    asset = _dict(candidate.get("asset"))
    if _clean_text(asset.get("asset_kind")) == "background":
        return False
    policies = [normalize_reuse_policy_fields(asset)]
    if target is not None:
        policies.append(normalize_reuse_policy_fields(_dict(target)))
    levels = {_clean_text(policy.get("reuse_level")) for policy in policies}
    return "strict" not in levels and bool(levels & {"loose", "medium"})






def _aspect_ratio_score(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    target_ratio = normalize_aspect_bucket(_asset_aspect_ratio_label(target))
    candidate_ratio = normalize_aspect_bucket(_asset_aspect_ratio_label(candidate))
    if not target_ratio or not candidate_ratio:
        return 0.5
    if target_ratio == candidate_ratio:
        return 1.0
    target_orientation = _ratio_orientation(target_ratio)
    candidate_orientation = _ratio_orientation(candidate_ratio)
    return 0.6 if target_orientation and target_orientation == candidate_orientation else 0.2






def _aspect_ratio_diff(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    t = _aspect_ratio_value(target)
    c = _aspect_ratio_value(candidate)
    if t <= 0 or c <= 0:
        return 1.0
    return abs(t - c) / t








def _copy_db_assets_to_library(
    db: dict[str, Any],
    *,
    session_root: Path,
    library_root: Path,
) -> dict[str, Any]:
    copied = deepcopy(db)
    image_dir = library_root / DEFAULT_LIBRARY_IMAGE_DIR
    image_dir.mkdir(parents=True, exist_ok=True)

    copied_assets: list[dict[str, Any]] = []
    warnings = copied.setdefault("warnings", [])

    for asset in copied.get("assets", []):
        if not isinstance(asset, dict):
            continue
        asset_id = _clean_text(asset.get("asset_id"))
        if _is_skip_reuse_group(asset.get("strict_reuse_group")):
            warnings.append(f"library ingest skipped C00 asset: {asset_id or '<missing asset_id>'}")
            continue
        input_image_path = _resolve_asset_image_path(session_root, asset.get("image_path"))
        if not asset_id or input_image_path is None or not input_image_path.exists():
            warnings.append(f"library ingest skipped missing image for {asset_id or '<missing asset_id>'}")
            continue

        dest_rel = f"{DEFAULT_LIBRARY_IMAGE_DIR}/{asset_id}.png"
        dest_path = library_root / dest_rel

        asset["image_path"] = dest_rel
        _normalize_rich_asset_fields(asset)
        _save_reusable_png_with_transparent_padding(
            input_image_path,
            dest_path,
            aspect_bucket=asset.get("aspect_bucket") or asset.get("aspect_ratio"),
        )
        copied_assets.append(asset)

    copied["output_root"] = str(library_root)
    copied["assets"] = copied_assets
    copied["asset_count"] = len(copied_assets)
    return copied


def _save_reusable_png_with_transparent_padding(
    input_path: Path,
    dest_path: Path,
    *,
    aspect_bucket: Any,
) -> None:
    """Persist a reusable-library image as PNG, padding to the bucket with transparency."""

    from PIL import Image

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    bucket = normalize_aspect_bucket(aspect_bucket)
    target_ratio = _ratio_value(bucket)
    with Image.open(input_path) as img:
        image = img.convert("RGBA")
        if target_ratio > 0:
            canvas_width, canvas_height = _contain_canvas_size(image.width, image.height, target_ratio)
            if canvas_width != image.width or canvas_height != image.height:
                canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
                left = (canvas_width - image.width) // 2
                top = (canvas_height - image.height) // 2
                canvas.paste(image, (left, top), image)
                image = canvas
        image.save(dest_path, format="PNG", optimize=True)


def _read_existing_asset_index(library_root: Path, index_path: Path) -> tuple[dict[str, Any], Path]:
    split = read_ai_image_split_match_index(library_root)
    if split is not None:
        return split
    return _read_existing_db(index_path), index_path






























def _merge_asset_library_db(
    existing: dict[str, Any],
    incoming: dict[str, Any],
    *,
    library_root: Path,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    by_id: dict[str, dict[str, Any]] = {}

    for asset in existing.get("assets", []):
        if isinstance(asset, dict):
            if _is_skip_reuse_group(asset.get("strict_reuse_group")):
                continue
            asset_id = _clean_text(asset.get("asset_id"))
            if asset_id:
                by_id[asset_id] = asset

    for asset in incoming.get("assets", []):
        if isinstance(asset, dict):
            if _is_skip_reuse_group(asset.get("strict_reuse_group")):
                continue
            asset_id = _clean_text(asset.get("asset_id"))
            if asset_id and asset_id not in by_id:
                by_id[asset_id] = asset

    assets = []
    for asset in by_id.values():
        normalized_asset = deepcopy(asset)
        _normalize_rich_asset_fields(normalized_asset)
        if _is_skip_reuse_group(normalized_asset.get("strict_reuse_group")):
            continue
        assets.append(normalized_asset)

    assets = sorted(
        assets,
        key=lambda item: (
            _clean_text(item.get("asset_kind")),
            _clean_text(item.get("image_path")),
            _clean_text(item.get("asset_id")),
        ),
    )
    schema_version = max(
        int(existing.get("schema_version") or 0),
        int(incoming.get("schema_version") or 0),
        SCHEMA_VERSION,
    )
    merged: dict[str, Any] = {
        "schema_version": schema_version,
        "built_at": existing.get("built_at") or incoming.get("built_at") or now,
        "updated_at": now,
        "output_root": str(library_root),
        "asset_count": len(assets),
        "assets": assets,
        "warnings": _dedupe_warnings(
            [
                *(_as_string_list(existing.get("warnings"))),
                *(_as_string_list(incoming.get("warnings"))),
            ]
        ),
    }
    keyword_built_at = incoming.get("keyword_built_at") or existing.get("keyword_built_at")
    keyword_builder = incoming.get("keyword_builder") or existing.get("keyword_builder")
    if keyword_built_at:
        merged["keyword_built_at"] = keyword_built_at
    if keyword_builder:
        merged["keyword_builder"] = keyword_builder
    return merged


def _asset_ids(db: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    assets = db.get("assets")
    if not isinstance(assets, list):
        return ids
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_id = _clean_text(asset.get("asset_id"))
        if asset_id:
            ids.add(asset_id)
    return ids








































































