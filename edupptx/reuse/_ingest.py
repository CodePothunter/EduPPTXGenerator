"""复用层入库：增量更新库、入库 job 执行(copy+merge+写索引)、从 session 输出批量入库、VLM 入库审查编排、未充分元数据补全。函数体逐字一致。"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from edupptx.reuse._util import (
    _clean_text,
    _client_model_name,
    _read_existing_db,
)
from edupptx.reuse._constants import (
    DEFAULT_KEYWORD_BATCH_SIZE,
    DEFAULT_LIBRARY_IMAGE_DIR,
    DEFAULT_MATCH_INDEX_FILENAME,
    KEYWORD_SCHEMA_VERSION,
    SCHEMA_VERSION,
    STRICT_REUSE_GROUPS,
    STRICT_REUSE_INDEX_DIRNAME,
    _GENERAL_REUSE_GROUP,
    _REUSE_TARGET_METADATA_SEEDED_FIELD,
)
from edupptx.reuse._assets import (
    _as_string_list,
    _is_background_asset,
)
from edupptx.reuse._normalize import (
    _normalize_binary_reuse_group,
)
from edupptx.reuse._scoring import (
    _ratio_value,
    normalize_aspect_bucket,
)
from edupptx.reuse._embedding import (
    _relative_output_path,
)
from edupptx.reuse._store import (
    _dedupe_warnings,
    _is_skip_reuse_group,
    _normalize_rich_asset_fields,
    _resolve_asset_image_path,
    read_ai_image_split_match_index,
)
from edupptx.reuse._build import (
    _iter_session_dirs,
    build_ai_image_asset_db,
    write_ai_image_match_index,
)
from edupptx.reuse._keywords import (
    enrich_ai_image_asset_db_keywords,
)
from edupptx.reuse._materialize import (
    _contain_canvas_size,
)


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
