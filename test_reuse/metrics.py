"""Metrics for staged AI-image reuse evaluation."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


def safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _labeled_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("label_status", "labeled") == "labeled"]


def _aspect_orientation(value: str) -> str:
    """Classify a 'W:H' aspect string as landscape, portrait, neutral, or unknown."""

    text = str(value or "").strip()
    if ":" not in text:
        return "unknown"
    w_text, _, h_text = text.partition(":")
    try:
        w, h = float(w_text), float(h_text)
    except ValueError:
        return "unknown"
    if w <= 0 or h <= 0:
        return "unknown"
    ratio = w / h
    if ratio > 1.0:
        return "landscape"
    if ratio < 1.0:
        return "portrait"
    return "neutral"


def _is_opposite_orientation(target_aspect: str, candidate_aspect: str) -> bool:
    """True iff target and candidate sit on opposite landscape/portrait sides."""

    return {_aspect_orientation(target_aspect), _aspect_orientation(candidate_aspect)} == {
        "landscape",
        "portrait",
    }


def drop_opposite_orientation_gold_pairs(
    rows: list[dict],
    asset_aspect_by_id: dict[str, str],
) -> list[dict]:
    """Remove gold asset ids whose aspect is opposite-orientation vs the target."""

    cleaned: list[dict] = []
    for row in rows:
        target = row.get("target") if isinstance(row.get("target"), dict) else {}
        target_aspect = str(target.get("aspect_ratio") or row.get("aspect_ratio") or "")

        def keep(asset_id: str) -> bool:
            return not _is_opposite_orientation(target_aspect, asset_aspect_by_id.get(asset_id, ""))

        new_row = dict(row)
        new_row["acceptable_asset_ids"] = [
            asset_id for asset_id in (row.get("acceptable_asset_ids") or []) if keep(asset_id)
        ]
        new_row["best_asset_ids"] = [
            asset_id for asset_id in (row.get("best_asset_ids") or []) if keep(asset_id)
        ]
        cleaned.append(new_row)
    return cleaned


def reject_reason_by_gold_crosstab(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Per policy_reason: how many gold vs non-gold candidates it rejected."""

    table: dict[str, dict[str, int]] = {}
    for row in rows:
        reason = str(row.get("policy_reason") or "").strip() or "<none>"
        bucket = table.setdefault(reason, {"gold": 0, "non_gold": 0})
        bucket["gold" if row.get("is_acceptable") else "non_gold"] += 1
    return table


def missed_gold_diagnostics(
    final_rows: list[dict],
    policy_rows_by_need: dict[str, list[dict]],
) -> list[dict]:
    """One row per missed reusable need with the best gold's scores and gate."""

    out: list[dict] = []
    for final_row in final_rows:
        if final_row.get("match_status") != "missed":
            continue
        need_id = final_row.get("need_id")
        best_ids = set(final_row.get("best_asset_ids") or [])
        gold_rows = [
            row
            for row in policy_rows_by_need.get(str(need_id), [])
            if row.get("is_acceptable")
        ]
        gold_rows.sort(key=lambda row: float(row.get("embedding_score") or 0.0), reverse=True)
        top = next((row for row in gold_rows if row.get("asset_id") in best_ids), None) or (
            gold_rows[0] if gold_rows else {}
        )
        out.append(
            {
                "need_id": need_id,
                "waterfall_stage": final_row.get("waterfall_stage"),
                "best_gold_asset_id": top.get("asset_id", ""),
                "keyword_score": top.get("keyword_score"),
                "embedding_score": top.get("embedding_score"),
                "substring_score": top.get("substring_score"),
                "policy_score": top.get("policy_score"),
                "policy_decision": top.get("policy_decision", ""),
                "policy_reason": top.get("policy_reason", ""),
                "gold_in_scored_set": bool(gold_rows),
            }
        )
    return out


def floor_sweep_recall_precision(
    gold_candidates: list[dict],
    *,
    total_reusable_needs: int,
    floors: list[float],
) -> list[dict]:
    """Upper-bound rescued recall for each candidate embedding floor."""

    out: list[dict] = []
    for floor in floors:
        needs = {
            row.get("need_id")
            for row in gold_candidates
            if row.get("is_acceptable") and float(row.get("embedding_score") or 0.0) >= floor
        }
        out.append(
            {
                "floor": floor,
                "rescued_gold_need_count": len(needs),
                "rescued_gold_recall_upper_bound": safe_div(len(needs), total_reusable_needs),
            }
        )
    return out


