"""Offline goldset construction helpers for staged reuse evaluation."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


REUSABLE_INDEX_FILES = (
    "background.json",
    "C01_irreplaceable_entity_event_action.json",
    "C02_generic_subject_object.json",
    "C03_scene_decor_container.json",
)

SEMANTIC_REBUILD_INDEX_FILES = REUSABLE_INDEX_FILES

SEMANTIC_REBUILD_LABEL_METHOD = "chatgpt55_caption_query_semantic_no_filter_v1"

VALID_TARGET_GROUPS = {
    "background",
    "C00_strict_text_problem_skip",
    "C01_irreplaceable_entity_event_action",
    "C02_generic_subject_object",
    "C03_scene_decor_container",
}

MAX_ACCEPTABLE_ASSET_IDS = 10


class GoldLabelError(ValueError):
    """Raised when a goldset row violates the labeling contract."""


def _clean(value: Any) -> str:
    return str(value or "").strip()


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: Any) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _slot_key(role: str, index: int) -> str:
    base = _clean(role) or "illustration"
    return f"{base}_{index + 1}"


def _gold_label_text(image: dict[str, Any]) -> tuple[str, str]:
    caption = _clean(image.get("caption"))
    if caption:
        return caption, "caption"
    return _clean(image.get("query")), "query"


def extract_plan_image_needs(plan_paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plan_path_value in plan_paths:
        plan_path = Path(plan_path_value)
        plan_data = read_json(plan_path)
        session_id = plan_path.parent.name
        pages = plan_data.get("pages") if isinstance(plan_data.get("pages"), list) else []
        for page in pages:
            if not isinstance(page, dict):
                continue
            needs = page.get("material_needs") if isinstance(page.get("material_needs"), dict) else {}
            images = needs.get("images") if isinstance(needs.get("images"), list) else []
            for image_index, image in enumerate(images):
                if not isinstance(image, dict) or _clean(image.get("source")) != "ai_generate":
                    continue
                page_number = int(page.get("page_number") or 0)
                slot_key = _slot_key(_clean(image.get("role")) or "illustration", image_index)
                gold_text, gold_source = _gold_label_text(image)
                rows.append(
                    {
                        "need_id": f"{session_id}:p{page_number:02d}:{slot_key}",
                        "session_id": session_id,
                        "plan_path": str(plan_path),
                        "page_number": page_number,
                        "page_title": _clean(page.get("title")),
                        "page_type": _clean(page.get("page_type")),
                        "slot_key": slot_key,
                        "role": _clean(image.get("role")),
                        "aspect_ratio": _clean(image.get("aspect_ratio")),
                        "query": _clean(image.get("query")),
                        "caption": _clean(image.get("caption")),
                        "generation_prompt": _clean(image.get("generation_prompt")),
                        "prompt_route": dict(image.get("prompt_route") or {}),
                        "gold_label_text": gold_text,
                        "gold_label_text_source": gold_source,
                    }
                )
    return rows


def load_reusable_asset_ids(index_dir: str | Path) -> set[str]:
    root = Path(index_dir)
    ids: set[str] = set()
    for file_name in REUSABLE_INDEX_FILES:
        path = root / file_name
        if not path.exists():
            continue
        payload = read_json(path)
        for asset in payload.get("assets") or []:
            if isinstance(asset, dict) and _clean(asset.get("asset_id")):
                ids.add(_clean(asset.get("asset_id")))
    return ids


def _join_caption_query(record: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            _clean(record.get("caption")),
            _clean(record.get("query")),
        )
        if part
    )


def target_semantic_text(row: dict[str, Any]) -> str:
    target = row.get("target") if isinstance(row.get("target"), dict) else {}
    text = _join_caption_query(target)
    if text:
        return text
    return " ".join(
        part
        for part in (
            _clean(row.get("caption")),
            _clean(row.get("raw_query")),
            _clean(row.get("query")),
        )
        if part
    )


def material_semantic_text(asset: dict[str, Any]) -> str:
    return _join_caption_query(asset)


def load_semantic_rebuild_assets(index_dir: str | Path) -> list[dict[str, Any]]:
    root = Path(index_dir)
    assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for file_name in SEMANTIC_REBUILD_INDEX_FILES:
        path = root / file_name
        if not path.exists():
            continue
        payload = read_json(path)
        for raw in payload.get("assets") or []:
            if not isinstance(raw, dict):
                continue
            asset_id = _clean(raw.get("asset_id"))
            if not asset_id or asset_id in seen:
                continue
            item = dict(raw)
            item["asset_id"] = asset_id
            item["_source_index_file"] = file_name
            item["_semantic_text"] = material_semantic_text(item)
            assets.append(item)
            seen.add(asset_id)
    return assets


def semantic_asset_metadata(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": _clean(asset.get("asset_id")),
        "asset_kind": _clean(asset.get("asset_kind")),
        "strict_reuse_group": _clean(asset.get("strict_reuse_group")),
        "subject": _clean(asset.get("subject")),
        "general": asset.get("general") if isinstance(asset.get("general"), bool) else None,
        "aspect_ratio": _clean(asset.get("aspect_ratio")),
        "caption": _clean(asset.get("caption")),
        "query": _clean(asset.get("query")),
        "source_index_file": _clean(asset.get("_source_index_file")),
    }


def _metadata_for_ids(asset_ids: list[str], assets_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    metadata: list[dict[str, Any]] = []
    for asset_id in asset_ids:
        if asset_id not in assets_by_id:
            raise GoldLabelError(f"unknown reusable asset id in semantic decision: {asset_id}")
        metadata.append(semantic_asset_metadata(assets_by_id[asset_id]))
    return metadata


def apply_semantic_decisions(
    target_rows: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    *,
    assets_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rebuilt: list[dict[str, Any]] = []
    for target in target_rows:
        need_id = _clean(target.get("need_id"))
        decision = decisions.get(need_id, {})
        acceptable = [
            _clean(asset_id)
            for asset_id in decision.get("acceptable_asset_ids") or []
            if _clean(asset_id)
        ]
        best = [
            _clean(asset_id)
            for asset_id in decision.get("best_asset_ids") or []
            if _clean(asset_id)
        ]
        row = dict(target)
        row["label_status"] = _clean(row.get("label_status")) or "labeled"
        row["should_reuse"] = bool(acceptable)
        row["acceptable_asset_ids"] = acceptable
        row["best_asset_ids"] = best
        row["acceptable_asset_metadata"] = _metadata_for_ids(acceptable, assets_by_id)
        row["best_asset_metadata"] = _metadata_for_ids(best, assets_by_id)
        row["label_notes"] = _clean(decision.get("label_notes")) or "caption/query 语义裁定未找到可接受素材"
        row["label_method"] = SEMANTIC_REBUILD_LABEL_METHOD
        rebuilt.append(row)
    return rebuilt


def _metadata_ids(rows: list[dict[str, Any]]) -> list[str]:
    return [_clean(row.get("asset_id")) for row in rows]


def validate_semantic_rebuild_rows(rows: Iterable[dict[str, Any]], *, reusable_asset_ids: set[str]) -> None:
    seen: set[str] = set()
    for row in rows:
        need_id = _clean(row.get("need_id"))
        if not need_id:
            raise GoldLabelError("missing need_id")
        if need_id in seen:
            raise GoldLabelError(f"duplicate need_id: {need_id}")
        seen.add(need_id)

        acceptable = [
            _clean(asset_id)
            for asset_id in row.get("acceptable_asset_ids") or []
            if _clean(asset_id)
        ]
        best = [
            _clean(asset_id)
            for asset_id in row.get("best_asset_ids") or []
            if _clean(asset_id)
        ]
        if row.get("should_reuse") is not bool(acceptable):
            raise GoldLabelError(f"should_reuse mismatch for {need_id}")
        if len(acceptable) > MAX_ACCEPTABLE_ASSET_IDS:
            raise GoldLabelError(f"acceptable_asset_ids exceeds {MAX_ACCEPTABLE_ASSET_IDS} for {need_id}")
        if len(best) > 1:
            raise GoldLabelError(f"best_asset_ids exceeds 1 for {need_id}")
        for asset_id in [*acceptable, *best]:
            if asset_id not in reusable_asset_ids:
                raise GoldLabelError(f"unknown reusable asset id for {need_id}: {asset_id}")
        for asset_id in best:
            if asset_id not in acceptable:
                raise GoldLabelError(f"best asset must be acceptable for {need_id}: {asset_id}")

        acceptable_meta = row.get("acceptable_asset_metadata") or []
        best_meta = row.get("best_asset_metadata") or []
        if _metadata_ids(acceptable_meta) != acceptable:
            raise GoldLabelError(f"acceptable metadata mismatch for {need_id}")
        if _metadata_ids(best_meta) != best:
            raise GoldLabelError(f"best metadata mismatch for {need_id}")


def validate_goldset_rows(rows: Iterable[dict[str, Any]], *, reusable_asset_ids: set[str]) -> None:
    seen: set[str] = set()
    for row in rows:
        need_id = _clean(row.get("need_id"))
        if not need_id:
            raise GoldLabelError("missing need_id")
        if need_id in seen:
            raise GoldLabelError(f"duplicate need_id: {need_id}")
        seen.add(need_id)

        group = _clean(row.get("target_strict_reuse_group_gold"))
        if group not in VALID_TARGET_GROUPS:
            raise GoldLabelError(f"invalid target_strict_reuse_group_gold for {need_id}: {group}")

        acceptable = list(row.get("acceptable_asset_ids") or [])
        best = list(row.get("best_asset_ids") or [])
        if len(acceptable) > MAX_ACCEPTABLE_ASSET_IDS:
            raise GoldLabelError(f"acceptable_asset_ids exceeds {MAX_ACCEPTABLE_ASSET_IDS} for {need_id}")
        if len(best) > 1:
            raise GoldLabelError(f"best_asset_ids exceeds 1 for {need_id}")
        if group == "C00_strict_text_problem_skip" and (acceptable or best):
            raise GoldLabelError(f"C00 target cannot have candidates: {need_id}")
        for asset_id in [*acceptable, *best]:
            if asset_id not in reusable_asset_ids:
                raise GoldLabelError(f"unknown reusable asset id for {need_id}: {asset_id}")
        for asset_id in best:
            if asset_id not in acceptable:
                raise GoldLabelError(f"best asset must be acceptable for {need_id}: {asset_id}")


def _file_fingerprint(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def write_goldset_artifacts(
    *,
    rows: list[dict[str, Any]],
    output_dir: str | Path,
    index_dir: str | Path,
    candidate_audit_rows: Iterable[dict[str, Any]] = (),
) -> None:
    root = Path(output_dir)
    reusable_ids = load_reusable_asset_ids(index_dir)
    validate_goldset_rows(rows, reusable_asset_ids=reusable_ids)
    write_json(root / "goldset.json", {"schema_version": 1, "items": rows})
    write_jsonl(root / "candidate_label_audit.jsonl", candidate_audit_rows)
    plans = sorted({_clean(row.get("plan_path")) for row in rows if _clean(row.get("plan_path"))})
    index_root = Path(index_dir)
    write_json(
        root / "manifest.json",
        {
            "schema_version": 1,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "plan_paths": plans,
            "need_count": len(rows),
            "index_fingerprints": [
                _file_fingerprint(index_root / name)
                for name in REUSABLE_INDEX_FILES
                if (index_root / name).exists()
            ],
        },
    )
    per_session: dict[str, dict[str, int]] = defaultdict(lambda: {"image_needs": 0, "caption_empty": 0})
    for row in rows:
        stats = per_session[_clean(row.get("session_id"))]
        stats["image_needs"] += 1
        if not _clean(row.get("caption")):
            stats["caption_empty"] += 1
    write_json(root / "plan_extraction_summary.json", {"sessions": dict(per_session)})
