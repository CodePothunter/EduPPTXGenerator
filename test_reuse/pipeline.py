"""Independent staged evaluation flow for AI-image reuse."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from edupptx.config import Config
from edupptx.llm_client import create_llm_client
from edupptx.materials.ai_image_asset_db import (
    ReuseSearchContext,
    _build_reuse_target_asset,
    _finalize_reuse_candidate_collection,
    _load_reuse_library_for_search,
    _reuse_hard_filter_reject_reason,
    find_reusable_ai_image_asset,
    infer_grade_band,
)
from edupptx.models import PlanningDraft, iter_image_slot_keys
from test_reuse.metrics import candidate_filter_metrics, final_match_metrics, ranking_metrics


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


def _clean(value: Any) -> str:
    return str(value or "").strip()


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
    output_dir: str | Path,
    review_enabled: bool,
    allow_llm: bool,
    notes: str = "",
) -> dict[str, Any]:
    plans = [Path(path) for path in plan_paths]
    libraries = [Path(path) for path in library_dirs]
    return {
        "schema_version": 1,
        "tool": "test_reuse",
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "plan_paths": [str(path.expanduser().resolve()) for path in plans],
        "library_dirs": [str(path.expanduser().resolve()) for path in libraries],
        "plan_fingerprints": [_path_fingerprint(path) for path in plans],
        "library_fingerprints": [_path_fingerprint(path) for path in libraries],
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
    }


def extract_plan_needs(plan_path: str | Path, *, run_id: str) -> list[dict[str, Any]]:
    source = Path(plan_path)
    plan_data = read_json(source)
    draft = PlanningDraft.model_validate(plan_data)
    meta = plan_data.get("meta") if isinstance(plan_data.get("meta"), dict) else {}
    raw_pages = _raw_page_by_number(plan_data if isinstance(plan_data, dict) else {})

    lesson_id = _clean(meta.get("lesson_id")) or source.stem
    subject = _clean(meta.get("subject") or plan_data.get("subject") if isinstance(plan_data, dict) else "")
    grade = _clean(meta.get("grade") or meta.get("grade_norm") or meta.get("audience"))
    grade_band = _clean(meta.get("grade_band")) or infer_grade_band(grade, meta.get("audience"), draft.meta.topic)

    rows: list[dict[str, Any]] = []
    for page in draft.pages:
        if not page.material_needs.images:
            continue
        raw_page = raw_pages.get(page.page_number, {})
        for image_index, (slot_key, need) in enumerate(iter_image_slot_keys(page.material_needs.images)):
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


def build_target_records(plan_needs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for need in plan_needs:
        target = _build_reuse_target_asset(
            asset_kind="page_image",
            prompt=_clean(need.get("raw_query")),
            prompt_route=need.get("prompt_route") if isinstance(need.get("prompt_route"), dict) else None,
            background_route=None,
            theme=_clean(need.get("lesson_title")),
            grade=_clean(need.get("grade")),
            subject=_clean(need.get("subject")),
            page_title=_clean(need.get("page_title")),
            page_type=_clean(need.get("page_type")),
            role=_clean(need.get("role")) or "illustration",
            aspect_ratio=_clean(need.get("raw_aspect_ratio")) or "16:9",
            caption=_clean(need.get("caption")),
        )
        targets.append(
            {
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
                "content_prompt": target.get("content_prompt") or target.get("caption") or "",
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
                "field_sources": {"target": "existing_reuse_target_builder"},
                "field_confidence": {},
                "warnings": list(need.get("warnings") or []),
                "label_status": need.get("label_status", "unlabeled"),
                "should_reuse": need.get("should_reuse"),
                "acceptable_asset_ids": list(need.get("acceptable_asset_ids") or []),
                "best_asset_ids": list(need.get("best_asset_ids") or []),
                "label_notes": need.get("label_notes", ""),
            }
        )
    return targets


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


def hard_filter_rows_for_target(
    target_record: dict[str, Any],
    assets: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
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
        reject_reason = _reuse_hard_filter_reject_reason(target, asset)
        flags = _hard_filter_flags(reject_reason)
        rows.append(
            {
                "run_id": run_id,
                "need_id": need_id,
                "asset_id": asset_id,
                "target_strict_reuse_group": target.get("strict_reuse_group", ""),
                "candidate_strict_reuse_group": asset.get("strict_reuse_group", ""),
                "target_subject": target.get("subject", ""),
                "candidate_subject": asset.get("subject", ""),
                "target_aspect_ratio": target.get("aspect_ratio", ""),
                "candidate_aspect_ratio": asset.get("aspect_ratio", ""),
                **flags,
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
    threshold_rows = [row for row in debug.get("thresholded_candidates") or [] if isinstance(row, dict)]

    bm25_rank = _rank_map(bm25_rows)
    embedding_rank = _rank_map(embedding_rows)
    substring_rank = _rank_map(substring_rows)
    hybrid_rank = _rank_map(ranked_rows)
    bm25_by_id = _by_asset(bm25_rows)
    embedding_by_id = _by_asset(embedding_rows)
    substring_by_id = _by_asset(substring_rows)
    ranked_by_id = _by_asset(ranked_rows)
    threshold_ids = set(_rank_map(threshold_rows))

    scored_candidates: list[dict[str, Any]] = []
    for asset_id, row in ranked_by_id.items():
        bm25 = bm25_by_id.get(asset_id, {})
        embedding = embedding_by_id.get(asset_id, {})
        substring = substring_by_id.get(asset_id, {})
        scored_candidates.append(
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
                "hybrid_score": row.get("hybrid_score"),
                "accepted_by": row.get("accepted_by"),
                "threshold_used": row.get("threshold_used") or bm25.get("threshold_used"),
                "threshold_pass": asset_id in threshold_ids,
                **_label_for_record(target_record, asset_id),
            }
        )

    threshold_candidates: list[dict[str, Any]] = []
    for asset_id, row in _by_asset(threshold_rows).items():
        ranked = ranked_by_id.get(asset_id, {})
        threshold_candidates.append(
            {
                "run_id": run_id,
                "need_id": need_id,
                "asset_id": asset_id,
                "rank_hybrid": hybrid_rank.get(asset_id),
                "rank_bm25": bm25_rank.get(asset_id),
                "rank_embedding": embedding_rank.get(asset_id),
                "rank_substring": substring_rank.get(asset_id),
                "keyword_score": row.get("keyword_score", ranked.get("keyword_score")),
                "embedding_score": row.get("embedding_score", ranked.get("embedding_score")),
                "substring_score": row.get("substring_score", ranked.get("substring_score")),
                "hybrid_score": row.get("hybrid_score", ranked.get("hybrid_score")),
                "accepted_by": row.get("accepted_by", ranked.get("accepted_by")),
                "threshold_used": row.get("threshold_used", ranked.get("threshold_used")),
                "threshold_pass": True,
                **_label_for_record(target_record, asset_id),
            }
        )

    return {
        "scored_candidates": scored_candidates,
        "threshold_candidates": threshold_candidates,
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


def build_final_match_row(
    *,
    run_id: str,
    target_record: dict[str, Any],
    match: dict[str, Any] | None,
    threshold_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    asset = match.get("asset") if isinstance(match, dict) and isinstance(match.get("asset"), dict) else {}
    selected_asset_id = _asset_id(asset)
    label = _label_for_record(target_record, selected_asset_id)
    selected = bool(selected_asset_id)
    should_reuse = target_record.get("should_reuse")
    selected_ok = bool(label["is_acceptable"])

    if label["label_status"] != "labeled":
        match_status = "unlabeled"
        failure_stage = ""
    elif selected and selected_ok:
        match_status = "correct"
        failure_stage = ""
    elif selected:
        match_status = "wrong"
        failure_stage = "final_selection"
    elif should_reuse is True:
        match_status = "missed"
        failure_stage = "threshold_filter" if not threshold_candidates else "reuse_policy_or_llm_review"
    else:
        match_status = "correct_none"
        failure_stage = ""

    return {
        "run_id": run_id,
        "need_id": target_record.get("need_id"),
        "lesson_id": target_record.get("lesson_id"),
        "page_number": target_record.get("page_number"),
        "selected_asset_id": selected_asset_id,
        "selected_keyword_score": match.get("keyword_score") if isinstance(match, dict) else None,
        "selected_hybrid_score": match.get("hybrid_score") if isinstance(match, dict) else None,
        "selected_reuse_policy": match.get("reuse_policy") if isinstance(match, dict) else None,
        "selected_is_acceptable": selected_ok,
        "selected_is_best": bool(label["is_best"]),
        "match_status": match_status,
        "failure_stage": failure_stage,
        **label,
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


def _read_targets(run_dir: str | Path) -> list[dict[str, Any]]:
    return read_jsonl(Path(run_dir) / "targets.jsonl")


def _write_hard_filter_outputs(run_dir: Path, hard_rows: list[dict[str, Any]]) -> None:
    write_jsonl(run_dir / "hard_filter_pairs.jsonl", hard_rows)
    write_json(
        run_dir / "hard_filter_summary.json",
        {
            "all_hard_filters": candidate_filter_metrics(hard_rows, pass_field="all_hard_pass"),
            "category_filter": candidate_filter_metrics(hard_rows, pass_field="category_pass"),
            "subject_filter": candidate_filter_metrics(hard_rows, pass_field="subject_pass"),
            "aspect_filter": candidate_filter_metrics(hard_rows, pass_field="aspect_pass"),
        },
    )


def _write_threshold_outputs(
    run_dir: Path,
    scored_rows: list[dict[str, Any]],
    threshold_rows: list[dict[str, Any]],
) -> None:
    write_jsonl(run_dir / "scored_candidates.jsonl", scored_rows)
    write_jsonl(run_dir / "threshold_candidates.jsonl", threshold_rows)
    write_json(
        run_dir / "threshold_summary.json",
        {"threshold_filter": candidate_filter_metrics(threshold_rows, pass_field="threshold_pass")},
    )


def _threshold_rows_by_need(run_dir: str | Path) -> dict[str, list[dict[str, Any]]]:
    by_need: dict[str, list[dict[str, Any]]] = {}
    for row in read_jsonl(Path(run_dir) / "threshold_candidates.jsonl"):
        by_need.setdefault(_clean(row.get("need_id")), []).append(row)
    return by_need


def _collections_by_need(run_dir: str | Path) -> dict[str, dict[str, Any]]:
    collections: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(Path(run_dir) / "candidate_collections.jsonl"):
        need_id = _clean(row.get("need_id"))
        collection = row.get("collection")
        if need_id and isinstance(collection, dict):
            collections[need_id] = collection
    return collections


def prepare_run(
    *,
    plan_paths: Iterable[str | Path],
    output_dir: str | Path = "report",
    run_id: str = "",
    library_dirs: Iterable[str | Path] = (),
    review_enabled: bool = False,
    allow_llm: bool = False,
    notes: str = "",
) -> Path:
    run_dir = _run_dir_for(output_dir, run_id)
    run_name = run_dir.name
    run_dir.mkdir(parents=True, exist_ok=True)
    plans_dir = run_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    plan_path_list = [Path(path).expanduser().resolve() for path in plan_paths]
    library_dir_list = [Path(path).expanduser().resolve() for path in library_dirs]
    manifest = build_manifest(
        run_id=run_name,
        plan_paths=plan_path_list,
        library_dirs=library_dir_list,
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
    write_jsonl(run_dir / "plan_needs.jsonl", all_needs)
    write_jsonl(run_dir / "targets.jsonl", build_target_records(all_needs))
    return run_dir


def run_hard_filter_stage(
    *,
    run_dir: str | Path,
    library_dirs: Iterable[str | Path],
) -> Path:
    root = Path(run_dir)
    search_context = ReuseSearchContext()
    library_dir_list = [Path(path).expanduser().resolve() for path in library_dirs]
    library_assets = load_library_assets(library_dir_list, reuse_search_context=search_context)
    hard_rows: list[dict[str, Any]] = []
    for target_record in _read_targets(root):
        hard_rows.extend(hard_filter_rows_for_target(target_record, library_assets))
    _write_hard_filter_outputs(root, hard_rows)
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
    search_context = ReuseSearchContext()
    client = _keyword_client(env_file, allow_llm=allow_llm)

    scored_rows: list[dict[str, Any]] = []
    threshold_rows: list[dict[str, Any]] = []
    collection_rows: list[dict[str, Any]] = []
    for target_record in _read_targets(root):
        target = target_record["target"]
        collection = find_reusable_ai_image_asset(
            library_dir=library_dir_list,
            asset_kind="page_image",
            prompt=_clean(target_record.get("raw_query")),
            prompt_route=target.get("prompt_route") or None,
            theme=target.get("theme", ""),
            grade=target.get("grade_hint", ""),
            subject=target.get("subject_hint", ""),
            page_title=target_record.get("page_title", ""),
            page_type=target.get("page_type", ""),
            role=target_record.get("role", "illustration"),
            aspect_ratio=target.get("aspect_ratio", "16:9"),
            caption=target.get("caption", ""),
            keyword_client=client,
            debug_path=None,
            reuse_search_context=search_context,
            llm_review_enabled=False,
            _collect_candidates_only=True,
        )
        collection = collection if isinstance(collection, dict) else {}
        collection_rows.append(
            {
                "run_id": run_name,
                "need_id": target_record.get("need_id"),
                "collection": collection,
            }
        )
        flattened = flatten_candidate_collection(
            run_id=run_name,
            target_record=target_record,
            collection=collection,
        )
        scored_rows.extend(flattened["scored_candidates"])
        threshold_rows.extend(flattened["threshold_candidates"])

    write_jsonl(root / "candidate_collections.jsonl", collection_rows)
    _write_threshold_outputs(root, scored_rows, threshold_rows)
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
    client = _keyword_client(env_file, allow_llm=allow_llm)
    collections = _collections_by_need(root)
    threshold_by_need = _threshold_rows_by_need(root)
    llm_review_rows: list[dict[str, Any]] = []
    final_rows: list[dict[str, Any]] = []

    for target_record in _read_targets(root):
        need_id = _clean(target_record.get("need_id"))
        collection = collections.get(need_id, {})
        debug_path = root / "reuse_finalize_debug.jsonl"
        match = _finalize_reuse_candidate_collection(
            collection,
            debug_path=debug_path,
            keyword_client=client,
            reuse_session_state=None,
            llm_review_enabled=bool(review_enabled),
            reuse_debug_mode="full",
            constraint_embedding_cache=None,
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
        final_rows.append(
            build_final_match_row(
                run_id=run_name,
                target_record=target_record,
                match=match,
                threshold_candidates=threshold_by_need.get(need_id, []),
            )
        )

    write_jsonl(root / "llm_reviews.jsonl", llm_review_rows)
    write_jsonl(root / "final_matches.jsonl", final_rows)
    return root


def run_summarize_stage(*, run_dir: str | Path) -> Path:
    root = Path(run_dir)
    targets = _read_targets(root)
    hard_rows = read_jsonl(root / "hard_filter_pairs.jsonl")
    scored_rows = read_jsonl(root / "scored_candidates.jsonl")
    threshold_rows = read_jsonl(root / "threshold_candidates.jsonl")
    final_rows = read_jsonl(root / "final_matches.jsonl")

    _write_hard_filter_outputs(root, hard_rows)
    _write_threshold_outputs(root, scored_rows, threshold_rows)

    reusable_need_ids = {
        _clean(row.get("need_id"))
        for row in targets
        if row.get("label_status") == "labeled" and row.get("should_reuse") is True
    }
    metrics = {
        "hard_filter": read_json(root / "hard_filter_summary.json"),
        "threshold": read_json(root / "threshold_summary.json"),
        "ranking": ranking_metrics(scored_rows, reusable_need_ids=reusable_need_ids, rank_field="rank_hybrid"),
        "final": final_match_metrics(final_rows),
        "target_count": len(targets),
        "unlabeled_need_count": sum(1 for row in targets if row.get("label_status") != "labeled"),
    }
    write_json(root / "metrics.json", metrics)
    write_jsonl(root / "failure_cases.jsonl", [row for row in final_rows if row.get("failure_stage")])
    write_jsonl(root / "prompt_issue_log.jsonl", [])
    (root / "report.md").write_text(
        "\n".join(
            [
                "# Reuse Eval Report",
                "",
                f"- Run: `{_run_id_for(root)}`",
                f"- Targets: {len(targets)}",
                f"- Unlabeled targets: {metrics['unlabeled_need_count']}",
                f"- Final labeled needs: {metrics['final']['labeled_needs']}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


def run_eval(
    *,
    plan_paths: Iterable[str | Path],
    library_dirs: Iterable[str | Path],
    output_dir: str | Path = "report",
    run_id: str = "",
    review_enabled: bool = False,
    allow_llm: bool = False,
    env_file: str | Path = ".env",
    notes: str = "",
) -> Path:
    run_dir = prepare_run(
        plan_paths=plan_paths,
        output_dir=output_dir,
        run_id=run_id,
        library_dirs=library_dirs,
        review_enabled=review_enabled,
        allow_llm=allow_llm,
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