def gold_sets_from_targets(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, set[str] | bool]]:
    result: dict[str, dict[str, set[str] | bool]] = {}
    for row in _labeled_rows(list(rows)):
        need_id = str(row.get("need_id") or "")
        if not need_id:
            continue
        acceptable = {
            str(asset_id)
            for asset_id in (row.get("acceptable_asset_ids") or [])
            if str(asset_id or "")
        }
        best = {
            str(asset_id)
            for asset_id in (row.get("best_asset_ids") or [])
            if str(asset_id or "")
        }
        result[need_id] = {
            "acceptable": acceptable,
            "best": best,
            "should_reuse": row.get("should_reuse") is True,
        }
    return result


def gold_sets_from_targets_by_asset_kind(
    rows: Iterable[dict[str, Any]],
    *,
    asset_kind: str,
) -> dict[str, dict[str, set[str] | bool]]:
    result: dict[str, dict[str, set[str] | bool]] = {}
    expected_kind = str(asset_kind or "")
    for row in _labeled_rows(list(rows)):
        need_id = str(row.get("need_id") or "")
        if not need_id:
            continue

        acceptable = {
            str(item.get("asset_id") or "")
            for item in (row.get("acceptable_asset_metadata") or [])
            if isinstance(item, dict)
            and str(item.get("asset_kind") or "") == expected_kind
            and str(item.get("asset_id") or "")
        }
        best = {
            str(item.get("asset_id") or "")
            for item in (row.get("best_asset_metadata") or [])
            if isinstance(item, dict)
            and str(item.get("asset_kind") or "") == expected_kind
            and str(item.get("asset_id") or "")
        }
        result[need_id] = {
            "acceptable": acceptable,
            "best": best,
            "should_reuse": bool(acceptable),
        }
    return result


def _relabel_rows_for_gold_sets(
    rows: Iterable[dict[str, Any]],
    gold_sets: dict[str, dict[str, set[str] | bool]],
) -> list[dict[str, Any]]:
    relabeled: list[dict[str, Any]] = []
    for row in rows:
        need_id = str(row.get("need_id") or "")
        asset_id = str(row.get("asset_id") or "")
        sets = gold_sets.get(need_id, {})
        acceptable = sets.get("acceptable") if isinstance(sets, dict) else set()
        best = sets.get("best") if isinstance(sets, dict) else set()
        item = dict(row)
        item["is_acceptable"] = asset_id in acceptable if isinstance(acceptable, set) else False
        item["is_best"] = asset_id in best if isinstance(best, set) else False
        relabeled.append(item)
    return relabeled


def relabel_rows_for_gold_sets(
    rows: Iterable[dict[str, Any]],
    gold_sets: dict[str, dict[str, set[str] | bool]],
) -> list[dict[str, Any]]:
    return _relabel_rows_for_gold_sets(rows, gold_sets)


def size_compatible_gold_sets_from_hard_rows(
    targets: Iterable[dict[str, Any]],
    hard_rows: Iterable[dict[str, Any]],
) -> dict[str, dict[str, set[str] | bool]]:
    gold_sets = gold_sets_from_targets(targets)
    size_pass_candidate_pairs: set[tuple[str, str]] = set()
    for row in _labeled_rows(list(hard_rows)):
        if row.get("size_only_pass") is not True:
            continue
        need_id = str(row.get("need_id") or "")
        asset_id = str(row.get("asset_id") or "")
        if need_id and asset_id:
            size_pass_candidate_pairs.add((need_id, asset_id))

    adjusted: dict[str, dict[str, set[str] | bool]] = {}
    for need_id, sets in gold_sets.items():
        acceptable = sets.get("acceptable") if isinstance(sets, dict) else set()
        best = sets.get("best") if isinstance(sets, dict) else set()
        acceptable_source = acceptable if isinstance(acceptable, set) else set()
        best_source = best if isinstance(best, set) else set()
        acceptable_set = {
            asset_id
            for asset_id in acceptable_source
            if (need_id, asset_id) in size_pass_candidate_pairs
        }
        best_set = {
            asset_id
            for asset_id in best_source
            if (need_id, asset_id) in size_pass_candidate_pairs
        }
        adjusted[need_id] = {
            "acceptable": acceptable_set,
            "best": best_set,
            "should_reuse": bool(acceptable_set),
        }
    return adjusted


