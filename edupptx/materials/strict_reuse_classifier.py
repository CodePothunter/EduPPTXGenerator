"""Binary reuse-group classification utilities for AI image material libraries."""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from html import escape as html_escape
from pathlib import Path
from typing import Any

from edupptx.materials.ai_image_asset_db import DEFAULT_MATCH_INDEX_FILENAME

STRICT_REUSE_CLASSIFIER_VERSION = 2
STRICT_REUSE_REVIEW_QUEUE_FILENAME = "strict_reuse_review_queue.jsonl"
STRICT_REUSE_REPORT_FILENAME = "strict_reuse_classification_report.json"
STRICT_REUSE_VISUAL_CHECK_MANIFEST_FILENAME = "manifest.json"
STRICT_REUSE_VISUAL_CHECK_HTML_FILENAME = "index.html"
STRICT_REUSE_VISUAL_CHECK_MODE = "strict-reuse-export-check"
STRICT_REUSE_INDEX_DIRNAME = "strict_reuse_indexes"

GENERAL_REUSE_GROUP = "general_reuse"
CONTENT_REUSE_GROUP = "content_reuse"
STRICT_REUSE_GROUPS = (GENERAL_REUSE_GROUP, CONTENT_REUSE_GROUP)
STRICT_REUSE_SPLIT_GROUPS = STRICT_REUSE_GROUPS

