"""Metrics for staged AI-image reuse evaluation."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


def safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _labeled_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("label_status", "labeled") == "labeled"]


def candidate_filter_metrics(rows: Iterable[dict[str, Any]], *, pass_field: str) -> dict[str, Any]:
    all_rows = list(rows)
    eval_rows = _labeled_rows(all_rows)

    passed_pairs = sum(1 for row in all_rows if bool(row.get(pass_field)))
    acceptable_pairs = sum(1 for row in eval_rows if bool(row.get("is_acceptable")))
    tp = sum(1 for row in eval_rows if bool(row.get(pass_field)) and bool(row.get("is_acceptable")))
    fp = sum(1 for row in eval_rows if bool(row.get(pass_field)) and not bool(row.get("is_acceptable")))
    fn = sum(1 for row in eval_rows if not bool(row.get(pass_field)) and bool(row.get("is_acceptable")))
    tn = sum(1 for row in eval_rows if not bool(row.get(pass_field)) and not bool(row.get("is_acceptable")))

    precision = safe_div(tp, tp + fp)
    recall = safe_div(tp, tp + fn)
    f1 = safe_div(2 * precision * recall, precision + recall)

    return {
        "total_pairs": len(all_rows),
        "labeled_pairs": len(eval_rows),
        "passed_pairs": passed_pairs,
        "rejected_pairs": len(all_rows) - passed_pairs,
        "acceptable_pairs": acceptable_pairs,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "pass_rate": safe_div(passed_pairs, len(all_rows)),
        "wrong_pass_rate": safe_div(fp, len(eval_rows)),
        "hit_in_passed_rate": precision,
        "acceptable_kept_rate": recall,
    }


def ranking_metrics(
    rows: Iterable[dict[str, Any]],
    *,
    reusable_need_ids: set[str],
    rank_field: str,
    ks: tuple[int, ...] = (1, 3, 5, 8),
) -> dict[str, Any]:
    by_need: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        need_id = str(row.get("need_id") or "")
        if need_id:
            by_need[need_id].append(row)

    best_ranks: dict[str, int] = {}
    for need_id in reusable_need_ids:
        ranks: list[int] = []
        for row in by_need.get(need_id, []):
            if not row.get("is_acceptable"):
                continue
            try:
                rank = int(row.get(rank_field) or 0)
            except (TypeError, ValueError):
                rank = 0
            if rank > 0:
                ranks.append(rank)
        if ranks:
            best_ranks[need_id] = min(ranks)

    total = len(reusable_need_ids)
    metrics: dict[str, Any] = {
        "reusable_need_count": total,
        "candidate_hit_need_count": len(best_ranks),
        "candidate_hit_rate": safe_div(len(best_ranks), total),
    }
    for k in ks:
        hit_count = sum(1 for rank in best_ranks.values() if rank <= k)
        metrics[f"top_{k}_recall"] = safe_div(hit_count, total)
    return metrics


def final_match_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    eval_rows = _labeled_rows(list(rows))
    reusable_rows = [row for row in eval_rows if row.get("should_reuse") is True]

    selected_rows = [row for row in eval_rows if str(row.get("selected_asset_id") or "")]
    correct_selected = [row for row in selected_rows if bool(row.get("selected_is_acceptable"))]
    wrong_selected = [row for row in selected_rows if not bool(row.get("selected_is_acceptable"))]
    missed_reusable = [
        row
        for row in reusable_rows
        if not str(row.get("selected_asset_id") or "")
    ]

    precision = safe_div(len(correct_selected), len(selected_rows))
    recall = safe_div(len(correct_selected), len(reusable_rows))
    f1 = safe_div(2 * precision * recall, precision + recall)

    return {
        "labeled_needs": len(eval_rows),
        "reusable_need_count": len(reusable_rows),
        "selected_count": len(selected_rows),
        "correct_selected_count": len(correct_selected),
        "wrong_selected_count": len(wrong_selected),
        "missed_reusable_count": len(missed_reusable),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "correct_match_rate": safe_div(len(correct_selected), len(eval_rows)),
        "wrong_match_rate": safe_div(len(wrong_selected), len(eval_rows)),
        "no_match_rate": safe_div(len(eval_rows) - len(selected_rows), len(eval_rows)),
    }
