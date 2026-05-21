"""VLM verification and enrichment for generated AI image assets."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

LOGGER = logging.getLogger(__name__)

VLM_SCHEMA_VERSION = 1

VLM_SYSTEM_PROMPT = """你是一个素材库的图像验证与增强器。给定一张教学插图和它的 metadata，只返回严格 JSON。

任务：
1. 验证 content_prompt 是否与画面真实内容一致，并把 content_prompt 原文作为 constraint_verification 的第一项。
2. 若 metadata.constraints 非空，再逐条验证 constraints[].value 是否在画面里真实存在。
3. theme、subject、grade、grade_band、page_type、context_summary、teaching_intent、topic_refs 只作为上下文参考，不得作为 constraint_verification 项，也不得因为画面未体现课程标题而判 false。
4. 列出画面里明显存在、但 metadata 没覆盖的实体、物体或动作。
5. 为每个 core_keyword 给出 1-3 个画面可见的视觉别名。
6. 提取主色、构图和背景类型。
7. 给出 content_prompt 与图像一致性评分 0-1；低于 0.5 时 needs_regeneration=true。

输出 schema:
{
  "constraint_verification": [{"value":"<原值>", "present":true, "evidence":"<画面证据>"}],
  "missing_from_metadata": [{"kind":"entity|object|action", "value":"...", "importance_hint":0}],
  "visual_aliases": {"<core_keyword>": ["alias1"]},
  "visual_style": {
    "dominant_colors": ["#RRGGBB"],
    "composition": "centered|grid|diagonal|asymmetric|...",
    "background_type": "gradient|flat|textured|scene|abstract|..."
  },
  "match_quality_score": 0.0,
  "needs_regeneration": false
}"""

_METADATA_FIELDS = (
    "asset_id",
    "asset_kind",
    "image_path",
    "aspect_ratio",
    "role",
    "page_type",
    "theme",
    "subject",
    "grade_norm",
    "grade_band",
    "content_prompt",
    "context_summary",
    "teaching_intent",
    "asset_category",
    "constraints",
    "core_keywords",
    "semantic_aliases",
    "context_summary_keywords",
)


def enrich_assets_with_vlm(
    db: dict[str, Any],
    vlm_client: Any,
    *,
    skip_verified: bool = True,
    batch_size: int = 1,
    image_root: Path | None = None,
    asset_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Run VLM verification/enrichment for page_image assets in an asset DB.

    The function appends only ``vlm_*`` fields to assets. It does not alter the
    existing text-LLM metadata or reuse policy fields.
    """

    root = _resolve_image_root(db, image_root)
    requested_ids = {_clean_text(item) for item in (asset_ids or []) if _clean_text(item)}
    seen_requested_ids: set[str] = set()
    raw_assets = db.get("assets")
    assets = raw_assets if isinstance(raw_assets, list) else []
    model = _clean_text(getattr(vlm_client, "_model", ""))
    report: dict[str, Any] = {
        "asset_count": len(assets),
        "processed_count": 0,
        "failed_count": 0,
        "skipped_verified_count": 0,
        "skipped_non_page_image_count": 0,
        "missing_image_count": 0,
        "processed_asset_ids": [],
        "failed_asset_ids": [],
    }
    if requested_ids:
        report["requested_asset_ids"] = sorted(requested_ids)
    if batch_size != 1:
        LOGGER.info("vlm_batch_size_ignored batch_size=%s reason=single_image_calls", batch_size)

    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_id = _clean_text(asset.get("asset_id"))
        if requested_ids:
            if asset_id in requested_ids:
                seen_requested_ids.add(asset_id)
            else:
                continue

        if _clean_text(asset.get("asset_kind")) != "page_image":
            report["skipped_non_page_image_count"] += 1
            continue
        if skip_verified and bool(asset.get("vlm_verified")):
            report["skipped_verified_count"] += 1
            continue

        image_path = _resolve_image_path(asset, root)
        if image_path is None or not image_path.exists():
            LOGGER.warning("vlm_skip_missing_image asset_id=%s", asset_id)
            report["missing_image_count"] += 1
            continue

        messages = _build_vlm_messages(asset, image_path)
        try:
            response = vlm_client.chat_vlm_json(messages=messages)
            _apply_vlm_payload(asset, response, model=model)
        except Exception as exc:
            LOGGER.warning("vlm_call_failed asset_id=%s err=%s", asset_id, exc)
            report["failed_count"] += 1
            report["failed_asset_ids"].append(asset_id)
            continue

        report["processed_count"] += 1
        report["processed_asset_ids"].append(asset_id)

    if requested_ids:
        report["missing_asset_ids"] = sorted(requested_ids - seen_requested_ids)
    if report["processed_count"]:
        db["vlm_enriched_at"] = datetime.now(timezone.utc).isoformat()
        db["vlm_builder"] = {
            "method": "vlm_asset_verification",
            "schema_version": VLM_SCHEMA_VERSION,
            "model": model,
        }
    return report