LEGACY_GENERAL_REUSE_GROUPS = {"", "none", "general", "general_reuse"}
LEGACY_CONTENT_REUSE_GROUPS = {
    "non_none",
    "content_reuse",
    "content_specific_reuse",
    "exact_reuse",
    "strict_reuse",
    "math_problem",
    "physics_problem",
    "chinese_word_text",
    "chinese_passage_text",
}

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
_EXACT_CONTENT_CONSTRAINT_KINDS = {
    "text",
    "math",
    "physics",
    "formula",
    "equation",
    "table",
    "data",
}
_LEGACY_CONTENT_TERMS = (
    "数学题",
    "算式",
    "计算题",
    "应用题",
    "题目",
    "题干",
    "公式",
    "方程",
    "竖式",
    "横式",
    "几何题",
    "统计表",
    "数据表",
    "物理题",
    "电路图",
    "实验题",
    "受力分析",
    "拼音",
    "生字",
    "字词",
    "词语",
    "组词",
    "偏旁",
    "部首",
    "汉字",
    "会认字",
    "会写字",
    "课文",
    "段落",
    "原文",
    "文本",
    "句子",
    "古诗",
    "诗句",
    "阅读题",
    "选择题",
    "填空题",
)
_LEGACY_BROAD_CONTENT_TERMS = {
    "题目",
    "题干",
    "课文",
    "段落",
    "文本",
    "句子",
    "古诗",
    "诗句",
}
_LEGACY_EXTRA_CONTENT_TERMS = (
    "带拼音",
    "课文片段",
    "课文段落",
    "课文文本",
    "课文节选",
    "课文材料",
    "段落文本",
    "原文段落",
    "语段",
    "选段",
    "摘抄",
    "歌词",
    "宋词",
    "儿歌",
    "字词注释",
    "米字格",
    "田字格",
    "笔顺",
    "笔画",
    "书法",
    "字样",
    "练习题",
    "习作",
    "写作任务",
    "阅读任务",
    "思维导图",
    "七巧板",
    "数位顺序表",
    "数位表格",
    "统计图",
    "统计表格",
    "表格",
    "方格纸",
    "分数墙",
    "坐标",
    "数轴",
    "平面图",
    "展开图",
    "面积",
    "体积",
    "几何",
    "三角形",
    "长方形",
    "正方形",
    "平行四边形",
    "梯形",
    "直线",
    "量角器",
    "刻度",
    "软尺",
    "天平",
    "等式",
    "未知数",
    "笔算",
    "除法",
    "加法",
    "减法",
    "乘法",
    "光路",
    "透镜",
    "光学",
    "实验装置",
    "光具座",
    "电路",
    "受力",
    "压强",
    "浮力",
    "电压",
    "电流",
)
_LEGACY_NEGATED_CONTENT_PATTERNS = (
    r"不要.{0,6}文字",
    r"不含.{0,6}文字",
    r"没有.{0,6}文字",
    r"无.{0,6}文字",
    r"避免.{0,6}文字",
    r"no\s+text",
    r"without\s+text",
)
_LEGACY_DECORATIVE_TEXT_TERMS = (
    "空白卡片",
    "空白标签",
    "空白词卡",
    "空白字卡",
    "装饰边框",
    "边框装饰",
    "文本框",
)
_LEGACY_MATH_EXPRESSION_RE = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:[+×xX*÷/＝=])\s*\d+(?:\.\d+)?)"
    r"|(\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?)"
    r"|(\d+(?:\.\d+)?\s*%)"
    r"|(?<![A-Za-z])([A-Z]\s*=\s*[A-Za-z]{1,3}\d*)(?![A-Za-z])"
)
_LEGACY_EXACT_CONTENT_CONTEXT_TERMS = (
    "数学题",
    "算式",
    "计算题",
    "应用题",
    "练习题",
    "例题",
    "看图列式",
    "题目",
    "题干",
    "解法",
    "分步推导",
    "推导过程",
    "混合运算",
    "小数比较",
    "分解质因数",
    "最大公因数",
    "可能性",
    "总个数",
    "成绩表",
    "统计表",
    "数据表",
    "表格",
    "五局三胜",
    "对阵",
    "课文",
    "课文内容",
    "内容页面",
    "古诗",
    "注释",
    "开放时间",
    "参观路线",
    "行为准则",
    "写作题目",
    "题目提示",
    "文本",
    "象形字",
    "篆书",
    "拼音标注",
    "词语卡片",
    "生字卡片",
    "物理示意图",
    "平面镜",
    "物体AB",
    "光路",
    "电路图",
    "受力分析",
    "配比示意图",
    "关系示意图",
    "局部地图",
    "售价示意图",
    "求总价",
    "购物小票",
    "品名",
    "单价",
    "金额",
    "选句",
    "交际小贴士",
    "小贴士",
    "主动交流",
    "行程路线",
    "时间示意图",
    "方位示意图",
    "方位射线",
    "内部连线",
    "三角形拼图",
    "字体演变",
    "甲骨文",
    "小篆",
    "隶书",
    "书写示范",
    "笔顺",
    "交友场景",
    "对话",
)
_LEGACY_HARD_EXACT_CONTENT_CONTEXT_TERMS = tuple(
    term
    for term in _LEGACY_EXACT_CONTENT_CONTEXT_TERMS
    if term not in {"古诗", "篆书", "文本"}
)
_LEGACY_GENERIC_VISUAL_CONTEXT_TERMS = (
    "简笔画",
    "实物",
    "手势",
    "手指操",
    "三角尺",
    "量角器",
    "显微镜",
    "光学显微镜",
    "田字格",
    "米字格",
    "七巧板",
    "枕头",
    "婴儿服",
    "椰子树",
    "绿植",
    "禾苗",
    "彩纸",
    "折角",
    "平面布局",
    "布局示意图",
    "工笔画",
    "水墨",
    "古风",
    "画像",
    "卡通形象",
    "诗集",
    "封面",
    "意境",
    "山水",
    "传统插画",
    "局部插画",
)
_LEGACY_ART_OR_INCIDENTAL_LABEL_TERMS = (
    "工笔画",
    "水墨",
    "古风",
    "画像",
    "卡通形象",
    "诗集",
    "封面",
    "意境",
    "传统插画",
    "局部插画",
    "印有",
    "字样",
    "上方有",
    "身穿",
)
_LEGACY_CHARACTER_GRID_TERMS = ("米字格", "田字格")
_LEGACY_CHARACTER_GRID_STYLE_TERMS = {
    "拼音",
    "楷书",
    "楷体",
    "红色",
    "灰色",
    "浅蓝灰色",
    "书写",
    "书法",
    "笔顺",
    "笔画",
}
_LEGACY_GENERIC_VISUAL_CATEGORIES = {
    "concept_scene",
    "generic_tool",
    "learning_behavior",
}


