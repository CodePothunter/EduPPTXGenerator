"""复用层裁决编排：find_reusable 入口、硬过滤+三档裁决(_apply_reuse_policy)、多库合并(_finalize)、复用落盘(record_reused)、占用门、embedding rescue。函数体逐字一致。"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from edupptx.materials.reuse_policy import (
    BACKGROUND_REUSE_THRESHOLD,
    T_DIRECT,
    T_GAP,
    T_REJECT,
    decide_reuse,
    evaluate_reuse_filter,
    normalize_reuse_policy_fields,
    reuse_threshold_for_target as policy_reuse_threshold_for_target,
)

# Env 驱动 + reload 敏感（test_embed_rescue_floor_respects_env，已改为 reload 本模块）。
EMBED_RESCUE_FLOOR = float(os.environ.get("EDUPPTX_REUSE_EMBED_RESCUE_FLOOR", "0.70"))

from edupptx.reuse._context import ReuseSearchContext
from edupptx.reuse._util import (
    _clean_text,
    _dedupe_terms,
    _dict,
    _read_existing_db,
    _read_json_if_exists,
)
from edupptx.reuse._constants import (
    BACKGROUND_REUSE_INDEX_GROUP,
    DEFAULT_DB_FILENAME,
    DEFAULT_HYBRID_RETRIEVAL_POOL_SIZE,
    DEFAULT_MIN_REUSE_KEYWORD_SCORE,
    DEFAULT_REUSE_CANDIDATE_LIMIT,
    EMBEDDING_KEYWORD_GAP_REJECT_THRESHOLD,
    MAX_LLM_REVIEWS_PER_QUERY,
    MAX_LLM_REVIEW_WORKERS,
    R5_NEAR_MISS_EPSILON,
    REUSE_MANIFEST_FILENAME,
    REUSE_REVIEW_ACCEPT_SCORE_THRESHOLD,
    STRICT_REUSE_MAX_PER_SESSION,
    _CONTENT_REUSE_GROUP,
    _GENERAL_REUSE_GROUP,
)
from edupptx.reuse._assets import (
    _is_background_asset,
    _normalize_subject_value,
    _page_retrieval_text,
    _topic_refs_for_asset,
)
from edupptx.reuse._normalize import (
    _normalize_binary_reuse_group,
)
from edupptx.reuse._scoring import (
    _aspect_ratio_loss,
    _aspect_ratio_penalty,
    _bm25_tokens_from_values,
    _candidate_policy_score,
    _optional_int,
    _subject_scope_decision,
)
from edupptx.reuse._embedding import (
    _read_ai_image_embedding_index,
    _relative_output_path,
)
from edupptx.reuse._store import (
    _is_skip_reuse_group,
    _normalize_asset_for_match,
    _read_match_index_or_build,
    _route_match_index_for_target,
)
from edupptx.reuse._retrieve import (
    _rank_embedding_candidates,
    _rank_hybrid_reuse_candidates,
    _rank_reuse_candidates,
    _rank_substring_candidates,
)
from edupptx.reuse._review import (
    _clamp_score,
    _reuse_debug_asset_payload,
    _reuse_review_accept_score_threshold,
    _review_reuse_candidate_with_llm,
)
from edupptx.reuse._build import (
    _build_reuse_target_asset,
)
from edupptx.reuse._debug import (
    _append_reuse_debug_record,
    _collect_reuse_candidate_debug,
    _flat_reuse_audit_fields,
    _new_reuse_debug_record,
    _reuse_debug_candidate_payload,
    _reuse_debug_record_for_mode,
)
from edupptx.reuse._keywords import (
    _enrich_reuse_target_keywords_once,
)
from edupptx.reuse._vlm import (
    _r5_try_reserve_session_vlm_budget,
    _review_reuse_candidate_with_vlm,
)


def _get_llm_max_workers() -> int:
    raw = os.environ.get("EDUPPTX_LLM_MAX_WORKERS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return MAX_LLM_REVIEW_WORKERS


def _review_worker_count(num_review_targets: int) -> int:
    """单个 target 的候选复审并发数。

    受每查询复审预算约束，绝不超过实际会被复审的候选数
    （≤ MAX_LLM_REVIEWS_PER_QUERY），同时尊重 _get_llm_max_workers() 这一全局上限。
    """
    return max(1, min(_get_llm_max_workers(), MAX_LLM_REVIEWS_PER_QUERY, max(0, int(num_review_targets))))


def _normalize_reuse_library_dirs(
    library_dir: str | Path | list[str | Path] | tuple[str | Path, ...],
) -> list[Path]:
    if isinstance(library_dir, (str, Path)):
        values = [library_dir]
    elif isinstance(library_dir, (list, tuple)):
        values = list(library_dir)
    else:
        values = [library_dir]
    roots: list[Path] = []
    seen: set[str] = set()
    for value in values:
        root = Path(value).expanduser().resolve()
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)
    return roots or [Path("materials_library").resolve()]


def _build_reuse_library_payload(library_root: Path) -> dict[str, Any]:
    db_path = library_root / DEFAULT_DB_FILENAME
    db = _read_existing_db(db_path)
    index, match_index_path = _read_match_index_or_build(library_root, db)
    embedding_index, embedding_status = _read_ai_image_embedding_index(library_root)
    return {
        "library_root": library_root,
        "db_path": db_path,
        "db": db,
        "index": index,
        "match_index_path": match_index_path,
        "embedding_index": embedding_index,
        "embedding_status": embedding_status,
    }


def _load_reuse_library_for_search(
    library_root: Path,
    reuse_search_context: ReuseSearchContext | None,
) -> dict[str, Any]:
    if reuse_search_context is None:
        return _build_reuse_library_payload(library_root)

    # Single-flight (M-14): build INSIDE the lock so concurrent first-readers
    # don't each rebuild the shared on-disk index. The previous double-checked
    # form ran the build OUTSIDE the lock, so a fan-out of slides hitting a
    # stale library all rebuilt the embedding sidecar in parallel. On-disk
    # writes on the build path are atomic, so this only removes redundant work,
    # never corruption. cache_lock is an RLock and the build never re-acquires
    # it, so holding it across the build cannot deadlock.
    cache_key = str(library_root)
    with reuse_search_context.cache_lock:
        cached = reuse_search_context.library_cache.get(cache_key)
        if isinstance(cached, dict):
            return cached
        loaded = _build_reuse_library_payload(library_root)
        reuse_search_context.library_cache[cache_key] = loaded
        return loaded


def _route_match_index_for_target_cached(
    library_root: Path,
    index: dict[str, Any],
    match_index_path: Path,
    target: dict[str, Any],
    reuse_search_context: ReuseSearchContext | None,
) -> tuple[dict[str, Any], Path, list[Any], str] | None:
    if reuse_search_context is None:
        return _route_match_index_for_target(library_root, index, match_index_path, target)
    cache_key = (str(library_root), _reuse_route_key_for_target(target))
    with reuse_search_context.cache_lock:
        if cache_key not in reuse_search_context.route_index_cache:
            reuse_search_context.route_index_cache[cache_key] = _route_match_index_for_target(
                library_root,
                index,
                match_index_path,
                target,
            )
        return reuse_search_context.route_index_cache[cache_key]


def _reuse_route_key_for_target(target: dict[str, Any]) -> str:
    if _clean_text(target.get("asset_kind")) == "background":
        return BACKGROUND_REUSE_INDEX_GROUP
    return _normalize_binary_reuse_group(target.get("strict_reuse_group"), default=_GENERAL_REUSE_GROUP)


def _global_reuse_candidate_rank(candidate: dict[str, Any]) -> tuple[float, float, float, float, float]:
    score_details = _dict(candidate.get("score_details"))
    return (
        float(candidate.get("policy_score") or score_details.get("policy_score") or 0.0),
        float(candidate.get("hybrid_score") or score_details.get("hybrid_score") or 0.0),
        float(candidate.get("keyword_score") or score_details.get("keyword_score") or score_details.get("score") or 0.0),
        float(candidate.get("embedding_score") or score_details.get("embedding_score") or 0.0),
        -float(candidate.get("library_search_order") or 0),
    )


def _has_structural_evidence(
    target: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Return (has_evidence, evidence_kinds) for the Q1/P7 consistency gate.

    The active structural evidence is normalized overlap between target and
    candidate retrieval text. Older constraint/core-keyword hooks were removed
    with the simplified reuse metadata schema.
    """

    def _norm_terms(values: Any) -> set[str]:
        return {
            _clean_text(item).casefold()
            for item in (values or [])
            if isinstance(item, (str, int, float)) and _clean_text(item)
        }

    target_terms = _norm_terms(_bm25_tokens_from_values([_page_retrieval_text(target)]))
    candidate_terms = _norm_terms(_bm25_tokens_from_values([_page_retrieval_text(candidate)]))
    if target_terms and candidate_terms and (target_terms & candidate_terms):
        return True, ["retrieval_text_overlap"]
    return False, []


