"""Offline builder for the generated AI image asset database."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from edupptx.materials.reuse_policy import (
    DF_RATIO_MIN_LIBRARY_SIZE,
    MEDIUM_EMBEDDING_REVIEW_THRESHOLD,
    STRICT_EMBEDDING_REVIEW_THRESHOLD,
    STRICT_SEMANTIC_GRAY_BM25_THRESHOLD,
    STRICT_SEMANTIC_GRAY_REVIEW_THRESHOLD,
    compute_keyword_df_ratio,
    evaluate_aspect_transform,
    evaluate_reuse_filter,
    has_precision_signal,
    normalize_asset_metadata,
    normalize_constraints,
    normalize_reuse_policy_fields,
    reuse_threshold_for_target as policy_reuse_threshold_for_target,
)

SCHEMA_VERSION = 1
KEYWORD_SCHEMA_VERSION = 13
DEFAULT_DB_FILENAME = "ai_image_asset_db.json"
DEFAULT_MATCH_INDEX_FILENAME = "ai_image_match_index.json"
DEFAULT_EMBEDDING_INDEX_FILENAME = "ai_image_embedding_index.npz"
DEFAULT_EMBEDDING_META_FILENAME = "ai_image_embedding_meta.json"
DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
MATCH_INDEX_SCHEMA_VERSION = 13
EMBEDDING_INDEX_SCHEMA_VERSION = 3
DEFAULT_KEYWORD_BATCH_SIZE = 12
DEFAULT_LIBRARY_IMAGE_DIR = "ai_images"
REUSE_MANIFEST_FILENAME = "ai_image_reuse_manifest.json"
REUSE_DEBUG_FILENAME = "ai_image_reuse_debug.json"
KEYWORD_REUSE_RULES_REFERENCE = Path(__file__).resolve().parent / "Reference" / "ai_image_reuse_metadata_rules.md"
REUSE_REVIEW_SCORE_RULES_REFERENCE = Path(__file__).resolve().parent / "Reference" / "ai_image_reuse_review_score_rules.md"
DEFAULT_REUSE_CANDIDATE_LIMIT = 5
DEFAULT_MIN_REUSE_KEYWORD_SCORE: float | None = None
DEFAULT_HYBRID_RETRIEVAL_POOL_SIZE = 20
DEFAULT_RRF_K = 60
HYBRID_BM25_WEIGHT = 0.50
HYBRID_EMBEDDING_WEIGHT = 0.35
HYBRID_SUBSTRING_WEIGHT = 0.15
BM25_GRAY_REUSE_THRESHOLD = 0.23
EMBEDDING_GRAY_REUSE_THRESHOLD = 0.72
STRICT_REUSE_MAX_PER_SESSION = 2
REUSE_REVIEW_ACCEPT_SCORE_THRESHOLD = 0.70
# Per-query LLM review budget. Caps the number of llm_review calls made
# for a single target so a noisy candidate pool can't burn the LLM on a
# long tail of equivalent-quality candidates after the top contender has
# already been judged. Empirically 7/26 reviewed queries triggered ≥3
# LLM calls and only the first or second usually contains the accepted
# match; raising K beyond 3 yields diminishing returns.
MAX_LLM_REVIEWS_PER_QUERY = 3
# Policy reasons under which the LLM review rules deterministically yield
# score ≤ accept_threshold. Skipping the LLM call here saves budget with
# no information loss — the structural conflict that triggered the review
# is also what the LLM scoring rules would penalize, and the LLM has no
# extra evidence available beyond the constraints already inspected by
# the deterministic gate. See ai_image_reuse_review_score_rules.md for
# the matching scoring caps (candidate extra teaching content ≤ 0.55,
# missing exact text/math/physics fact necessarily low-score, etc).
DETERMINISTIC_LLM_REJECT_REASONS = frozenset({
    "candidate_extra_teaching_content",
    "candidate_extra_strong_constraints",
})
LOGGER = logging.getLogger(__name__)

# Process-wide DF-ratio cache. Keyed by (library_root_str, db_mtime_ns) so
# the cache invalidates automatically when the on-disk database is
# regenerated. Library traversal is O(N keywords) — cheap once but adds
# up across the dozens of reuse queries in a single PPT generation, so
# the cache pays for itself within the first session.
_DF_RATIO_CACHE: dict[tuple[str, int], dict[str, float]] = {}

BACKGROUND_REUSE_GATE_THRESHOLDS = {
    "keyword_min": 0.10,
    "embedding_min": 0.42,
    "keyword_high": 0.38,
    "embedding_high": 0.78,
    "keyword_gray_high": 0.26,
    "embedding_gray_low": 0.55,
    "embedding_gray_high": 0.68,
    "keyword_gray_low": 0.16,
}
PAGE_IMAGE_REUSE_GATE_THRESHOLDS = {
    "loose": {
        "keyword_min": 0.12,
        "embedding_min": 0.46,
        "keyword_high": 0.54,
        "embedding_high": 0.78,
        "keyword_gray_high": 0.28,
        "embedding_gray_low": 0.56,
        "embedding_gray_high": 0.68,
        "keyword_gray_low": 0.18,
    },
    "medium": {
        "keyword_min": 0.14,
        "embedding_min": 0.50,
        "keyword_high": 0.58,
        "embedding_high": 0.80,
        "keyword_gray_high": 0.30,
        "embedding_gray_low": 0.60,
        "embedding_gray_high": 0.70,
        "keyword_gray_low": 0.20,
    },
    "strict_literary": {
        "keyword_min": 0.12,
        "embedding_min": 0.50,
        "keyword_high": 0.58,
        "embedding_high": 0.80,
        "keyword_gray_high": 0.32,
        "embedding_gray_low": 0.60,
        "embedding_gray_high": 0.72,
        "keyword_gray_low": 0.18,
    },
    "strict_knowledge": {
        "keyword_min": 0.20,
        "embedding_min": 0.55,
        "keyword_high": 0.64,
        "embedding_high": 0.80,
        "keyword_gray_high": 0.45,
        "embedding_gray_low": 0.70,
        "embedding_gray_high": 0.76,
        "keyword_gray_low": 0.25,
    },
}
TEXT_OVERLAP_REVIEW_THRESHOLD = 0.15
TEXT_OVERLAP_EMBEDDING_THRESHOLD = 0.78
PAGE_IMAGE_SCORE_GATE_REVIEW_REASONS = {
    "keyword_high_review",
    "embedding_high_review",
    "text_overlap_embedding_review",
    "keyword_led_gray_review",
    "embedding_led_gray_review",
}

CONTENT_PROMPT_REUSE_WEIGHT = 0.85
ROUTE_REUSE_WEIGHT = 0.05
ASPECT_REUSE_WEIGHT = 0.05
LIGHT_CONTEXT_REUSE_WEIGHT = 0.05
BACKGROUND_CONTENT_PROMPT_REUSE_WEIGHT = 0.85
BACKGROUND_COLOR_BIAS_REUSE_WEIGHT = 0.15

VISUAL_GENERIC_REUSE_THRESHOLD = 0.28
BACKGROUND_REUSE_THRESHOLD = 0.38

_EMBEDDING_MODEL_CACHE: dict[str, Any] = {}

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_TOPIC_REF_WRAPPER_RE = re.compile(r"[《〈「『“\"]([^《》〈〉「」『』“”\"']{1,40})[》〉」』”\"]")
_TOPIC_REF_LEADING_NOISE_RE = re.compile(
    r"^(?:小学|初中|高中)?(?:[一二三四五六七八九十\d]+年级|高[一二三\d]|初[一二三\d]|小[一二三四五六\d])"
)
_TOPIC_REF_SUBJECT_PREFIXES = (
    "语文",
    "数学",
    "英语",
    "物理",
    "化学",
    "生物",
    "历史",
    "地理",
    "政治",
    "道德与法治",
    "科学",
    "信息技术",
)
_TOPIC_REF_TRAILING_NOISE = (
    "课文教学",
    "教学课件",
    "课件",
    "教学设计",
    "单元复习",
    "专题复习",
    "复习课",
    "讲解",
    "导入",
    "练习",
    "教学",
    "课程",
    "PPT",
    "ppt",
)
_VLM_RICH_ASSET_FIELDS = (
    "vlm_schema_version",
    "vlm_verified",
    "vlm_verified_at",
    "vlm_model",
    "vlm_verified_constraints",
    "vlm_missing_from_prompt",
    "vlm_visual_aliases",
    "vlm_visual_style",
    "vlm_match_quality",
    "vlm_needs_regeneration",
)
_BACKGROUND_ROUTE_FIELDS = (
    "template_family",
    "style_name",
    "palette_id",
    "primary_color",
    "secondary_color",
    "accent_color",
    "card_bg_color",
    "secondary_bg_color",
    "background_color_bias",
)
_BACKGROUND_ROUTE_MATCH_FIELDS = (
    "background_color_bias",
)
_PAGE_TYPE_CONTEXT_SUMMARIES = {
    "cover": "作为封面主视觉，建立课程主题和导入氛围",
    "toc": "作为目录页辅助导览插图，引导学生理解本节课学习路径",
    "content": "作为内容页辅助说明插图，帮助学生理解本页知识点",
    "exercise": "作为练习页辅助插图，帮助学生理解互动任务",
    "summary": "作为总结页辅助记忆插图，帮助学生回顾核心内容",
    "closing": "作为结束页辅助插图，形成课程收束氛围",
}
_GENERIC_CORE_NOISE = {
    "插画",
    "教学插画",
    "编辑感",
    "风格",
    "简洁",
    "清晰",
    "简洁清晰",
    "高清",
    "背景",
    "场景",
    "示意图",
    "图片",
}
_GENERIC_STYLE_NOISE = {
    "插画",
    "教学插画",
    "编辑感",
    "风格",
    "高清",
    "背景",
    "图片",
}
_CORE_GENERIC_EXACT = {
    "ppt",
    "ai",
    "logo",
    "图标",
    "插画",
    "配图",
    "主图",
    "背景",
    "背景简洁",
    "风格统一",
    "无文字",
    "无文字水印",
    "教学插画",
    "教学示意",
    "语文教学",
    "高年级",
    "低年级",
    "高年级风格",
    "低年级风格",
    "高年级编辑感",
}
# Union of the three existing noise sets, used as the stopword input for
# has_precision_signal. These terms saturate the library (every asset
# uses them as a style descriptor), so two assets sharing any one of
# them does not constitute precision evidence — only sharing a more
# discriminative keyword does. Casefolded for direct set membership
# checks against normalized keyword tokens.
_PRECISION_SIGNAL_STOPWORDS = frozenset(
    s.casefold() for s in (_GENERIC_CORE_NOISE | _GENERIC_STYLE_NOISE | _CORE_GENERIC_EXACT)
)
_CORE_STYLE_MARKERS = (
    "风格",
    "画风",
    "色调",
    "构图",
    "质感",
    "肌理",
    "水印",
    "logo",
)
_STYLE_DESCRIPTOR_MARKERS = (
    "卡通",
    "手绘",
    "写实",
    "抽象",
    "简约",
    "极简",
    "线稿",
    "淡彩",
    "水彩",
    "扁平",
    "绘本",
    "编辑感",
    "高年级",
    "低年级",
    "教学",
)
_VISUAL_FORM_MARKERS = (
    "插画",
    "图标",
    "配图",
    "主图",
    "背景",
    "示意图",
)
_CORE_USAGE_MARKERS = (
    "适合",
    "用于",
    "教学用",
    "教学插画",
    "教学配图",
    "教学示意",
    "课堂导入",
    "无多余",
    "不要",
    "避免",
)
_CHINESE_GRADE_DIGITS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_GRADE_NAMES = {
    1: "一年级",
    2: "二年级",
    3: "三年级",
    4: "四年级",
    5: "五年级",
    6: "六年级",
    7: "七年级",
    8: "八年级",
    9: "九年级",
    10: "十年级",
    11: "十一年级",
    12: "十二年级",
}
_LOW_GRADE_BAND = "低年级"
_HIGH_GRADE_BAND = "高年级"
_GRADE_RE = re.compile(
    r"(小学[一二三四五六0-9]+年级|初中[一二三0-9]+年级|高中[一二三0-9]+年级|"
    r"[一二三四五六七八九十0-9]+年级|初[一二三123]|高[一二三123]|大[一二三四1234])"
)

_SUBJECT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("语文", ("语文", "课文", "作文", "阅读", "古诗", "文言文", "文学", "拼音", "汉字")),
    ("数学", ("数学", "代数", "几何", "函数", "方程", "勾股", "概率", "统计")),
    ("英语", ("英语", "英文", "english", "grammar", "vocabulary")),
    ("物理", ("物理", "力学", "电磁", "光学", "热学", "运动", "电路")),
    ("化学", ("化学", "元素", "分子", "原子", "化合", "实验")),
    ("生物", ("生物", "细胞", "生态", "光合作用", "遗传", "生命科学")),
    ("历史", ("历史", "朝代", "战争", "革命", "文明史")),
    ("地理", ("地理", "地图", "气候", "地形", "经纬", "区域")),
)


def build_ai_image_asset_db(output_root: str | Path) -> dict[str, Any]:
    """Scan rendered sessions and return the generated-image asset database.

    The persisted fields stay focused on reusable image content:
    prompt text, route metadata, normalized prompt, context summary,
    teaching intent, grade/subject, and normalized reuse constraints.
    """

    root = Path(output_root).expanduser().resolve()
    assets: list[dict[str, Any]] = []
    warnings: list[str] = []

    for session_dir in _iter_session_dirs(root):
        plan_path = session_dir / "plan.json"
        try:
            plan = _read_json(plan_path)
        except Exception as exc:
            warnings.append(f"skip {plan_path}: {exc}")
            continue

        context = _extract_context(plan)
        materials_dir = session_dir / "materials"
        reused_image_paths = _load_reused_image_paths(session_dir)

        background_asset = _build_background_asset(
            root=root,
            session_dir=session_dir,
            plan_path=plan_path,
            materials_dir=materials_dir,
            context=context,
            plan=plan,
            reused_image_paths=reused_image_paths,
        )
        if background_asset is not None:
            assets.append(background_asset)

        pages = plan.get("pages") if isinstance(plan, dict) else None
        if not isinstance(pages, list):
            warnings.append(f"skip image slots in {plan_path}: pages is not a list")
            continue

        for page_index, page in enumerate(pages):
            if not isinstance(page, dict):
                continue
            for asset in _iter_page_image_assets(
                root=root,
                session_dir=session_dir,
                plan_path=plan_path,
                materials_dir=materials_dir,
                context=context,
                page=page,
                page_index=page_index,
                reused_image_paths=reused_image_paths,
            ):
                assets.append(asset)

    assets.sort(
        key=lambda item: (
            _clean_text(item.get("asset_kind")),
            _clean_text(item.get("image_path")),
            _clean_text(item.get("asset_id")),
        )
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "output_root": str(root),
        "asset_count": len(assets),
        "assets": assets,
        "warnings": warnings,
    }


def write_ai_image_asset_db(
    output_root: str | Path,
    db_path: str | Path | None = None,
    *,
    keyword_client: Any | None = None,
    keyword_batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
) -> tuple[dict[str, Any], Path]:
    """Build and write the database JSON file."""

    root = Path(output_root).expanduser().resolve()
    target = Path(db_path).expanduser().resolve() if db_path else root / DEFAULT_DB_FILENAME
    db = build_ai_image_asset_db(root)
    if keyword_client is not None:
        enrich_ai_image_asset_db_keywords(
            db,
            keyword_client,
            batch_size=keyword_batch_size,
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    write_ai_image_match_index(db, target.parent)
    return db, target


def update_ai_image_asset_library(
    session_dir: str | Path,
    library_dir: str | Path,
    *,
    db_filename: str = DEFAULT_DB_FILENAME,
    keyword_client: Any | None = None,
    keyword_batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
) -> tuple[dict[str, Any], Path]:
    """Copy a session's AI-generated images into the reusable library and merge metadata.

    The library is append/update only: assets are keyed by asset_id and the
    central JSON stays at ``library_dir/db_filename``. This does not perform
    reuse matching; it only ingests newly generated materials for future runs.
    """

    session_root = Path(session_dir).expanduser().resolve()
    library_root = Path(library_dir).expanduser().resolve()
    db_path = library_root / db_filename
    library_root.mkdir(parents=True, exist_ok=True)

    session_db = build_ai_image_asset_db(session_root)
    if keyword_client is not None:
        enrich_ai_image_asset_db_keywords(
            session_db,
            keyword_client,
            batch_size=keyword_batch_size,
        )

    ingested_db = _copy_db_assets_to_library(
        session_db,
        session_root=session_root,
        library_root=library_root,
    )
    existing_db = _read_existing_db(db_path)
    merged_db = _merge_asset_library_db(
        existing_db,
        ingested_db,
        library_root=library_root,
    )
    db_path.write_text(json.dumps(merged_db, ensure_ascii=False, indent=2), encoding="utf-8")
    write_ai_image_match_index(merged_db, library_root)
    return merged_db, db_path


def ingest_ai_image_asset_library_from_output(
    output_root: str | Path,
    library_dir: str | Path,
    *,
    db_filename: str = DEFAULT_DB_FILENAME,
    keyword_client: Any | None = None,
    keyword_batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    """Ingest all output sessions into the reusable AI image asset library.

    Unlike ``write_ai_image_asset_db()``, this copies images into the central
    library image directory and merges each session into ``library_dir``.
    """

    root = Path(output_root).expanduser().resolve()
    library_root = Path(library_dir).expanduser().resolve()
    db_path = library_root / db_filename
    library_root.mkdir(parents=True, exist_ok=True)

    sessions = list(_iter_session_dirs(root))
    report: dict[str, Any] = {
        "output_root": str(root),
        "library_dir": str(library_root),
        "asset_root": str(library_root),
        "db_path": str(db_path),
        "session_count": len(sessions),
        "processed_sessions": [],
        "failed_sessions": [],
        "warnings": [],
    }
    merged_db = _read_existing_db(db_path)

    for session_dir in sessions:
        try:
            merged_db, _target = update_ai_image_asset_library(
                session_dir,
                library_root,
                db_filename=db_filename,
                keyword_client=keyword_client,
                keyword_batch_size=keyword_batch_size,
            )
        except Exception as exc:
            message = f"{session_dir}: {exc}"
            report["failed_sessions"].append(message)
            report["warnings"].append(f"session ingest failed: {message}")
            continue

        session_asset_count = int(merged_db.get("asset_count") or 0)
        report["processed_sessions"].append(
            {
                "session_dir": str(session_dir),
                "asset_count": session_asset_count,
            }
        )

    if not db_path.exists():
        merged_db = _merge_asset_library_db(
            {},
            {"schema_version": SCHEMA_VERSION, "assets": [], "warnings": []},
            library_root=library_root,
        )
        db_path.write_text(json.dumps(merged_db, ensure_ascii=False, indent=2), encoding="utf-8")
        write_ai_image_match_index(merged_db, library_root)

    report["asset_count"] = int(merged_db.get("asset_count") or 0)
    report["warning_count"] = len(_as_string_list(merged_db.get("warnings"))) + len(report["warnings"])
    return merged_db, db_path, report


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

    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": MATCH_INDEX_SCHEMA_VERSION,
        "built_at": now,
        "updated_at": now,
        "db_schema_version": int(db.get("schema_version") or 0),
        "asset_root": str(root) if root is not None else _clean_text(db.get("output_root")),
        "input_asset_count": len(raw_assets) if isinstance(raw_assets, list) else 0,
        "asset_count": len(deduped_assets),
        "assets": deduped_assets,
        "warnings": _dedupe_warnings(warnings),
    }


def write_ai_image_match_index(
    db: dict[str, Any],
    library_dir: str | Path,
    *,
    index_filename: str = DEFAULT_MATCH_INDEX_FILENAME,
) -> tuple[dict[str, Any], Path]:
    """Write the slim matching index next to the rich asset database."""

    root = Path(library_dir).expanduser().resolve()
    index = build_ai_image_match_index(db, library_root=root)
    embedding_report = write_ai_image_embedding_index(index, root)
    if embedding_report:
        index["embedding_index"] = embedding_report
    target = root / index_filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index, target


def write_ai_image_embedding_index(
    match_index: dict[str, Any],
    library_dir: str | Path,
    *,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    index_filename: str = DEFAULT_EMBEDDING_INDEX_FILENAME,
    meta_filename: str = DEFAULT_EMBEDDING_META_FILENAME,
) -> dict[str, Any]:
    """Write the vector sidecar index used by hybrid image reuse retrieval."""

    root = Path(library_dir).expanduser().resolve()
    model_name = _embedding_model_name(model_name)
    if _embedding_disabled():
        return {
            "enabled": False,
            "reason": "disabled_by_environment",
            "model": model_name,
        }

    assets = match_index.get("assets")
    if not isinstance(assets, list) or not assets:
        return {
            "enabled": False,
            "reason": "empty_match_index",
            "model": model_name,
        }

    rows: list[tuple[str, str]] = []
    background_color_bias_rows: list[tuple[str, str]] = []
    context_rows: list[tuple[str, str]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_id = _clean_text(asset.get("asset_id"))
        text = _asset_embedding_text(asset)
        if asset_id and text:
            rows.append((asset_id, text))
        color_bias = _background_color_bias(asset) if _is_background_asset(asset) else ""
        if asset_id and color_bias:
            background_color_bias_rows.append((asset_id, color_bias))
        context_text = _candidate_context_embedding_text(asset)
        if asset_id and context_text:
            context_rows.append((asset_id, context_text))
    if not rows:
        return {
            "enabled": False,
            "reason": "empty_embedding_text",
            "model": model_name,
        }

    try:
        vectors = _encode_embedding_texts([text for _asset_id, text in rows], model_name=model_name, query=False)
        background_color_bias_vectors = None
        if background_color_bias_rows:
            background_color_bias_vectors = _encode_embedding_texts(
                [text for _asset_id, text in background_color_bias_rows],
                model_name=model_name,
                query=False,
            )
        context_vectors = None
        if context_rows:
            context_vectors = _encode_embedding_texts(
                [text for _asset_id, text in context_rows],
                model_name=model_name,
                query=False,
            )
        import numpy as np
    except Exception as exc:
        return {
            "enabled": False,
            "reason": "embedding_build_failed",
            "model": model_name,
            "warnings": [f"AI image embedding index skipped: {str(exc)[:180]}"],
        }

    index_path = root / index_filename
    meta_path = root / meta_filename
    index_path.parent.mkdir(parents=True, exist_ok=True)
    asset_ids = np.asarray([asset_id for asset_id, _text in rows], dtype=str)
    payload: dict[str, Any] = {
        "asset_ids": asset_ids,
        "vectors": vectors.astype("float32"),
    }
    if background_color_bias_rows and background_color_bias_vectors is not None:
        payload["background_color_bias_asset_ids"] = np.asarray(
            [asset_id for asset_id, _text in background_color_bias_rows],
            dtype=str,
        )
        payload["background_color_bias_vectors"] = background_color_bias_vectors.astype("float32")
    if context_rows and context_vectors is not None:
        payload["context_asset_ids"] = np.asarray([asset_id for asset_id, _text in context_rows], dtype=str)
        payload["context_vectors"] = context_vectors.astype("float32")
    np.savez_compressed(index_path, **payload)

    meta = {
        "schema_version": EMBEDDING_INDEX_SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "index_filename": index_filename,
        "asset_count": len(rows),
        "background_color_bias_asset_count": len(background_color_bias_rows),
        "context_asset_count": len(context_rows),
        "vector_dim": int(vectors.shape[1]) if len(vectors.shape) == 2 else 0,
        "assets": [
            {"asset_id": asset_id, "embedding_text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]}
            for asset_id, text in rows
        ],
        "background_color_bias_assets": [
            {"asset_id": asset_id, "embedding_text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]}
            for asset_id, text in background_color_bias_rows
        ],
        "context_assets": [
            {"asset_id": asset_id, "embedding_text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]}
            for asset_id, text in context_rows
        ],
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "enabled": True,
        "model": model_name,
        "index_path": str(index_path),
        "meta_path": str(meta_path),
        "asset_count": len(rows),
        "vector_dim": meta["vector_dim"],
    }


def _get_library_df_ratio(
    library_root: Path,
    db_path: Path,
    assets: list[Any] | None,
) -> dict[str, float]:
    """Return cached keyword DF ratio for the given library.

    Returns an empty dict (≡ "DF unavailable") when the library is below
    DF_RATIO_MIN_LIBRARY_SIZE so downstream gates fall back to legacy
    behavior on small libraries where statistics are unreliable. The
    cache key includes db_path mtime so a library rebuild invalidates
    stale entries automatically.
    """

    if not isinstance(assets, list) or not assets:
        return {}
    page_image_count = sum(
        1 for a in assets if isinstance(a, dict) and a.get("asset_kind") == "page_image"
    )
    if page_image_count < DF_RATIO_MIN_LIBRARY_SIZE:
        return {}
    try:
        mtime_ns = db_path.stat().st_mtime_ns if db_path.exists() else 0
    except OSError:
        mtime_ns = 0
    key = (str(library_root), mtime_ns)
    cached = _DF_RATIO_CACHE.get(key)
    if cached is not None:
        return cached
    ratio = compute_keyword_df_ratio(assets)
    _DF_RATIO_CACHE[key] = ratio
    return ratio


def find_reusable_ai_image_asset(
    *,
    library_dir: str | Path,
    asset_kind: str,
    prompt: str,
    prompt_route: dict[str, Any] | None = None,
    background_route: dict[str, Any] | None = None,
    theme: str = "",
    grade: str = "",
    subject: str = "",
    page_title: str = "",
    page_type: str = "",
    role: str = "",
    aspect_ratio: str = "",
    keyword_client: Any | None = None,
    candidate_limit: int = DEFAULT_REUSE_CANDIDATE_LIMIT,
    min_keyword_score: float | None = DEFAULT_MIN_REUSE_KEYWORD_SCORE,
    debug_path: str | Path | None = None,
    debug_context: dict[str, Any] | None = None,
    reuse_session_state: dict[str, Any] | None = None,
    llm_review_enabled: bool = True,
    reuse_debug_mode: str = "",
) -> dict[str, Any] | None:
    """Find a reusable AI image asset from the central library.

    BM25 remains the precision signal, while optional Qwen embedding and
    substring retrieval provide gray-zone recall through RRF fusion. When a
    strict reuse policy needs semantic confirmation, the same LLM client can
    perform a bounded second-stage review.
    """

    library_root = Path(library_dir).expanduser().resolve()
    db_path = library_root / DEFAULT_DB_FILENAME
    db = _read_existing_db(db_path)
    index, match_index_path = _read_match_index_or_build(library_root, db)
    assets = index.get("assets")
    embedding_index, embedding_status = _read_ai_image_embedding_index(library_root)
    reuse_debug_mode = _normalize_reuse_debug_mode(reuse_debug_mode)

    target = _build_reuse_target_asset(
        asset_kind=asset_kind,
        prompt=prompt,
        prompt_route=prompt_route,
        background_route=background_route,
        theme=theme,
        grade=grade,
        subject=subject,
        page_title=page_title,
        page_type=page_type,
        role=role,
        aspect_ratio=aspect_ratio,
    )

    debug_record = _new_reuse_debug_record(
        library_root=library_root,
        db_path=db_path,
        match_index_path=match_index_path,
        asset_count=len(assets) if isinstance(assets, list) else 0,
        candidate_limit=candidate_limit,
        min_keyword_score=min_keyword_score,
        context=debug_context,
    )
    debug_record["embedding_index"] = embedding_status
    debug_record["llm_review_enabled"] = bool(llm_review_enabled)
    debug_record["debug_mode"] = reuse_debug_mode
    debug_record["threshold_used"] = _reuse_threshold_for_target(target, min_keyword_score)

    def finish(reason: str, match: dict[str, Any] | None = None) -> dict[str, Any] | None:
        debug_record["decision"] = {
            "reused": match is not None,
            "reason": reason,
            "asset_id": _dict(match.get("asset")).get("asset_id") if match else "",
            "keyword_score": match.get("keyword_score") if match else None,
            "threshold_used": debug_record.get("threshold_used"),
            "reuse_policy": match.get("reuse_policy") if match else None,
            "reuse_audit": match.get("reuse_audit") if match else None,
            "llm_reuse_review_performed": _match_llm_reuse_review_performed(match) if match else False,
            "strict_reuse_occupancy": match.get("strict_reuse_occupancy") if match else None,
        }
        _append_reuse_debug_record(
            debug_path,
            _reuse_debug_record_for_mode(debug_record, mode=reuse_debug_mode, match=match),
        )
        return match

    if not isinstance(assets, list) or not assets:
        debug_record["target"] = _reuse_debug_asset_payload(target)
        return finish("empty_asset_store")

    if keyword_client is not None:
        target_db = {"schema_version": SCHEMA_VERSION, "assets": [target], "warnings": []}
        enrich_ai_image_asset_db_keywords(
            target_db,
            keyword_client,
            batch_size=1,
            include_match_keywords=True,
        )
        target = target_db["assets"][0]
    target = _normalize_asset_for_match(target, for_target=True) or target
    threshold = _reuse_threshold_for_target(target, min_keyword_score)
    debug_record["threshold_used"] = threshold
    debug_record["target"] = _reuse_debug_asset_payload(target)
    debug_record["candidate_scores"] = _collect_reuse_candidate_debug(target, assets, library_root)

    pool_limit = max(DEFAULT_HYBRID_RETRIEVAL_POOL_SIZE, int(candidate_limit or DEFAULT_REUSE_CANDIDATE_LIMIT))
    bm25_ranked_candidates = _rank_reuse_candidates(
        target,
        assets,
        library_root=library_root,
        limit=pool_limit,
    )
    embedding_ranked_candidates = _rank_embedding_candidates(
        target,
        assets,
        library_root=library_root,
        embedding_index=embedding_index,
        limit=pool_limit,
    )
    substring_ranked_candidates = _rank_substring_candidates(
        target,
        assets,
        library_root=library_root,
        limit=pool_limit,
    )
    ranked_candidates = _rank_hybrid_reuse_candidates(
        target,
        assets,
        library_root=library_root,
        bm25_ranked=bm25_ranked_candidates,
        embedding_ranked=embedding_ranked_candidates,
        substring_ranked=substring_ranked_candidates,
        threshold=threshold,
        limit=candidate_limit,
    )
    for candidate in ranked_candidates:
        candidate["reuse_audit"] = _reuse_audit_payload(
            target,
            _dict(candidate.get("asset")),
            debug_context,
            _match_transform_policy(candidate),
        )
    debug_record["bm25_ranked_candidates"] = [
        _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in bm25_ranked_candidates
    ]
    debug_record["embedding_ranked_candidates"] = [
        _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in embedding_ranked_candidates
    ]
    debug_record["substring_ranked_candidates"] = [
        _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in substring_ranked_candidates
    ]
    debug_record["ranked_candidates"] = [
        _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in ranked_candidates
    ]
    candidates = [
        candidate
        for candidate in ranked_candidates
        if _candidate_passes_reuse_threshold(candidate, threshold, target=target)
    ]
    debug_record["thresholded_candidates"] = [
        _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in candidates
    ]
    if not candidates:
        return finish("no_candidate_above_reuse_threshold")

    df_ratio_lookup = _get_library_df_ratio(library_root, db_path, assets)
    precision_stopwords = _PRECISION_SIGNAL_STOPWORDS
    debug_record["df_ratio_library_size"] = sum(
        1 for a in assets if isinstance(a, dict) and a.get("asset_kind") == "page_image"
    )
    debug_record["df_ratio_active"] = bool(df_ratio_lookup)

    accepted_candidates: list[dict[str, Any]] = []
    rejected_by_policy: list[dict[str, Any]] = []
    rejected_by_occupancy: list[dict[str, Any]] = []
    llm_reviews_used = 0
    for candidate in candidates:
        score_details = dict(_dict(candidate.get("score_details")))
        for key in (
            "keyword_score",
            "embedding_score",
            "substring_score",
            "hybrid_score",
            "rrf_score",
            "accepted_by",
            "background_reuse_score",
            "transform_policy",
        ):
            if key in candidate and key not in score_details:
                score_details[key] = candidate.get(key)
        if _dict(embedding_status).get("enabled"):
            score_details["constraint_embedding_scores"] = _score_constraint_embedding_pairs(
                target,
                _dict(candidate.get("asset")),
            )
        candidate_asset = _dict(candidate.get("asset"))
        if df_ratio_lookup:
            score_details["df_ratio_lookup"] = df_ratio_lookup
            score_details["precision_signal"] = has_precision_signal(
                target,
                candidate_asset,
                keyword_df_ratio=df_ratio_lookup,
                keyword_stopwords=precision_stopwords,
            )
        policy_result = evaluate_reuse_filter(
            target,
            candidate_asset,
            score_details,
            threshold=threshold,
        )
        review_decision = _clean_text(policy_result.get("decision"))
        review_reason = _clean_text(policy_result.get("reason"))
        # Per-query LLM review budget + deterministic-reject skip + early
        # stop. These three gates together implement the cost-cutting
        # measures: (a) reasons where the LLM rules deterministically
        # yield reject are short-circuited without invoking the LLM, (b)
        # once an earlier candidate has been accepted the LLM is not
        # invoked on lower-ranked candidates that will never be returned,
        # (c) the per-query budget caps tail review on long candidate
        # lists.
        deterministic_reject = review_reason in DETERMINISTIC_LLM_REJECT_REASONS
        budget_exhausted = llm_reviews_used >= MAX_LLM_REVIEWS_PER_QUERY
        skip_for_existing_accept = bool(accepted_candidates)
        if review_decision == "llm_review" and (
            deterministic_reject or budget_exhausted or skip_for_existing_accept
        ):
            skip_threshold = _reuse_review_accept_score_threshold(
                target,
                candidate_asset,
                policy_result=policy_result,
            )
            if deterministic_reject:
                skip_brief = "deterministic_reject_skip"
                skip_decision_reason = (
                    "strict_deterministic_llm_skip"
                    if review_reason.startswith("strict_")
                    else "deterministic_llm_skip"
                )
            elif skip_for_existing_accept:
                skip_brief = "earlier_candidate_accepted"
                skip_decision_reason = (
                    "strict_llm_review_skipped_after_accept"
                    if review_reason.startswith("strict_")
                    else "llm_review_skipped_after_accept"
                )
            else:
                skip_brief = "per_query_budget_exhausted"
                skip_decision_reason = (
                    "strict_llm_review_budget_exhausted"
                    if review_reason.startswith("strict_")
                    else "llm_review_budget_exhausted"
                )
            policy_result = dict(policy_result)
            policy_result["llm_review_required"] = True
            policy_result["llm_review_performed"] = False
            policy_result["llm_review"] = {
                "score": 0.0,
                "threshold": skip_threshold,
                "decision": "reject",
                "brief_reason": skip_brief,
            }
            policy_result["decision"] = "reject"
            policy_result["reason"] = skip_decision_reason
        elif review_decision == "llm_review" and llm_review_enabled:
            llm_reviews_used += 1
            policy_result = dict(policy_result)
            policy_result["llm_review_required"] = True
            review_result = _review_reuse_candidate_with_llm(
                keyword_client,
                target=target,
                candidate=candidate_asset,
                policy_result=policy_result,
                score_details=score_details,
            )
            policy_result["llm_review"] = review_result
            policy_result["llm_review_performed"] = True
            if _reuse_review_accepts(review_result):
                policy_result = dict(policy_result)
                policy_result["decision"] = "full_match"
                policy_result["reason"] = (
                    "strict_llm_score_review_accepted" if review_reason.startswith("strict_") else "llm_score_review_accepted"
                )
                policy_result["confidence"] = max(
                    float(policy_result.get("confidence") or 0.0),
                    _clamp_score(review_result.get("score")),
                )
            else:
                policy_result = dict(policy_result)
                policy_result["decision"] = "reject"
                policy_result["reason"] = (
                    "strict_llm_score_review_rejected" if review_reason.startswith("strict_") else "llm_score_review_rejected"
                )
        elif review_decision == "llm_review":
            review_threshold = _reuse_review_accept_score_threshold(
                target,
                candidate_asset,
                policy_result=policy_result,
            )
            policy_result = dict(policy_result)
            policy_result["llm_review_required"] = True
            policy_result["llm_review_performed"] = False
            policy_result["llm_review"] = {
                "score": 0.0,
                "threshold": review_threshold,
                "decision": "reject",
                "brief_reason": "llm_review_disabled",
            }
            policy_result["decision"] = "reject"
            policy_result["reason"] = (
                "strict_llm_review_disabled" if review_reason.startswith("strict_") else "llm_review_disabled"
            )
        else:
            policy_result = dict(policy_result)
            policy_result["llm_review_required"] = False
            policy_result["llm_review_performed"] = False
        candidate["reuse_policy"] = policy_result
        decision = _clean_text(policy_result.get("decision"))
        if decision in {"full_match", "generic_support"}:
            occupancy = _strict_reuse_occupancy_status(candidate, reuse_session_state)
            candidate["strict_reuse_occupancy"] = occupancy
            if _clean_text(occupancy.get("decision")) == "skip_strict_asset_reuse_limit":
                rejected_by_occupancy.append(candidate)
                continue
            accepted_candidates.append(candidate)
        else:
            rejected_by_policy.append(candidate)
    debug_record["llm_reviews_invoked"] = llm_reviews_used
    debug_record["llm_reviews_budget"] = MAX_LLM_REVIEWS_PER_QUERY

    debug_record["policy_candidates"] = [
        _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in candidates
    ]
    if not accepted_candidates:
        debug_record["policy_rejected_candidates"] = [
            _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in rejected_by_policy
        ]
        debug_record["occupancy_rejected_candidates"] = [
            _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in rejected_by_occupancy
        ]
        return finish("no_candidate_after_reuse_policy_or_occupancy")

    best = accepted_candidates[0]
    accepted_by = _clean_text(best.get("accepted_by"))
    policy_decision = _clean_text(_dict(best.get("reuse_policy")).get("decision"))
    if accepted_by == "background_threshold":
        reason = "reused_by_background_reuse_score"
    elif accepted_by == "bm25_threshold":
        reason = "reused_by_core_score"
    elif policy_decision == "generic_support":
        reason = "reused_by_policy_generic_support"
    else:
        reason = "reused_by_hybrid_retrieval_score"
    return finish(reason, best)


def record_reused_ai_image_asset(
    *,
    session_dir: str | Path,
    session_image_path: str | Path,
    match: dict[str, Any],
) -> None:
    """Record that a session image came from the reusable asset library."""

    session_root = Path(session_dir).expanduser().resolve()
    image_path = Path(session_image_path).expanduser().resolve()
    try:
        rel_image_path = image_path.relative_to(session_root).as_posix()
    except ValueError:
        rel_image_path = str(image_path)

    asset = _dict(match.get("asset"))
    entry = {
        "image_path": rel_image_path,
        "reuse_asset_id": asset.get("asset_id"),
        "candidate_image_path": asset.get("image_path"),
        "keyword_score": match.get("keyword_score"),
        "score_details": match.get("score_details", {}),
        "reuse_policy": match.get("reuse_policy", {}),
        "reuse_audit": match.get("reuse_audit", {}),
        "llm_reuse_review_performed": _match_llm_reuse_review_performed(match),
        "transform_policy": _match_transform_policy(match),
        "reused_at": datetime.now(timezone.utc).isoformat(),
    }
    entry.update(_flat_reuse_audit_fields(_dict(match.get("reuse_audit"))))

    manifest_path = session_root / "materials" / REUSE_MANIFEST_FILENAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = _read_json_if_exists(manifest_path)
    entries = manifest.get("reused_assets") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        entries = []
    entries = [item for item in entries if _dict(item).get("image_path") != rel_image_path]
    entries.append(entry)
    manifest = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "reused_assets": entries,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def mark_reused_ai_image_asset_in_session(
    match: dict[str, Any],
    reuse_session_state: dict[str, Any] | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record an accepted match in the current in-memory reuse session state."""

    if reuse_session_state is None:
        return {}
    asset = _dict(match.get("asset"))
    if not _is_strict_reuse_limited_asset(asset):
        return {
            "enabled": True,
            "max_per_session": STRICT_REUSE_MAX_PER_SESSION,
            "limited": False,
            "decision": "not_limited",
        }

    counts = reuse_session_state.setdefault("strict_asset_use_counts", {})
    used_by = reuse_session_state.setdefault("strict_asset_used_by", {})
    ids = _strict_reuse_occupancy_ids(asset)
    used_count_before = max([int(_dict(counts).get(asset_id) or 0) for asset_id in ids] or [0])
    context_payload = context or {}
    for asset_id in ids:
        counts[asset_id] = int(counts.get(asset_id) or 0) + 1
        used_by.setdefault(asset_id, []).append(context_payload)
    used_count_after = max([int(_dict(counts).get(asset_id) or 0) for asset_id in ids] or [0])
    occupancy = {
        "enabled": True,
        "max_per_session": STRICT_REUSE_MAX_PER_SESSION,
        "limited": True,
        "asset_ids": ids,
        "used_count_before": used_count_before,
        "used_count_after": used_count_after,
        "decision": "accepted_within_limit",
    }
    match["strict_reuse_occupancy"] = occupancy
    return occupancy