def size_compatible_gold_summary(
    original_gold_sets: dict[str, dict[str, set[str] | bool]],
    adjusted_gold_sets: dict[str, dict[str, set[str] | bool]],
) -> dict[str, int]:
    removed_acceptable_pairs: set[tuple[str, str]] = set()
    removed_best_pairs: set[tuple[str, str]] = set()
    original_reusable = 0
    adjusted_reusable = 0

    for need_id, original in original_gold_sets.items():
        adjusted = adjusted_gold_sets.get(need_id, {})
        original_acceptable = original.get("acceptable") if isinstance(original, dict) else set()
        original_best = original.get("best") if isinstance(original, dict) else set()
        adjusted_acceptable = adjusted.get("acceptable") if isinstance(adjusted, dict) else set()
        adjusted_best = adjusted.get("best") if isinstance(adjusted, dict) else set()

        if isinstance(original_acceptable, set) and original_acceptable:
            original_reusable += 1
        if isinstance(adjusted_acceptable, set) and adjusted_acceptable:
            adjusted_reusable += 1
        if isinstance(original_acceptable, set) and isinstance(adjusted_acceptable, set):
            for asset_id in original_acceptable - adjusted_acceptable:
                removed_acceptable_pairs.add((need_id, asset_id))
        if isinstance(original_best, set) and isinstance(adjusted_best, set):
            for asset_id in original_best - adjusted_best:
                removed_best_pairs.add((need_id, asset_id))

    return {
        "removed_acceptable_pair_count": len(removed_acceptable_pairs),
        "removed_best_pair_count": len(removed_best_pairs),
        "affected_acceptable_need_count": len({need_id for need_id, _ in removed_acceptable_pairs}),
        "affected_best_need_count": len({need_id for need_id, _ in removed_best_pairs}),
        "original_reusable_need_count": original_reusable,
        "adjusted_reusable_need_count": adjusted_reusable,
    }


def _relabel_final_rows_for_gold_sets(
    rows: Iterable[dict[str, Any]],
    gold_sets: dict[str, dict[str, set[str] | bool]],
) -> list[dict[str, Any]]:
    relabeled: list[dict[str, Any]] = []
    for row in rows:
        need_id = str(row.get("need_id") or "")
        selected_asset_id = str(row.get("selected_asset_id") or "")
        sets = gold_sets.get(need_id, {})
        acceptable = sets.get("acceptable") if isinstance(sets, dict) else set()
        best = sets.get("best") if isinstance(sets, dict) else set()
        acceptable_set = acceptable if isinstance(acceptable, set) else set()
        best_set = best if isinstance(best, set) else set()
        item = dict(row)
        item["should_reuse"] = bool(acceptable_set)
        item["selected_is_acceptable"] = selected_asset_id in acceptable_set if selected_asset_id else False
        item["selected_is_best"] = selected_asset_id in best_set if selected_asset_id else False
        relabeled.append(item)
    return relabeled


def candidate_filter_metrics(
    rows: Iterable[dict[str, Any]],
    *,
    pass_field: str,
    gold_sets: dict[str, dict[str, set[str] | bool]] | None = None,
) -> dict[str, Any]:
    all_rows = list(rows)
    eval_rows = _labeled_rows(all_rows)
    gold_acceptable_pairs: set[tuple[str, str]] = set()
    if gold_sets is not None:
        for need_id, sets in gold_sets.items():
            acceptable = sets.get("acceptable") if isinstance(sets, dict) else set()
            for asset_id in acceptable if isinstance(acceptable, set) else set():
                gold_acceptable_pairs.add((need_id, asset_id))

    passed_pairs = sum(1 for row in all_rows if bool(row.get(pass_field)))
    passed_eval_pairs = {
        (str(row.get("need_id") or ""), str(row.get("asset_id") or ""))
        for row in eval_rows
        if bool(row.get(pass_field))
    }
    if gold_sets is None:
        tp_pairs = {
            (str(row.get("need_id") or ""), str(row.get("asset_id") or ""))
            for row in eval_rows
            if bool(row.get(pass_field)) and bool(row.get("is_acceptable"))
        }
    else:
        tp_pairs = passed_eval_pairs & gold_acceptable_pairs
    tp = len(tp_pairs)
    if gold_sets is None:
        fp = sum(1 for row in eval_rows if bool(row.get(pass_field)) and not bool(row.get("is_acceptable")))
    else:
        fp = len(passed_eval_pairs - gold_acceptable_pairs)
    if gold_sets is None:
        acceptable_pairs = sum(1 for row in eval_rows if bool(row.get("is_acceptable")))
        fn = sum(1 for row in eval_rows if not bool(row.get(pass_field)) and bool(row.get("is_acceptable")))
    else:
        acceptable_pairs = len(gold_acceptable_pairs)
        fn = len(gold_acceptable_pairs - tp_pairs)
    if gold_sets is None:
        tn = sum(1 for row in eval_rows if not bool(row.get(pass_field)) and not bool(row.get("is_acceptable")))
    else:
        tn = sum(
            1
            for row in eval_rows
            if not bool(row.get(pass_field))
            and (str(row.get("need_id") or ""), str(row.get("asset_id") or "")) not in gold_acceptable_pairs
        )

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


