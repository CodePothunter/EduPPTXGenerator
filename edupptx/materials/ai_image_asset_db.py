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
































































# Default batch size for the prewarm. Kept aligned with the canonical keyword
# batch size so replay, live generation, and library ingest use the same
# throughput/latency trade-off unless a caller explicitly overrides it.
# Previous experiments used many short batches running in parallel: each LLM
# round-trip is wall-clock bound, so total time ≈ (longest batch latency).

# Concurrency cap for the prewarm thread pool. Tuned so a typical 16-need
# plan fits in 3-4 parallel batches without saturating the upstream API.












# _R5_VLM_BUDGET_LOCK + VLM review moved to reuse/_vlm.py (re-imported below).






















































































# _REUSE_DEBUG_LOCK + debug-record writers moved to reuse/_debug.py (re-imported below).
























































# _target_unknown_fields_for_reuse / _candidate_unknown_fields_for_reuse moved to reuse/_gates.py.
















































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
from edupptx.reuse._materialize import (
    _average_rgb,
    _average_rgba,
    _blur_pad_image,
    _contain_canvas_size,
    _contain_pad_image,
    _cover_crop_image,
    _match_asset_id,
    _match_decision_reason,
    _match_score,
    _materialize_plan_reuse_match,
    _micro_stretch_image,
    _plan_need_debug_payload,
    _plan_reuse_check_record,
    _target_size_from_transform_policy,
    _transparent_pad_image,
    _write_transformed_reuse_image,
    evaluate_ai_image_reuse_matches_from_plan,
    mark_reused_ai_image_asset_in_session,
    materialize_reused_ai_image_asset,
)
from edupptx.reuse._ingest import (
    _asset_ids,
    _asset_needs_library_llm_metadata,
    _copy_db_assets_to_library,
    _enrich_split_reuse_groups_with_vlm,
    _enrich_unseeded_asset_metadata,
    _merge_asset_library_db,
    _read_existing_asset_index,
    _save_reusable_png_with_transparent_padding,
    ingest_ai_image_asset_job,
    ingest_ai_image_asset_library_from_output,
    update_ai_image_asset_library,
)
from edupptx.reuse._gates import (
    _aspect_ratio_diff,
    _aspect_ratio_score,
    _candidate_unknown_fields_for_reuse,
    _target_unknown_fields_for_reuse,
    _clean_core_keyword_terms,
    _clean_semantic_aliases,
    _context_exclusions,
    _effective_grade_band,
    _extract_entity_from_visual_style_term,
    _is_generic_core_term,
    _is_medium_embedding_review_candidate,
    _is_strict_embedding_review_candidate,
    _is_strict_semantic_gray_review_candidate,
    _is_text_overlap_review_slot,
    _looks_like_style_or_usage_term,
    _merge_semantic_aliases,
    _overlap_score,
    _overlap_score_with_hits,
    _reuse_acceptance_reason,
    _reuse_gate_profile,
    _reuse_gate_reason,
    _reuse_gate_thresholds_for_target,
    _select_best_library_reuse_match,
    _semantic_coverage,
    _semantic_terms,
    _subject_scope_compatible,
    _target_is_background_like,
    _target_metadata_unknown_fields,
    _terms_match,
)
































































































































































# Page type values that mark a page_image slot as serving an ambience
# purpose rather than precise content. Used by ``_target_is_background_like``
# instead of substring matching against arbitrary slot strings.





















































































































































