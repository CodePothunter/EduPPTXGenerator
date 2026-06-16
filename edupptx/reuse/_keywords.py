"""复用层 target 关键词富化：调 LLM 抽核心词/别名(含规则参考)、批量预热、缓存。函数体逐字一致。"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger as PROGRESS_LOGGER

from edupptx.materials.vlm_metadata_rules import normalize_padding_capacity

# materials/Reference/ 关键词规则参考（随 _keywords 迁出；路径从 reuse/ 上溯到 materials/）。
KEYWORD_REUSE_RULES_REFERENCE = (
    Path(__file__).resolve().parent.parent / "materials" / "Reference" / "ai_image_reuse_metadata_rules.md"
)

from edupptx.reuse._util import (
    _clean_text,
    _client_model_name,
    _dedupe_terms,
)
from edupptx.reuse._constants import (
    DEFAULT_KEYWORD_BATCH_SIZE,
    KEYWORD_SCHEMA_VERSION,
    SCHEMA_VERSION,
    _GENERAL_REUSE_GROUP,
)
from edupptx.reuse._assets import (
    _as_string_list,
    _asset_caption,
    _asset_content_prompt,
    _asset_page_type,
    _asset_query,
    _background_retrieval_text,
    _is_background_asset,
    _normalize_subject_value,
    _optional_bool,
    _page_retrieval_text,
    _topic_refs_for_asset,
    _unit_ref_for_asset,
)
from edupptx.reuse._normalize import (
    _load_json_response,
    _normalize_binary_reuse_group,
    _normalize_grade_band_value,
    _normalize_grade_norm_value,
)
from edupptx.reuse._scoring import (
    _bm25_tokens_from_values,
)
from edupptx.reuse._store import (
    _default_normalized_prompt,
    _default_teaching_intent,
    _fallback_context_summary,
    _match_background_route,
    _match_prompt_route,
    _preserve_review_fields,
)
from edupptx.reuse._review import (
    _log_snippet,
)
from edupptx.reuse._build import (
    _target_keyword_cache_key,
    normalize_grade_info,
)
from edupptx.reuse._debug import (
    _optional_float,
)


def _enrich_reuse_target_keywords_once(
    target: dict[str, Any],
    keyword_client: Any | None,
    target_keyword_cache: dict[str, Any] | None,
) -> dict[str, Any]:
    cache_key = _target_keyword_cache_key(target)
    if target_keyword_cache is not None:
        cached = target_keyword_cache.get(cache_key)
        if isinstance(cached, dict):
            return deepcopy(cached)
    if keyword_client is None:
        return target

    target_db = {"schema_version": SCHEMA_VERSION, "assets": [target], "warnings": []}
    PROGRESS_LOGGER.info(
        "AI image reuse target keywords start: kind={}, prompt={}",
        _clean_text(target.get("asset_kind")) or "unknown",
        _log_snippet(_asset_content_prompt(target), 96),
    )
    enrich_ai_image_asset_db_keywords(
        target_db,
        keyword_client,
        batch_size=1,
        include_match_keywords=True,
    )
    enriched = target_db["assets"][0]
    PROGRESS_LOGGER.info(
        "AI image reuse target metadata done: group={}",
        _clean_text(enriched.get("strict_reuse_group")) or "unknown",
    )
    if target_keyword_cache is not None:
        target_keyword_cache[cache_key] = deepcopy(enriched)
    return enriched


def _reuse_target_keyword_batch_size() -> int:
    raw = os.environ.get("EDUPPTX_REUSE_TARGET_KEYWORD_BATCH_SIZE", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 1


def _reuse_target_keyword_workers() -> int:
    raw = os.environ.get("EDUPPTX_REUSE_TARGET_KEYWORD_WORKERS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 15


def _prewarm_reuse_target_keywords(
    targets: list[dict[str, Any]],
    keyword_client: Any | None,
    target_keyword_cache: dict[str, Any],
    *,
    batch_size: int | None = None,
    max_workers: int | None = None,
    on_batch_cached: Callable[[int, int], None] | None = None,
) -> int:
    """Batch-enrich plan targets so per-slot search can reuse the cached payload.

    Performance design (P5):

    * The pending targets are split into fixed-size batches.
    * Batches are dispatched to a ``ThreadPoolExecutor`` so multiple LLM
      round-trips overlap (each call is I/O bound).
    * Smaller ``batch_size`` (default 6) keeps any single batch's latency
      bounded, since the LLM call time scales with batch size; combined
      with parallel dispatch, the overall prewarm makespan drops from
      ``sum(batches)`` to roughly ``max(batches)``.

    The function is structurally identical to the previous sequential
    implementation when ``max_workers=1`` — there is no behavioural
    difference in the cached output, only in wall-clock time.
    """

    if keyword_client is None or not targets:
        return 0
    pending: list[tuple[str, dict[str, Any]]] = []
    for target in targets:
        cache_key = _target_keyword_cache_key(target)
        if isinstance(target_keyword_cache.get(cache_key), dict):
            continue
        pending.append((cache_key, deepcopy(target)))
    if not pending:
        return 0

    batch_size = max(1, int(batch_size if batch_size is not None else _reuse_target_keyword_batch_size()))
    max_workers = max(1, int(max_workers if max_workers is not None else _reuse_target_keyword_workers()))

    pending_batches: list[list[tuple[str, dict[str, Any]]]] = [
        pending[start:start + batch_size]
        for start in range(0, len(pending), batch_size)
    ]
    batches: list[list[dict[str, Any]]] = [
        [target for _cache_key, target in batch]
        for batch in pending_batches
    ]

    PROGRESS_LOGGER.info(
        "AI image reuse target keyword prewarm start: targets={}, batches={}, batch_size={}, workers={}",
        len(pending),
        len(batches),
        batch_size,
        min(max_workers, len(batches)),
    )

    def _enrich_one_batch(batch_assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Each thread builds its own throwaway DB wrapper so the canonical
        # ``enrich_ai_image_asset_db_keywords`` can be reused without
        # synchronising on the shared ``target_db``. The function mutates
        # the assets in place and returns them in input order.
        batch_db = {
            "schema_version": SCHEMA_VERSION,
            "assets": batch_assets,
            "warnings": [],
        }
        try:
            enrich_ai_image_asset_db_keywords(
                batch_db,
                keyword_client,
                batch_size=len(batch_assets),
                include_match_keywords=True,
            )
        except Exception as exc:  # pragma: no cover — defensive
            PROGRESS_LOGGER.warning(
                "AI image reuse target keyword prewarm batch failed: {}",
                str(exc)[:200],
            )
        return batch_db.get("assets") or []

    def _cache_batch(
        batch_pending: list[tuple[str, dict[str, Any]]],
        batch_enriched: list[dict[str, Any]],
    ) -> int:
        cached_count = 0
        for (cache_key, _target), enriched in zip(batch_pending, batch_enriched):
            if isinstance(enriched, dict):
                target_keyword_cache[cache_key] = deepcopy(enriched)
                cached_count += 1
        if cached_count and on_batch_cached is not None:
            on_batch_cached(cached_count, len(target_keyword_cache))
        return cached_count

    cached_new = 0
    if len(batches) == 1:
        cached_new += _cache_batch(pending_batches[0], _enrich_one_batch(batches[0]))
    else:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(batches))) as executor:
            future_to_batch = {
                executor.submit(_enrich_one_batch, batch_assets): batch_pending
                for batch_pending, batch_assets in zip(pending_batches, batches)
            }
            for future in as_completed(future_to_batch):
                batch_pending = future_to_batch[future]
                try:
                    batch_enriched = future.result()
                except Exception as exc:  # pragma: no cover - defensive
                    PROGRESS_LOGGER.warning(
                        "AI image reuse target keyword prewarm batch failed: {}",
                        str(exc)[:200],
                    )
                    batch_enriched = []
                cached_new += _cache_batch(batch_pending, batch_enriched)

    PROGRESS_LOGGER.info(
        "AI image reuse target keyword prewarm done: targets={}, cached_new={}, cached={}",
        len(pending),
        cached_new,
        len(target_keyword_cache),
    )
    return cached_new


def enrich_ai_image_asset_db_keywords(
    db: dict[str, Any],
    client: Any,
    *,
    batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
    include_match_keywords: bool = False,
    preserve_existing_context_fields: bool = False,
) -> dict[str, Any]:
    """Add LLM-built keyword fields to an already scanned asset DB.

    This is intentionally an offline enrichment step. It does not participate
    in PPT generation unless a caller later chooses to consume the generated
    fields.
    """

    assets = db.get("assets")
    if not isinstance(assets, list) or not assets:
        return db

    batch_size = max(1, int(batch_size or DEFAULT_KEYWORD_BATCH_SIZE))
    warnings = db.setdefault("warnings", [])
    db["schema_version"] = max(int(db.get("schema_version") or 0), KEYWORD_SCHEMA_VERSION)
    db["keyword_built_at"] = datetime.now(timezone.utc).isoformat()
    db["keyword_builder"] = {
        "method": "llm_reuse_target_keyword_extraction" if include_match_keywords else "llm_reuse_metadata_extraction",
        "batch_size": batch_size,
        "model": _client_model_name(client),
    }

    for start in range(0, len(assets), batch_size):
        batch = [asset for asset in assets[start:start + batch_size] if isinstance(asset, dict)]
        if not batch:
            continue
        try:
            response = _call_keyword_llm(client, batch, include_match_keywords=include_match_keywords)
            by_id = _keyword_payload_by_asset_id(response)
        except Exception as exc:
            # Per-asset fallback: a single malformed LLM response otherwise
            # discards keyword data for the entire batch — a real failure mode
            # that produced 7 page_image assets with empty core_keywords +
            # constraints in one observed library build. Retry each asset
            # singly so one bad apple no longer poisons its neighbors.
            warnings.append(
                f"keyword batch {start // batch_size + 1} failed: {exc}; retrying singly"
            )
            by_id = {}
            for asset in batch:
                asset_id = _clean_text(asset.get("asset_id"))
                try:
                    single_response = _call_keyword_llm(
                        client, [asset], include_match_keywords=include_match_keywords
                    )
                    by_id.update(_keyword_payload_by_asset_id(single_response))
                except Exception as single_exc:
                    warnings.append(
                        f"keyword asset {asset_id} failed after single retry: {single_exc}"
                    )

        for asset in batch:
            asset_id = _clean_text(asset.get("asset_id"))
            payload = by_id.get(asset_id)
            if payload is None:
                warnings.append(f"keyword payload missing for {asset_id}")
                continue
            _apply_keyword_payload(
                asset,
                payload,
                include_match_keywords=include_match_keywords,
                preserve_existing_context_fields=preserve_existing_context_fields,
            )

    return db


def _call_keyword_llm(
    client: Any,
    batch: list[dict[str, Any]],
    *,
    include_match_keywords: bool,
) -> dict[str, Any] | list[Any]:
    messages = _build_keyword_messages(batch, include_match_keywords=include_match_keywords)
    max_tokens = max(2048, min(16384, 900 * len(batch) + 1200))
    chat_json = getattr(client, "chat_json", None)
    PROGRESS_LOGGER.info(
        "AI image keyword LLM start: assets={}, include_match_keywords={}",
        len(batch),
        bool(include_match_keywords),
    )
    if callable(chat_json):
        try:
            response = chat_json(
                messages=messages,
                temperature=0.0,
                max_tokens=max_tokens,
                max_retries=1,
            )
        except TypeError:
            response = chat_json(messages, temperature=0.0, max_tokens=max_tokens)
        PROGRESS_LOGGER.info("AI image keyword LLM done: assets={}", len(batch))
        return response

    chat = getattr(client, "chat", None)
    if not callable(chat):
        raise TypeError("keyword client must provide chat_json() or chat()")
    raw = chat(messages=messages, temperature=0.0, max_tokens=max_tokens)
    response = _load_json_response(raw)
    PROGRESS_LOGGER.info("AI image keyword LLM done: assets={}", len(batch))
    return response


def _build_keyword_messages(
    batch: list[dict[str, Any]],
    *,
    include_match_keywords: bool = False,
) -> list[dict[str, str]]:
    from edupptx.materials.strict_reuse_classifier import (
        MATERIAL_CATEGORY_RULES_TEXT as _MATERIAL_CATEGORY_RULES_TEXT,
    )
    from edupptx.materials.caption_rules import CAPTION_RULE as _CAPTION_RULE
    from edupptx.materials.general_rules import GENERAL_RULE as _GENERAL_RULE
    items: list[dict[str, Any]] = []
    for asset in batch:
        items.append(
            {
                "asset_id": asset.get("asset_id"),
                "asset_kind": asset.get("asset_kind"),
                "theme": asset.get("theme"),
                "query": _asset_query(asset),
                "caption": _asset_caption(asset),
                "prompt_route": _match_prompt_route(asset.get("prompt_route")),
                "background_route": _match_background_route(asset.get("background_route")),
                "grade_norm": asset.get("grade_norm"),
                "grade_band": asset.get("grade_band"),
                "subject": asset.get("subject"),
                "subject_hint": asset.get("subject_hint") or asset.get("subject"),
                "grade_hint": asset.get("grade_hint") or asset.get("grade"),
                "page_type": _asset_page_type(asset),
                "aspect_ratio": _clean_text(asset.get("aspect_ratio")),
            }
        )

    if include_match_keywords:
        page_image_fields = (
            "asset_id、caption、context_summary、teaching_intent、general、strict_reuse_group、"
            "strict_reuse_secondary_group、secondary_reuse_query、secondary_reuse_caption、"
            "strict_reuse_confidence、strict_reuse_reason。"
        )
        background_fields = (
            "asset_id、normalized_prompt、color_temperature、context_summary、teaching_intent、general、"
            "strict_reuse_group、strict_reuse_secondary_group、strict_reuse_confidence、strict_reuse_reason。"
        )
        deck_metadata_instruction = (
            "subject、grade_norm 和 grade_band 已由 PPT/deck 级流程归一化，输入中仅作为固定上下文；"
            "不要输出、不要重新判断、不要覆盖这三个字段。"
        )
    else:
        page_image_fields = (
            "asset_id、caption、context_summary、teaching_intent、subject、grade_norm、grade_band、"
            "general、strict_reuse_group、strict_reuse_secondary_group、secondary_reuse_query、"
            "secondary_reuse_caption、strict_reuse_confidence、strict_reuse_reason。"
        )
        background_fields = (
            "asset_id、normalized_prompt、color_temperature、context_summary、teaching_intent、"
            "subject、grade_norm、grade_band、general、strict_reuse_group、strict_reuse_secondary_group、"
            "strict_reuse_confidence、strict_reuse_reason。"
        )
        deck_metadata_instruction = (
            "subject 必须只从以下枚举中选择：语文、数学、物理、其他。"
            "grade_norm 必须只从以下枚举中选择：一年级、二年级、三年级、四年级、五年级、六年级、七年级、八年级、九年级、高一、高二、高三、其他。"
            "grade_band 必须只从以下枚举中选择：低年级、高年级、其他。"
            "subject、grade_norm 和 grade_band 由你根据 theme、caption、subject_hint、grade_hint 以及用户显式线索自行判断并归一；"
            "即使输入 subject 或 grade 已有值，也必须重新输出上述枚举，不要复制非枚举格式。"
            "如果字段缺失、无法判断或不确定，一律输出其他。"
        )

    system = (
        "必须只返回严格 JSON，顶层对象必须包含 assets 数组。"
        f"page_image 只允许输出这些字段：{page_image_fields}"
        f"background 只允许输出这些字段：{background_fields}"
        f"{deck_metadata_instruction}"
        "general 必须是布尔值 true 或 false，表示当前素材本身是否可跨语文、数学、物理通用复用。"
        "page_image 和 background 输出示例都必须包含 \"general\": true 或 false 布尔字段，示例值不代表默认值。"
        "general 字段按下述共享规则判定：\n"
        + _GENERAL_RULE
        + "\n"
        "不要输出 core_keywords、semantic_aliases、constraints、context_summary_keywords、asset_category、query_aliases。"
        "strict_reuse_group 必须是下方 4 个素材类别主类 ID 之一。"
        "strict_reuse_secondary_group 只在主类为 C01 的具名地标图、其周边场景本身也可作氛围复用时，"
        "输出 C03_scene_decor_container；纯肖像/角色/文献/结构图及其它情况一律省略该字段。"
        "C00_strict_text_problem_skip 表示图片需要精确匹配文字、数字或符号，将跳过复用和素材库入库。"
        "page_image 的 context_summary 描述可见内容和页面用途；teaching_intent 描述教学动作。"
        "strict_reuse_group 分类只能基于 query 的完整描述内容（保留数值、汉字、标注、图形关系）。"
        "不要使用 page_type、subject、grade_norm、grade_band 来判断 strict_reuse_group。"
        "background 的 normalized_prompt 是视觉特征列表，格式为："
        "『色调:X; 纹理:Y; 明度:Z; 构图:W』。冷色、暖色、中性色只写入 color_temperature。"
        "默认使用简体中文；专有名词、缩写、品牌和公式保持原样。"
        "\n\n" + _MATERIAL_CATEGORY_RULES_TEXT
        + "strict_reuse_confidence 为 0-1。"
        "strict_reuse_reason 格式：『属于<类别中文名>：<被描述的主体>』。"
    )
    user = "请按结构规范化以下素材：\n" + json.dumps({"assets": items}, ensure_ascii=False, indent=2)
    system += "\n\ncaption 字段按下述规则产出（与 plan 侧共用同一规则）：\n" + _CAPTION_RULE
    keyword_rules = _load_keyword_reuse_rules_reference().replace("content_prompt", "query")
    if include_match_keywords:
        keyword_rules = re.sub(
            r"## 学科与年级字段.*?## 通用复用字段",
            (
                "## 学科与年级字段\n\n"
                "`subject`、`grade_norm`、`grade_band` 是 PPT/deck 级固定上下文字段。"
                "target keyword enrich 不输出、不重新判断、不覆盖这些字段。\n\n"
                "## 通用复用字段"
            ),
            keyword_rules,
            flags=re.S,
        )
    system += "\n\n" + keyword_rules
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _load_keyword_reuse_rules_reference() -> str:
    try:
        text = KEYWORD_REUSE_RULES_REFERENCE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"missing AI image reuse metadata rules reference: {KEYWORD_REUSE_RULES_REFERENCE}") from exc
    if not text:
        raise RuntimeError(f"empty AI image reuse metadata rules reference: {KEYWORD_REUSE_RULES_REFERENCE}")
    return text


def _keyword_payload_by_asset_id(response: dict[str, Any] | list[Any]) -> dict[str, dict[str, Any]]:
    if isinstance(response, dict):
        items = response.get("assets")
    else:
        items = response
    if not isinstance(items, list):
        raise ValueError("keyword LLM response must contain an assets array")

    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        asset_id = _clean_text(item.get("asset_id"))
        if asset_id:
            by_id[asset_id] = item
    return by_id


def _apply_general_from_payload(asset: dict[str, Any], payload: dict[str, Any]) -> None:
    general = _optional_bool(payload.get("general"))
    if general is not None:
        asset["general"] = general


def _grade_info_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    return {
        "grade_norm": _normalize_grade_norm_value(payload.get("grade_norm")),
        "grade_band": _normalize_grade_band_value(payload.get("grade_band")),
    }


def _apply_keyword_payload(
    asset: dict[str, Any],
    payload: dict[str, Any],
    *,
    include_match_keywords: bool = False,
    preserve_existing_context_fields: bool = False,
) -> None:
    preserved_review_fields = _preserve_review_fields(asset)
    padding_capacity = normalize_padding_capacity(asset.get("padding_capacity"))
    preserve_deck_metadata = bool(include_match_keywords)
    if preserve_deck_metadata:
        grade_info = normalize_grade_info(
            asset.get("grade_norm") or asset.get("grade"),
            asset.get("grade_band"),
        )
        subject = _normalize_subject_value(asset.get("subject"))
    else:
        grade_info = _grade_info_from_payload(payload)
        subject = _normalize_subject_value(payload.get("subject"))
    normalized_prompt = _clean_text(payload.get("normalized_prompt")) or _default_normalized_prompt(asset)
    color_temperature = _clean_text(payload.get("color_temperature"))
    if preserve_existing_context_fields:
        context_summary = (
            _clean_text(asset.get("context_summary"))
            or _clean_text(payload.get("context_summary"))
            or _fallback_context_summary(asset)
        )
        teaching_intent = (
            _clean_text(asset.get("teaching_intent"))
            or _clean_text(payload.get("teaching_intent"))
            or _default_teaching_intent(asset)
        )
    else:
        context_summary = _clean_text(payload.get("context_summary")) or _fallback_context_summary(asset)
        teaching_intent = _clean_text(payload.get("teaching_intent")) or _default_teaching_intent(asset)
    if _is_background_asset(asset):
        cleaned = {
            "asset_id": _clean_text(asset.get("asset_id")),
            "asset_kind": "background",
            "image_path": _clean_text(asset.get("image_path")),
            "aspect_ratio": _clean_text(asset.get("aspect_ratio")),
            "theme": _clean_text(asset.get("theme")),
            "subject": subject,
            "grade_norm": grade_info["grade_norm"] or _clean_text(asset.get("grade_norm")),
            "grade_band": grade_info["grade_band"] or _clean_text(asset.get("grade_band")),
            "unit_ref": _unit_ref_for_asset(asset),
            "topic_refs": _topic_refs_for_asset(asset),
            "content_prompt": _asset_content_prompt(asset),
            "background_route": _match_background_route(asset.get("background_route")),
            "normalized_prompt": normalized_prompt,
            "color_temperature": color_temperature or _clean_text(asset.get("color_temperature")),
            "context_summary": context_summary,
            "teaching_intent": teaching_intent,
        }
        cleaned.update(preserved_review_fields)
        _apply_general_from_payload(cleaned, payload)
        _apply_strict_reuse_group_from_payload(cleaned, payload)
        cleaned["strict_reuse_group"] = _clean_text(cleaned.get("strict_reuse_group")) or _GENERAL_REUSE_GROUP
        asset.clear()
        asset.update(cleaned)
        if include_match_keywords:
            asset["match_text"] = _build_match_text(asset)
            asset["match_key"] = _build_match_key(asset)
        return

    cleaned = {
        "asset_id": _clean_text(asset.get("asset_id")),
        "asset_kind": "page_image",
        "image_path": _clean_text(asset.get("image_path")),
        "aspect_ratio": _clean_text(asset.get("aspect_ratio")),
        "page_type": _asset_page_type(asset),
        "theme": _clean_text(asset.get("theme")),
        "subject": subject,
        "grade_norm": grade_info["grade_norm"] or _clean_text(asset.get("grade_norm")),
        "grade_band": grade_info["grade_band"] or _clean_text(asset.get("grade_band")),
        "unit_ref": _unit_ref_for_asset(asset),
        "topic_refs": _topic_refs_for_asset(asset),
        "caption": _clean_text(payload.get("caption")) or _asset_caption(asset),
        "context_summary": context_summary,
        "teaching_intent": teaching_intent,
        "duplicate_asset_ids": _dedupe_terms(_as_string_list(asset.get("duplicate_asset_ids"))),
    }
    detail_prompt = _clean_text(asset.get("detail_prompt"))
    if detail_prompt:
        cleaned["detail_prompt"] = detail_prompt
    if padding_capacity:
        cleaned["padding_capacity"] = padding_capacity
    cleaned.update(preserved_review_fields)
    _apply_general_from_payload(cleaned, payload)
    _apply_strict_reuse_group_from_payload(cleaned, payload)
    asset.clear()
    asset.update(cleaned)
    if include_match_keywords:
        asset["match_text"] = _build_match_text(asset)
        asset["match_key"] = _build_match_key(asset)


def _build_match_text(asset: dict[str, Any]) -> str:
    if _is_background_asset(asset):
        return _background_retrieval_text(asset)

    return _page_retrieval_text(asset)


def _build_match_key(asset: dict[str, Any]) -> str:
    if _is_background_asset(asset):
        terms = _bm25_tokens_from_values([_background_retrieval_text(asset)])
    else:
        terms = _bm25_tokens_from_values([_page_retrieval_text(asset)])
    return "|".join(terms[:12])


def _apply_strict_reuse_group_from_payload(asset: dict[str, Any], payload: dict[str, Any]) -> None:
    from edupptx.materials.strict_reuse_classifier import (
        SECONDARY_REUSE_GROUP_FIELD,
        normalize_secondary_reuse_group,
    )

    payload_has_group = bool(_clean_text(payload.get("strict_reuse_group")))
    existing_has_group = bool(_clean_text(asset.get("strict_reuse_group")))

    if payload_has_group:
        group = _normalize_binary_reuse_group(payload.get("strict_reuse_group"))
    elif existing_has_group:
        group = _normalize_binary_reuse_group(asset.get("strict_reuse_group"))
    else:
        return

    asset["strict_reuse_group"] = group

    if payload_has_group:
        confidence = _optional_float(payload.get("strict_reuse_confidence"))
        if confidence is None:
            confidence = _optional_float(asset.get("strict_reuse_confidence"))
    else:
        confidence = _optional_float(asset.get("strict_reuse_confidence"))
        if confidence is None:
            confidence = _optional_float(payload.get("strict_reuse_confidence"))
    if confidence is None:
        confidence = 0.8 if payload_has_group else 0.9
    asset["strict_reuse_confidence"] = round(max(0.0, min(1.0, confidence)), 4)

    if payload_has_group:
        reason = _clean_text(payload.get("strict_reuse_reason")) or _clean_text(asset.get("strict_reuse_reason"))
    else:
        reason = _clean_text(asset.get("strict_reuse_reason")) or _clean_text(payload.get("strict_reuse_reason"))
    asset["strict_reuse_reason"] = reason or "LLM reuse group classification"

    signal = "llm_reuse_group" if payload_has_group else "upstream_reuse_group"
    if payload_has_group:
        prior_signals = [
            item
            for item in _as_string_list(asset.get("strict_reuse_signals"))
            if item != "upstream_reuse_group"
        ]
    else:
        prior_signals = _as_string_list(asset.get("strict_reuse_signals"))
    asset["strict_reuse_signals"] = _dedupe_terms([*prior_signals, signal])

    secondary_source = (
        payload.get(SECONDARY_REUSE_GROUP_FIELD)
        if _clean_text(payload.get(SECONDARY_REUSE_GROUP_FIELD))
        else asset.get(SECONDARY_REUSE_GROUP_FIELD)
    )
    secondary = normalize_secondary_reuse_group(secondary_source, primary=group)
    if secondary:
        asset[SECONDARY_REUSE_GROUP_FIELD] = secondary
        secondary_query = _clean_text(payload.get("secondary_reuse_query")) or _clean_text(
            asset.get("secondary_reuse_query")
        )
        secondary_caption = _clean_text(payload.get("secondary_reuse_caption")) or _clean_text(
            asset.get("secondary_reuse_caption")
        )
        if secondary_query:
            asset["secondary_reuse_query"] = secondary_query
        if secondary_caption:
            asset["secondary_reuse_caption"] = secondary_caption
    else:
        asset.pop(SECONDARY_REUSE_GROUP_FIELD, None)
        asset.pop("secondary_reuse_query", None)
        asset.pop("secondary_reuse_caption", None)