def final_match_metrics(
    rows: Iterable[dict[str, Any]],
    *,
    gold_sets: dict[str, dict[str, set[str] | bool]] | None = None,
) -> dict[str, Any]:
    all_rows = list(rows)
    if gold_sets is not None:
        all_rows = _relabel_final_rows_for_gold_sets(all_rows, gold_sets)
    eval_rows = _labeled_rows(all_rows)
    reusable_rows = [row for row in eval_rows if row.get("should_reuse") is True]
    non_reusable_rows = [row for row in eval_rows if row.get("should_reuse") is not True]

    selected_rows = [row for row in eval_rows if str(row.get("selected_asset_id") or "")]
    correct_selected = [row for row in selected_rows if bool(row.get("selected_is_acceptable"))]
    wrong_selected = [row for row in selected_rows if not bool(row.get("selected_is_acceptable"))]
    selected_best = [row for row in correct_selected if bool(row.get("selected_is_best"))]
    correct_none = [
        row
        for row in non_reusable_rows
        if not str(row.get("selected_asset_id") or "")
    ]
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
        "selected_best_count": len(selected_best),
        "selected_best_rate": safe_div(len(selected_best), len(correct_selected)),
        "correct_none_count": len(correct_none),
        "correct_none_rate": safe_div(len(correct_none), len(non_reusable_rows)),
        "missed_reusable_count": len(missed_reusable),
        "missed_reusable_rate": safe_div(len(missed_reusable), len(reusable_rows)),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "correct_match_rate": safe_div(len(correct_selected), len(eval_rows)),
        "wrong_match_rate": safe_div(len(wrong_selected), len(eval_rows)),
        "no_match_rate": safe_div(len(eval_rows) - len(selected_rows), len(eval_rows)),
    }


def target_classification_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    eval_rows = _labeled_rows(list(rows))
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    correct = 0
    c00_label = "C00_strict_text_problem_skip"
    c00_tp = c00_fp = c00_fn = 0

    for row in eval_rows:
        gold = str(row.get("target_strict_reuse_group_gold") or "")
        predicted = str(row.get("strict_reuse_group") or "")
        confusion[gold][predicted] += 1
        if gold == predicted:
            correct += 1
        if predicted == c00_label and gold == c00_label:
            c00_tp += 1
        elif predicted == c00_label and gold != c00_label:
            c00_fp += 1
        elif predicted != c00_label and gold == c00_label:
            c00_fn += 1

    c00_precision = safe_div(c00_tp, c00_tp + c00_fp)
    c00_recall = safe_div(c00_tp, c00_tp + c00_fn)
    c00_f1 = safe_div(2 * c00_precision * c00_recall, c00_precision + c00_recall)

    return {
        "total_targets": len(eval_rows),
        "target_class_accuracy": safe_div(correct, len(eval_rows)),
        "c00_precision": c00_precision,
        "c00_recall": c00_recall,
        "c00_f1": c00_f1,
        "confusion_matrix": {
            gold: dict(predicted_counts)
            for gold, predicted_counts in confusion.items()
        },
    }


def _need_gold_sets(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, set[str]]]:
    gold_sets: dict[str, dict[str, set[str]]] = {}
    for row in _labeled_rows(list(rows)):
        need_id = str(row.get("need_id") or "")
        asset_id = str(row.get("asset_id") or "")
        if not need_id or not asset_id:
            continue
        if need_id not in gold_sets:
            gold_sets[need_id] = {"acceptable": set(), "best": set()}
        if row.get("is_acceptable"):
            gold_sets[need_id]["acceptable"].add(asset_id)
        if row.get("is_best"):
            gold_sets[need_id]["best"].add(asset_id)
    return gold_sets


