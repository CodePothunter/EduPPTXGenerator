"""复用层索引存储：建库内存索引(归一/去重/C01→C03投影)、split JSON 读写(原子)、match index 组装、按 target 路由分组、json/sqlite 后端分支读。依赖 _util/_constants/_assets/_normalize/_scoring/_backend/_embedding。函数体与原 ai_image_asset_db.py 逐字一致。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from edupptx.reuse._util import (
    _clean_text,
    _dedupe_terms,
    _join_texts,
    _read_existing_db,
)
from edupptx.reuse._constants import (
    BACKGROUND_REUSE_INDEX_FILENAME,
    BACKGROUND_REUSE_INDEX_GROUP,
    DEFAULT_MATCH_INDEX_FILENAME,
    MATCH_INDEX_SCHEMA_VERSION,
    STRICT_REUSE_GROUPS,
    STRICT_REUSE_INDEX_DIRNAME,
    _BACKGROUND_ROUTE_FIELDS,
    _BACKGROUND_ROUTE_MATCH_FIELDS,
    _CONTENT_REUSE_GROUP,
    _GENERAL_REUSE_GROUP,
    _METADATA_PASSTHROUGH_FIELDS,
    _PAGE_TYPE_CONTEXT_SUMMARIES,
    _STRICT_REUSE_READ_GROUPS,
)
from edupptx.reuse._assets import (
    _as_string_list,
    _asset_caption,
    _asset_content_prompt,
    _asset_page_type,
    _clean_prompt_route,
    _is_background_asset,
    _normalize_subject_value,
    _optional_bool,
    _source_pptx_refs_for_asset,
    _topic_refs_for_asset,
    _unit_ref_for_asset,
)
from edupptx.reuse._normalize import (
    _normalize_binary_reuse_group,
    _normalize_grade_band_value,
    _normalize_grade_norm_value,
)
from edupptx.reuse._scoring import (
    _background_color_bias,
    _bm25_similarity_with_hits,
    _bm25_tokens_from_values,
    _clean_background_route,
    normalize_aspect_bucket,
)
from edupptx.reuse._backend import (
    _get_asset_store,
    _use_sqlite_backend,
)
from edupptx.reuse._embedding import (
    _ensure_ai_image_embedding_index,
    write_ai_image_embedding_index,
)


def build_ai_image_match_index(
    db: dict[str, Any],
    *,
    library_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build the slim deterministic index used by image reuse matching."""

    root = Path(library_root).expanduser().resolve() if library_root is not None else None
    raw_assets = db.get("assets")
    assets: list[dict[str, Any]] = []
    warnings: list[str] = []
    if isinstance(raw_assets, list):
        for raw_asset in raw_assets:
            if not isinstance(raw_asset, dict):
                continue
            match_asset = _normalize_asset_for_match(raw_asset, library_root=root)
            if match_asset is None:
                warnings.append(f"match index skipped invalid asset: {_clean_text(raw_asset.get('asset_id')) or '<missing>'}")
                continue
            assets.append(match_asset)

    deduped_assets = _dedupe_match_assets(assets)
    for asset in deduped_assets:
        asset.pop("_image_sha256", None)
        asset.pop("_quality_score", None)
        asset["strict_reuse_group"] = _normalize_binary_reuse_group(
            asset.get("strict_reuse_group"),
            default=_GENERAL_REUSE_GROUP,
        )
    skip_reuse_assets = [
        asset
        for asset in deduped_assets
        if _is_skip_reuse_group(asset.get("strict_reuse_group"))
    ]
    deduped_assets = [
        asset
        for asset in deduped_assets
        if not _is_skip_reuse_group(asset.get("strict_reuse_group"))
    ]

    now = datetime.now(timezone.utc).isoformat()
    index = {
        "schema_version": MATCH_INDEX_SCHEMA_VERSION,
        "built_at": now,
        "updated_at": now,
        "db_schema_version": int(db.get("schema_version") or 0),
        "asset_root": str(root) if root is not None else _clean_text(db.get("output_root")),
        "input_asset_count": len(raw_assets) if isinstance(raw_assets, list) else 0,
        "asset_count": len(deduped_assets),
        "assets": deduped_assets,
        "warnings": _dedupe_warnings([*_as_string_list(db.get("warnings")), *warnings]),
    }
    if skip_reuse_assets:
        index["skip_reuse_assets"] = skip_reuse_assets
    for key in ("ppt_extractor", "keyword_builder", "keyword_built_at"):
        if key in db:
            index[key] = deepcopy(db[key])
    return index