def materialize_reused_ai_image_asset(
    *,
    session_dir: str | Path,
    session_image_path: str | Path,
    match: dict[str, Any],
) -> None:
    """Copy or derive a reusable image according to its aspect transform policy."""

    dest = Path(session_image_path).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    reuse_image_path = Path(_clean_text(match.get("candidate_image_path"))).expanduser()
    transform_policy = _match_transform_policy(match)
    if _clean_text(transform_policy.get("decision")) == "reject":
        reason = _clean_text(transform_policy.get("reason")) or "aspect_transform_rejected"
        raise ValueError(f"refusing to materialize rejected AI image reuse match: {reason}")
    mode = _clean_text(transform_policy.get("mode")) or "copy"

    try:
        if mode == "copy":
            shutil.copy2(reuse_image_path, dest)
        else:
            _write_transformed_reuse_image(reuse_image_path, dest, transform_policy)
    except Exception:
        shutil.copy2(reuse_image_path, dest)

    record_reused_ai_image_asset(
        session_dir=session_dir,
        session_image_path=dest,
        match=match,
    )


def evaluate_ai_image_reuse_matches_from_plan(
    *,
    plan_path: str | Path,
    library_dir: str | Path,
    keyword_client: Any | None = None,
    debug_path: str | Path | None = None,
    include_background: bool = True,
    materialize_matches: bool = False,
    llm_review_enabled: bool = True,
    reuse_debug_mode: str = "full",
) -> dict[str, Any]:
    """Evaluate reuse matches from a plan without generating or ingesting assets.

    When ``materialize_matches`` is true, accepted reusable-library matches are
    copied into the plan session's ``materials/`` directory. This still does not
    generate new images or update the central asset library.
    """

    from edupptx.materials.background_generator import build_background_content_prompt
    from edupptx.materials.image_prompt_router import build_routed_image_needs
    from edupptx.models import PlanningDraft, iter_image_slot_keys

    plan_file = Path(plan_path).expanduser().resolve()
    data = json.loads(plan_file.read_text(encoding="utf-8"))
    draft = PlanningDraft.model_validate(data)
    plan_data = draft.model_dump()
    context = {
        "theme": _clean_text(draft.meta.topic),
        "grade": infer_grade(
            draft.meta.topic,
            draft.meta.audience,
            draft.style_routing.template_family,
            draft.style_routing.style_name,
        ),
        "subject": infer_subject(
            draft.meta.topic,
            draft.meta.audience,
            draft.meta.purpose,
            draft.meta.style_direction,
        ),
    }
    reuse_session_state: dict[str, Any] = {
        "strict_asset_use_counts": {},
        "strict_asset_used_by": {},
    }
    reuse_debug_mode = _normalize_reuse_debug_mode(reuse_debug_mode)
    checks: list[dict[str, Any]] = []
    materialized_count = 0

    if include_background:
        background_match = find_reusable_ai_image_asset(
            library_dir=library_dir,
            asset_kind="background",
            prompt=build_background_content_prompt(draft.visual),
            background_route=_build_background_route(plan_data),
            theme=context["theme"],
            grade=context["grade"],
            subject=context["subject"],
            aspect_ratio="16:9",
            keyword_client=keyword_client,
            debug_path=debug_path,
            debug_context={"check_type": "plan_reuse_match", "asset_kind": "background"},
            reuse_session_state=reuse_session_state,
            llm_review_enabled=llm_review_enabled,
            reuse_debug_mode=reuse_debug_mode,
        )
        session_image_path: Path | None = None
        if background_match:
            if materialize_matches:
                session_image_path = _materialize_plan_reuse_match(
                    session_dir=plan_file.parent,
                    asset_kind="background",
                    page_number=None,
                    slot_key="background",
                    match=background_match,
                )
                materialized_count += 1
            mark_reused_ai_image_asset_in_session(
                background_match,
                reuse_session_state,
                {
                    "asset_kind": "background",
                    "slot_key": "background",
                    "session_image_path": str(session_image_path or ""),
                },
            )
        checks.append(
            _plan_reuse_check_record(
                "background",
                None,
                "background",
                None,
                background_match,
                session_image_path=session_image_path,
            )
        )

    for page in draft.pages:
        routed_needs = build_routed_image_needs(draft, page)
        for slot_key, need in iter_image_slot_keys(routed_needs):
            if need.source != "ai_generate":
                continue
            debug_context = {
                "check_type": "plan_reuse_match",
                "asset_kind": "page_image",
                "page_number": page.page_number,
                "slot_key": slot_key,
                "aspect_ratio": need.aspect_ratio,
            }
            match = find_reusable_ai_image_asset(
                library_dir=library_dir,
                asset_kind="page_image",
                prompt=need.query,
                prompt_route=need.prompt_route,
                theme=context["theme"],
                grade=context["grade"],
                subject=context["subject"],
                page_title=page.title,
                page_type=page.page_type,
                role=need.role,
                aspect_ratio=need.aspect_ratio,
                keyword_client=keyword_client,
                debug_path=debug_path,
                debug_context=debug_context,
                reuse_session_state=reuse_session_state,
                llm_review_enabled=llm_review_enabled,
                reuse_debug_mode=reuse_debug_mode,
            )
            session_image_path: Path | None = None
            if match:
                if materialize_matches:
                    session_image_path = _materialize_plan_reuse_match(
                        session_dir=plan_file.parent,
                        asset_kind="page_image",
                        page_number=page.page_number,
                        slot_key=slot_key,
                        match=match,
                    )
                    materialized_count += 1
                mark_context = dict(debug_context)
                mark_context["session_image_path"] = str(session_image_path or "")
                mark_reused_ai_image_asset_in_session(match, reuse_session_state, mark_context)
            checks.append(
                _plan_reuse_check_record(
                    "page_image",
                    page.page_number,
                    slot_key,
                    need.model_dump(),
                    match,
                    session_image_path=session_image_path,
                )
            )

    matched = [item for item in checks if item["matched"]]
    return {
        "schema_version": 1,
        "asset_root": str(Path(library_dir).expanduser().resolve()),
        "generated_images": False,
        "updated_asset_store": False,
        "materialize_matches": materialize_matches,
        "materialized_count": materialized_count,
        "materials_dir": str(plan_file.parent / "materials") if materialize_matches else "",
        "check_count": len(checks),
        "matched_count": len(matched),
        "unmatched_count": len(checks) - len(matched),
        "strict_asset_use_counts": reuse_session_state["strict_asset_use_counts"],
        "checks": checks,
    }