def _stage_hit_metrics(
    rows: Iterable[dict[str, Any]],
    *,
    pass_field: str,
    gold_sets: dict[str, dict[str, set[str] | bool]] | None = None,
) -> dict[str, float | int]:
    eval_rows = _labeled_rows(list(rows))
    if gold_sets is None:
        gold_sets = _need_gold_sets(eval_rows)
    reusable_need_ids = {
        need_id
        for need_id, sets in gold_sets.items()
        if sets["acceptable"]
    }
    best_need_ids = {
        need_id
        for need_id, sets in gold_sets.items()
        if sets["best"]
    }

    acceptable_hit_need_ids: set[str] = set()
    best_hit_need_ids: set[str] = set()
    for row in eval_rows:
        if not row.get(pass_field):
            continue
        need_id = str(row.get("need_id") or "")
        asset_id = str(row.get("asset_id") or "")
        if asset_id in gold_sets.get(need_id, {}).get("acceptable", set()):
            acceptable_hit_need_ids.add(need_id)
        if asset_id in gold_sets.get(need_id, {}).get("best", set()):
            best_hit_need_ids.add(need_id)

    return {
        "reusable_need_count": len(reusable_need_ids),
        "candidate_hit_need_count": len(acceptable_hit_need_ids),
        "candidate_hit_rate": safe_div(len(acceptable_hit_need_ids), len(reusable_need_ids)),
        "best_need_count": len(best_need_ids),
        "best_hit_need_count": len(best_hit_need_ids),
        "best_hit_rate": safe_div(len(best_hit_need_ids), len(best_need_ids)),
    }


def hard_filter_stage_metrics(
    rows: Iterable[dict[str, Any]],
    *,
    gold_sets: dict[str, dict[str, set[str] | bool]] | None = None,
) -> dict[str, Any]:
    all_rows = list(rows)
    metrics: dict[str, Any] = {
        "pair_metrics": candidate_filter_metrics(all_rows, pass_field="all_hard_pass", gold_sets=gold_sets),
        **_stage_hit_metrics(all_rows, pass_field="all_hard_pass", gold_sets=gold_sets),
    }

    loss_by_reason: dict[str, int] = defaultdict(int)
    for row in _labeled_rows(all_rows):
        if row.get("all_hard_pass") or not row.get("is_acceptable"):
            continue
        reasons = row.get("reject_reasons") or ["unknown"]
        for reason in reasons:
            loss_by_reason[str(reason)] += 1
    metrics["loss_by_reason"] = dict(loss_by_reason)
    return metrics


def filter_ablation_metrics(
    rows: Iterable[dict[str, Any]],
    *,
    pass_fields: dict[str, str],
    gold_sets: dict[str, dict[str, set[str] | bool]] | None = None,
) -> dict[str, Any]:
    all_rows = list(rows)
    return {
        name: {
            "pair_metrics": candidate_filter_metrics(
                all_rows,
                pass_field=pass_field,
                gold_sets=gold_sets,
            ),
            **_stage_hit_metrics(all_rows, pass_field=pass_field, gold_sets=gold_sets),
        }
        for name, pass_field in pass_fields.items()
    }


def _stage_metric_bundle(
    rows: Iterable[dict[str, Any]],
    *,
    pass_field: str,
    rank_field: str,
    gold_sets: dict[str, dict[str, set[str] | bool]],
    ks: tuple[int, ...],
) -> dict[str, Any]:
    all_rows = list(rows)
    metrics: dict[str, Any] = {
        "pair_metrics": candidate_filter_metrics(all_rows, pass_field=pass_field, gold_sets=gold_sets),
        **_stage_hit_metrics(all_rows, pass_field=pass_field, gold_sets=gold_sets),
    }
    metrics.update(
        _topk_recall(
            all_rows,
            predicate_key="is_acceptable",
            rank_field=rank_field,
            ks=ks,
            gold_sets=gold_sets,
        )
    )
    metrics.update(
        _topk_recall(
            all_rows,
            predicate_key="is_best",
            rank_field=rank_field,
            ks=ks,
            gold_sets=gold_sets,
        )
    )
    return metrics


