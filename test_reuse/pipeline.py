"""Independent staged evaluation flow for AI-image reuse."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from edupptx.config import Config
from edupptx.llm_client import create_llm_client
from edupptx.materials.ai_image_asset_db import (
    CONTENT_REUSE_GROUP,
    DEFAULT_REUSE_MAX_WORKERS,
    ReuseSearchContext,
    _aspect_ratio_penalty,
    _build_reuse_target_asset,
    _candidate_unknown_fields_for_reuse,
    _enrich_reuse_target_keywords_once,
    _finalize_reuse_candidate_collection,
    _load_reuse_library_for_search,
    _normalize_binary_reuse_group,
    _prewarm_reuse_target_keywords,
    _reuse_hard_filter_reject_reason,
    _review_reuse_candidate_with_llm,
    _route_match_index_for_target_cached,
    _subject_scope_decision,
    _strict_reuse_occupancy_status,
    _target_keyword_cache_key,
    find_reusable_ai_image_asset,
    infer_grade_band,
    mark_reused_ai_image_asset_in_session,
    read_ai_image_split_match_index,
)
from edupptx.materials.image_prompt_router import build_routed_image_needs
from edupptx.models import PlanningDraft, iter_image_slot_keys
from test_reuse.metrics import (
    asset_kind_bucket_stage_metrics,
    candidate_filter_metrics,
    drop_opposite_orientation_gold_pairs,
    filter_ablation_metrics,
    floor_sweep_recall_precision,
    final_match_metrics,
    gold_sets_from_targets,
    hard_filter_stage_metrics,
    llm_review_stage_metrics,
    missed_gold_diagnostics,
    ranking_metrics,
    relabel_rows_for_gold_sets,
    reject_reason_by_gold_crosstab,
    safe_div,
    size_compatible_gold_sets_from_hard_rows,
    size_compatible_gold_summary,
    target_classification_metrics,
)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: Any) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text:
            rows.append(json.loads(text))
    return rows


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False, default=str) for row in rows]
    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return max(1, int(default))
    try:
        return max(1, int(str(raw).strip()))
    except ValueError:
        return max(1, int(default))


def _bounded_worker_count(*, item_count: int, default: int, env_name: str) -> int:
    if item_count <= 0:
        return 1
    return max(1, min(_env_positive_int(env_name, default), item_count))


def write_csv(
    path: str | Path,
    rows: Iterable[dict[str, Any]],
    *,
    fieldnames: Iterable[str],
    encoding: str = "utf-8",
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = list(fieldnames)
    with output.open("w", encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _short_reuse_group(value: Any) -> str:
    text = _clean(value)
    if len(text) >= 3 and text[0] == "C" and text[1:3].isdigit():
        return text[:3]
    return text


ARTIFACT_SCHEMA_VERSION = 2

REQUIRED_ENRICHED_TARGET_FIELDS = (
    "caption",
    "strict_reuse_group",
    "subject",
    "grade_norm",
    "grade_band",
    "match_text",
)
STRICT_REUSE_GROUPS_BY_PREFIX = {
    "C00": "C00_strict_text_problem_skip",
    "C01": "C01_irreplaceable_entity_event_action",
    "C02": "C02_generic_subject_object",
    "C03": "C03_scene_decor_container",
}
DEFAULT_TARGET_FALLBACK_GROUP = "C03_scene_decor_container"
DEFAULT_REUSE_POLICY_WORKERS = 5
REUSE_POLICY_WORKERS_ENV = "EDUPPTX_REUSE_POLICY_WORKERS"
REUSE_SEARCH_WORKERS_ENV = "EDUPPTX_REUSE_SEARCH_WORKERS"
CATEGORY_ROUTING_BASELINE = "baseline"
CATEGORY_ROUTING_MERGE_C01_C03 = "merge-c01-c03"
CATEGORY_ROUTING_MODES = (CATEGORY_ROUTING_BASELINE, CATEGORY_ROUTING_MERGE_C01_C03)
MERGE_C01_C03_GROUPS = frozenset(
    STRICT_REUSE_GROUPS_BY_PREFIX[prefix] for prefix in ("C01", "C02", "C03")
)

TARGET_CLASS_REVIEW_COLUMNS = (
    "gold_group",
    "pred_group",
    "query",
    "target_reason",
)

TARGET_CLASS_MISMATCH_SUMMARY_COLUMNS = ("count", "gold_group", "pred_group")
SIZE_FILTER_GOLD_REJECTION_BY_ASPECT_COMBO_COLUMNS = (
    "target_aspect_ratio",
    "candidate_aspect_ratio",
    "acceptable_rejected_pair_count",
    "best_rejected_pair_count",
    "acceptable_affected_need_count",
    "best_affected_need_count",
    "rejected_pair_count",
    "affected_need_count",
)
OBSOLETE_SIZE_FILTER_OUTPUT_NAMES = (
    "size_filter_rejections.csv",
    "size_filter_rejection_summary.csv",
    "size_filter_rejection_by_target.csv",
    "size_filter_target_stats_sorted.csv",
)
OBSOLETE_SIZE_FILTER_OUTPUT_DIRS = (
    "size_padding_examples",
)
SUBJECT_FILTER_FALSE_REJECTION_COLUMNS = (
    "need_id",
    "asset_id",
    "target_query",
    "target_caption",
    "candidate_query",
    "candidate_caption",
    "target_subject",
    "candidate_subject",
    "candidate_general",
    "target_strict_reuse_group",
    "candidate_strict_reuse_group",
    "target_aspect_ratio",
    "candidate_aspect_ratio",
    "is_best",
    "reject_reasons",
)
CANDIDATE_SCORE_AUDIT_COLUMNS = (
    "need_id",
    "asset_id",
    "policy_input",
    "is_acceptable",
    "is_best",
    "rank_hybrid",
    "rank_bm25",
    "rank_embedding",
    "rank_substring",
    "keyword_score",
    "embedding_score",
    "substring_score",
    "policy_score",
    "hybrid_score",
    "threshold_used",
    "policy_decision",
    "policy_reason",
)
MISSED_GOLD_DIAGNOSTIC_COLUMNS = (
    "need_id",
    "waterfall_stage",
    "best_gold_asset_id",
    "keyword_score",
    "embedding_score",
    "substring_score",
    "policy_score",
    "policy_decision",
    "policy_reason",
    "gold_in_scored_set",
)
LLM_COUNTERFACTUAL_COLUMNS = (
    "need_id",
    "asset_id",
    "embedding_score",
    "llm_score",
    "llm_decision",
    "llm_threshold",
    "llm_reason",
)

STAGE_DIR_NAMES = {
    "prepare": "01_prepare",
    "hard_filter": "02_hard_filter",
    "retrieve": "03_retrieve",
    "review": "04_review",
    "summarize": "05_summarize",
}


def stage_artifact_dir(run_dir: str | Path, stage: str) -> Path:
    stage_name = STAGE_DIR_NAMES[stage]
    return Path(run_dir) / stage_name


def stage_artifact_path(run_dir: str | Path, stage: str, filename: str) -> Path:
    return stage_artifact_dir(run_dir, stage) / filename


def stage_artifact_read_path(run_dir: str | Path, stage: str, filename: str) -> Path:
    root = Path(run_dir)
    staged = stage_artifact_path(root, stage, filename)
    return staged if staged.exists() else root / filename


def stage_artifact_exists(run_dir: str | Path, stage: str, filename: str) -> bool:
    return stage_artifact_read_path(run_dir, stage, filename).exists()


def read_jsonl_artifact(run_dir: str | Path, stage: str, filename: str) -> list[dict[str, Any]]:
    return read_jsonl(stage_artifact_read_path(run_dir, stage, filename))


def read_json_artifact(run_dir: str | Path, stage: str, filename: str, default: Any | None = None) -> Any:
    path = stage_artifact_read_path(run_dir, stage, filename)
    if not path.exists():
        return default
    return read_json(path)


def _target_payload(row: dict[str, Any]) -> dict[str, Any]:
    target = row.get("target")
    return target if isinstance(target, dict) else {}


def _normalize_strict_reuse_group_for_test(value: Any, *, default: str = DEFAULT_TARGET_FALLBACK_GROUP) -> str:
    text = _clean(value)
    if not text:
        return default
    if text in STRICT_REUSE_GROUPS_BY_PREFIX.values():
        return text
    prefix = text.split("_", 1)[0]
    return STRICT_REUSE_GROUPS_BY_PREFIX.get(prefix, default)


def normalize_category_routing(value: Any = CATEGORY_ROUTING_BASELINE) -> str:
    mode = _clean(value) or CATEGORY_ROUTING_BASELINE
    if mode not in CATEGORY_ROUTING_MODES:
        raise ValueError(f"unknown category_routing: {mode}")
    return mode


def _reuse_group_for_category_routing(value: Any) -> str:
    return _normalize_binary_reuse_group(value, default="")


def _merge_c01_c03_group(value: Any) -> bool:
    return _reuse_group_for_category_routing(value) in MERGE_C01_C03_GROUPS


def _missing_enriched_target_fields(target: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in REQUIRED_ENRICHED_TARGET_FIELDS:
        if field == "strict_reuse_group":
            if not _normalize_strict_reuse_group_for_test(target.get(field), default=""):
                missing.append(field)
        elif not _clean(target.get(field)):
            missing.append(field)
    return missing


def validate_enriched_targets(rows: Iterable[dict[str, Any]], *, stage: str) -> dict[str, Any]:
    items = list(rows)
    missing: dict[str, list[str]] = {}
    for row in items:
        need_id = _clean(row.get("need_id")) or "<unknown>"
        target = _target_payload(row)
        absent = _missing_enriched_target_fields(target)
        if absent:
            missing[need_id] = absent
    if missing:
        first_need_id = next(iter(missing))
        fields = ", ".join(missing[first_need_id])
        raise ValueError(
            f"{stage} requires enriched targets; need_id={first_need_id} missing fields: {fields}. "
            "Run `python -m test_reuse prepare --allow-llm` first."
        )
    return {
        "target_count": len(items),
        "missing_required_field_count": 0,
        "missing_required_fields": {},
    }


def _has_required_enriched_target_fields(target: dict[str, Any]) -> bool:
    return not _missing_enriched_target_fields(target)


def _fallback_enrich_target(target: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(target)
    missing_before = _missing_enriched_target_fields(enriched)
    route = enriched.get("prompt_route") if isinstance(enriched.get("prompt_route"), dict) else {}
    group = _normalize_strict_reuse_group_for_test(
        enriched.get("strict_reuse_group") or route.get("strict_reuse_group")
    )
    match_text = (
        _clean(enriched.get("match_text"))
        or _clean(enriched.get("caption"))
        or _clean(enriched.get("query"))
        or _clean(enriched.get("content_prompt"))
        or _clean(enriched.get("normalized_prompt"))
        or _clean(enriched.get("theme"))
        or _clean(enriched.get("asset_id"))
    )
    enriched["strict_reuse_group"] = group
    enriched["caption"] = _clean(enriched.get("caption")) or match_text
    enriched["match_text"] = match_text
    enriched["match_key"] = _clean(enriched.get("match_key")) or f"{_clean(enriched.get('asset_kind')) or 'page_image'}|{match_text}"
    enriched["subject"] = _clean(enriched.get("subject")) or _clean(enriched.get("subject_hint")) or "其他"
    enriched["grade_norm"] = _clean(enriched.get("grade_norm")) or _clean(enriched.get("grade_hint")) or "未知年级"
    enriched["grade_band"] = _clean(enriched.get("grade_band")) or infer_grade_band(enriched["grade_norm"])
    enriched["target_enrichment_fallback"] = True
    enriched["target_enrichment_fallback_missing_fields"] = missing_before
    signals = list(enriched.get("strict_reuse_signals") or [])
    if "target_enrichment_fallback" not in signals:
        signals.append("target_enrichment_fallback")
    enriched["strict_reuse_signals"] = signals
    enriched["strict_reuse_reason"] = (
        _clean(enriched.get("strict_reuse_reason"))
        or "LLM target enrichment did not return all required fields; filled from routed target metadata."
    )
    return enriched


def _path_fingerprint(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    payload: dict[str, Any] = {
        "path": str(resolved),
        "exists": resolved.exists(),
        "kind": "directory" if resolved.is_dir() else "file" if resolved.is_file() else "missing",
    }
    if resolved.is_file():
        data = resolved.read_bytes()
        payload["sha256"] = hashlib.sha256(data).hexdigest()
        payload["bytes"] = len(data)
    return payload


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def build_manifest(
    *,
    run_id: str,
    plan_paths: Iterable[str | Path],
    library_dirs: Iterable[str | Path],
    goldset_paths: Iterable[str | Path] = (),
    output_dir: str | Path,
    review_enabled: bool,
    allow_llm: bool,
    notes: str = "",
) -> dict[str, Any]:
    plans = [Path(path) for path in plan_paths]
    libraries = [Path(path) for path in library_dirs]
    goldsets = [Path(path) for path in goldset_paths]
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "tool": "test_reuse",
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "plan_paths": [str(path.expanduser().resolve()) for path in plans],
        "library_dirs": [str(path.expanduser().resolve()) for path in libraries],
        "goldset_paths": [str(path.expanduser().resolve()) for path in goldsets],
        "plan_fingerprints": [_path_fingerprint(path) for path in plans],
        "library_fingerprints": [_path_fingerprint(path) for path in libraries],
        "goldset_fingerprints": [_path_fingerprint(path) for path in goldsets],
        "output_dir": str(Path(output_dir).expanduser().resolve()),
        "review_enabled": bool(review_enabled),
        "allow_llm": bool(allow_llm),
        "notes": notes,
        "dataset_generation": False,
    }


def _raw_page_by_number(plan_data: dict[str, Any]) -> dict[int, dict[str, Any]]:
    pages = plan_data.get("pages") if isinstance(plan_data.get("pages"), list) else []
    out: dict[int, dict[str, Any]] = {}
    for page in pages:
        if not isinstance(page, dict):
            continue
        try:
            number = int(page.get("page_number") or 0)
        except (TypeError, ValueError):
            number = 0
        if number:
            out[number] = page
    return out


def _raw_image_at(raw_page: dict[str, Any], index: int) -> dict[str, Any]:
    needs = raw_page.get("material_needs") if isinstance(raw_page.get("material_needs"), dict) else {}
    images = needs.get("images") if isinstance(needs.get("images"), list) else []
    if 0 <= index < len(images) and isinstance(images[index], dict):
        return images[index]
    return {}


def _label_fields(raw_need: dict[str, Any]) -> dict[str, Any]:
    acceptable = raw_need.get("acceptable_asset_ids")
    best = raw_need.get("best_asset_ids")
    return {
        "label_status": _clean(raw_need.get("label_status")) or "unlabeled",
        "should_reuse": raw_need.get("should_reuse") if isinstance(raw_need.get("should_reuse"), bool) else None,
        "acceptable_asset_ids": list(acceptable) if isinstance(acceptable, list) else [],
        "best_asset_ids": list(best) if isinstance(best, list) else [],
        "label_notes": _clean(raw_need.get("label_notes")),
        "target_strict_reuse_group_gold": _clean(raw_need.get("target_strict_reuse_group_gold")),
        "target_is_c00_skip": raw_need.get("target_is_c00_skip")
        if isinstance(raw_need.get("target_is_c00_skip"), bool)
        else None,
        "gold_label_text": _clean(raw_need.get("gold_label_text")),
        "gold_label_text_source": _clean(raw_need.get("gold_label_text_source")),
    }


def _lesson_id_for_plan(source: Path, meta: dict[str, Any]) -> str:
    explicit = _clean(meta.get("lesson_id"))
    if explicit:
        return explicit
    if source.name.lower() == "plan.json" and _clean(source.parent.name):
        return source.parent.name
    return source.stem


def load_goldset_labels(goldset_paths: Iterable[str | Path]) -> dict[str, dict[str, Any]]:
    labels: dict[str, dict[str, Any]] = {}
    for goldset_path in goldset_paths:
        payload = read_json(goldset_path)
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise ValueError(f"goldset must be a list or contain an items list: {goldset_path}")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"goldset item must be an object: {goldset_path}")
            need_id = _clean(item.get("need_id"))
            if not need_id:
                raise ValueError(f"goldset item missing need_id: {goldset_path}")
            if need_id in labels:
                raise ValueError(f"duplicate goldset need_id: {need_id}")
            labels[need_id] = {
                **_label_fields(item),
                "goldset_need_id": need_id,
                "goldset_session_id": _clean(item.get("session_id")) or need_id.split(":", 1)[0],
                "goldset_page_number": item.get("page_number"),
                "goldset_role": _clean(item.get("role")),
                "goldset_query": _clean(item.get("query")),
                "goldset_caption": _clean(item.get("caption")),
            }
    return labels


def _fallback_key(*, session_id: str, page_number: Any, role: str, text: str) -> tuple[str, str, str, str] | None:
    cleaned_text = _clean(text)
    if not cleaned_text:
        return None
    try:
        page_text = str(int(page_number))
    except (TypeError, ValueError):
        page_text = _clean(page_number)
    if not session_id or not page_text or not role:
        return None
    return (session_id, page_text, role, cleaned_text)


def _gold_label_fallback_keys(label: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    keys: list[tuple[str, str, str, str]] = []
    for text in (
        label.get("goldset_caption"),
        label.get("goldset_query"),
        label.get("gold_label_text"),
    ):
        key = _fallback_key(
            session_id=_clean(label.get("goldset_session_id")),
            page_number=label.get("goldset_page_number"),
            role=_clean(label.get("goldset_role")) or "illustration",
            text=_clean(text),
        )
        if key and key not in keys:
            keys.append(key)
    return keys


def _need_fallback_keys(need: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    keys: list[tuple[str, str, str, str]] = []
    for text in (
        need.get("caption"),
        need.get("raw_query"),
        need.get("gold_label_text"),
    ):
        key = _fallback_key(
            session_id=_clean(need.get("lesson_id")),
            page_number=need.get("page_number"),
            role=_clean(need.get("role")) or "illustration",
            text=_clean(text),
        )
        if key and key not in keys:
            keys.append(key)
    return keys


def _unique_gold_fallback_index(
    gold_labels: dict[str, dict[str, Any]],
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    index: dict[tuple[str, str, str, str], dict[str, Any] | None] = {}
    for label in gold_labels.values():
        for key in _gold_label_fallback_keys(label):
            if key in index:
                index[key] = None
            else:
                index[key] = label
    return {key: label for key, label in index.items() if label is not None}


def apply_goldset_labels(
    plan_needs: Iterable[dict[str, Any]],
    gold_labels: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    fallback_index = _unique_gold_fallback_index(gold_labels)
    rows: list[dict[str, Any]] = []
    for need in plan_needs:
        row = dict(need)
        label = gold_labels.get(_clean(row.get("need_id")))
        if not label:
            for key in _need_fallback_keys(row):
                label = fallback_index.get(key)
                if label:
                    break
        if label:
            row.update(label)
        rows.append(row)
    return rows


def extract_plan_needs(plan_path: str | Path, *, run_id: str) -> list[dict[str, Any]]:
    source = Path(plan_path)
    plan_data = read_json(source)
    draft = PlanningDraft.model_validate(plan_data)
    meta = plan_data.get("meta") if isinstance(plan_data.get("meta"), dict) else {}
    raw_pages = _raw_page_by_number(plan_data if isinstance(plan_data, dict) else {})

    lesson_id = _lesson_id_for_plan(source, meta)
    subject = _clean(meta.get("subject") or plan_data.get("subject") if isinstance(plan_data, dict) else "")
    grade = _clean(meta.get("grade") or meta.get("grade_norm") or meta.get("audience"))
    grade_band = _clean(meta.get("grade_band")) or infer_grade_band(grade, meta.get("audience"), draft.meta.topic)

    rows: list[dict[str, Any]] = []
    for page in draft.pages:
        routed_image_needs = build_routed_image_needs(draft, page)
        if not routed_image_needs:
            continue
        raw_page = raw_pages.get(page.page_number, {})
        for image_index, (slot_key, need) in enumerate(iter_image_slot_keys(routed_image_needs)):
            if need.source != "ai_generate":
                continue
            raw_need = _raw_image_at(raw_page, image_index)
            rows.append(
                {
                    "run_id": run_id,
                    "need_id": f"{lesson_id}:p{page.page_number:02d}:{slot_key}",
                    "lesson_id": lesson_id,
                    "lesson_title": draft.meta.topic,
                    "subject": subject,
                    "grade": grade,
                    "grade_band": grade_band,
                    "page_number": page.page_number,
                    "page_title": page.title,
                    "page_type": str(page.page_type),
                    "slot_key": slot_key,
                    "role": need.role,
                    "raw_query": need.query,
                    "caption": need.caption,
                    "raw_aspect_ratio": need.aspect_ratio,
                    "prompt_route": dict(need.prompt_route or {}),
                    "warnings": [],
                    **_label_fields(raw_need),
                }
            )
    return rows


def _base_target_for_need(need: dict[str, Any]) -> dict[str, Any]:
    return _build_reuse_target_asset(
        asset_kind="page_image",
        prompt=_clean(need.get("raw_query")),
        prompt_route=need.get("prompt_route") if isinstance(need.get("prompt_route"), dict) else None,
        background_route=None,
        theme=_clean(need.get("lesson_title")),
        grade=_clean(need.get("grade")),
        subject=_clean(need.get("subject")),
        grade_band=_clean(need.get("grade_band")),
        page_title=_clean(need.get("page_title")),
        page_type=_clean(need.get("page_type")),
        role=_clean(need.get("role")) or "illustration",
        aspect_ratio=_clean(need.get("raw_aspect_ratio")) or "16:9",
        caption=_clean(need.get("caption")),
    )


def _target_record_from_need(need: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    fallback_enriched = bool(target.get("target_enrichment_fallback"))
    warnings = list(need.get("warnings") or [])
    if fallback_enriched and "target_enrichment_fallback" not in warnings:
        warnings.append("target_enrichment_fallback")
    return {
        "run_id": need.get("run_id"),
        "need_id": need.get("need_id"),
        "lesson_id": need.get("lesson_id"),
        "page_number": need.get("page_number"),
        "page_title": need.get("page_title"),
        "page_type": need.get("page_type"),
        "slot_key": need.get("slot_key"),
        "role": need.get("role"),
        "raw_query": need.get("raw_query"),
        "caption": target.get("caption") or "",
        "content_prompt": target.get("content_prompt") or target.get("query") or target.get("caption") or "",
        "asset_kind": target.get("asset_kind"),
        "subject": target.get("subject"),
        "grade_norm": target.get("grade_norm"),
        "grade_band": target.get("grade_band"),
        "strict_reuse_group": target.get("strict_reuse_group") or "",
        "reuse_level": target.get("reuse_level") or "",
        "aspect_ratio": target.get("aspect_ratio") or "",
        "aspect_bucket": target.get("aspect_bucket") or target.get("aspect_ratio") or "",
        "context_summary": target.get("context_summary") or "",
        "topic_refs": list(target.get("topic_refs") or []),
        "target": target,
        "field_sources": {
            "target": (
                "production_reuse_target_enrichment_fallback"
                if fallback_enriched
                else "production_reuse_target_enrichment"
            )
        },
        "field_confidence": {},
        "warnings": warnings,
        "label_status": need.get("label_status", "unlabeled"),
        "should_reuse": need.get("should_reuse"),
        "acceptable_asset_ids": list(need.get("acceptable_asset_ids") or []),
        "best_asset_ids": list(need.get("best_asset_ids") or []),
        "label_notes": need.get("label_notes", ""),
        "target_strict_reuse_group_gold": need.get("target_strict_reuse_group_gold", ""),
        "target_is_c00_skip": need.get("target_is_c00_skip"),
        "gold_label_text": need.get("gold_label_text", ""),
        "gold_label_text_source": need.get("gold_label_text_source", ""),
    }


def build_target_records(
    plan_needs: Iterable[dict[str, Any]],
    *,
    keyword_client: Any | None = None,
    require_enrichment: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    needs = list(plan_needs)
    base_targets = [_base_target_for_need(need) for need in needs]
    search_context = ReuseSearchContext()
    if require_enrichment and keyword_client is None:
        raise ValueError("prepare requires --allow-llm because enriched target fields are missing")
    if keyword_client is not None:
        _prewarm_reuse_target_keywords(
            base_targets,
            keyword_client,
            search_context.target_keyword_cache,
        )

    target_rows: list[dict[str, Any]] = []
    enrichment_rows: list[dict[str, Any]] = []
    for need, base_target in zip(needs, base_targets):
        cache_key = _target_keyword_cache_key(base_target)
        cached = search_context.target_keyword_cache.get(cache_key)
        target = dict(cached) if isinstance(cached, dict) else dict(base_target)
        if keyword_client is not None and not _has_required_enriched_target_fields(target):
            search_context.target_keyword_cache.pop(cache_key, None)
            repaired = _enrich_reuse_target_keywords_once(
                dict(base_target),
                keyword_client,
                search_context.target_keyword_cache,
            )
            if isinstance(repaired, dict):
                target = dict(repaired)
        if keyword_client is not None and not _has_required_enriched_target_fields(target):
            target = _fallback_enrich_target({**base_target, **target})
        row = _target_record_from_need(need, target)
        target_rows.append(row)
        enrichment_rows.append(
            {
                "run_id": need.get("run_id"),
                "need_id": need.get("need_id"),
                "cache_key": cache_key,
                "target": target,
                "enriched": _has_required_enriched_target_fields(target),
                "fallback_enriched": bool(target.get("target_enrichment_fallback")),
                "fallback_missing_fields": list(target.get("target_enrichment_fallback_missing_fields") or []),
            }
        )
    if require_enrichment:
        validate_enriched_targets(target_rows, stage="prepare")
    return target_rows, enrichment_rows


def _asset_id(row: dict[str, Any]) -> str:
    return _clean(row.get("asset_id") or row.get("reuse_asset_id"))


def _label_for_record(record: dict[str, Any], asset_id: str) -> dict[str, Any]:
    acceptable = set(record.get("acceptable_asset_ids") or [])
    best = set(record.get("best_asset_ids") or [])
    return {
        "label_status": record.get("label_status", "unlabeled"),
        "should_reuse": record.get("should_reuse"),
        "acceptable_asset_ids": sorted(acceptable),
        "best_asset_ids": sorted(best),
        "is_acceptable": asset_id in acceptable,
        "is_best": asset_id in best,
    }


def load_library_assets(
    library_dirs: Iterable[str | Path],
    *,
    reuse_search_context: ReuseSearchContext | None = None,
) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for library_dir in library_dirs:
        loaded = _load_reuse_library_for_search(
            Path(library_dir).expanduser().resolve(),
            reuse_search_context,
        )
        index = loaded.get("index") if isinstance(loaded.get("index"), dict) else {}
        for asset in index.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            asset_id = _asset_id(asset)
            if not asset_id or asset_id in seen:
                continue
            seen.add(asset_id)
            assets.append(dict(asset))
    return assets


def load_routed_library_assets_for_target(
    library_dirs: Iterable[str | Path],
    target: dict[str, Any],
    *,
    reuse_search_context: ReuseSearchContext | None = None,
    category_routing: str = CATEGORY_ROUTING_BASELINE,
) -> list[dict[str, Any]]:
    mode = normalize_category_routing(category_routing)
    target_kind = _clean(target.get("asset_kind"))
    use_merge_pool = (
        mode == CATEGORY_ROUTING_MERGE_C01_C03
        and target_kind != "background"
        and _merge_c01_c03_group(target.get("strict_reuse_group"))
    )
    assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for library_dir in library_dirs:
        library_root = Path(library_dir).expanduser().resolve()
        split = read_ai_image_split_match_index(library_root)
        if split is not None:
            index, match_index_path = split
        else:
            loaded = _load_reuse_library_for_search(library_root, reuse_search_context)
            index = loaded.get("index") if isinstance(loaded.get("index"), dict) else {}
            match_index_path = Path(loaded.get("match_index_path") or library_root)
        candidate_assets = index.get("assets") if isinstance(index.get("assets"), list) else []
        if use_merge_pool:
            candidate_assets = [
                asset
                for asset in candidate_assets
                if isinstance(asset, dict)
                and _merge_c01_c03_group(asset.get("strict_reuse_group"))
                and (
                    not target_kind
                    or not _clean(asset.get("asset_kind"))
                    or _clean(asset.get("asset_kind")) == target_kind
                )
            ]
        else:
            routed = _route_match_index_for_target_cached(
                library_root,
                index,
                Path(match_index_path),
                target,
                reuse_search_context,
            )
            if routed is not None:
                _routed_index, _routed_path, routed_assets, _route_group = routed
                candidate_assets = routed_assets
        for asset in candidate_assets:
            if not isinstance(asset, dict):
                continue
            asset_id = _asset_id(asset)
            if not asset_id or asset_id in seen:
                continue
            seen.add(asset_id)
            assets.append(dict(asset))
    return assets


def _hard_filter_flags(reject_reason: str) -> dict[str, bool]:
    return {
        "category_pass": reject_reason not in {
            "material_category_skip",
            "candidate_material_category_skip",
            "strict_reuse_group_mismatch",
        },
        "subject_pass": reject_reason not in {"subject_mismatch", "candidate_metadata_unknown"},
        "aspect_pass": reject_reason != "aspect_ratio_too_far",
    }


def _hard_filter_reject_reason_for_category_routing(
    target: dict[str, Any],
    candidate: dict[str, Any],
    *,
    category_routing: str = CATEGORY_ROUTING_BASELINE,
) -> str:
    reject_reason = _reuse_hard_filter_reject_reason(target, candidate)
    if category_routing != CATEGORY_ROUTING_MERGE_C01_C03 or reject_reason != "strict_reuse_group_mismatch":
        return reject_reason
    if not (
        _merge_c01_c03_group(target.get("strict_reuse_group"))
        and _merge_c01_c03_group(candidate.get("strict_reuse_group"))
    ):
        return reject_reason

    subject_decision = _subject_scope_decision(target, candidate)
    if _candidate_unknown_fields_for_reuse(candidate, subject_decision):
        return "candidate_metadata_unknown"
    if _aspect_ratio_penalty(target, candidate) < 0:
        return "aspect_ratio_too_far"
    if not subject_decision["compatible"]:
        return "subject_mismatch"
    return ""


def _category_only_pass(
    target: dict[str, Any],
    candidate: dict[str, Any],
    *,
    category_routing: str = CATEGORY_ROUTING_BASELINE,
) -> bool:
    target_group = _normalize_binary_reuse_group(target.get("strict_reuse_group"), default="")
    candidate_group = _normalize_binary_reuse_group(candidate.get("strict_reuse_group"), default="")
    if target_group == CONTENT_REUSE_GROUP:
        return False
    if candidate_group == CONTENT_REUSE_GROUP:
        return False
    if (
        category_routing == CATEGORY_ROUTING_MERGE_C01_C03
        and target_group in MERGE_C01_C03_GROUPS
        and candidate_group in MERGE_C01_C03_GROUPS
    ):
        return True
    return not bool(target_group and candidate_group and target_group != candidate_group)


def _subject_only_pass(target: dict[str, Any], candidate: dict[str, Any]) -> bool:
    subject_decision = _subject_scope_decision(target, candidate)
    if _candidate_unknown_fields_for_reuse(candidate, subject_decision):
        return False
    return bool(subject_decision.get("compatible"))


def _size_only_pass(target: dict[str, Any], candidate: dict[str, Any]) -> bool:
    return _aspect_ratio_penalty(target, candidate) >= 0


def _hard_filter_ablation_flags(
    target: dict[str, Any],
    candidate: dict[str, Any],
    *,
    category_routing: str = CATEGORY_ROUTING_BASELINE,
) -> dict[str, bool]:
    subject_pass = _subject_only_pass(target, candidate)
    size_pass = _size_only_pass(target, candidate)
    return {
        "category_only_pass": _category_only_pass(target, candidate, category_routing=category_routing),
        "subject_only_pass": subject_pass,
        "size_only_pass": size_pass,
        "subject_size_pass": subject_pass and size_pass,
    }


def hard_filter_rows_for_target(
    target_record: dict[str, Any],
    assets: Iterable[dict[str, Any]],
    *,
    category_routing: str = CATEGORY_ROUTING_BASELINE,
) -> list[dict[str, Any]]:
    mode = normalize_category_routing(category_routing)
    run_id = _clean(target_record.get("run_id"))
    need_id = _clean(target_record.get("need_id"))
    target = target_record.get("target") if isinstance(target_record.get("target"), dict) else {}
    rows: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_id = _asset_id(asset)
        if not asset_id:
            continue
        reject_reason = _hard_filter_reject_reason_for_category_routing(
            target,
            asset,
            category_routing=mode,
        )
        flags = _hard_filter_flags(reject_reason)
        rows.append(
            {
                "run_id": run_id,
                "need_id": need_id,
                "category_routing": mode,
                "asset_id": asset_id,
                "target_query": _clean(target_record.get("raw_query")) or _clean(target.get("query")),
                "target_caption": target.get("caption", ""),
                "candidate_query": asset.get("query", ""),
                "candidate_caption": asset.get("caption", ""),
                "candidate_general": asset.get("general", ""),
                "target_strict_reuse_group": target.get("strict_reuse_group", ""),
                "candidate_strict_reuse_group": asset.get("strict_reuse_group", ""),
                "target_subject": target.get("subject", ""),
                "candidate_subject": asset.get("subject", ""),
                "target_aspect_ratio": target.get("aspect_ratio", ""),
                "candidate_aspect_ratio": asset.get("aspect_ratio", ""),
                **flags,
                **_hard_filter_ablation_flags(target, asset, category_routing=mode),
                "all_hard_pass": not reject_reason,
                "reject_reasons": [reject_reason] if reject_reason else [],
                **_label_for_record(target_record, asset_id),
            }
        )
    return rows


def _rank_map(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for index, row in enumerate(rows, start=1):
        asset_id = _asset_id(row)
        if asset_id and asset_id not in out:
            out[asset_id] = index
    return out


def _by_asset(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        asset_id = _asset_id(row)
        if asset_id and asset_id not in out:
            out[asset_id] = row
    return out


def flatten_candidate_collection(
    *,
    run_id: str,
    target_record: dict[str, Any],
    collection: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    need_id = _clean(target_record.get("need_id"))
    debug = collection.get("debug_record") if isinstance(collection.get("debug_record"), dict) else {}
    bm25_rows = [row for row in debug.get("bm25_ranked_candidates") or [] if isinstance(row, dict)]
    embedding_rows = [row for row in debug.get("embedding_ranked_candidates") or [] if isinstance(row, dict)]
    substring_rows = [row for row in debug.get("substring_ranked_candidates") or [] if isinstance(row, dict)]
    ranked_rows = [row for row in debug.get("ranked_candidates") or [] if isinstance(row, dict)]
    policy_input_rows = [row for row in debug.get("policy_input_candidates") or [] if isinstance(row, dict)]

    bm25_rank = _rank_map(bm25_rows)
    embedding_rank = _rank_map(embedding_rows)
    substring_rank = _rank_map(substring_rows)
    hybrid_rank = _rank_map(ranked_rows)
    bm25_by_id = _by_asset(bm25_rows)
    embedding_by_id = _by_asset(embedding_rows)
    substring_by_id = _by_asset(substring_rows)
    ranked_by_id = _by_asset(ranked_rows)
    policy_input_ids = set(_rank_map(policy_input_rows))

    candidate_score_audit: list[dict[str, Any]] = []
    for asset_id, row in ranked_by_id.items():
        bm25 = bm25_by_id.get(asset_id, {})
        embedding = embedding_by_id.get(asset_id, {})
        substring = substring_by_id.get(asset_id, {})
        reuse_policy = row.get("reuse_policy") if isinstance(row.get("reuse_policy"), dict) else {}
        candidate_score_audit.append(
            {
                "run_id": run_id,
                "need_id": need_id,
                "asset_id": asset_id,
                "rank_hybrid": hybrid_rank.get(asset_id),
                "rank_bm25": bm25_rank.get(asset_id),
                "rank_embedding": embedding_rank.get(asset_id),
                "rank_substring": substring_rank.get(asset_id),
                "keyword_score": row.get("keyword_score", bm25.get("keyword_score")),
                "embedding_score": row.get("embedding_score", embedding.get("embedding_score")),
                "substring_score": row.get("substring_score", substring.get("substring_score")),
                "policy_score": row.get("policy_score"),
                "hybrid_score": row.get("hybrid_score"),
                "threshold_used": row.get("threshold_used") or bm25.get("threshold_used"),
                "policy_input": asset_id in policy_input_ids,
                "policy_decision": reuse_policy.get("decision", ""),
                "policy_reason": reuse_policy.get("reason", ""),
                **_label_for_record(target_record, asset_id),
            }
        )

    return {
        "candidate_score_audit": candidate_score_audit,
    }


def extract_llm_review_rows(
    *,
    run_id: str,
    target_record: dict[str, Any],
    collection: dict[str, Any],
    selected_asset_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    need_id = _clean(target_record.get("need_id"))
    for candidate in collection.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        policy = candidate.get("reuse_policy") if isinstance(candidate.get("reuse_policy"), dict) else {}
        review = policy.get("llm_review") if isinstance(policy.get("llm_review"), dict) else {}
        if not policy.get("llm_review_required") and not review:
            continue
        asset = candidate.get("asset") if isinstance(candidate.get("asset"), dict) else candidate
        asset_id = _asset_id(asset)
        rows.append(
            {
                "run_id": run_id,
                "need_id": need_id,
                "asset_id": asset_id,
                "llm_review_required": bool(policy.get("llm_review_required")),
                "llm_review_performed": bool(policy.get("llm_review_performed")),
                "decision": review.get("decision"),
                "score": review.get("score"),
                "threshold": review.get("threshold"),
                "reason": review.get("brief_reason", ""),
                "raw_response": review,
                "selected_by_finalize": asset_id == selected_asset_id,
                **_label_for_record(target_record, asset_id),
            }
        )
    return rows


def extract_policy_decision_rows(
    *,
    run_id: str,
    target_record: dict[str, Any],
    collection: dict[str, Any],
    selected_asset_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    need_id = _clean(target_record.get("need_id"))
    for candidate in collection.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        asset = candidate.get("asset") if isinstance(candidate.get("asset"), dict) else {}
        asset_id = _asset_id(asset)
        policy = candidate.get("reuse_policy") if isinstance(candidate.get("reuse_policy"), dict) else {}
        llm_review = policy.get("llm_review") if isinstance(policy.get("llm_review"), dict) else {}
        rows.append(
            {
                "run_id": run_id,
                "need_id": need_id,
                "asset_id": asset_id,
                "policy_score": candidate.get("policy_score") or policy.get("policy_score"),
                "keyword_score": candidate.get("keyword_score"),
                "embedding_score": candidate.get("embedding_score"),
                "substring_score": candidate.get("substring_score"),
                "hybrid_score": candidate.get("hybrid_score"),
                "policy_decision": policy.get("decision", ""),
                "policy_reason": policy.get("reason", ""),
                "llm_review_required": bool(policy.get("llm_review_required")),
                "llm_review_performed": bool(policy.get("llm_review_performed")),
                "llm_decision": llm_review.get("decision", ""),
                "llm_score": llm_review.get("score"),
                "llm_threshold": llm_review.get("threshold"),
                "selected_by_finalize": asset_id == selected_asset_id,
                **_label_for_record(target_record, asset_id),
            }
        )
    return rows


def _waterfall_stage(
    *,
    target_record: dict[str, Any],
    selected: bool,
    selected_ok: bool,
    candidate_score_rows: list[dict[str, Any]],
    policy_decision_rows: list[dict[str, Any]],
    collection: dict[str, Any],
) -> str:
    should_reuse = target_record.get("should_reuse")
    if should_reuse is not True:
        return "final_selected_wrong" if selected else "correct_none"
    if selected:
        return "final_selected_correct" if selected_ok else "final_selected_wrong"
    target = _target_payload(target_record)
    if _short_reuse_group(target.get("strict_reuse_group") or target_record.get("gold_group")) == "C00":
        return "target_class_skip"
    if not candidate_score_rows:
        empty_reason = _clean(collection.get("empty_reason"))
        if empty_reason == "retrieval_no_candidate":
            return "retrieval_no_candidate"
        return "hard_filter_no_candidate"
    if not any(row.get("is_acceptable") for row in candidate_score_rows):
        return "retrieval_no_gold_in_top_k"
    reasons = {_clean(row.get("policy_reason")) for row in policy_decision_rows}
    decisions = {_clean(row.get("policy_decision")) for row in policy_decision_rows}
    if "llm_disabled" in reasons:
        return "llm_disabled"
    if "llm_budget_exhausted" in reasons:
        return "llm_budget_exhausted"
    if any(row.get("llm_review_performed") for row in policy_decision_rows) and "reject" in decisions:
        return "llm_reject"
    if any(reason.startswith("policy_score_below") or reason == "policy_not_selected" for reason in reasons):
        return "policy_reject"
    return "policy_reject"


def build_final_match_row(
    *,
    run_id: str,
    target_record: dict[str, Any],
    match: dict[str, Any] | None,
    candidate_score_rows: list[dict[str, Any]],
    policy_decision_rows: list[dict[str, Any]],
    collection: dict[str, Any],
) -> dict[str, Any]:
    asset = match.get("asset") if isinstance(match, dict) and isinstance(match.get("asset"), dict) else {}
    selected_asset_id = _asset_id(asset)
    label = _label_for_record(target_record, selected_asset_id)
    selected = bool(selected_asset_id)
    should_reuse = target_record.get("should_reuse")
    selected_ok = bool(label["is_acceptable"])

    if label["label_status"] != "labeled":
        match_status = "unlabeled"
        waterfall_stage = ""
    elif selected and selected_ok:
        match_status = "correct"
        waterfall_stage = "final_selected_correct"
    elif selected:
        match_status = "wrong"
        waterfall_stage = "final_selected_wrong"
    elif should_reuse is True:
        match_status = "missed"
        waterfall_stage = _waterfall_stage(
            target_record=target_record,
            selected=selected,
            selected_ok=selected_ok,
            candidate_score_rows=candidate_score_rows,
            policy_decision_rows=policy_decision_rows,
            collection=collection,
        )
    else:
        match_status = "correct_none"
        waterfall_stage = "correct_none"

    return {
        "run_id": run_id,
        "need_id": target_record.get("need_id"),
        "lesson_id": target_record.get("lesson_id"),
        "page_number": target_record.get("page_number"),
        "selected_asset_id": selected_asset_id,
        "selected_keyword_score": match.get("keyword_score") if isinstance(match, dict) else None,
        "selected_policy_score": match.get("policy_score") if isinstance(match, dict) else None,
        "selected_hybrid_score": match.get("hybrid_score") if isinstance(match, dict) else None,
        "selected_reuse_policy": match.get("reuse_policy") if isinstance(match, dict) else None,
        "selected_is_acceptable": selected_ok,
        "selected_is_best": bool(label["is_best"]),
        "match_status": match_status,
        "waterfall_stage": waterfall_stage,
        "failure_stage": waterfall_stage,
        **label,
    }


def _waterfall_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    total = 0
    for row in rows:
        stage = _clean(row.get("waterfall_stage") or row.get("failure_stage"))
        if not stage:
            continue
        total += 1
        counts[stage] = counts.get(stage, 0) + 1
    return {
        "total_count": total,
        "counts": counts,
    }


def _keyword_client(env_file: str | Path, *, allow_llm: bool) -> Any | None:
    if not allow_llm:
        return None
    config = Config.from_env(env_file)
    if not config.llm_api_key or not config.llm_model:
        return None
    return create_llm_client(config, web_search=False)


def _run_dir_for(output_dir: str | Path, run_id: str) -> Path:
    run_name = run_id or f"reuse_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_base = Path(output_dir)
    return output_base if output_base.name == run_name else output_base / run_name


def _run_id_for(run_dir: str | Path) -> str:
    root = Path(run_dir)
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        if isinstance(manifest, dict) and manifest.get("run_id"):
            return _clean(manifest.get("run_id"))
    return root.name


def _manifest_library_dirs(run_dir: str | Path) -> list[Path]:
    manifest_path = Path(run_dir) / "manifest.json"
    if not manifest_path.exists():
        return []
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        return []
    return [
        Path(path).expanduser().resolve()
        for path in (manifest.get("library_dirs") or [])
        if _clean(path) and Path(path).expanduser().exists()
    ]


def _asset_aspect_by_id_from_manifest(run_dir: str | Path) -> dict[str, str]:
    library_dirs = _manifest_library_dirs(run_dir)
    if not library_dirs:
        return {}
    return {
        _asset_id(asset): _clean(asset.get("aspect_ratio"))
        for asset in load_library_assets(library_dirs)
        if _asset_id(asset)
    }


def _read_targets(run_dir: str | Path) -> list[dict[str, Any]]:
    return read_jsonl_artifact(run_dir, "prepare", "targets.jsonl")


def _seed_target_keyword_cache_from_targets(
    targets: Iterable[dict[str, Any]],
    target_keyword_cache: dict[str, Any],
) -> int:
    count = 0
    for row in targets:
        target = _target_payload(row)
        if not target:
            continue
        target_keyword_cache[_target_keyword_cache_key(target)] = dict(target)
        count += 1
    return count


def _write_target_classification_summary(run_dir: Path, targets: list[dict[str, Any]]) -> None:
    rows: list[dict[str, Any]] = []
    for row in targets:
        target = row.get("target") if isinstance(row.get("target"), dict) else {}
        rows.append(
            {
                **row,
                "strict_reuse_group": target.get("strict_reuse_group") or row.get("strict_reuse_group", ""),
            }
        )
    write_json(
        stage_artifact_path(run_dir, "prepare", "target_classification_summary.json"),
        target_classification_metrics(rows),
    )


def _target_classification_review_row(row: dict[str, Any]) -> dict[str, Any]:
    target = _target_payload(row)
    return {
        "gold_group": _short_reuse_group(row.get("target_strict_reuse_group_gold")),
        "pred_group": _short_reuse_group(target.get("strict_reuse_group") or row.get("strict_reuse_group")),
        "query": row.get("raw_query", ""),
        "target_reason": target.get("strict_reuse_reason") or row.get("strict_reuse_reason", ""),
    }


def _write_target_classification_review_tables(run_dir: Path, targets: list[dict[str, Any]]) -> None:
    labeled_rows = [
        _target_classification_review_row(row)
        for row in targets
        if row.get("label_status", "labeled") == "labeled"
    ]
    mismatch_rows = [
        row
        for row in labeled_rows
        if row["gold_group"] and row["pred_group"] and row["gold_group"] != row["pred_group"]
    ]
    c00_rows = [
        row
        for row in labeled_rows
        if row["gold_group"] == "C00" or row["pred_group"] == "C00"
    ]

    summary_counts: dict[tuple[str, str], int] = {}
    for row in mismatch_rows:
        key = (row["gold_group"], row["pred_group"])
        summary_counts[key] = summary_counts.get(key, 0) + 1
    summary_rows = [
        {"count": count, "gold_group": gold_group, "pred_group": pred_group}
        for (gold_group, pred_group), count in sorted(
            summary_counts.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1]),
        )
    ]

    write_csv(
        stage_artifact_path(run_dir, "prepare", "target_class_mismatches_review.csv"),
        mismatch_rows,
        fieldnames=TARGET_CLASS_REVIEW_COLUMNS,
        encoding="utf-8-sig",
    )
    write_csv(
        stage_artifact_path(run_dir, "prepare", "target_class_c00_cases_review.csv"),
        c00_rows,
        fieldnames=TARGET_CLASS_REVIEW_COLUMNS,
        encoding="utf-8-sig",
    )
    write_csv(
        stage_artifact_path(run_dir, "prepare", "target_class_mismatch_summary.csv"),
        summary_rows,
        fieldnames=TARGET_CLASS_MISMATCH_SUMMARY_COLUMNS,
        encoding="utf-8-sig",
    )


def _size_filter_gold_rejection_by_aspect_combo_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row.get("size_only_pass") is not False:
            continue
        target_ratio = _clean(row.get("target_aspect_ratio"))
        candidate_ratio = _clean(row.get("candidate_aspect_ratio"))
        key = (target_ratio, candidate_ratio)
        bucket = grouped.setdefault(
            key,
            {
                "target_aspect_ratio": target_ratio,
                "candidate_aspect_ratio": candidate_ratio,
                "rejected_pair_count": 0,
                "acceptable_rejected_pair_count": 0,
                "best_rejected_pair_count": 0,
                "_need_ids": set(),
                "_acceptable_need_ids": set(),
                "_best_need_ids": set(),
            },
        )
        bucket["rejected_pair_count"] += 1
        need_id = _clean(row.get("need_id"))
        if need_id:
            bucket["_need_ids"].add(need_id)
        if row.get("is_acceptable") is True:
            bucket["acceptable_rejected_pair_count"] += 1
            if need_id:
                bucket["_acceptable_need_ids"].add(need_id)
        if row.get("is_best") is True:
            bucket["best_rejected_pair_count"] += 1
            if need_id:
                bucket["_best_need_ids"].add(need_id)

    summary_rows: list[dict[str, Any]] = []
    for bucket in grouped.values():
        if not bucket["acceptable_rejected_pair_count"] and not bucket["best_rejected_pair_count"]:
            continue
        summary_rows.append(
            {
                "target_aspect_ratio": bucket["target_aspect_ratio"],
                "candidate_aspect_ratio": bucket["candidate_aspect_ratio"],
                "acceptable_rejected_pair_count": bucket["acceptable_rejected_pair_count"],
                "best_rejected_pair_count": bucket["best_rejected_pair_count"],
                "acceptable_affected_need_count": len(bucket["_acceptable_need_ids"]),
                "best_affected_need_count": len(bucket["_best_need_ids"]),
                "rejected_pair_count": bucket["rejected_pair_count"],
                "affected_need_count": len(bucket["_need_ids"]),
            }
        )
    return sorted(
        summary_rows,
        key=lambda row: (
            -int(row["acceptable_rejected_pair_count"]),
            -int(row["best_rejected_pair_count"]),
            -int(row["rejected_pair_count"]),
            str(row["target_aspect_ratio"]),
            str(row["candidate_aspect_ratio"]),
        ),
    )


def _remove_obsolete_size_filter_outputs(stage_dir: Path) -> None:
    for name in OBSOLETE_SIZE_FILTER_OUTPUT_NAMES:
        path = stage_dir / name
        if path.exists() and path.is_file():
            path.unlink()
    for name in OBSOLETE_SIZE_FILTER_OUTPUT_DIRS:
        path = stage_dir / name
        if path.exists() and path.is_dir():
            shutil.rmtree(path)


def _subject_filter_false_rejection_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("subject_only_pass") is not False or row.get("is_acceptable") is not True:
            continue
        copied = {column: row.get(column, "") for column in SUBJECT_FILTER_FALSE_REJECTION_COLUMNS}
        copied["reject_reasons"] = ";".join(str(reason) for reason in row.get("reject_reasons") or [])
        out.append(copied)
    return sorted(
        out,
        key=lambda row: (
            str(row.get("target_subject", "")),
            str(row.get("candidate_subject", "")),
            str(row.get("need_id", "")),
            str(row.get("asset_id", "")),
        ),
    )


def _non_c00_target_match_counts(
    hard_rows: Iterable[dict[str, Any]],
    *,
    targets: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    group_by_need: dict[str, str] = {}

    def is_non_c00(group: str) -> bool:
        return bool(group) and _short_reuse_group(group) != "C00"

    def bucket(group: str) -> dict[str, Any]:
        if group not in counts:
            counts[group] = {
                "target_count": 0,
                "reusable_need_count": 0,
                "best_need_count": 0,
                "acceptable_gold_pair_count": 0,
                "best_gold_pair_count": 0,
                "candidate_pair_count": 0,
                "hard_pass_pair_count": 0,
                "_candidate_hit_need_ids": set(),
                "_best_hit_need_ids": set(),
            }
        return counts[group]

    for target in targets:
        if target.get("label_status", "labeled") != "labeled":
            continue
        need_id = _clean(target.get("need_id"))
        group = _clean(_target_payload(target).get("strict_reuse_group") or target.get("strict_reuse_group"))
        if not need_id or not is_non_c00(group):
            continue
        group_by_need[need_id] = group
        current = bucket(group)
        acceptable = {str(asset_id) for asset_id in target.get("acceptable_asset_ids") or [] if str(asset_id or "")}
        best = {str(asset_id) for asset_id in target.get("best_asset_ids") or [] if str(asset_id or "")}
        current["target_count"] += 1
        if target.get("should_reuse") is True:
            current["reusable_need_count"] += 1
        if best:
            current["best_need_count"] += 1
        current["acceptable_gold_pair_count"] += len(acceptable)
        current["best_gold_pair_count"] += len(best)

    for row in hard_rows:
        if row.get("label_status", "labeled") != "labeled":
            continue
        need_id = _clean(row.get("need_id"))
        group = group_by_need.get(need_id) or _clean(row.get("target_strict_reuse_group"))
        if not is_non_c00(group):
            continue
        current = bucket(group)
        current["candidate_pair_count"] += 1
        if row.get("all_hard_pass") is True:
            current["hard_pass_pair_count"] += 1
            if row.get("is_acceptable"):
                current["_candidate_hit_need_ids"].add(need_id)
            if row.get("is_best"):
                current["_best_hit_need_ids"].add(need_id)

    output: dict[str, dict[str, Any]] = {}
    for group in sorted(counts):
        current = dict(counts[group])
        candidate_hit_need_ids = current.pop("_candidate_hit_need_ids")
        best_hit_need_ids = current.pop("_best_hit_need_ids")
        current["candidate_hit_need_count"] = len(candidate_hit_need_ids)
        current["candidate_hit_rate"] = safe_div(len(candidate_hit_need_ids), current["reusable_need_count"])
        current["best_hit_need_count"] = len(best_hit_need_ids)
        current["best_hit_rate"] = safe_div(len(best_hit_need_ids), current["best_need_count"])
        output[group] = current
    return output


def _hard_filter_summary_payload(
    hard_rows: list[dict[str, Any]],
    *,
    targets: list[dict[str, Any]] | None = None,
    category_routing: str = CATEGORY_ROUTING_BASELINE,
) -> dict[str, Any]:
    mode = normalize_category_routing(category_routing)
    gold_sets = gold_sets_from_targets(targets or [])
    size_gold_sets = size_compatible_gold_sets_from_hard_rows(targets or [], hard_rows)
    pass_fields = {
        "size_only": "size_only_pass",
        "subject_only": "subject_only_pass",
        "category_only": "category_only_pass",
        "subject_size": "subject_size_pass",
    }
    return {
        "category_routing": mode,
        "all_hard_filters": candidate_filter_metrics(
            hard_rows,
            pass_field="all_hard_pass",
            gold_sets=gold_sets,
        ),
        "stage": hard_filter_stage_metrics(hard_rows, gold_sets=gold_sets),
        "non_c00_target_match_counts": _non_c00_target_match_counts(
            hard_rows,
            targets=targets or [],
        ),
        "filter_ablation": filter_ablation_metrics(
            hard_rows,
            pass_fields=pass_fields,
            gold_sets=gold_sets,
        ),
        "category_filter": candidate_filter_metrics(
            hard_rows,
            pass_field="category_pass",
            gold_sets=gold_sets,
        ),
        "subject_filter": candidate_filter_metrics(
            hard_rows,
            pass_field="subject_pass",
            gold_sets=gold_sets,
        ),
        "aspect_filter": candidate_filter_metrics(
            hard_rows,
            pass_field="aspect_pass",
            gold_sets=gold_sets,
        ),
        "size_compatible_gold": {
            "gold_policy": "size_filter_after_hard_filter",
            "gold_adjustment": size_compatible_gold_summary(gold_sets, size_gold_sets),
            "all_hard_filters": candidate_filter_metrics(
                hard_rows,
                pass_field="all_hard_pass",
                gold_sets=size_gold_sets,
            ),
            "stage": hard_filter_stage_metrics(hard_rows, gold_sets=size_gold_sets),
            "filter_ablation": filter_ablation_metrics(
                hard_rows,
                pass_fields=pass_fields,
                gold_sets=size_gold_sets,
            ),
            "category_filter": candidate_filter_metrics(
                hard_rows,
                pass_field="category_pass",
                gold_sets=size_gold_sets,
            ),
            "subject_filter": candidate_filter_metrics(
                hard_rows,
                pass_field="subject_pass",
                gold_sets=size_gold_sets,
            ),
            "aspect_filter": candidate_filter_metrics(
                hard_rows,
                pass_field="aspect_pass",
                gold_sets=size_gold_sets,
            ),
        },
    }


def _hard_filter_pass_count(rows: Iterable[dict[str, Any]], field: str) -> int:
    return sum(1 for row in rows if row.get(field) is True)


def _hard_filter_comparison_metrics(
    hard_rows: list[dict[str, Any]],
    *,
    targets: list[dict[str, Any]],
    category_routing: str,
) -> dict[str, Any]:
    summary = _hard_filter_summary_payload(
        hard_rows,
        targets=targets,
        category_routing=category_routing,
    )
    stage = summary.get("stage") if isinstance(summary.get("stage"), dict) else {}
    pair_metrics = stage.get("pair_metrics") if isinstance(stage.get("pair_metrics"), dict) else {}
    category_filter = summary.get("category_filter") if isinstance(summary.get("category_filter"), dict) else {}
    return {
        "category_routing": summary["category_routing"],
        "candidate_pair_count": len(hard_rows),
        "category_pass_pair_count": _hard_filter_pass_count(hard_rows, "category_pass"),
        "all_hard_pass_pair_count": _hard_filter_pass_count(hard_rows, "all_hard_pass"),
        "candidate_hit_rate": stage.get("candidate_hit_rate", 0.0),
        "best_hit_rate": stage.get("best_hit_rate", 0.0),
        "pair_precision": pair_metrics.get("precision", 0.0),
        "category_filter_candidate_hit_rate": category_filter.get("candidate_hit_rate", 0.0),
        "category_filter_pair_precision": (category_filter.get("pair_metrics") or {}).get("precision", 0.0),
    }


def _hard_filter_comparison_payload(
    *,
    baseline_rows: list[dict[str, Any]],
    merge_rows: list[dict[str, Any]],
    targets: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline = _hard_filter_comparison_metrics(
        baseline_rows,
        targets=targets,
        category_routing=CATEGORY_ROUTING_BASELINE,
    )
    merge = _hard_filter_comparison_metrics(
        merge_rows,
        targets=targets,
        category_routing=CATEGORY_ROUTING_MERGE_C01_C03,
    )
    delta_keys = (
        "candidate_pair_count",
        "category_pass_pair_count",
        "all_hard_pass_pair_count",
        "candidate_hit_rate",
        "best_hit_rate",
        "pair_precision",
        "category_filter_candidate_hit_rate",
        "category_filter_pair_precision",
    )
    return {
        "baseline": baseline,
        "merge_no_llm": merge,
        "delta": {
            key: (merge.get(key, 0) or 0) - (baseline.get(key, 0) or 0)
            for key in delta_keys
        },
    }


def _write_category_routing_comparison_outputs(
    run_dir: Path,
    *,
    baseline_rows: list[dict[str, Any]],
    merge_rows: list[dict[str, Any]],
    targets: list[dict[str, Any]],
) -> None:
    write_jsonl(
        stage_artifact_path(run_dir, "hard_filter", "baseline_hard_filter_pairs.jsonl"),
        baseline_rows,
    )
    write_json(
        stage_artifact_path(run_dir, "hard_filter", "category_routing_comparison.json"),
        _hard_filter_comparison_payload(
            baseline_rows=baseline_rows,
            merge_rows=merge_rows,
            targets=targets,
        ),
    )


def _write_hard_filter_outputs(
    run_dir: Path,
    hard_rows: list[dict[str, Any]],
    *,
    targets: list[dict[str, Any]] | None = None,
    category_routing: str = CATEGORY_ROUTING_BASELINE,
) -> None:
    mode = normalize_category_routing(category_routing)
    stage_dir = stage_artifact_dir(run_dir, "hard_filter")
    _remove_obsolete_size_filter_outputs(stage_dir)
    write_jsonl(stage_artifact_path(run_dir, "hard_filter", "hard_filter_pairs.jsonl"), hard_rows)
    write_csv(
        stage_artifact_path(run_dir, "hard_filter", "size_filter_gold_rejection_by_aspect_combo.csv"),
        _size_filter_gold_rejection_by_aspect_combo_rows(hard_rows),
        fieldnames=SIZE_FILTER_GOLD_REJECTION_BY_ASPECT_COMBO_COLUMNS,
        encoding="utf-8-sig",
    )
    write_csv(
        stage_artifact_path(run_dir, "hard_filter", "subject_filter_false_rejections.csv"),
        _subject_filter_false_rejection_rows(hard_rows),
        fieldnames=SUBJECT_FILTER_FALSE_REJECTION_COLUMNS,
        encoding="utf-8-sig",
    )
    write_csv(
        stage_artifact_path(run_dir, "hard_filter", "subject_only_false_rejections.csv"),
        _subject_filter_false_rejection_rows(hard_rows),
        fieldnames=SUBJECT_FILTER_FALSE_REJECTION_COLUMNS,
        encoding="utf-8-sig",
    )
    write_json(
        stage_artifact_path(run_dir, "hard_filter", "hard_filter_summary.json"),
        _hard_filter_summary_payload(hard_rows, targets=targets, category_routing=mode),
    )


def _write_retrieve_outputs(
    run_dir: Path,
    candidate_score_rows: list[dict[str, Any]],
    *,
    targets: list[dict[str, Any]] | None = None,
    hard_rows: list[dict[str, Any]] | None = None,
) -> None:
    gold_sets = gold_sets_from_targets(targets or [])
    size_gold_sets = size_compatible_gold_sets_from_hard_rows(targets or [], hard_rows or [])
    relabeled_size_rows = relabel_rows_for_gold_sets(candidate_score_rows, size_gold_sets)
    reusable_need_ids = _reusable_need_ids_from_gold_sets(gold_sets)
    size_reusable_need_ids = _reusable_need_ids_from_gold_sets(size_gold_sets)
    write_jsonl(stage_artifact_path(run_dir, "retrieve", "candidate_score_audit.jsonl"), candidate_score_rows)
    write_csv(
        stage_artifact_path(run_dir, "retrieve", "candidate_score_audit.csv"),
        candidate_score_rows,
        fieldnames=CANDIDATE_SCORE_AUDIT_COLUMNS,
        encoding="utf-8-sig",
    )
    write_csv(
        stage_artifact_path(run_dir, "retrieve", "retrieval_missed_gold_candidates.csv"),
        _retrieval_missed_gold_rows(candidate_score_rows),
        fieldnames=CANDIDATE_SCORE_AUDIT_COLUMNS,
        encoding="utf-8-sig",
    )
    write_json(
        stage_artifact_path(run_dir, "retrieve", "retrieve_summary.json"),
        {
            "candidate_score_audit": _candidate_score_audit_metrics(candidate_score_rows),
            "ranking": ranking_metrics(
                candidate_score_rows,
                reusable_need_ids=reusable_need_ids,
                rank_field="rank_hybrid",
            ),
            "size_compatible_gold": _size_compatible_retrieval_metrics(
                candidate_score_rows,
                original_gold_sets=gold_sets,
                size_gold_sets=size_gold_sets,
            ),
            "ranking_size_compatible_gold": ranking_metrics(
                relabeled_size_rows,
                reusable_need_ids=size_reusable_need_ids,
                rank_field="rank_hybrid",
            ),
        },
    )


def _reusable_need_ids_from_gold_sets(gold_sets: dict[str, dict[str, set[str] | bool]]) -> set[str]:
    reusable_need_ids: set[str] = set()
    for need_id, sets in gold_sets.items():
        acceptable = sets.get("acceptable") if isinstance(sets, dict) else set()
        if isinstance(acceptable, set) and acceptable:
            reusable_need_ids.add(need_id)
    return reusable_need_ids


def _size_compatible_retrieval_metrics(
    candidate_score_rows: list[dict[str, Any]],
    *,
    original_gold_sets: dict[str, dict[str, set[str] | bool]],
    size_gold_sets: dict[str, dict[str, set[str] | bool]],
) -> dict[str, Any]:
    relabeled_rows = relabel_rows_for_gold_sets(candidate_score_rows, size_gold_sets)
    return {
        "gold_policy": "size_filter_after_hard_filter",
        "gold_adjustment": size_compatible_gold_summary(original_gold_sets, size_gold_sets),
        "candidate_score_audit": _candidate_score_audit_metrics(relabeled_rows),
        "ranking": ranking_metrics(
            relabeled_rows,
            reusable_need_ids=_reusable_need_ids_from_gold_sets(size_gold_sets),
            rank_field="rank_hybrid",
        ),
    }


def _candidate_score_audit_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    eval_rows = [row for row in rows if row.get("label_status", "labeled") == "labeled"]
    policy_input_rows = [row for row in eval_rows if row.get("policy_input") is True]
    gold_rows = [row for row in eval_rows if row.get("is_acceptable")]
    best_rows = [row for row in eval_rows if row.get("is_best")]
    gold_need_ids = {_clean(row.get("need_id")) for row in gold_rows}
    hit_gold_need_ids = {_clean(row.get("need_id")) for row in gold_rows if row.get("rank_hybrid")}
    return {
        "candidate_pair_count": len(eval_rows),
        "policy_input_pair_count": len(policy_input_rows),
        "acceptable_pair_count": len(gold_rows),
        "best_pair_count": len(best_rows),
        "acceptable_need_count": len(gold_need_ids),
        "acceptable_need_hit_count": len(hit_gold_need_ids),
        "acceptable_need_hit_rate": safe_div(len(hit_gold_need_ids), len(gold_need_ids)),
        "max_policy_score": max((float(row.get("policy_score") or 0.0) for row in eval_rows), default=0.0),
        "min_policy_score": min((float(row.get("policy_score") or 0.0) for row in eval_rows), default=0.0),
    }


def _retrieval_missed_gold_rows(
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("label_status", "labeled") != "labeled":
            continue
        if row.get("is_acceptable") is True and not row.get("rank_hybrid"):
            out.append({column: row.get(column, "") for column in CANDIDATE_SCORE_AUDIT_COLUMNS})
    return sorted(
        out,
        key=lambda row: (
            str(row.get("need_id", "")),
            int(row.get("rank_hybrid") or 0),
            str(row.get("asset_id", "")),
        ),
    )


def _candidate_score_rows_by_need(run_dir: str | Path) -> dict[str, list[dict[str, Any]]]:
    by_need: dict[str, list[dict[str, Any]]] = {}
    for row in read_jsonl_artifact(run_dir, "retrieve", "candidate_score_audit.jsonl"):
        by_need.setdefault(_clean(row.get("need_id")), []).append(row)
    return by_need


def _collections_by_need(run_dir: str | Path) -> dict[str, dict[str, Any]]:
    collections: dict[str, dict[str, Any]] = {}
    for row in read_jsonl_artifact(run_dir, "retrieve", "candidate_collections.jsonl"):
        need_id = _clean(row.get("need_id"))
        collection = row.get("collection")
        if need_id and isinstance(collection, dict):
            collections[need_id] = collection
    return collections


def _candidate_lookup_by_need_asset(run_dir: str | Path) -> dict[tuple[str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for need_id, collection in _collections_by_need(run_dir).items():
        for candidate in collection.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            asset = candidate.get("asset") if isinstance(candidate.get("asset"), dict) else {}
            asset_id = _asset_id(asset)
            if need_id and asset_id:
                lookup[(need_id, asset_id)] = candidate
    return lookup


def _merge_reuse_finalize_debug_files(output_path: Path, debug_paths: Iterable[Path]) -> None:
    queries: list[dict[str, Any]] = []
    for path in debug_paths:
        if not path.exists():
            continue
        payload = read_json(path)
        path_queries = payload.get("queries") if isinstance(payload, dict) else None
        if isinstance(path_queries, list):
            queries.extend(item for item in path_queries if isinstance(item, dict))
    if not queries:
        return
    write_json(
        output_path,
        {
            "schema_version": 1,
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "queries": queries,
        },
    )


def prepare_run(
    *,
    plan_paths: Iterable[str | Path],
    output_dir: str | Path = "report",
    run_id: str = "",
    goldset_paths: Iterable[str | Path] = (),
    library_dirs: Iterable[str | Path] = (),
    review_enabled: bool = False,
    allow_llm: bool = False,
    env_file: str | Path = ".env",
    notes: str = "",
) -> Path:
    run_dir = _run_dir_for(output_dir, run_id)
    run_name = run_dir.name
    run_dir.mkdir(parents=True, exist_ok=True)
    plans_dir = run_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    plan_path_list = [Path(path).expanduser().resolve() for path in plan_paths]
    library_dir_list = [Path(path).expanduser().resolve() for path in library_dirs]
    goldset_path_list = [Path(path).expanduser().resolve() for path in goldset_paths]
    manifest = build_manifest(
        run_id=run_name,
        plan_paths=plan_path_list,
        library_dirs=library_dir_list,
        goldset_paths=goldset_path_list,
        output_dir=run_dir,
        review_enabled=review_enabled,
        allow_llm=allow_llm,
        notes=notes,
    )
    write_json(run_dir / "manifest.json", manifest)

    all_needs: list[dict[str, Any]] = []
    for plan_path in plan_path_list:
        if plan_path.exists():
            shutil.copy2(plan_path, plans_dir / plan_path.name)
        all_needs.extend(extract_plan_needs(plan_path, run_id=run_name))
    if goldset_path_list:
        all_needs = apply_goldset_labels(all_needs, load_goldset_labels(goldset_path_list))
    write_jsonl(stage_artifact_path(run_dir, "prepare", "plan_needs.jsonl"), all_needs)
    client = _keyword_client(env_file, allow_llm=allow_llm)
    if client is None:
        raise ValueError("prepare requires --allow-llm because enriched target fields are missing")
    target_rows, enrichment_rows = build_target_records(
        all_needs,
        keyword_client=client,
        require_enrichment=True,
    )
    write_jsonl(stage_artifact_path(run_dir, "prepare", "target_enrichment.jsonl"), enrichment_rows)
    write_jsonl(stage_artifact_path(run_dir, "prepare", "targets.jsonl"), target_rows)
    target_enrichment_summary = validate_enriched_targets(target_rows, stage="prepare")
    target_enrichment_summary["fallback_enriched_count"] = sum(
        1 for row in target_rows if _target_payload(row).get("target_enrichment_fallback")
    )
    write_json(
        stage_artifact_path(run_dir, "prepare", "target_enrichment_summary.json"),
        target_enrichment_summary,
    )
    _write_target_classification_summary(run_dir, target_rows)
    _write_target_classification_review_tables(run_dir, target_rows)
    return run_dir


def run_hard_filter_stage(
    *,
    run_dir: str | Path,
    library_dirs: Iterable[str | Path],
    category_routing: str = CATEGORY_ROUTING_BASELINE,
) -> Path:
    mode = normalize_category_routing(category_routing)
    root = Path(run_dir)
    search_context = ReuseSearchContext()
    library_dir_list = [Path(path).expanduser().resolve() for path in library_dirs]
    targets = _read_targets(root)
    validate_enriched_targets(targets, stage="hard-filter")
    hard_rows: list[dict[str, Any]] = []
    baseline_rows: list[dict[str, Any]] = []
    for target_record in targets:
        target = _target_payload(target_record)
        if mode == CATEGORY_ROUTING_MERGE_C01_C03:
            baseline_assets = load_routed_library_assets_for_target(
                library_dir_list,
                target,
                reuse_search_context=search_context,
                category_routing=CATEGORY_ROUTING_BASELINE,
            )
            baseline_rows.extend(
                hard_filter_rows_for_target(
                    target_record,
                    baseline_assets,
                    category_routing=CATEGORY_ROUTING_BASELINE,
                )
            )
        library_assets = load_routed_library_assets_for_target(
            library_dir_list,
            target,
            reuse_search_context=search_context,
            category_routing=mode,
        )
        hard_rows.extend(
            hard_filter_rows_for_target(target_record, library_assets, category_routing=mode)
        )
    _write_hard_filter_outputs(root, hard_rows, targets=targets, category_routing=mode)
    if mode == CATEGORY_ROUTING_MERGE_C01_C03:
        _write_category_routing_comparison_outputs(
            root,
            baseline_rows=baseline_rows,
            merge_rows=hard_rows,
            targets=targets,
        )
    return root


def run_retrieve_stage(
    *,
    run_dir: str | Path,
    library_dirs: Iterable[str | Path],
    allow_llm: bool = False,
    env_file: str | Path = ".env",
) -> Path:
    root = Path(run_dir)
    run_name = _run_id_for(root)
    library_dir_list = [Path(path).expanduser().resolve() for path in library_dirs]
    search_context = ReuseSearchContext(
        query_embedding_cache_dir=stage_artifact_dir(root, "retrieve")
    )
    targets = _read_targets(root)
    validate_enriched_targets(targets, stage="retrieve")
    _seed_target_keyword_cache_from_targets(targets, search_context.target_keyword_cache)

    candidate_score_rows: list[dict[str, Any]] = []
    collection_rows: list[dict[str, Any]] = []

    def collect_one(target_record: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        target = _target_payload(target_record)
        collection = find_reusable_ai_image_asset(
            library_dir=library_dir_list,
            asset_kind="page_image",
            prompt=_clean(target_record.get("raw_query")),
            prompt_route=target.get("prompt_route") or None,
            theme=target.get("theme", ""),
            grade=target.get("grade_hint") or target.get("grade_norm") or "",
            subject=target.get("subject_hint") or target.get("subject") or "",
            grade_band=target.get("grade_band") or "",
            page_title=target_record.get("page_title", ""),
            page_type=target.get("page_type", ""),
            role=target_record.get("role", "illustration"),
            aspect_ratio=target.get("aspect_ratio", "16:9"),
            caption=target.get("caption", ""),
            keyword_client=None,
            debug_path=None,
            reuse_search_context=search_context,
            llm_review_enabled=False,
            _target_keyword_cache=search_context.target_keyword_cache,
            _collect_candidates_only=True,
        )
        collection = collection if isinstance(collection, dict) else {}
        collection_row = {
            "run_id": run_name,
            "need_id": target_record.get("need_id"),
            "collection": collection,
        }
        flattened = flatten_candidate_collection(
            run_id=run_name,
            target_record=target_record,
            collection=collection,
        )
        return collection_row, flattened["candidate_score_audit"]

    worker_count = _bounded_worker_count(
        item_count=len(targets),
        default=DEFAULT_REUSE_MAX_WORKERS,
        env_name=REUSE_SEARCH_WORKERS_ENV,
    )
    if targets:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for collection_row, score_rows in executor.map(collect_one, targets):
                collection_rows.append(collection_row)
                candidate_score_rows.extend(score_rows)

    write_jsonl(stage_artifact_path(root, "retrieve", "candidate_collections.jsonl"), collection_rows)
    hard_rows = read_jsonl_artifact(root, "hard_filter", "hard_filter_pairs.jsonl")
    _write_retrieve_outputs(root, candidate_score_rows, targets=targets, hard_rows=hard_rows)
    return root


def run_review_stage(
    *,
    run_dir: str | Path,
    review_enabled: bool = False,
    allow_llm: bool = False,
    env_file: str | Path = ".env",
) -> Path:
    root = Path(run_dir)
    run_name = _run_id_for(root)
    if review_enabled and not allow_llm:
        raise ValueError("review requires --allow-llm when --review is enabled")
    client = _keyword_client(env_file, allow_llm=allow_llm) if review_enabled else None
    if review_enabled and client is None:
        raise ValueError("review requires --allow-llm with configured LLM credentials")
    targets = _read_targets(root)
    validate_enriched_targets(targets, stage="review")
    collections = _collections_by_need(root)
    candidate_score_by_need = _candidate_score_rows_by_need(root)
    policy_input_candidate_count = sum(len(rows) for rows in candidate_score_by_need.values())
    reuse_session_state: dict[str, Any] = {
        "strict_asset_use_counts": {},
        "strict_asset_used_by": {},
    }
    llm_review_rows: list[dict[str, Any]] = []
    policy_decision_rows: list[dict[str, Any]] = []
    final_rows: list[dict[str, Any]] = []
    debug_output_path = stage_artifact_path(root, "review", "reuse_finalize_debug.jsonl")
    debug_work_dir = stage_artifact_dir(root, "review") / "reuse_finalize_debug_parts"
    debug_work_dir.mkdir(parents=True, exist_ok=True)
    debug_paths = [
        debug_work_dir / f"reuse_finalize_debug_{index:05d}.json"
        for index in range(len(targets))
    ]
    for path in debug_paths:
        path.unlink(missing_ok=True)
    debug_output_path.unlink(missing_ok=True)

    def finalize_one(index_and_target: tuple[int, dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        index, target_record = index_and_target
        need_id = _clean(target_record.get("need_id"))
        collection = collections.get(need_id, {})
        match = _finalize_reuse_candidate_collection(
            collection,
            debug_path=debug_paths[index],
            keyword_client=client,
            reuse_session_state=None,
            llm_review_enabled=bool(review_enabled),
            reuse_debug_mode="full",
            vlm_client=None,
            near_miss_vlm_state=None,
        )
        return match, collection

    worker_count = _bounded_worker_count(
        item_count=len(targets),
        default=DEFAULT_REUSE_POLICY_WORKERS,
        env_name=REUSE_POLICY_WORKERS_ENV,
    )
    if targets:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            policy_results = list(executor.map(finalize_one, enumerate(targets)))
    else:
        policy_results = []
    _merge_reuse_finalize_debug_files(debug_output_path, debug_paths)

    for target_record, policy_result in zip(targets, policy_results):
        need_id = _clean(target_record.get("need_id"))
        match, collection = policy_result
        occupancy_reject: dict[str, Any] | None = None
        if match:
            occupancy = _strict_reuse_occupancy_status(match, reuse_session_state)
            if occupancy.get("decision") == "skip_strict_asset_reuse_limit":
                match["strict_reuse_occupancy"] = occupancy
                occupancy_reject = match
                match = None
            else:
                mark_reused_ai_image_asset_in_session(
                    match,
                    reuse_session_state,
                    {
                        "need_id": need_id,
                        "page_number": target_record.get("page_number"),
                        "slot_key": target_record.get("slot_key"),
                    },
                )
        selected_asset = match.get("asset") if isinstance(match, dict) and isinstance(match.get("asset"), dict) else {}
        selected_asset_id = _asset_id(selected_asset)
        llm_review_rows.extend(
            extract_llm_review_rows(
                run_id=run_name,
                target_record=target_record,
                collection=collection,
                selected_asset_id=selected_asset_id,
            )
        )
        need_policy_rows = extract_policy_decision_rows(
            run_id=run_name,
            target_record=target_record,
            collection=collection,
            selected_asset_id=selected_asset_id,
        )
        policy_decision_rows.extend(need_policy_rows)
        final_row = build_final_match_row(
            run_id=run_name,
            target_record=target_record,
            match=match,
            candidate_score_rows=candidate_score_by_need.get(need_id, []),
            policy_decision_rows=need_policy_rows,
            collection=collection,
        )
        if occupancy_reject is not None:
            final_row["waterfall_stage"] = "policy_reject"
            final_row["failure_stage"] = "policy_reject"
            final_row["occupancy_rejected_asset_id"] = _asset_id(occupancy_reject.get("asset") or {})
            final_row["strict_reuse_occupancy"] = occupancy_reject.get("strict_reuse_occupancy")
        final_rows.append(final_row)

    write_jsonl(stage_artifact_path(root, "review", "llm_reviews.jsonl"), llm_review_rows)
    write_jsonl(stage_artifact_path(root, "review", "policy_decisions.jsonl"), policy_decision_rows)
    write_json(
        stage_artifact_path(root, "review", "llm_review_summary.json"),
        llm_review_stage_metrics(
            llm_review_rows,
            policy_candidate_count=policy_input_candidate_count,
        ),
    )
    write_jsonl(stage_artifact_path(root, "review", "final_matches.jsonl"), final_rows)
    return root


def run_summarize_stage(*, run_dir: str | Path) -> Path:
    root = Path(run_dir)
    targets = _read_targets(root)
    asset_aspect_by_id = _asset_aspect_by_id_from_manifest(root)
    if asset_aspect_by_id:
        targets = drop_opposite_orientation_gold_pairs(targets, asset_aspect_by_id)
    hard_rows = read_jsonl_artifact(root, "hard_filter", "hard_filter_pairs.jsonl")
    candidate_score_rows = read_jsonl_artifact(root, "retrieve", "candidate_score_audit.jsonl")
    final_rows = read_jsonl_artifact(root, "review", "final_matches.jsonl")
    policy_rows = read_jsonl_artifact(root, "review", "policy_decisions.jsonl")
    hard_summary = read_json_artifact(root, "hard_filter", "hard_filter_summary.json", {})
    retrieve_summary = read_json_artifact(root, "retrieve", "retrieve_summary.json", {})
    original_gold_sets = gold_sets_from_targets(targets)
    relabeled_original_candidate_rows = relabel_rows_for_gold_sets(candidate_score_rows, original_gold_sets)
    relabeled_policy_rows = relabel_rows_for_gold_sets(policy_rows, original_gold_sets)
    size_gold_sets = size_compatible_gold_sets_from_hard_rows(targets, hard_rows)
    relabeled_size_candidate_rows = relabel_rows_for_gold_sets(candidate_score_rows, size_gold_sets)
    size_compatible_retrieve_metrics = _size_compatible_retrieval_metrics(
        candidate_score_rows,
        original_gold_sets=original_gold_sets,
        size_gold_sets=size_gold_sets,
    )
    policy_rows_by_need: dict[str, list[dict[str, Any]]] = {}
    for row in relabeled_policy_rows:
        policy_rows_by_need.setdefault(_clean(row.get("need_id")), []).append(row)
    missed_rows = missed_gold_diagnostics(final_rows, policy_rows_by_need)
    reject_gold_table = reject_reason_by_gold_crosstab(relabeled_policy_rows)

    reusable_need_ids = {
        _clean(row.get("need_id"))
        for row in targets
        if row.get("label_status") == "labeled" and row.get("should_reuse") is True
    }
    asset_kind_buckets = {
        "hard_filter": asset_kind_bucket_stage_metrics(
            hard_rows,
            targets=targets,
            pass_field="all_hard_pass",
        ),
        "retrieval": asset_kind_bucket_stage_metrics(
            candidate_score_rows,
            targets=targets,
            pass_field="policy_input",
        ),
    }
    metrics = {
        "target_classification": read_json_artifact(root, "prepare", "target_classification_summary.json", {}),
        "hard_filter": hard_summary,
        "retrieval": retrieve_summary,
        "asset_kind_buckets": asset_kind_buckets,
        "llm_review": read_json_artifact(root, "review", "llm_review_summary.json", {}),
        "ranking": ranking_metrics(
            relabeled_original_candidate_rows,
            reusable_need_ids=reusable_need_ids,
            rank_field="rank_hybrid",
        ),
        "ranking_size_compatible_gold": ranking_metrics(
            relabeled_size_candidate_rows,
            reusable_need_ids=_reusable_need_ids_from_gold_sets(size_gold_sets),
            rank_field="rank_hybrid",
        ),
        "final": final_match_metrics(final_rows, gold_sets=size_gold_sets),
        "final_raw_gold_audit": final_match_metrics(final_rows, gold_sets=original_gold_sets),
        "waterfall": _waterfall_metrics(final_rows),
        "retrieval_size_compatible_gold": size_compatible_retrieve_metrics,
        "size_compatible_gold_adjustment": size_compatible_gold_summary(original_gold_sets, size_gold_sets),
        "target_count": len(targets),
        "unlabeled_need_count": sum(1 for row in targets if row.get("label_status") != "labeled"),
    }
    write_json(stage_artifact_path(root, "summarize", "metrics.json"), metrics)
    write_jsonl(
        stage_artifact_path(root, "summarize", "failure_cases.jsonl"),
        [
            row for row in final_rows
            if row.get("waterfall_stage") not in {"", "correct_none", "final_selected_correct"}
        ],
    )
    write_jsonl(stage_artifact_path(root, "summarize", "prompt_issue_log.jsonl"), [])
    write_csv(
        stage_artifact_path(root, "summarize", "missed_gold_diagnostics.csv"),
        missed_rows,
        fieldnames=MISSED_GOLD_DIAGNOSTIC_COLUMNS,
    )
    write_json(stage_artifact_path(root, "summarize", "reject_reason_by_gold.json"), reject_gold_table)
    final = metrics.get("final", {}) if isinstance(metrics.get("final"), dict) else {}
    raw_final = metrics.get("final_raw_gold_audit", {}) if isinstance(metrics.get("final_raw_gold_audit"), dict) else {}
    target_cls = metrics.get("target_classification", {}) if isinstance(metrics.get("target_classification"), dict) else {}
    hard = metrics.get("hard_filter", {}) if isinstance(metrics.get("hard_filter"), dict) else {}
    retrieval = metrics.get("retrieval", {}) if isinstance(metrics.get("retrieval"), dict) else {}
    hard_stage = hard.get("stage") if isinstance(hard.get("stage"), dict) else {}
    retrieval_ranking = retrieval.get("ranking") if isinstance(retrieval.get("ranking"), dict) else {}
    buckets = metrics.get("asset_kind_buckets", {}) if isinstance(metrics.get("asset_kind_buckets"), dict) else {}
    retrieval_buckets = buckets.get("retrieval", {}) if isinstance(buckets.get("retrieval"), dict) else {}
    page_image_retrieval = retrieval_buckets.get("page_image", {}) if isinstance(retrieval_buckets.get("page_image"), dict) else {}
    background_retrieval = retrieval_buckets.get("background", {}) if isinstance(retrieval_buckets.get("background"), dict) else {}
    report_path = stage_artifact_path(root, "summarize", "report.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "\n".join(
            [
                "# 复用评估报告",
                "",
                f"- 运行 ID：`{_run_id_for(root)}`",
                f"- 目标数：{len(targets)}",
                f"- 未标注目标数：{metrics['unlabeled_need_count']}",
                f"- 最终已标注需求数：{int(final.get('labeled_needs') or 0)}",
                f"- 最终准确率：{float(final.get('precision') or 0.0):.4f}",
                f"- 最终召回率：{float(final.get('recall') or 0.0):.4f}",
                f"- raw(已去正交翻转)召回率：{float(raw_final.get('recall') or 0.0):.4f}",
                f"- 最终 F1：{float(final.get('f1') or 0.0):.4f}",
                f"- 选中最佳素材率：{float(final.get('selected_best_rate') or 0.0):.4f}",
                f"- 正确不复用率：{float(final.get('correct_none_rate') or 0.0):.4f}",
                "",
                "## 阶段结果",
                "",
                f"- Target 分类准确率：{float(target_cls.get('target_class_accuracy') or 0.0):.4f}",
                f"- C00 跳过类 F1：{float(target_cls.get('c00_f1') or 0.0):.4f}",
                f"- 硬过滤候选命中率：{float(hard_stage.get('candidate_hit_rate') or 0.0):.4f}",
                f"- retrieval candidate_hit_rate: {float(retrieval_ranking.get('candidate_hit_rate') or 0.0):.4f}",
                "",
                "## asset_kind 拆分",
                "",
                f"- page_image retrieval candidate_hit_rate: {float(page_image_retrieval.get('candidate_hit_rate') or 0.0):.4f}",
                f"- background retrieval candidate_hit_rate: {float(background_retrieval.get('candidate_hit_rate') or 0.0):.4f}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


def run_analyze_stage(
    *,
    run_dir: str | Path,
    allow_llm: bool = False,
    env_file: str | Path = ".env",
) -> Path:
    root = Path(run_dir)
    targets = _read_targets(root)
    asset_aspect_by_id = _asset_aspect_by_id_from_manifest(root)
    if asset_aspect_by_id:
        targets = drop_opposite_orientation_gold_pairs(targets, asset_aspect_by_id)
    hard_rows = read_jsonl_artifact(root, "hard_filter", "hard_filter_pairs.jsonl")
    candidate_score_rows = read_jsonl_artifact(root, "retrieve", "candidate_score_audit.jsonl")
    policy_rows = read_jsonl_artifact(root, "review", "policy_decisions.jsonl")

    original_gold_sets = gold_sets_from_targets(targets)
    size_gold_sets = size_compatible_gold_sets_from_hard_rows(targets, hard_rows)
    relabeled_candidate_rows = relabel_rows_for_gold_sets(candidate_score_rows, size_gold_sets)
    relabeled_policy_rows = relabel_rows_for_gold_sets(policy_rows, size_gold_sets)
    total_reusable_needs = len(_reusable_need_ids_from_gold_sets(size_gold_sets))
    sweep = floor_sweep_recall_precision(
        [row for row in relabeled_candidate_rows if row.get("is_acceptable")],
        total_reusable_needs=total_reusable_needs,
        floors=[0.55, 0.57, 0.60, 0.62],
    )
    write_json(stage_artifact_path(root, "summarize", "floor_sweep.json"), sweep)

    counterfactual_rows: list[dict[str, Any]] = []
    counterfactual_candidates = [
        row
        for row in relabeled_policy_rows
        if row.get("is_acceptable")
        and _clean(row.get("policy_reason")) == "policy_score_below_reject_threshold"
    ]
    if allow_llm and counterfactual_candidates:
        client = _keyword_client(env_file, allow_llm=True)
        if client is None:
            raise ValueError("analyze --allow-llm requires configured LLM credentials")
        targets_by_need = {_clean(row.get("need_id")): _target_payload(row) for row in targets}
        candidates_by_key = _candidate_lookup_by_need_asset(root)
        seen: set[tuple[str, str]] = set()
        for row in counterfactual_candidates:
            need_id = _clean(row.get("need_id"))
            asset_id = _clean(row.get("asset_id"))
            key = (need_id, asset_id)
            if key in seen:
                continue
            seen.add(key)
            candidate = candidates_by_key.get(key, {})
            candidate_asset = candidate.get("asset") if isinstance(candidate.get("asset"), dict) else {}
            if not targets_by_need.get(need_id) or not candidate_asset:
                continue
            policy_result = candidate.get("reuse_policy") if isinstance(candidate.get("reuse_policy"), dict) else {
                "decision": row.get("policy_decision", ""),
                "reason": row.get("policy_reason", ""),
                "policy_score": row.get("policy_score"),
            }
            score_details = candidate.get("score_details") if isinstance(candidate.get("score_details"), dict) else {
                "keyword_score": row.get("keyword_score"),
                "embedding_score": row.get("embedding_score"),
                "substring_score": row.get("substring_score"),
                "policy_score": row.get("policy_score"),
            }
            review = _review_reuse_candidate_with_llm(
                client,
                target=targets_by_need[need_id],
                candidate=candidate_asset,
                policy_result=policy_result,
                score_details=score_details,
            )
            counterfactual_rows.append(
                {
                    "need_id": need_id,
                    "asset_id": asset_id,
                    "embedding_score": row.get("embedding_score"),
                    "llm_score": review.get("score"),
                    "llm_decision": review.get("decision"),
                    "llm_threshold": review.get("threshold"),
                    "llm_reason": review.get("brief_reason"),
                }
            )

    write_jsonl(stage_artifact_path(root, "summarize", "llm_counterfactual.jsonl"), counterfactual_rows)
    accepted_count = sum(1 for row in counterfactual_rows if row.get("llm_decision") == "accept")
    write_json(
        stage_artifact_path(root, "summarize", "llm_counterfactual_summary.json"),
        {
            "candidate_count": len(counterfactual_candidates),
            "reviewed_count": len(counterfactual_rows),
            "accepted_count": accepted_count,
            "accept_rate": safe_div(accepted_count, len(counterfactual_rows)),
            "skipped_reason": "" if allow_llm else "allow_llm_false",
            "raw_gold_adjustment": size_compatible_gold_summary(original_gold_sets, size_gold_sets),
        },
    )
    return root


def run_eval(
    *,
    plan_paths: Iterable[str | Path],
    library_dirs: Iterable[str | Path],
    output_dir: str | Path = "report",
    run_id: str = "",
    goldset_paths: Iterable[str | Path] = (),
    review_enabled: bool = False,
    allow_llm: bool = False,
    env_file: str | Path = ".env",
    notes: str = "",
) -> Path:
    run_dir = prepare_run(
        plan_paths=plan_paths,
        output_dir=output_dir,
        run_id=run_id,
        goldset_paths=goldset_paths,
        library_dirs=library_dirs,
        review_enabled=review_enabled,
        allow_llm=allow_llm,
        env_file=env_file,
        notes=notes,
    )
    run_hard_filter_stage(run_dir=run_dir, library_dirs=library_dirs)
    run_retrieve_stage(run_dir=run_dir, library_dirs=library_dirs, allow_llm=allow_llm, env_file=env_file)
    run_review_stage(
        run_dir=run_dir,
        review_enabled=review_enabled,
        allow_llm=allow_llm,
        env_file=env_file,
    )
    run_summarize_stage(run_dir=run_dir)
    return run_dir
