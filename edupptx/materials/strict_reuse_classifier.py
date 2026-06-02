"""Material category classification utilities for AI image material libraries."""

from __future__ import annotations

import json
import re
import shutil
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from edupptx.materials.ai_image_asset_db import (
    BACKGROUND_REUSE_INDEX_FILENAME,
    BACKGROUND_REUSE_INDEX_GROUP,
    DEFAULT_MATCH_INDEX_FILENAME,
)

STRICT_REUSE_CLASSIFIER_VERSION = 7
STRICT_REUSE_REVIEW_QUEUE_FILENAME = "strict_reuse_review_queue.jsonl"
STRICT_REUSE_REPORT_FILENAME = "strict_reuse_classification_report.json"
STRICT_REUSE_VISUAL_CHECK_MANIFEST_FILENAME = "manifest.json"
STRICT_REUSE_VISUAL_CHECK_HTML_FILENAME = "index.html"
STRICT_REUSE_VISUAL_CHECK_MODE = "strict-reuse-export-check"
STRICT_REUSE_INDEX_DIRNAME = "strict_reuse_indexes"

# --- 4 active material categories (contiguous IDs; no historical aliases) ---
C00_STRICT_TEXT_PROBLEM_SKIP = "C00_strict_text_problem_skip"
C01_IRREPLACEABLE_ENTITY_EVENT_ACTION = "C01_irreplaceable_entity_event_action"
C02_GENERIC_SUBJECT_OBJECT = "C02_generic_subject_object"
C03_SCENE_DECOR_CONTAINER = "C03_scene_decor_container"

MATERIAL_CATEGORIES = (
    C00_STRICT_TEXT_PROBLEM_SKIP,
    C01_IRREPLACEABLE_ENTITY_EVENT_ACTION,
    C02_GENERIC_SUBJECT_OBJECT,
    C03_SCENE_DECOR_CONTAINER,
)
_MATERIAL_CATEGORY_SET = frozenset(MATERIAL_CATEGORIES)

_LEGACY_CATEGORY_MIGRATION: dict[str, str] = {}