def _embedding_keyword_gap_reject(
    target: dict[str, Any],
    candidate_asset: dict[str, Any],
    score_details: dict[str, Any],
    *,
    gap_threshold: float = EMBEDDING_KEYWORD_GAP_REJECT_THRESHOLD,
) -> dict[str, Any] | None:
    """Return a synthetic policy result when the consistency gate trips.

    Trip condition: the embedding score sits ``gap_threshold`` or more
    above the keyword score *and* the candidate carries no structural
    evidence. Returning ``None`` means the gate does not apply — the
    caller should continue with its normal policy evaluation.

    Backgrounds are exempted: their reuse logic uses background-specific
    scoring and the keyword score is not directly comparable.
    """

    if _clean_text(target.get("asset_kind")) == "background":
        return None
    embedding = _maybe_float_score(score_details.get("embedding_score"))
    keyword = _maybe_float_score(score_details.get("keyword_score"))
    if embedding is None or keyword is None:
        return None
    if (embedding - keyword) < gap_threshold:
        return None
    has_evidence, evidence_kinds = _has_structural_evidence(target, candidate_asset)
    if has_evidence:
        return None
    if embedding >= EMBED_RESCUE_FLOOR:
        return None
    return {
        "decision": "reject",
        "reason": "embedding_keyword_gap_no_structural_evidence",
        "confidence": 0.85,
        "llm_skip_safe": True,
        "consistency_gate": {
            "embedding_score": round(embedding, 4),
            "keyword_score": round(keyword, 4),
            "gap": round(embedding - keyword, 4),
            "gap_threshold": gap_threshold,
            "evidence_kinds_checked": ["retrieval_text_overlap"],
            "evidence_kinds_found": evidence_kinds,
        },
    }