def asset_kind_bucket_stage_metrics(
    rows: Iterable[dict[str, Any]],
    *,
    targets: Iterable[dict[str, Any]],
    pass_field: str,
    rank_field: str = "rank_hybrid",
    asset_kinds: tuple[str, ...] = ("page_image", "background"),
    ks: tuple[int, ...] = (1, 3, 5, 8),
) -> dict[str, Any]:
    all_rows = list(rows)
    target_rows = list(targets)
    overall_gold = gold_sets_from_targets(target_rows)
    result: dict[str, Any] = {
        "overall": _stage_metric_bundle(
            _relabel_rows_for_gold_sets(all_rows, overall_gold),
            pass_field=pass_field,
            rank_field=rank_field,
            gold_sets=overall_gold,
            ks=ks,
        )
    }

    for asset_kind in asset_kinds:
        bucket_gold = gold_sets_from_targets_by_asset_kind(target_rows, asset_kind=asset_kind)
        bucket_rows = [
            row
            for row in all_rows
            if str(row.get("asset_kind") or "") == asset_kind
        ]
        result[asset_kind] = _stage_metric_bundle(
            _relabel_rows_for_gold_sets(bucket_rows, bucket_gold),
            pass_field=pass_field,
            rank_field=rank_field,
            gold_sets=bucket_gold,
            ks=ks,
        )
    return result


def _topk_recall(
    rows: Iterable[dict[str, Any]],
    *,
    predicate_key: str,
    rank_field: str,
    ks: tuple[int, ...],
    gold_sets: dict[str, dict[str, set[str] | bool]] | None = None,
) -> dict[str, float]:
    eval_rows = _labeled_rows(list(rows))
    by_need: dict[str, list[dict[str, Any]]] = defaultdict(list)
    target_need_ids: set[str] = set()
    for row in eval_rows:
        need_id = str(row.get("need_id") or "")
        if not need_id:
            continue
        by_need[need_id].append(row)
        if gold_sets is None and row.get(predicate_key):
            target_need_ids.add(need_id)
    if gold_sets is not None:
        gold_key = "best" if predicate_key == "is_best" else "acceptable"
        target_need_ids = {
            need_id
            for need_id, sets in gold_sets.items()
            if sets[gold_key]
        }

    result: dict[str, float] = {}
    metric_name = predicate_key[3:] if predicate_key.startswith("is_") else predicate_key
    for k in ks:
        hit_count = 0
        for need_id in target_need_ids:
            for row in by_need.get(need_id, []):
                if not row.get(predicate_key):
                    continue
                try:
                    rank = int(row.get(rank_field) or 0)
                except (TypeError, ValueError):
                    rank = 0
                if 0 < rank <= k:
                    hit_count += 1
                    break
        result[f"top_{k}_{metric_name}_recall"] = safe_div(
            hit_count,
            len(target_need_ids),
        )
    return result


def llm_review_stage_metrics(
    rows: Iterable[dict[str, Any]],
    *,
    policy_candidate_count: int = 0,
) -> dict[str, Any]:
    eval_rows = _labeled_rows(list(rows))
    required_rows = [
        row
        for row in eval_rows
        if row.get("llm_review_required")
    ]
    reviewed_rows = [
        row
        for row in eval_rows
        if row.get("llm_review_performed")
    ]
    accepted_rows = [
        row
        for row in reviewed_rows
        if str(row.get("decision") or "").lower() == "accept"
    ]
    rejected_rows = [
        row
        for row in reviewed_rows
        if str(row.get("decision") or "").lower() == "reject"
    ]

    correct_accepts = sum(1 for row in accepted_rows if row.get("is_acceptable"))
    wrong_accepts = len(accepted_rows) - correct_accepts
    false_rejects = sum(1 for row in rejected_rows if row.get("is_acceptable"))

    return {
        "policy_candidate_count": policy_candidate_count,
        "review_candidate_count": len(eval_rows),
        "llm_review_required_count": len(required_rows),
        "llm_review_performed_count": len(reviewed_rows),
        "reviewed_count": len(reviewed_rows),
        "accepted_count": len(accepted_rows),
        "rejected_count": len(rejected_rows),
        "correct_accept_count": correct_accepts,
        "wrong_accept_count": wrong_accepts,
        "false_reject_count": false_rejects,
        "llm_review_candidate_rate": safe_div(len(eval_rows), policy_candidate_count),
        "llm_review_required_rate": safe_div(len(required_rows), policy_candidate_count),
        "llm_review_performed_rate": safe_div(len(reviewed_rows), policy_candidate_count),
        "llm_accept_correctness_rate": safe_div(correct_accepts, len(accepted_rows)),
        "llm_wrong_accept_rate": safe_div(wrong_accepts, len(accepted_rows)),
        "llm_false_reject_rate": safe_div(false_rejects, len(rejected_rows)),
    }