MATERIAL_CATEGORY_RULES_TEXT = (
    "## strict_reuse_group 4 类分类规则（v3，复用不变量锐化）\n"
    "\n"
    "全局原则：只根据 query 字面描述判断复用粒度。严禁参考 theme、subject、grade_norm、文件名、"
    "原始 strict_reuse_group 或其他元数据。按 C00→C01→C02→C03 顺序判断，命中高优先即停。\n"
    "\n"
    "只允许输出以下 4 个主类 ID：\n"
    "C00_strict_text_problem_skip\n"
    "C01_irreplaceable_entity_event_action\n"
    "C02_generic_subject_object\n"
    "C03_scene_decor_container\n"
    "\n"
    "复用宽松度谱系（严→松）：C00 不复用 → C01 实体/事件/动作严格匹配 → C02 类型匹配 → C03 语境匹配\n"
    "\n"
    "C00↔C01 优先级例外（避免具名实体被图示/文字分支抢先）：先判画面核心是什么——\n"
    "画面核心是逐字文字/符号载荷本身（整段课文/题干/生字/拼音/公式/竖式）→ C00 优先；\n"
    "画面核心是具名/特定实体本身（具名人物/角色/地标/文献/文物），即便呈现为结构图、布局图、"
    "含手写字或场景 → C01 优先，不落 C00（赵州桥结构图、故宫平面布局图、黄文秀日记都按 C01）；\n"
    "二者都不是、而是通用知识结构图示（无名几何/光路/思维导图/泛结构图）→ 回到 C00 图示分支。\n"
    "\n"
    "0. C00_strict_text_problem_skip（精确不可替换载荷 — 跳过复用）：\n"
    "画面复用价值依赖必须逐一复现的精确内容时归 C00：\n"
    "(a) 字面文字/数字/公式：整段课文/诗文/题干/选项/解题步骤、生字表、竖式算式；\n"
    "(b) 语言符号本体（含手势/口型演示）：汉字/拼音/笔顺/部首/字源；拼音手指操、口型手势也算"
    "——教的是那个特定符号，换一个就丧失教学功能；\n"
    "(c) 精确教学关系/结构（即使无数字）：几何带数据图、光路/成像/实验装置结构、思维导图/流程图/关系图；"
    "无数字但表达特定平衡/对应关系的图（如天平两端特定物体平衡）也算——关系本身是必须复现的载荷。\n"
    "排除（替换不变性判据）：换一组仍服务同样复用的就不算 C00。空白脚手架（空网格/空坐标系/空数位表/"
    "空白田字格模板/空烧杯线稿）无任何要复现的载荷，按主体走 C02/C03；通用量具印着的刻度/标号、"
    "装饰文字、可替换短标签都不算载荷。\n"
    "\n"
    "1. C01_irreplaceable_entity_event_action（不可替换具名/特定身份 或 复杂动作叙事 — 严格匹配）：\n"
    "满足任一即归 C01：具名/特定人物（含肖像/照片/塑像，如李白、鲁迅、黄文秀）；具名/特定角色"
    "（即使静态，如孙悟空、猪八戒、哆啦A梦）；具名/特定地标或地点（即使呈现为场景、结构图或平面布局图，"
    "如卢沟桥、故宫、赵州桥、望湖楼）；具名/特定实物/文献/文物（如黄文秀扶贫日记）；复杂动作/叙事/"
    "人物关系（儿子发火母亲门口偷看、纪昌学射、红军翻越雪山，有意图/对象/结果的动作、冲突/抗拒/后果/"
    "状态转折、强情绪故事状态）。\n"
    "不归 C01：匿名通用主体的简单姿态/普通表情、无名拟人形象、通用演示手势，不构成不可替换命题时"
    "下探 C02/C03。\n"
    "地标双用副标签：当主类为 C01 的具名地标图，其周边场景本身也可作氛围复用（具名地标嵌在可迁移场景里，"
    "如寒山寺江景、望湖楼雨景）时，额外输出 strict_reuse_secondary_group=C03_scene_decor_container；"
    "纯肖像/角色/文献/结构图不输出。\n"
    "\n"
    "2. C02_generic_subject_object（通用/匿名主体对象 — 类型级匹配）：\n"
    "画面核心是可辨识但通用、匿名、可替换的主体或对象：匿名人物/动物/植物、无名拟人形象"
    "（卡通雾孩子、拟人化蚂蚁）；通用工具/器材/技术件（空数位表、计数器、量角器、温度计、直尺、"
    "钟表表盘、汽车轮胎、邮票）；通用操作/姿势手势（执笔姿势、折纸的手）——与 C00 语言符号手势区分："
    "教通用动作=C02，教特定拼音/字符符号=C00；通用物附带的品牌/标识/标签字不升级类别"
    "（米其林轮胎、带水印的手电筒光束仍 C02）。自然物按离散主体与整体景观二分：可单独指代、可替换的"
    "离散主体（一棵树/一只鹤）归 C02。\n"
    "\n"
    "3. C03_scene_decor_container（场景装饰容器 — 语境级匹配）：\n"
    "画面核心是无具名焦点主体的整体景观/场景/氛围/装饰/容器：泛山水/泛场景/远景（泛江南水乡、"
    "云海红日、整体景观如山峦山水）；匿名小人物融入的场景（山居秋暝里匿名浣女与渔夫）；氛围/天气/版式/"
    "留白容器/边框/装饰图案/内容占位模板。裁出某主体后仍主要作该主体复用则回 C02。\n"
    "\n"
    "速记：手势——教特定符号→C00，教通用动作→C02；具名压过形态——具名地标/人物即便是图示/结构图/"
    "场景也按 C01；空白 vs 填充——空白脚手架无载荷→C02/C03，承载特定字符/数据/关系→C00。\n"
)

MATERIAL_CATEGORY_RULES_TEXT = MATERIAL_CATEGORY_RULES_TEXT.replace("content_prompt", "query")


def _asset_caption(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("caption")) or _clean_text(asset.get("content_prompt"))


def _asset_query(asset: dict[str, Any]) -> str:
    return (
        _clean_text(asset.get("query"))
        or _clean_text(asset.get("detail_prompt"))
        or _clean_text(asset.get("content_prompt"))
    )


def _build_classify_prompt(payload: dict[str, Any]) -> str:
    query = _asset_query(payload)
    request = {
        "asset_id": _clean_text(payload.get("asset_id")),
        "query": query,
    }
    return (
        "Classify this material into exactly one strict_reuse_group using only the query field.\n\n"
        + MATERIAL_CATEGORY_RULES_TEXT
        + "\n\nInput JSON:\n"
        + json.dumps(request, ensure_ascii=False, indent=2)
    )