def _maybe_float_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _review_score_value(score_details: dict[str, Any], key: str) -> float:
    try:
        return float(score_details.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _review_keyword_score(score_details: dict[str, Any]) -> float:
    return max(
        _review_score_value(score_details, "keyword_score"),
        _review_score_value(score_details, "score"),
    )


def _llm_review_priority(record: dict[str, Any]) -> tuple[float, float, float, float, float]:
    score_details = _dict(record.get("score_details"))
    policy_result = _dict(record.get("policy_result"))
    return (
        _review_score_value(score_details, "embedding_score"),
        float(policy_result.get("policy_score") or score_details.get("policy_score") or 0.0),
        _review_keyword_score(score_details),
        _review_score_value(score_details, "substring_score"),
        _review_score_value(score_details, "hybrid_score"),
    )


def _apply_reuse_policy_to_ranked_candidates(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    threshold: float,
    embedding_status: dict[str, Any],
    df_ratio_lookup: dict[str, float],
    keyword_client: Any | None,
    reuse_session_state: dict[str, Any] | None,
    llm_review_enabled: bool,
    llm_review_budget: int = MAX_LLM_REVIEWS_PER_QUERY,
    vlm_client: Any | None = None,
    near_miss_vlm_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate policy and dispatch LLM reviews for a ranked candidate list.

    Current LLM-review step uses strict one-candidate prompts. We do two passes:

    1. Pre-review pass — compute each candidate's pre-LLM ``policy_result``
       (including the Q1/P7 consistency gate), and classify into one of
       ``direct``, ``llm_review`` or ``skip``.
    2. LLM review calls — collect up to ``llm_review_budget`` ``llm_review``
       candidates and submit them as a single request via
       one strict single-candidate LLM call per reviewed candidate; multiple
       candidate reviews may run concurrently.

    The output schema (accepted/rejected lists, llm_reviews_used) is
    unchanged so callers do not need to be updated.
    """

    rejected_by_policy: list[dict[str, Any]] = []
    rejected_by_occupancy: list[dict[str, Any]] = []

    # ----- Pass 1: pre-review classification ------------------------------
    pre_records: list[dict[str, Any]] = []
    for candidate in candidates:
        score_details = dict(_dict(candidate.get("score_details")))
        for key in (
            "keyword_score",
            "embedding_score",
            "substring_score",
            "hybrid_score",
            "rrf_score",
            "policy_score",
            "background_reuse_score",
            "transform_policy",
        ):
            if key in candidate and key not in score_details:
                score_details[key] = candidate.get(key)
        policy_score = _candidate_policy_score(candidate, score_details)
        candidate["policy_score"] = policy_score
        score_details["policy_score"] = policy_score
        candidate_asset = _dict(candidate.get("asset"))
        candidate_df_ratio_lookup = candidate.get("_reuse_df_ratio_lookup")
        if not isinstance(candidate_df_ratio_lookup, dict):
            candidate_df_ratio_lookup = df_ratio_lookup
        if candidate_df_ratio_lookup:
            score_details["df_ratio_lookup"] = candidate_df_ratio_lookup
        policy_result = evaluate_reuse_filter(
            target,
            candidate_asset,
            score_details,
            threshold=threshold,
        )
        # Q1/P7 consistency gate (runs before LLM dispatch).
        if _clean_text(policy_result.get("decision")) != "reject":
            consistency_reject = _embedding_keyword_gap_reject(
                target,
                candidate_asset,
                score_details,
            )
            if consistency_reject is not None:
                policy_result = {**policy_result, **consistency_reject}
        pre_records.append({
            "candidate": candidate,
            "candidate_asset": candidate_asset,
            "score_details": score_details,
            "policy_result": policy_result,
        })

    # ----- Pass 1b: three-tier decision authority (spec §4 / decide_reuse) -
    # The wired evaluate_reuse_filter only yields full_match/reject and never
    # routes the borderline band to llm_review, so the LLM-review pass below
    # was unreachable and the per-target threshold was ignored. decide_reuse
    # now owns the score tier for non-background page images: it keys on the
    # absolute keyword_score with the per-target accept threshold as the
    # discard line (production hybrid_score is RRF-normalized and unusable for
    # the high/low cut). Pass-1 hard rejects (skip / kind / subject / aspect /
    # consistency gap / pre-LLM floor) are preserved untouched.
    if not _is_background_asset(target):
        tier_items = [
            {
                "_index": index,
                "asset_id": _clean_text(_dict(record["candidate_asset"]).get("asset_id")),
                "policy_score": float(record["score_details"].get("policy_score") or 0.0),
                "size_distance": _reuse_size_distance(target, _dict(record["candidate_asset"])),
            }
            for index, record in enumerate(pre_records)
            if _clean_text(record["policy_result"].get("decision")) != "reject"
        ]
        if tier_items:
            tier_items.sort(key=lambda item: item["policy_score"], reverse=True)
            tier = decide_reuse(
                tier_items,
                score_key="policy_score",
                t_direct=T_DIRECT,
                t_reject=T_REJECT,
                t_gap=T_GAP,
            )
            tier_decision = _clean_text(tier.get("decision"))
            if tier_decision == "direct_reuse":
                selected_index = tier_items[0]["_index"]
                for item in tier_items:
                    record = pre_records[item["_index"]]
                    if item["_index"] == selected_index:
                        record["policy_result"] = {
                            **record["policy_result"],
                            "decision": "direct_reuse",
                            "reason": "policy_score_direct_reuse",
                            "policy_score": item["policy_score"],
                        }
                    else:
                        record["policy_result"] = {
                            **record["policy_result"],
                            "decision": "reject",
                            "reason": "policy_not_selected",
                            "policy_score": item["policy_score"],
                        }
            elif tier_decision == "llm_review":
                cluster_indices = {item["_index"] for item in (tier.get("cluster") or [])}
                for item in tier_items:
                    record = pre_records[item["_index"]]
                    if item["_index"] in cluster_indices:
                        record["policy_result"] = {
                            **record["policy_result"],
                            "decision": "llm_review",
                            "reason": "policy_score_llm_review",
                            "policy_score": item["policy_score"],
                        }
                    else:
                        record["policy_result"] = {
                            **record["policy_result"],
                            "decision": "reject",
                            "reason": "policy_not_selected",
                            "policy_score": item["policy_score"],
                        }
            else:
                for item in tier_items:
                    record = pre_records[item["_index"]]
                    if _embedding_rescue_decision(
                        embedding_score=_maybe_float_score(
                            _dict(record["score_details"]).get("embedding_score")
                        ),
                        transform_rejected=_transform_rejects_candidate(record["candidate"]),
                    ):
                        record["policy_result"] = {
                            **record["policy_result"],
                            "decision": "llm_review",
                            "reason": "embedding_rescue_review",
                            "policy_score": item["policy_score"],
                        }
                    else:
                        record["policy_result"] = {
                            **record["policy_result"],
                            "decision": "reject",
                            "reason": "policy_score_below_reject_threshold",
                            "policy_score": item["policy_score"],
                        }

    # Identify the slice that needs an LLM review, capped by budget.
    review_targets: list[int] = []
    if llm_review_enabled:
        review_candidates: list[int] = []
        for index, record in enumerate(pre_records):
            decision = _clean_text(record["policy_result"].get("decision"))
            if decision != "llm_review":
                continue
            if record["policy_result"].get("llm_skip_safe"):
                continue
            review_candidates.append(index)
        review_candidates.sort(key=lambda index: _llm_review_priority(pre_records[index]), reverse=True)
        review_targets = review_candidates[: max(0, int(llm_review_budget or 0))]

    # ----- Pass 2: one LLM call per candidate, optionally parallel --------
    review_results_by_index: dict[int, dict[str, Any]] = {}
    if review_targets:
        def review_one(index: int) -> tuple[int, dict[str, Any]]:
            record = pre_records[index]
            review = _review_reuse_candidate_with_llm(
                keyword_client,
                target=target,
                candidate=record["candidate_asset"],
                policy_result=record["policy_result"],
                score_details=record["score_details"],
            )
            return index, review

        if len(review_targets) == 1:
            index, review = review_one(review_targets[0])
            review_results_by_index[index] = review
        else:
            max_workers = _review_worker_count(len(review_targets))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_index = {executor.submit(review_one, index): index for index in review_targets}
                for future in as_completed(future_to_index):
                    index, review = future.result()
                    review_results_by_index[index] = review

    llm_reviews_used = len(review_targets)

    # ----- Pass 3: assemble per-candidate outcomes -----------------------
    accepted_candidates: list[dict[str, Any]] = []
    for index, record in enumerate(pre_records):
        candidate = record["candidate"]
        candidate_asset = record["candidate_asset"]
        policy_result = record["policy_result"]
        review_decision = _clean_text(policy_result.get("decision"))
        review_reason = _clean_text(policy_result.get("reason"))
        deterministic_reject = bool(policy_result.get("llm_skip_safe"))
        skip_for_existing_accept = bool(accepted_candidates)

        if review_decision == "llm_review" and index in review_results_by_index:
            review_result = review_results_by_index[index]
            policy_result = dict(policy_result)
            policy_result["llm_review_required"] = True
            policy_result["llm_review_performed"] = True
            policy_result["llm_review"] = review_result
            if _reuse_review_accepts(review_result) and not skip_for_existing_accept:
                policy_result["decision"] = "direct_reuse"
                policy_result["reason"] = "llm_accept"
                policy_result["confidence"] = max(
                    float(policy_result.get("confidence") or 0.0),
                    _clamp_score(review_result.get("score")),
                )
            elif skip_for_existing_accept and _reuse_review_accepts(review_result):
                # Earlier candidate already accepted — keep the LLM result
                # in the record for debug but downgrade to reject so we
                # never materialise more than one candidate per query.
                policy_result["decision"] = "reject"
                policy_result["reason"] = (
                    "strict_llm_review_skipped_after_accept"
                    if review_reason.startswith("strict_")
                    else "llm_review_skipped_after_accept"
                )
            else:
                policy_result["decision"] = "reject"
                policy_result["reason"] = (
                    "strict_llm_score_review_rejected"
                    if review_reason.startswith("strict_")
                    else "llm_score_review_rejected"
                )
        elif review_decision == "llm_review":
            # Either budget exhausted, llm_skip_safe deterministic reject, or
            # llm_review disabled. Synthesise the appropriate skip record.
            skip_threshold = _reuse_review_accept_score_threshold(
                target,
                candidate_asset,
                policy_result=policy_result,
            )
            if not llm_review_enabled:
                skip_brief = "llm_disabled"
                skip_decision_reason = "llm_disabled"
            elif deterministic_reject:
                skip_brief = "deterministic_reject_skip"
                skip_decision_reason = (
                    "strict_deterministic_llm_skip"
                    if review_reason.startswith("strict_")
                    else "deterministic_llm_skip"
                )
            else:
                skip_brief = "per_query_budget_exhausted"
                skip_decision_reason = "llm_budget_exhausted"
            policy_result = dict(policy_result)
            policy_result["llm_review_required"] = True
            policy_result["llm_review_performed"] = False
            policy_result["llm_review"] = {
                "score": 0.0,
                "threshold": skip_threshold,
                "decision": "reject",
                "brief_reason": skip_brief,
            }
            policy_result["decision"] = "reject"
            policy_result["reason"] = skip_decision_reason
        else:
            policy_result = dict(policy_result)
            policy_result["llm_review_required"] = False
            policy_result["llm_review_performed"] = False

        candidate["reuse_policy"] = policy_result
        decision = _clean_text(policy_result.get("decision"))
        if decision in {"direct_reuse", "full_match", "generic_support"}:
            occupancy = _strict_reuse_occupancy_status(candidate, reuse_session_state)
            candidate["strict_reuse_occupancy"] = occupancy
            if _clean_text(occupancy.get("decision")) == "skip_strict_asset_reuse_limit":
                rejected_by_occupancy.append(candidate)
                continue
            accepted_candidates.append(candidate)
        else:
            rejected_by_policy.append(candidate)

    # ----- R5: near-miss VLM verification --------------------------------
    # Triggers only when:
    #   * no candidate has been accepted yet (no point salvaging if we
    #     already have a match);
    #   * a candidate's LLM score sat within R5_NEAR_MISS_EPSILON of its
    #     accept threshold (the metadata reviewer was almost convinced);
    #   * a VLM client is configured AND the per-session budget allows.
    # Successful VLM accepts promote the candidate to ``full_match`` after
    # a fresh occupancy check, preserving the per-session strict-reuse
    # invariants.
    vlm_used_this_query = 0
    if (
        not accepted_candidates
        and vlm_client is not None
        and pre_records
    ):
        best_near_miss_index: int | None = None
        best_near_miss_gap: float = R5_NEAR_MISS_EPSILON + 1.0
        for index, record in enumerate(pre_records):
            policy = record["candidate"].get("reuse_policy") or {}
            llm_review = policy.get("llm_review") or {}
            if not policy.get("llm_review_performed"):
                continue
            llm_score = _maybe_float_score(llm_review.get("score"))
            llm_threshold = _maybe_float_score(llm_review.get("threshold"))
            if llm_score is None or llm_threshold is None:
                continue
            gap = llm_threshold - llm_score
            # Near miss: the candidate is BELOW threshold but by no more
            # than the epsilon. Negative gaps mean already-accepted (we
            # would have entered the accepted branch above), so we skip.
            if 0.0 <= gap <= R5_NEAR_MISS_EPSILON and gap < best_near_miss_gap:
                best_near_miss_gap = gap
                best_near_miss_index = index
        if best_near_miss_index is not None:
            if _r5_try_reserve_session_vlm_budget(near_miss_vlm_state):
                record = pre_records[best_near_miss_index]
                candidate = record["candidate"]
                policy_result = candidate.get("reuse_policy") or {}
                accept_threshold = float(
                    (policy_result.get("llm_review") or {}).get("threshold")
                    or _reuse_review_accept_score_threshold(target, record["candidate_asset"], policy_result=policy_result)
                )
                vlm_result = _review_reuse_candidate_with_vlm(
                    vlm_client,
                    target=target,
                    candidate_asset=record["candidate_asset"],
                    candidate_image_path=candidate.get("candidate_image_path"),
                    accept_threshold=accept_threshold,
                    llm_review_result=policy_result.get("llm_review"),
                )
                _r5_consume_session_vlm_budget(near_miss_vlm_state)
                vlm_used_this_query += 1
                policy_result = dict(policy_result)
                policy_result["vlm_near_miss_review"] = vlm_result
                if vlm_result.get("decision") == "accept":
                    policy_result["decision"] = "direct_reuse"
                    policy_result["reason"] = "vlm_near_miss_accept"
                    candidate["reuse_policy"] = policy_result
                    occupancy = _strict_reuse_occupancy_status(candidate, reuse_session_state)
                    candidate["strict_reuse_occupancy"] = occupancy
                    if _clean_text(occupancy.get("decision")) == "skip_strict_asset_reuse_limit":
                        rejected_by_occupancy.append(candidate)
                        if candidate in rejected_by_policy:
                            rejected_by_policy.remove(candidate)
                    else:
                        # Promote out of rejected_by_policy (if it was there) and
                        # into accepted_candidates.
                        if candidate in rejected_by_policy:
                            rejected_by_policy.remove(candidate)
                        accepted_candidates.append(candidate)
                else:
                    candidate["reuse_policy"] = policy_result

    return {
        "accepted_candidates": accepted_candidates,
        "rejected_by_policy": rejected_by_policy,
        "rejected_by_occupancy": rejected_by_occupancy,
        "llm_reviews_used": llm_reviews_used,
        "llm_review_budget": llm_review_budget,
        "vlm_near_miss_reviews_used": vlm_used_this_query,
    }


def _reuse_accept_reason(best: dict[str, Any]) -> str:
    policy_decision = _clean_text(_dict(best.get("reuse_policy")).get("decision"))
    policy_reason = _clean_text(_dict(best.get("reuse_policy")).get("reason"))
    if _clean_text(_dict(best.get("asset")).get("asset_kind")) == "background":
        return "reused_by_background_reuse_score"
    if policy_decision == "direct_reuse" and policy_reason == "llm_accept":
        return "reused_by_llm_review"
    if policy_decision == "direct_reuse":
        return "reused_by_policy_score"
    if policy_decision == "generic_support":
        return "reused_by_policy_generic_support"
    return "reused_by_policy_score"


def _reuse_collection_empty_reason(collection: dict[str, Any]) -> str:
    return _clean_text(collection.get("empty_reason")) or "retrieval_no_candidate"


def _finalize_reuse_candidate_collection(
    collection: dict[str, Any] | None,
    *,
    debug_path: str | Path | None,
    keyword_client: Any | None,
    reuse_session_state: dict[str, Any] | None,
    llm_review_enabled: bool,
    reuse_debug_mode: str,
    vlm_client: Any | None = None,
    near_miss_vlm_state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(collection, dict) or not collection.get("_reuse_candidate_collection"):
        return None

    reuse_debug_mode = _normalize_reuse_debug_mode(reuse_debug_mode)
    child_collections = [
        item for item in collection.get("collections") or []
        if isinstance(item, dict) and item.get("_reuse_candidate_collection")
    ]
    if child_collections:
        target = _dict(collection.get("target") or child_collections[0].get("target"))
        threshold = float(collection.get("threshold") or child_collections[0].get("threshold") or 0.0)
        combined_candidates = list(collection.get("candidates") or [])
        if combined_candidates:
            policy_outcome = _apply_reuse_policy_to_ranked_candidates(
                target,
                combined_candidates,
                threshold=threshold,
                embedding_status={},
                df_ratio_lookup={},
                keyword_client=keyword_client,
                reuse_session_state=reuse_session_state,
                llm_review_enabled=llm_review_enabled,
                vlm_client=vlm_client,
                near_miss_vlm_state=near_miss_vlm_state,
            )
            accepted_candidates = policy_outcome["accepted_candidates"]
            rejected_by_policy = policy_outcome["rejected_by_policy"]
            rejected_by_occupancy = policy_outcome["rejected_by_occupancy"]
        else:
            policy_outcome = {
                "accepted_candidates": [],
                "rejected_by_policy": [],
                "rejected_by_occupancy": [],
                "llm_reviews_used": 0,
                "llm_review_budget": MAX_LLM_REVIEWS_PER_QUERY,
            }
            accepted_candidates = []
            rejected_by_policy = []
            rejected_by_occupancy = []

        best = accepted_candidates[0] if accepted_candidates else None
        reason = (
            _reuse_accept_reason(best)
            if best
            else (
                "policy_reject"
                if combined_candidates
                else "retrieval_no_candidate"
            )
        )
        if best:
            best["multi_library_reuse_reason"] = reason
            best["llm_reviews_invoked"] = policy_outcome["llm_reviews_used"]
            best["llm_reviews_budget"] = policy_outcome["llm_review_budget"]

        for child in child_collections:
            collection_candidates = child.get("candidates") or []
            collection_candidate_ids = {id(candidate) for candidate in collection_candidates}
            record = _dict(child.get("debug_record"))
            collection_threshold = float(child.get("threshold") or threshold)
            record["llm_reviews_invoked"] = policy_outcome["llm_reviews_used"]
            record["llm_reviews_budget"] = policy_outcome["llm_review_budget"]
            record["policy_candidates"] = [
                _reuse_debug_candidate_payload(candidate, threshold=collection_threshold)
                for candidate in collection_candidates
            ]
            record["policy_rejected_candidates"] = [
                _reuse_debug_candidate_payload(candidate, threshold=collection_threshold)
                for candidate in rejected_by_policy
                if id(candidate) in collection_candidate_ids
            ]
            record["occupancy_rejected_candidates"] = [
                _reuse_debug_candidate_payload(candidate, threshold=collection_threshold)
                for candidate in rejected_by_occupancy
                if id(candidate) in collection_candidate_ids
            ]
            local_match = best if best is not None and id(best) in collection_candidate_ids else None
            local_reason = reason if local_match else ("reused_from_other_library" if best else reason)
            if not collection_candidates and not best:
                local_reason = _reuse_collection_empty_reason(child)
            record["decision"] = {
                "reused": local_match is not None,
                "reason": local_reason,
                "asset_id": _dict(local_match.get("asset")).get("asset_id") if local_match else "",
                "keyword_score": local_match.get("keyword_score") if local_match else None,
                "threshold_used": collection_threshold,
                "reuse_policy": local_match.get("reuse_policy") if local_match else None,
                "reuse_audit": local_match.get("reuse_audit") if local_match else None,
                "llm_reuse_review_performed": _match_llm_reuse_review_performed(local_match) if local_match else False,
                "strict_reuse_occupancy": local_match.get("strict_reuse_occupancy") if local_match else None,
            }
            _append_reuse_debug_record(
                debug_path,
                _reuse_debug_record_for_mode(record, mode=reuse_debug_mode, match=local_match),
            )
        return best

    target = _dict(collection.get("target"))
    candidates = list(collection.get("candidates") or [])
    record = _dict(collection.get("debug_record"))
    threshold = float(collection.get("threshold") or record.get("threshold_used") or 0.0)
    if not candidates:
        reason = _reuse_collection_empty_reason(collection)
        record["decision"] = {
            "reused": False,
            "reason": reason,
            "asset_id": "",
            "keyword_score": None,
            "threshold_used": threshold,
            "reuse_policy": None,
            "reuse_audit": None,
            "llm_reuse_review_performed": False,
            "strict_reuse_occupancy": None,
        }
        _append_reuse_debug_record(
            debug_path,
            _reuse_debug_record_for_mode(record, mode=reuse_debug_mode, match=None),
        )
        return None

    policy_outcome = _apply_reuse_policy_to_ranked_candidates(
        target,
        candidates,
        threshold=threshold,
        embedding_status=_dict(collection.get("embedding_status")),
        df_ratio_lookup={},
        keyword_client=keyword_client,
        reuse_session_state=reuse_session_state,
        llm_review_enabled=llm_review_enabled,
        vlm_client=vlm_client,
        near_miss_vlm_state=near_miss_vlm_state,
    )
    accepted_candidates = policy_outcome["accepted_candidates"]
    rejected_by_policy = policy_outcome["rejected_by_policy"]
    rejected_by_occupancy = policy_outcome["rejected_by_occupancy"]
    record["llm_reviews_invoked"] = policy_outcome["llm_reviews_used"]
    record["llm_reviews_budget"] = policy_outcome["llm_review_budget"]
    record["policy_candidates"] = [
        _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in candidates
    ]
    if not accepted_candidates:
        record["policy_rejected_candidates"] = [
            _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in rejected_by_policy
        ]
        record["occupancy_rejected_candidates"] = [
            _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in rejected_by_occupancy
        ]
        reason = "policy_reject"
        record["decision"] = {
            "reused": False,
            "reason": reason,
            "asset_id": "",
            "keyword_score": None,
            "threshold_used": threshold,
            "reuse_policy": None,
            "reuse_audit": None,
            "llm_reuse_review_performed": False,
            "strict_reuse_occupancy": None,
        }
        _append_reuse_debug_record(
            debug_path,
            _reuse_debug_record_for_mode(record, mode=reuse_debug_mode, match=None),
        )
        return None

    best = accepted_candidates[0]
    reason = _reuse_accept_reason(best)
    best["llm_reviews_invoked"] = policy_outcome["llm_reviews_used"]
    best["llm_reviews_budget"] = policy_outcome["llm_review_budget"]
    record["decision"] = {
        "reused": True,
        "reason": reason,
        "asset_id": _dict(best.get("asset")).get("asset_id"),
        "keyword_score": best.get("keyword_score"),
        "threshold_used": threshold,
        "reuse_policy": best.get("reuse_policy"),
        "reuse_audit": best.get("reuse_audit"),
        "llm_reuse_review_performed": _match_llm_reuse_review_performed(best),
        "strict_reuse_occupancy": best.get("strict_reuse_occupancy"),
    }
    _append_reuse_debug_record(
        debug_path,
        _reuse_debug_record_for_mode(record, mode=reuse_debug_mode, match=best),
    )
    return best


def find_reusable_ai_image_asset(
    *,
    library_dir: str | Path | list[str | Path] | tuple[str | Path, ...],
    asset_kind: str,
    prompt: str,
    prompt_route: dict[str, Any] | None = None,
    background_route: dict[str, Any] | None = None,
    theme: str = "",
    grade: str = "",
    subject: str = "",
    grade_band: str = "",
    page_title: str = "",
    page_type: str = "",
    role: str = "",
    aspect_ratio: str = "",
    caption: str = "",
    keyword_client: Any | None = None,
    candidate_limit: int = DEFAULT_REUSE_CANDIDATE_LIMIT,
    min_keyword_score: float | None = DEFAULT_MIN_REUSE_KEYWORD_SCORE,
    debug_path: str | Path | None = None,
    debug_context: dict[str, Any] | None = None,
    reuse_session_state: dict[str, Any] | None = None,
    llm_review_enabled: bool = True,
    reuse_debug_mode: str = "",
    reuse_search_context: ReuseSearchContext | None = None,
    _target_keyword_cache: dict[str, Any] | None = None,
    _collect_candidates_only: bool = False,
    _library_search_order: int = 0,
) -> dict[str, Any] | None:
    """Find a reusable AI image asset from the central library.

    BM25 remains the precision signal, while optional Qwen embedding and
    substring retrieval provide gray-zone recall through RRF fusion. When a
    strict reuse policy needs semantic confirmation, the same LLM client can
    perform a bounded second-stage review.
    """

    library_roots = _normalize_reuse_library_dirs(library_dir)
    if reuse_search_context is None:
        reuse_search_context = ReuseSearchContext()
    target_keyword_cache = (
        _target_keyword_cache
        if _target_keyword_cache is not None
        else reuse_search_context.target_keyword_cache
    )
    if len(library_roots) > 1:
        collections: list[dict[str, Any]] = []
        for order, root in enumerate(library_roots):
            context = dict(debug_context or {})
            context["reuse_library_dir"] = str(root)
            collection = find_reusable_ai_image_asset(
                library_dir=root,
                asset_kind=asset_kind,
                prompt=prompt,
                prompt_route=prompt_route,
                background_route=background_route,
                theme=theme,
                grade=grade,
                subject=subject,
                page_title=page_title,
                page_type=page_type,
                role=role,
                aspect_ratio=aspect_ratio,
                caption=caption,
                grade_band=grade_band,
                keyword_client=keyword_client,
                candidate_limit=candidate_limit,
                min_keyword_score=min_keyword_score,
                debug_path=debug_path,
                debug_context=context,
                reuse_session_state=reuse_session_state,
                llm_review_enabled=llm_review_enabled,
                reuse_debug_mode=reuse_debug_mode,
                reuse_search_context=reuse_search_context,
                _target_keyword_cache=target_keyword_cache,
                _collect_candidates_only=True,
                _library_search_order=order,
            )
            if isinstance(collection, dict) and collection.get("_reuse_candidate_collection"):
                collections.append(collection)
        combined_candidates: list[dict[str, Any]] = []
        target: dict[str, Any] | None = None
        threshold = _reuse_threshold_for_target(
            _build_reuse_target_asset(
                asset_kind=asset_kind,
                prompt=prompt,
                prompt_route=prompt_route,
                background_route=background_route,
                theme=theme,
                grade=grade,
                subject=subject,
                page_title=page_title,
                page_type=page_type,
                role=role,
                aspect_ratio=aspect_ratio,
                caption=caption,
                grade_band=grade_band,
            ),
            min_keyword_score,
        )
        for collection in collections:
            if target is None:
                target = _dict(collection.get("target"))
                threshold = float(collection.get("threshold") or threshold)
            combined_candidates.extend(collection.get("candidates") or [])
        combined_candidates.sort(key=_global_reuse_candidate_rank, reverse=True)
        combined_collection = {
            "_reuse_candidate_collection": True,
            "target": target or {},
            "threshold": threshold,
            "candidates": combined_candidates,
            "collections": collections,
        }
        if _collect_candidates_only:
            return combined_collection
        return _finalize_reuse_candidate_collection(
            combined_collection,
            debug_path=debug_path,
            keyword_client=keyword_client,
            reuse_session_state=reuse_session_state,
            llm_review_enabled=llm_review_enabled,
            reuse_debug_mode=reuse_debug_mode,
        )

    library_root = library_roots[0]
    loaded_library = _load_reuse_library_for_search(library_root, reuse_search_context)
    db_path = loaded_library["db_path"]
    index = loaded_library["index"]
    match_index_path = loaded_library["match_index_path"]
    assets = index.get("assets")
    embedding_index = loaded_library["embedding_index"]
    embedding_status = loaded_library["embedding_status"]
    reuse_debug_mode = _normalize_reuse_debug_mode(reuse_debug_mode)

    target = _build_reuse_target_asset(
        asset_kind=asset_kind,
        prompt=prompt,
        prompt_route=prompt_route,
        background_route=background_route,
        theme=theme,
        grade=grade,
        subject=subject,
        page_title=page_title,
        page_type=page_type,
        role=role,
        aspect_ratio=aspect_ratio,
        caption=caption,
        grade_band=grade_band,
    )

    debug_record = _new_reuse_debug_record(
        library_root=library_root,
        db_path=db_path,
        match_index_path=match_index_path,
        asset_count=len(assets) if isinstance(assets, list) else 0,
        candidate_limit=candidate_limit,
        min_keyword_score=min_keyword_score,
        context=debug_context,
    )
    debug_record["embedding_index"] = embedding_status
    debug_record["llm_review_enabled"] = bool(llm_review_enabled)
    debug_record["debug_mode"] = reuse_debug_mode
    debug_record["threshold_used"] = _reuse_threshold_for_target(target, min_keyword_score)

    def finish(reason: str, match: dict[str, Any] | None = None) -> dict[str, Any] | None:
        debug_record["decision"] = {
            "reused": match is not None,
            "reason": reason,
            "asset_id": _dict(match.get("asset")).get("asset_id") if match else "",
            "keyword_score": match.get("keyword_score") if match else None,
            "threshold_used": debug_record.get("threshold_used"),
            "reuse_policy": match.get("reuse_policy") if match else None,
            "reuse_audit": match.get("reuse_audit") if match else None,
            "llm_reuse_review_performed": _match_llm_reuse_review_performed(match) if match else False,
            "strict_reuse_occupancy": match.get("strict_reuse_occupancy") if match else None,
        }
        _append_reuse_debug_record(
            debug_path,
            _reuse_debug_record_for_mode(debug_record, mode=reuse_debug_mode, match=match),
        )
        return match

    if not isinstance(assets, list) or not assets:
        debug_record["target"] = _reuse_debug_asset_payload(target)
        if _collect_candidates_only:
            return {
                "_reuse_candidate_collection": True,
                "target": target,
                "threshold": debug_record.get("threshold_used"),
                "candidates": [],
                "debug_record": debug_record,
                "empty_reason": "empty_asset_store",
            }
        return finish("empty_asset_store")

    target = _enrich_reuse_target_keywords_once(target, keyword_client, target_keyword_cache)
    target = _normalize_asset_for_match(target, for_target=True) or target
    if _is_skip_reuse_group(target.get("strict_reuse_group")):
        debug_record["target"] = _reuse_debug_asset_payload(target)
        if _collect_candidates_only:
            return {
                "_reuse_candidate_collection": True,
                "target": target,
                "threshold": debug_record.get("threshold_used"),
                "candidates": [],
                "debug_record": debug_record,
                "empty_reason": "material_category_skip",
            }
        return finish("material_category_skip")
    if _clean_text(target.get("asset_kind")) == "background" and not _clean_text(target.get("strict_reuse_group")):
        target["strict_reuse_group"] = _GENERAL_REUSE_GROUP
    route_mode = "split"
    target_route_group = _normalize_binary_reuse_group(
        target.get("strict_reuse_group"),
        default=_GENERAL_REUSE_GROUP,
    )
    debug_record["reuse_group_route"] = {
        "route_mode": route_mode,
        "strict_reuse_group": target_route_group,
        "routed": False,
        "match_index_path": _relative_output_path(match_index_path),
        "asset_count": len(assets) if isinstance(assets, list) else 0,
    }
    routed = _route_match_index_for_target_cached(
        library_root,
        index,
        match_index_path,
        target,
        reuse_search_context,
    )
    if routed is not None:
        index, match_index_path, assets, route_group = routed
        debug_record["match_index_path"] = _relative_output_path(match_index_path)
        debug_record["asset_count"] = len(assets) if isinstance(assets, list) else 0
        debug_record["reuse_group_route"] = {
            "route_mode": route_mode,
            "strict_reuse_group": route_group,
            "routed": True,
            "match_index_path": _relative_output_path(match_index_path),
            "asset_count": debug_record["asset_count"],
        }
        if not isinstance(assets, list) or not assets:
            debug_record["target"] = _reuse_debug_asset_payload(target)
            if _collect_candidates_only:
                return {
                    "_reuse_candidate_collection": True,
                    "target": target,
                    "threshold": debug_record.get("threshold_used"),
                    "candidates": [],
                    "debug_record": debug_record,
                    "empty_reason": "empty_routed_asset_store",
                }
            return finish("empty_routed_asset_store")
    threshold = _reuse_threshold_for_target(target, min_keyword_score)
    debug_record["threshold_used"] = threshold
    debug_record["target"] = _reuse_debug_asset_payload(target)
    score_details_cache: dict[int, dict[str, Any]] = {}
    if debug_path is not None and reuse_debug_mode != "off":
        # 仅调试路径扫描全量池，保留"候选为何被硬过滤"的可见性；生产路径跳过此开销。
        debug_record["candidate_scores"] = _collect_reuse_candidate_debug(
            target,
            assets,
            library_root,
            score_details_cache=score_details_cache,
        )

    eligible_assets, hard_filter_summary = _eligible_reuse_assets(
        target,
        assets,
        reuse_search_context,
        library_root,
        target_route_group,
    )
    debug_record["hard_filter"] = hard_filter_summary
    if not eligible_assets:
        if _collect_candidates_only:
            return {
                "_reuse_candidate_collection": True,
                "target": target,
                "threshold": threshold,
                "candidates": [],
                "debug_record": debug_record,
                "empty_reason": "no_eligible_candidate_after_hard_filter",
                "embedding_status": embedding_status,
            }
        return finish("no_eligible_candidate_after_hard_filter")

    pool_limit = max(DEFAULT_HYBRID_RETRIEVAL_POOL_SIZE, int(candidate_limit or DEFAULT_REUSE_CANDIDATE_LIMIT))
    bm25_ranked_candidates = _rank_reuse_candidates(
        target,
        eligible_assets,
        library_root=library_root,
        limit=pool_limit,
        score_details_cache=score_details_cache,
    )
    embedding_ranked_candidates = _rank_embedding_candidates(
        target,
        eligible_assets,
        library_root=library_root,
        embedding_index=embedding_index,
        limit=pool_limit,
        query_embedding_cache=reuse_search_context.query_embedding_cache if reuse_search_context else None,
        query_embedding_cache_dir=(
            reuse_search_context.query_embedding_cache_dir if reuse_search_context else None
        ),
        status_sink=embedding_status if isinstance(embedding_status, dict) else None,
    )
    substring_ranked_candidates = _rank_substring_candidates(
        target,
        eligible_assets,
        library_root=library_root,
        limit=pool_limit,
    )
    ranked_candidates = _rank_hybrid_reuse_candidates(
        target,
        eligible_assets,
        library_root=library_root,
        bm25_ranked=bm25_ranked_candidates,
        embedding_ranked=embedding_ranked_candidates,
        substring_ranked=substring_ranked_candidates,
        threshold=threshold,
        limit=candidate_limit,
        score_details_cache=score_details_cache,
    )
    for candidate in ranked_candidates:
        candidate["reuse_audit"] = _reuse_audit_payload(
            target,
            _dict(candidate.get("asset")),
            debug_context,
            _match_transform_policy(candidate),
        )
    debug_record["bm25_ranked_candidates"] = [
        _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in bm25_ranked_candidates
    ]
    debug_record["embedding_ranked_candidates"] = [
        _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in embedding_ranked_candidates
    ]
    debug_record["substring_ranked_candidates"] = [
        _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in substring_ranked_candidates
    ]
    debug_record["ranked_candidates"] = [
        _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in ranked_candidates
    ]
    # 三档检索阈值（loose/medium/strict）当前不在此处闸门：真实裁决在
    # decide_reuse(policy_score, T_DIRECT=0.75/T_REJECT=0.35) + LLM review(0.60)。
    # 阈值真实恢复属行为变更，需 goldset 验证，留待后续调参阶段。
    candidates = list(ranked_candidates)
    debug_record["policy_input_candidates"] = [
        _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in candidates
    ]
    if not candidates:
        if _collect_candidates_only:
            return {
                "_reuse_candidate_collection": True,
                "target": target,
                "threshold": threshold,
                "candidates": [],
                "debug_record": debug_record,
                "empty_reason": "retrieval_no_candidate",
                "embedding_status": embedding_status,
            }
        return finish("retrieval_no_candidate")

    for candidate in candidates:
        candidate["library_dir"] = str(library_root)
        candidate["asset_root"] = str(library_root)
        candidate["library_search_order"] = _library_search_order
        candidate["_reuse_embedding_status"] = embedding_status

    if _collect_candidates_only:
        debug_record["decision"] = {
            "reused": False,
            "reason": "candidate_collection_only",
            "threshold_used": debug_record.get("threshold_used"),
            "policy_input_candidate_count": len(candidates),
        }
        return {
            "_reuse_candidate_collection": True,
            "target": target,
            "threshold": threshold,
            "candidates": candidates,
            "debug_record": debug_record,
            "embedding_status": embedding_status,
        }

    policy_outcome = _apply_reuse_policy_to_ranked_candidates(
        target,
        candidates,
        threshold=threshold,
        embedding_status=embedding_status,
        df_ratio_lookup={},
        keyword_client=keyword_client,
        reuse_session_state=reuse_session_state,
        llm_review_enabled=llm_review_enabled,
    )
    accepted_candidates = policy_outcome["accepted_candidates"]
    rejected_by_policy = policy_outcome["rejected_by_policy"]
    rejected_by_occupancy = policy_outcome["rejected_by_occupancy"]
    debug_record["llm_reviews_invoked"] = policy_outcome["llm_reviews_used"]
    debug_record["llm_reviews_budget"] = policy_outcome["llm_review_budget"]

    debug_record["policy_candidates"] = [
        _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in candidates
    ]
    if not accepted_candidates:
        debug_record["policy_rejected_candidates"] = [
            _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in rejected_by_policy
        ]
        debug_record["occupancy_rejected_candidates"] = [
            _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in rejected_by_occupancy
        ]
        return finish("no_candidate_after_reuse_policy_or_occupancy")

    best = accepted_candidates[0]
    reason = _reuse_accept_reason(best)
    return finish(reason, best)


def record_reused_ai_image_asset(
    *,
    session_dir: str | Path,
    session_image_path: str | Path,
    match: dict[str, Any],
) -> None:
    """Record that a session image came from the reusable asset library."""

    session_root = Path(session_dir).expanduser().resolve()
    image_path = Path(session_image_path).expanduser().resolve()
    try:
        rel_image_path = image_path.relative_to(session_root).as_posix()
    except ValueError:
        rel_image_path = _relative_output_path(image_path)

    asset = _dict(match.get("asset"))
    entry = {
        "image_path": rel_image_path,
        "reuse_asset_id": asset.get("asset_id"),
        "candidate_image_path": _relative_output_path(asset.get("image_path")),
        "reuse_library_dir": _relative_output_path(match.get("library_dir") or match.get("asset_root")),
        "keyword_score": match.get("keyword_score"),
        "score_details": match.get("score_details", {}),
        "reuse_policy": match.get("reuse_policy", {}),
        "reuse_audit": match.get("reuse_audit", {}),
        "llm_reuse_review_performed": _match_llm_reuse_review_performed(match),
        "transform_policy": _match_transform_policy(match),
        "reused_at": datetime.now(timezone.utc).isoformat(),
    }
    entry.update(_flat_reuse_audit_fields(_dict(match.get("reuse_audit"))))

    manifest_path = session_root / "materials" / REUSE_MANIFEST_FILENAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = _read_json_if_exists(manifest_path)
    entries = manifest.get("reused_assets") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        entries = []
    entries = [item for item in entries if _dict(item).get("image_path") != rel_image_path]
    entries.append(entry)
    manifest = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "reused_assets": entries,
    }
    temp_path = manifest_path.with_name(f"{manifest_path.name}.tmp")
    temp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, manifest_path)


def _match_transform_policy(match: dict[str, Any]) -> dict[str, Any]:
    policy = _dict(match.get("transform_policy"))
    if policy:
        return policy
    return _dict(_dict(match.get("score_details")).get("transform_policy"))


def _match_llm_reuse_review_performed(match: dict[str, Any]) -> bool:
    return bool(_dict(match.get("reuse_policy")).get("llm_review_performed"))


def _reuse_audit_payload(
    target: dict[str, Any],
    candidate: dict[str, Any],
    context: dict[str, Any] | None,
    transform_policy: dict[str, Any] | None,
) -> dict[str, Any]:
    context = _dict(context)

    target_theme = _clean_text(target.get("theme"))
    candidate_theme = _clean_text(candidate.get("theme"))
    target_topic_refs = _topic_refs_for_asset(target)
    candidate_topic_refs = _topic_refs_for_asset(candidate)
    topic_overlap = sorted(set(target_topic_refs) & set(candidate_topic_refs))
    target_page_number = _optional_int(context.get("page_number"))
    same_theme = bool(target_theme and candidate_theme and target_theme == candidate_theme)
    cross_theme = bool(target_theme and candidate_theme and target_theme != candidate_theme)
    return {
        "target_theme": target_theme,
        "target_topic_refs": target_topic_refs,
        "target_page_number": target_page_number,
        "candidate_theme": candidate_theme,
        "candidate_topic_refs": candidate_topic_refs,
        "same_topic_ref": bool(topic_overlap),
        "topic_ref_overlap": topic_overlap,
        "target_aspect_ratio": _clean_text(target.get("aspect_ratio")) or _clean_text(context.get("aspect_ratio")),
        "candidate_aspect_ratio": _clean_text(candidate.get("aspect_ratio")),
        "transform_policy": transform_policy or {},
        "same_theme": same_theme,
        "cross_theme": cross_theme,
        "candidate_available": bool(candidate.get("asset_id") and candidate.get("image_path")),
    }


def _strict_reuse_occupancy_status(
    candidate: dict[str, Any],
    reuse_session_state: dict[str, Any] | None,
) -> dict[str, Any]:
    asset = _dict(candidate.get("asset"))
    if reuse_session_state is None:
        return {
            "enabled": False,
            "max_per_session": STRICT_REUSE_MAX_PER_SESSION,
            "limited": _is_strict_reuse_limited_asset(asset),
            "decision": "disabled",
        }
    if not _is_strict_reuse_limited_asset(asset):
        return {
            "enabled": True,
            "max_per_session": STRICT_REUSE_MAX_PER_SESSION,
            "limited": False,
            "decision": "not_limited",
        }

    counts = _dict(reuse_session_state.get("strict_asset_use_counts"))
    used_by = _dict(reuse_session_state.get("strict_asset_used_by"))
    ids = _strict_reuse_occupancy_ids(asset)
    used_count = max([int(counts.get(asset_id) or 0) for asset_id in ids] or [0])
    occupancy = {
        "enabled": True,
        "max_per_session": STRICT_REUSE_MAX_PER_SESSION,
        "limited": True,
        "asset_ids": ids,
        "used_count": used_count,
        "used_by": {asset_id: used_by.get(asset_id, []) for asset_id in ids},
    }
    if used_count >= STRICT_REUSE_MAX_PER_SESSION:
        occupancy["decision"] = "skip_strict_asset_reuse_limit"
    else:
        occupancy["decision"] = "available_within_limit"
    return occupancy


def _is_strict_reuse_limited_asset(asset: dict[str, Any]) -> bool:
    if _clean_text(asset.get("asset_kind")) != "page_image":
        return False
    policy = normalize_reuse_policy_fields(asset)
    return policy["reuse_level"] == "strict"


def _strict_reuse_occupancy_ids(asset: dict[str, Any]) -> list[str]:
    ids = [_clean_text(asset.get("asset_id"))]
    duplicates = asset.get("duplicate_asset_ids")
    if isinstance(duplicates, list):
        ids.extend(_clean_text(item) for item in duplicates)
    return _dedupe_terms([asset_id for asset_id in ids if asset_id])


def _normalize_reuse_debug_mode(value: Any) -> str:
    mode = _clean_text(value).casefold()
    if mode in {"full", "summary", "off"}:
        return mode
    env_mode = _clean_text(os.environ.get("EDUPPTX_AI_IMAGE_REUSE_DEBUG_MODE")).casefold()
    if env_mode in {"full", "summary", "off"}:
        return env_mode
    return "full"


def _reuse_review_accepts(review: dict[str, Any]) -> bool:
    threshold = review.get("threshold", REUSE_REVIEW_ACCEPT_SCORE_THRESHOLD)
    try:
        threshold_float = float(threshold)
    except (TypeError, ValueError):
        threshold_float = REUSE_REVIEW_ACCEPT_SCORE_THRESHOLD
    return _clamp_score(review.get("score")) >= threshold_float


def _reuse_size_distance(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    if _aspect_ratio_penalty(target, candidate) < 0:
        return float("inf")
    return _aspect_ratio_loss(target, candidate)


def _reuse_static_filter_reject_reason(target: dict[str, Any], candidate: dict[str, Any]) -> str:
    """与 per-image aspect 无关的确定性硬拒。

    只依赖候选自身 + plan 常量目标字段（asset_kind / subject），因此 ""==eligible
    的判定可按 (library, group, subject, kind) 缓存。等价于 _reuse_hard_filter_reject_reason
    去掉 aspect 检查，再加上 _score_reuse_candidate_details 在上游做的 asset_kind 等值。
    作布尔使用（""==通过），分支顺序不影响结果集合。
    """
    if _clean_text(target.get("asset_kind")) != _clean_text(candidate.get("asset_kind")):
        return "asset_kind_mismatch"
    target_group = _normalize_binary_reuse_group(target.get("strict_reuse_group"), default="")
    candidate_group = _normalize_binary_reuse_group(candidate.get("strict_reuse_group"), default="")
    if target_group == _CONTENT_REUSE_GROUP:
        return "material_category_skip"
    if candidate_group == _CONTENT_REUSE_GROUP:
        return "candidate_material_category_skip"
    if target_group and candidate_group and target_group != candidate_group:
        return "strict_reuse_group_mismatch"
    subject_decision = _subject_scope_decision(target, candidate)
    if not subject_decision["compatible"]:
        return "subject_mismatch"
    return ""


def _eligible_reuse_assets(
    target: dict[str, Any],
    assets: list[Any],
    reuse_search_context: "ReuseSearchContext | None",
    library_root: Any,
    route_group: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """召回前剪枝：静态层（可缓存）+ 每图 aspect 层。

    静态层只依赖候选与 plan 常量（subject/asset_kind），按
    (library, group, subject, kind) 缓存；aspect 随图变化，不缓存。
    """
    asset_kind = _clean_text(target.get("asset_kind"))
    subject = _normalize_subject_value(target.get("subject"))
    cache_key = (str(library_root), route_group, subject, asset_kind)

    static_subset: list[dict[str, Any]] | None = None
    if reuse_search_context is not None:
        with reuse_search_context.cache_lock:
            static_subset = reuse_search_context.eligible_static_cache.get(cache_key)
    cache_hit = static_subset is not None

    if static_subset is None:
        static_subset = [
            candidate
            for candidate in assets
            if isinstance(candidate, dict)
            and _reuse_static_filter_reject_reason(target, candidate) == ""
        ]
        if reuse_search_context is not None:
            with reuse_search_context.cache_lock:
                # setdefault: 并发首次构建时所有线程共享同一子集
                static_subset = reuse_search_context.eligible_static_cache.setdefault(cache_key, static_subset)

    aspect_filtered = 0
    eligible: list[dict[str, Any]] = []
    for candidate in static_subset:
        if _aspect_ratio_penalty(target, candidate) < 0:
            aspect_filtered += 1
            continue
        eligible.append(candidate)

    summary = {
        "routed_count": sum(1 for candidate in assets if isinstance(candidate, dict)),
        "static_subset_count": len(static_subset),
        "aspect_filtered_count": aspect_filtered,
        "eligible_count": len(eligible),
        "static_cache_hit": cache_hit,
    }
    return eligible, summary


def _transform_rejects_candidate(candidate: dict[str, Any]) -> bool:
    transform_policy = _dict(candidate.get("transform_policy"))
    if not transform_policy:
        transform_policy = _dict(_dict(candidate.get("score_details")).get("transform_policy"))
    return _clean_text(transform_policy.get("decision")) == "reject"


def _embedding_rescue_decision(
    *,
    embedding_score: float | None,
    transform_rejected: bool,
    floor: float = EMBED_RESCUE_FLOOR,
) -> bool:
    """True iff a policy-score rejected candidate should go to LLM review."""

    if transform_rejected:
        return False
    if embedding_score is None:
        return False
    return float(embedding_score) >= float(floor)


def _reuse_threshold_for_target(target: dict[str, Any], explicit_threshold: float | None) -> float:
    if explicit_threshold is not None:
        try:
            return max(0.0, min(1.0, float(explicit_threshold)))
        except (TypeError, ValueError):
            pass
    if _clean_text(target.get("asset_kind")) == "background":
        return BACKGROUND_REUSE_THRESHOLD
    return policy_reuse_threshold_for_target(target)
