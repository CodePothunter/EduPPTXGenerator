"""Cross-session observability for the AI image reuse pipeline.

This module is the home for two complementary outputs:

* **Per-session logical-need summary**
  (:func:`write_reuse_logical_summary` → ``ai_image_reuse_debug_summary.json``)

  The existing ``ai_image_reuse_debug.json`` records *per-library queries*.
  When a session queries two libraries per slot, that file balloons to
  ``2 × needs`` rows, so anyone reading the file has to manually group rows
  by ``(page_number, slot_key)`` before they can reason about coverage. The
  summary file inverts that view: one row per logical image need, listing
  the libraries searched, the best candidate seen, and the final decision.

* **Cross-session coverage gap log**
  (:func:`append_coverage_gap_event` → ``materials_library_coverage_log.jsonl``)

  An append-only JSONL that captures *what the library failed to serve*
  — useful for prioritising library expansion rather than tuning thresholds.

Both outputs are derived from the same in-session debug records, so they
share the same source of truth. The functions are intentionally tolerant
of partial / malformed inputs — they should never raise from the main
generation path.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


DEFAULT_SUMMARY_FILENAME = "ai_image_reuse_debug_summary.json"
DEFAULT_COVERAGE_LOG_FILENAME = "materials_library_coverage_log.jsonl"
COVERAGE_GAP_EMBEDDING_CEILING = 0.60  # threshold below which a need is
# considered uncovered (i.e. there is no candidate with even a moderate
# semantic match). Set above the canonical retrieval threshold so the log
# captures meaningful gaps, not edge cases.

SUMMARY_SCHEMA_VERSION = 1
COVERAGE_LOG_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Reading the per-library debug file
# ---------------------------------------------------------------------------

def load_debug_records(debug_path: str | Path) -> list[dict[str, Any]]:
    """Read the canonical per-library debug file.

    Returns the ``queries`` list. Missing / empty / malformed files return
    an empty list so callers can iterate unconditionally.
    """

    path = Path(debug_path)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    queries = payload.get("queries") if isinstance(payload, dict) else None
    return [q for q in (queries or []) if isinstance(q, dict)]


# ---------------------------------------------------------------------------
# Logical-need summary (Q6)
# ---------------------------------------------------------------------------

def _need_key(record: dict[str, Any]) -> tuple[Any, str]:
    ctx = record.get("context") if isinstance(record.get("context"), dict) else {}
    return ctx.get("page_number"), str(ctx.get("slot_key") or "")


def _library_label(record: dict[str, Any]) -> str:
    ctx = record.get("context") if isinstance(record.get("context"), dict) else {}
    library = str(ctx.get("reuse_library_dir") or record.get("asset_root") or "")
    if not library:
        return ""
    return Path(library).name


def _best_candidate_summary(record: dict[str, Any]) -> dict[str, Any] | None:
    """Pick the best candidate from a single per-library debug record.

    "Best" here is the first entry of ``no_reuse_top_candidates`` (which the
    upstream debug writer already sorts by overall reuse signal), or — for
    the matched case — the asset surfaced by ``decision.asset_id``.
    """

    decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
    if decision.get("reused"):
        return {
            "asset_id": str(decision.get("asset_id") or ""),
            "keyword_score": _maybe_float(decision.get("keyword_score")),
            "embedding_score": None,
            "policy_reason": str(decision.get("reason") or "reused"),
            "via_llm_review": bool(decision.get("llm_reuse_review_performed")),
        }

    tops = record.get("no_reuse_top_candidates") or []
    if not isinstance(tops, list) or not tops:
        return None
    # Sort defensively in case the writer changes ordering: pick by embedding
    # score then keyword score, falling back to the natural list order.
    def _score_key(item: Any) -> tuple[float, float]:
        if not isinstance(item, dict):
            return (0.0, 0.0)
        return (_maybe_float(item.get("embedding_score")) or 0.0, _maybe_float(item.get("keyword_score")) or 0.0)

    best = max(tops, key=_score_key)
    if not isinstance(best, dict):
        return None
    policy = best.get("reuse_policy") if isinstance(best.get("reuse_policy"), dict) else {}
    return {
        "asset_id": str(best.get("asset_id") or ""),
        "keyword_score": _maybe_float(best.get("keyword_score")),
        "embedding_score": _maybe_float(best.get("embedding_score")),
        "policy_reason": str(policy.get("reason") or decision.get("reason") or ""),
        "via_llm_review": bool(best.get("llm_reuse_review_performed")),
    }


def _failure_category(record: dict[str, Any], best: dict[str, Any] | None) -> str:
    """Coarse failure-category tag used by both summary and coverage log.

    The categories mirror the analysis in the deep-dive report and remain
    *structural*: no subject keyword is referenced.
    """

    decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
    if decision.get("reused"):
        return "matched"
    reason = str(decision.get("reason") or "")
    if reason == "no_candidate_above_reuse_threshold":
        return "no_candidate_above_threshold"
    if best is None:
        return "no_candidate"
    embed = best.get("embedding_score") or 0.0
    if embed >= 0.7:
        return "high_semantic_rejected_by_policy"
    if embed >= 0.55:
        return "near_match_rejected_by_policy"
    return "low_semantic_signal"


def write_reuse_logical_summary(
    debug_path: str | Path,
    *,
    summary_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Group per-library debug records into one row per logical image need.

    The output file lives next to the per-library debug file by default
    (``ai_image_reuse_debug_summary.json``). Pass ``summary_path`` to
    override.

    Returns the written summary dict, or ``None`` if there was nothing to
    summarise (typically because the per-library file was absent).
    """

    records = load_debug_records(debug_path)
    if not records:
        return None

    grouped: dict[tuple[Any, str], list[dict[str, Any]]] = {}
    order: list[tuple[Any, str]] = []
    for record in records:
        key = _need_key(record)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(record)

    summary_records: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    matched_count = 0
    for key in order:
        rows = grouped[key]
        page_number, slot_key = key
        target = rows[0].get("target") if isinstance(rows[0].get("target"), dict) else {}
        libraries: list[str] = []
        candidates: list[dict[str, Any]] = []
        final_decision: str = "no_match"
        final_asset: str = ""
        final_via_llm = False
        for row in rows:
            label = _library_label(row)
            if label and label not in libraries:
                libraries.append(label)
            best = _best_candidate_summary(row)
            if best is not None:
                best["library"] = label
                candidates.append(best)
            decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
            if decision.get("reused"):
                final_decision = "matched"
                final_asset = str(decision.get("asset_id") or "")
                final_via_llm = bool(decision.get("llm_reuse_review_performed"))

        # Combined best across libraries (for the logical summary row)
        combined_best: dict[str, Any] | None = None
        if candidates:
            def _score_key(cand: dict[str, Any]) -> tuple[float, float]:
                return (
                    _maybe_float(cand.get("embedding_score")) or 0.0,
                    _maybe_float(cand.get("keyword_score")) or 0.0,
                )
            combined_best = max(candidates, key=_score_key)

        category = (
            "matched" if final_decision == "matched"
            else _failure_category(rows[-1], combined_best)
        )
        category_counts[category] += 1
        if final_decision == "matched":
            matched_count += 1

        summary_records.append({
            "page_number": page_number,
            "slot_key": slot_key,
            "target_summary": _target_summary(target),
            "searched_libraries": libraries,
            "per_library_candidates": candidates,
            "best_candidate": combined_best,
            "final_decision": final_decision,
            "final_asset_id": final_asset,
            "final_via_llm_review": final_via_llm,
            "failure_category": category,
        })

    summary_payload: dict[str, Any] = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "logical_check_count": len(summary_records),
        "matched_count": matched_count,
        "match_rate": round(matched_count / max(1, len(summary_records)), 4),
        "category_counts": dict(category_counts),
        "logical_checks": summary_records,
    }

    output_path = Path(summary_path) if summary_path else Path(debug_path).with_name(DEFAULT_SUMMARY_FILENAME)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return summary_payload
    return summary_payload