# Canonical convenience constants (downstream code still imports these names).
GENERAL_REUSE_GROUP = C03_SCENE_DECOR_CONTAINER
CONTENT_REUSE_GROUP = C00_STRICT_TEXT_PROBLEM_SKIP
STRICT_REUSE_GROUPS = MATERIAL_CATEGORIES
STRICT_REUSE_SPLIT_GROUPS = MATERIAL_CATEGORIES

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def normalize_strict_reuse_group(value: Any, *, default: str = GENERAL_REUSE_GROUP) -> str:
    """Normalize canonical material category labels."""

    text = _clean_text(value).casefold()
    if not text:
        return default
    if text in _MATERIAL_CATEGORY_SET:
        return text
    # Case-insensitive match for current IDs
    for cat in MATERIAL_CATEGORIES:
        if text == cat.casefold():
            return cat
    return default


SECONDARY_REUSE_GROUP_FIELD = "strict_reuse_secondary_group"


def normalize_secondary_reuse_group(value: Any, *, primary: Any) -> str:
    """Normalize the lazy C03 dual tag carried on a C01 primary asset.

    This version is annotation-only (never double-written into the C03 split).
    Only ``C03`` attached to a ``C01`` primary is meaningful; anything else
    collapses to ``""``.
    """

    primary_norm = normalize_strict_reuse_group(primary, default="")
    secondary = normalize_strict_reuse_group(value, default="")
    if (
        primary_norm == C01_IRREPLACEABLE_ENTITY_EVENT_ACTION
        and secondary == C03_SCENE_DECOR_CONTAINER
    ):
        return secondary
    return ""


SKIP_FROM_INDEX_VLM_QUALITY_THRESHOLD = 0.3


def should_skip_from_index(asset: dict[str, Any]) -> bool:
    group = normalize_strict_reuse_group(asset.get("strict_reuse_group"))
    if group == C00_STRICT_TEXT_PROBLEM_SKIP:
        return True

    vlm_quality = asset.get("vlm_match_quality")
    if vlm_quality is not None:
        try:
            if float(vlm_quality) < SKIP_FROM_INDEX_VLM_QUALITY_THRESHOLD:
                return True
        except (TypeError, ValueError):
            pass

    from edupptx.materials.ai_image_asset_db import normalize_aspect_bucket
    bucket = normalize_aspect_bucket(asset.get("aspect_ratio"))
    padding = _clean_text(asset.get("padding_capacity")).casefold()
    if bucket == "other" and padding == "none":
        return True

    return False


def classify_asset_strict_reuse(
    asset: dict[str, Any],
    *,
    infer_legacy_missing: bool = False,
) -> dict[str, Any]:
    """Normalize one asset's upstream reuse-group decision.

    New assets are classified by the LLM/VLM stages and arrive with
    ``strict_reuse_group`` already set by an LLM/VLM stage. This pass trusts
    explicit upstream labels and only normalizes their format. Missing labels
    are never inferred from local keywords.
    """

    asset_kind = _clean_text(asset.get("asset_kind"))
    if asset_kind == "background":
        return _classification(
            GENERAL_REUSE_GROUP,
            1.0,
            ["background_asset_kind"],
            [],
            reason="background assets routed by asset_kind, not classified",
        )
    if asset_kind and asset_kind != "page_image":
        return _classification(
            GENERAL_REUSE_GROUP,
            1.0,
            ["non_page_image"],
            [],
            reason=f"non-page images use {GENERAL_REUSE_GROUP} routing",
        )

    raw_group = _clean_text(asset.get("strict_reuse_group"))
    if (
        infer_legacy_missing
        and raw_group
        and normalize_strict_reuse_group(raw_group) == GENERAL_REUSE_GROUP
        and _is_missing_upstream_default(asset)
    ):
        raw_group = ""
    if infer_legacy_missing and raw_group and _is_legacy_unclassified_inference(asset):
        raw_group = ""

    if raw_group:
        group = normalize_strict_reuse_group(raw_group, default="")
        if not group:
            return _classification(
                GENERAL_REUSE_GROUP,
                0.5,
                ["invalid_upstream_reuse_group"],
                ["invalid_upstream_reuse_group"],
                reason=f"invalid upstream reuse group {raw_group}; expected LLM/VLM material category",
            )
        signals = ["upstream_reuse_group"]
        confidence = _to_score(asset.get("strict_reuse_confidence"))
        if confidence is None:
            confidence = 0.86 if group == CONTENT_REUSE_GROUP else 0.9
        reason = f"kept upstream reuse group {group}"
        return _classification(group, confidence, signals, [], reason=reason)

    if infer_legacy_missing:
        return _classification(
            GENERAL_REUSE_GROUP,
            0.78,
            ["legacy_default_generic_scene_activity"],
            [],
            reason=f"legacy unclassified asset defaulted to {GENERAL_REUSE_GROUP}",
        )

    return _classification(
        GENERAL_REUSE_GROUP,
        0.5,
        ["missing_upstream_reuse_classification"],
        ["missing_upstream_reuse_classification"],
        reason=f"no LLM/VLM reuse classification; defaulted to {GENERAL_REUSE_GROUP}",
    )


