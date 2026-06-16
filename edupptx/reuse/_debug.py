"""复用层 per-query 调试记录：构造/写入 ai_image_reuse_debug.json（线程安全 RMW）、候选/资产调试 payload、按 mode 裁剪。函数体逐字一致。"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from edupptx.reuse._util import (
    _dict,
    _read_json_if_exists,
)

# per-query 调试日志的 RMW 串行锁，随其唯一消费者 _append_reuse_debug_record 归此（M-13）。
_REUSE_DEBUG_LOCK = threading.Lock()
from edupptx.reuse._assets import (
    _asset_caption,
)
from edupptx.reuse._scoring import (
    _cached_base_reuse_score_details,
    _candidate_policy_score,
)
from edupptx.reuse._embedding import (
    _relative_output_path,
)
from edupptx.reuse._store import (
    _resolve_asset_image_path,
)
from edupptx.reuse._retrieve import (
    _debug_score_details,
)
from edupptx.reuse._review import (
    _reuse_debug_asset_payload,
)


def _relative_output_context(context: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(context or {})
    for key in (
        "reuse_library_dir",
        "library_dir",
        "asset_root",
        "output_root",
        "session_dir",
        "plan_file",
        "debug_path",
        "match_index_path",
        "db_path",
    ):
        if key in payload:
            payload[key] = _relative_output_path(payload.get(key))
    return payload


def _flat_reuse_audit_fields(audit: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "target_theme",
        "target_topic_refs",
        "target_page_number",
        "candidate_theme",
        "candidate_topic_refs",
        "same_topic_ref",
        "topic_ref_overlap",
        "target_aspect_ratio",
        "candidate_aspect_ratio",
        "same_theme",
        "cross_theme",
        "candidate_available",
    )
    return {key: audit.get(key) for key in keys if key in audit}


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _new_reuse_debug_record(
    *,
    library_root: Path,
    db_path: Path,
    match_index_path: Path,
    asset_count: int,
    candidate_limit: int,
    min_keyword_score: float | None,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "context": _relative_output_context(context),
        "asset_root": _relative_output_path(library_root),
        "db_path": _relative_output_path(db_path),
        "match_index_path": _relative_output_path(match_index_path),
        "asset_count": asset_count,
        "candidate_limit": candidate_limit,
        "min_keyword_score": min_keyword_score,
        "threshold_used": min_keyword_score,
        "target": {},
        "candidate_scores": [],
        "ranked_candidates": [],
        "policy_input_candidates": [],
        "decision": {},
    }


def _append_reuse_debug_record(path: str | Path | None, record: dict[str, Any]) -> None:
    if path is None or not record:
        return
    debug_path = Path(path).expanduser()
    with _REUSE_DEBUG_LOCK:
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        existing = _read_json_if_exists(debug_path)
        queries = existing.get("queries") if isinstance(existing, dict) else None
        if not isinstance(queries, list):
            queries = []
        queries.append(record)
        payload = {
            "schema_version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "queries": queries,
        }
        temp_path = debug_path.with_name(
            f"{debug_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp_path, debug_path)


def _reuse_debug_record_for_mode(
    record: dict[str, Any],
    *,
    mode: str,
    match: dict[str, Any] | None,
) -> dict[str, Any]:
    if mode == "off":
        return {}
    if mode == "full":
        return record

    summary = {
        "ts": record.get("ts"),
        "debug_mode": "summary",
        "context": record.get("context") or {},
        "asset_root": record.get("asset_root"),
        "db_path": record.get("db_path"),
        "match_index_path": record.get("match_index_path"),
        "asset_count": record.get("asset_count"),
        "candidate_limit": record.get("candidate_limit"),
        "threshold_used": record.get("threshold_used"),
        "llm_review_enabled": bool(record.get("llm_review_enabled")),
        "embedding_index": record.get("embedding_index") or {},
        "target": record.get("target") or {},
        "decision": record.get("decision") or {},
    }
    if match is not None:
        summary["reused_asset"] = _reuse_debug_candidate_summary(
            _reuse_debug_candidate_payload(match, threshold=_optional_float(record.get("threshold_used")))
        )
    else:
        summary["no_reuse_top_candidates"] = _reuse_no_match_top_candidate_summaries(record, limit=2)
    return summary


def _reuse_no_match_top_candidate_summaries(record: dict[str, Any], *, limit: int = 2) -> list[dict[str, Any]]:
    for key in ("policy_candidates", "policy_input_candidates", "ranked_candidates", "candidate_scores"):
        candidates = record.get(key)
        if isinstance(candidates, list) and candidates:
            return [_reuse_debug_candidate_summary(item) for item in candidates[:limit] if isinstance(item, dict)]
    return []


def _reuse_debug_candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    policy = _dict(candidate.get("reuse_policy"))
    audit = _dict(candidate.get("reuse_audit"))
    llm_review_performed = bool(policy.get("llm_review_performed"))
    payload = {
        "asset_id": candidate.get("asset_id"),
        "image_path": _relative_output_path(candidate.get("image_path")),
        "candidate_image_path": _relative_output_path(candidate.get("candidate_image_path")),
        "caption": _asset_caption(candidate),
        "reuse_level": candidate.get("reuse_level"),
        "keyword_score": candidate.get("keyword_score"),
        "embedding_score": candidate.get("embedding_score"),
        "substring_score": candidate.get("substring_score"),
        "policy_score": candidate.get("policy_score"),
        "hybrid_score": candidate.get("hybrid_score"),
        "score_gap_to_threshold": candidate.get("score_gap_to_threshold"),
        "reuse_audit": audit,
        "llm_reuse_review_performed": llm_review_performed,
        "reuse_policy": {
            "decision": policy.get("decision"),
            "reason": policy.get("reason"),
            "missing": policy.get("missing") or [],
            "conflicts": policy.get("conflicts") or [],
            "review_items": policy.get("review_items") or [],
            "llm_review_required": bool(policy.get("llm_review_required")),
            "llm_review_performed": llm_review_performed,
            "llm_review": policy.get("llm_review") or {},
        },
        "strict_reuse_occupancy": candidate.get("strict_reuse_occupancy") or {},
    }
    payload.update(_flat_reuse_audit_fields(audit))
    return payload


def _reuse_debug_candidate_payload(candidate: dict[str, Any], *, threshold: float | None = None) -> dict[str, Any]:
    payload = _reuse_debug_asset_payload(_dict(candidate.get("asset")))
    payload["keyword_score"] = candidate.get("keyword_score")
    payload["embedding_score"] = candidate.get("embedding_score")
    payload["substring_score"] = candidate.get("substring_score")
    payload["policy_score"] = candidate.get("policy_score") or _candidate_policy_score(candidate)
    payload["hybrid_score"] = candidate.get("hybrid_score")
    payload["rrf_score"] = candidate.get("rrf_score")
    payload["retrieval_ranks"] = candidate.get("retrieval_ranks") or {}
    payload["substring_hits"] = candidate.get("substring_hits") or []
    payload["candidate_image_path"] = _relative_output_path(candidate.get("candidate_image_path"))
    payload["score_details"] = candidate.get("score_details") or {}
    payload["reuse_policy"] = candidate.get("reuse_policy") or {}
    payload["reuse_audit"] = candidate.get("reuse_audit") or {}
    payload.update(_flat_reuse_audit_fields(_dict(payload["reuse_audit"])))
    payload["llm_reuse_review_performed"] = bool(_dict(payload["reuse_policy"]).get("llm_review_performed"))
    payload["strict_reuse_occupancy"] = candidate.get("strict_reuse_occupancy") or {}
    if threshold is not None:
        payload["threshold_used"] = threshold
        payload["score_gap_to_threshold"] = round(float(candidate.get("keyword_score") or 0.0) - threshold, 4)
    return payload


def _collect_reuse_candidate_debug(
    target: dict[str, Any],
    assets: list[Any],
    library_root: Path,
    score_details_cache: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        payload = _reuse_debug_asset_payload(item)
        image_path = _resolve_asset_image_path(library_root, item.get("image_path"))
        if image_path is None or not image_path.exists():
            payload["keyword_score"] = 0.0
            payload["candidate_image_path"] = _relative_output_path(image_path)
            payload["score_details"] = {
                "score": 0.0,
                "reject_reason": "missing_candidate_image",
            }
            rows.append(payload)
            continue

        details = _cached_base_reuse_score_details(target, item, score_details_cache)
        score = float(details.get("score") or 0.0)
        payload["keyword_score"] = round(score, 4)
        payload["candidate_image_path"] = _relative_output_path(image_path)
        payload["score_details"] = _debug_score_details(details)
        rows.append(payload)

    rows.sort(key=lambda item: float(item.get("keyword_score") or 0.0), reverse=True)
    return rows