def _c01_secondary_c03_projections(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from edupptx.materials.strict_reuse_classifier import (
        C01_IRREPLACEABLE_ENTITY_EVENT_ACTION,
        C03_SCENE_DECOR_CONTAINER,
        normalize_secondary_reuse_group,
    )

    projections: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict) or _is_background_asset(asset):
            continue
        primary = _normalize_binary_reuse_group(asset.get("strict_reuse_group"), default=_GENERAL_REUSE_GROUP)
        if primary != C01_IRREPLACEABLE_ENTITY_EVENT_ACTION:
            continue
        secondary = normalize_secondary_reuse_group(asset.get("strict_reuse_secondary_group"), primary=primary)
        if secondary != C03_SCENE_DECOR_CONTAINER:
            continue
        projection = deepcopy(asset)
        asset_id = _clean_text(asset.get("asset_id"))
        projection["strict_reuse_group"] = C03_SCENE_DECOR_CONTAINER
        denamed_query = (
            _clean_text(asset.get("secondary_reuse_query"))
            or _clean_text(asset.get("secondary_reuse_caption"))
            or _clean_text(asset.get("query"))
        )
        denamed_caption = (
            _clean_text(asset.get("secondary_reuse_caption"))
            or denamed_query
            or _clean_text(asset.get("caption"))
        )
        if denamed_query:
            projection["query"] = denamed_query
        projection["caption"] = denamed_caption
        projection["secondary_projection"] = True
        projection["secondary_projection_of"] = asset_id
        projection.pop("strict_reuse_secondary_group", None)
        projection.pop("secondary_reuse_query", None)
        projection.pop("secondary_reuse_caption", None)
        projections.append(projection)
    return projections


def _merge_skip_group_assets(existing: list[Any], current: list[Any]) -> list[dict[str, Any]]:
    """Union skip-group (C00) assets by asset_id; current-run entries win, id-less kept.

    M-4: C00 是归档/审计桶，不参与复用。读取端与构建端都会丢弃历史 C00，写端又每次
    整体覆盖，导致逐个 PPTX 入库后 C00.json 只剩最后一批。写前按 asset_id 取并集让
    C00 跨 run 累积。C00 无删除路径，故 union 安全。
    """
    merged: dict[str, dict[str, Any]] = {}
    for asset in existing:
        if not isinstance(asset, dict):
            continue
        aid = _clean_text(asset.get("asset_id"))
        if aid:
            merged[aid] = asset
    extras: list[dict[str, Any]] = []
    for asset in current:
        if not isinstance(asset, dict):
            continue
        aid = _clean_text(asset.get("asset_id"))
        if aid:
            merged[aid] = asset
        else:
            extras.append(asset)
    return [*merged.values(), *extras]