def classify_strict_reuse_groups(
    index: dict[str, Any],
    *,
    infer_legacy_missing: bool = False,
) -> dict[str, Any]:
    """Mutate an index/database in-place with normalized binary reuse fields."""

    assets = index.get("assets")
    asset_list = assets if isinstance(assets, list) else []
    group_counts: Counter[str] = Counter()
    review_reason_counts: Counter[str] = Counter()
    review_items: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()

    for asset in asset_list:
        if not isinstance(asset, dict):
            continue
        result = classify_asset_strict_reuse(asset, infer_legacy_missing=infer_legacy_missing)
        _apply_classification(asset, result)
        group_counts[result["strict_reuse_group"]] += 1
        review_reasons = result["strict_reuse_review_reasons"]
        if result["strict_reuse_review_required"]:
            for reason in review_reasons:
                review_reason_counts[reason] += 1
            review_items.append(_review_queue_item(asset, result))

    for group in STRICT_REUSE_GROUPS:
        group_counts.setdefault(group, 0)

    classification_source = (
        "legacy_unclassified_index_migration" if infer_legacy_missing else "reuse_group_format_migration"
    )
    metadata = {
        "classifier_version": STRICT_REUSE_CLASSIFIER_VERSION,
        "updated_at": now,
        "group_counts": dict(group_counts),
        "review_required_count": len(review_items),
        "classification_source": classification_source,
        "legacy_inference_enabled": bool(infer_legacy_missing),
    }
    index["strict_reuse_classification"] = metadata
    index["updated_at"] = now

    return {
        "classifier_version": STRICT_REUSE_CLASSIFIER_VERSION,
        "updated_at": now,
        "asset_count": len(asset_list),
        "group_counts": dict(group_counts),
        "review_required_count": len(review_items),
        "review_reason_counts": dict(review_reason_counts),
        "review_asset_ids": [item["asset_id"] for item in review_items],
        "review_items": review_items,
        "classification_source": classification_source,
        "legacy_inference_enabled": bool(infer_legacy_missing),
    }