def _match_transform_policy(match: dict[str, Any]) -> dict[str, Any]:
    policy = _dict(match.get("transform_policy"))
    if policy:
        return policy
    return _dict(_dict(match.get("score_details")).get("transform_policy"))


def _match_llm_reuse_review_performed(match: dict[str, Any]) -> bool:
    return bool(_dict(match.get("reuse_policy")).get("llm_review_performed"))


def _reuse_audit_payload(
    target: dict[str, Any],
    candidate: dict[str, Any],
    context: dict[str, Any] | None,
    transform_policy: dict[str, Any] | None,
) -> dict[str, Any]:
    context = _dict(context)

    target_theme = _clean_text(target.get("theme"))
    candidate_theme = _clean_text(candidate.get("theme"))
    target_topic_refs = _topic_refs_for_asset(target)
    candidate_topic_refs = _topic_refs_for_asset(candidate)
    topic_overlap = sorted(set(target_topic_refs) & set(candidate_topic_refs))
    target_page_number = _optional_int(context.get("page_number"))
    same_theme = bool(target_theme and candidate_theme and target_theme == candidate_theme)
    cross_theme = bool(target_theme and candidate_theme and target_theme != candidate_theme)
    return {
        "target_theme": target_theme,
        "target_topic_refs": target_topic_refs,
        "target_page_number": target_page_number,
        "candidate_theme": candidate_theme,
        "candidate_topic_refs": candidate_topic_refs,
        "same_topic_ref": bool(topic_overlap),
        "topic_ref_overlap": topic_overlap,
        "target_aspect_ratio": _clean_text(target.get("aspect_ratio")) or _clean_text(context.get("aspect_ratio")),
        "candidate_aspect_ratio": _clean_text(candidate.get("aspect_ratio")),
        "transform_policy": transform_policy or {},
        "same_theme": same_theme,
        "cross_theme": cross_theme,
        "candidate_available": bool(candidate.get("asset_id") and candidate.get("image_path")),
    }


def _flat_reuse_audit_fields(audit: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "target_theme",
        "target_topic_refs",
        "target_page_number",
        "candidate_theme",
        "candidate_topic_refs",
        "same_topic_ref",
        "topic_ref_overlap",
        "target_aspect_ratio",
        "candidate_aspect_ratio",
        "same_theme",
        "cross_theme",
        "candidate_available",
    )
    return {key: audit.get(key) for key in keys if key in audit}


def _materialize_plan_reuse_match(
    *,
    session_dir: Path,
    asset_kind: str,
    page_number: int | None,
    slot_key: str,
    match: dict[str, Any],
) -> Path:
    materials_dir = session_dir / "materials"
    if asset_kind == "background":
        dest = materials_dir / "background.png"
    else:
        suffix = Path(_clean_text(match.get("candidate_image_path"))).suffix.lower() or ".img"
        dest = materials_dir / f"page_{int(page_number or 0):02d}_{slot_key}{suffix}"
    materialize_reused_ai_image_asset(
        session_dir=session_dir,
        session_image_path=dest,
        match=match,
    )
    return dest


def _plan_reuse_check_record(
    asset_kind: str,
    page_number: int | None,
    slot_key: str,
    need: dict[str, Any] | None,
    match: dict[str, Any] | None,
    *,
    session_image_path: str | Path | None = None,
) -> dict[str, Any]:
    asset = _dict(match.get("asset")) if match else {}
    return {
        "asset_kind": asset_kind,
        "page_number": page_number,
        "slot_key": slot_key,
        "need": _plan_need_debug_payload(need),
        "matched": match is not None,
        "asset_id": asset.get("asset_id", ""),
        "candidate_image_path": _clean_text(match.get("candidate_image_path")) if match else "",
        "session_image_path": str(session_image_path or ""),
        "keyword_score": match.get("keyword_score") if match else None,
        "accepted_by": match.get("accepted_by") if match else "",
        "reuse_policy": match.get("reuse_policy") if match else {},
        "reuse_audit": match.get("reuse_audit") if match else {},
        "llm_reuse_review_performed": _match_llm_reuse_review_performed(match) if match else False,
        "transform_policy": _match_transform_policy(match) if match else {},
        "strict_reuse_occupancy": match.get("strict_reuse_occupancy") if match else {},
    }


def _plan_need_debug_payload(need: dict[str, Any] | None) -> dict[str, Any]:
    data = _dict(need)
    return {
        key: data.get(key)
        for key in ("query", "role", "aspect_ratio", "prompt_route")
        if key in data
    }


def _strict_reuse_occupancy_status(
    candidate: dict[str, Any],
    reuse_session_state: dict[str, Any] | None,
) -> dict[str, Any]:
    asset = _dict(candidate.get("asset"))
    if reuse_session_state is None:
        return {
            "enabled": False,
            "max_per_session": STRICT_REUSE_MAX_PER_SESSION,
            "limited": _is_strict_reuse_limited_asset(asset),
            "decision": "disabled",
        }
    if not _is_strict_reuse_limited_asset(asset):
        return {
            "enabled": True,
            "max_per_session": STRICT_REUSE_MAX_PER_SESSION,
            "limited": False,
            "decision": "not_limited",
        }

    counts = _dict(reuse_session_state.get("strict_asset_use_counts"))
    used_by = _dict(reuse_session_state.get("strict_asset_used_by"))
    ids = _strict_reuse_occupancy_ids(asset)
    used_count = max([int(counts.get(asset_id) or 0) for asset_id in ids] or [0])
    occupancy = {
        "enabled": True,
        "max_per_session": STRICT_REUSE_MAX_PER_SESSION,
        "limited": True,
        "asset_ids": ids,
        "used_count": used_count,
        "used_by": {asset_id: used_by.get(asset_id, []) for asset_id in ids},
    }
    if used_count >= STRICT_REUSE_MAX_PER_SESSION:
        occupancy["decision"] = "skip_strict_asset_reuse_limit"
    else:
        occupancy["decision"] = "available_within_limit"
    return occupancy


def _is_strict_reuse_limited_asset(asset: dict[str, Any]) -> bool:
    if _clean_text(asset.get("asset_kind")) != "page_image":
        return False
    policy = normalize_reuse_policy_fields(asset)
    return policy["reuse_level"] == "strict"


def _strict_reuse_occupancy_ids(asset: dict[str, Any]) -> list[str]:
    ids = [_clean_text(asset.get("asset_id"))]
    duplicates = asset.get("duplicate_asset_ids")
    if isinstance(duplicates, list):
        ids.extend(_clean_text(item) for item in duplicates)
    return _dedupe_terms([asset_id for asset_id in ids if asset_id])


def _write_transformed_reuse_image(input_path: Path, dest: Path, transform_policy: dict[str, Any]) -> None:
    from PIL import Image

    mode = _clean_text(transform_policy.get("mode")) or "copy"
    target_ratio = _ratio_value(_clean_text(transform_policy.get("target_aspect_ratio")))
    with Image.open(input_path) as img:
        image = img.convert("RGBA") if img.mode not in {"RGB", "RGBA"} else img.copy()
        if target_ratio <= 0:
            image.save(dest)
            return

        if mode == "cover_crop":
            result = _cover_crop_image(image, target_ratio)
        elif mode == "contain_pad":
            result = _contain_pad_image(image, target_ratio)
        elif mode == "blur_pad":
            result = _blur_pad_image(image, target_ratio)
        elif mode == "micro_stretch":
            result = _micro_stretch_image(image, target_ratio)
        else:
            result = image

        if dest.suffix.lower() in {".jpg", ".jpeg"} and result.mode == "RGBA":
            background = Image.new("RGB", result.size, _average_rgb(result))
            background.paste(result, mask=result.getchannel("A"))
            result = background
        result.save(dest)


