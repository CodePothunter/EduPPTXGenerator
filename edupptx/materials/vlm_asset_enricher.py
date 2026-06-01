"""VLM review and metadata repair for generated AI image assets."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from edupptx.materials.strict_reuse_classifier import (
    MATERIAL_CATEGORY_RULES_TEXT,
    MATERIAL_CATEGORIES,
    STRICT_REUSE_GROUPS,
    normalize_strict_reuse_group,
)

# padding_capacity is intentionally NOT computed here. It's a pixel-derived
# field the VLM never sees; it lives on the asset top-level and is set at the
# earliest moment image_path exists (annotation time for library builds,
# registration time at runtime). See vlm_metadata_rules.normalize_padding_capacity.

LOGGER = logging.getLogger(__name__)

VLM_SCHEMA_VERSION = 7
VLM_REVIEW_INDEX_FILENAME = "ai_image_vlm_review.json"
VLM_DEBUG_DIRNAME = "debug"
VLM_REVIEW_QUEUE_FILENAME = "vlm_review_queue.jsonl"
ABSENT_IMPORTANCE_CONFIDENCE_THRESHOLD = 0.8
AUTO_REWRITE_MATCH_QUALITY_THRESHOLD = 0.4
LOW_REVIEW_MATCH_QUALITY_THRESHOLD = 0.5
AUTO_ACCEPT_MATCH_QUALITY_THRESHOLD = 0.75
POSSIBLE_VISUAL_MISREAD_REVIEW_UPPER_THRESHOLD = 0.75
STRICT_REUSE_VLM_CORRECTION_CONFIDENCE_THRESHOLD = 0.75

VLM_SYSTEM_PROMPT = """你是一个素材库的图像验证器。给定一张教学插图和它的元数据，只返回严格 JSON。

任务：
1. 验证 content_prompt 是否能被画面合理支持，并把 content_prompt 原文作为 constraint_verification 的第一项。
2. 若元数据中的 constraints 非空，再逐条验证 constraints[].value 是否在画面里真实存在。
3. context_summary 与 teaching_intent 只作为弱上下文参考，不得作为 constraint_verification 项；不得因为图片没有体现教学用途、页面功能或课程语境而判 false。
4. 列出画面里明显存在、但元数据没覆盖的实体、物体或动作。
5. 给出 content_prompt 与图像一致性评分 0-1；低于 0.5 时 needs_regeneration=true。

重要原则：
- 不要生成 core_keywords、semantic_aliases、query_aliases 或任何同义词。
- 不要把抽象、低龄卡通、画得不够好但仍可合理表达的主体直接改判为其他实体。
- present 使用三态："present"、"absent"、"uncertain"。只有高置信明确不存在或明显错误时才用 "absent"。
- 当元数据指向某个对象，但画面因抽象、变形、低质量、局部遮挡或风格化而可能被误读为视觉相近对象时，应输出 present="uncertain" 并写 possible_misread_as；不要直接把原对象改判为相近对象。
- kind=text 或 subtype=teaching_content 的文字约束，只有画面中真的出现对应字词时才能 present；仅有图像语义对应时必须输出 uncertain 或 absent。

输出结构：
{
  "constraint_verification": [{
    "value":"<原值>",
    "present":"present|absent|uncertain",
    "confidence":0.0,
    "evidence":"<画面证据>",
    "possible_misread_as":["..."]
  }],
  "missing_from_metadata": [{"kind":"entity|object|action", "value":"...", "importance_hint":0}],
  "match_quality_score": 0.0,
  "needs_regeneration": false
}"""

VLM_SYSTEM_PROMPT += """