def normalize_strict_reuse_group(value: Any, *, default: str = GENERAL_REUSE_GROUP) -> str:
    """Normalize legacy/future labels to the binary reuse groups."""

    text = _clean_text(value).casefold()
    if text in LEGACY_CONTENT_REUSE_GROUPS:
        return CONTENT_REUSE_GROUP
    if text in LEGACY_GENERAL_REUSE_GROUPS:
        return GENERAL_REUSE_GROUP
    return default


def classify_asset_strict_reuse(
    asset: dict[str, Any],
    *,
    infer_legacy_missing: bool = False,
) -> dict[str, Any]:
    """Normalize one asset's upstream reuse-group decision.

    New assets are classified by the LLM/VLM stages and arrive with
    ``strict_reuse_group`` already set to ``general_reuse`` or ``content_reuse``.
    This pass trusts that upstream label and only normalizes its format (legacy
    fine-grained groups are migrated to ``content_reuse``). Keyword-based rules
    are used as a fallback only when no upstream label is present.
    """

    asset_kind = _clean_text(asset.get("asset_kind"))
    if asset_kind and asset_kind != "page_image":
        return _classification(
            GENERAL_REUSE_GROUP,
            1.0,
            ["non_page_image"],
            [],
            reason="non-page images use general reuse routing",
        )

    raw_group = _clean_text(asset.get("strict_reuse_group"))
    if not raw_group:
        raw_group = _clean_text(asset.get("reuse_group"))
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
        group = normalize_strict_reuse_group(raw_group)
        legacy = raw_group.casefold() not in STRICT_REUSE_GROUPS
        signals = [f"legacy_group:{raw_group}"] if legacy else ["upstream_reuse_group"]
        confidence = _to_score(asset.get("strict_reuse_confidence"))
        if confidence is None:
            confidence = 0.86 if group == CONTENT_REUSE_GROUP else 0.9
        reason = (
            f"migrated legacy reuse group {raw_group} to {group}"
            if legacy
            else f"kept upstream reuse group {group}"
        )
        return _classification(group, confidence, signals, [], reason=reason)

    if infer_legacy_missing:
        legacy_signals = _legacy_content_reuse_signals(asset)
        if legacy_signals:
            return _classification(
                CONTENT_REUSE_GROUP,
                0.82,
                legacy_signals,
                [],
                reason="legacy unclassified asset inferred as content_reuse",
            )
        return _classification(
            GENERAL_REUSE_GROUP,
            0.78,
            ["legacy_default_general_reuse"],
            [],
            reason="legacy unclassified asset defaulted to general_reuse",
        )

    return _classification(
        GENERAL_REUSE_GROUP,
        0.5,
        ["missing_upstream_reuse_classification"],
        ["missing_upstream_reuse_classification"],
        reason="no LLM/VLM reuse classification; defaulted to general_reuse",
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
        for legacy_group in sorted((LEGACY_GENERAL_REUSE_GROUPS | LEGACY_CONTENT_REUSE_GROUPS) - set(STRICT_REUSE_GROUPS)):
            if not legacy_group:
                continue
            legacy_path = target_dir / f"{legacy_group}.json"
            if legacy_path.exists():
                legacy_path.unlink()
    for group in STRICT_REUSE_SPLIT_GROUPS:
        group_assets = [
            deepcopy(asset)
            for asset in assets
            if normalize_strict_reuse_group(asset.get("strict_reuse_group")) == group
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
    """Copy assets into general_reuse/content_reuse folders for inspection."""

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
                "content_prompt": _clean_text(asset.get("content_prompt")),
                "vlm_match_quality": asset.get("vlm_match_quality"),
            }
        )
        group_counts[group] += 1

    for group in STRICT_REUSE_GROUPS:
        (target_dir / group).mkdir(parents=True, exist_ok=True)
        group_counts.setdefault(group, 0)

    manifest_path = target_dir / STRICT_REUSE_VISUAL_CHECK_MANIFEST_FILENAME
    html_path = target_dir / STRICT_REUSE_VISUAL_CHECK_HTML_FILENAME
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
        "html_path": str(html_path),
        "missing_items": missing_items,
        "assets": entries,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_render_visual_check_html(manifest), encoding="utf-8")
    return manifest, target_dir