def _target_summary(target: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(target, dict):
        return {}
    prompt = target.get("caption") or target.get("generation_prompt") or ""
    prompt = str(prompt).strip()
    return {
        "subject": str(target.get("subject") or ""),
        "grade": str(target.get("grade_norm") or target.get("grade") or ""),
        "theme": str(target.get("theme") or ""),
        "role": str(target.get("role") or ""),
        "asset_kind": str(target.get("asset_kind") or ""),
        "topic_refs": list(target.get("topic_refs") or []),
        "core_keywords": list(target.get("core_keywords") or []),
        "prompt_excerpt": prompt[:160],
        "reuse_level": str(target.get("reuse_level") or ""),
        "aspect_ratio": str(target.get("aspect_ratio") or ""),
    }


# ---------------------------------------------------------------------------
# Coverage gap log (R6)
# ---------------------------------------------------------------------------

def append_coverage_gap_events(
    debug_path: str | Path,
    *,
    log_path: str | Path,
) -> int:
    """Scan one session's debug file and append coverage-gap events.

    A coverage gap is recorded when, across all libraries, **no candidate
    had an embedding score >= COVERAGE_GAP_EMBEDDING_CEILING** for a
    logical need. This captures cases where the library has nothing
    even moderately relevant — the cases where retrieval/threshold
    tuning cannot help and the right action is to expand the library.

    Returns the number of events appended.
    """

    records = load_debug_records(debug_path)
    if not records:
        return 0

    grouped: dict[tuple[Any, str], list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(_need_key(record), []).append(record)

    appended = 0
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        for key, rows in grouped.items():
            # Skip needs that ended up matched — coverage is not the issue
            if any(_dict(row.get("decision")).get("reused") for row in rows):
                continue
            best_embed = 0.0
            best_record: dict[str, Any] | None = None
            target = rows[0].get("target") if isinstance(rows[0].get("target"), dict) else {}
            for row in rows:
                best = _best_candidate_summary(row)
                if best is None:
                    continue
                embed = best.get("embedding_score") or 0.0
                if embed > best_embed:
                    best_embed = embed
                    best_record = best
            if best_embed >= COVERAGE_GAP_EMBEDDING_CEILING:
                continue
            event = {
                "schema_version": COVERAGE_LOG_SCHEMA_VERSION,
                "ts": _now_iso(),
                "session_debug_path": str(debug_path),
                "page_number": key[0],
                "slot_key": key[1],
                "subject": str(target.get("subject") or ""),
                "grade": str(target.get("grade_norm") or target.get("grade") or ""),
                "topic_refs": list(target.get("topic_refs") or []),
                "target_prompt_summary": str(target.get("caption") or "").strip()[:200],
                "best_embedding": round(best_embed, 4) if best_embed else 0.0,
                "best_candidate_asset_id": (best_record or {}).get("asset_id") or "",
            }
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            appended += 1
    return appended


def aggregate_coverage_log(log_path: str | Path) -> dict[str, Any]:
    """Aggregate the coverage log into a heatmap suitable for prioritising
    library expansion."""

    path = Path(log_path)
    if not path.exists():
        return {"events": 0, "by_subject_topic": {}, "by_grade_subject": {}}

    by_subject_topic: Counter[tuple[str, str]] = Counter()
    by_grade_subject: Counter[tuple[str, str]] = Counter()
    total = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            subject = str(row.get("subject") or "unknown")
            grade = str(row.get("grade") or "unknown")
            topic_refs = row.get("topic_refs") or []
            for topic in topic_refs or ["(no_topic)"]:
                by_subject_topic[(subject, str(topic))] += 1
            by_grade_subject[(grade, subject)] += 1
    return {
        "events": total,
        "by_subject_topic": {
            f"{s} / {t}": n for (s, t), n in sorted(by_subject_topic.items(), key=lambda kv: -kv[1])
        },
        "by_grade_subject": {
            f"{g} / {s}": n for (g, s), n in sorted(by_grade_subject.items(), key=lambda kv: -kv[1])
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