额外素材分类判断：
- 同时判断图片应归入哪个素材分类，输出 visual_reuse_group 为以下 7 个类别 ID 之一。
""" + MATERIAL_CATEGORY_RULES_TEXT + """
- visual_reuse_confidence 表示你对该分类判断的置信度，0-1。
- visual_reuse_reason 格式："属于<类别中文名>：<画面主体描述>"。
- 输出 JSON 需要额外包含：
  "visual_reuse_group": "<C00-C06 类别ID>",
  "visual_reuse_confidence": 0.0,
  "visual_reuse_reason": "..."
"""

VLM_REDESCRIBE_SYSTEM_PROMPT = """你是教学课件素材库的图片标注助手。给定一张图片和原始元数据，只根据图片实际内容重新生成可复用语义描述，只返回严格 JSON。

输出 JSON 结构：
{
  "content_prompt": "短中文图片需求，只描述图片本体",
  "detail_prompt": "完整的视觉细节描述，保留布局、控件、装饰、配色等",
  "context_summary": "一句 20-40 个汉字的短句，描述画面内容和页面功能",
  "teaching_intent": "该图服务的教学动作或学习目标",
  "general": false
}

规则：
1. content_prompt 只回答“这张图实际是什么”，写成可直接检索图片的短中文名词短语，长度不超过 30 个汉字。
2. 不要沿用原始元数据中被图片否定的对象、文字、数量、动作或教学事实。
3. 如果图片是空白卡片、空白边框、空白举牌等可叠字素材，content_prompt 必须显式写“空白”或“不含具体文字”。
4. detail_prompt 记录主体外观、数量、布局、背景、装饰、配色、构图和可见文字；不得虚构图片里没有的文字。
5. context_summary 写“画面内容 + 页面功能”，不要只写页面类型。
6. teaching_intent 写该图按真实画面可支持的教学动作或学习目标。
7. general 必须是布尔值，表示当前图片本身是否可跨语文、数学、物理通用复用。只有明确不依赖具体学科、固定文字、固定数字、精确图形关系、课文故事、文化身份或科学实验结构时才输出 true；模糊时输出 false。
8. 不输出 core_keywords、semantic_aliases、query_aliases、constraints 或结构之外字段。"""

VLM_REDESCRIBE_SYSTEM_PROMPT = """你是教学课件素材库的图片标注助手。给定一张图片和原始元数据，只根据图片实际内容写出可重新生成该图的完整 query，只返回严格 JSON。

query 规则：
- query 是"若要重新生成这张图，会怎么写生成 prompt"——保留画面所有影响复用的本体信息：主体、动作/事件/关系、图示类型与主题。
- 必须保留所有可见的文字、汉字、拼音、数字、公式、标注、题干，以及主体之间的数量、顺序、对应、因果、空间或比较关系（这些是分类的依据，不得省略）。
- 不写用途、页面功能、教学目标、来源课程/年级/学科等上下文。
- 若为空白卡片/边框/底图等可叠字素材，query 必须显式写"空白"或"不含具体文字"。

输出 JSON 结构：
{
  "query": "可重新生成该图的完整中文 prompt，保留全部可见文字/数值/标注/图形关系",
  "context_summary": "一句 20-40 个汉字的短句，描述画面内容和页面功能",
  "teaching_intent": "该图服务的教学动作或学习目标",
  "general": false
}