def _legacy_content_reuse_signals(asset: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    text = _legacy_asset_text(asset)
    text_without_negations = _strip_legacy_negated_content_terms(text)
    if _looks_like_blank_decorative_text_asset(text_without_negations):
        return []
    content_keyword_hits = _legacy_content_keyword_hits(text_without_negations)
    character_grid_context = _looks_like_legacy_character_grid_content(asset, text_without_negations)
    hard_exact_context = _has_legacy_hard_exact_content_context(text_without_negations) or character_grid_context
    exact_context = _has_legacy_exact_content_context(text_without_negations) or character_grid_context
    incidental_art_label = _looks_like_legacy_art_or_incidental_label_asset(text_without_negations)
    if incidental_art_label and not hard_exact_context:
        content_keyword_hits = []
        exact_context = False
    generic_visual_context = _looks_like_legacy_generic_visual_asset(asset, text_without_negations)
    if exact_context:
        signals.append("legacy_content_context")
    if content_keyword_hits and (exact_context or not generic_visual_context):
        signals.append("legacy_content_keyword")
    if _LEGACY_MATH_EXPRESSION_RE.search(text_without_negations):
        signals.append("legacy_math_expression")
    for constraint in _constraint_items(asset):
        kind = _clean_text(constraint.get("kind")).casefold()
        importance = _constraint_importance(constraint)
        if importance < 2:
            continue
        value = _clean_text(constraint.get("value")).casefold()
        if kind in {"formula", "equation", "table", "data"}:
            signals.append(f"legacy_exact_constraint:{kind}")
        elif kind == "text" and (
            not _looks_like_legacy_gesture_label(value, text_without_negations)
            and (
                exact_context
                or (content_keyword_hits and not generic_visual_context)
                or _looks_like_substantive_text_content(value, text_without_negations)
            )
        ):
            signals.append("legacy_exact_constraint:text")
        elif kind == "math" and (
            exact_context
            or (content_keyword_hits and not generic_visual_context)
            or _LEGACY_MATH_EXPRESSION_RE.search(value)
            or _looks_like_math_content(value, text_without_negations)
        ):
            signals.append("legacy_exact_constraint:math")
        elif kind == "physics" and (
            exact_context
            or (content_keyword_hits and not generic_visual_context)
            or _looks_like_physics_content(value, text_without_negations)
        ):
            signals.append("legacy_exact_constraint:physics")
    return _dedupe(signals)


def _legacy_asset_text(asset: dict[str, Any]) -> str:
    pieces: list[str] = []
    for key in (
        "content_prompt",
    ):
        pieces.append(_clean_text(asset.get(key)))
    for key in ("core_keywords",):
        value = asset.get(key)
        if isinstance(value, list):
            pieces.extend(_clean_text(item) for item in value)
    aliases = asset.get("semantic_aliases")
    if isinstance(aliases, dict):
        for key, values in aliases.items():
            pieces.append(_clean_text(key))
            if isinstance(values, list):
                pieces.extend(_clean_text(item) for item in values)
    for constraint in _constraint_items(asset):
        pieces.append(_clean_text(constraint.get("value")))
    return " ".join(piece for piece in pieces if piece).casefold()


def _strip_legacy_negated_content_terms(text: str) -> str:
    stripped = text
    for pattern in _LEGACY_NEGATED_CONTENT_PATTERNS:
        stripped = re.sub(pattern, " ", stripped, flags=re.IGNORECASE)
    return _clean_text(stripped).casefold()


def _looks_like_blank_decorative_text_asset(text: str) -> bool:
    return any(term.casefold() in text for term in _LEGACY_DECORATIVE_TEXT_TERMS)


def _legacy_content_keyword_hits(text: str) -> list[str]:
    terms = [
        term
        for term in (*_LEGACY_CONTENT_TERMS, *_LEGACY_EXTRA_CONTENT_TERMS)
        if term and term not in _LEGACY_BROAD_CONTENT_TERMS
    ]
    return [term for term in _dedupe(terms) if term.casefold() in text]


def _has_legacy_exact_content_context(text: str) -> bool:
    return any(term.casefold() in text for term in _LEGACY_EXACT_CONTENT_CONTEXT_TERMS)


def _has_legacy_hard_exact_content_context(text: str) -> bool:
    return any(term.casefold() in text for term in _LEGACY_HARD_EXACT_CONTENT_CONTEXT_TERMS)


def _looks_like_legacy_character_grid_content(asset: dict[str, Any], text: str) -> bool:
    if not any(term.casefold() in text for term in _LEGACY_CHARACTER_GRID_TERMS):
        return False
    for constraint in _constraint_items(asset):
        if _clean_text(constraint.get("kind")).casefold() != "text":
            continue
        if _constraint_importance(constraint) < 2:
            continue
        value = _clean_text(constraint.get("value"))
        if not value:
            continue
        if value.casefold() in {term.casefold() for term in _LEGACY_CHARACTER_GRID_STYLE_TERMS}:
            continue
        return True
    return False


def _looks_like_legacy_art_or_incidental_label_asset(text: str) -> bool:
    return any(term.casefold() in text for term in _LEGACY_ART_OR_INCIDENTAL_LABEL_TERMS)


def _looks_like_legacy_generic_visual_asset(asset: dict[str, Any], text: str) -> bool:
    if _has_legacy_exact_content_context(text):
        return False
    category = _clean_text(asset.get("asset_category")).casefold()
    if category in _LEGACY_GENERIC_VISUAL_CATEGORIES:
        return True
    return any(term.casefold() in text for term in _LEGACY_GENERIC_VISUAL_CONTEXT_TERMS)


def _looks_like_legacy_gesture_label(value: str, text: str) -> bool:
    if not any(term.casefold() in text for term in ("手势", "手指操")):
        return False
    compact = re.sub(r"\s+", "", value).casefold()
    if not compact:
        return False
    return bool(re.fullmatch(r"[a-zɑüāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ]{1,3}", compact))


def _looks_like_substantive_text_content(value: str, text: str) -> bool:
    if not value:
        return False
    if "《" in text and "》" in text and any(term in text for term in ("选段", "摘抄", "节选", "原文")):
        return True
    return any(
        term in text
        for term in (
            "带拼音",
            "课文片段",
            "课文段落",
            "课文文本",
            "课文节选",
            "段落文本",
            "语段",
            "选段",
            "摘抄",
            "歌词",
            "字词注释",
            "阅读任务",
            "写作任务",
            "习作",
        )
    )


def _looks_like_math_content(value: str, text: str) -> bool:
    if not value:
        return False
    if _LEGACY_MATH_EXPRESSION_RE.search(value):
        return True
    return any(
        term in text
        for term in (
            "数学题",
            "算式",
            "计算题",
            "应用题",
            "练习题",
            "填空题",
            "数位",
            "统计图",
            "统计表",
            "数轴",
            "方程",
            "等式",
            "未知数",
            "笔算",
            "除法",
            "加法",
            "减法",
            "乘法",
        )
    )


def _looks_like_physics_content(value: str, text: str) -> bool:
    if not value:
        return False
    return any(
        term in text
        for term in (
            "物理题",
            "电路图",
            "实验题",
            "受力分析",
            "光路",
            "透镜",
            "光学",
            "实验装置",
            "光具座",
            "电压",
            "电流",
            "电阻",
            "压强",
            "浮力",
        )
    )


def _constraint_items(asset: dict[str, Any]) -> list[dict[str, Any]]:
    raw_constraints = asset.get("constraints")
    if not isinstance(raw_constraints, list):
        return []
    return [item for item in raw_constraints if isinstance(item, dict)]


def _constraint_importance(constraint: dict[str, Any]) -> int:
    try:
        return int(float(constraint.get("importance")))
    except (TypeError, ValueError):
        return 0


def _is_missing_upstream_default(asset: dict[str, Any]) -> bool:
    signals = {_clean_text(item) for item in _as_string_list(asset.get("strict_reuse_signals"))}
    if "missing_upstream_reuse_classification" in signals:
        return True
    reason = _clean_text(asset.get("strict_reuse_reason")).casefold()
    return "missing_upstream_reuse_classification" in reason or "defaulted to general_reuse" in reason


def _is_legacy_unclassified_inference(asset: dict[str, Any]) -> bool:
    signals = {_clean_text(item) for item in _as_string_list(asset.get("strict_reuse_signals"))}
    if any(
        signal == "legacy_default_general_reuse"
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
        "content_prompt": _clean_text(asset.get("content_prompt")),
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


def _render_visual_check_html(manifest: dict[str, Any]) -> str:
    cards_by_group = {group: [] for group in STRICT_REUSE_GROUPS}
    for entry in manifest.get("assets", []):
        if not isinstance(entry, dict):
            continue
        group = normalize_strict_reuse_group(entry.get("strict_reuse_group"))
        if entry.get("copied"):
            image_html = (
                f'<img loading="lazy" src="{html_escape(_clean_text(entry.get("output_image_path")))}" '
                f'alt="{html_escape(_clean_text(entry.get("asset_id")))}">'
            )
        else:
            image_html = '<div class="missing">missing image</div>'
        cards_by_group[group].append(
            "\n".join(
                [
                    '<article class="card">',
                    image_html,
                    f'<div class="meta"><strong>{html_escape(group)}</strong></div>',
                    f'<div class="id">{html_escape(_clean_text(entry.get("asset_id")))}</div>',
                    f'<div class="prompt">{html_escape(_clean_text(entry.get("content_prompt")))}</div>',
                    "</article>",
                ]
            )
        )

    def section(group: str) -> str:
        count = manifest.get("group_counts", {}).get(group, 0)
        cards = "\n".join(cards_by_group[group])
        return f"<section><h2>{html_escape(group)} ({count})</h2><div class=\"grid\">{cards}</div></section>"

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>Reuse Group Visual Check</title>",
            "<style>",
            "body{font-family:Arial,'Microsoft YaHei',sans-serif;margin:24px;background:#f6f7f9;color:#20242a}",
            "h1{font-size:24px;margin:0 0 8px} h2{font-size:20px;margin:28px 0 12px}",
            ".summary{color:#56606b;margin-bottom:18px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}",
            ".card{background:white;border:1px solid #dde2e8;border-radius:8px;padding:10px;box-shadow:0 1px 2px rgba(0,0,0,.04)}",
            ".card img{width:100%;height:160px;object-fit:contain;background:#eef1f4;border-radius:4px}",
            ".missing{height:160px;display:flex;align-items:center;justify-content:center;background:#f2dede;color:#8a1f11;border-radius:4px}",
            ".meta{font-size:13px;margin-top:8px;color:#334155}.id{font-size:12px;color:#667085;margin-top:4px;word-break:break-all}",
            ".prompt{font-size:13px;line-height:1.35;margin-top:6px;max-height:72px;overflow:auto}",
            "</style>",
            "</head>",
            "<body>",
            "<h1>Reuse Group Visual Check</h1>",
            (
                '<div class="summary">'
                f"Assets: {manifest.get('asset_count', 0)} | "
                f"Copied: {manifest.get('copied_count', 0)} | "
                f"Missing: {manifest.get('missing_image_count', 0)}"
                "</div>"
            ),
            section(CONTENT_REUSE_GROUP),
            section(GENERAL_REUSE_GROUP),
            "</body>",
            "</html>",
        ]
    )


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