def classify_strict_reuse_library(
    library_dir: str | Path,
    *,
    index_filename: str = DEFAULT_MATCH_INDEX_FILENAME,
    dry_run: bool = False,
    write_debug: bool = True,
    split_dir: str | Path | None = STRICT_REUSE_INDEX_DIRNAME,
    prefer_split_index: bool = True,
) -> tuple[dict[str, Any], Path]:
    """Normalize one material library index on disk."""

    root = Path(library_dir).expanduser().resolve()
    index_path = root / index_filename
    index, source_path, source_kind = _read_classification_source(
        root,
        index_path,
        split_dir,
        prefer_split_index=prefer_split_index,
    )

    infer_legacy_missing = (
        source_kind == "legacy_match_index"
        or _has_missing_upstream_defaults(index)
        or _has_legacy_unclassified_inferences(index)
    )
    report = classify_strict_reuse_groups(index, infer_legacy_missing=infer_legacy_missing)
    report["library_dir"] = str(root)
    report["source_index_path"] = str(source_path)
    report["source_kind"] = source_kind

    if split_dir is not None:
        split_report = write_strict_reuse_group_indexes(index, root, split_dir=split_dir, dry_run=dry_run)
        report["split_indexes"] = split_report

    if not dry_run:
        if index_path.exists():
            index_path.unlink()
        if write_debug:
            debug_dir = root / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            report_path = debug_dir / STRICT_REUSE_REPORT_FILENAME
            queue_path = debug_dir / STRICT_REUSE_REVIEW_QUEUE_FILENAME
            report_path.write_text(
                json.dumps(_report_without_review_items(report), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _write_review_queue(queue_path, report["review_items"])
            report["debug_report_path"] = str(report_path)
            report["review_queue_path"] = str(queue_path)

    return report, Path(report.get("split_indexes", {}).get("split_dir") or source_path)


def write_strict_reuse_group_indexes(
    index: dict[str, Any],
    library_dir: str | Path,
    *,
    split_dir: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write binary reuse-group indexes that reference the same image paths."""

    root = Path(library_dir).expanduser().resolve()
    target_dir = Path(split_dir)
    if not target_dir.is_absolute():
        target_dir = root / target_dir

    assets = [asset for asset in index.get("assets", []) if isinstance(asset, dict)]
    written: dict[str, dict[str, Any]] = {}
    now = datetime.now(timezone.utc).isoformat()
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
    for group in STRICT_REUSE_SPLIT_GROUPS:
        group_assets = [
            deepcopy(asset)
            for asset in assets
            if normalize_strict_reuse_group(asset.get("strict_reuse_group")) == group
            and (group == C00_STRICT_TEXT_PROBLEM_SKIP or not should_skip_from_index(asset))
            and _clean_text(asset.get("asset_kind")) != "background"
        ]
        payload = {
            "schema_version": index.get("schema_version"),
            "strict_reuse_group": group,
            "built_at": now,
            "updated_at": now,
            "asset_root": index.get("asset_root") or str(root),
            "asset_count": len(group_assets),
            "assets": group_assets,
        }
        output_path = target_dir / f"{group}.json"
        written[group] = {"path": str(output_path), "asset_count": len(group_assets)}
        if not dry_run:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    background_assets = [
        deepcopy(asset)
        for asset in assets
        if _clean_text(asset.get("asset_kind")) == "background"
        and not should_skip_from_index(asset)
    ]
    for asset in background_assets:
        asset["asset_kind"] = "background"
        asset["strict_reuse_group"] = normalize_strict_reuse_group(asset.get("strict_reuse_group"))
    background_payload = {
        "schema_version": index.get("schema_version"),
        "strict_reuse_group": BACKGROUND_REUSE_INDEX_GROUP,
        "built_at": now,
        "updated_at": now,
        "asset_root": index.get("asset_root") or str(root),
        "asset_count": len(background_assets),
        "assets": background_assets,
    }
    background_path = target_dir / BACKGROUND_REUSE_INDEX_FILENAME
    written[BACKGROUND_REUSE_INDEX_GROUP] = {
        "path": str(background_path),
        "asset_count": len(background_assets),
    }
    if not dry_run:
        background_path.parent.mkdir(parents=True, exist_ok=True)
        background_path.write_text(json.dumps(background_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not dry_run:
        legacy_manifest = target_dir / "strict_reuse_split_manifest.json"
        if legacy_manifest.exists():
            legacy_manifest.unlink()
    return {"split_dir": str(target_dir), "groups": written}


def _read_classification_source(
    root: Path,
    index_path: Path,
    split_dir: str | Path | None,
    *,
    prefer_split_index: bool = True,
) -> tuple[dict[str, Any], Path, str]:
    target_dir = Path(split_dir) if split_dir is not None else Path(STRICT_REUSE_INDEX_DIRNAME)
    if not target_dir.is_absolute():
        target_dir = root / target_dir
    split_source = _read_split_classification_source(root, target_dir)
    if prefer_split_index and split_source is not None:
        index, source_path = split_source
        if index.get("assets") or not index_path.exists():
            return index, source_path, "split_index"

    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(index, dict):
            raise ValueError(f"AI image match index is not a JSON object: {index_path}")
        return index, index_path, "legacy_match_index"

    if split_source is not None:
        index, source_path = split_source
        return index, source_path, "split_index"

    raise FileNotFoundError(
        f"AI image match index not found: {index_path}; split indexes not found under: {target_dir}"
    )


def _read_split_classification_source(root: Path, target_dir: Path) -> tuple[dict[str, Any], Path] | None:
    assets: list[dict[str, Any]] = []
    found = False
    first_payload: dict[str, Any] = {}
    background_path = target_dir / BACKGROUND_REUSE_INDEX_FILENAME
    has_background_split = background_path.exists()
    for group in STRICT_REUSE_GROUPS:
        path = target_dir / f"{group}.json"
        if not path.exists():
            continue
        found = True
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Strict reuse index is not a JSON object: {path}")
        if not first_payload:
            first_payload = payload
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list):
            continue
        for raw_asset in raw_assets:
            if not isinstance(raw_asset, dict):
                continue
            asset = deepcopy(raw_asset)
            asset["strict_reuse_group"] = normalize_strict_reuse_group(
                asset.get("strict_reuse_group") or payload.get("strict_reuse_group") or group,
            )
            if has_background_split and _clean_text(asset.get("asset_kind")) == "background":
                continue
            assets.append(asset)
    if has_background_split:
        found = True
        payload = json.loads(background_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Strict reuse index is not a JSON object: {background_path}")
        if not first_payload:
            first_payload = payload
        raw_assets = payload.get("assets")
        if isinstance(raw_assets, list):
            for raw_asset in raw_assets:
                if not isinstance(raw_asset, dict):
                    continue
                asset = deepcopy(raw_asset)
                if _clean_text(asset.get("asset_kind")) != "background":
                    continue
                asset["asset_kind"] = "background"
                asset["strict_reuse_group"] = normalize_strict_reuse_group(
                    asset.get("strict_reuse_group"),
                )
                assets.append(asset)
    if not found:
        return None

    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": first_payload.get("schema_version"),
        "built_at": first_payload.get("built_at") or now,
        "updated_at": now,
        "asset_root": first_payload.get("asset_root") or str(root),
        "asset_count": len(assets),
        "assets": assets,
        "warnings": first_payload.get("warnings", []),
    }, target_dir


def export_strict_reuse_visual_check(
    library_dir: str | Path,
    output_dir: str | Path,
    *,
    index_filename: str = DEFAULT_MATCH_INDEX_FILENAME,
    clean: bool = True,
    force: bool = False,
) -> tuple[dict[str, Any], Path]:
    """Copy assets into material category folders for inspection."""

    root = Path(library_dir).expanduser().resolve()
    index_path = root / index_filename
    index, source_path, _source_kind = _read_classification_source(root, index_path, None)

    target_dir = Path(output_dir).expanduser()
    if not target_dir.is_absolute():
        target_dir = Path.cwd() / target_dir
    target_dir = target_dir.resolve()
    _ensure_visual_check_target_is_separate(root, target_dir)
    _prepare_visual_check_dir(target_dir, clean=clean, force=force)

    assets = [asset for asset in index.get("assets", []) if isinstance(asset, dict)]
    entries: list[dict[str, Any]] = []
    group_counts: Counter[str] = Counter()
    missing_items: list[dict[str, Any]] = []

    for ordinal, asset in enumerate(assets, 1):
        group = normalize_strict_reuse_group(asset.get("strict_reuse_group"))
        source_path = _resolve_asset_image_path(asset, root, index)
        target_path = _visual_check_target_path(target_dir, group, asset, source_path, ordinal)
        copied = False
        if source_path.exists() and source_path.is_file():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            copied = True
        else:
            missing_items.append(
                {
                    "asset_id": _clean_text(asset.get("asset_id")),
                    "image_path": _clean_text(asset.get("image_path")),
                    "resolved_source_path": str(source_path),
                    "strict_reuse_group": group,
                }
            )

        entries.append(
            {
                "asset_id": _clean_text(asset.get("asset_id")),
                "strict_reuse_group": group,
                "source_image_path": str(source_path),
                "output_image_path": _relative_posix(target_path, target_dir) if copied else "",
                "copied": copied,
                "subject": _clean_text(asset.get("subject")),
                "caption": _asset_caption(asset),
                "vlm_match_quality": asset.get("vlm_match_quality"),
            }
        )
        group_counts[group] += 1

    for group in STRICT_REUSE_GROUPS:
        (target_dir / group).mkdir(parents=True, exist_ok=True)
        group_counts.setdefault(group, 0)

    manifest_path = target_dir / STRICT_REUSE_VISUAL_CHECK_MANIFEST_FILENAME
    now = datetime.now(timezone.utc).isoformat()
    manifest = {
        "mode": STRICT_REUSE_VISUAL_CHECK_MODE,
        "built_at": now,
        "library_dir": str(root),
        "source_index_path": str(source_path),
        "output_dir": str(target_dir),
        "asset_count": len(assets),
        "copied_count": sum(1 for entry in entries if entry["copied"]),
        "missing_image_count": len(missing_items),
        "group_counts": dict(group_counts),
        "manifest_path": str(manifest_path),
        "missing_items": missing_items,
        "assets": entries,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest, target_dir


def _is_missing_upstream_default(asset: dict[str, Any]) -> bool:
    signals = {_clean_text(item) for item in _as_string_list(asset.get("strict_reuse_signals"))}
    if "missing_upstream_reuse_classification" in signals:
        return True
    reason = _clean_text(asset.get("strict_reuse_reason")).casefold()
    return "missing_upstream_reuse_classification" in reason or f"defaulted to {GENERAL_REUSE_GROUP}".casefold() in reason


def _is_legacy_unclassified_inference(asset: dict[str, Any]) -> bool:
    signals = {_clean_text(item) for item in _as_string_list(asset.get("strict_reuse_signals"))}
    if any(
        signal == "legacy_default_generic_scene_activity"
        or signal == "legacy_content_context"
        or signal == "legacy_content_keyword"
        or signal == "legacy_math_expression"
        or signal.startswith("legacy_exact_constraint:")
        for signal in signals
    ):
        return True
    reason = _clean_text(asset.get("strict_reuse_reason")).casefold()
    return "legacy unclassified asset" in reason


def _has_missing_upstream_defaults(index: dict[str, Any]) -> bool:
    assets = index.get("assets")
    if not isinstance(assets, list):
        return False
    return any(isinstance(asset, dict) and _is_missing_upstream_default(asset) for asset in assets)


def _has_legacy_unclassified_inferences(index: dict[str, Any]) -> bool:
    assets = index.get("assets")
    if not isinstance(assets, list):
        return False
    return any(isinstance(asset, dict) and _is_legacy_unclassified_inference(asset) for asset in assets)


def _apply_classification(asset: dict[str, Any], result: dict[str, Any]) -> None:
    asset["strict_reuse_group"] = result["strict_reuse_group"]
    asset["strict_reuse_confidence"] = result["strict_reuse_confidence"]
    asset["strict_reuse_reason"] = result["strict_reuse_reason"]
    asset.pop("strict_reuse_vlm_review_required", None)
    asset.pop("strict_reuse_vlm_review_reasons", None)
    asset.pop("strict_reuse_review_required", None)
    asset.pop("strict_reuse_review_reasons", None)
    asset.pop("strict_reuse_requires_exact_match", None)
    signals = result["strict_reuse_signals"]
    if signals:
        asset["strict_reuse_signals"] = signals
    else:
        asset.pop("strict_reuse_signals", None)
    secondary = normalize_secondary_reuse_group(
        asset.get(SECONDARY_REUSE_GROUP_FIELD),
        primary=result["strict_reuse_group"],
    )
    if secondary:
        asset[SECONDARY_REUSE_GROUP_FIELD] = secondary
    else:
        asset.pop(SECONDARY_REUSE_GROUP_FIELD, None)


def _classification(
    group: str,
    confidence: float,
    signals: list[str],
    review_reasons: list[str],
    *,
    reason: str = "",
) -> dict[str, Any]:
    group = normalize_strict_reuse_group(group)
    review_reasons = _dedupe(review_reasons)
    return {
        "strict_reuse_group": group,
        "strict_reuse_confidence": round(max(0.0, min(1.0, confidence)), 4),
        "strict_reuse_reason": reason or _reason_for_group(group, signals, review_reasons),
        "strict_reuse_signals": _dedupe(signals),
        "strict_reuse_review_required": bool(review_reasons),
        "strict_reuse_review_reasons": review_reasons,
    }


def _review_queue_item(asset: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": _clean_text(asset.get("asset_id")),
        "image_path": _clean_text(asset.get("image_path")),
        "subject": _clean_text(asset.get("subject")),
        "caption": _asset_caption(asset),
        "strict_reuse_group": result["strict_reuse_group"],
        "strict_reuse_confidence": result["strict_reuse_confidence"],
        "review_reasons": result["strict_reuse_review_reasons"],
        "signals": result["strict_reuse_signals"],
        "vlm_match_quality": asset.get("vlm_match_quality"),
        "constraints": asset.get("constraints") or [],
        "review_status": "pending",
    }


def _write_review_queue(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not items:
        path.write_text("", encoding="utf-8")
        return
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in items) + "\n",
        encoding="utf-8",
    )


def _report_without_review_items(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "review_items"}


def _reason_for_group(group: str, signals: list[str], review_reasons: list[str]) -> str:
    if review_reasons:
        return f"{group} assigned with review flags"
    if signals:
        return f"{group} assigned from {signals[0]}"
    return f"{group} assigned"


def _ensure_visual_check_target_is_separate(library_root: Path, target_dir: Path) -> None:
    if target_dir == library_root or _is_relative_to(target_dir, library_root):
        raise ValueError(
            "Visual check output directory must be outside the material library "
            f"to keep the library unchanged: {target_dir}"
        )


def _prepare_visual_check_dir(target_dir: Path, *, clean: bool, force: bool) -> None:
    manifest_path = target_dir / STRICT_REUSE_VISUAL_CHECK_MANIFEST_FILENAME
    if manifest_path.exists() and not force:
        existing = _load_json_object_or_none(manifest_path)
        if existing is None or existing.get("mode") != STRICT_REUSE_VISUAL_CHECK_MODE:
            raise ValueError(
                f"Refusing to overwrite an unrelated manifest: {manifest_path}. "
                "Use --force or choose a dedicated output directory."
            )

    target_dir.mkdir(parents=True, exist_ok=True)
    if not clean:
        return
    for name in (*STRICT_REUSE_GROUPS, "none", "non_none"):
        path = target_dir / name
        if path.exists():
            shutil.rmtree(path)
    for filename in (STRICT_REUSE_VISUAL_CHECK_MANIFEST_FILENAME, STRICT_REUSE_VISUAL_CHECK_HTML_FILENAME):
        path = target_dir / filename
        if path.exists():
            path.unlink()


def _resolve_asset_image_path(asset: dict[str, Any], library_root: Path, index: dict[str, Any]) -> Path:
    image_path = _clean_text(asset.get("image_path"))
    if not image_path:
        return library_root / "__missing_image_path__"
    raw_path = Path(image_path)
    if raw_path.is_absolute():
        return raw_path

    candidates = [library_root / raw_path]
    asset_root = _clean_text(index.get("asset_root"))
    if asset_root:
        candidates.append(Path(asset_root).expanduser() / raw_path)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def _visual_check_target_path(
    target_dir: Path,
    group: str,
    asset: dict[str, Any],
    source_path: Path,
    ordinal: int,
) -> Path:
    asset_id = _safe_filename(_clean_text(asset.get("asset_id")) or f"asset_{ordinal:06d}")[:96]
    suffix = source_path.suffix.lower()
    if suffix not in _IMAGE_SUFFIXES:
        suffix = ".png"
    return target_dir / group / f"{ordinal:06d}_{group}_{asset_id}{suffix}"


def _relative_posix(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "asset"


def _load_json_object_or_none(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def _to_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, score))


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_clean_text(item) for item in value if _clean_text(item)]
    text = _clean_text(value)
    return [text] if text else []


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _build_classify_system_prompt() -> str:
    return (
        "你是中文教育课件图片的素材分类器。只根据每个元素的 query 文本判断 strict_reuse_group。\n\n"
        + MATERIAL_CATEGORY_RULES_TEXT
        + "\n\n我会给你一个 JSON 数组，每个元素含 query 字段。"
        "只输出 JSON 数组，长度、顺序与输入一致；每个元素保留 query，"
        "并新增或覆盖 strict_reuse_group 字段（必须是上面 4 个 ID 之一）。"
        "不要输出解释、Markdown 或多余文本。"
    )


def _parse_classify_array(raw: str) -> list[dict[str, Any]]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        import json_repair

        payload = json_repair.loads(text)
    if not isinstance(payload, list):
        raise ValueError("classify response must be a JSON array")
    return [item for item in payload if isinstance(item, dict)]


def classify_records(
    records: list[dict[str, Any]],
    client: Any,
    *,
    query_field: str = "query",
    group_field: str = "strict_reuse_group",
    batch_size: int = 50,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    """Classify each record's query into one of the 4 material categories."""

    if batch_size <= 0:
        raise ValueError("batch_size must be greater than 0")
    items = list(records)
    output: list[dict[str, Any]] = []
    total = len(items)
    for start in range(0, total, batch_size):
        batch = items[start : start + batch_size]
        minimal = [{"query": str(record.get(query_field, "")).strip()} for record in batch]
        messages = [
            {"role": "system", "content": _build_classify_system_prompt()},
            {
                "role": "user",
                "content": "现在请处理下面的 JSON 数组：\n"
                + json.dumps(minimal, ensure_ascii=False, indent=2),
            },
        ]
        max_tokens = max(2048, min(12000, 60 * len(batch) + 1600))
        raw = client.chat(messages=messages, temperature=0.0, max_tokens=max_tokens)
        parsed = _parse_classify_array(raw)
        if len(parsed) != len(batch):
            raise ValueError(f"expected {len(batch)} classify items, got {len(parsed)}")
        for original, generated in zip(batch, parsed):
            item = deepcopy(original)
            item[group_field] = normalize_strict_reuse_group(generated.get("strict_reuse_group"))
            output.append(item)
        if sleep_seconds > 0 and start + batch_size < total:
            time.sleep(sleep_seconds)
    return output