def _read_split_group_assets(group_path: Path) -> list[dict[str, Any]]:
    if not group_path.exists():
        return []
    try:
        payload = json.loads(group_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    assets = payload.get("assets") if isinstance(payload, dict) else None
    return assets if isinstance(assets, list) else []


def write_ai_image_split_match_indexes(
    match_index: dict[str, Any],
    library_dir: str | Path,
    *,
    split_dirname: str = STRICT_REUSE_INDEX_DIRNAME,
) -> Path:
    """Persist only the binary reuse-group JSON indexes."""

    root = Path(library_dir).expanduser().resolve()
    split_dir = root / split_dirname
    split_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    raw_assets = match_index.get("assets")
    assets = raw_assets if isinstance(raw_assets, list) else []
    raw_skip_assets = match_index.get("skip_reuse_assets")
    skip_assets = raw_skip_assets if isinstance(raw_skip_assets, list) else []
    split_source_assets = [*assets, *skip_assets]

    for group in STRICT_REUSE_GROUPS:
        group_assets: list[dict[str, Any]] = []
        for asset in split_source_assets:
            if not isinstance(asset, dict):
                continue
            if _is_background_asset(asset):
                continue
            normalized_group = _normalize_binary_reuse_group(
                asset.get("strict_reuse_group"),
                default=_GENERAL_REUSE_GROUP,
            )
            if _is_skip_reuse_group(normalized_group) and normalized_group != group:
                continue
            if normalized_group != group:
                continue
            copied = deepcopy(asset)
            copied["strict_reuse_group"] = group
            if _is_skip_reuse_group(group):
                copied.pop("image_path", None)
            group_assets.append(copied)
        if group == _GENERAL_REUSE_GROUP:
            group_assets.extend(_c01_secondary_c03_projections(split_source_assets))
        group_path = split_dir / f"{group}.json"
        if _is_skip_reuse_group(group):
            # M-4: 写前与磁盘上旧 C00.json 取并集，避免跨 run 整体覆盖丢失归档。
            group_assets = _merge_skip_group_assets(_read_split_group_assets(group_path), group_assets)
        payload = {
            "schema_version": match_index.get("schema_version", MATCH_INDEX_SCHEMA_VERSION),
            "strict_reuse_group": group,
            "built_at": match_index.get("built_at") or now,
            "updated_at": now,
            "asset_root": match_index.get("asset_root") or str(root),
            "asset_count": len(group_assets),
            "assets": group_assets,
            "warnings": match_index.get("warnings", []),
        }
        for key in ("ppt_extractor", "keyword_builder", "keyword_built_at"):
            if key in match_index:
                payload[key] = deepcopy(match_index[key])
        temp_path = group_path.with_name(f"{group_path.name}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, group_path)

    background_assets: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict) or not _is_background_asset(asset):
            continue
        copied = deepcopy(asset)
        copied["asset_kind"] = "background"
        copied["strict_reuse_group"] = _normalize_binary_reuse_group(
            copied.get("strict_reuse_group"),
            default=_GENERAL_REUSE_GROUP,
        )
        if _is_skip_reuse_group(copied.get("strict_reuse_group")):
            continue
        background_assets.append(copied)
    background_payload = {
        "schema_version": match_index.get("schema_version", MATCH_INDEX_SCHEMA_VERSION),
        "strict_reuse_group": BACKGROUND_REUSE_INDEX_GROUP,
        "built_at": match_index.get("built_at") or now,
        "updated_at": now,
        "asset_root": match_index.get("asset_root") or str(root),
        "asset_count": len(background_assets),
        "assets": background_assets,
        "warnings": match_index.get("warnings", []),
    }
    for key in ("ppt_extractor", "keyword_builder", "keyword_built_at"):
        if key in match_index:
            background_payload[key] = deepcopy(match_index[key])
    background_path = split_dir / BACKGROUND_REUSE_INDEX_FILENAME
    temp_path = background_path.with_name(f"{background_path.name}.tmp")
    temp_path.write_text(json.dumps(background_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, background_path)

    legacy_manifest = split_dir / "strict_reuse_split_manifest.json"
    if legacy_manifest.exists():
        legacy_manifest.unlink()
    return split_dir


def _assemble_match_index_from_group_payloads(
    group_payloads: list[tuple[str, dict[str, Any]]],
    root: Path,
) -> dict[str, Any] | None:
    """Assemble the in-memory match index from per-group payloads (file- or sqlite-sourced).

    ``group_payloads`` is an ordered list of (group_file, payload); ``group_file`` is one
    of the 4 read groups or 'background'. Reproduces the historical split-file assembly:
    drop secondary projections + C00 (skip) from groups, dedup backgrounds when a
    background split exists, and keep only backgrounds (kind-forced) from the bg group.
    """
    assets: list[dict[str, Any]] = []
    warnings: list[str] = []
    source_payloads: list[dict[str, Any]] = []
    has_background_split = any(group == BACKGROUND_REUSE_INDEX_GROUP for group, _ in group_payloads)
    for group, payload in group_payloads:
        source_payloads.append(payload)
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list):
            warnings.append(f"split index skipped invalid assets: {group}")
            continue
        if group == BACKGROUND_REUSE_INDEX_GROUP:
            for item in raw_assets:
                if not isinstance(item, dict):
                    continue
                asset = deepcopy(item)
                if not _is_background_asset(asset):
                    continue
                asset["asset_kind"] = "background"
                asset["strict_reuse_group"] = _normalize_binary_reuse_group(
                    asset.get("strict_reuse_group"), default=_GENERAL_REUSE_GROUP
                )
                if _is_skip_reuse_group(asset.get("strict_reuse_group")):
                    continue
                assets.append(asset)
        else:
            for item in raw_assets:
                if not isinstance(item, dict):
                    continue
                asset = deepcopy(item)
                if asset.get("secondary_projection") is True:
                    continue
                asset["strict_reuse_group"] = _normalize_binary_reuse_group(
                    asset.get("strict_reuse_group") or payload.get("strict_reuse_group") or group,
                    default=_GENERAL_REUSE_GROUP,
                )
                if _is_skip_reuse_group(asset.get("strict_reuse_group")):
                    continue
                if has_background_split and _is_background_asset(asset):
                    continue
                assets.append(asset)
        warnings.extend(_as_string_list(payload.get("warnings")))

    if not source_payloads:
        return None
    now = datetime.now(timezone.utc).isoformat()
    first_payload = source_payloads[0]
    index = {
        "schema_version": MATCH_INDEX_SCHEMA_VERSION,
        "built_at": first_payload.get("built_at") or now,
        "updated_at": now,
        "asset_root": first_payload.get("asset_root") or str(root),
        "input_asset_count": len(assets),
        "asset_count": len(assets),
        "assets": assets,
        "warnings": _dedupe_warnings(warnings),
    }
    for key in ("ppt_extractor", "keyword_builder", "keyword_built_at"):
        if key in first_payload:
            index[key] = deepcopy(first_payload[key])
    return index


def read_ai_image_split_match_index(
    library_dir: str | Path,
    *,
    split_dirname: str = STRICT_REUSE_INDEX_DIRNAME,
) -> tuple[dict[str, Any], Path] | None:
    """Read the binary reuse-group indexes as one in-memory index."""

    root = Path(library_dir).expanduser().resolve()
    if _use_sqlite_backend(root):
        group_payloads = list(_get_asset_store(root).iter_group_payloads())
        if not group_payloads:
            return None
        index = _assemble_match_index_from_group_payloads(group_payloads, root)
        if index is None:
            return None
        from edupptx.materials.asset_store import default_library_db_path

        return index, default_library_db_path(root)

    split_dir = root / split_dirname
    if not split_dir.exists():
        return None
    group_payloads = []
    for group in _STRICT_REUSE_READ_GROUPS:
        path = split_dir / f"{group}.json"
        if path.exists():
            group_payloads.append((group, _read_existing_db(path)))
    background_split_path = split_dir / BACKGROUND_REUSE_INDEX_FILENAME
    if background_split_path.exists():
        group_payloads.append((BACKGROUND_REUSE_INDEX_GROUP, _read_existing_db(background_split_path)))
    if not group_payloads:
        return None
    index = _assemble_match_index_from_group_payloads(group_payloads, root)
    if index is None:
        return None
    return index, split_dir


def _grade_info_from_asset(asset: dict[str, Any]) -> dict[str, str]:
    return {
        "grade_norm": _normalize_grade_norm_value(asset.get("grade_norm")),
        "grade_band": _normalize_grade_band_value(asset.get("grade_band")),
    }


def _fallback_context_summary(asset: dict[str, Any]) -> str:
    return _default_context_summary(
        asset_kind=_clean_text(asset.get("asset_kind")),
        content_prompt=_asset_content_prompt(asset),
        theme=_clean_text(asset.get("theme")),
        page_type=_asset_page_type(asset),
    )


def _default_normalized_prompt(asset: dict[str, Any]) -> str:
    return (_clean_text(asset.get("normalized_prompt")) or _asset_content_prompt(asset))[:80]


def _default_context_summary(
    *,
    asset_kind: str,
    content_prompt: str,
    theme: str,
    page_title: str = "",
    page_type: str = "",
) -> str:
    if asset_kind == "background":
        return "作为课件统一背景，提供低干扰视觉氛围并承载页面文字"[:120]

    usage = _PAGE_TYPE_CONTEXT_SUMMARIES.get(
        page_type,
        "作为页面辅助插图，支持本页教学内容呈现",
    )
    title = _clean_text(page_title)
    if title:
        return f"{title}：{usage}"[:120]
    return usage[:120]


def _default_teaching_intent(asset: dict[str, Any] | None = None, *, asset_kind: str = "", page_type: str = "") -> str:
    if asset is not None:
        asset_kind = _clean_text(asset.get("asset_kind"))
        page_type = _asset_page_type(asset)
    if asset_kind == "background":
        return "作为整套课件的低干扰视觉背景，承载页面文字和主要内容"
    if page_type == "cover":
        return "作为页面主视觉，建立课程主题和导入氛围"
    if page_type == "exercise":
        return "辅助练习或互动任务呈现，降低阅读负担"
    if page_type == "summary":
        return "辅助总结页面形成视觉记忆点"
    return "辅助解释页面知识点，帮助学生理解和记忆"


def _image_dimension_fields(item: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    original_image_path = _clean_text(item.get("original_image_path"))
    if original_image_path:
        fields["original_image_path"] = original_image_path
    for key in ("actual_width", "actual_height", "padded_width", "padded_height"):
        try:
            value = int(item.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            fields[key] = value
    return fields


def _normalize_asset_for_match(
    asset: dict[str, Any],
    *,
    library_root: Path | None = None,
    for_target: bool = False,
) -> dict[str, Any] | None:
    item = deepcopy(asset)
    _normalize_rich_asset_fields(item, keep_match_keywords=for_target)

    asset_id = _clean_text(item.get("asset_id"))
    asset_kind = _clean_text(item.get("asset_kind"))
    image_path = _clean_text(item.get("image_path"))
    normalized_group = _normalize_binary_reuse_group(item.get("strict_reuse_group"), default="")
    if not asset_id or not asset_kind:
        return None
    if not for_target and not image_path and not _is_skip_reuse_group(normalized_group):
        return None

    if _is_background_asset(item):
        match_asset: dict[str, Any] = {
            "asset_id": asset_id,
            "asset_kind": "background",
            "image_path": image_path,
            "aspect_ratio": _clean_text(item.get("aspect_ratio")),
            "theme": _clean_text(item.get("theme")),
            "subject": _normalize_subject_value(item.get("subject")),
            "grade_norm": _normalize_grade_norm_value(item.get("grade_norm")),
            "grade_band": _normalize_grade_band_value(item.get("grade_band")),
            "unit_ref": _unit_ref_for_asset(item),
            "topic_refs": _topic_refs_for_asset(item),
            "content_prompt": _asset_content_prompt(item),
            "background_route": _match_background_route(item.get("background_route")),
            "normalized_prompt": _clean_text(item.get("normalized_prompt")) or _asset_content_prompt(item),
            "color_temperature": _clean_text(item.get("color_temperature")),
            "context_summary": _clean_text(item.get("context_summary")),
            "teaching_intent": _clean_text(item.get("teaching_intent")),
        }
    else:
        match_asset = {
            "asset_id": asset_id,
            "asset_kind": "page_image",
            "image_path": image_path,
            "aspect_ratio": _clean_text(item.get("aspect_ratio")),
            "page_type": _asset_page_type(item),
            "theme": _clean_text(item.get("theme")),
            "subject": _normalize_subject_value(item.get("subject")),
            "grade_norm": _normalize_grade_norm_value(item.get("grade_norm")),
            "grade_band": _normalize_grade_band_value(item.get("grade_band")),
            "unit_ref": _unit_ref_for_asset(item),
            "topic_refs": _topic_refs_for_asset(item),
            "caption": _asset_caption(item),
            "detail_prompt": _clean_text(item.get("detail_prompt")),
            "context_summary": _clean_text(item.get("context_summary")),
            "teaching_intent": _clean_text(item.get("teaching_intent")),
            "duplicate_asset_ids": _dedupe_terms(_as_string_list(item.get("duplicate_asset_ids"))),
        }
    source_refs = _source_pptx_refs_for_asset(item)
    if source_refs:
        match_asset["source_pptx_refs"] = source_refs
    match_asset.update(_image_dimension_fields(item))
    match_asset.update(_preserve_review_fields(item))
    general = _optional_bool(item.get("general"))
    if general is not None:
        match_asset["general"] = general
    if library_root is not None and image_path:
        image_file = _resolve_asset_image_path(library_root, image_path)
        if image_file is not None and image_file.exists():
            match_asset["_image_sha256"] = _file_sha256(image_file)

    match_asset["_quality_score"] = _match_asset_quality_score(match_asset)
    return _strip_empty_match_fields(match_asset)


def _normalize_rich_asset_fields(asset: dict[str, Any], *, keep_match_keywords: bool = False) -> None:
    preserved_review_fields = _preserve_review_fields(asset)
    content_prompt = _asset_content_prompt(asset)
    caption = _asset_caption(asset)
    normalized_prompt = _default_normalized_prompt(asset)
    context_summary = _clean_text(asset.get("context_summary")) or _fallback_context_summary(asset)
    teaching_intent = _clean_text(asset.get("teaching_intent")) or _default_teaching_intent(asset)
    grade_info = _grade_info_from_asset(asset)
    general = _optional_bool(asset.get("general"))
    if _is_background_asset(asset):
        background_route = _match_background_route(asset.get("background_route"))
        color_bias = _background_color_bias(asset)
        content_prompt = _strip_background_color_bias_from_prompt(content_prompt, color_bias)
        color_temperature = _clean_text(asset.get("color_temperature"))
        cleaned = {
            "asset_id": _clean_text(asset.get("asset_id")),
            "asset_kind": "background",
            "image_path": _clean_text(asset.get("image_path")),
            "aspect_ratio": _clean_text(asset.get("aspect_ratio")),
            "theme": _clean_text(asset.get("theme")),
            "subject_hint": _clean_text(asset.get("subject_hint")),
            "grade_hint": _clean_text(asset.get("grade_hint")),
            "subject": _normalize_subject_value(asset.get("subject")),
            "grade_norm": grade_info["grade_norm"] or _clean_text(asset.get("grade_norm")),
            "grade_band": grade_info["grade_band"] or _clean_text(asset.get("grade_band")),
            "unit_ref": _unit_ref_for_asset(asset),
            "topic_refs": _topic_refs_for_asset(asset),
            "content_prompt": content_prompt,
            "background_route": background_route,
            "normalized_prompt": normalized_prompt,
            "color_temperature": color_temperature,
            "context_summary": context_summary,
            "teaching_intent": teaching_intent,
        }
        source_refs = _source_pptx_refs_for_asset(asset)
        if source_refs:
            cleaned["source_pptx_refs"] = source_refs
        if general is not None:
            cleaned["general"] = general
        cleaned.update(_image_dimension_fields(asset))
        cleaned.update(preserved_review_fields)
        asset.clear()
        asset.update(cleaned)
        return

    cleaned = {
        "asset_id": _clean_text(asset.get("asset_id")),
        "asset_kind": "page_image",
        "image_path": _clean_text(asset.get("image_path")),
        "aspect_ratio": _clean_text(asset.get("aspect_ratio")),
        "page_type": _asset_page_type(asset),
        "theme": _clean_text(asset.get("theme")),
        "subject_hint": _clean_text(asset.get("subject_hint")),
        "grade_hint": _clean_text(asset.get("grade_hint")),
        "subject": _normalize_subject_value(asset.get("subject")),
        "grade_norm": grade_info["grade_norm"] or _clean_text(asset.get("grade_norm")),
        "grade_band": grade_info["grade_band"] or _clean_text(asset.get("grade_band")),
        "unit_ref": _unit_ref_for_asset(asset),
        "topic_refs": _topic_refs_for_asset(asset),
        "caption": caption,
        "context_summary": context_summary,
        "teaching_intent": teaching_intent,
        "duplicate_asset_ids": _dedupe_terms(_as_string_list(asset.get("duplicate_asset_ids"))),
    }
    source_refs = _source_pptx_refs_for_asset(asset)
    if source_refs:
        cleaned["source_pptx_refs"] = source_refs
    detail_prompt = _clean_text(asset.get("detail_prompt"))
    if detail_prompt:
        cleaned["detail_prompt"] = detail_prompt
    if general is not None:
        cleaned["general"] = general
    cleaned.update(_image_dimension_fields(asset))
    cleaned.update(preserved_review_fields)
    asset.clear()
    asset.update(cleaned)


def _strip_empty_match_fields(asset: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in asset.items():
        if key.startswith("_"):
            cleaned[key] = value
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        if value in ("", None):
            continue
        cleaned[key] = value
    return cleaned


def _route_match_text(asset: dict[str, Any]) -> str:
    if not _is_background_asset(asset):
        return _join_texts(
            _dedupe_terms(
                [
                    _asset_page_type(asset),
                    _route_grade_family(asset),
                ]
            )
        )

    route = _match_prompt_route(asset.get("prompt_route"))
    terms: list[str] = [
        _clean_text(route.get("template_family")),
        *_as_string_list(route.get("profile_ids")),
        *_background_route_match_terms(asset),
    ]
    return _join_texts(_dedupe_terms(terms))


def _match_prompt_route(value: Any) -> dict[str, Any]:
    route = _clean_prompt_route(value)
    match_route: dict[str, Any] = {}
    template_family = _clean_text(route.get("template_family"))
    if template_family:
        match_route["template_family"] = template_family
    profile_ids = _as_string_list(route.get("profile_ids"))
    if profile_ids:
        match_route["profile_ids"] = _dedupe_terms(profile_ids)
    return match_route


def _background_route_match_terms(asset: dict[str, Any]) -> list[str]:
    route = _match_background_route(asset.get("background_route"))
    return [_clean_text(route.get(key)) for key in _BACKGROUND_ROUTE_MATCH_FIELDS]


def _match_background_route(value: Any) -> dict[str, Any]:
    route = _clean_background_route(value)
    match_route: dict[str, Any] = {}
    for key in _BACKGROUND_ROUTE_MATCH_FIELDS:
        text = _clean_text(route.get(key))
        if text:
            match_route[key] = text
    return match_route


def _background_route_terms(asset: dict[str, Any]) -> list[str]:
    route = _clean_background_route(asset.get("background_route"))
    terms: list[str] = []
    for key in _BACKGROUND_ROUTE_FIELDS:
        terms.append(_clean_text(route.get(key)))
    terms.extend(_as_string_list(route.get("color_terms")))
    return terms


def _strip_background_color_bias_from_prompt(prompt: str, color_bias: str) -> str:
    prompt = _clean_text(prompt)
    color_bias = _clean_text(color_bias)
    if not prompt or not color_bias or color_bias not in prompt:
        return prompt

    stripped = prompt.replace(f"配色偏向：{color_bias}", "")
    stripped = stripped.replace(color_bias, "")
    stripped = re.sub(r"[\s,，、;；:：]+$", "", stripped)
    stripped = re.sub(r"^[\s,，、;；:：]+", "", stripped)
    return _clean_text(stripped)


def _route_grade_family(asset: dict[str, Any]) -> str:
    return _normalize_grade_band_value(asset.get("grade_band"))


def _ratio_orientation(value: str) -> str:
    value = normalize_aspect_bucket(value)
    if value == "other":
        return ""
    parts = value.split(":")
    if len(parts) != 2:
        return ""
    try:
        width = float(parts[0])
        height = float(parts[1])
    except ValueError:
        return ""
    if width == height:
        return "square"
    return "landscape" if width > height else "portrait"


def _read_match_index_or_build(library_root: Path, db: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    if _use_sqlite_backend(library_root):
        # sqlite backend: pure read, never rebuild/write on the read path.
        from edupptx.materials.asset_store import default_library_db_path

        result = read_ai_image_split_match_index(library_root)
        if result is not None:
            return result
        return (
            {"schema_version": MATCH_INDEX_SCHEMA_VERSION, "asset_count": 0, "assets": []},
            default_library_db_path(library_root),
        )

    index_path = library_root / DEFAULT_MATCH_INDEX_FILENAME
    split_index = read_ai_image_split_match_index(library_root)
    if split_index is not None:
        index, split_dir = split_index
        embedding_report = _ensure_ai_image_embedding_index(index, library_root)
        if embedding_report:
            index["embedding_index"] = embedding_report
        return index, split_dir

    index = _read_existing_db(index_path)
    index_assets = index.get("assets")
    db_assets = db.get("assets")
    if isinstance(index_assets, list) and int(index.get("schema_version") or 0) == MATCH_INDEX_SCHEMA_VERSION:
        db_asset_count = len(db_assets) if isinstance(db_assets, list) else None
        if db_asset_count is None or int(index.get("input_asset_count") or -1) == db_asset_count:
            embedding_report = _ensure_ai_image_embedding_index(index, library_root)
            if embedding_report:
                index["embedding_index"] = embedding_report
            split_dir = write_ai_image_split_match_indexes(index, library_root)
            if index_path.exists():
                index_path.unlink()
            return index, split_dir

    if isinstance(db_assets, list):
        index = build_ai_image_match_index(db, library_root=library_root)
        try:
            embedding_report = write_ai_image_embedding_index(index, library_root)
            if embedding_report:
                index["embedding_index"] = embedding_report
            split_dir = write_ai_image_split_match_indexes(index, library_root)
            if index_path.exists():
                index_path.unlink()
        except Exception:
            pass
        return index, library_root / STRICT_REUSE_INDEX_DIRNAME

    return {"schema_version": MATCH_INDEX_SCHEMA_VERSION, "asset_count": 0, "assets": []}, library_root / STRICT_REUSE_INDEX_DIRNAME


def _route_match_index_for_target(
    library_root: Path,
    index: dict[str, Any],
    match_index_path: Path,
    target: dict[str, Any],
) -> tuple[dict[str, Any], Path, list[Any], str] | None:
    asset_kind = _clean_text(target.get("asset_kind"))
    if not asset_kind:
        return None
    if _use_sqlite_backend(library_root):
        store = _get_asset_store(library_root)
        if asset_kind == "background":
            group = BACKGROUND_REUSE_INDEX_GROUP
        else:
            group = _normalize_binary_reuse_group(target.get("strict_reuse_group"), default=_GENERAL_REUSE_GROUP)
        # An empty-but-valid route group must route to an EMPTY bucket (assets=[]),
        # mirroring the JSON backend where <group>.json exists with assets:[]. Returning
        # None here would let find_reusable fall into a more permissive non-routed path.
        payload = store.load_group_payload(group) or {"strict_reuse_group": group, "asset_count": 0, "assets": []}
        split_assets = payload.get("assets")
        if not isinstance(split_assets, list):
            split_assets = []
        split_index = dict(payload)
        split_index.setdefault("source_index_dir", str(library_root))
        return split_index, library_root / "library.db", split_assets, group
    if asset_kind == "background":
        background_path = library_root / STRICT_REUSE_INDEX_DIRNAME / BACKGROUND_REUSE_INDEX_FILENAME
        if background_path.exists():
            split_index = _read_existing_db(background_path)
            split_assets = split_index.get("assets")
            if not isinstance(split_assets, list):
                return None
            split_index = dict(split_index)
            split_index.setdefault("source_index_dir", str(match_index_path.parent if match_index_path.is_file() else match_index_path))
            return split_index, background_path, split_assets, BACKGROUND_REUSE_INDEX_GROUP
    route_group = _normalize_binary_reuse_group(target.get("strict_reuse_group"), default=_GENERAL_REUSE_GROUP)
    split_path = library_root / STRICT_REUSE_INDEX_DIRNAME / f"{route_group}.json"
    if not split_path.exists():
        return None
    split_index = _read_existing_db(split_path)
    split_assets = split_index.get("assets")
    if not isinstance(split_assets, list):
        return None
    split_index = dict(split_index)
    split_index.setdefault("source_index_dir", str(match_index_path.parent if match_index_path.is_file() else match_index_path))
    return split_index, split_path, split_assets, route_group


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _dedupe_match_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    ordered = sorted(
        assets,
        key=lambda item: (
            -float(item.get("_quality_score") or 0.0),
            _clean_text(item.get("asset_id")),
        ),
    )
    for asset in ordered:
        bucket_key = _dedupe_bucket_key(asset)
        representatives = buckets.setdefault(bucket_key, [])
        duplicate_of = next(
            (
                representative
                for representative in representatives
                if _are_match_assets_duplicates(asset, representative)
            ),
            None,
        )
        if duplicate_of is None:
            representatives.append(asset)
            continue
        duplicate_ids = duplicate_of.setdefault("duplicate_asset_ids", [])
        duplicate_ids.append(asset["asset_id"])
        duplicate_ids.extend(asset.get("duplicate_asset_ids") or [])
        duplicate_of["duplicate_asset_ids"] = sorted(_dedupe_terms(duplicate_ids))

    deduped = [asset for representatives in buckets.values() for asset in representatives]
    for asset in deduped:
        asset["duplicate_asset_ids"] = sorted(_dedupe_terms(asset.get("duplicate_asset_ids") or []))
    return sorted(deduped, key=lambda item: _clean_text(item.get("asset_id")))


def _dedupe_bucket_key(asset: dict[str, Any]) -> tuple[str, ...]:
    return (
        _clean_text(asset.get("asset_kind")),
        _ratio_orientation(_clean_text(asset.get("aspect_ratio"))),
        _clean_text(asset.get("subject")),
        _clean_text(asset.get("grade_band")),
    )


def _are_match_assets_duplicates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_hash = _clean_text(left.get("_image_sha256"))
    right_hash = _clean_text(right.get("_image_sha256"))
    if left_hash and right_hash and left_hash == right_hash:
        return True
    return _match_asset_similarity(left, right) >= 0.86


def _match_asset_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    if _is_background_asset(left) and _is_background_asset(right):
        left_doc = [left.get("normalized_prompt")]
        right_doc = [right.get("normalized_prompt")]
    else:
        left_doc = [_asset_content_prompt(left)]
        right_doc = [_asset_content_prompt(right)]
    prompt, _hits = _bm25_similarity_with_hits(
        _bm25_tokens_from_values(left_doc),
        _bm25_tokens_from_values(right_doc),
    )
    route, _route_hits = _bm25_similarity_with_hits(
        _bm25_tokens_from_values([_route_match_text(left)]),
        _bm25_tokens_from_values([_route_match_text(right)]),
    )
    return 0.75 * prompt + 0.25 * route


def _match_asset_quality_score(asset: dict[str, Any]) -> float:
    score = 0.0
    if asset.get("_image_sha256"):
        score += 2.0
    if _clean_text(asset.get("content_prompt")):
        score += 1.0
    if _clean_text(asset.get("normalized_prompt")):
        score += 0.8
    if _clean_text(asset.get("context_summary")):
        score += 0.6
    if _background_route_terms(asset):
        score += 0.6
    return score


def _resolve_asset_image_path(root: Path, image_path: Any) -> Path | None:
    text = _clean_text(image_path)
    if not text:
        return None
    path = Path(text)
    return path if path.is_absolute() else root / path


def _preserve_review_fields(asset: dict[str, Any]) -> dict[str, Any]:
    preserved = {key: deepcopy(asset[key]) for key in _METADATA_PASSTHROUGH_FIELDS if key in asset}
    query = _clean_text(asset.get("query"))
    if query:
        preserved["query"] = query
    for key in ("visual_reuse_group", "visual_reuse_confidence", "visual_reuse_reason"):
        if asset.get(key) not in (None, ""):
            preserved[key] = deepcopy(asset[key])
    return preserved


def _is_skip_reuse_group(value: Any) -> bool:
    return _normalize_binary_reuse_group(value, default="") == _CONTENT_REUSE_GROUP


def _dedupe_warnings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    warnings: list[str] = []
    for value in values:
        warning = _clean_text(value)
        if not warning or warning in seen:
            continue
        seen.add(warning)
        warnings.append(warning)
    return warnings