规则：
1. 只输出上述四个字段；不输出 caption、strict_reuse_group、core_keywords、semantic_aliases、query_aliases、constraints 或结构之外字段。
2. 不要沿用原始元数据中被图片否定的对象、文字、数量、动作或教学事实。
3. general 必须是布尔值，表示当前图片本身是否可跨语文、数学、物理通用复用；模糊时输出 false。"""

_METADATA_FIELDS = (
    "query",
    "context_summary",
    "teaching_intent",
    "strict_reuse_group",
    "strict_reuse_confidence",
)

_REBUILDABLE_METADATA_FIELDS = (
    "match_text",
    "match_key",
    "normalized_prompt",
)


def enrich_assets_with_vlm(
    db: dict[str, Any],
    vlm_client: Any,
    *,
    skip_reviewed: bool = True,
    batch_size: int = 1,
    image_root: Path | None = None,
    asset_ids: Iterable[str] | None = None,
    debug_dir: Path | None = None,
    review_index_path: Path | None = None,
    keyword_client: Any | None = None,
    keyword_batch_size: int = 12,
) -> dict[str, Any]:
    """Run VLM review for page_image assets.

    The asset records keep only ``vlm_match_quality``. Full VLM judgement is
    persisted to ``ai_image_vlm_review.json``. ``padding_capacity`` is set
    elsewhere (annotation / registration) and is not touched by this step.
    """

    root = _resolve_image_root(db, image_root)
    review_queue_path = _resolve_debug_review_path(root, debug_dir)
    review_path = _resolve_review_index_path(root, review_index_path)
    review_index = _read_review_index(review_path)
    review_assets = _dict(review_index.get("assets"))
    reviewed_ids = set(review_assets.keys())
    requested_ids = {_clean_text(item) for item in (asset_ids or []) if _clean_text(item)}
    seen_requested_ids: set[str] = set()
    raw_assets = db.get("assets")
    assets = raw_assets if isinstance(raw_assets, list) else []
    model = _clean_text(getattr(vlm_client, "_model", ""))
    report: dict[str, Any] = {
        "asset_count": len(assets),
        "processed_count": 0,
        "failed_count": 0,
        "skipped_reviewed_count": 0,
        "skipped_non_page_image_count": 0,
        "missing_image_count": 0,
        "auto_rewrite_count": 0,
        "manual_review_count": 0,
        "accepted_count": 0,
        "keyword_rewrite_count": 0,
        "processed_asset_ids": [],
        "auto_rewrite_asset_ids": [],
        "manual_review_asset_ids": [],
        "failed_asset_ids": [],
        "warnings": [],
        "review_index_path": str(review_path),
    }
    if requested_ids:
        report["requested_asset_ids"] = sorted(requested_ids)
    if batch_size != 1:
        LOGGER.info("vlm_batch_size_ignored batch_size=%s reason=single_image_calls", batch_size)

    rewritten_assets: list[dict[str, Any]] = []

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
        if skip_reviewed and asset_id in reviewed_ids:
            report["skipped_reviewed_count"] += 1
            continue

        image_path = _resolve_image_path(asset, root)
        if image_path is None or not image_path.exists():
            LOGGER.warning("vlm_skip_missing_image asset_id=%s", asset_id)
            report["missing_image_count"] += 1
            continue

        try:
            response = vlm_client.chat_vlm_json(
                messages=_build_vlm_messages(asset, image_path),
                temperature=0.1,
                max_tokens=4096,
            )
            review_record = _build_review_record(
                asset,
                response,
                model=model,
                image_path=image_path,
                image_root=root,
            )
            _apply_review_to_asset(asset, review_record)
            if review_record["action"] == "auto_rewrite":
                rewrite_payload = vlm_client.chat_vlm_json(
                    messages=_build_redescribe_messages(asset, image_path),
                    temperature=0.1,
                    max_tokens=4096,
                )
                _apply_redescription(asset, rewrite_payload)
                _clear_rebuildable_metadata(asset)
                asset["regenerate"] = True
                review_record["regenerate"] = True
                review_record["rewritten_metadata"] = _rewritten_metadata_snapshot(asset)
                rewritten_assets.append(asset)
                report["auto_rewrite_count"] += 1
                report["auto_rewrite_asset_ids"].append(asset_id)
            elif review_record["manual_review_required"]:
                report["manual_review_count"] += 1
                report["manual_review_asset_ids"].append(asset_id)
                debug_record = _build_debug_review_record(review_record)
                if debug_record:
                    _append_debug_review_record(review_queue_path, debug_record)
                    report["debug_path"] = str(review_queue_path)
            else:
                report["accepted_count"] += 1
        except Exception as exc:
            LOGGER.warning("vlm_call_failed asset_id=%s err=%s", asset_id, exc)
            report["failed_count"] += 1
            report["failed_asset_ids"].append(asset_id)
            continue

        review_assets[asset_id] = review_record
        report["processed_count"] += 1
        report["processed_asset_ids"].append(asset_id)

    if rewritten_assets and keyword_client is not None:
        _summarize_captions_for_assets(rewritten_assets, keyword_client)
        _rewrite_keywords_for_assets(
            rewritten_assets,
            keyword_client=keyword_client,
            keyword_batch_size=keyword_batch_size,
        )
        report["keyword_rewrite_count"] = len(rewritten_assets)
    elif rewritten_assets:
        report["warnings"].append("low_quality_metadata_rewritten_without_keyword_llm")

    if report["processed_count"]:
        now = datetime.now(timezone.utc).isoformat()
        review_index = {
            "schema_version": VLM_SCHEMA_VERSION,
            "updated_at": now,
            "asset_root": str(root),
            "model": model,
            "reviewed_asset_count": len(review_assets),
            "assets": dict(sorted(review_assets.items())),
        }
        _write_review_index(review_path, review_index)
        db["vlm_reviewed_at"] = now
        db["vlm_review"] = {
            "schema_version": VLM_SCHEMA_VERSION,
            "review_index_path": str(review_path),
            "processed_count": report["processed_count"],
            "model": model,
        }

    if requested_ids:
        report["missing_asset_ids"] = sorted(requested_ids - seen_requested_ids)
    return report


def _resolve_debug_review_path(root: Path, debug_dir: Path | None) -> Path:
    base = Path(debug_dir).expanduser().resolve() if debug_dir is not None else root / VLM_DEBUG_DIRNAME
    return base / VLM_REVIEW_QUEUE_FILENAME


def _resolve_review_index_path(root: Path, review_index_path: Path | None) -> Path:
    if review_index_path is not None:
        return Path(review_index_path).expanduser().resolve()
    return root / VLM_REVIEW_INDEX_FILENAME


def _read_review_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": VLM_SCHEMA_VERSION, "assets": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        LOGGER.warning("vlm_review_index_unreadable path=%s", path)
        return {"schema_version": VLM_SCHEMA_VERSION, "assets": {}}
    return data if isinstance(data, dict) else {"schema_version": VLM_SCHEMA_VERSION, "assets": {}}


def _write_review_index(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
                    "text": "元数据 JSON:\n"
                    + json.dumps(metadata, ensure_ascii=False, indent=2),
                },
                {"type": "image_url", "image_url": {"url": _image_data_url(image_path)}},
            ],
        },
    ]


def _build_redescribe_messages(asset: dict[str, Any], image_path: Path) -> list[dict[str, Any]]:
    metadata = _asset_metadata_for_vlm(asset)
    return [
        {"role": "system", "content": VLM_REDESCRIBE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "请按图片真实内容重新标注。原始元数据 JSON:\n"
                    + json.dumps(metadata, ensure_ascii=False, indent=2),
                },
                {"type": "image_url", "image_url": {"url": _image_data_url(image_path)}},
            ],
        },
    ]


def _asset_metadata_for_vlm(asset: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for field in _METADATA_FIELDS:
        value = _asset_query(asset) if field == "query" else asset.get(field)
        if value in (None, "", [], {}):
            continue
        metadata[field] = value
    return metadata


def _evaluate_vlm_strict_reuse_group(asset: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    metadata_group = _optional_strict_reuse_group(
        asset.get("strict_reuse_group") or asset.get("reuse_group")
    )
    visual_group = _optional_strict_reuse_group(payload.get("visual_reuse_group"))
    visual_confidence = _to_score(payload.get("visual_reuse_confidence"))
    visual_confidence_value = round(visual_confidence if visual_confidence is not None else 0.0, 4)
    visual_reason = _clean_text(payload.get("visual_reuse_reason"))

    mismatch = bool(metadata_group and visual_group and metadata_group != visual_group)
    missing_metadata_group = bool(not metadata_group and visual_group)
    update_group = ""
    manual_reason = ""
    risk_reason = ""
    auto_corrected = False
    if mismatch or missing_metadata_group:
        risk_reason = "strict_reuse_group_mismatch" if mismatch else "missing_strict_reuse_group"
        if (
            visual_confidence is not None
            and visual_confidence > STRICT_REUSE_VLM_CORRECTION_CONFIDENCE_THRESHOLD
        ):
            update_group = visual_group
            auto_corrected = True
        else:
            manual_reason = risk_reason

    return {
        "llm_reuse_group": metadata_group,
        "visual_reuse_group": visual_group,
        "visual_reuse_confidence": visual_confidence_value,
        "visual_reuse_reason": visual_reason,
        "strict_reuse_group_mismatch": mismatch,
        "strict_reuse_group_missing": missing_metadata_group,
        "strict_reuse_group_update": update_group,
        "strict_reuse_auto_corrected": auto_corrected,
        "strict_reuse_group_risk_reason": risk_reason,
        "strict_reuse_group_manual_review_reason": manual_reason,
    }


def _optional_strict_reuse_group(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    group = normalize_strict_reuse_group(text, default="")
    return group if group in MATERIAL_CATEGORIES else ""


def _image_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _build_review_record(
    asset: dict[str, Any],
    payload: dict[str, Any],
    *,
    model: str,
    image_path: Path,
    image_root: Path,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("VLM payload must be a JSON object")

    score = _to_score(payload.get("match_quality_score"))
    score_value = round(score if score is not None else 0.0, 4)
    needs_default = bool(score is not None and score < LOW_REVIEW_MATCH_QUALITY_THRESHOLD)
    constraint_visibility = _normalize_constraint_visibility(payload.get("constraint_verification"))
    effective_constraints = _build_effective_constraints(asset.get("constraints"), constraint_visibility)
    needs_regeneration = _to_bool(payload.get("needs_regeneration"), default=needs_default)
    risk_reasons = _derive_risk_reasons(
        score=score,
        needs_regeneration=needs_regeneration,
        effective_constraints=effective_constraints,
        visibility=constraint_visibility,
    )
    manual_reasons = _derive_manual_review_reasons(
        score=score_value,
        needs_regeneration=needs_regeneration,
        risk_reasons=risk_reasons,
    )
    strict_reuse_review = _evaluate_vlm_strict_reuse_group(asset, payload)
    reuse_group_risk_reason = _clean_text(strict_reuse_review.get("strict_reuse_group_risk_reason"))
    if reuse_group_risk_reason:
        risk_reasons = _dedupe_texts([*risk_reasons, reuse_group_risk_reason], max_items=8)
    reuse_group_manual_reason = _clean_text(
        strict_reuse_review.get("strict_reuse_group_manual_review_reason")
    )
    if score_value >= AUTO_REWRITE_MATCH_QUALITY_THRESHOLD and reuse_group_manual_reason:
        manual_reasons = _dedupe_texts([*manual_reasons, reuse_group_manual_reason], max_items=8)
    action = _review_action(score_value, manual_reasons)
    try:
        relative_image_path = str(image_path.relative_to(image_root))
    except ValueError:
        relative_image_path = str(image_path)
    return {
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "asset_id": _clean_text(asset.get("asset_id")),
        "image_path": relative_image_path,
        "caption": _asset_caption(asset),
        "model": model,
        "vlm_match_quality": score_value,
        "vlm_needs_regeneration": needs_regeneration,
        "action": action,
        "manual_review_required": bool(manual_reasons),
        "manual_review_reasons": manual_reasons,
        "risk_reasons": risk_reasons,
        "constraint_visibility": constraint_visibility,
        "effective_constraints": effective_constraints,
        "missing_from_metadata": _normalize_missing_items(payload.get("missing_from_metadata")),
        "possible_misreads": _possible_misreads_from_visibility(constraint_visibility),
        **strict_reuse_review,
        "failed_constraints": [
            item
            for item in effective_constraints
            if _clean_text(item.get("vlm_presence")) == "absent"
            and _clamp_int(item.get("original_importance"), 0, 2) >= 1
        ],
        "uncertain_constraints": [
            item
            for item in effective_constraints
            if _clean_text(item.get("vlm_presence")) == "uncertain"
            and _clamp_int(item.get("original_importance"), 0, 2) >= 1
        ],
        "regenerate": False,
    }


def _apply_review_to_asset(asset: dict[str, Any], review_record: dict[str, Any]) -> None:
    asset["vlm_match_quality"] = review_record["vlm_match_quality"]
    asset.pop("strict_reuse_requires_exact_match", None)
    update_group = _optional_strict_reuse_group(review_record.get("strict_reuse_group_update"))
    if update_group:
        asset["strict_reuse_group"] = update_group
        confidence = _to_score(review_record.get("visual_reuse_confidence"))
        if confidence is not None:
            asset["strict_reuse_confidence"] = round(confidence, 4)
        reason = _clean_text(review_record.get("visual_reuse_reason"))
        asset["strict_reuse_reason"] = reason or "VLM review corrected reuse group"


def _apply_redescription(asset: dict[str, Any], payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ValueError("VLM redescription payload must be a JSON object")
    query = _clean_text(payload.get("query"))
    if query:
        asset["query"] = query
    asset.pop("caption", None)
    asset.pop("content_prompt", None)
    asset.pop("detail_prompt", None)
    context_summary = _clean_text(payload.get("context_summary"))
    if context_summary:
        asset["context_summary"] = context_summary
    teaching_intent = _clean_text(payload.get("teaching_intent"))
    if teaching_intent:
        asset["teaching_intent"] = teaching_intent
    general = _optional_bool(payload.get("general"))
    if general is not None:
        asset["general"] = general


def _clear_rebuildable_metadata(asset: dict[str, Any]) -> None:
    for field in _REBUILDABLE_METADATA_FIELDS:
        asset.pop(field, None)


def _rewritten_metadata_snapshot(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "query": _asset_query(asset),
        "context_summary": _clean_text(asset.get("context_summary")),
        "teaching_intent": _clean_text(asset.get("teaching_intent")),
    }


def _summarize_captions_for_assets(assets: list[dict[str, Any]], client: Any) -> None:
    """Fill caption from query via the shared CAPTION_RULE summarizer."""
    from edupptx.materials.caption_rules import summarize_records

    targets = [a for a in assets if not _clean_text(a.get("caption")) and _asset_query(a)]
    if not targets:
        return
    records = [{"query": _asset_query(a)} for a in targets]
    try:
        summarized = summarize_records(records, client, query_field="query", caption_field="caption")
    except Exception as exc:
        LOGGER.warning("vlm_caption_summarize_failed err=%s", exc)
        for asset in targets:
            asset["caption"] = _asset_query(asset)
        return
    for asset, item in zip(targets, summarized):
        asset["caption"] = _clean_text(item.get("caption")) or _asset_query(asset)


def _rewrite_keywords_for_assets(
    assets: list[dict[str, Any]],
    *,
    keyword_client: Any,
    keyword_batch_size: int,
) -> None:
    from edupptx.materials.ai_image_asset_db import enrich_ai_image_asset_db_keywords

    temp_db = {
        "schema_version": VLM_SCHEMA_VERSION,
        "assets": assets,
        "warnings": [],
    }
    enrich_ai_image_asset_db_keywords(
        temp_db,
        keyword_client,
        batch_size=keyword_batch_size,
        preserve_existing_context_fields=True,
    )


def _normalize_constraint_visibility(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        original = _clean_text(item.get("value"))
        if not original:
            continue
        presence = _normalize_presence(item.get("present"))
        confidence = _presence_confidence(item, presence)
        normalized: dict[str, Any] = {
            "value": original,
            "presence": presence,
            "confidence": round(confidence, 4),
        }
        evidence = _clean_text(item.get("evidence"))
        if evidence:
            normalized["evidence"] = evidence
        kind = _clean_text(item.get("kind"))
        if kind:
            normalized["kind"] = kind
        possible_misreads = _dedupe_texts(item.get("possible_misread_as"), max_items=3)
        if possible_misreads:
            normalized["possible_misread_as"] = possible_misreads
        result.append(normalized)
    return result


def _normalize_presence(value: Any) -> str:
    text = _clean_text(value).casefold()
    if text == "present":
        return "present"
    if text == "absent":
        return "absent"
    return "uncertain"


def _presence_confidence(item: dict[str, Any], presence: str) -> float:
    score = _to_score(item.get("confidence", item.get("score")))
    if score is not None:
        return score
    if presence == "uncertain":
        return 0.5
    return 1.0


def _build_effective_constraints(
    constraints: Any,
    visibility: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_constraints = constraints if isinstance(constraints, list) else []
    visibility_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    visibility_by_value: dict[str, dict[str, Any]] = {}
    for item in visibility:
        value = _clean_text(item.get("value"))
        kind = _clean_text(item.get("kind"))
        if not value:
            continue
        visibility_by_value.setdefault(value.casefold(), item)
        if kind:
            visibility_by_key.setdefault((kind.casefold(), value.casefold()), item)

    result: list[dict[str, Any]] = []
    for raw in raw_constraints:
        if not isinstance(raw, dict):
            continue
        value = _clean_text(raw.get("value"))
        kind = _clean_text(raw.get("kind"))
        original_importance = _clamp_int(raw.get("importance"), 0, 2)
        visibility_item = visibility_by_key.get((kind.casefold(), value.casefold()))
        if visibility_item is None:
            visibility_item = visibility_by_value.get(value.casefold())

        presence = _clean_text(_dict(visibility_item).get("presence")) or "unverified"
        confidence = _to_score(_dict(visibility_item).get("confidence"))
        confidence = 0.0 if confidence is None else confidence
        effective_importance = original_importance
        if presence == "absent" and confidence >= ABSENT_IMPORTANCE_CONFIDENCE_THRESHOLD:
            effective_importance = 0
        elif presence == "uncertain" and original_importance >= 2:
            effective_importance = 1

        item = dict(raw)
        item["original_importance"] = original_importance
        item["effective_importance"] = effective_importance
        item["vlm_presence"] = presence
        item["vlm_confidence"] = round(confidence, 4)
        evidence = _clean_text(_dict(visibility_item).get("evidence"))
        if evidence:
            item["vlm_evidence"] = evidence
        possible_misreads = _as_string_list(_dict(visibility_item).get("possible_misread_as"))
        if possible_misreads:
            item["vlm_possible_misread_as"] = possible_misreads
        result.append(item)
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


def _derive_risk_reasons(
    *,
    score: float | None,
    needs_regeneration: bool,
    effective_constraints: list[dict[str, Any]],
    visibility: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    if score is not None and score < LOW_REVIEW_MATCH_QUALITY_THRESHOLD:
        reasons.append("low_match_quality")
    if needs_regeneration:
        reasons.append("needs_regeneration")
    for item in effective_constraints:
        original_importance = _clamp_int(item.get("original_importance"), 0, 2)
        effective_importance = _clamp_int(item.get("effective_importance"), 0, 2)
        presence = _clean_text(item.get("vlm_presence"))
        if original_importance >= 2 and presence == "absent" and effective_importance == 0:
            reasons.append("strong_constraint_absent")
        if original_importance >= 2 and presence == "uncertain":
            reasons.append("strong_constraint_uncertain")
        if item.get("vlm_possible_misread_as"):
            reasons.append("possible_visual_misread")
    if any(item.get("possible_misread_as") for item in visibility):
        reasons.append("possible_visual_misread")
    return _dedupe_texts(reasons, max_items=8)


def _derive_manual_review_reasons(
    *,
    score: float,
    needs_regeneration: bool,
    risk_reasons: list[str],
) -> list[str]:
    if score < AUTO_REWRITE_MATCH_QUALITY_THRESHOLD:
        return []
    if score >= AUTO_ACCEPT_MATCH_QUALITY_THRESHOLD:
        return []
    if score < LOW_REVIEW_MATCH_QUALITY_THRESHOLD:
        return ["low_match_quality"]

    reasons: list[str] = []
    if needs_regeneration and score >= LOW_REVIEW_MATCH_QUALITY_THRESHOLD:
        reasons.append("needs_regeneration")
    if score > LOW_REVIEW_MATCH_QUALITY_THRESHOLD:
        for reason in ("strong_constraint_absent", "strong_constraint_uncertain"):
            if reason in risk_reasons:
                reasons.append(reason)
    if (
        not reasons
        and "possible_visual_misread" in risk_reasons
        and LOW_REVIEW_MATCH_QUALITY_THRESHOLD <= score < POSSIBLE_VISUAL_MISREAD_REVIEW_UPPER_THRESHOLD
    ):
        reasons.append("possible_visual_misread")
    return _dedupe_texts(reasons, max_items=8)


def _review_action(score: float, manual_reasons: list[str]) -> str:
    if score < AUTO_REWRITE_MATCH_QUALITY_THRESHOLD:
        return "auto_rewrite"
    if manual_reasons:
        return "manual_review"
    return "accept"


def _possible_misreads_from_visibility(visibility: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in visibility:
        misreads = _as_string_list(item.get("possible_misread_as"))
        if not misreads:
            continue
        result.append(
            {
                "value": item.get("value"),
                "possible_misread_as": misreads,
                "presence": item.get("presence"),
                "confidence": item.get("confidence"),
            }
        )
    return result


def _build_debug_review_record(review_record: dict[str, Any]) -> dict[str, Any] | None:
    if not bool(review_record.get("manual_review_required")):
        return None
    reasons = _as_string_list(review_record.get("manual_review_reasons"))
    if not reasons:
        return None
    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "asset_id": _clean_text(review_record.get("asset_id")),
        "image_path": _clean_text(review_record.get("image_path")),
        "caption": _clean_text(review_record.get("caption")),
        "vlm_match_quality": review_record.get("vlm_match_quality"),
        "reasons": reasons,
        "failed_constraints": review_record.get("failed_constraints") or [],
        "uncertain_constraints": review_record.get("uncertain_constraints") or [],
        "possible_misreads": review_record.get("possible_misreads") or [],
        "llm_reuse_group": _clean_text(review_record.get("llm_reuse_group")),
        "visual_reuse_group": _clean_text(review_record.get("visual_reuse_group")),
        "visual_reuse_confidence": review_record.get("visual_reuse_confidence"),
        "visual_reuse_reason": _clean_text(review_record.get("visual_reuse_reason")),
        "strict_reuse_group_mismatch": bool(review_record.get("strict_reuse_group_mismatch")),
        "review_status": "pending",
    }


def _append_debug_review_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


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


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_clean_text(item) for item in value if _clean_text(item)]
    text = _clean_text(value)
    return [text] if text else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _asset_caption(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("caption")) or _clean_text(asset.get("content_prompt"))


def _asset_query(asset: dict[str, Any]) -> str:
    return (
        _clean_text(asset.get("query"))
        or _clean_text(asset.get("detail_prompt"))
        or _asset_caption(asset)
    )


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None