def _resolve_image_root(db: dict[str, Any], image_root: Path | None) -> Path:
    if image_root is not None:
        return Path(image_root).expanduser().resolve()
    output_root = _clean_text(db.get("output_root"))
    return Path(output_root or ".").expanduser().resolve()


def _resolve_image_path(asset: dict[str, Any], image_root: Path) -> Path | None:
    image_path = _clean_text(asset.get("image_path"))
    if not image_path:
        return None
    path = Path(image_path)
    return path if path.is_absolute() else image_root / path


def _build_vlm_messages(asset: dict[str, Any], image_path: Path) -> list[dict[str, Any]]:
    metadata = _asset_metadata_for_vlm(asset)
    return [
        {"role": "system", "content": VLM_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "metadata JSON:\n"
                    + json.dumps(metadata, ensure_ascii=False, indent=2),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": _image_data_url(image_path)},
                },
            ],
        },
    ]


def _asset_metadata_for_vlm(asset: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for field in _METADATA_FIELDS:
        value = asset.get(field)
        if value in (None, "", [], {}):
            continue
        metadata[field] = value
    return metadata


def _image_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _apply_vlm_payload(
    asset: dict[str, Any],
    payload: dict[str, Any],
    *,
    model: str = "",
) -> None:
    if not isinstance(payload, dict):
        raise ValueError("VLM payload must be a JSON object")

    score = _to_score(payload.get("match_quality_score", payload.get("vlm_match_quality")))
    needs_default = bool(score is not None and score < 0.5)
    asset["vlm_schema_version"] = VLM_SCHEMA_VERSION
    asset["vlm_verified_constraints"] = _normalize_constraint_verification(
        payload.get("constraint_verification", payload.get("vlm_verified_constraints"))
    )
    asset["vlm_missing_from_prompt"] = _normalize_missing_items(
        payload.get("missing_from_metadata", payload.get("vlm_missing_from_prompt"))
    )
    asset["vlm_visual_aliases"] = _normalize_visual_aliases(
        payload.get("visual_aliases", payload.get("vlm_visual_aliases"))
    )
    asset["vlm_visual_style"] = _normalize_visual_style(
        payload.get("visual_style", payload.get("vlm_visual_style"))
    )
    asset["vlm_match_quality"] = round(score if score is not None else 0.0, 4)
    asset["vlm_needs_regeneration"] = _to_bool(
        payload.get("needs_regeneration", payload.get("vlm_needs_regeneration")),
        default=needs_default,
    )
    asset["vlm_verified"] = True
    asset["vlm_verified_at"] = datetime.now(timezone.utc).isoformat()
    if model:
        asset["vlm_model"] = model


def _normalize_constraint_verification(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        original = _clean_text(item.get("value"))
        if not original:
            continue
        normalized: dict[str, Any] = {
            "value": original,
            "present": _to_bool(item.get("present"), default=False),
        }
        evidence = _clean_text(item.get("evidence"))
        if evidence:
            normalized["evidence"] = evidence
        kind = _clean_text(item.get("kind"))
        if kind:
            normalized["kind"] = kind
        result.append(normalized)
    return result


def _normalize_missing_items(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = _clean_text(item.get("value"))
        if not text:
            continue
        kind = _clean_text(item.get("kind")) or "object"
        if kind not in {"entity", "object", "action"}:
            kind = "object"
        result.append(
            {
                "kind": kind,
                "value": text,
                "importance_hint": _clamp_int(item.get("importance_hint"), 0, 2),
            }
        )
    return result


def _normalize_visual_aliases(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[str]] = {}
    for raw_key, raw_values in value.items():
        key = _clean_text(raw_key)
        if not key:
            continue
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        aliases = _dedupe_texts(values, max_items=3)
        if aliases:
            result[key] = aliases
    return result


def _normalize_visual_style(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    style: dict[str, Any] = {}
    colors = _dedupe_texts(value.get("dominant_colors"), max_items=3)
    if colors:
        style["dominant_colors"] = [_normalize_hex_color(item) for item in colors]
    composition = _clean_text(value.get("composition"))
    if composition:
        style["composition"] = composition
    background_type = _clean_text(value.get("background_type"))
    if background_type:
        style["background_type"] = background_type
    return style


def _dedupe_texts(value: Any, *, max_items: int) -> list[str]:
    raw_items = value if isinstance(value, list) else [value]
    seen: set[str] = set()
    result: list[str] = []
    for item in raw_items:
        text = _clean_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= max_items:
            break
    return result


def _normalize_hex_color(value: str) -> str:
    text = _clean_text(value)
    if len(text) == 7 and text.startswith("#"):
        return text.upper()
    return text


def _to_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = _clean_text(value).casefold()
    if text in {"true", "yes", "y", "1"}:
        return True
    if text in {"false", "no", "n", "0"}:
        return False
    return default


def _to_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, score))


def _clamp_int(value: Any, min_value: int, max_value: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = min_value
    return max(min_value, min(max_value, number))


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())