def _cover_crop_image(image: Any, target_ratio: float) -> Any:
    width, height = image.size
    image_ratio = width / max(1, height)
    if image_ratio > target_ratio:
        crop_width = max(1, int(round(height * target_ratio)))
        left = max(0, (width - crop_width) // 2)
        return image.crop((left, 0, left + crop_width, height))
    crop_height = max(1, int(round(width / target_ratio)))
    top = max(0, (height - crop_height) // 2)
    return image.crop((0, top, width, top + crop_height))


def _contain_pad_image(image: Any, target_ratio: float) -> Any:
    from PIL import Image

    width, height = image.size
    canvas_width, canvas_height = _contain_canvas_size(width, height, target_ratio)
    canvas = Image.new(image.mode, (canvas_width, canvas_height), _average_rgba(image))
    left = (canvas_width - width) // 2
    top = (canvas_height - height) // 2
    canvas.paste(image, (left, top), image if image.mode == "RGBA" else None)
    return canvas


def _blur_pad_image(image: Any, target_ratio: float) -> Any:
    from PIL import ImageFilter

    width, height = image.size
    canvas_width, canvas_height = _contain_canvas_size(width, height, target_ratio)
    background = image.convert("RGB").resize((canvas_width, canvas_height))
    background = background.filter(ImageFilter.GaussianBlur(radius=max(8, min(canvas_width, canvas_height) // 24)))
    foreground = image.convert("RGBA")
    background = background.convert("RGBA")
    left = (canvas_width - width) // 2
    top = (canvas_height - height) // 2
    background.paste(foreground, (left, top), foreground)
    return background


def _micro_stretch_image(image: Any, target_ratio: float) -> Any:
    width, height = image.size
    area = max(1, width * height)
    target_width = max(1, int(round(math.sqrt(area * target_ratio))))
    target_height = max(1, int(round(target_width / target_ratio)))
    return image.resize((target_width, target_height))


def _contain_canvas_size(width: int, height: int, target_ratio: float) -> tuple[int, int]:
    image_ratio = width / max(1, height)
    if image_ratio > target_ratio:
        return width, max(height, int(round(width / target_ratio)))
    return max(width, int(round(height * target_ratio))), height


def _average_rgba(image: Any) -> tuple[int, int, int, int]:
    rgb = _average_rgb(image)
    return rgb[0], rgb[1], rgb[2], 255


def _average_rgb(image: Any) -> tuple[int, int, int]:
    from PIL import ImageStat

    stat = ImageStat.Stat(image.convert("RGB").resize((1, 1)))
    return tuple(int(value) for value in stat.mean[:3])


def _ratio_value(value: str) -> float:
    value = _clean_text(value).lower()
    if not value:
        return 0.0
    parts = re.split(r"[:/x×]", value)
    if len(parts) == 2:
        try:
            width = float(parts[0])
            height = float(parts[1])
        except ValueError:
            return 0.0
        return width / height if width > 0 and height > 0 else 0.0
    try:
        parsed = float(value)
    except ValueError:
        return 0.0
    return parsed if parsed > 0 else 0.0


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _new_reuse_debug_record(
    *,
    library_root: Path,
    db_path: Path,
    match_index_path: Path,
    asset_count: int,
    candidate_limit: int,
    min_keyword_score: float | None,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "context": context or {},
        "asset_root": str(library_root),
        "db_path": str(db_path),
        "match_index_path": str(match_index_path),
        "asset_count": asset_count,
        "candidate_limit": candidate_limit,
        "min_keyword_score": min_keyword_score,
        "threshold_used": min_keyword_score,
        "target": {},
        "candidate_scores": [],
        "ranked_candidates": [],
        "thresholded_candidates": [],
        "decision": {},
    }


def _append_reuse_debug_record(path: str | Path | None, record: dict[str, Any]) -> None:
    if path is None or not record:
        return
    debug_path = Path(path).expanduser()
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_json_if_exists(debug_path)
    queries = existing.get("queries") if isinstance(existing, dict) else None
    if not isinstance(queries, list):
        queries = []
    queries.append(record)
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "queries": queries,
    }
    debug_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_reuse_debug_mode(value: Any) -> str:
    mode = _clean_text(value).casefold()
    if mode in {"full", "summary", "off"}:
        return mode
    env_mode = _clean_text(os.environ.get("EDUPPTX_AI_IMAGE_REUSE_DEBUG_MODE")).casefold()
    if env_mode in {"full", "summary", "off"}:
        return env_mode
    return "summary"


def _reuse_debug_record_for_mode(
    record: dict[str, Any],
    *,
    mode: str,
    match: dict[str, Any] | None,
) -> dict[str, Any]:
    if mode == "off":
        return {}
    if mode == "full":
        return record

    summary = {
        "ts": record.get("ts"),
        "debug_mode": "summary",
        "context": record.get("context") or {},
        "asset_root": record.get("asset_root"),
        "db_path": record.get("db_path"),
        "match_index_path": record.get("match_index_path"),
        "asset_count": record.get("asset_count"),
        "candidate_limit": record.get("candidate_limit"),
        "threshold_used": record.get("threshold_used"),
        "llm_review_enabled": bool(record.get("llm_review_enabled")),
        "embedding_index": record.get("embedding_index") or {},
        "target": record.get("target") or {},
        "decision": record.get("decision") or {},
    }
    if match is not None:
        summary["reused_asset"] = _reuse_debug_candidate_summary(
            _reuse_debug_candidate_payload(match, threshold=_optional_float(record.get("threshold_used")))
        )
    else:
        summary["no_reuse_top_candidates"] = _reuse_no_match_top_candidate_summaries(record, limit=2)
    return summary


def _reuse_no_match_top_candidate_summaries(record: dict[str, Any], *, limit: int = 2) -> list[dict[str, Any]]:
    for key in ("policy_candidates", "thresholded_candidates", "ranked_candidates", "candidate_scores"):
        candidates = record.get(key)
        if isinstance(candidates, list) and candidates:
            return [_reuse_debug_candidate_summary(item) for item in candidates[:limit] if isinstance(item, dict)]
    return []


def _reuse_debug_candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    policy = _dict(candidate.get("reuse_policy"))
    audit = _dict(candidate.get("reuse_audit"))
    llm_review_performed = bool(policy.get("llm_review_performed"))
    payload = {
        "asset_id": candidate.get("asset_id"),
        "image_path": candidate.get("image_path"),
        "candidate_image_path": candidate.get("candidate_image_path"),
        "content_prompt": candidate.get("content_prompt"),
        "reuse_level": candidate.get("reuse_level"),
        "asset_category": candidate.get("asset_category"),
        "core_keywords": candidate.get("core_keywords") or [],
        "constraints": normalize_constraints(candidate.get("constraints")),
        "keyword_score": candidate.get("keyword_score"),
        "embedding_score": candidate.get("embedding_score"),
        "substring_score": candidate.get("substring_score"),
        "hybrid_score": candidate.get("hybrid_score"),
        "accepted_by": candidate.get("accepted_by"),
        "score_gap_to_threshold": candidate.get("score_gap_to_threshold"),
        "reuse_audit": audit,
        "llm_reuse_review_performed": llm_review_performed,
        "reuse_policy": {
            "decision": policy.get("decision"),
            "reason": policy.get("reason"),
            "missing": policy.get("missing") or [],
            "conflicts": policy.get("conflicts") or [],
            "review_items": policy.get("review_items") or [],
            "llm_review_required": bool(policy.get("llm_review_required")),
            "llm_review_performed": llm_review_performed,
            "llm_review": policy.get("llm_review") or {},
        },
        "strict_reuse_occupancy": candidate.get("strict_reuse_occupancy") or {},
    }
    payload.update(_flat_reuse_audit_fields(audit))
    return payload


def _reuse_debug_asset_payload(asset: dict[str, Any]) -> dict[str, Any]:
    grade = _clean_text(asset.get("grade"))
    reuse_policy = normalize_reuse_policy_fields(asset)
    return {
        "asset_id": asset.get("asset_id"),
        "asset_kind": asset.get("asset_kind"),
        "image_path": asset.get("image_path"),
        "content_prompt": _asset_content_prompt(asset),
        "generation_prompt": _asset_generation_prompt(asset),
        "style_prompt": _asset_style_prompt(asset),
        "prompt_route": _clean_prompt_route(asset.get("prompt_route")),
        "background_route": _clean_background_route(asset.get("background_route")),
        "color_temperature": _clean_text(asset.get("color_temperature")),
        "theme": _clean_text(asset.get("theme")),
        "topic_refs": _topic_refs_for_asset(asset),
        "core_keywords": _keyword_list(asset.get("core_keywords"), max_items=16),
        "semantic_aliases": _clean_semantic_aliases(asset.get("semantic_aliases")),
        "context_summary_keywords": _keyword_list(asset.get("context_summary_keywords"), max_items=10),
        "teaching_intent": asset.get("teaching_intent"),
        "role": _asset_role(asset),
        "page_type": _asset_page_type(asset),
        "subject": asset.get("subject"),
        "grade": grade,
        "grade_norm": asset.get("grade_norm"),
        "grade_band": asset.get("grade_band") or infer_grade_band(grade),
        "aspect_ratio": asset.get("aspect_ratio"),
        "context_summary": asset.get("context_summary"),
        "reuse_level": reuse_policy["reuse_level"],
        "asset_category": reuse_policy["asset_category"],
        "constraints": reuse_policy["constraints"],
        "generic_support_allowed": reuse_policy["generic_support_allowed"],
    }


def _reuse_debug_candidate_payload(candidate: dict[str, Any], *, threshold: float | None = None) -> dict[str, Any]:
    payload = _reuse_debug_asset_payload(_dict(candidate.get("asset")))
    payload["keyword_score"] = candidate.get("keyword_score")
    payload["embedding_score"] = candidate.get("embedding_score")
    payload["substring_score"] = candidate.get("substring_score")
    payload["hybrid_score"] = candidate.get("hybrid_score")
    payload["rrf_score"] = candidate.get("rrf_score")
    payload["accepted_by"] = candidate.get("accepted_by")
    payload["retrieval_ranks"] = candidate.get("retrieval_ranks") or {}
    payload["substring_hits"] = candidate.get("substring_hits") or []
    payload["candidate_image_path"] = str(candidate.get("candidate_image_path") or "")
    payload["score_details"] = candidate.get("score_details") or {}
    payload["reuse_policy"] = candidate.get("reuse_policy") or {}
    payload["reuse_audit"] = candidate.get("reuse_audit") or {}
    payload.update(_flat_reuse_audit_fields(_dict(payload["reuse_audit"])))
    payload["llm_reuse_review_performed"] = bool(_dict(payload["reuse_policy"]).get("llm_review_performed"))
    payload["strict_reuse_occupancy"] = candidate.get("strict_reuse_occupancy") or {}
    if threshold is not None:
        payload["threshold_used"] = threshold
        payload["score_gap_to_threshold"] = round(float(candidate.get("keyword_score") or 0.0) - threshold, 4)
    return payload


def _collect_reuse_candidate_debug(
    target: dict[str, Any],
    assets: list[Any],
    library_root: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        payload = _reuse_debug_asset_payload(item)
        image_path = _resolve_asset_image_path(library_root, item.get("image_path"))
        if image_path is None or not image_path.exists():
            payload["keyword_score"] = 0.0
            payload["candidate_image_path"] = str(image_path or "")
            payload["score_details"] = {
                "score": 0.0,
                "reject_reason": "missing_candidate_image",
            }
            rows.append(payload)
            continue

        details = _score_reuse_candidate_details(target, item)
        score = float(details.get("score") or 0.0)
        payload["keyword_score"] = round(score, 4)
        payload["candidate_image_path"] = str(image_path)
        payload["score_details"] = _debug_score_details(details)
        rows.append(payload)

    rows.sort(key=lambda item: float(item.get("keyword_score") or 0.0), reverse=True)
    return rows


def enrich_ai_image_asset_db_keywords(
    db: dict[str, Any],
    client: Any,
    *,
    batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
    include_match_keywords: bool = False,
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
            _apply_keyword_payload(asset, payload, include_match_keywords=include_match_keywords)

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
    if callable(chat_json):
        try:
            return chat_json(
                messages=messages,
                temperature=0.0,
                max_tokens=max_tokens,
                max_retries=1,
            )
        except TypeError:
            return chat_json(messages, temperature=0.0, max_tokens=max_tokens)

    chat = getattr(client, "chat", None)
    if not callable(chat):
        raise TypeError("keyword client must provide chat_json() or chat()")
    raw = chat(messages=messages, temperature=0.0, max_tokens=max_tokens)
    return _load_json_response(raw)


def _review_reuse_candidate_with_llm(
    client: Any | None,
    *,
    target: dict[str, Any],
    candidate: dict[str, Any],
    policy_result: dict[str, Any],
    score_details: dict[str, Any],
) -> dict[str, Any]:
    accept_threshold = _reuse_review_accept_score_threshold(
        target,
        candidate,
        policy_result=policy_result,
    )
    if client is None:
        return _normalize_reuse_review_score_response(
            {"score": 0.0, "brief_reason": "missing_llm_client"},
            accept_threshold=accept_threshold,
        )

    messages = _build_reuse_review_messages(
        target=target,
        candidate=candidate,
        policy_result=policy_result,
        score_details=score_details,
    )
    chat_json = getattr(client, "chat_json", None)
    try:
        if callable(chat_json):
            try:
                response = chat_json(messages=messages, temperature=0.0, max_tokens=1200, max_retries=1)
            except TypeError:
                response = chat_json(messages, temperature=0.0, max_tokens=1200)
        else:
            chat = getattr(client, "chat", None)
            if not callable(chat):
                return _normalize_reuse_review_score_response(
                    {"score": 0.0, "brief_reason": "llm_client_missing_chat"},
                    accept_threshold=accept_threshold,
                )
            response = _load_json_response(chat(messages=messages, temperature=0.0, max_tokens=1200))
    except Exception as exc:
        return _normalize_reuse_review_score_response(
            {"score": 0.0, "brief_reason": f"llm_review_failed: {str(exc)[:160]}"},
            accept_threshold=accept_threshold,
        )

    if not isinstance(response, dict):
        return _normalize_reuse_review_score_response(
            {"score": 0.0, "brief_reason": "llm_review_invalid_response"},
            accept_threshold=accept_threshold,
        )
    return _normalize_reuse_review_score_response(response, accept_threshold=accept_threshold)


def _build_reuse_review_messages(
    *,
    target: dict[str, Any],
    candidate: dict[str, Any],
    policy_result: dict[str, Any],
    score_details: dict[str, Any],
) -> list[dict[str, str]]:
    payload = {
        "reuse_review": True,
        "target": _reuse_debug_asset_payload(target),
        "candidate": _reuse_debug_asset_payload(candidate),
        "reuse_policy": policy_result,
        "score_details": _debug_score_details(score_details),
        "accept_score_threshold": _reuse_review_accept_score_threshold(
            target,
            candidate,
            policy_result=policy_result,
        ),
    }
    system = _load_reuse_review_score_rules_reference()
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def _normalize_reuse_review_score_response(
    response: dict[str, Any],
    *,
    accept_threshold: float = REUSE_REVIEW_ACCEPT_SCORE_THRESHOLD,
) -> dict[str, Any]:
    score = _clamp_score(response.get("score", response.get("reuse_score")))
    threshold = max(0.0, min(1.0, float(accept_threshold)))
    return {
        "score": score,
        "threshold": threshold,
        "decision": "accept" if score >= threshold else "reject",
        "brief_reason": _clean_text(response.get("brief_reason", response.get("reason"))) or "llm_score_review",
        "evidence": _as_string_list(response.get("evidence")),
        "risk_factors": _as_string_list(response.get("risk_factors")),
        "matched_constraints": response.get("matched_constraints") if isinstance(response.get("matched_constraints"), list) else [],
        "mismatched_constraints": response.get("mismatched_constraints") if isinstance(response.get("mismatched_constraints"), list) else [],
        "missing_constraints": response.get("missing_constraints") if isinstance(response.get("missing_constraints"), list) else [],
    }


def _reuse_review_accepts(review: dict[str, Any]) -> bool:
    threshold = review.get("threshold", REUSE_REVIEW_ACCEPT_SCORE_THRESHOLD)
    try:
        threshold_float = float(threshold)
    except (TypeError, ValueError):
        threshold_float = REUSE_REVIEW_ACCEPT_SCORE_THRESHOLD
    return _clamp_score(review.get("score")) >= threshold_float


def _build_keyword_messages(
    batch: list[dict[str, Any]],
    *,
    include_match_keywords: bool = False,
) -> list[dict[str, str]]:
    items: list[dict[str, Any]] = []
    for asset in batch:
        items.append(
            {
                "asset_id": asset.get("asset_id"),
                "asset_kind": asset.get("asset_kind"),
                "content_prompt": _asset_content_prompt(asset),
                "prompt_route": _match_prompt_route(asset.get("prompt_route")),
                "background_route": _match_background_route(asset.get("background_route")),
                "grade": asset.get("grade_norm") or asset.get("grade"),
                "subject": asset.get("subject"),
                "page_type": _asset_page_type(asset),
                "image_role": _asset_role(asset),
                "aspect_ratio": _clean_text(asset.get("aspect_ratio")),
            }
        )

    system = (
        "只返回严格 JSON，顶层必须是 assets 数组。"
        "每个条目必须使用其 asset_kind 对应的结构。"
        "page_image 结构：asset_id, context_summary, teaching_intent, "
        "context_summary_keywords, asset_category, constraints, core_keywords, semantic_aliases. "
        "对于 page_image，constraints 和 core_keywords 都必须直接从 content_prompt 的可见内容中提取；"
        "constraints 用于复用安全过滤，core_keywords 用于 BM25、embedding 和 substring 召回。"
        "每个 constraint 必须包含 kind, subtype, value, importance, confidence, evidence, reason 七个字段。"
        "subtype 是对 value 这个词的分类（不是对整张图的分类），决定 importance 的上限。"
        "importance 默认为 0；只有当 subtype 满足升级条件时才升到 1 或 2。"
        "大多数约束应为 imp=0；只有命名个体、教学事实、教学载体、故事绑定物种才升 imp=2。"
        "角色/亲缘/职业/泛类指代（妈妈、老师、小朋友、医生等）受硬性词表限制，最多 imp=1。"
        "background 结构：asset_id, normalized_prompt, color_temperature, context_summary, teaching_intent, "
        "core_keywords, semantic_aliases, context_summary_keywords. "
        "对于 background，core_keywords 是用于背景复用召回的可见、鲜明检索词。"
        "background 的 normalized_prompt 是视觉特征清单（不是描述句），按 "
        "\"色调:X; 纹理:Y; 明度:Z; 构图:W\" 四段格式输出，缺则省略不凑数。"
        "每段只用客观视觉词（例：淡蓝/浅青/米白/浅灰；梧桐叶/几何线条/圆点；低饱和,中明度；整体平铺/中心放射/边角点缀）。"
        "禁止使用主观评价词（柔和/温暖/不突兀/不刺眼/适合阅读/适配氛围 一律去掉）。"
        "色调段只写背景底色、渐变底色或大面积色块；不要把水草、小植物、叶片、线条、气泡等局部纹理/装饰物的颜色拆到色调。"
        "冷/暖/中性只能写入 color_temperature 字段，禁止出现在 normalized_prompt 的任何段落。"
        "不要照抄 background_route.background_color_bias 的整句描述。"
        "纹理段只写具体可见元素；模糊/叠加/柔化/渐隐等处理方式不要作为独立纹理，纹理颜色只有在不可分割时才保留在纹理名中。"
        "context_summary 用一句短句描述图像在当前页面中的作用。"
        "teaching_intent 说明该图像为什么支持本页教学。"
        "context_summary_keywords 来自教学和使用情境。"
        "约束值必须是原子级短名词、文字、公式、物理量或短动作短语。"
        "core_keywords 必须是少量、可见、有区分度、适合召回的原子级关键词。"
        "semantic_aliases 必须在 core_keywords 之后生成；key 必须来自 core_keywords，value 应是等价或近义短语。"
        "默认使用简体中文；专有名词、缩写、品牌和公式保持原样。"
        "仅使用 page_type 推断 context_summary、teaching_intent 和 context_summary_keywords。"
        "下面是 AI 图像复用元数据规则。"
    )
    user = "请按结构规范化以下素材：\n" + json.dumps({"assets": items}, ensure_ascii=False, indent=2)
    system += "\n\n" + _load_keyword_reuse_rules_reference()
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


def _load_reuse_review_score_rules_reference() -> str:
    try:
        text = REUSE_REVIEW_SCORE_RULES_REFERENCE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"missing AI image reuse review score rules reference: {REUSE_REVIEW_SCORE_RULES_REFERENCE}") from exc
    if not text:
        raise RuntimeError(f"empty AI image reuse review score rules reference: {REUSE_REVIEW_SCORE_RULES_REFERENCE}")
    return text


def _load_json_response(raw: Any) -> dict[str, Any] | list[Any]:
    text = _strip_fences(str(raw or ""))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            import json_repair

            repaired = json_repair.loads(text)
        except Exception:
            raise
        if not isinstance(repaired, (dict, list)):
            raise ValueError("keyword LLM response is not a JSON object or array")
        return repaired


def _strip_fences(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines:
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _keyword_payload_by_asset_id(response: dict[str, Any] | list[Any]) -> dict[str, dict[str, Any]]:
    if isinstance(response, dict):
        items = response.get("assets")
        if items is None:
            items = response.get("keywords")
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


def _apply_keyword_payload(
    asset: dict[str, Any],
    payload: dict[str, Any],
    *,
    include_match_keywords: bool = False,
) -> None:
    preserved_vlm_fields = _preserve_vlm_fields(asset)
    context_exclusions = _context_exclusions(asset)
    grade_info = normalize_grade_info(asset.get("grade") or asset.get("grade_norm"), asset.get("theme"))
    normalized_prompt = _clean_text(payload.get("normalized_prompt")) or _default_normalized_prompt(asset)
    color_temperature = _clean_text(
        payload.get("color_temperature", payload.get("temperature", payload.get("hue_temperature")))
    )
    context_summary = _clean_text(payload.get("context_summary")) or _fallback_context_summary(asset)
    teaching_intent = _clean_text(payload.get("teaching_intent")) or _default_teaching_intent(asset)
    if _is_background_asset(asset):
        raw_keywords = _dedupe_terms(
            [
                *_keyword_list(
                    asset.get("core_keywords"),
                    max_items=12,
                    exclude=context_exclusions | _GENERIC_CORE_NOISE,
                ),
                *_keyword_list(
                    payload.get("core_keywords", payload.get("prompt_keywords", payload.get("content_keywords"))),
                    max_items=12,
                    exclude=context_exclusions | _GENERIC_CORE_NOISE,
                ),
            ]
        )
        core_keywords = _clean_recall_core_keywords(
            raw_keywords,
            exclude=context_exclusions | _GENERIC_CORE_NOISE,
            max_items=12,
        )
        if not core_keywords:
            for source in (
                _asset_content_prompt(asset),
                _clean_text(payload.get("normalized_prompt")) or _clean_text(asset.get("normalized_prompt")),
                _clean_text(asset.get("theme")),
            ):
                fallback = _fallback_core_keywords_from_text(
                    source,
                    exclude=context_exclusions | _GENERIC_CORE_NOISE,
                    max_items=12,
                )
                if fallback:
                    LOGGER.warning(
                        "background_core_keywords_fallback asset_id=%s tokens=%s",
                        _clean_text(asset.get("asset_id")),
                        fallback,
                    )
                    core_keywords = fallback
                    break
        semantic_aliases = _merge_semantic_aliases(
            _clean_semantic_aliases(asset.get("semantic_aliases")),
            _clean_semantic_aliases(payload.get("semantic_aliases")),
        )
        context_summary_keywords = _dedupe_terms(
            [
                *_keyword_list(
                    asset.get("context_summary_keywords"),
                    max_items=10,
                    exclude=context_exclusions | _GENERIC_CORE_NOISE,
                ),
                *_keyword_list(
                    payload.get("context_summary_keywords"),
                    max_items=10,
                    exclude=context_exclusions | _GENERIC_CORE_NOISE,
                ),
            ]
        )[:10]
        cleaned = {
            "asset_id": _clean_text(asset.get("asset_id")),
            "asset_kind": "background",
            "image_path": _clean_text(asset.get("image_path")),
            "aspect_ratio": _clean_text(asset.get("aspect_ratio")),
            "role": "background",
            "theme": _clean_text(asset.get("theme")),
            "subject": _clean_text(asset.get("subject")),
            "grade_norm": grade_info["grade_norm"] or _clean_text(asset.get("grade_norm")),
            "grade_band": grade_info["grade_band"] or _clean_text(asset.get("grade_band")),
            "topic_refs": _topic_refs_for_asset(asset),
            "content_prompt": _asset_content_prompt(asset),
            "background_route": _match_background_route(asset.get("background_route")),
            "normalized_prompt": normalized_prompt,
            "color_temperature": color_temperature or _clean_text(asset.get("color_temperature")),
            "context_summary": context_summary,
            "teaching_intent": teaching_intent,
            "core_keywords": core_keywords,
            "semantic_aliases": semantic_aliases,
            "context_summary_keywords": context_summary_keywords,
        }
        cleaned.update(preserved_vlm_fields)
        asset.clear()
        asset.update(cleaned)
        if include_match_keywords:
            asset["match_text"] = _build_match_text(asset)
            asset["match_key"] = _build_match_key(asset)
        return

    explicit_constraints = normalize_constraints(asset.get("constraints"))
    payload_constraints = normalize_constraints(payload.get("constraints"))
    merged_constraints = normalize_constraints([*explicit_constraints, *payload_constraints])
    asset_category = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "asset_category": payload.get("asset_category", asset.get("asset_category")),
            "constraints": merged_constraints,
        }
    )["asset_category"]
    core_keywords = _resolve_page_core_keywords(
        asset,
        payload,
        exclude=context_exclusions | _GENERIC_CORE_NOISE,
        max_items=8,
    )
    if not core_keywords:
        _warn_missing_page_core_keywords(asset)
    semantic_aliases = _merge_semantic_aliases(
        _clean_semantic_aliases(asset.get("semantic_aliases")),
        _clean_semantic_aliases(payload.get("semantic_aliases")),
    )
    context_summary_keywords = _dedupe_terms(
        [
            *_keyword_list(
                asset.get("context_summary_keywords"),
                max_items=10,
                exclude=context_exclusions | _GENERIC_CORE_NOISE,
            ),
            *_keyword_list(
                payload.get("context_summary_keywords"),
                max_items=10,
                exclude=context_exclusions | _GENERIC_CORE_NOISE,
            ),
        ]
    )[:10]
    cleaned = {
        "asset_id": _clean_text(asset.get("asset_id")),
        "asset_kind": "page_image",
        "image_path": _clean_text(asset.get("image_path")),
        "aspect_ratio": _clean_text(asset.get("aspect_ratio")),
        "role": _asset_role(asset),
        "page_type": _asset_page_type(asset),
        "theme": _clean_text(asset.get("theme")),
        "subject": _clean_text(asset.get("subject")),
        "grade_norm": grade_info["grade_norm"] or _clean_text(asset.get("grade_norm")),
        "grade_band": grade_info["grade_band"] or _clean_text(asset.get("grade_band")),
        "topic_refs": _topic_refs_for_asset(asset),
        "content_prompt": _asset_content_prompt(asset),
        "context_summary": context_summary,
        "teaching_intent": teaching_intent,
        "context_summary_keywords": context_summary_keywords,
        "asset_category": asset_category,
        "constraints": merged_constraints,
        "core_keywords": core_keywords,
        "semantic_aliases": semantic_aliases,
        "duplicate_asset_ids": _dedupe_terms(_as_string_list(asset.get("duplicate_asset_ids"))),
    }
    cleaned.update(preserved_vlm_fields)
    asset.clear()
    asset.update(cleaned)
    if include_match_keywords:
        asset["match_text"] = _build_match_text(asset)
        asset["match_key"] = _build_match_key(asset)


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


def _clean_semantic_aliases(value: Any) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    if not isinstance(value, dict):
        return aliases
    for raw_key, raw_values in value.items():
        key = _clean_keyword(raw_key)
        if not key:
            continue
        terms = _keyword_list(raw_values, max_items=6)
        if terms:
            aliases[key] = terms
    return aliases


def _merge_semantic_aliases(*items: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for aliases in items:
        for key, values in aliases.items():
            clean_key = _clean_keyword(key)
            if not clean_key:
                continue
            merged[clean_key] = _dedupe_terms([*merged.get(clean_key, []), *values])[:8]
    return merged


def extract_topic_refs(*texts: Any) -> list[str]:
    """Extract compact lesson/knowledge-topic anchors from theme-like text."""

    wrapped: list[str] = []
    for text in texts:
        clean = _clean_text(text)
        if not clean:
            continue
        wrapped.extend(_clean_topic_ref(match.group(1)) for match in _TOPIC_REF_WRAPPER_RE.finditer(clean))
    wrapped = _dedupe_terms([item for item in wrapped if item])
    if wrapped:
        return wrapped[:6]

    fallback: list[str] = []
    for text in texts:
        topic = _clean_topic_ref(text)
        if topic:
            fallback.append(topic)
    return _dedupe_terms(fallback)[:6]


def _topic_refs_for_asset(asset: dict[str, Any]) -> list[str]:
    explicit = _keyword_list(asset.get("topic_refs"), max_items=6)
    if explicit:
        return explicit
    return extract_topic_refs(asset.get("theme"))


def _clean_topic_ref(value: Any) -> str:
    text = _clean_text(value).strip("《》〈〉「」『』“”\"'()（）[]【】 ")
    if not text:
        return ""
    text = _TOPIC_REF_LEADING_NOISE_RE.sub("", text).strip()
    changed = True
    while changed:
        changed = False
        for subject in _TOPIC_REF_SUBJECT_PREFIXES:
            if text.startswith(subject) and len(text) > len(subject):
                text = text[len(subject):].strip()
                changed = True
                break
    changed = True
    while changed:
        changed = False
        for suffix in _TOPIC_REF_TRAILING_NOISE:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)].strip()
                changed = True
                break
    text = text.strip("：:，,。；;、-_/ ")
    compact = text.replace(" ", "")
    if not compact or len(compact) > 40:
        return ""
    return text


def _context_exclusions(asset: dict[str, Any]) -> set[str]:
    grade = _clean_text(asset.get("grade"))
    subject = _clean_text(asset.get("subject"))
    grade_info = normalize_grade_info(grade, asset.get("theme"))
    exclusions = {
        grade,
        _clean_text(asset.get("grade_norm")),
        _clean_text(asset.get("grade_band")),
        _clean_text(grade_info.get("grade_norm")),
        _clean_text(grade_info.get("grade_band")),
        subject,
    }
    if grade and subject:
        exclusions.add(f"{grade}{subject}")
        exclusions.add(f"{grade} {subject}")
    grade_norm = _clean_text(grade_info.get("grade_norm"))
    if grade_norm and subject:
        exclusions.add(f"{grade_norm}{subject}")
        exclusions.add(f"{grade_norm} {subject}")
    return {item for item in exclusions if item}


def _clean_recall_core_keywords(
    value: Any,
    *,
    exclude: set[str] | None = None,
    max_items: int,
) -> list[str]:
    cleaned, _style_terms = _clean_core_keyword_terms(
        _keyword_list(
            value,
            max_items=max_items * 4,
            exclude=(exclude or set()) | _GENERIC_CORE_NOISE,
        )
    )
    results: list[str] = []
    for term in cleaned:
        if _should_skip_recall_core_keyword(term):
            continue
        results.append(term)
        if len(results) >= max_items:
            break
    return _dedupe_terms(results)[:max_items]


_FALLBACK_TEXT_SPLITTER = re.compile(
    r"[\s,，。、；;:：!！?？/／()（）\[\]【】「」『』\"'…—\-_"
    r"的着了和与及或里中上下前后内外"
    r"各种一些若干许多很多多种各类"
    r"一只一群一条一头一个其中"
    r"0123456789０１２３４５６７８９%％]+"
)


def _fallback_core_keywords_from_text(
    text: str,
    *,
    exclude: set[str],
    max_items: int,
) -> list[str]:
    """Last-resort core_keywords extraction from a raw text source.

    Used only when the LLM payload omits core_keywords and the asset would
    otherwise ship with an empty list (breaking BM25/substring recall).
    Splits on Chinese particles and punctuation and keeps short atomic
    fragments that pass the existing recall-keyword filter. This is a safety
    net, not a real tokenizer.
    """

    if not text:
        return []
    parts = _FALLBACK_TEXT_SPLITTER.split(str(text))
    results: list[str] = []
    seen: set[str] = set()
    for part in parts:
        token = _clean_keyword(part)
        if not token or token in seen:
            continue
        compact = token.replace(" ", "")
        if len(compact) < 2 or len(compact) > 8:
            continue
        if not re.search(r"[一-鿿]", compact):
            continue
        if _should_skip_recall_core_keyword(token):
            continue
        if _is_excluded_keyword(token, exclude):
            continue
        seen.add(token)
        results.append(token)
        if len(results) >= max_items:
            break
    return results


def _resolve_page_core_keywords(
    asset: dict[str, Any],
    payload: dict[str, Any],
    *,
    exclude: set[str],
    max_items: int,
) -> list[str]:
    """Resolve page_image core_keywords with a three-tier fallback.

    Order: (1) LLM payload core_keywords, (2) fallback extracted from
    content_prompt, (3) fallback extracted from normalized_prompt, (4) theme
    tokens. Each tier is filtered through the standard recall skip rules and
    the running exclude set so we never inject grade/subject/style noise.
    """

    primary = _clean_recall_core_keywords(
        payload.get("core_keywords"),
        exclude=exclude,
        max_items=max_items,
    )
    if primary:
        return primary

    for source in (
        _asset_content_prompt(asset),
        _clean_text(payload.get("normalized_prompt")) or _clean_text(asset.get("normalized_prompt")),
    ):
        fallback = _fallback_core_keywords_from_text(
            source,
            exclude=exclude,
            max_items=max_items,
        )
        if fallback:
            LOGGER.warning(
                "page_image_core_keywords_fallback asset_id=%s source=%s tokens=%s",
                _clean_text(asset.get("asset_id")),
                "content_prompt" if source == _asset_content_prompt(asset) else "normalized_prompt",
                fallback,
            )
            return fallback

    theme_tokens = _fallback_core_keywords_from_text(
        _clean_text(asset.get("theme")),
        exclude=exclude,
        max_items=max_items,
    )
    if theme_tokens:
        LOGGER.warning(
            "page_image_core_keywords_fallback asset_id=%s source=theme tokens=%s",
            _clean_text(asset.get("asset_id")),
            theme_tokens,
        )
    return theme_tokens


def _warn_missing_page_core_keywords(asset: dict[str, Any]) -> None:
    LOGGER.warning(
        "page_image_core_keywords_empty asset_id=%s image_path=%s",
        _clean_text(asset.get("asset_id")),
        _clean_text(asset.get("image_path")),
    )


def _should_skip_recall_core_keyword(term: str) -> bool:
    text = _clean_keyword(term)
    compact = text.replace(" ", "")
    if not text or _is_generic_core_term(text) or _looks_like_style_or_usage_term(text):
        return True
    if "的" in compact:
        return True
    if len(compact) > 18:
        return True
    if any(mark in compact for mark in ("，", "。", "；", "！", "？", "如果", "用于", "适合")):
        return True
    return False


def _keyword_list(value: Any, *, max_items: int, exclude: set[str] | None = None) -> list[str]:
    if isinstance(value, str):
        raw_items: list[Any] = re.split(r"[,;\n、，；]+", value)
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        raw_items = []

    terms: list[str] = []
    seen: set[str] = set()
    excluded = exclude or set()
    for item in raw_items:
        term = _clean_keyword(item)
        if not term or term in seen or _is_excluded_keyword(term, excluded):
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= max_items:
            break
    return terms


def _is_excluded_keyword(term: str, excluded: set[str]) -> bool:
    normalized = term.replace(" ", "")
    for value in excluded:
        blocked = value.replace(" ", "")
        if normalized == blocked:
            return True
        if len(blocked) >= 4 and blocked in normalized:
            return True
    return False


def _clean_keyword(value: Any) -> str:
    text = _clean_text(value)
    text = text.strip(" \t\r\n,;:.!?\"'[](){}<>")
    text = text.strip("、，；：。！？“”‘’【】（）")
    return text[:40]


def _build_match_text(asset: dict[str, Any]) -> str:
    if _is_background_asset(asset):
        terms = _dedupe_terms(
            [
                *_keyword_list(asset.get("core_keywords"), max_items=16),
                *_semantic_alias_terms(asset),
                *_keyword_list(asset.get("context_summary_keywords"), max_items=10),
                _clean_text(asset.get("normalized_prompt")),
            ]
        )
        return " ".join(terms)

    terms = _dedupe_terms(
        [
            *_keyword_list(asset.get("core_keywords"), max_items=16),
            *_semantic_alias_terms(asset),
            *_keyword_list(asset.get("context_summary_keywords"), max_items=10),
            _asset_content_prompt(asset),
            _clean_text(asset.get("context_summary")),
            _clean_text(asset.get("teaching_intent")),
            _route_match_text(asset),
        ]
    )
    return " ".join(terms)


def _asset_embedding_text(asset: dict[str, Any]) -> str:
    if _is_background_asset(asset):
        return _join_texts(
            asset.get("normalized_prompt"),
            " ".join(_keyword_list(asset.get("core_keywords"), max_items=16)),
            " ".join(_semantic_alias_terms(asset)),
            " ".join(_keyword_list(asset.get("context_summary_keywords"), max_items=10)),
        )

    return _join_texts(
        _asset_content_prompt(asset),
        asset.get("context_summary"),
        asset.get("teaching_intent"),
        " ".join(_keyword_list(asset.get("core_keywords"), max_items=16)),
        " ".join(_semantic_alias_terms(asset)),
        " ".join(_keyword_list(asset.get("context_summary_keywords"), max_items=10)),
    )


def _target_embedding_text(asset: dict[str, Any]) -> str:
    if _is_background_asset(asset):
        return _join_texts(
            asset.get("normalized_prompt"),
            " ".join(_keyword_list(asset.get("core_keywords"), max_items=16)),
            " ".join(_semantic_alias_terms(asset)),
        )

    return _join_texts(
        _asset_content_prompt(asset),
        asset.get("context_summary"),
        asset.get("teaching_intent"),
        " ".join(_keyword_list(asset.get("core_keywords"), max_items=16)),
        " ".join(_semantic_alias_terms(asset)),
        " ".join(_target_context_summary_terms(asset)),
    )


def _embedding_query_text(text: str) -> str:
    text = _clean_text(text)
    if not text:
        return ""
    return f"Instruct: 根据图片需求检索可复用的教学图片素材\nQuery: {text}"


def _embedding_disabled() -> bool:
    value = _clean_text(os.environ.get("EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS")).lower()
    return value in {"1", "true", "yes", "on"}


def _embedding_model_name(model_name: str | None = None) -> str:
    configured = _clean_text(os.environ.get("EDUPPTX_AI_IMAGE_EMBEDDING_MODEL"))
    return configured or _clean_text(model_name) or DEFAULT_EMBEDDING_MODEL


def _load_embedding_model(model_name: str = DEFAULT_EMBEDDING_MODEL) -> Any:
    model_name = _embedding_model_name(model_name)
    cached = _EMBEDDING_MODEL_CACHE.get(model_name)
    if cached is not None:
        return cached
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)
    _EMBEDDING_MODEL_CACHE[model_name] = model
    return model


def _encode_embedding_texts(
    texts: list[str],
    *,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    query: bool = False,
) -> Any:
    model_name = _embedding_model_name(model_name)
    cleaned = [_clean_text(text) for text in texts if _clean_text(text)]
    if not cleaned:
        raise ValueError("empty embedding texts")
    if query:
        cleaned = [_embedding_query_text(text) for text in cleaned]
    model = _load_embedding_model(model_name)
    vectors = model.encode(
        cleaned,
        batch_size=16,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    import numpy as np

    if len(vectors.shape) == 1:
        vectors = vectors.reshape(1, -1)
    return np.asarray(vectors, dtype="float32")


def _score_constraint_embedding_pairs(target: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    target_constraints = [
        item for item in normalize_reuse_policy_fields(target)["constraints"]
        if int(item.get("importance") or 0) >= 1
    ]
    candidate_constraints = [
        item for item in normalize_reuse_policy_fields(candidate)["constraints"]
        if int(item.get("importance") or 0) >= 1
    ]
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for target_constraint in target_constraints:
        for candidate_constraint in candidate_constraints:
            if _clean_text(target_constraint.get("kind")) != _clean_text(candidate_constraint.get("kind")):
                continue
            if _constraints_have_light_match(target_constraint, candidate_constraint):
                continue
            pairs.append((target_constraint, candidate_constraint))
    if not pairs:
        return []

    texts: list[str] = []
    for left, right in pairs:
        texts.append(_constraint_embedding_text(left))
        texts.append(_constraint_embedding_text(right))
    try:
        vectors = _encode_embedding_texts(texts, query=False)
    except Exception:
        return []

    rows: list[dict[str, Any]] = []
    for index, (target_constraint, candidate_constraint) in enumerate(pairs):
        left_vector = vectors[index * 2]
        right_vector = vectors[index * 2 + 1]
        score = float((left_vector * right_vector).sum())
        rows.append(
            {
                "kind": _clean_text(target_constraint.get("kind")),
                "target": _clean_text(target_constraint.get("value")),
                "candidate": _clean_text(candidate_constraint.get("value")),
                "score": round(max(0.0, min(1.0, score)), 4),
            }
        )
    return rows


def _constraint_embedding_text(constraint: dict[str, Any]) -> str:
    kind = _clean_text(constraint.get("kind"))
    value = _clean_text(constraint.get("value"))
    return f"{kind}: {value}" if kind else value


def _constraints_have_light_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    kind = _clean_text(left.get("kind"))
    if kind != _clean_text(right.get("kind")):
        return False
    left_value = _normalize_constraint_for_match(kind, left.get("value"))
    right_value = _normalize_constraint_for_match(kind, right.get("value"))
    if not left_value or not right_value:
        return False
    if left_value == right_value:
        return True
    if min(len(left_value), len(right_value)) >= 2 and (left_value in right_value or right_value in left_value):
        return True
    return False


def _normalize_constraint_for_match(kind: str, value: Any) -> str:
    text = _clean_text(value).casefold()
    if kind in {"math", "physics", "text"}:
        text = re.sub(r"\s+", "", text)
    else:
        text = re.sub(r"\s+", " ", text)
    return text.strip(" ,;:()[]{}<>")


def _build_match_key(asset: dict[str, Any]) -> str:
    if _is_background_asset(asset):
        terms = _dedupe_terms(
            [
                *_keyword_list(asset.get("core_keywords"), max_items=10),
                *_semantic_alias_terms(asset),
                _clean_text(asset.get("normalized_prompt")),
            ]
        )
    else:
        terms = _dedupe_terms(
            [
                *_keyword_list(asset.get("core_keywords"), max_items=10),
                *_semantic_alias_terms(asset),
                _asset_content_prompt(asset),
            ]
        )
    return "|".join(terms[:12])


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
    if not asset_id or not asset_kind:
        return None
    if not for_target and not image_path:
        return None

    if _is_background_asset(item):
        match_asset: dict[str, Any] = {
            "asset_id": asset_id,
            "asset_kind": "background",
            "image_path": image_path,
            "aspect_ratio": _clean_text(item.get("aspect_ratio")),
            "role": "background",
            "theme": _clean_text(item.get("theme")),
            "subject": _clean_text(item.get("subject")),
            "grade_norm": _clean_text(item.get("grade_norm")),
            "grade_band": _clean_text(item.get("grade_band")),
            "topic_refs": _topic_refs_for_asset(item),
            "content_prompt": _asset_content_prompt(item),
            "background_route": _match_background_route(item.get("background_route")),
            "normalized_prompt": _clean_text(item.get("normalized_prompt")) or _asset_content_prompt(item),
            "color_temperature": _clean_text(item.get("color_temperature")),
            "context_summary": _clean_text(item.get("context_summary")),
            "teaching_intent": _clean_text(item.get("teaching_intent")),
            "core_keywords": _keyword_list(item.get("core_keywords"), max_items=12),
            "semantic_aliases": _clean_semantic_aliases(item.get("semantic_aliases")),
            "context_summary_keywords": _keyword_list(
                item.get("context_summary_keywords"),
                max_items=10,
                exclude=_context_exclusions(item) | _GENERIC_CORE_NOISE,
            ),
        }
    else:
        metadata = normalize_asset_metadata(item)
        match_asset = {
            "asset_id": asset_id,
            "asset_kind": "page_image",
            "image_path": image_path,
            "aspect_ratio": _clean_text(item.get("aspect_ratio")),
            "role": _asset_role(item),
            "page_type": _asset_page_type(item),
            "theme": _clean_text(item.get("theme")),
            "subject": _clean_text(item.get("subject")),
            "grade_norm": _clean_text(item.get("grade_norm")),
            "grade_band": _clean_text(item.get("grade_band")),
            "topic_refs": _topic_refs_for_asset(item),
            "content_prompt": _asset_content_prompt(item),
            "context_summary": _clean_text(item.get("context_summary")),
            "teaching_intent": _clean_text(item.get("teaching_intent")),
            "context_summary_keywords": _keyword_list(
                item.get("context_summary_keywords"),
                max_items=10,
                exclude=_context_exclusions(item) | _GENERIC_CORE_NOISE,
            ),
            "asset_category": normalize_reuse_policy_fields(item)["asset_category"],
            "constraints": metadata.constraints,
            "core_keywords": _clean_recall_core_keywords(
                item.get("core_keywords"),
                exclude=_context_exclusions(item) | _GENERIC_CORE_NOISE,
                max_items=8,
            ),
            "semantic_aliases": _clean_semantic_aliases(item.get("semantic_aliases")),
            "duplicate_asset_ids": metadata.duplicate_asset_ids,
        }
    if library_root is not None and image_path:
        image_file = _resolve_asset_image_path(library_root, image_path)
        if image_file is not None and image_file.exists():
            match_asset["_image_sha256"] = _file_sha256(image_file)

    match_asset["_quality_score"] = _match_asset_quality_score(match_asset)
    return _strip_empty_match_fields(match_asset)


def _normalize_rich_asset_fields(asset: dict[str, Any], *, keep_match_keywords: bool = False) -> None:
    preserved_vlm_fields = _preserve_vlm_fields(asset)
    content_prompt = _asset_content_prompt(asset)
    normalized_prompt = _default_normalized_prompt(asset)
    context_summary = _clean_text(asset.get("context_summary")) or _fallback_context_summary(asset)
    teaching_intent = _clean_text(asset.get("teaching_intent")) or _default_teaching_intent(asset)
    grade_info = normalize_grade_info(asset.get("grade") or asset.get("grade_norm"), asset.get("theme"))
    if _is_background_asset(asset):
        background_route = _match_background_route(asset.get("background_route"))
        color_bias = _background_color_bias(asset)
        content_prompt = _strip_background_color_bias_from_prompt(content_prompt, color_bias)
        color_temperature = _clean_text(asset.get("color_temperature"))
        core_keywords = _clean_recall_core_keywords(
            asset.get("core_keywords"),
            exclude=_context_exclusions(asset) | _GENERIC_CORE_NOISE,
            max_items=12,
        )
        semantic_aliases = _clean_semantic_aliases(asset.get("semantic_aliases"))
        cleaned = {
            "asset_id": _clean_text(asset.get("asset_id")),
            "asset_kind": "background",
            "image_path": _clean_text(asset.get("image_path")),
            "aspect_ratio": _clean_text(asset.get("aspect_ratio")),
            "role": "background",
            "theme": _clean_text(asset.get("theme")),
            "subject": _clean_text(asset.get("subject")),
            "grade_norm": grade_info["grade_norm"] or _clean_text(asset.get("grade_norm")),
            "grade_band": grade_info["grade_band"] or _clean_text(asset.get("grade_band")),
            "topic_refs": _topic_refs_for_asset(asset),
            "content_prompt": content_prompt,
            "background_route": background_route,
            "normalized_prompt": normalized_prompt,
            "color_temperature": color_temperature,
            "context_summary": context_summary,
            "teaching_intent": teaching_intent,
            "core_keywords": core_keywords,
            "semantic_aliases": semantic_aliases,
            "context_summary_keywords": _keyword_list(
                asset.get("context_summary_keywords"),
                max_items=10,
                exclude=_context_exclusions(asset) | _GENERIC_CORE_NOISE,
            ),
        }
        cleaned.update(preserved_vlm_fields)
        asset.clear()
        asset.update(cleaned)
        return

    constraints = normalize_constraints(asset.get("constraints"))
    core_keywords = _clean_recall_core_keywords(
        asset.get("core_keywords"),
        exclude=_context_exclusions(asset) | _GENERIC_CORE_NOISE,
        max_items=8,
    )
    semantic_aliases = _clean_semantic_aliases(asset.get("semantic_aliases"))
    policy = normalize_reuse_policy_fields(
        {
            "asset_kind": "page_image",
            "asset_category": asset.get("asset_category"),
            "constraints": constraints,
        }
    )
    cleaned = {
        "asset_id": _clean_text(asset.get("asset_id")),
        "asset_kind": "page_image",
        "image_path": _clean_text(asset.get("image_path")),
        "aspect_ratio": _clean_text(asset.get("aspect_ratio")),
        "role": _asset_role(asset),
        "page_type": _asset_page_type(asset),
        "theme": _clean_text(asset.get("theme")),
        "subject": _clean_text(asset.get("subject")),
        "grade_norm": grade_info["grade_norm"] or _clean_text(asset.get("grade_norm")),
        "grade_band": grade_info["grade_band"] or _clean_text(asset.get("grade_band")),
        "topic_refs": _topic_refs_for_asset(asset),
        "content_prompt": content_prompt,
        "context_summary": context_summary,
        "teaching_intent": teaching_intent,
        "context_summary_keywords": _keyword_list(
            asset.get("context_summary_keywords"),
            max_items=10,
            exclude=_context_exclusions(asset) | _GENERIC_CORE_NOISE,
        ),
        "asset_category": policy["asset_category"],
        "constraints": constraints,
        "core_keywords": core_keywords,
        "semantic_aliases": semantic_aliases,
        "duplicate_asset_ids": _dedupe_terms(_as_string_list(asset.get("duplicate_asset_ids"))),
    }
    cleaned.update(preserved_vlm_fields)
    asset.clear()
    asset.update(cleaned)


def _clean_core_keyword_terms(terms: list[str]) -> tuple[list[str], list[str]]:
    core_terms: list[str] = []
    style_terms: list[str] = []
    for term in terms:
        if _is_generic_core_term(term):
            continue
        if _looks_like_style_or_usage_term(term):
            style_terms.append(term)
            extracted = _extract_entity_from_visual_style_term(term)
            if extracted and not _is_generic_core_term(extracted):
                core_terms.append(extracted)
            continue
        core_terms.append(term)
    return _dedupe_terms(core_terms), _dedupe_terms(style_terms)


def _is_generic_core_term(term: str) -> bool:
    normalized = _clean_keyword(term).casefold().replace(" ", "")
    if not normalized:
        return True
    return normalized in {item.casefold().replace(" ", "") for item in _CORE_GENERIC_EXACT}


def _looks_like_style_or_usage_term(term: str) -> bool:
    normalized = _clean_keyword(term).casefold().replace(" ", "")
    if not normalized:
        return False
    if any(marker.casefold() in normalized for marker in _CORE_USAGE_MARKERS):
        return True
    if any(marker.casefold() in normalized for marker in _CORE_STYLE_MARKERS):
        return True
    if any(form.casefold() in normalized for form in _VISUAL_FORM_MARKERS) and any(
        marker.casefold() in normalized for marker in _STYLE_DESCRIPTOR_MARKERS
    ):
        return True
    return False


def _extract_entity_from_visual_style_term(term: str) -> str:
    cleaned = _clean_keyword(term)
    if not cleaned:
        return ""
    compact = cleaned.replace(" ", "")
    for marker in _STYLE_DESCRIPTOR_MARKERS:
        compact = compact.replace(marker, "")
    for marker in _CORE_STYLE_MARKERS:
        compact = compact.replace(marker, "")
    for marker in _VISUAL_FORM_MARKERS:
        if compact.endswith(marker):
            compact = compact[: -len(marker)]
    return _clean_keyword(compact)


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


def _semantic_alias_terms(asset: dict[str, Any]) -> list[str]:
    aliases = asset.get("semantic_aliases")
    if not isinstance(aliases, dict):
        return []
    terms: list[str] = []
    for key, values in aliases.items():
        terms.append(_clean_keyword(key))
        terms.extend(_keyword_list(values, max_items=8))
    return _dedupe_terms(terms)


def _semantic_alias_groups(asset: dict[str, Any], core_keywords: list[str] | None = None) -> list[dict[str, Any]]:
    core_terms = core_keywords if core_keywords is not None else _keyword_list(asset.get("core_keywords"), max_items=16)
    aliases = _clean_semantic_aliases(asset.get("semantic_aliases"))
    groups: list[dict[str, Any]] = []
    consumed_alias_keys: set[str] = set()

    for core in core_terms:
        group_terms = [core]
        for alias_key, alias_values in aliases.items():
            alias_terms = _dedupe_terms([alias_key, *alias_values])
            if _terms_match(alias_key, core) or any(_terms_match(core, alias_term) for alias_term in alias_terms):
                group_terms.extend(alias_terms)
                consumed_alias_keys.add(alias_key)
        terms = _dedupe_terms(group_terms)
        if terms:
            groups.append({"concept": core, "terms": terms})

    for alias_key, alias_values in aliases.items():
        if alias_key in consumed_alias_keys:
            continue
        terms = _dedupe_terms([alias_key, *alias_values])
        if not terms:
            continue
        overlaps_existing = any(
            _terms_match(term, existing_term)
            for term in terms
            for group in groups
            for existing_term in group.get("terms", [])
        )
        if not overlaps_existing:
            groups.append({"concept": alias_key, "terms": terms})

    return groups[:16]


def _grouped_core_similarity_with_hits(
    groups: list[dict[str, Any]],
    doc_tokens: list[str],
) -> tuple[float, list[dict[str, Any]], list[str]]:
    if not groups or not doc_tokens:
        return 0.0, [], [_clean_text(group.get("concept")) for group in groups if _clean_text(group.get("concept"))]

    doc_text = " ".join(doc_tokens)
    total = 0.0
    hits: list[dict[str, Any]] = []
    missing: list[str] = []
    for group in groups:
        concept = _clean_text(group.get("concept"))
        terms = _dedupe_terms([str(term) for term in group.get("terms", [])])
        best_score = 0.0
        best_term = ""
        best_hits: list[dict[str, str]] = []
        for term in terms:
            if _term_in_text(term, doc_text):
                score = 1.0
                term_hits = [{"target": term, "candidate": term}]
            else:
                score, term_hits = _bm25_similarity_with_hits(_bm25_tokens_from_values([term]), doc_tokens)
            if score > best_score:
                best_score = score
                best_term = term
                best_hits = term_hits
        total += best_score
        if best_score > 0:
            hits.append(
                {
                    "concept": concept,
                    "matched_term": best_term,
                    "group_score": round(best_score, 4),
                    "aliases": [term for term in terms if term != concept],
                    "token_hits": best_hits,
                }
            )
        else:
            missing.append(concept)
    return max(0.0, min(1.0, total / len(groups))), hits, missing


def _dedupe_terms(values: list[str]) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for value in values:
        term = _clean_keyword(value)
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _client_model_name(client: Any) -> str:
    return _clean_text(getattr(client, "_model", "")) or _clean_text(getattr(client, "model", ""))


_PROMPT_ROUTE_LIST_FIELDS = (
    "profile_ids",
    "profile_prompt_terms",
    "role_prompt_terms",
    "page_type_prompt_terms",
    "aspect_ratio_prompt_terms",
    "quality_terms",
    "negative_terms",
)


def _clean_prompt_route(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    route: dict[str, Any] = {}
    template_family = _clean_text(value.get("template_family"))
    if template_family:
        route["template_family"] = template_family

    profiles: list[dict[str, Any]] = []
    raw_profiles = value.get("profiles")
    if isinstance(raw_profiles, list):
        for profile in raw_profiles:
            if not isinstance(profile, dict):
                continue
            item: dict[str, Any] = {}
            profile_id = _clean_text(profile.get("id"))
            if profile_id:
                item["id"] = profile_id
            try:
                item["priority"] = int(profile.get("priority", 0))
            except (TypeError, ValueError):
                pass
            prompt_terms = _as_string_list(profile.get("prompt_terms"))
            negative_terms = _as_string_list(profile.get("negative_terms"))
            if prompt_terms:
                item["prompt_terms"] = prompt_terms
            if negative_terms:
                item["negative_terms"] = negative_terms
            if item:
                profiles.append(item)
    if profiles:
        route["profiles"] = profiles

    for key in _PROMPT_ROUTE_LIST_FIELDS:
        terms = _as_string_list(value.get(key))
        if terms:
            route[key] = _dedupe_terms(terms)

    style_prompt = _clean_text(value.get("style_prompt"))
    if style_prompt:
        route["style_prompt"] = style_prompt

    return route


def _clean_background_route(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    route: dict[str, Any] = {}
    for key in _BACKGROUND_ROUTE_FIELDS:
        text = _clean_text(value.get(key))
        if text:
            route[key] = text
    color_terms = _as_string_list(value.get("color_terms"))
    if color_terms:
        route["color_terms"] = _dedupe_terms(color_terms)
    return route


def _route_style_prompt(route: dict[str, Any]) -> str:
    explicit = _clean_text(route.get("style_prompt"))
    if explicit:
        return explicit

    terms: list[str] = []
    for key in (
        "profile_prompt_terms",
        "role_prompt_terms",
        "page_type_prompt_terms",
        "aspect_ratio_prompt_terms",
        "quality_terms",
        "negative_terms",
    ):
        terms.extend(_as_string_list(route.get(key)))
    return " ".join(_dedupe_terms(terms))


def _route_match_text(asset: dict[str, Any]) -> str:
    if not _is_background_asset(asset):
        return _join_texts(
            _dedupe_terms(
                [
                    _asset_role(asset),
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


def _asset_content_prompt(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("content_prompt")) or _clean_text(asset.get("prompt"))


def _is_background_asset(asset: dict[str, Any]) -> bool:
    return _clean_text(asset.get("asset_kind")) == "background"


def _background_color_bias(asset: dict[str, Any]) -> str:
    route = _clean_background_route(asset.get("background_route"))
    return _clean_text(route.get("background_color_bias"))


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


def _asset_generation_prompt(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("generation_prompt")) or _asset_content_prompt(asset)


def _asset_style_prompt(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("style_prompt")) or _route_style_prompt(_clean_prompt_route(asset.get("prompt_route")))


def _asset_role(asset: dict[str, Any]) -> str:
    role = _clean_text(asset.get("role"))
    if role:
        return role
    image_path = _clean_text(asset.get("image_path"))
    match = re.search(r"page_\d+_([a-zA-Z]+)_\d+", image_path)
    return _clean_text(match.group(1)) if match else ""


def _asset_page_type(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("page_type"))


def _route_grade_family(asset: dict[str, Any]) -> str:
    for value in (
        asset.get("grade_band"),
    ):
        text = _clean_text(value)
        if not text:
            continue
        if "复用" in text:
            return "复用"
        if "低年级" in text:
            return "低年级"
        if any(term in text for term in ("高年级", "初中", "高中", "high", "upper")):
            return "高年级"
        if any(term in text for term in ("low", "lower")):
            return "低年级"
        band = infer_grade_band(text)
        if band:
            return band
    return ""


def _build_reuse_target_asset(
    *,
    asset_kind: str,
    prompt: str,
    prompt_route: dict[str, Any] | None,
    theme: str,
    grade: str,
    subject: str,
    page_title: str,
    page_type: str,
    role: str,
    aspect_ratio: str,
    background_route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    route = _clean_prompt_route(prompt_route)
    bg_route = _clean_background_route(background_route)
    content_prompt = _clean_text(prompt)
    if asset_kind == "background":
        content_prompt = _strip_background_color_bias_from_prompt(
            content_prompt,
            _clean_text(bg_route.get("background_color_bias")),
        )
    asset_key = "|".join([asset_kind, content_prompt, grade, subject, aspect_ratio])
    grade_info = normalize_grade_info(grade, theme)
    target = {
        "asset_id": "target_" + hashlib.sha256(asset_key.encode("utf-8")).hexdigest()[:16],
        "asset_kind": asset_kind,
        "image_path": "",
        "aspect_ratio": aspect_ratio,
        "theme": _clean_text(theme),
        "topic_refs": extract_topic_refs(theme),
        "content_prompt": content_prompt,
        "prompt_route": route,
        "background_route": bg_route,
        "normalized_prompt": content_prompt[:80],
        "context_summary": _default_context_summary(
            asset_kind=asset_kind,
            content_prompt=content_prompt,
            theme=theme,
            page_title=page_title,
            page_type=page_type,
        ),
        "teaching_intent": _default_teaching_intent(asset_kind=asset_kind),
        "role": _clean_text(role),
        "page_type": _clean_text(page_type),
        "grade_norm": grade_info["grade_norm"],
        "grade_band": grade_info["grade_band"],
        "subject": _clean_text(subject),
    }
    _normalize_rich_asset_fields(target, keep_match_keywords=True)
    return target


def _rank_reuse_candidates(
    target: dict[str, Any],
    assets: list[Any],
    *,
    library_root: Path,
    limit: int,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        image_path = _resolve_asset_image_path(library_root, item.get("image_path"))
        if image_path is None or not image_path.exists():
            continue
        score_details = _score_reuse_candidate_details(target, item)
        score = float(score_details.get("score") or 0.0)
        if score <= 0:
            continue
        scored.append(
            {
                "asset": item,
                "candidate_image_path": image_path,
                "keyword_score": round(score, 4),
                "transform_policy": score_details.get("transform_policy") or {},
                "score_details": _debug_score_details(score_details),
            }
        )
    scored.sort(key=lambda item: item["keyword_score"], reverse=True)
    return scored[: max(1, int(limit or DEFAULT_REUSE_CANDIDATE_LIMIT))]


def _rank_embedding_candidates(
    target: dict[str, Any],
    assets: list[Any],
    *,
    library_root: Path,
    embedding_index: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    vectors = embedding_index.get("vectors")
    asset_ids = embedding_index.get("asset_ids")
    if vectors is None or not isinstance(asset_ids, list) or not asset_ids:
        return []

    try:
        import numpy as np

        query_vectors = _encode_embedding_texts([_target_embedding_text(target)], query=True)
        query_vector = query_vectors[0]
        scores = np.asarray(vectors).dot(query_vector)
        background_color_bias_scores_by_id: dict[str, float] = {}
        color_bias_vectors = embedding_index.get("background_color_bias_vectors")
        color_bias_asset_ids = embedding_index.get("background_color_bias_asset_ids")
        target_color_bias = _background_color_bias(target)
        if (
            _is_background_asset(target)
            and target_color_bias
            and color_bias_vectors is not None
            and isinstance(color_bias_asset_ids, list)
            and color_bias_asset_ids
        ):
            color_query_vector = _encode_embedding_texts([target_color_bias], query=True)[0]
            color_scores = np.asarray(color_bias_vectors).dot(color_query_vector)
            background_color_bias_scores_by_id = {
                _clean_text(asset_id): float(color_scores[idx])
                for idx, asset_id in enumerate(color_bias_asset_ids)
            }
        context_scores_by_id: dict[str, float] = {}
        context_vectors = embedding_index.get("context_vectors")
        context_asset_ids = embedding_index.get("context_asset_ids")
        target_context = _target_context_embedding_text(target)
        if (
            not _is_background_asset(target)
            and target_context
            and context_vectors is not None
            and isinstance(context_asset_ids, list)
            and context_asset_ids
        ):
            context_query_vector = _encode_embedding_texts([target_context], query=True)[0]
            context_scores = np.asarray(context_vectors).dot(context_query_vector)
            context_scores_by_id = {
                _clean_text(asset_id): float(context_scores[idx])
                for idx, asset_id in enumerate(context_asset_ids)
            }
    except Exception:
        return []

    assets_by_id = {
        _clean_text(item.get("asset_id")): item
        for item in assets
        if isinstance(item, dict) and _clean_text(item.get("asset_id"))
    }
    scored: list[dict[str, Any]] = []
    for idx, asset_id in enumerate(asset_ids):
        asset = assets_by_id.get(_clean_text(asset_id))
        if not asset:
            continue
        if _clean_text(target.get("asset_kind")) != _clean_text(asset.get("asset_kind")):
            continue
        image_path = _resolve_asset_image_path(library_root, asset.get("image_path"))
        if image_path is None or not image_path.exists():
            continue
        row = {
            "asset": asset,
            "candidate_image_path": image_path,
            "embedding_score": round(float(scores[idx]), 4),
        }
        clean_asset_id = _clean_text(asset_id)
        if clean_asset_id in background_color_bias_scores_by_id:
            row["background_color_bias_embedding_score"] = round(
                float(background_color_bias_scores_by_id[clean_asset_id]),
                4,
            )
        if clean_asset_id in context_scores_by_id:
            row["context_embedding_score"] = round(float(context_scores_by_id[clean_asset_id]), 4)
        scored.append(row)
    scored.sort(key=lambda item: float(item.get("embedding_score") or 0.0), reverse=True)
    return scored[: max(1, int(limit or DEFAULT_HYBRID_RETRIEVAL_POOL_SIZE))]


def _rank_substring_candidates(
    target: dict[str, Any],
    assets: list[Any],
    *,
    library_root: Path,
    limit: int,
) -> list[dict[str, Any]]:
    if _is_background_asset(target):
        terms = _background_prompt_query_terms(target)
    else:
        terms = _dedupe_terms(
            [
                *_keyword_list(target.get("core_keywords"), max_items=16),
                *_semantic_alias_terms(target),
                *_target_context_summary_terms(target),
            ]
        )
    terms = [term for term in terms if len(term.replace(" ", "")) >= 2]
    if not terms:
        return []

    scored: list[dict[str, Any]] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        if _clean_text(target.get("asset_kind")) != _clean_text(item.get("asset_kind")):
            continue
        image_path = _resolve_asset_image_path(library_root, item.get("image_path"))
        if image_path is None or not image_path.exists():
            continue
        text = _candidate_hybrid_text(item)
        hits = [term for term in terms if _term_in_text(term, text)]
        if not hits:
            continue
        scored.append(
            {
                "asset": item,
                "candidate_image_path": image_path,
                "substring_score": round(len(hits) / max(1, len(terms)), 4),
                "substring_hits": hits[:16],
            }
        )
    scored.sort(key=lambda item: float(item.get("substring_score") or 0.0), reverse=True)
    return scored[: max(1, int(limit or DEFAULT_HYBRID_RETRIEVAL_POOL_SIZE))]


def _rank_hybrid_reuse_candidates(
    target: dict[str, Any],
    assets: list[Any],
    *,
    library_root: Path,
    bm25_ranked: list[dict[str, Any]],
    embedding_ranked: list[dict[str, Any]],
    substring_ranked: list[dict[str, Any]],
    threshold: float,
    limit: int,
) -> list[dict[str, Any]]:
    candidate_by_id: dict[str, dict[str, Any]] = {}
    rrf_scores: dict[str, float] = {}

    def add_ranked(items: list[dict[str, Any]], score_key: str, weight: float) -> None:
        for rank, item in enumerate(items, start=1):
            asset = _dict(item.get("asset"))
            asset_id = _clean_text(asset.get("asset_id"))
            if not asset_id:
                continue
            candidate = candidate_by_id.setdefault(
                asset_id,
                {
                    "asset": asset,
                    "candidate_image_path": item.get("candidate_image_path"),
                    "keyword_score": 0.0,
                    "embedding_score": 0.0,
                    "substring_score": 0.0,
                    "substring_hits": [],
                    "retrieval_ranks": {},
                },
            )
            candidate["candidate_image_path"] = candidate.get("candidate_image_path") or item.get("candidate_image_path")
            candidate[score_key] = max(float(candidate.get(score_key) or 0.0), float(item.get(score_key) or 0.0))
            if "background_color_bias_embedding_score" in item:
                candidate["background_color_bias_embedding_score"] = max(
                    float(candidate.get("background_color_bias_embedding_score") or 0.0),
                    float(item.get("background_color_bias_embedding_score") or 0.0),
                )
            if "context_embedding_score" in item:
                candidate["context_embedding_score"] = max(
                    float(candidate.get("context_embedding_score") or 0.0),
                    float(item.get("context_embedding_score") or 0.0),
                )
            if score_key == "substring_score":
                candidate["substring_hits"] = _dedupe_terms(
                    [*(candidate.get("substring_hits") or []), *(item.get("substring_hits") or [])]
                )[:16]
            retrieval_name = {
                "keyword_score": "bm25",
                "embedding_score": "embedding",
                "substring_score": "substring",
            }.get(score_key, score_key)
            candidate["retrieval_ranks"][retrieval_name] = rank
            rrf_scores[asset_id] = rrf_scores.get(asset_id, 0.0) + weight / (DEFAULT_RRF_K + rank)

    add_ranked(bm25_ranked, "keyword_score", HYBRID_BM25_WEIGHT)
    add_ranked(embedding_ranked, "embedding_score", HYBRID_EMBEDDING_WEIGHT)
    add_ranked(substring_ranked, "substring_score", HYBRID_SUBSTRING_WEIGHT)

    if not candidate_by_id:
        return []

    max_rrf = max(rrf_scores.values()) if rrf_scores else 1.0
    results: list[dict[str, Any]] = []
    for asset_id, candidate in candidate_by_id.items():
        asset = _dict(candidate.get("asset"))
        if _is_background_asset(target):
            retrieval_ranks = _dict(candidate.get("retrieval_ranks"))
            score_details = _score_background_reuse_candidate_details(
                target,
                asset,
                prompt_embedding_score=(
                    float(candidate.get("embedding_score") or 0.0) if "embedding" in retrieval_ranks else None
                ),
                prompt_substring_score=(
                    float(candidate.get("substring_score") or 0.0) if "substring" in retrieval_ranks else None
                ),
                color_bias_embedding_score=(
                    float(candidate.get("background_color_bias_embedding_score") or 0.0)
                    if "background_color_bias_embedding_score" in candidate
                    else None
                ),
            )
            candidate["keyword_score"] = round(float(score_details.get("score") or 0.0), 4)
            candidate["background_reuse_score"] = candidate["keyword_score"]
        else:
            score_details = _score_reuse_candidate_details(
                target,
                asset,
                context_embedding_score=(
                    float(candidate.get("context_embedding_score") or 0.0)
                    if "context_embedding_score" in candidate
                    else None
                ),
            )
            bm25_score = float(score_details.get("score") or 0.0)
            candidate["keyword_score"] = round(max(float(candidate.get("keyword_score") or 0.0), bm25_score), 4)
        candidate["rrf_score"] = round(rrf_scores.get(asset_id, 0.0), 6)
        candidate["hybrid_score"] = round(rrf_scores.get(asset_id, 0.0) / max(max_rrf, 1e-9), 4)
        candidate["accepted_by"] = _reuse_acceptance_reason(candidate, threshold, target=target)
        candidate["transform_policy"] = score_details.get("transform_policy") or {}
        score_details.update(
            {
                "embedding_score": candidate.get("embedding_score"),
                "substring_score": candidate.get("substring_score"),
                "substring_hits": candidate.get("substring_hits"),
                "background_color_bias_embedding_score": candidate.get("background_color_bias_embedding_score"),
                "context_embedding_score": candidate.get("context_embedding_score"),
                "rrf_score": candidate.get("rrf_score"),
                "hybrid_score": candidate.get("hybrid_score"),
                "retrieval_ranks": candidate.get("retrieval_ranks"),
                "accepted_by": candidate.get("accepted_by"),
            }
        )
        candidate["score_details"] = _debug_score_details(score_details)
        results.append(candidate)

    results.sort(
        key=lambda item: (
            1 if item.get("accepted_by") else 0,
            float(item.get("hybrid_score") or 0.0),
            float(item.get("keyword_score") or 0.0),
            float(item.get("embedding_score") or 0.0),
        ),
        reverse=True,
    )
    return results[: max(1, int(limit or DEFAULT_REUSE_CANDIDATE_LIMIT))]


def _score_reuse_candidate(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    return float(_score_reuse_candidate_details(target, candidate).get("score", 0.0))


def _score_reuse_candidate_details(
    target: dict[str, Any],
    candidate: dict[str, Any],
    *,
    context_embedding_score: float | None = None,
) -> dict[str, Any]:
    if _clean_text(target.get("asset_kind")) != _clean_text(candidate.get("asset_kind")):
        return {"score": 0.0, "reject_reason": "asset_kind_mismatch"}
    if _is_background_asset(target):
        return _score_background_reuse_candidate_details(target, candidate)

    target_core = _keyword_list(target.get("core_keywords"), max_items=16)
    target_alias_groups = _semantic_alias_groups(target, target_core)
    candidate_content_tokens = _bm25_tokens_from_values(
        [_asset_content_prompt(candidate)]
    )
    core_score, core_hits, missing_core_groups = _grouped_core_similarity_with_hits(
        target_alias_groups,
        candidate_content_tokens,
    )

    aspect_score = _aspect_ratio_score(target, candidate)
    transform_policy = evaluate_aspect_transform(target, candidate)
    transform_penalty = float(transform_policy.get("transform_penalty") or 0.0)
    route_details = _route_score_details(target, candidate)
    route_score = float(route_details.get("route_score") or 0.0)
    route_hits = route_details.get("route_hits") or []
    style_score, style_hits = 0.0, []
    context_details = _context_score_details(
        target,
        candidate,
        context_embedding_score=context_embedding_score,
    )
    context_score = float(context_details.get("context_score") or 0.0)
    context_hits = context_details.get("context_hits") or []

    content_match_score = core_score
    if content_match_score <= 0:
        return {
            "score": 0.0,
            "reject_reason": "no_content_match",
            "transform_policy": transform_policy,
            "target_core_keywords": target_core,
            "target_semantic_aliases": _semantic_alias_terms(target),
            "target_semantic_alias_groups": target_alias_groups,
            "missing_core_groups": missing_core_groups,
            "target_context_summary_keywords": _target_context_summary_terms(target),
            **context_details,
        }

    raw_score = (
        CONTENT_PROMPT_REUSE_WEIGHT * content_match_score
        + ROUTE_REUSE_WEIGHT * route_score
        + ASPECT_REUSE_WEIGHT * aspect_score
        + LIGHT_CONTEXT_REUSE_WEIGHT * context_score
    )
    if _clean_text(transform_policy.get("decision")) == "reject":
        return {
            "score": 0.0,
            "reject_reason": "aspect_transform_rejected",
            "content_match_score": max(0.0, min(1.0, content_match_score)),
            "route_score": route_score,
            "route_hits": route_hits,
            "core_score": core_score,
            "core_hits": core_hits,
            "missing_core_groups": missing_core_groups,
            "scope_score": 0.0,
            "aspect_score": aspect_score,
            "style_score": style_score,
            "style_hits": style_hits,
            "context_score": context_score,
            "context_hits": context_hits,
            "transform_policy": transform_policy,
            "raw_score_before_transform_penalty": max(0.0, min(1.0, raw_score)),
            **route_details,
            **context_details,
            "target_core_keywords": target_core,
            "target_semantic_aliases": _semantic_alias_terms(target),
            "target_semantic_alias_groups": target_alias_groups,
            "target_context_summary_keywords": _target_context_summary_terms(target),
            "candidate_core_keywords": [],
        }
    score = raw_score - transform_penalty
    return {
        "score": max(0.0, min(1.0, score)),
        "reject_reason": "",
        "content_match_score": max(0.0, min(1.0, content_match_score)),
        "route_score": route_score,
        "route_hits": route_hits,
        "core_score": core_score,
        "core_hits": core_hits,
        "missing_core_groups": missing_core_groups,
        "scope_score": 0.0,
        "aspect_score": aspect_score,
        "style_score": style_score,
        "style_hits": style_hits,
        "context_score": context_score,
        "context_hits": context_hits,
        "transform_policy": transform_policy,
        "raw_score_before_transform_penalty": max(0.0, min(1.0, raw_score)),
        **route_details,
        **context_details,
        "target_core_keywords": target_core,
        "target_semantic_aliases": _semantic_alias_terms(target),
        "target_semantic_alias_groups": target_alias_groups,
        "target_context_summary_keywords": _target_context_summary_terms(target),
        "candidate_core_keywords": [],
    }


def _score_background_reuse_candidate_details(
    target: dict[str, Any],
    candidate: dict[str, Any],
    *,
    prompt_embedding_score: float | None = None,
    prompt_substring_score: float | None = None,
    color_bias_embedding_score: float | None = None,
) -> dict[str, Any]:
    if _clean_text(target.get("asset_kind")) != _clean_text(candidate.get("asset_kind")):
        return {"score": 0.0, "reject_reason": "asset_kind_mismatch"}

    prompt_bm25_score, prompt_bm25_hits = _bm25_similarity_with_hits(
        _background_prompt_query_tokens(target),
        _background_prompt_doc_tokens(candidate),
    )
    local_prompt_substring_score, prompt_substring_hits = _background_substring_similarity(
        _background_prompt_query_terms(target),
        _clean_text(candidate.get("normalized_prompt")),
    )
    prompt_substring = max(_optional_score(prompt_substring_score), local_prompt_substring_score)
    prompt_embedding = _optional_score(prompt_embedding_score)
    prompt_match_score = _weighted_hybrid_signal(
        bm25_score=prompt_bm25_score,
        embedding_score=prompt_embedding_score,
        substring_score=prompt_substring,
        use_hybrid=True,
    )

    target_bias = _background_color_bias(target)
    candidate_bias = _background_color_bias(candidate)
    color_bias_used = bool(target_bias and candidate_bias)
    color_bias_bm25_score = 0.0
    color_bias_bm25_hits: list[dict[str, str]] = []
    color_bias_substring_score = 0.0
    color_bias_substring_hits: list[str] = []
    color_bias_match_score = 0.0
    if color_bias_used:
        color_bias_bm25_score, color_bias_bm25_hits = _bm25_similarity_with_hits(
            _bm25_tokens_from_values([target_bias]),
            _bm25_tokens_from_values([candidate_bias]),
        )
        color_bias_substring_score, color_bias_substring_hits = _background_substring_similarity(
            _background_text_terms(target_bias),
            candidate_bias,
        )
        color_bias_match_score = _weighted_hybrid_signal(
            bm25_score=color_bias_bm25_score,
            embedding_score=color_bias_embedding_score,
            substring_score=color_bias_substring_score,
            use_hybrid=True,
        )

    transform_policy = evaluate_aspect_transform(target, candidate)
    transform_penalty = float(transform_policy.get("transform_penalty") or 0.0)
    raw_score = (
        BACKGROUND_CONTENT_PROMPT_REUSE_WEIGHT * prompt_match_score
        + BACKGROUND_COLOR_BIAS_REUSE_WEIGHT * color_bias_match_score
        if color_bias_used
        else prompt_match_score
    )
    score = 0.0 if _clean_text(transform_policy.get("decision")) == "reject" else raw_score - transform_penalty
    reject_reason = "" if score > 0 else "no_background_prompt_match"
    if _clean_text(transform_policy.get("decision")) == "reject":
        reject_reason = "aspect_transform_rejected"
    return {
        "score": max(0.0, min(1.0, score)),
        "reject_reason": reject_reason,
        "background_reuse_score": max(0.0, min(1.0, score)),
        "background_prompt_match_score": max(0.0, min(1.0, prompt_match_score)),
        "background_prompt_bm25_score": prompt_bm25_score,
        "background_prompt_bm25_hits": prompt_bm25_hits,
        "background_prompt_embedding_score": prompt_embedding,
        "background_prompt_substring_score": prompt_substring,
        "background_prompt_substring_hits": prompt_substring_hits,
        "background_color_bias_used": color_bias_used,
        "background_color_bias_match_score": max(0.0, min(1.0, color_bias_match_score)),
        "background_color_bias_bm25_score": color_bias_bm25_score,
        "background_color_bias_bm25_hits": color_bias_bm25_hits,
        "background_color_bias_embedding_score": _optional_score(color_bias_embedding_score),
        "background_color_bias_substring_score": color_bias_substring_score,
        "background_color_bias_substring_hits": color_bias_substring_hits,
        "content_match_score": max(0.0, min(1.0, prompt_match_score)),
        "route_score": 0.0,
        "route_hits": [],
        "core_score": prompt_bm25_score,
        "core_hits": prompt_bm25_hits,
        "missing_core_groups": [],
        "scope_score": 0.0,
        "aspect_score": 0.0,
        "style_score": 0.0,
        "style_hits": [],
        "context_score": 0.0,
        "context_hits": [],
        "transform_policy": transform_policy,
        "raw_score_before_transform_penalty": max(0.0, min(1.0, raw_score)),
        "target_core_keywords": _keyword_list(target.get("core_keywords"), max_items=16),
        "candidate_core_keywords": [],
        "target_semantic_aliases": _semantic_alias_terms(target),
        "target_semantic_alias_groups": _semantic_alias_groups(
            target,
            _keyword_list(target.get("core_keywords"), max_items=16),
        ),
        "target_context_summary_keywords": [],
    }


def _background_prompt_query_terms(asset: dict[str, Any]) -> list[str]:
    terms = _dedupe_terms(
        [
            *_keyword_list(asset.get("core_keywords"), max_items=16),
            *_semantic_alias_terms(asset),
            *_background_text_terms(_clean_text(asset.get("normalized_prompt"))),
        ]
    )
    return terms


def _background_prompt_query_tokens(asset: dict[str, Any]) -> list[str]:
    return _bm25_tokens_from_values(_background_prompt_query_terms(asset))


def _background_prompt_doc_tokens(asset: dict[str, Any]) -> list[str]:
    return _bm25_tokens_from_values([asset.get("normalized_prompt")])


def _background_text_terms(text: str) -> list[str]:
    text = _clean_text(text)
    if not text:
        return []
    terms = [text]
    terms.extend(re.findall(r"[A-Za-z0-9]+|[一-鿿]{2,}", text.casefold()))
    return _dedupe_terms(terms)[:16]


def _background_substring_similarity(query_terms: list[str], candidate_text: str) -> tuple[float, list[str]]:
    terms = [term for term in _dedupe_terms(query_terms) if len(term.replace(" ", "")) >= 2]
    candidate_text = _clean_text(candidate_text)
    if not terms or not candidate_text:
        return 0.0, []
    hits = [term for term in terms if _term_in_text(term, candidate_text)]
    return len(hits) / max(1, len(terms)), hits[:16]


def _optional_score(value: float | None) -> float:
    if value is None:
        return 0.0
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _weighted_hybrid_signal(
    *,
    bm25_score: float,
    embedding_score: float | None,
    substring_score: float | None,
    use_hybrid: bool,
) -> float:
    bm25_score = _optional_score(bm25_score)
    if not use_hybrid:
        return bm25_score

    total_weight = HYBRID_BM25_WEIGHT
    total_score = HYBRID_BM25_WEIGHT * bm25_score
    if embedding_score is not None:
        total_weight += HYBRID_EMBEDDING_WEIGHT
        total_score += HYBRID_EMBEDDING_WEIGHT * _optional_score(embedding_score)
    if substring_score is not None:
        total_weight += HYBRID_SUBSTRING_WEIGHT
        total_score += HYBRID_SUBSTRING_WEIGHT * _optional_score(substring_score)
    return total_score / max(total_weight, 1e-9)


def _context_score_details(
    target: dict[str, Any],
    candidate: dict[str, Any],
    *,
    context_embedding_score: float | None = None,
) -> dict[str, Any]:
    target_terms = _target_context_summary_terms(target)
    candidate_text = _candidate_context_embedding_text(candidate)
    if not target_terms or not candidate_text:
        return {
            "context_score": 0.0,
            "context_hits": [],
            "context_bm25_score": 0.0,
            "context_bm25_hits": [],
            "context_embedding_score": _optional_score(context_embedding_score),
            "context_substring_score": 0.0,
            "context_substring_hits": [],
        }

    context_bm25_score, context_bm25_hits = _bm25_similarity_with_hits(
        _bm25_tokens_from_values([target_terms]),
        _bm25_tokens_from_values([candidate_text]),
    )
    context_substring_score, context_substring_hits = _background_substring_similarity(
        target_terms,
        candidate_text,
    )
    context_score = _weighted_hybrid_signal(
        bm25_score=context_bm25_score,
        embedding_score=context_embedding_score,
        substring_score=context_substring_score,
        use_hybrid=True,
    )
    return {
        "context_score": max(0.0, min(1.0, context_score)),
        "context_hits": context_bm25_hits,
        "context_bm25_score": context_bm25_score,
        "context_bm25_hits": context_bm25_hits,
        "context_embedding_score": _optional_score(context_embedding_score),
        "context_substring_score": context_substring_score,
        "context_substring_hits": context_substring_hits,
    }


def _route_score_details(target: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    target_role = _asset_role(target)
    candidate_role = _asset_role(candidate)
    target_grade_family = _route_grade_family(target)
    candidate_grade_family = _route_grade_family(candidate)

    role_match = _exact_route_match(target_role, candidate_role)
    grade_family_match = _exact_route_match(target_grade_family, candidate_grade_family)
    route_hits: list[dict[str, str]] = []
    if role_match:
        route_hits.append({"field": "role", "target": target_role, "candidate": candidate_role})
    if grade_family_match:
        route_hits.append(
            {"field": "grade_family", "target": target_grade_family, "candidate": candidate_grade_family}
        )

    return {
        "route_score": 0.55 * role_match + 0.45 * grade_family_match,
        "route_hits": route_hits,
        "route_role_match": role_match,
        "route_grade_family_match": grade_family_match,
        "target_route_role": target_role,
        "candidate_route_role": candidate_role,
        "target_route_grade_family": target_grade_family,
        "candidate_route_grade_family": candidate_grade_family,
    }


def _exact_route_match(target_value: str, candidate_value: str) -> float:
    target_value = _clean_text(target_value)
    candidate_value = _clean_text(candidate_value)
    return 1.0 if target_value and candidate_value and target_value == candidate_value else 0.0


def _semantic_terms(
    asset: dict[str, Any],
    field: str,
    *,
    fallback_fields: tuple[str, ...] = (),
    max_items: int = 12,
) -> list[str]:
    terms = _keyword_list(asset.get(field), max_items=max_items)
    if not terms:
        for fallback in fallback_fields:
            terms.extend(_keyword_list(asset.get(fallback), max_items=max_items))
            if terms:
                break
    return _dedupe_terms(terms)[:max_items]


def _semantic_coverage(
    target_terms: list[str],
    candidate_terms: list[str],
    *,
    neutral: float,
) -> tuple[float, list[dict[str, str]], list[str]]:
    if not target_terms:
        return neutral, [], []
    if not candidate_terms:
        return 0.0, [], target_terms
    score, hits = _overlap_score_with_hits(target_terms, candidate_terms)
    matched = {_clean_keyword(item.get("target")) for item in hits}
    missing = [term for term in target_terms if _clean_keyword(term) not in matched]
    return score, hits, missing


def _debug_score_details(details: dict[str, Any]) -> dict[str, Any]:
    score = float(details.get("score") or 0.0)
    return {
        "score": round(score, 4),
        "reject_reason": _clean_text(details.get("reject_reason")),
        "keyword_score": round(float(details.get("keyword_score") or 0.0), 4),
        "content_match_score": round(float(details.get("content_match_score") or 0.0), 4),
        "route_score": round(float(details.get("route_score") or 0.0), 4),
        "route_hits": details.get("route_hits") or [],
        "route_role_match": round(float(details.get("route_role_match") or 0.0), 4),
        "route_grade_family_match": round(float(details.get("route_grade_family_match") or 0.0), 4),
        "route_page_type_match": round(float(details.get("route_page_type_match") or 0.0), 4),
        "target_route_role": _clean_text(details.get("target_route_role")),
        "candidate_route_role": _clean_text(details.get("candidate_route_role")),
        "target_route_grade_family": _clean_text(details.get("target_route_grade_family")),
        "candidate_route_grade_family": _clean_text(details.get("candidate_route_grade_family")),
        "target_route_page_type": _clean_text(details.get("target_route_page_type")),
        "candidate_route_page_type": _clean_text(details.get("candidate_route_page_type")),
        "core_score": round(float(details.get("core_score") or 0.0), 4),
        "core_hits": details.get("core_hits") or [],
        "missing_core_groups": details.get("missing_core_groups") or [],
        "scope_score": round(float(details.get("scope_score") or 0.0), 4),
        "aspect_score": round(float(details.get("aspect_score") or 0.0), 4),
        "transform_policy": details.get("transform_policy") or {},
        "raw_score_before_transform_penalty": round(
            float(details.get("raw_score_before_transform_penalty") or 0.0),
            4,
        ),
        "style_score": round(float(details.get("style_score") or 0.0), 4),
        "style_hits": details.get("style_hits") or [],
        "context_score": round(float(details.get("context_score") or 0.0), 4),
        "context_hits": details.get("context_hits") or [],
        "context_bm25_score": round(float(details.get("context_bm25_score") or 0.0), 4),
        "context_bm25_hits": details.get("context_bm25_hits") or [],
        "context_embedding_score": round(float(details.get("context_embedding_score") or 0.0), 4),
        "context_substring_score": round(float(details.get("context_substring_score") or 0.0), 4),
        "context_substring_hits": details.get("context_substring_hits") or [],
        "embedding_score": round(float(details.get("embedding_score") or 0.0), 4),
        "substring_score": round(float(details.get("substring_score") or 0.0), 4),
        "substring_hits": details.get("substring_hits") or [],
        "rrf_score": round(float(details.get("rrf_score") or 0.0), 6),
        "hybrid_score": round(float(details.get("hybrid_score") or 0.0), 4),
        "retrieval_ranks": details.get("retrieval_ranks") or {},
        "accepted_by": _clean_text(details.get("accepted_by")),
        "target_core_keywords": details.get("target_core_keywords") or [],
        "candidate_core_keywords": details.get("candidate_core_keywords") or [],
        "target_semantic_aliases": details.get("target_semantic_aliases") or [],
        "target_semantic_alias_groups": details.get("target_semantic_alias_groups") or [],
        "target_context_summary_keywords": details.get("target_context_summary_keywords") or [],
        "background_reuse_score": round(float(details.get("background_reuse_score") or 0.0), 4),
        "background_prompt_match_score": round(float(details.get("background_prompt_match_score") or 0.0), 4),
        "background_prompt_bm25_score": round(float(details.get("background_prompt_bm25_score") or 0.0), 4),
        "background_prompt_bm25_hits": details.get("background_prompt_bm25_hits") or [],
        "background_prompt_embedding_score": round(float(details.get("background_prompt_embedding_score") or 0.0), 4),
        "background_prompt_substring_score": round(float(details.get("background_prompt_substring_score") or 0.0), 4),
        "background_prompt_substring_hits": details.get("background_prompt_substring_hits") or [],
        "background_color_bias_used": bool(details.get("background_color_bias_used")),
        "background_color_bias_match_score": round(
            float(details.get("background_color_bias_match_score") or 0.0),
            4,
        ),
        "background_color_bias_bm25_score": round(
            float(details.get("background_color_bias_bm25_score") or 0.0),
            4,
        ),
        "background_color_bias_bm25_hits": details.get("background_color_bias_bm25_hits") or [],
        "background_color_bias_embedding_score": round(
            float(details.get("background_color_bias_embedding_score") or 0.0),
            4,
        ),
        "background_color_bias_substring_score": round(
            float(details.get("background_color_bias_substring_score") or 0.0),
            4,
        ),
        "background_color_bias_substring_hits": details.get("background_color_bias_substring_hits") or [],
    }


def _asset_context_text(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("context_summary"))


def _target_context_summary_terms(asset: dict[str, Any]) -> list[str]:
    return _dedupe_terms(
        [
            *_keyword_list(asset.get("context_summary_keywords"), max_items=10),
            *_topic_refs_for_asset(asset),
        ]
    )[:12]


def _candidate_context_summary_terms(asset: dict[str, Any]) -> list[str]:
    return [_clean_text(asset.get("context_summary"))]


def _target_context_embedding_text(asset: dict[str, Any]) -> str:
    return " ".join(_target_context_summary_terms(asset))


def _candidate_context_embedding_text(asset: dict[str, Any]) -> str:
    if _is_background_asset(asset):
        return ""
    return _join_texts(_clean_text(asset.get("context_summary")), " ".join(_topic_refs_for_asset(asset)))


def _bm25_tokens_from_values(values: list[Any]) -> list[str]:
    tokens: list[str] = []
    for value in values:
        if isinstance(value, (list, tuple)):
            tokens.extend(_bm25_tokens_from_values(list(value)))
            continue
        text = _clean_text(value)
        if not text:
            continue
        lowered = text.casefold()
        tokens.append(lowered)
        for part in re.findall(r"[A-Za-z0-9]+|[一-鿿]+", lowered):
            tokens.append(part)
            if re.fullmatch(r"[一-鿿]+", part):
                max_n = min(4, len(part))
                for n in range(2, max_n + 1):
                    for idx in range(0, len(part) - n + 1):
                        tokens.append(part[idx:idx + n])
            elif len(part) > 3:
                for sub in re.split(r"[_\-\s]+", part):
                    if sub:
                        tokens.append(sub)
    return _dedupe_terms(tokens)


def _bm25_similarity_with_hits(query_tokens: list[str], doc_tokens: list[str]) -> tuple[float, list[dict[str, str]]]:
    query = [token for token in query_tokens if token]
    doc = [token for token in doc_tokens if token]
    if not query or not doc:
        return 0.0, []

    score = _bm25_score(query, doc, [doc, query])
    self_score = _bm25_score(query, query, [doc, query])
    normalized = 0.0 if self_score <= 0 else min(1.0, score / self_score)
    doc_terms = set(doc)
    hits = [{"target": token, "candidate": token} for token in _dedupe_terms(query) if token in doc_terms]
    return normalized, hits[:24]


def _bm25_score(query_tokens: list[str], doc_tokens: list[str], corpus_docs: list[list[str]]) -> float:
    if not query_tokens or not doc_tokens or not corpus_docs:
        return 0.0
    k1 = 1.5
    b = 0.75
    doc_len = len(doc_tokens)
    avgdl = sum(len(doc) for doc in corpus_docs) / max(1, len(corpus_docs))
    frequencies: dict[str, int] = {}
    for token in doc_tokens:
        frequencies[token] = frequencies.get(token, 0) + 1
    score = 0.0
    for token in _dedupe_terms(query_tokens):
        freq = frequencies.get(token, 0)
        if freq <= 0:
            continue
        containing_docs = sum(1 for doc in corpus_docs if token in set(doc))
        idf = math.log(1 + (len(corpus_docs) - containing_docs + 0.5) / (containing_docs + 0.5))
        denom = freq + k1 * (1 - b + b * doc_len / max(avgdl, 1e-9))
        score += idf * (freq * (k1 + 1)) / max(denom, 1e-9)
    return score


def _overlap_score(target_terms: list[str], candidate_terms: list[str]) -> float:
    score, _hits = _overlap_score_with_hits(target_terms, candidate_terms)
    return score


def _overlap_score_with_hits(
    target_terms: list[str],
    candidate_terms: list[str],
) -> tuple[float, list[dict[str, str]]]:
    if not target_terms or not candidate_terms:
        return 0.0, []
    hits: list[dict[str, str]] = []
    for target in target_terms:
        matched = next((candidate for candidate in candidate_terms if _terms_match(target, candidate)), "")
        if matched:
            hits.append({"target": target, "candidate": matched})
    return len(hits) / len(target_terms), hits


def _terms_match(left: str, right: str) -> bool:
    left = _clean_keyword(left)
    right = _clean_keyword(right)
    if not left or not right:
        return False
    if left == right:
        return True
    return min(len(left), len(right)) >= 2 and (left in right or right in left)


def _candidate_hybrid_text(asset: dict[str, Any]) -> str:
    if _is_background_asset(asset):
        return _join_texts(
            asset.get("normalized_prompt"),
            " ".join(_keyword_list(asset.get("core_keywords"), max_items=16)),
            " ".join(_semantic_alias_terms(asset)),
            " ".join(_keyword_list(asset.get("context_summary_keywords"), max_items=10)),
        )

    return _join_texts(
        _asset_content_prompt(asset),
        asset.get("context_summary"),
        asset.get("teaching_intent"),
        " ".join(_keyword_list(asset.get("core_keywords"), max_items=16)),
        " ".join(_semantic_alias_terms(asset)),
        " ".join(_keyword_list(asset.get("context_summary_keywords"), max_items=10)),
    )


def _term_in_text(term: str, text: str) -> bool:
    term = _clean_keyword(term).replace(" ", "")
    text = _clean_text(text).replace(" ", "")
    return bool(term and text and term in text)


def _transform_rejects_candidate(candidate: dict[str, Any]) -> bool:
    transform_policy = _dict(candidate.get("transform_policy"))
    if not transform_policy:
        transform_policy = _dict(_dict(candidate.get("score_details")).get("transform_policy"))
    return _clean_text(transform_policy.get("decision")) == "reject"


def _reuse_gate_profile(target: dict[str, Any] | None) -> str:
    if target is None:
        return "medium"
    if _clean_text(target.get("asset_kind")) == "background":
        return "background"
    policy = normalize_reuse_policy_fields(_dict(target))
    constraint_kinds = {
        _clean_text(item.get("kind"))
        for item in policy.get("constraints", [])
        if isinstance(item, dict)
        and int(item.get("importance") or 0) >= 2
    }
    has_strict_knowledge = bool(constraint_kinds & {"text", "math", "physics"})
    # Background-like page_image slot: when the slot's role or page_type
    # declares it serves as backdrop, treat it like a background asset for
    # LLM-review purposes — its function is ambience, not precise content,
    # so the 二次评审 should match background expectations rather than the
    # stricter page_image expectations. Guarded by the absence of strict
    # knowledge (text/math/physics) constraints so a "background_1 with
    # 写字" slot still keeps strict review.
    role = _clean_text(_dict(target).get("role")).casefold()
    page_type = _clean_text(_dict(target).get("page_type")).casefold()
    background_like = "background" in role or "background" in page_type
    if background_like and not has_strict_knowledge:
        return "loose"

    level = _clean_text(policy.get("reuse_level")) or "medium"
    if level == "strict":
        if has_strict_knowledge:
            return "strict_knowledge"
        return "strict_literary"
    return level if level in {"loose", "medium"} else "medium"


def _reuse_gate_thresholds_for_target(target: dict[str, Any] | None) -> dict[str, float]:
    profile = _reuse_gate_profile(target)
    if profile == "background":
        return BACKGROUND_REUSE_GATE_THRESHOLDS
    return PAGE_IMAGE_REUSE_GATE_THRESHOLDS.get(profile, PAGE_IMAGE_REUSE_GATE_THRESHOLDS["medium"])


def _is_text_overlap_review_slot(target: dict[str, Any] | None, candidate: dict[str, Any]) -> bool:
    target_policy = normalize_reuse_policy_fields(_dict(target)) if target is not None else {}
    candidate_asset = _dict(candidate.get("asset")) or _dict(candidate)
    candidate_policy = normalize_reuse_policy_fields(candidate_asset)
    for policy in (target_policy, candidate_policy):
        for item in policy.get("constraints", []):
            if int(_dict(item).get("importance") or 0) >= 1 and _clean_text(_dict(item).get("kind")) in {"text", "math", "physics"}:
                return True
    return False


def _reuse_gate_reason(
    *,
    target: dict[str, Any] | None,
    candidate: dict[str, Any],
    keyword_score: float,
    embedding_score: float,
    substring_score: float,
) -> str:
    if _transform_rejects_candidate(candidate):
        return ""
    thresholds = _reuse_gate_thresholds_for_target(target)
    if keyword_score < thresholds["keyword_min"] and embedding_score < thresholds["embedding_min"]:
        return ""
    if keyword_score >= thresholds["keyword_high"]:
        return "keyword_high_review"
    if embedding_score >= thresholds["embedding_high"]:
        return "embedding_high_review"
    if (
        _is_text_overlap_review_slot(target, candidate)
        and substring_score >= TEXT_OVERLAP_REVIEW_THRESHOLD
        and embedding_score >= TEXT_OVERLAP_EMBEDDING_THRESHOLD
    ):
        return "text_overlap_embedding_review"
    if keyword_score >= thresholds["keyword_gray_high"] and embedding_score >= thresholds["embedding_gray_low"]:
        return "keyword_led_gray_review"
    if embedding_score >= thresholds["embedding_gray_high"] and keyword_score >= thresholds["keyword_gray_low"]:
        return "embedding_led_gray_review"
    return ""


def _reuse_review_accept_score_threshold(
    target: dict[str, Any],
    candidate: dict[str, Any] | None = None,
    *,
    policy_result: dict[str, Any] | None = None,
) -> float:
    transform_policy = _dict(policy_result).get("transform_policy")
    if isinstance(transform_policy, dict) and _clean_text(transform_policy.get("decision")) == "reject":
        return 1.0
    # When the policy stage has confirmed that every imp>=2 target constraint
    # is exact-covered by candidate metadata but kept LLM review on text-bound
    # kinds for visual confirmation, the LLM only needs to verify the glyph
    # is visible — not judge prose suitability. Drop the bar accordingly.
    reason = _clean_text(_dict(policy_result).get("reason"))
    if reason == "strict_text_exact_covered_review":
        return 0.58
    profile = _reuse_gate_profile(target)
    if profile == "loose":
        # Loose covers decorative tools, learning behaviors, and
        # background-like page_image slots (see _reuse_gate_profile). None of
        # these carry exact-content gating, so the LLM review only needs to
        # confirm visual plausibility — 0.55 keeps the bar above noise without
        # rejecting near-correct ambience images.
        return 0.55
    if profile == "medium":
        return 0.64
    if profile == "strict_literary":
        return 0.64
    if profile == "strict_knowledge":
        return 0.72
    return REUSE_REVIEW_ACCEPT_SCORE_THRESHOLD


def _reuse_acceptance_reason(
    candidate: dict[str, Any],
    threshold: float | None = None,
    *,
    target: dict[str, Any] | None = None,
) -> str:
    threshold = VISUAL_GENERIC_REUSE_THRESHOLD if threshold is None else float(threshold)
    if _transform_rejects_candidate(candidate):
        return ""
    if candidate.get("background_reuse_score") is not None:
        keyword_score = float(candidate.get("background_reuse_score") or candidate.get("keyword_score") or 0.0)
        embedding_score = float(candidate.get("embedding_score") or 0.0)
        substring_score = float(candidate.get("substring_score") or 0.0)
        return (
            "background_threshold"
            if _reuse_gate_reason(
                target=target,
                candidate=candidate,
                keyword_score=keyword_score,
                embedding_score=embedding_score,
                substring_score=substring_score,
            )
            else ""
        )

    bm25_score = float(candidate.get("keyword_score") or 0.0)
    embedding_score = float(candidate.get("embedding_score") or 0.0)
    substring_score = float(candidate.get("substring_score") or 0.0)
    thresholds = _reuse_gate_thresholds_for_target(target)
    if bm25_score >= threshold and embedding_score >= thresholds["embedding_min"]:
        return "bm25_threshold"
    gate_reason = _reuse_gate_reason(
        target=target,
        candidate=candidate,
        keyword_score=bm25_score,
        embedding_score=embedding_score,
        substring_score=substring_score,
    )
    if gate_reason:
        return gate_reason
    if _is_strict_embedding_review_candidate(target, candidate, embedding_score):
        return "strict_embedding_review"
    if _is_strict_semantic_gray_review_candidate(
        target,
        candidate,
        bm25_score=bm25_score,
        embedding_score=embedding_score,
        substring_score=substring_score,
    ):
        return "strict_semantic_gray_review"
    if bm25_score >= BM25_GRAY_REUSE_THRESHOLD and embedding_score >= EMBEDDING_GRAY_REUSE_THRESHOLD:
        return "embedding_gray_zone"
    if bm25_score >= max(0.0, threshold - 0.03) and substring_score >= 0.35 and embedding_score >= 0.62:
        return "substring_embedding_gray_zone"
    if _is_medium_embedding_review_candidate(target, candidate, embedding_score):
        return "medium_embedding_review"
    return ""


def _is_strict_embedding_review_candidate(
    target: dict[str, Any] | None,
    candidate: dict[str, Any],
    embedding_score: float,
) -> bool:
    if embedding_score < STRICT_EMBEDDING_REVIEW_THRESHOLD:
        return False
    asset = _dict(candidate.get("asset"))
    if _clean_text(asset.get("asset_kind")) == "background":
        return False
    policies = [normalize_reuse_policy_fields(asset)]
    if target is not None:
        policies.append(normalize_reuse_policy_fields(_dict(target)))
    return any(policy.get("reuse_level") == "strict" for policy in policies)


def _is_strict_semantic_gray_review_candidate(
    target: dict[str, Any] | None,
    candidate: dict[str, Any],
    *,
    bm25_score: float,
    embedding_score: float,
    substring_score: float,
) -> bool:
    if target is None:
        return False
    if embedding_score < STRICT_SEMANTIC_GRAY_REVIEW_THRESHOLD:
        return False
    if bm25_score < STRICT_SEMANTIC_GRAY_BM25_THRESHOLD and substring_score < 0.25:
        return False

    asset = _dict(candidate.get("asset"))
    if _clean_text(asset.get("asset_kind")) == "background":
        return False

    target_theme = _clean_text(target.get("theme"))
    candidate_theme = _clean_text(asset.get("theme"))
    if not (target_theme and candidate_theme and target_theme == candidate_theme):
        return False

    policies = [
        normalize_reuse_policy_fields(asset),
        normalize_reuse_policy_fields(_dict(target)),
    ]

    def strict_by_constraints(policy: dict[str, Any]) -> bool:
        strong_kinds: list[str] = []
        for item in policy.get("constraints", []):
            if not isinstance(item, dict):
                continue
            try:
                importance = int(item.get("importance") or 0)
            except (TypeError, ValueError):
                importance = 0
            if importance >= 2:
                strong_kinds.append(_clean_text(item.get("kind")))
        return len(strong_kinds) >= 3 or bool({"text", "math", "physics"} & set(strong_kinds))

    return any(strict_by_constraints(policy) for policy in policies)


def _is_medium_embedding_review_candidate(
    target: dict[str, Any] | None,
    candidate: dict[str, Any],
    embedding_score: float,
) -> bool:
    if embedding_score < MEDIUM_EMBEDDING_REVIEW_THRESHOLD:
        return False
    asset = _dict(candidate.get("asset"))
    if _clean_text(asset.get("asset_kind")) == "background":
        return False
    policies = [normalize_reuse_policy_fields(asset)]
    if target is not None:
        policies.append(normalize_reuse_policy_fields(_dict(target)))
    levels = {_clean_text(policy.get("reuse_level")) for policy in policies}
    return "strict" not in levels and bool(levels & {"loose", "medium"})


def _candidate_passes_reuse_threshold(
    candidate: dict[str, Any],
    threshold: float,
    *,
    target: dict[str, Any] | None = None,
) -> bool:
    return bool(candidate.get("accepted_by") or _reuse_acceptance_reason(candidate, threshold, target=target))


def _reuse_threshold_for_target(target: dict[str, Any], explicit_threshold: float | None) -> float:
    if explicit_threshold is not None:
        try:
            return max(0.0, min(1.0, float(explicit_threshold)))
        except (TypeError, ValueError):
            pass
    if _clean_text(target.get("asset_kind")) == "background":
        return BACKGROUND_REUSE_THRESHOLD
    return policy_reuse_threshold_for_target(target)


def _aspect_ratio_score(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    target_ratio = _clean_text(target.get("aspect_ratio"))
    candidate_ratio = _clean_text(candidate.get("aspect_ratio"))
    if not target_ratio or not candidate_ratio:
        return 0.5
    if target_ratio == candidate_ratio:
        return 1.0
    target_orientation = _ratio_orientation(target_ratio)
    candidate_orientation = _ratio_orientation(candidate_ratio)
    return 0.6 if target_orientation and target_orientation == candidate_orientation else 0.2


def _ratio_orientation(value: str) -> str:
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
        input_image_path = _resolve_asset_image_path(session_root, asset.get("image_path"))
        if not asset_id or input_image_path is None or not input_image_path.exists():
            warnings.append(f"library ingest skipped missing image for {asset_id or '<missing asset_id>'}")
            continue

        suffix = input_image_path.suffix.lower()
        if suffix not in _IMAGE_SUFFIXES:
            suffix = input_image_path.suffix or ".img"
        dest_rel = f"{DEFAULT_LIBRARY_IMAGE_DIR}/{asset_id}{suffix}"
        dest_path = library_root / dest_rel
        shutil.copy2(input_image_path, dest_path)

        asset["image_path"] = dest_rel
        _normalize_rich_asset_fields(asset)
        copied_assets.append(asset)

    copied["output_root"] = str(library_root)
    copied["assets"] = copied_assets
    copied["asset_count"] = len(copied_assets)
    return copied


def _read_existing_db(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "warnings": [f"existing library DB could not be read: {path}"],
        }
    return data if isinstance(data, dict) else {"warnings": [f"existing library DB is not an object: {path}"]}


def _read_match_index_or_build(library_root: Path, db: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    index_path = library_root / DEFAULT_MATCH_INDEX_FILENAME
    index = _read_existing_db(index_path)
    index_assets = index.get("assets")
    db_assets = db.get("assets")
    if isinstance(index_assets, list) and int(index.get("schema_version") or 0) == MATCH_INDEX_SCHEMA_VERSION:
        db_asset_count = len(db_assets) if isinstance(db_assets, list) else None
        if db_asset_count is None or int(index.get("input_asset_count") or -1) == db_asset_count:
            embedding_report = _ensure_ai_image_embedding_index(index, library_root)
            if embedding_report:
                index["embedding_index"] = embedding_report
            return index, index_path

    if isinstance(db_assets, list):
        index = build_ai_image_match_index(db, library_root=library_root)
        try:
            embedding_report = write_ai_image_embedding_index(index, library_root)
            if embedding_report:
                index["embedding_index"] = embedding_report
            index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return index, index_path

    return {"schema_version": MATCH_INDEX_SCHEMA_VERSION, "asset_count": 0, "assets": []}, index_path


def _ensure_ai_image_embedding_index(match_index: dict[str, Any], library_root: Path) -> dict[str, Any]:
    model_name = _embedding_model_name()
    if _embedding_disabled():
        return {"enabled": False, "reason": "disabled_by_environment", "model": model_name}
    index_path = library_root / DEFAULT_EMBEDDING_INDEX_FILENAME
    meta_path = library_root / DEFAULT_EMBEDDING_META_FILENAME
    meta = _read_json_if_exists(meta_path)
    assets = match_index.get("assets")
    expected_count = len(assets) if isinstance(assets, list) else 0
    if (
        index_path.exists()
        and meta_path.exists()
        and int(meta.get("schema_version") or 0) == EMBEDDING_INDEX_SCHEMA_VERSION
        and _clean_text(meta.get("model")) == model_name
        and int(meta.get("asset_count") or -1) == expected_count
    ):
        return {
            "enabled": True,
            "model": model_name,
            "index_path": str(index_path),
            "meta_path": str(meta_path),
            "asset_count": expected_count,
            "background_color_bias_asset_count": int(meta.get("background_color_bias_asset_count") or 0),
            "context_asset_count": int(meta.get("context_asset_count") or 0),
            "vector_dim": int(meta.get("vector_dim") or 0),
        }
    return write_ai_image_embedding_index(match_index, library_root)


def _read_ai_image_embedding_index(library_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    model_name = _embedding_model_name()
    if _embedding_disabled():
        return {}, {"enabled": False, "reason": "disabled_by_environment", "model": model_name}
    index_path = library_root / DEFAULT_EMBEDDING_INDEX_FILENAME
    meta_path = library_root / DEFAULT_EMBEDDING_META_FILENAME
    if not index_path.exists() or not meta_path.exists():
        return {}, {"enabled": False, "reason": "missing_embedding_index", "model": model_name}
    try:
        import numpy as np

        data = np.load(index_path, allow_pickle=False)
        asset_ids = [str(item) for item in data["asset_ids"].tolist()]
        vectors = np.asarray(data["vectors"], dtype="float32")
        background_color_bias_asset_ids: list[str] = []
        background_color_bias_vectors = None
        if "background_color_bias_asset_ids" in data.files and "background_color_bias_vectors" in data.files:
            background_color_bias_asset_ids = [
                str(item) for item in data["background_color_bias_asset_ids"].tolist()
            ]
            background_color_bias_vectors = np.asarray(data["background_color_bias_vectors"], dtype="float32")
        context_asset_ids: list[str] = []
        context_vectors = None
        if "context_asset_ids" in data.files and "context_vectors" in data.files:
            context_asset_ids = [str(item) for item in data["context_asset_ids"].tolist()]
            context_vectors = np.asarray(data["context_vectors"], dtype="float32")
        meta = _read_json_if_exists(meta_path)
    except Exception as exc:
        return {}, {
            "enabled": False,
            "reason": "embedding_index_read_failed",
            "model": model_name,
            "warnings": [f"AI image embedding index could not be read: {str(exc)[:180]}"],
        }
    return {
        "asset_ids": asset_ids,
        "vectors": vectors,
        "background_color_bias_asset_ids": background_color_bias_asset_ids,
        "background_color_bias_vectors": background_color_bias_vectors,
        "context_asset_ids": context_asset_ids,
        "context_vectors": context_vectors,
        "meta": meta,
    }, {
        "enabled": True,
        "model": _clean_text(meta.get("model")) or model_name,
        "index_path": str(index_path),
        "meta_path": str(meta_path),
        "asset_count": len(asset_ids),
        "background_color_bias_asset_count": len(background_color_bias_asset_ids),
        "context_asset_count": len(context_asset_ids),
        "vector_dim": int(vectors.shape[1]) if len(vectors.shape) == 2 else 0,
    }


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
    if _duplicate_identity_constraints_conflict(left, right):
        return False
    return _match_asset_similarity(left, right) >= 0.86


def _duplicate_identity_constraints_conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_terms = _duplicate_identity_constraint_terms(left)
    right_terms = _duplicate_identity_constraint_terms(right)
    for kind in set(left_terms) & set(right_terms):
        if left_terms[kind] and right_terms[kind] and left_terms[kind].isdisjoint(right_terms[kind]):
            return True
    return False


def _duplicate_identity_constraint_terms(asset: dict[str, Any]) -> dict[str, set[str]]:
    terms: dict[str, set[str]] = {}
    for constraint in normalize_constraints(asset.get("constraints")):
        try:
            importance = int(constraint.get("importance") or 0)
        except (TypeError, ValueError):
            importance = 0
        if importance < 2:
            continue
        kind = _clean_text(constraint.get("kind"))
        subtype = _clean_text(constraint.get("subtype")).casefold()
        if kind in {"text", "math", "physics"} or (
            kind == "entity" and subtype in {"named_individual", "species_instance"}
        ):
            value = _normalize_constraint_for_match(kind, constraint.get("value"))
            if value:
                terms.setdefault(kind, set()).add(value)
    return terms


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


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load_reused_image_paths(session_dir: Path) -> set[str]:
    manifest = _read_json_if_exists(session_dir / "materials" / REUSE_MANIFEST_FILENAME)
    entries = manifest.get("reused_assets")
    if not isinstance(entries, list):
        return set()
    paths: set[str] = set()
    for item in entries:
        image_path = _clean_text(_dict(item).get("image_path"))
        if image_path:
            paths.add(image_path.replace("\\", "/"))
    return paths


def _is_reused_image_path(image_path: Path, session_dir: Path, reused_image_paths: set[str] | None) -> bool:
    if not reused_image_paths:
        return False
    try:
        rel_path = image_path.resolve().relative_to(session_dir.resolve()).as_posix()
    except ValueError:
        rel_path = image_path.as_posix()
    return rel_path in reused_image_paths


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
            asset_id = _clean_text(asset.get("asset_id"))
            if asset_id:
                by_id[asset_id] = asset

    for asset in incoming.get("assets", []):
        if isinstance(asset, dict):
            asset_id = _clean_text(asset.get("asset_id"))
            if asset_id:
                by_id[asset_id] = asset

    assets = []
    for asset in by_id.values():
        normalized_asset = deepcopy(asset)
        _normalize_rich_asset_fields(normalized_asset)
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


def _resolve_asset_image_path(root: Path, image_path: Any) -> Path | None:
    text = _clean_text(image_path)
    if not text:
        return None
    path = Path(text)
    return path if path.is_absolute() else root / path


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_clean_text(item) for item in value if _clean_text(item)]
    text = _clean_text(value)
    return [text] if text else []


def _preserve_vlm_fields(asset: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(asset[key]) for key in _VLM_RICH_ASSET_FIELDS if key in asset}


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


def normalize_grade_info(*texts: Any) -> dict[str, Any]:
    combined = _join_texts(*texts)
    grade_number = _infer_grade_number(combined)
    if grade_number is not None:
        return {
            "grade_norm": _GRADE_NAMES.get(grade_number, f"{grade_number}年级"),
            "grade_band": _LOW_GRADE_BAND if grade_number <= 3 else _HIGH_GRADE_BAND,
        }

    normalized = _clean_text(infer_grade(combined))
    band = ""
    if _LOW_GRADE_BAND in combined or _LOW_GRADE_BAND in normalized:
        band = _LOW_GRADE_BAND
    elif _HIGH_GRADE_BAND in combined or _HIGH_GRADE_BAND in normalized:
        band = _HIGH_GRADE_BAND
    return {
        "grade_norm": normalized,
        "grade_band": band or infer_grade_band(normalized),
    }


def _infer_grade_number(text: Any) -> int | None:
    combined = _clean_text(text)
    if not combined:
        return None
    if combined.isdigit():
        number = int(combined)
        return number if 1 <= number <= 12 else None

    match = re.search(r"(\d{1,2})\s*年级", combined)
    if match:
        number = int(match.group(1))
        return number if 1 <= number <= 12 else None

    match = re.search(r"([一二两三四五六七八九十]{1,3})年级", combined)
    if match:
        number = _chinese_grade_number(match.group(1))
        return number if number is not None and 1 <= number <= 12 else None

    match = re.search(r"初中?([123一二三])", combined)
    if match:
        offset = _grade_digit(match.group(1))
        return 6 + offset if offset is not None else None

    match = re.search(r"高中?([123一二三])", combined)
    if match:
        offset = _grade_digit(match.group(1))
        return 9 + offset if offset is not None else None

    return None


def _grade_digit(value: str) -> int | None:
    value = _clean_text(value)
    if value.isdigit():
        return int(value)
    return _CHINESE_GRADE_DIGITS.get(value)


def _chinese_grade_number(value: str) -> int | None:
    cleaned = _clean_text(value)
    if cleaned in _CHINESE_GRADE_DIGITS:
        return _CHINESE_GRADE_DIGITS[cleaned]
    if cleaned == "十":
        return 10
    if cleaned.startswith("十"):
        ones = _CHINESE_GRADE_DIGITS.get(cleaned[1:], 0) if len(cleaned) > 1 else 0
        return 10 + ones
    if "十" in cleaned:
        left, right = cleaned.split("十", 1)
        tens = _CHINESE_GRADE_DIGITS.get(left, 1) if left else 1
        ones = _CHINESE_GRADE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def infer_grade(*texts: Any) -> str:
    """Infer grade from topic/audience/template text, returning an empty string if unknown."""

    combined = _join_texts(*texts)
    match = _GRADE_RE.search(combined)
    if match:
        return _normalize_grade(match.group(1))
    if "低年级" in combined:
        return "低年级"
    if "高年级" in combined:
        return "高年级"
    if "小学" in combined:
        return "小学"
    if "初中" in combined:
        return "初中"
    if "高中" in combined:
        return "高中"
    return ""


def infer_grade_band(*texts: Any) -> str:
    """Map concrete grades to the reuse bands used by image matching."""

    inferred_number = _infer_grade_number(_join_texts(*texts))
    if inferred_number is not None:
        return _LOW_GRADE_BAND if inferred_number <= 3 else _HIGH_GRADE_BAND

    combined = _normalize_grade(_join_texts(*texts))
    if not combined:
        return ""
    if "低年级" in combined:
        return "低年级"
    if any(term in combined for term in ("高年级", "初中", "高中", "初一", "初二", "初三", "高一", "高二", "高三")):
        return "高年级"
    return ""


def infer_subject(*texts: Any) -> str:
    """Infer subject from topic/audience/page text, returning an empty string if unknown."""

    combined = _join_texts(*texts).casefold()
    for subject, keywords in _SUBJECT_KEYWORDS:
        if any(keyword.casefold() in combined for keyword in keywords):
            return subject
    return ""


def _iter_session_dirs(root: Path):
    if (root / "plan.json").exists():
        yield root
        return
    if not root.exists():
        return
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if child.is_dir() and (child / "plan.json").exists():
            yield child


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_context(plan: dict[str, Any]) -> dict[str, str]:
    meta = _dict(plan.get("meta"))
    routing = _dict(plan.get("style_routing"))
    topic = _clean_text(meta.get("topic"))
    audience = _clean_text(meta.get("audience"))
    purpose = _clean_text(meta.get("purpose"))
    style_direction = _clean_text(meta.get("style_direction"))
    template_family = _clean_text(routing.get("template_family"))
    style_name = _clean_text(routing.get("style_name"))

    grade = _clean_text(meta.get("grade")) or infer_grade(
        topic,
        audience,
        template_family,
        style_name,
    )
    subject = _clean_text(meta.get("subject")) or infer_subject(
        topic,
        audience,
        purpose,
        style_direction,
    )

    return {
        "theme": topic,
        "grade": grade,
        "subject": subject,
    }


def _build_background_asset(
    *,
    root: Path,
    session_dir: Path,
    plan_path: Path,
    materials_dir: Path,
    context: dict[str, str],
    plan: dict[str, Any],
    reused_image_paths: set[str] | None = None,
) -> dict[str, Any] | None:
    from edupptx.materials.background_generator import build_background_content_prompt

    visual = _dict(plan.get("visual"))
    prompt = _clean_text(build_background_content_prompt(visual))
    image_path = materials_dir / "background.png"
    if not prompt or not image_path.exists():
        return None
    if _is_reused_image_path(image_path, session_dir, reused_image_paths):
        return None

    return _make_asset(
        root=root,
        session_dir=session_dir,
        image_path=image_path,
        prompt=prompt,
        context=context,
        asset_kind="background",
        page_title="",
        role="background",
        aspect_ratio="16:9",
        background_route=_build_background_route(plan),
    )


def _build_background_route(plan: dict[str, Any]) -> dict[str, Any]:
    visual = _dict(plan.get("visual"))
    routing = _dict(plan.get("style_routing"))
    route = {
        "template_family": routing.get("template_family"),
        "style_name": routing.get("style_name"),
        "palette_id": routing.get("palette_id"),
        "primary_color": visual.get("primary_color"),
        "secondary_color": visual.get("secondary_color"),
        "accent_color": visual.get("accent_color"),
        "card_bg_color": visual.get("card_bg_color"),
        "secondary_bg_color": visual.get("secondary_bg_color"),
        "background_color_bias": visual.get("background_color_bias"),
    }
    color_terms = [
        visual.get("primary_color"),
        visual.get("secondary_color"),
        visual.get("accent_color"),
        visual.get("background_color_bias"),
    ]
    cleaned = _clean_background_route(route)
    terms = _dedupe_terms([_clean_text(item) for item in color_terms if _clean_text(item)])
    if terms:
        cleaned["color_terms"] = terms
    return cleaned


def _iter_page_image_assets(
    *,
    root: Path,
    session_dir: Path,
    plan_path: Path,
    materials_dir: Path,
    context: dict[str, str],
    page: dict[str, Any],
    page_index: int,
    reused_image_paths: set[str] | None = None,
):
    needs = _dict(page.get("material_needs"))
    images = needs.get("images")
    if not isinstance(images, list):
        return

    page_number = _as_int(page.get("page_number"))
    if page_number is None:
        return

    role_counts: dict[str, int] = {}
    for image_index, image_need in enumerate(images):
        if not isinstance(image_need, dict):
            continue
        if image_need.get("source") != "ai_generate":
            continue

        role = _clean_text(image_need.get("role")) or "illustration"
        role_counts[role] = role_counts.get(role, 0) + 1
        prompt = _clean_text(image_need.get("query"))
        if not prompt:
            continue
        prompt_route = _clean_prompt_route(image_need.get("prompt_route"))

        image_path = _find_page_image_path(materials_dir, page_number, role, role_counts[role])
        if image_path is None:
            continue
        if _is_reused_image_path(image_path, session_dir, reused_image_paths):
            continue

        yield _make_asset(
            root=root,
            session_dir=session_dir,
            image_path=image_path,
            prompt=prompt,
            prompt_route=prompt_route,
            context=context,
            asset_kind="page_image",
            page_title=_clean_text(page.get("title")),
            role=role,
            aspect_ratio=_clean_text(image_need.get("aspect_ratio")),
            page_type=_clean_text(page.get("page_type")),
        )


def _find_page_image_path(materials_dir: Path, page_number: int, role: str, occurrence: int) -> Path | None:
    stem = f"page_{page_number:02d}_{role}_{occurrence}"
    for suffix in _IMAGE_SUFFIXES:
        path = materials_dir / f"{stem}{suffix}"
        if path.exists():
            return path
    matches = sorted(materials_dir.glob(f"{stem}.*"))
    return matches[0] if matches else None


def _make_asset(
    *,
    root: Path,
    session_dir: Path,
    image_path: Path,
    prompt: str,
    context: dict[str, str],
    asset_kind: str,
    page_title: str,
    role: str = "",
    aspect_ratio: str = "",
    page_type: str = "",
    prompt_route: dict[str, Any] | None = None,
    background_route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rel_image_path = _relative_path(image_path, root)
    route = _clean_prompt_route(prompt_route)
    bg_route = _clean_background_route(background_route)
    content_prompt = _clean_text(prompt)
    if asset_kind == "background":
        content_prompt = _strip_background_color_bias_from_prompt(
            content_prompt,
            _clean_text(bg_route.get("background_color_bias")),
        )
    asset_key = "|".join(
        [
            session_dir.name,
            asset_kind,
            rel_image_path,
            content_prompt,
            context.get("theme", ""),
            context.get("grade", ""),
            context.get("subject", ""),
        ]
    )
    asset_id = "aiimg_" + hashlib.sha256(asset_key.encode("utf-8")).hexdigest()[:20]
    grade_info = normalize_grade_info(context.get("grade"), context.get("theme"))
    normalized_prompt = content_prompt[:80]
    context_summary = _default_context_summary(
        asset_kind=asset_kind,
        content_prompt=content_prompt,
        theme=context.get("theme", ""),
        page_title=page_title,
        page_type=page_type,
    )
    teaching_intent = _default_teaching_intent(asset_kind=asset_kind, page_type=page_type)
    topic_refs = extract_topic_refs(context.get("theme", ""))
    if asset_kind == "background":
        return {
            "asset_id": asset_id,
            "asset_kind": "background",
            "image_path": rel_image_path,
            "aspect_ratio": aspect_ratio,
            "role": "background",
            "theme": context.get("theme", ""),
            "subject": context.get("subject", ""),
            "grade_norm": grade_info["grade_norm"],
            "grade_band": grade_info["grade_band"],
            "topic_refs": topic_refs,
            "content_prompt": content_prompt,
            "background_route": bg_route,
            "normalized_prompt": normalized_prompt,
            "context_summary": context_summary,
            "teaching_intent": teaching_intent,
            "core_keywords": [],
            "semantic_aliases": {},
            "context_summary_keywords": [],
        }

    return {
        "asset_id": asset_id,
        "asset_kind": "page_image",
        "image_path": rel_image_path,
        "aspect_ratio": aspect_ratio,
        "role": role,
        "page_type": page_type,
        "theme": context.get("theme", ""),
        "subject": context.get("subject", ""),
        "grade_norm": grade_info["grade_norm"],
        "grade_band": grade_info["grade_band"],
        "topic_refs": topic_refs,
        "content_prompt": content_prompt,
        "context_summary": context_summary,
        "teaching_intent": teaching_intent,
        "context_summary_keywords": [],
        "asset_category": "unknown",
        "constraints": [],
        "core_keywords": [],
        "semantic_aliases": {},
        "duplicate_asset_ids": [],
    }


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path.resolve())


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _join_texts(*texts: Any) -> str:
    return "\n".join(_clean_text(text) for text in texts if _clean_text(text))


def _normalize_grade(value: str) -> str:
    return value.replace("初1", "初一").replace("初2", "初二").replace("初3", "初三").replace(
        "高1", "高一"
    ).replace("高2", "高二").replace("高3", "高三")
