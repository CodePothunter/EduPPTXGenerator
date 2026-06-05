"""Offline builder for the generated AI image asset database."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from loguru import logger as PROGRESS_LOGGER

from edupptx.materials.reuse_policy import (
    BACKGROUND_REUSE_THRESHOLD,
    T_GAP,
    T_DIRECT,
    T_REJECT,
    decide_reuse,
    evaluate_aspect_transform,
    evaluate_reuse_filter,
    normalize_reuse_policy_fields,
    reuse_threshold_for_target as policy_reuse_threshold_for_target,
)
from edupptx.materials.vlm_metadata_rules import (
    normalize_padding_capacity,
)

SCHEMA_VERSION = 1
KEYWORD_SCHEMA_VERSION = 14
DEFAULT_DB_FILENAME = "ai_image_asset_db.json"
DEFAULT_MATCH_INDEX_FILENAME = "ai_image_match_index.json"
DEFAULT_EMBEDDING_INDEX_FILENAME = "ai_image_embedding_index.npz"
DEFAULT_EMBEDDING_META_FILENAME = "ai_image_embedding_meta.json"
DEFAULT_QUERY_EMBEDDING_CACHE_FILENAME = "ai_image_query_embedding_cache.npz"
DEFAULT_QUERY_EMBEDDING_CACHE_META_FILENAME = "ai_image_query_embedding_cache_meta.json"
DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
MATCH_INDEX_SCHEMA_VERSION = 14
EMBEDDING_INDEX_SCHEMA_VERSION = 5
QUERY_EMBEDDING_CACHE_SCHEMA_VERSION = 1
DEFAULT_KEYWORD_BATCH_SIZE = 8
DEFAULT_EMBEDDING_BATCH_SIZE = 16
DEFAULT_REUSE_MAX_WORKERS = 4
DEFAULT_LIBRARY_IMAGE_DIR = "ai_images"
REUSE_MANIFEST_FILENAME = "ai_image_reuse_manifest.json"
REUSE_DEBUG_FILENAME = "ai_image_reuse_debug.json"
KEYWORD_REUSE_RULES_REFERENCE = Path(__file__).resolve().parent / "Reference" / "ai_image_reuse_metadata_rules.md"
REUSE_REVIEW_SCORE_RULES_REFERENCE = Path(__file__).resolve().parent / "Reference" / "ai_image_reuse_review_score_rules.md"
DEFAULT_REUSE_CANDIDATE_LIMIT = 8
DEFAULT_MIN_REUSE_KEYWORD_SCORE: float | None = None
DEFAULT_HYBRID_RETRIEVAL_POOL_SIZE = 20
DEFAULT_RRF_K = 60
HYBRID_BM25_WEIGHT = 0.25
HYBRID_EMBEDDING_WEIGHT = 0.55
HYBRID_SUBSTRING_WEIGHT = 0.20
BM25_GRAY_REUSE_THRESHOLD = 0.23
EMBEDDING_GRAY_REUSE_THRESHOLD = 0.72
STRICT_REUSE_MAX_PER_SESSION = 2
REUSE_REVIEW_ACCEPT_SCORE_THRESHOLD = 0.60
# Per-query LLM review budget. Caps the number of llm_review calls made
# for a single target so a noisy candidate pool can't burn the LLM on a
# long tail of equivalent-quality candidates after the top contender has
# already been judged. K=5 gives embedding-first ordering enough room to
# recover strong semantic matches without opening the full candidate tail.
MAX_LLM_REVIEWS_PER_QUERY = 5
MAX_LLM_REVIEW_WORKERS = 15


def _get_llm_max_workers() -> int:
    raw = os.environ.get("EDUPPTX_LLM_MAX_WORKERS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return MAX_LLM_REVIEW_WORKERS


def _review_worker_count(num_review_targets: int) -> int:
    """单个 target 的候选复审并发数。

    受每查询复审预算约束，绝不超过实际会被复审的候选数
    （≤ MAX_LLM_REVIEWS_PER_QUERY），同时尊重 _get_llm_max_workers() 这一全局上限。
    """
    return max(1, min(_get_llm_max_workers(), MAX_LLM_REVIEWS_PER_QUERY, max(0, int(num_review_targets))))


ASPECT_REUSE_BUCKETS = ("1:1", "3:4", "4:3", "9:16", "16:9", "other")
_ASPECT_REUSE_BUCKET_VALUES = {
    "1:1": 1.0,
    "3:4": 3 / 4,
    "4:3": 4 / 3,
    "9:16": 9 / 16,
    "16:9": 16 / 9,
}
_ASPECT_BUCKET_MAX_LOSS = 0.08

ASPECT_RATIO_TOLERANCE_SAME = 0.08
ASPECT_RATIO_TOLERANCE_ADJACENT = 0.15
ASPECT_RATIO_ADJACENT_PENALTY = 0.05
ALLOWED_CROSS_ASPECT_RATIO_REUSE_PAIRS = frozenset(
    {
        ("4:3", "16:9"),
        ("16:9", "4:3"),
        ("3:4", "9:16"),
        ("9:16", "3:4"),
        ("4:3", "1:1"),
        ("1:1", "4:3"),
        ("3:4", "1:1"),
        ("1:1", "3:4"),
    }
)

# Q1/P7: embedding-keyword consistency gate.
#
# A candidate where the dense semantic score (embedding) sits far above the
# lexical score (keyword/BM25) is the structural signature of "same topic,
# different content". In session_20260523_012722 every LLM rejection of an
# allegedly-relevant 凸透镜 candidate fit this pattern: e≥0.7, k≤0.3.
#
# The threshold is intentionally one number per gate, not per subject. The
# gate is bypassed when target/candidate retrieval text shares at least one
# normalized token. That catches cases where the lexical mismatch is accidental
# while still treating a large embedding-vs-keyword gap as a "wrong content"
# signal without burning an LLM call.
EMBEDDING_KEYWORD_GAP_REJECT_THRESHOLD = 0.40

# R5: near-miss VLM image verification.
#
# When the text-only LLM reviewer gives a candidate a score that *just*
# misses the accept threshold (within R5_NEAR_MISS_EPSILON), the metadata
# cannot decide between "the picture is actually fine" and "the metadata
# accidentally omits a discriminating tag". The fix is to look at the
# actual image with a VLM. Trade-off: one VLM call (~5-10s) per session
# is far cheaper than the 30s+ image regeneration the rejection would
# otherwise trigger.
#
# Per-session budget is enforced through ``reuse_session_state`` so the
# fallback cannot run away on a session with many near-misses.
R5_NEAR_MISS_EPSILON = 0.05
R5_MAX_VLM_CALLS_PER_SESSION = 3
R5_SESSION_VLM_COUNT_KEY = "near_miss_vlm_calls_used"
# Deterministic LLM reject is now signalled directly by the policy via the
# ``llm_skip_safe`` field on policy_result.
LOGGER = logging.getLogger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_OUTPUT_PATH_MARKERS = (
    "output",
    "materials_library",
    "materials_library_ppt",
    "report",
)


def _relative_output_path(value: Any) -> str:
    """Return repo-relative paths for JSON reports/debug payloads."""

    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    if "://" in raw:
        return raw

    normalized = raw.replace("\\", "/")
    path = Path(raw).expanduser()
    native_abs = path.is_absolute()
    windows_abs = bool(re.match(r"^[A-Za-z]:[\\/]", raw))
    posix_abs = normalized.startswith("/")
    if not (native_abs or windows_abs or posix_abs):
        return normalized

    if native_abs:
        try:
            resolved = path.resolve(strict=False)
        except Exception:
            resolved = path
        roots = (_PROJECT_ROOT, Path.cwd().resolve())
        for root in roots:
            try:
                return resolved.relative_to(root).as_posix()
            except ValueError:
                continue

    parts = [part for part in normalized.split("/") if part]
    if _PROJECT_ROOT.name in parts:
        start = parts.index(_PROJECT_ROOT.name) + 1
        if start < len(parts):
            return "/".join(parts[start:])
    for marker in _OUTPUT_PATH_MARKERS:
        if marker in parts:
            return "/".join(parts[parts.index(marker) :])
    return normalized


def _relative_output_context(context: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(context or {})
    for key in (
        "reuse_library_dir",
        "library_dir",
        "asset_root",
        "output_root",
        "session_dir",
        "plan_file",
        "debug_path",
        "match_index_path",
        "db_path",
    ):
        if key in payload:
            payload[key] = _relative_output_path(payload.get(key))
    return payload

@dataclass
class ReuseSearchContext:
    """Per-generation cache for repeated AI image reuse lookups.

    A PPT can query the same material libraries dozens of times. Keeping this
    object for one generation avoids rereading JSON/NPZ sidecars and
    re-encoding identical target embedding texts for each image slot.
    """

    library_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    route_index_cache: dict[tuple[str, str], tuple[dict[str, Any], Path, list[Any], str] | None] = field(
        default_factory=dict
    )
    target_keyword_cache: dict[str, Any] = field(default_factory=dict)
    query_embedding_cache: dict[str, Any] = field(default_factory=dict)
    query_embedding_cache_dir: Path | None = None
    eligible_static_cache: dict[tuple[str, str, str, str], list[dict[str, Any]]] = field(default_factory=dict)
    cache_lock: Any = field(default_factory=threading.RLock, repr=False)

BACKGROUND_REUSE_GATE_THRESHOLDS = {
    "keyword_min": 0.00,
    "embedding_min": 0.42,
    "keyword_high": 0.32,
    "embedding_high": 0.70,
    "keyword_gray_high": 0.18,
    "embedding_gray_low": 0.52,
    "embedding_gray_high": 0.60,
    "keyword_gray_low": 0.00,
}
PAGE_IMAGE_REUSE_GATE_THRESHOLDS = {
    "loose": {
        "keyword_min": 0.00,
        "embedding_min": 0.46,
        "keyword_high": 0.50,
        "embedding_high": 0.70,
        "keyword_gray_high": 0.20,
        "embedding_gray_low": 0.52,
        "embedding_gray_high": 0.60,
        "keyword_gray_low": 0.00,
    },
    "medium": {
        "keyword_min": 0.00,
        "embedding_min": 0.50,
        "keyword_high": 0.52,
        "embedding_high": 0.72,
        "keyword_gray_high": 0.22,
        "embedding_gray_low": 0.55,
        "embedding_gray_high": 0.62,
        "keyword_gray_low": 0.00,
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

KEYWORD_LED_LLM_REVIEW_MIN_KEYWORD = 0.28
KEYWORD_LED_LLM_REVIEW_MIN_EMBEDDING = 0.60
EMBEDDING_LED_LLM_REVIEW_MIN_KEYWORD = 0.10
EMBEDDING_LED_LLM_REVIEW_MIN_SUBSTRING = 0.10

# Pre-LLM embedding floor: candidates routed to llm_review whose embedding
# falls below this are deterministically rejected without an LLM call.
# Calibrated on the materials_library_ppt eval: every acc/best LLM-required
# candidate carried embedding >= 0.572, while ~61% of LLM-bound rejects
# scored below 0.55 (often 0.0 from BM25-only acceptance paths).
EMBEDDING_PRE_LLM_FLOOR = 0.55
# Embedding rescue floor. Keyword-sparse candidates can fall below T_REJECT
# even with strong semantic similarity; route those to LLM review instead of
# silently hard-rejecting them.
EMBED_RESCUE_FLOOR = float(os.environ.get("EDUPPTX_REUSE_EMBED_RESCUE_FLOOR", "0.70"))
TEXT_OVERLAP_REVIEW_THRESHOLD = 0.15
TEXT_OVERLAP_EMBEDDING_THRESHOLD = 0.78

CONTENT_PROMPT_REUSE_WEIGHT = 0.85
ROUTE_REUSE_WEIGHT = 0.05
ASPECT_REUSE_WEIGHT = 0.05
LIGHT_CONTEXT_REUSE_WEIGHT = 0.05
BACKGROUND_CONTENT_PROMPT_REUSE_WEIGHT = 0.85
BACKGROUND_COLOR_BIAS_REUSE_WEIGHT = 0.15

VISUAL_GENERIC_REUSE_THRESHOLD = 0.28
# BACKGROUND_REUSE_THRESHOLD imported from reuse_policy — single source of truth.

_EMBEDDING_MODEL_CACHE: dict[str, Any] = {}
_EMBEDDING_MODEL_LOCK = threading.RLock()

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
_REVIEW_PASSTHROUGH_FIELDS = (
    "vlm_match_quality",
    "regenerate",
)
_STRICT_REUSE_PASSTHROUGH_FIELDS = (
    "strict_reuse_group",
    "strict_reuse_secondary_group",
    "secondary_reuse_query",
    "secondary_reuse_caption",
    "strict_reuse_confidence",
    "strict_reuse_reason",
    "strict_reuse_signals",
)
_PPT_COMPARISON_PASSTHROUGH_FIELDS = (
    "vlm_caption",
    "vlm_general",
    "llm_general",
)
_METADATA_PASSTHROUGH_FIELDS = (
    *_REVIEW_PASSTHROUGH_FIELDS,
    *_STRICT_REUSE_PASSTHROUGH_FIELDS,
    *_PPT_COMPARISON_PASSTHROUGH_FIELDS,
)
_REUSE_TARGET_METADATA_SEEDED_FIELD = "_reuse_target_metadata_seeded"
_PAGE_REUSE_TARGET_METADATA_FIELDS = (
    "caption",
    "context_summary",
    "teaching_intent",
    "subject",
    "grade_norm",
    "grade_band",
    "general",
    "strict_reuse_group",
    "strict_reuse_secondary_group",
    "secondary_reuse_query",
    "secondary_reuse_caption",
    "strict_reuse_confidence",
    "strict_reuse_reason",
    "strict_reuse_signals",
)
_BACKGROUND_REUSE_TARGET_METADATA_FIELDS = (
    "normalized_prompt",
    "color_temperature",
    "context_summary",
    "teaching_intent",
    "subject",
    "grade_norm",
    "grade_band",
    "general",
    "strict_reuse_group",
    "strict_reuse_secondary_group",
    "strict_reuse_confidence",
    "strict_reuse_reason",
    "strict_reuse_signals",
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
_GENERAL_REUSE_GROUP = "C03_scene_decor_container"
_CONTENT_REUSE_GROUP = "C00_strict_text_problem_skip"
GENERAL_REUSE_GROUP = _GENERAL_REUSE_GROUP
CONTENT_REUSE_GROUP = _CONTENT_REUSE_GROUP
STRICT_REUSE_INDEX_DIRNAME = "strict_reuse_indexes"
BACKGROUND_REUSE_INDEX_GROUP = "background"
BACKGROUND_REUSE_INDEX_FILENAME = "background.json"
STRICT_REUSE_GROUPS = (
    "C00_strict_text_problem_skip",
    "C01_irreplaceable_entity_event_action",
    "C02_generic_subject_object",
    "C03_scene_decor_container",
)
LEGACY_STRICT_REUSE_GROUPS: tuple[str, ...] = ()
_STRICT_REUSE_READ_GROUPS = STRICT_REUSE_GROUPS
_PAGE_TYPE_CONTEXT_SUMMARIES = {
    "cover": "作为封面主视觉，建立课程主题和导入氛围",
    "toc": "作为目录页辅助导览插图，引导学生理解本节课学习路径",
    "content": "作为内容页辅助说明插图，帮助学生理解本页知识点",
    "exercise": "作为练习页辅助插图，帮助学生理解互动任务",
    "summary": "作为总结页辅助记忆插图，帮助学生回顾核心内容",
    "closing": "作为结束页辅助插图，形成课程收束氛围",
}
# Single source of truth for "style / form / usage / quality noise tokens
# that saturate the library". Two assets sharing any of these does not
# constitute precision evidence — only sharing a more discriminative
# keyword does.
_NOISE_TOKENS = frozenset({
    # Form/medium descriptors
    "插画", "教学插画", "配图", "主图", "图标", "logo",
    "背景", "场景", "示意图", "图片",
    # Style/quality descriptors
    "编辑感", "风格", "风格统一",
    "简洁", "清晰", "简洁清晰", "高清",
    "背景简洁",
    # Usage / negative descriptors
    "无文字", "无文字水印",
    "教学示意",
    # Audience / subject descriptors
    "语文教学",
    "高年级", "低年级",
    "高年级风格", "低年级风格", "高年级编辑感",
    # Tooling
    "ppt", "ai",
})
_PRECISION_SIGNAL_STOPWORDS = frozenset(s.casefold() for s in _NOISE_TOKENS)
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
_LOW_GRADE_BAND = "低年级"
_HIGH_GRADE_BAND = "高年级"
_OTHER_GRADE = "其他"
_OTHER_SUBJECT = "其他"
_ALLOWED_GRADE_NORMS = frozenset(
    (
        "一年级",
        "二年级",
        "三年级",
        "四年级",
        "五年级",
        "六年级",
        "七年级",
        "八年级",
        "九年级",
        "高一",
        "高二",
        "高三",
        _OTHER_GRADE,
    )
)
_ALLOWED_GRADE_BANDS = frozenset((_LOW_GRADE_BAND, _HIGH_GRADE_BAND, _OTHER_GRADE))
_ALLOWED_SUBJECTS = frozenset(("语文", "数学", "物理", _OTHER_SUBJECT))
_KNOWN_SUBJECTS = frozenset({"语文", "数学", "物理"})
_LOW_GRADE_NORMS = frozenset(("一年级", "二年级", "三年级"))


def build_ai_image_asset_db(
    output_root: str | Path,
    *,
    target_keyword_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scan rendered sessions and return the generated-image asset database.

    The persisted fields stay focused on reusable image content:
    prompt text, route metadata, normalized prompt, context summary,
    teaching intent, and grade/subject.
    """

    root = Path(output_root).expanduser().resolve()
    assets: list[dict[str, Any]] = []
    warnings: list[str] = []

    single_session_root = (root / "plan.json").exists()
    provided_target_keyword_cache = target_keyword_cache if isinstance(target_keyword_cache, dict) else None

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
        session_target_keyword_cache = (
            provided_target_keyword_cache
            if single_session_root and provided_target_keyword_cache is not None
            else _load_session_reuse_target_keyword_cache(session_dir)
        )

        background_asset = _build_background_asset(
            root=root,
            session_dir=session_dir,
            plan_path=plan_path,
            materials_dir=materials_dir,
            context=context,
            plan=plan,
            reused_image_paths=reused_image_paths,
            target_keyword_cache=session_target_keyword_cache,
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
                target_keyword_cache=session_target_keyword_cache,
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


def write_ai_image_match_index(
    db: dict[str, Any],
    library_dir: str | Path,
    *,
    index_filename: str = DEFAULT_MATCH_INDEX_FILENAME,
    write_embedding_index: bool = True,
) -> tuple[dict[str, Any], Path]:
    """Write the split matching indexes used by image reuse matching."""

    root = Path(library_dir).expanduser().resolve()
    index = build_ai_image_match_index(db, library_root=root)
    if write_embedding_index:
        embedding_report = write_ai_image_embedding_index(index, root)
        if embedding_report:
            index["embedding_index"] = embedding_report
    split_dir = write_ai_image_split_match_indexes(index, root)
    legacy_target = root / index_filename
    if legacy_target.exists():
        legacy_target.unlink()
    return index, split_dir


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
            group_assets.append(copied)
        if group == _GENERAL_REUSE_GROUP:
            group_assets.extend(_c01_secondary_c03_projections(split_source_assets))
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
        group_path = split_dir / f"{group}.json"
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


def read_ai_image_split_match_index(
    library_dir: str | Path,
    *,
    split_dirname: str = STRICT_REUSE_INDEX_DIRNAME,
) -> tuple[dict[str, Any], Path] | None:
    """Read the binary reuse-group indexes as one in-memory index."""

    root = Path(library_dir).expanduser().resolve()
    split_dir = root / split_dirname
    if not split_dir.exists():
        return None

    assets: list[dict[str, Any]] = []
    warnings: list[str] = []
    source_payloads: list[dict[str, Any]] = []
    found = False
    background_split_path = split_dir / BACKGROUND_REUSE_INDEX_FILENAME
    has_background_split = background_split_path.exists()
    for group in _STRICT_REUSE_READ_GROUPS:
        path = split_dir / f"{group}.json"
        if not path.exists():
            continue
        found = True
        payload = _read_existing_db(path)
        source_payloads.append(payload)
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list):
            warnings.append(f"split index skipped invalid assets: {path}")
            continue
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
    if has_background_split:
        found = True
        payload = _read_existing_db(background_split_path)
        source_payloads.append(payload)
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list):
            warnings.append(f"split index skipped invalid assets: {background_split_path}")
        else:
            for item in raw_assets:
                if not isinstance(item, dict):
                    continue
                asset = deepcopy(item)
                if not _is_background_asset(asset):
                    continue
                asset["asset_kind"] = "background"
                asset["strict_reuse_group"] = _normalize_binary_reuse_group(
                    asset.get("strict_reuse_group"),
                    default=_GENERAL_REUSE_GROUP,
                )
                if _is_skip_reuse_group(asset.get("strict_reuse_group")):
                    continue
                assets.append(asset)
        warnings.extend(_as_string_list(payload.get("warnings")))
    if not found:
        return None

    now = datetime.now(timezone.utc).isoformat()
    first_payload = source_payloads[0] if source_payloads else {}
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
    return index, split_dir


def write_ai_image_embedding_index(
    match_index: dict[str, Any],
    library_dir: str | Path,
    *,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    index_filename: str = DEFAULT_EMBEDDING_INDEX_FILENAME,
    meta_filename: str = DEFAULT_EMBEDDING_META_FILENAME,
) -> dict[str, Any]:
    """Write the vector sidecar index used by hybrid image reuse retrieval.

    The build is checkpointed after each encode batch. If the process is
    interrupted, rerunning the command resumes from the checkpoint and only
    atomically replaces the final sidecar after every category is complete.
    """

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
    if not rows:
        return {
            "enabled": False,
            "reason": "empty_embedding_text",
            "model": model_name,
        }

    import numpy as np

    index_path = root / index_filename
    meta_path = root / meta_filename
    index_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_index_path = index_path.with_name(f"{index_path.stem}.checkpoint{index_path.suffix}")
    checkpoint_meta_path = meta_path.with_name(f"{meta_path.stem}.checkpoint.json")
    try:
        build_batch_size = int(
            os.environ.get("EDUPPTX_AI_IMAGE_EMBEDDING_BUILD_BATCH_SIZE")
            or DEFAULT_EMBEDDING_BATCH_SIZE
        )
    except ValueError:
        build_batch_size = DEFAULT_EMBEDDING_BATCH_SIZE
    build_batch_size = max(1, build_batch_size)

    def rows_digest(items: list[tuple[str, str]]) -> str:
        digest = hashlib.sha256()
        for asset_id, text in items:
            digest.update(asset_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(text.encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()

    category_specs = [
        {
            "name": "asset",
            "rows": rows,
            "ids_key": "asset_ids",
            "texts_key": None,
            "vectors_key": "vectors",
        },
        {
            "name": "background_color_bias",
            "rows": background_color_bias_rows,
            "ids_key": "background_color_bias_asset_ids",
            "texts_key": None,
            "vectors_key": "background_color_bias_vectors",
        },
    ]
    total_counts = {spec["name"]: len(spec["rows"]) for spec in category_specs}
    total_texts = sum(total_counts.values())
    fingerprint_payload = {
        "schema_version": EMBEDDING_INDEX_SCHEMA_VERSION,
        "model": model_name,
        "index_filename": index_filename,
        "meta_filename": meta_filename,
        "row_hashes": {
            spec["name"]: rows_digest(spec["rows"])
            for spec in category_specs
        },
    }
    build_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    encoded: dict[str, dict[str, Any]] = {
        spec["name"]: {"ids": [], "texts": [], "vectors": None}
        for spec in category_specs
    }

    def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        temp_path = path.with_name(f"{path.name}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, path)

    def write_npz_atomic(path: Path, payload: dict[str, Any]) -> None:
        temp_path = path.with_name(f"{path.name}.tmp")
        with temp_path.open("wb") as handle:
            np.savez_compressed(handle, **payload)
        os.replace(temp_path, path)

    def checkpoint_payload() -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for spec in category_specs:
            state = encoded[spec["name"]]
            vectors = state.get("vectors")
            if vectors is None:
                continue
            ids = list(state.get("ids") or [])
            if not ids:
                continue
            payload[spec["ids_key"]] = np.asarray(ids, dtype=str)
            texts_key = spec.get("texts_key")
            if texts_key:
                payload[texts_key] = np.asarray(list(state.get("texts") or []), dtype=str)
            payload[spec["vectors_key"]] = np.asarray(vectors, dtype="float32")
        return payload

    def checkpoint_meta() -> dict[str, Any]:
        encoded_counts = {
            spec["name"]: len(encoded[spec["name"]].get("ids") or [])
            for spec in category_specs
        }
        return {
            "checkpoint_schema_version": 1,
            "schema_version": EMBEDDING_INDEX_SCHEMA_VERSION,
            "build_fingerprint": build_fingerprint,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "model": model_name,
            "index_filename": index_filename,
            "meta_filename": meta_filename,
            "batch_size": build_batch_size,
            "total_counts": total_counts,
            "encoded_counts": encoded_counts,
        }

    def save_checkpoint(reason: str) -> None:
        payload = checkpoint_payload()
        if not payload:
            return
        write_npz_atomic(checkpoint_index_path, payload)
        write_json_atomic(checkpoint_meta_path, checkpoint_meta())
        encoded_total = sum(len(encoded[spec["name"]].get("ids") or []) for spec in category_specs)
        PROGRESS_LOGGER.info(
            "AI image embedding checkpoint saved: library={}, encoded={}/{}, reason={}, checkpoint={}",
            root,
            encoded_total,
            total_texts,
            reason,
            checkpoint_index_path,
        )

    def validate_loaded_prefix(spec: dict[str, Any], ids: list[str], texts: list[str]) -> bool:
        rows_for_spec = spec["rows"]
        count = len(ids)
        if count > len(rows_for_spec):
            return False
        if ids != [asset_id for asset_id, _text in rows_for_spec[:count]]:
            return False
        texts_key = spec.get("texts_key")
        if texts_key and texts != [text for _asset_id, text in rows_for_spec[:count]]:
            return False
        return True

    def load_checkpoint() -> None:
        if not checkpoint_index_path.exists() or not checkpoint_meta_path.exists():
            return
        meta = _read_json_if_exists(checkpoint_meta_path)
        if meta.get("build_fingerprint") != build_fingerprint:
            PROGRESS_LOGGER.info(
                "AI image embedding checkpoint ignored: library={}, reason=fingerprint_changed",
                root,
            )
            return
        try:
            data = np.load(checkpoint_index_path, allow_pickle=False)
            try:
                for spec in category_specs:
                    vectors_key = spec["vectors_key"]
                    ids_key = spec["ids_key"]
                    if vectors_key not in data.files or ids_key not in data.files:
                        continue
                    vectors = np.asarray(data[vectors_key], dtype="float32")
                    if len(vectors.shape) == 1:
                        vectors = vectors.reshape(1, -1)
                    ids = [str(item) for item in data[ids_key].tolist()]
                    count = min(len(ids), int(vectors.shape[0]))
                    ids = ids[:count]
                    vectors = vectors[:count]
                    texts: list[str] = []
                    texts_key = spec.get("texts_key")
                    if texts_key:
                        if texts_key not in data.files:
                            raise ValueError(f"checkpoint missing {texts_key}")
                        texts = [str(item) for item in data[texts_key].tolist()][:count]
                    if not validate_loaded_prefix(spec, ids, texts):
                        raise ValueError(f"checkpoint prefix mismatch: {spec['name']}")
                    encoded[spec["name"]] = {
                        "ids": ids,
                        "texts": texts,
                        "vectors": vectors,
                    }
            finally:
                data.close()
        except Exception as exc:
            PROGRESS_LOGGER.warning(
                "AI image embedding checkpoint ignored: library={}, reason={}",
                root,
                str(exc)[:180],
            )
            return

        encoded_total = sum(len(encoded[spec["name"]].get("ids") or []) for spec in category_specs)
        if encoded_total:
            PROGRESS_LOGGER.info(
                "AI image embedding checkpoint loaded: library={}, encoded={}/{}",
                root,
                encoded_total,
                total_texts,
            )

    def append_vectors(existing: Any, new_vectors: Any) -> Any:
        new_vectors = np.asarray(new_vectors, dtype="float32")
        if existing is None:
            return new_vectors
        return np.vstack([np.asarray(existing, dtype="float32"), new_vectors])

    def encode_missing(spec: dict[str, Any]) -> None:
        name = spec["name"]
        spec_rows = spec["rows"]
        state = encoded[name]
        done = len(state.get("ids") or [])
        while done < len(spec_rows):
            batch_rows = spec_rows[done:done + build_batch_size]
            batch_texts = [text for _asset_id, text in batch_rows]
            vectors = _encode_embedding_texts(batch_texts, model_name=model_name, query=False)
            state["ids"].extend(asset_id for asset_id, _text in batch_rows)
            if spec.get("texts_key"):
                state["texts"].extend(batch_texts)
            state["vectors"] = append_vectors(state.get("vectors"), vectors)
            done = len(state.get("ids") or [])
            save_checkpoint(f"{name}:{done}/{len(spec_rows)}")

    try:
        load_checkpoint()
        for spec in category_specs:
            if spec["rows"]:
                encode_missing(spec)
    except Exception as exc:
        return {
            "enabled": False,
            "reason": "embedding_build_failed",
            "model": model_name,
            "warnings": [f"AI image embedding index skipped: {str(exc)[:180]}"],
        }

    final_payload = checkpoint_payload()
    vectors = encoded["asset"].get("vectors")
    if vectors is None:
        return {
            "enabled": False,
            "reason": "empty_embedding_vectors",
            "model": model_name,
        }

    meta = {
        "schema_version": EMBEDDING_INDEX_SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "index_filename": index_filename,
        "match_asset_count": len(assets),
        "asset_count": len(rows),
        "non_embeddable_asset_count": max(0, len(assets) - len(rows)),
        "background_color_bias_asset_count": len(background_color_bias_rows),
        "vector_dim": int(vectors.shape[1]) if len(vectors.shape) == 2 else 0,
        "assets": [
            {"asset_id": asset_id, "embedding_text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]}
            for asset_id, text in rows
        ],
        "background_color_bias_assets": [
            {"asset_id": asset_id, "embedding_text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]}
            for asset_id, text in background_color_bias_rows
        ],
    }
    write_npz_atomic(index_path, final_payload)
    write_json_atomic(meta_path, meta)
    try:
        checkpoint_index_path.unlink(missing_ok=True)
        checkpoint_meta_path.unlink(missing_ok=True)
    except Exception:
        pass
    return {
        "enabled": True,
        "model": model_name,
        "index_path": _relative_output_path(index_path),
        "meta_path": _relative_output_path(meta_path),
        "match_asset_count": len(assets),
        "asset_count": len(rows),
        "non_embeddable_asset_count": max(0, len(assets) - len(rows)),
        "background_color_bias_asset_count": len(background_color_bias_rows),
        "vector_dim": meta["vector_dim"],
    }


def _normalize_reuse_library_dirs(
    library_dir: str | Path | list[str | Path] | tuple[str | Path, ...],
) -> list[Path]:
    if isinstance(library_dir, (str, Path)):
        values = [library_dir]
    elif isinstance(library_dir, (list, tuple)):
        values = list(library_dir)
    else:
        values = [library_dir]
    roots: list[Path] = []
    seen: set[str] = set()
    for value in values:
        root = Path(value).expanduser().resolve()
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)
    return roots or [Path("materials_library").resolve()]


def _load_reuse_library_for_search(
    library_root: Path,
    reuse_search_context: ReuseSearchContext | None,
) -> dict[str, Any]:
    cache_key = str(library_root)
    if reuse_search_context is not None:
        with reuse_search_context.cache_lock:
            cached = reuse_search_context.library_cache.get(cache_key)
            if isinstance(cached, dict):
                return cached

    db_path = library_root / DEFAULT_DB_FILENAME
    db = _read_existing_db(db_path)
    index, match_index_path = _read_match_index_or_build(library_root, db)
    embedding_index, embedding_status = _read_ai_image_embedding_index(library_root)
    loaded = {
        "library_root": library_root,
        "db_path": db_path,
        "db": db,
        "index": index,
        "match_index_path": match_index_path,
        "embedding_index": embedding_index,
        "embedding_status": embedding_status,
    }
    if reuse_search_context is not None:
        with reuse_search_context.cache_lock:
            cached = reuse_search_context.library_cache.get(cache_key)
            if isinstance(cached, dict):
                return cached
            reuse_search_context.library_cache[cache_key] = loaded
    return loaded


def _route_match_index_for_target_cached(
    library_root: Path,
    index: dict[str, Any],
    match_index_path: Path,
    target: dict[str, Any],
    reuse_search_context: ReuseSearchContext | None,
) -> tuple[dict[str, Any], Path, list[Any], str] | None:
    if reuse_search_context is None:
        return _route_match_index_for_target(library_root, index, match_index_path, target)
    cache_key = (str(library_root), _reuse_route_key_for_target(target))
    with reuse_search_context.cache_lock:
        if cache_key not in reuse_search_context.route_index_cache:
            reuse_search_context.route_index_cache[cache_key] = _route_match_index_for_target(
                library_root,
                index,
                match_index_path,
                target,
            )
        return reuse_search_context.route_index_cache[cache_key]


def _reuse_route_key_for_target(target: dict[str, Any]) -> str:
    if _clean_text(target.get("asset_kind")) == "background":
        return BACKGROUND_REUSE_INDEX_GROUP
    return _normalize_binary_reuse_group(target.get("strict_reuse_group"), default=_GENERAL_REUSE_GROUP)


def _select_best_library_reuse_match(matches: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not matches:
        return None

    def rank(match: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
        policy = _dict(match.get("reuse_policy"))
        decision = _clean_text(policy.get("decision"))
        decision_rank = 2.0 if decision in {"direct_reuse", "full_match"} else 1.0 if decision == "generic_support" else 0.0
        score_details = _dict(match.get("score_details"))
        return (
            decision_rank,
            float(match.get("policy_score") or score_details.get("policy_score") or 0.0),
            float(match.get("keyword_score") or 0.0),
            float(match.get("hybrid_score") or score_details.get("hybrid_score") or 0.0),
            float(match.get("embedding_score") or score_details.get("embedding_score") or 0.0),
            -float(match.get("library_search_order") or 0),
        )

    return max(matches, key=rank)


def _candidate_score_component(
    candidate: dict[str, Any],
    score_details: dict[str, Any],
    key: str,
    *aliases: str,
) -> float:
    values: list[float] = []
    for source in (candidate, score_details):
        for name in (key, *aliases):
            if name not in source:
                continue
            try:
                values.append(float(source.get(name) or 0.0))
            except (TypeError, ValueError):
                continue
    return max(values) if values else 0.0


def _candidate_policy_score(candidate: dict[str, Any], score_details: dict[str, Any] | None = None) -> float:
    details = _dict(score_details if score_details is not None else candidate.get("score_details"))
    keyword_score = _candidate_score_component(candidate, details, "keyword_score", "score")
    embedding_score = _candidate_score_component(candidate, details, "embedding_score")
    substring_score = _candidate_score_component(candidate, details, "substring_score")
    component_score = (
        HYBRID_BM25_WEIGHT * keyword_score
        + HYBRID_EMBEDDING_WEIGHT * embedding_score
        + HYBRID_SUBSTRING_WEIGHT * substring_score
    )
    if component_score <= 0.0:
        fallback = _candidate_score_component(candidate, details, "policy_score")
        if fallback > 0.0:
            component_score = fallback
    return round(max(0.0, min(1.0, float(component_score))), 4)


def _global_reuse_candidate_rank(candidate: dict[str, Any]) -> tuple[float, float, float, float, float]:
    score_details = _dict(candidate.get("score_details"))
    return (
        float(candidate.get("policy_score") or score_details.get("policy_score") or 0.0),
        float(candidate.get("hybrid_score") or score_details.get("hybrid_score") or 0.0),
        float(candidate.get("keyword_score") or score_details.get("keyword_score") or score_details.get("score") or 0.0),
        float(candidate.get("embedding_score") or score_details.get("embedding_score") or 0.0),
        -float(candidate.get("library_search_order") or 0),
    )


def _target_keyword_cache_key(target: dict[str, Any]) -> str:
    payload = {
        "asset_kind": _clean_text(target.get("asset_kind")),
        "caption": _asset_caption(target),
        "normalized_prompt": _clean_text(target.get("normalized_prompt")),
        "theme": _clean_text(target.get("theme")),
        "topic_refs": _topic_refs_for_asset(target),
        "grade_norm": _clean_text(target.get("grade_norm")),
        "grade_band": _clean_text(target.get("grade_band")),
        "subject": _clean_text(target.get("subject")),
        "page_type": _asset_page_type(target),
        "aspect_ratio": _clean_text(target.get("aspect_ratio")),
        "prompt_route": _match_prompt_route(target.get("prompt_route")),
        "background_route": _match_background_route(target.get("background_route")),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "target:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


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


# Default batch size for the prewarm. Kept aligned with the canonical keyword
# batch size so replay, live generation, and library ingest use the same
# throughput/latency trade-off unless a caller explicitly overrides it.
# Previous experiments used many short batches running in parallel: each LLM
# round-trip is wall-clock bound, so total time ≈ (longest batch latency).
PREWARM_KEYWORD_BATCH_SIZE = DEFAULT_KEYWORD_BATCH_SIZE

# Concurrency cap for the prewarm thread pool. Tuned so a typical 16-need
# plan fits in 3-4 parallel batches without saturating the upstream API.
PREWARM_KEYWORD_MAX_WORKERS = DEFAULT_REUSE_MAX_WORKERS


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


def _has_structural_evidence(
    target: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Return (has_evidence, evidence_kinds) for the Q1/P7 consistency gate.

    The active structural evidence is normalized overlap between target and
    candidate retrieval text. Older constraint/core-keyword hooks were removed
    with the simplified reuse metadata schema.
    """

    def _norm_terms(values: Any) -> set[str]:
        return {
            _clean_text(item).casefold()
            for item in (values or [])
            if isinstance(item, (str, int, float)) and _clean_text(item)
        }

    target_terms = _norm_terms(_bm25_tokens_from_values([_page_retrieval_text(target)]))
    candidate_terms = _norm_terms(_bm25_tokens_from_values([_page_retrieval_text(candidate)]))
    if target_terms and candidate_terms and (target_terms & candidate_terms):
        return True, ["retrieval_text_overlap"]
    return False, []


def _embedding_keyword_gap_reject(
    target: dict[str, Any],
    candidate_asset: dict[str, Any],
    score_details: dict[str, Any],
    *,
    gap_threshold: float = EMBEDDING_KEYWORD_GAP_REJECT_THRESHOLD,
) -> dict[str, Any] | None:
    """Return a synthetic policy result when the consistency gate trips.

    Trip condition: the embedding score sits ``gap_threshold`` or more
    above the keyword score *and* the candidate carries no structural
    evidence. Returning ``None`` means the gate does not apply — the
    caller should continue with its normal policy evaluation.

    Backgrounds are exempted: their reuse logic uses background-specific
    scoring and the keyword score is not directly comparable.
    """

    if _clean_text(target.get("asset_kind")) == "background":
        return None
    embedding = _maybe_float_score(score_details.get("embedding_score"))
    keyword = _maybe_float_score(score_details.get("keyword_score"))
    if embedding is None or keyword is None:
        return None
    if (embedding - keyword) < gap_threshold:
        return None
    has_evidence, evidence_kinds = _has_structural_evidence(target, candidate_asset)
    if has_evidence:
        return None
    if embedding >= EMBED_RESCUE_FLOOR:
        return None
    return {
        "decision": "reject",
        "reason": "embedding_keyword_gap_no_structural_evidence",
        "confidence": 0.85,
        "llm_skip_safe": True,
        "consistency_gate": {
            "embedding_score": round(embedding, 4),
            "keyword_score": round(keyword, 4),
            "gap": round(embedding - keyword, 4),
            "gap_threshold": gap_threshold,
            "evidence_kinds_checked": ["retrieval_text_overlap"],
            "evidence_kinds_found": evidence_kinds,
        },
    }


def _maybe_float_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _embedding_pre_llm_floor_reject(
    target: dict[str, Any],
    score_details: dict[str, Any],
    policy_result: dict[str, Any],
    embedding_status: dict[str, Any] | None,
    *,
    floor: float = EMBEDDING_PRE_LLM_FLOOR,
) -> dict[str, Any] | None:
    """Reject llm_review candidates whose embedding sits below the floor.

    Calibrated on materials_library_ppt: all acc/best LLM-required
    candidates carry embedding >= 0.572, while a large share of LLM-bound
    rejects score below 0.55 (often via BM25-only paths where embedding
    is zero). Cutting them here avoids burning LLM budget on candidates
    that ground-truth analysis shows have no chance of acceptance.

    Skipped when embedding indexing is unavailable (every candidate's
    embedding_score would be 0 and the floor cannot distinguish "real
    low signal" from "no signal at all"). Backgrounds are also exempted:
    their reuse uses background-specific scoring whose distribution is
    not directly comparable.
    """

    if _clean_text(target.get("asset_kind")) == "background":
        return None
    if not _dict(embedding_status).get("enabled"):
        return None
    if _clean_text(policy_result.get("decision")) != "llm_review":
        return None
    embedding = _maybe_float_score(score_details.get("embedding_score"))
    if embedding is None or embedding >= floor:
        return None
    return {
        "decision": "reject",
        "reason": "embedding_below_pre_llm_floor",
        "confidence": 0.85,
        "llm_skip_safe": True,
        "embedding_pre_llm_floor_gate": {
            "embedding_score": round(embedding, 4),
            "floor": floor,
        },
    }


def _review_reuse_candidate_with_vlm(
    vlm_client: Any | None,
    *,
    target: dict[str, Any],
    candidate_asset: dict[str, Any],
    candidate_image_path: str | Path | None,
    accept_threshold: float,
    llm_review_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """R5: VLM-side near-miss verification on the candidate image.

    Returns a dict with the canonical review-result shape (``score``,
    ``threshold``, ``decision``, ``brief_reason`` …). The caller decides
    whether to overwrite the LLM result based on the returned decision.

    Failure modes (missing client, missing image, VLM error) all degrade
    gracefully: the function returns a non-accept stub so the caller
    behaves identically to the no-fallback path.
    """

    stub = {
        "score": _clamp_score((llm_review_result or {}).get("score") or 0.0),
        "threshold": max(0.0, min(1.0, float(accept_threshold))),
        "decision": "reject",
        "brief_reason": "vlm_unavailable",
        "evidence": [],
        "risk_factors": [],
    }
    if vlm_client is None:
        stub["brief_reason"] = "vlm_client_missing"
        return stub
    if not candidate_image_path:
        stub["brief_reason"] = "vlm_image_path_missing"
        return stub
    image_path = Path(str(candidate_image_path))
    if not image_path.exists():
        stub["brief_reason"] = "vlm_image_path_not_found"
        return stub

    # Build the multimodal message. We import lazily to avoid pulling the
    # VLM module's heavy imports at module load.
    try:
        from edupptx.materials.vlm_asset_enricher import _image_data_url
    except Exception as exc:  # pragma: no cover — defensive
        stub["brief_reason"] = f"vlm_helper_import_failed: {str(exc)[:120]}"
        return stub
    try:
        data_url = _image_data_url(image_path)
    except Exception as exc:  # pragma: no cover — defensive
        stub["brief_reason"] = f"vlm_image_encode_failed: {str(exc)[:120]}"
        return stub

    target_prompt = _clean_text(_asset_caption(target))
    target_summary = {
        "caption": target_prompt,
        "context_summary": _clean_text(target.get("context_summary")),
    }

    system_text = (
        "You are a teaching-image reviewer. The candidate image was almost "
        "accepted by a text-only reviewer (score sits within "
        f"{R5_NEAR_MISS_EPSILON:.2f} of the accept threshold). Inspect the "
        "image and decide whether it can be reused for the target prompt. "
        "Answer with strict JSON only — do not add commentary."
    )
    instruction_text = (
        "Compare the image against the target requirement. Reply with "
        "{\"decision\": \"accept\"|\"reject\", \"score\": <float in 0..1>, "
        "\"brief_reason\": <string>, \"matched\": [<string>], "
        "\"missing\": [<string>]}. Set decision=accept only when the "
        "image clearly satisfies the target's content; otherwise reject."
    )
    user_content = [
        {"type": "text", "text": instruction_text},
        {"type": "text", "text": json.dumps({"target": target_summary, "candidate_text": _reuse_debug_asset_payload(candidate_asset)}, ensure_ascii=False)},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    messages = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_content},
    ]

    PROGRESS_LOGGER.info(
        "AI image reuse VLM near-miss verify start: candidate_asset_id={}, llm_score={}, threshold={}",
        _clean_text(candidate_asset.get("asset_id")),
        round(stub["score"], 4),
        round(accept_threshold, 4),
    )
    chat_json = getattr(vlm_client, "chat_json", None)
    response: Any
    try:
        if callable(chat_json):
            try:
                response = chat_json(messages=messages, temperature=0.0, max_tokens=512, max_retries=1)
            except TypeError:
                response = chat_json(messages, temperature=0.0, max_tokens=512)
        else:
            chat = getattr(vlm_client, "chat", None)
            if not callable(chat):
                stub["brief_reason"] = "vlm_client_missing_chat"
                return stub
            response = _load_json_response(chat(messages=messages, temperature=0.0, max_tokens=512))
    except Exception as exc:
        PROGRESS_LOGGER.warning(
            "AI image reuse VLM near-miss verify failed: candidate_asset_id={}, error={}",
            _clean_text(candidate_asset.get("asset_id")),
            str(exc)[:200],
        )
        stub["brief_reason"] = f"vlm_call_failed: {str(exc)[:160]}"
        return stub

    if not isinstance(response, dict):
        stub["brief_reason"] = "vlm_invalid_response"
        return stub

    raw_decision = _clean_text(response.get("decision")).casefold()
    score = _clamp_score(response.get("score"))
    decision = "accept" if raw_decision == "accept" else "reject"
    PROGRESS_LOGGER.info(
        "AI image reuse VLM near-miss verify done: candidate_asset_id={}, decision={}, score={}",
        _clean_text(candidate_asset.get("asset_id")),
        decision,
        round(score, 4),
    )
    return {
        "score": score,
        "threshold": max(0.0, min(1.0, float(accept_threshold))),
        "decision": decision,
        "brief_reason": _clean_text(response.get("brief_reason")) or f"vlm_{decision}",
        "evidence": _as_string_list(response.get("matched")),
        "risk_factors": _as_string_list(response.get("missing")),
    }


def _r5_session_vlm_budget(near_miss_vlm_state: dict[str, Any] | None) -> tuple[bool, int]:
    """Return ``(has_budget, used_count)`` for the R5 VLM verification.

    Uses a dedicated shared dict — *not* ``reuse_session_state`` — because
    the policy phase often runs in parallel with ``reuse_session_state``
    set to ``None`` (to suppress occupancy races during scoring), but the
    near-miss VLM budget must still be coordinated across the parallel
    workers. A ``None`` dict here truly means "no coordination available",
    which conservatively denies the budget.
    """

    if near_miss_vlm_state is None:
        return False, 0
    used = int(near_miss_vlm_state.get(R5_SESSION_VLM_COUNT_KEY) or 0)
    return used < R5_MAX_VLM_CALLS_PER_SESSION, used


def _r5_consume_session_vlm_budget(near_miss_vlm_state: dict[str, Any] | None) -> None:
    if near_miss_vlm_state is None:
        return
    used = int(near_miss_vlm_state.get(R5_SESSION_VLM_COUNT_KEY) or 0)
    near_miss_vlm_state[R5_SESSION_VLM_COUNT_KEY] = used + 1


def _review_score_value(score_details: dict[str, Any], key: str) -> float:
    try:
        return float(score_details.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _review_keyword_score(score_details: dict[str, Any]) -> float:
    return max(
        _review_score_value(score_details, "keyword_score"),
        _review_score_value(score_details, "score"),
    )


def _llm_review_priority(record: dict[str, Any]) -> tuple[float, float, float, float, float]:
    score_details = _dict(record.get("score_details"))
    policy_result = _dict(record.get("policy_result"))
    return (
        _review_score_value(score_details, "embedding_score"),
        float(policy_result.get("policy_score") or score_details.get("policy_score") or 0.0),
        _review_keyword_score(score_details),
        _review_score_value(score_details, "substring_score"),
        _review_score_value(score_details, "hybrid_score"),
    )


def _apply_reuse_policy_to_ranked_candidates(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    threshold: float,
    embedding_status: dict[str, Any],
    df_ratio_lookup: dict[str, float],
    keyword_client: Any | None,
    reuse_session_state: dict[str, Any] | None,
    llm_review_enabled: bool,
    llm_review_budget: int = MAX_LLM_REVIEWS_PER_QUERY,
    vlm_client: Any | None = None,
    near_miss_vlm_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate policy and dispatch LLM reviews for a ranked candidate list.

    Current LLM-review step uses strict one-candidate prompts. We do two passes:

    1. Pre-review pass — compute each candidate's pre-LLM ``policy_result``
       (including the Q1/P7 consistency gate), and classify into one of
       ``direct``, ``llm_review`` or ``skip``.
    2. LLM review calls — collect up to ``llm_review_budget`` ``llm_review``
       candidates and submit them as a single request via
       one strict single-candidate LLM call per reviewed candidate; multiple
       candidate reviews may run concurrently.

    The output schema (accepted/rejected lists, llm_reviews_used) is
    unchanged so callers do not need to be updated.
    """

    rejected_by_policy: list[dict[str, Any]] = []
    rejected_by_occupancy: list[dict[str, Any]] = []

    # ----- Pass 1: pre-review classification ------------------------------
    pre_records: list[dict[str, Any]] = []
    for candidate in candidates:
        score_details = dict(_dict(candidate.get("score_details")))
        for key in (
            "keyword_score",
            "embedding_score",
            "substring_score",
            "hybrid_score",
            "rrf_score",
            "policy_score",
            "background_reuse_score",
            "transform_policy",
        ):
            if key in candidate and key not in score_details:
                score_details[key] = candidate.get(key)
        policy_score = _candidate_policy_score(candidate, score_details)
        candidate["policy_score"] = policy_score
        score_details["policy_score"] = policy_score
        candidate_asset = _dict(candidate.get("asset"))
        candidate_embedding_status = _dict(candidate.get("_reuse_embedding_status") or embedding_status)
        candidate_df_ratio_lookup = candidate.get("_reuse_df_ratio_lookup")
        if not isinstance(candidate_df_ratio_lookup, dict):
            candidate_df_ratio_lookup = df_ratio_lookup
        if candidate_df_ratio_lookup:
            score_details["df_ratio_lookup"] = candidate_df_ratio_lookup
        policy_result = evaluate_reuse_filter(
            target,
            candidate_asset,
            score_details,
            threshold=threshold,
        )
        # Q1/P7 consistency gate (runs before LLM dispatch).
        if _clean_text(policy_result.get("decision")) != "reject":
            consistency_reject = _embedding_keyword_gap_reject(
                target,
                candidate_asset,
                score_details,
            )
            if consistency_reject is not None:
                policy_result = {**policy_result, **consistency_reject}
        # Pre-LLM embedding floor: drop llm_review candidates that no
        # acc/best ground-truth example reaches. See
        # _embedding_pre_llm_floor_reject for calibration notes.
        floor_reject = _embedding_pre_llm_floor_reject(
            target,
            score_details,
            policy_result,
            candidate_embedding_status,
        )
        if floor_reject is not None:
            policy_result = {**policy_result, **floor_reject}
        pre_records.append({
            "candidate": candidate,
            "candidate_asset": candidate_asset,
            "score_details": score_details,
            "policy_result": policy_result,
        })

    # ----- Pass 1b: three-tier decision authority (spec §4 / decide_reuse) -
    # The wired evaluate_reuse_filter only yields full_match/reject and never
    # routes the borderline band to llm_review, so the LLM-review pass below
    # was unreachable and the per-target threshold was ignored. decide_reuse
    # now owns the score tier for non-background page images: it keys on the
    # absolute keyword_score with the per-target accept threshold as the
    # discard line (production hybrid_score is RRF-normalized and unusable for
    # the high/low cut). Pass-1 hard rejects (skip / kind / subject / aspect /
    # consistency gap / pre-LLM floor) are preserved untouched.
    if not _is_background_asset(target):
        tier_items = [
            {
                "_index": index,
                "asset_id": _clean_text(_dict(record["candidate_asset"]).get("asset_id")),
                "policy_score": float(record["score_details"].get("policy_score") or 0.0),
                "size_distance": _reuse_size_distance(target, _dict(record["candidate_asset"])),
            }
            for index, record in enumerate(pre_records)
            if _clean_text(record["policy_result"].get("decision")) != "reject"
        ]
        if tier_items:
            tier_items.sort(key=lambda item: item["policy_score"], reverse=True)
            tier = decide_reuse(
                tier_items,
                score_key="policy_score",
                t_direct=T_DIRECT,
                t_reject=T_REJECT,
                t_gap=T_GAP,
            )
            tier_decision = _clean_text(tier.get("decision"))
            if tier_decision == "direct_reuse":
                selected_index = tier_items[0]["_index"]
                for item in tier_items:
                    record = pre_records[item["_index"]]
                    if item["_index"] == selected_index:
                        record["policy_result"] = {
                            **record["policy_result"],
                            "decision": "direct_reuse",
                            "reason": "policy_score_direct_reuse",
                            "policy_score": item["policy_score"],
                        }
                    else:
                        record["policy_result"] = {
                            **record["policy_result"],
                            "decision": "reject",
                            "reason": "policy_not_selected",
                            "policy_score": item["policy_score"],
                        }
            elif tier_decision == "llm_review":
                cluster_indices = {item["_index"] for item in (tier.get("cluster") or [])}
                for item in tier_items:
                    record = pre_records[item["_index"]]
                    if item["_index"] in cluster_indices:
                        record["policy_result"] = {
                            **record["policy_result"],
                            "decision": "llm_review",
                            "reason": "policy_score_llm_review",
                            "policy_score": item["policy_score"],
                        }
                    else:
                        record["policy_result"] = {
                            **record["policy_result"],
                            "decision": "reject",
                            "reason": "policy_not_selected",
                            "policy_score": item["policy_score"],
                        }
            else:
                for item in tier_items:
                    record = pre_records[item["_index"]]
                    if _embedding_rescue_decision(
                        embedding_score=_maybe_float_score(
                            _dict(record["score_details"]).get("embedding_score")
                        ),
                        transform_rejected=_transform_rejects_candidate(record["candidate"]),
                    ):
                        record["policy_result"] = {
                            **record["policy_result"],
                            "decision": "llm_review",
                            "reason": "embedding_rescue_review",
                            "policy_score": item["policy_score"],
                        }
                    else:
                        record["policy_result"] = {
                            **record["policy_result"],
                            "decision": "reject",
                            "reason": "policy_score_below_reject_threshold",
                            "policy_score": item["policy_score"],
                        }

    # Identify the slice that needs an LLM review, capped by budget.
    review_targets: list[int] = []
    if llm_review_enabled:
        review_candidates: list[int] = []
        for index, record in enumerate(pre_records):
            decision = _clean_text(record["policy_result"].get("decision"))
            if decision != "llm_review":
                continue
            if record["policy_result"].get("llm_skip_safe"):
                continue
            review_candidates.append(index)
        review_candidates.sort(key=lambda index: _llm_review_priority(pre_records[index]), reverse=True)
        review_targets = review_candidates[: max(0, int(llm_review_budget or 0))]

    # ----- Pass 2: one LLM call per candidate, optionally parallel --------
    review_results_by_index: dict[int, dict[str, Any]] = {}
    if review_targets:
        def review_one(index: int) -> tuple[int, dict[str, Any]]:
            record = pre_records[index]
            review = _review_reuse_candidate_with_llm(
                keyword_client,
                target=target,
                candidate=record["candidate_asset"],
                policy_result=record["policy_result"],
                score_details=record["score_details"],
            )
            return index, review

        if len(review_targets) == 1:
            index, review = review_one(review_targets[0])
            review_results_by_index[index] = review
        else:
            max_workers = _review_worker_count(len(review_targets))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_index = {executor.submit(review_one, index): index for index in review_targets}
                for future in as_completed(future_to_index):
                    index, review = future.result()
                    review_results_by_index[index] = review

    llm_reviews_used = len(review_targets)

    # ----- Pass 3: assemble per-candidate outcomes -----------------------
    accepted_candidates: list[dict[str, Any]] = []
    for index, record in enumerate(pre_records):
        candidate = record["candidate"]
        candidate_asset = record["candidate_asset"]
        policy_result = record["policy_result"]
        review_decision = _clean_text(policy_result.get("decision"))
        review_reason = _clean_text(policy_result.get("reason"))
        deterministic_reject = bool(policy_result.get("llm_skip_safe"))
        skip_for_existing_accept = bool(accepted_candidates)

        if review_decision == "llm_review" and index in review_results_by_index:
            review_result = review_results_by_index[index]
            policy_result = dict(policy_result)
            policy_result["llm_review_required"] = True
            policy_result["llm_review_performed"] = True
            policy_result["llm_review"] = review_result
            if _reuse_review_accepts(review_result) and not skip_for_existing_accept:
                policy_result["decision"] = "direct_reuse"
                policy_result["reason"] = "llm_accept"
                policy_result["confidence"] = max(
                    float(policy_result.get("confidence") or 0.0),
                    _clamp_score(review_result.get("score")),
                )
            elif skip_for_existing_accept and _reuse_review_accepts(review_result):
                # Earlier candidate already accepted — keep the LLM result
                # in the record for debug but downgrade to reject so we
                # never materialise more than one candidate per query.
                policy_result["decision"] = "reject"
                policy_result["reason"] = (
                    "strict_llm_review_skipped_after_accept"
                    if review_reason.startswith("strict_")
                    else "llm_review_skipped_after_accept"
                )
            else:
                policy_result["decision"] = "reject"
                policy_result["reason"] = (
                    "strict_llm_score_review_rejected"
                    if review_reason.startswith("strict_")
                    else "llm_score_review_rejected"
                )
        elif review_decision == "llm_review":
            # Either budget exhausted, llm_skip_safe deterministic reject, or
            # llm_review disabled. Synthesise the appropriate skip record.
            skip_threshold = _reuse_review_accept_score_threshold(
                target,
                candidate_asset,
                policy_result=policy_result,
            )
            if not llm_review_enabled:
                skip_brief = "llm_disabled"
                skip_decision_reason = "llm_disabled"
            elif deterministic_reject:
                skip_brief = "deterministic_reject_skip"
                skip_decision_reason = (
                    "strict_deterministic_llm_skip"
                    if review_reason.startswith("strict_")
                    else "deterministic_llm_skip"
                )
            else:
                skip_brief = "per_query_budget_exhausted"
                skip_decision_reason = "llm_budget_exhausted"
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
        else:
            policy_result = dict(policy_result)
            policy_result["llm_review_required"] = False
            policy_result["llm_review_performed"] = False

        candidate["reuse_policy"] = policy_result
        decision = _clean_text(policy_result.get("decision"))
        if decision in {"direct_reuse", "full_match", "generic_support"}:
            occupancy = _strict_reuse_occupancy_status(candidate, reuse_session_state)
            candidate["strict_reuse_occupancy"] = occupancy
            if _clean_text(occupancy.get("decision")) == "skip_strict_asset_reuse_limit":
                rejected_by_occupancy.append(candidate)
                continue
            accepted_candidates.append(candidate)
        else:
            rejected_by_policy.append(candidate)

    # ----- R5: near-miss VLM verification --------------------------------
    # Triggers only when:
    #   * no candidate has been accepted yet (no point salvaging if we
    #     already have a match);
    #   * a candidate's LLM score sat within R5_NEAR_MISS_EPSILON of its
    #     accept threshold (the metadata reviewer was almost convinced);
    #   * a VLM client is configured AND the per-session budget allows.
    # Successful VLM accepts promote the candidate to ``full_match`` after
    # a fresh occupancy check, preserving the per-session strict-reuse
    # invariants.
    vlm_used_this_query = 0
    if (
        not accepted_candidates
        and vlm_client is not None
        and pre_records
    ):
        best_near_miss_index: int | None = None
        best_near_miss_gap: float = R5_NEAR_MISS_EPSILON + 1.0
        for index, record in enumerate(pre_records):
            policy = record["candidate"].get("reuse_policy") or {}
            llm_review = policy.get("llm_review") or {}
            if not policy.get("llm_review_performed"):
                continue
            llm_score = _maybe_float_score(llm_review.get("score"))
            llm_threshold = _maybe_float_score(llm_review.get("threshold"))
            if llm_score is None or llm_threshold is None:
                continue
            gap = llm_threshold - llm_score
            # Near miss: the candidate is BELOW threshold but by no more
            # than the epsilon. Negative gaps mean already-accepted (we
            # would have entered the accepted branch above), so we skip.
            if 0.0 <= gap <= R5_NEAR_MISS_EPSILON and gap < best_near_miss_gap:
                best_near_miss_gap = gap
                best_near_miss_index = index
        if best_near_miss_index is not None:
            has_budget, used = _r5_session_vlm_budget(near_miss_vlm_state)
            if has_budget:
                record = pre_records[best_near_miss_index]
                candidate = record["candidate"]
                policy_result = candidate.get("reuse_policy") or {}
                accept_threshold = float(
                    (policy_result.get("llm_review") or {}).get("threshold")
                    or _reuse_review_accept_score_threshold(target, record["candidate_asset"], policy_result=policy_result)
                )
                vlm_result = _review_reuse_candidate_with_vlm(
                    vlm_client,
                    target=target,
                    candidate_asset=record["candidate_asset"],
                    candidate_image_path=candidate.get("candidate_image_path"),
                    accept_threshold=accept_threshold,
                    llm_review_result=policy_result.get("llm_review"),
                )
                _r5_consume_session_vlm_budget(near_miss_vlm_state)
                vlm_used_this_query += 1
                policy_result = dict(policy_result)
                policy_result["vlm_near_miss_review"] = vlm_result
                if vlm_result.get("decision") == "accept":
                    policy_result["decision"] = "direct_reuse"
                    policy_result["reason"] = "vlm_near_miss_accept"
                    candidate["reuse_policy"] = policy_result
                    occupancy = _strict_reuse_occupancy_status(candidate, reuse_session_state)
                    candidate["strict_reuse_occupancy"] = occupancy
                    if _clean_text(occupancy.get("decision")) == "skip_strict_asset_reuse_limit":
                        rejected_by_occupancy.append(candidate)
                        if candidate in rejected_by_policy:
                            rejected_by_policy.remove(candidate)
                    else:
                        # Promote out of rejected_by_policy (if it was there) and
                        # into accepted_candidates.
                        if candidate in rejected_by_policy:
                            rejected_by_policy.remove(candidate)
                        accepted_candidates.append(candidate)
                else:
                    candidate["reuse_policy"] = policy_result

    return {
        "accepted_candidates": accepted_candidates,
        "rejected_by_policy": rejected_by_policy,
        "rejected_by_occupancy": rejected_by_occupancy,
        "llm_reviews_used": llm_reviews_used,
        "llm_review_budget": llm_review_budget,
        "vlm_near_miss_reviews_used": vlm_used_this_query,
    }


def _reuse_accept_reason(best: dict[str, Any]) -> str:
    policy_decision = _clean_text(_dict(best.get("reuse_policy")).get("decision"))
    policy_reason = _clean_text(_dict(best.get("reuse_policy")).get("reason"))
    if _clean_text(_dict(best.get("asset")).get("asset_kind")) == "background":
        return "reused_by_background_reuse_score"
    if policy_decision == "direct_reuse" and policy_reason == "llm_accept":
        return "reused_by_llm_review"
    if policy_decision == "direct_reuse":
        return "reused_by_policy_score"
    if policy_decision == "generic_support":
        return "reused_by_policy_generic_support"
    return "reused_by_policy_score"


def _reuse_collection_empty_reason(collection: dict[str, Any]) -> str:
    return _clean_text(collection.get("empty_reason")) or "retrieval_no_candidate"


def _finalize_reuse_candidate_collection(
    collection: dict[str, Any] | None,
    *,
    debug_path: str | Path | None,
    keyword_client: Any | None,
    reuse_session_state: dict[str, Any] | None,
    llm_review_enabled: bool,
    reuse_debug_mode: str,
    vlm_client: Any | None = None,
    near_miss_vlm_state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(collection, dict) or not collection.get("_reuse_candidate_collection"):
        return None

    reuse_debug_mode = _normalize_reuse_debug_mode(reuse_debug_mode)
    child_collections = [
        item for item in collection.get("collections") or []
        if isinstance(item, dict) and item.get("_reuse_candidate_collection")
    ]
    if child_collections:
        target = _dict(collection.get("target") or child_collections[0].get("target"))
        threshold = float(collection.get("threshold") or child_collections[0].get("threshold") or 0.0)
        combined_candidates = list(collection.get("candidates") or [])
        if combined_candidates:
            policy_outcome = _apply_reuse_policy_to_ranked_candidates(
                target,
                combined_candidates,
                threshold=threshold,
                embedding_status={},
                df_ratio_lookup={},
                keyword_client=keyword_client,
                reuse_session_state=reuse_session_state,
                llm_review_enabled=llm_review_enabled,
                vlm_client=vlm_client,
                near_miss_vlm_state=near_miss_vlm_state,
            )
            accepted_candidates = policy_outcome["accepted_candidates"]
            rejected_by_policy = policy_outcome["rejected_by_policy"]
            rejected_by_occupancy = policy_outcome["rejected_by_occupancy"]
        else:
            policy_outcome = {
                "accepted_candidates": [],
                "rejected_by_policy": [],
                "rejected_by_occupancy": [],
                "llm_reviews_used": 0,
                "llm_review_budget": MAX_LLM_REVIEWS_PER_QUERY,
            }
            accepted_candidates = []
            rejected_by_policy = []
            rejected_by_occupancy = []

        best = accepted_candidates[0] if accepted_candidates else None
        reason = (
            _reuse_accept_reason(best)
            if best
            else (
                "policy_reject"
                if combined_candidates
                else "retrieval_no_candidate"
            )
        )
        if best:
            best["multi_library_reuse_reason"] = reason
            best["llm_reviews_invoked"] = policy_outcome["llm_reviews_used"]
            best["llm_reviews_budget"] = policy_outcome["llm_review_budget"]

        for child in child_collections:
            collection_candidates = child.get("candidates") or []
            collection_candidate_ids = {id(candidate) for candidate in collection_candidates}
            record = _dict(child.get("debug_record"))
            collection_threshold = float(child.get("threshold") or threshold)
            record["llm_reviews_invoked"] = policy_outcome["llm_reviews_used"]
            record["llm_reviews_budget"] = policy_outcome["llm_review_budget"]
            record["policy_candidates"] = [
                _reuse_debug_candidate_payload(candidate, threshold=collection_threshold)
                for candidate in collection_candidates
            ]
            record["policy_rejected_candidates"] = [
                _reuse_debug_candidate_payload(candidate, threshold=collection_threshold)
                for candidate in rejected_by_policy
                if id(candidate) in collection_candidate_ids
            ]
            record["occupancy_rejected_candidates"] = [
                _reuse_debug_candidate_payload(candidate, threshold=collection_threshold)
                for candidate in rejected_by_occupancy
                if id(candidate) in collection_candidate_ids
            ]
            local_match = best if best is not None and id(best) in collection_candidate_ids else None
            local_reason = reason if local_match else ("reused_from_other_library" if best else reason)
            if not collection_candidates and not best:
                local_reason = _reuse_collection_empty_reason(child)
            record["decision"] = {
                "reused": local_match is not None,
                "reason": local_reason,
                "asset_id": _dict(local_match.get("asset")).get("asset_id") if local_match else "",
                "keyword_score": local_match.get("keyword_score") if local_match else None,
                "threshold_used": collection_threshold,
                "reuse_policy": local_match.get("reuse_policy") if local_match else None,
                "reuse_audit": local_match.get("reuse_audit") if local_match else None,
                "llm_reuse_review_performed": _match_llm_reuse_review_performed(local_match) if local_match else False,
                "strict_reuse_occupancy": local_match.get("strict_reuse_occupancy") if local_match else None,
            }
            _append_reuse_debug_record(
                debug_path,
                _reuse_debug_record_for_mode(record, mode=reuse_debug_mode, match=local_match),
            )
        return best

    target = _dict(collection.get("target"))
    candidates = list(collection.get("candidates") or [])
    record = _dict(collection.get("debug_record"))
    threshold = float(collection.get("threshold") or record.get("threshold_used") or 0.0)
    if not candidates:
        reason = _reuse_collection_empty_reason(collection)
        record["decision"] = {
            "reused": False,
            "reason": reason,
            "asset_id": "",
            "keyword_score": None,
            "threshold_used": threshold,
            "reuse_policy": None,
            "reuse_audit": None,
            "llm_reuse_review_performed": False,
            "strict_reuse_occupancy": None,
        }
        _append_reuse_debug_record(
            debug_path,
            _reuse_debug_record_for_mode(record, mode=reuse_debug_mode, match=None),
        )
        return None

    policy_outcome = _apply_reuse_policy_to_ranked_candidates(
        target,
        candidates,
        threshold=threshold,
        embedding_status=_dict(collection.get("embedding_status")),
        df_ratio_lookup={},
        keyword_client=keyword_client,
        reuse_session_state=reuse_session_state,
        llm_review_enabled=llm_review_enabled,
        vlm_client=vlm_client,
        near_miss_vlm_state=near_miss_vlm_state,
    )
    accepted_candidates = policy_outcome["accepted_candidates"]
    rejected_by_policy = policy_outcome["rejected_by_policy"]
    rejected_by_occupancy = policy_outcome["rejected_by_occupancy"]
    record["llm_reviews_invoked"] = policy_outcome["llm_reviews_used"]
    record["llm_reviews_budget"] = policy_outcome["llm_review_budget"]
    record["policy_candidates"] = [
        _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in candidates
    ]
    if not accepted_candidates:
        record["policy_rejected_candidates"] = [
            _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in rejected_by_policy
        ]
        record["occupancy_rejected_candidates"] = [
            _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in rejected_by_occupancy
        ]
        reason = "policy_reject"
        record["decision"] = {
            "reused": False,
            "reason": reason,
            "asset_id": "",
            "keyword_score": None,
            "threshold_used": threshold,
            "reuse_policy": None,
            "reuse_audit": None,
            "llm_reuse_review_performed": False,
            "strict_reuse_occupancy": None,
        }
        _append_reuse_debug_record(
            debug_path,
            _reuse_debug_record_for_mode(record, mode=reuse_debug_mode, match=None),
        )
        return None

    best = accepted_candidates[0]
    reason = _reuse_accept_reason(best)
    best["llm_reviews_invoked"] = policy_outcome["llm_reviews_used"]
    best["llm_reviews_budget"] = policy_outcome["llm_review_budget"]
    record["decision"] = {
        "reused": True,
        "reason": reason,
        "asset_id": _dict(best.get("asset")).get("asset_id"),
        "keyword_score": best.get("keyword_score"),
        "threshold_used": threshold,
        "reuse_policy": best.get("reuse_policy"),
        "reuse_audit": best.get("reuse_audit"),
        "llm_reuse_review_performed": _match_llm_reuse_review_performed(best),
        "strict_reuse_occupancy": best.get("strict_reuse_occupancy"),
    }
    _append_reuse_debug_record(
        debug_path,
        _reuse_debug_record_for_mode(record, mode=reuse_debug_mode, match=best),
    )
    return best


def find_reusable_ai_image_asset(
    *,
    library_dir: str | Path | list[str | Path] | tuple[str | Path, ...],
    asset_kind: str,
    prompt: str,
    prompt_route: dict[str, Any] | None = None,
    background_route: dict[str, Any] | None = None,
    theme: str = "",
    grade: str = "",
    subject: str = "",
    grade_band: str = "",
    page_title: str = "",
    page_type: str = "",
    role: str = "",
    aspect_ratio: str = "",
    caption: str = "",
    keyword_client: Any | None = None,
    candidate_limit: int = DEFAULT_REUSE_CANDIDATE_LIMIT,
    min_keyword_score: float | None = DEFAULT_MIN_REUSE_KEYWORD_SCORE,
    debug_path: str | Path | None = None,
    debug_context: dict[str, Any] | None = None,
    reuse_session_state: dict[str, Any] | None = None,
    llm_review_enabled: bool = True,
    reuse_debug_mode: str = "",
    reuse_search_context: ReuseSearchContext | None = None,
    _target_keyword_cache: dict[str, Any] | None = None,
    _collect_candidates_only: bool = False,
    _library_search_order: int = 0,
) -> dict[str, Any] | None:
    """Find a reusable AI image asset from the central library.

    BM25 remains the precision signal, while optional Qwen embedding and
    substring retrieval provide gray-zone recall through RRF fusion. When a
    strict reuse policy needs semantic confirmation, the same LLM client can
    perform a bounded second-stage review.
    """

    library_roots = _normalize_reuse_library_dirs(library_dir)
    if reuse_search_context is None:
        reuse_search_context = ReuseSearchContext()
    target_keyword_cache = (
        _target_keyword_cache
        if _target_keyword_cache is not None
        else reuse_search_context.target_keyword_cache
    )
    if len(library_roots) > 1:
        collections: list[dict[str, Any]] = []
        for order, root in enumerate(library_roots):
            context = dict(debug_context or {})
            context["reuse_library_dir"] = str(root)
            collection = find_reusable_ai_image_asset(
                library_dir=root,
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
                caption=caption,
                grade_band=grade_band,
                keyword_client=keyword_client,
                candidate_limit=candidate_limit,
                min_keyword_score=min_keyword_score,
                debug_path=debug_path,
                debug_context=context,
                reuse_session_state=reuse_session_state,
                llm_review_enabled=llm_review_enabled,
                reuse_debug_mode=reuse_debug_mode,
                reuse_search_context=reuse_search_context,
                _target_keyword_cache=target_keyword_cache,
                _collect_candidates_only=True,
                _library_search_order=order,
            )
            if isinstance(collection, dict) and collection.get("_reuse_candidate_collection"):
                collections.append(collection)
        combined_candidates: list[dict[str, Any]] = []
        target: dict[str, Any] | None = None
        threshold = _reuse_threshold_for_target(
            _build_reuse_target_asset(
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
                caption=caption,
                grade_band=grade_band,
            ),
            min_keyword_score,
        )
        for collection in collections:
            if target is None:
                target = _dict(collection.get("target"))
                threshold = float(collection.get("threshold") or threshold)
            combined_candidates.extend(collection.get("candidates") or [])
        combined_candidates.sort(key=_global_reuse_candidate_rank, reverse=True)
        combined_collection = {
            "_reuse_candidate_collection": True,
            "target": target or {},
            "threshold": threshold,
            "candidates": combined_candidates,
            "collections": collections,
        }
        if _collect_candidates_only:
            return combined_collection
        return _finalize_reuse_candidate_collection(
            combined_collection,
            debug_path=debug_path,
            keyword_client=keyword_client,
            reuse_session_state=reuse_session_state,
            llm_review_enabled=llm_review_enabled,
            reuse_debug_mode=reuse_debug_mode,
        )

    library_root = library_roots[0]
    loaded_library = _load_reuse_library_for_search(library_root, reuse_search_context)
    db_path = loaded_library["db_path"]
    index = loaded_library["index"]
    match_index_path = loaded_library["match_index_path"]
    assets = index.get("assets")
    embedding_index = loaded_library["embedding_index"]
    embedding_status = loaded_library["embedding_status"]
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
        caption=caption,
        grade_band=grade_band,
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
        if reason == "target_metadata_unknown":
            debug_record["decision"]["unknown_fields"] = list(debug_record.get("unknown_metadata_fields") or [])
            debug_record["decision"]["fallback"] = "ai_generation"
        _append_reuse_debug_record(
            debug_path,
            _reuse_debug_record_for_mode(debug_record, mode=reuse_debug_mode, match=match),
        )
        return match

    if not isinstance(assets, list) or not assets:
        debug_record["target"] = _reuse_debug_asset_payload(target)
        if _collect_candidates_only:
            return {
                "_reuse_candidate_collection": True,
                "target": target,
                "threshold": debug_record.get("threshold_used"),
                "candidates": [],
                "debug_record": debug_record,
                "empty_reason": "empty_asset_store",
            }
        return finish("empty_asset_store")

    target = _enrich_reuse_target_keywords_once(target, keyword_client, target_keyword_cache)
    target = _normalize_asset_for_match(target, for_target=True) or target
    unknown_fields = _target_unknown_fields_for_reuse(target)
    if unknown_fields:
        debug_record["target"] = _reuse_debug_asset_payload(target)
        debug_record["unknown_metadata_fields"] = unknown_fields
        if _collect_candidates_only:
            return {
                "_reuse_candidate_collection": True,
                "target": target,
                "threshold": debug_record.get("threshold_used"),
                "candidates": [],
                "debug_record": debug_record,
                "empty_reason": "target_metadata_unknown",
            }
        return finish("target_metadata_unknown")
    if _is_skip_reuse_group(target.get("strict_reuse_group")):
        debug_record["target"] = _reuse_debug_asset_payload(target)
        if _collect_candidates_only:
            return {
                "_reuse_candidate_collection": True,
                "target": target,
                "threshold": debug_record.get("threshold_used"),
                "candidates": [],
                "debug_record": debug_record,
                "empty_reason": "material_category_skip",
            }
        return finish("material_category_skip")
    if _clean_text(target.get("asset_kind")) == "background" and not _clean_text(target.get("strict_reuse_group")):
        target["strict_reuse_group"] = _GENERAL_REUSE_GROUP
    route_mode = "split"
    target_route_group = _normalize_binary_reuse_group(
        target.get("strict_reuse_group"),
        default=_GENERAL_REUSE_GROUP,
    )
    debug_record["reuse_group_route"] = {
        "route_mode": route_mode,
        "strict_reuse_group": target_route_group,
        "routed": False,
        "match_index_path": _relative_output_path(match_index_path),
        "asset_count": len(assets) if isinstance(assets, list) else 0,
    }
    routed = _route_match_index_for_target_cached(
        library_root,
        index,
        match_index_path,
        target,
        reuse_search_context,
    )
    if routed is not None:
        index, match_index_path, assets, route_group = routed
        debug_record["match_index_path"] = _relative_output_path(match_index_path)
        debug_record["asset_count"] = len(assets) if isinstance(assets, list) else 0
        debug_record["reuse_group_route"] = {
            "route_mode": route_mode,
            "strict_reuse_group": route_group,
            "routed": True,
            "match_index_path": _relative_output_path(match_index_path),
            "asset_count": debug_record["asset_count"],
        }
        if not isinstance(assets, list) or not assets:
            debug_record["target"] = _reuse_debug_asset_payload(target)
            if _collect_candidates_only:
                return {
                    "_reuse_candidate_collection": True,
                    "target": target,
                    "threshold": debug_record.get("threshold_used"),
                    "candidates": [],
                    "debug_record": debug_record,
                    "empty_reason": "empty_routed_asset_store",
                }
            return finish("empty_routed_asset_store")
    threshold = _reuse_threshold_for_target(target, min_keyword_score)
    debug_record["threshold_used"] = threshold
    debug_record["target"] = _reuse_debug_asset_payload(target)
    score_details_cache: dict[int, dict[str, Any]] = {}
    if debug_path is not None and reuse_debug_mode != "off":
        # 仅调试路径扫描全量池，保留"候选为何被硬过滤"的可见性；生产路径跳过此开销。
        debug_record["candidate_scores"] = _collect_reuse_candidate_debug(
            target,
            assets,
            library_root,
            score_details_cache=score_details_cache,
        )

    eligible_assets, hard_filter_summary = _eligible_reuse_assets(
        target,
        assets,
        reuse_search_context,
        library_root,
        target_route_group,
    )
    debug_record["hard_filter"] = hard_filter_summary
    if not eligible_assets:
        if _collect_candidates_only:
            return {
                "_reuse_candidate_collection": True,
                "target": target,
                "threshold": threshold,
                "candidates": [],
                "debug_record": debug_record,
                "empty_reason": "no_eligible_candidate_after_hard_filter",
                "embedding_status": embedding_status,
            }
        return finish("no_eligible_candidate_after_hard_filter")

    pool_limit = max(DEFAULT_HYBRID_RETRIEVAL_POOL_SIZE, int(candidate_limit or DEFAULT_REUSE_CANDIDATE_LIMIT))
    bm25_ranked_candidates = _rank_reuse_candidates(
        target,
        eligible_assets,
        library_root=library_root,
        limit=pool_limit,
        score_details_cache=score_details_cache,
    )
    embedding_ranked_candidates = _rank_embedding_candidates(
        target,
        eligible_assets,
        library_root=library_root,
        embedding_index=embedding_index,
        limit=pool_limit,
        query_embedding_cache=reuse_search_context.query_embedding_cache if reuse_search_context else None,
        query_embedding_cache_dir=(
            reuse_search_context.query_embedding_cache_dir if reuse_search_context else None
        ),
    )
    substring_ranked_candidates = _rank_substring_candidates(
        target,
        eligible_assets,
        library_root=library_root,
        limit=pool_limit,
    )
    ranked_candidates = _rank_hybrid_reuse_candidates(
        target,
        eligible_assets,
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
    debug_record["policy_input_candidates"] = [
        _reuse_debug_candidate_payload(candidate, threshold=threshold) for candidate in candidates
    ]
    if not candidates:
        if _collect_candidates_only:
            return {
                "_reuse_candidate_collection": True,
                "target": target,
                "threshold": threshold,
                "candidates": [],
                "debug_record": debug_record,
                "empty_reason": "retrieval_no_candidate",
                "embedding_status": embedding_status,
            }
        return finish("retrieval_no_candidate")

    for candidate in candidates:
        candidate["library_dir"] = str(library_root)
        candidate["asset_root"] = str(library_root)
        candidate["library_search_order"] = _library_search_order
        candidate["_reuse_embedding_status"] = embedding_status

    if _collect_candidates_only:
        debug_record["decision"] = {
            "reused": False,
            "reason": "candidate_collection_only",
            "threshold_used": debug_record.get("threshold_used"),
            "policy_input_candidate_count": len(candidates),
        }
        return {
            "_reuse_candidate_collection": True,
            "target": target,
            "threshold": threshold,
            "candidates": candidates,
            "debug_record": debug_record,
            "embedding_status": embedding_status,
        }

    policy_outcome = _apply_reuse_policy_to_ranked_candidates(
        target,
        candidates,
        threshold=threshold,
        embedding_status=embedding_status,
        df_ratio_lookup={},
        keyword_client=keyword_client,
        reuse_session_state=reuse_session_state,
        llm_review_enabled=llm_review_enabled,
    )
    accepted_candidates = policy_outcome["accepted_candidates"]
    rejected_by_policy = policy_outcome["rejected_by_policy"]
    rejected_by_occupancy = policy_outcome["rejected_by_occupancy"]
    debug_record["llm_reviews_invoked"] = policy_outcome["llm_reviews_used"]
    debug_record["llm_reviews_budget"] = policy_outcome["llm_review_budget"]

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
    reason = _reuse_accept_reason(best)
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
        rel_image_path = _relative_output_path(image_path)

    asset = _dict(match.get("asset"))
    entry = {
        "image_path": rel_image_path,
        "reuse_asset_id": asset.get("asset_id"),
        "candidate_image_path": _relative_output_path(asset.get("image_path")),
        "reuse_library_dir": _relative_output_path(match.get("library_dir") or match.get("asset_root")),
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
    temp_path = manifest_path.with_name(f"{manifest_path.name}.tmp")
    temp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, manifest_path)


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
        if mode == "transparent_pad":
            raise
        shutil.copy2(reuse_image_path, dest)

    record_reused_ai_image_asset(
        session_dir=session_dir,
        session_image_path=dest,
        match=match,
    )


def evaluate_ai_image_reuse_matches_from_plan(
    *,
    plan_path: str | Path,
    library_dir: str | Path | list[str | Path] | tuple[str | Path, ...],
    keyword_client: Any | None = None,
    debug_path: str | Path | None = None,
    include_background: bool = True,
    materialize_matches: bool = False,
    llm_review_enabled: bool = True,
    reuse_debug_mode: str = "full",
    reuse_search_concurrency: int = DEFAULT_REUSE_MAX_WORKERS,
    target_keyword_batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
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
    library_roots = _normalize_reuse_library_dirs(library_dir)
    data = json.loads(plan_file.read_text(encoding="utf-8"))
    draft = PlanningDraft.model_validate(data)
    plan_data = draft.model_dump()
    context = {
        "theme": _clean_text(draft.meta.topic),
        "grade": _clean_text(getattr(draft.meta, "grade", "")),
        "subject": _clean_text(getattr(draft.meta, "subject", "")),
        "grade_band": _clean_text(getattr(draft.meta, "grade_band", "")),
    }
    reuse_session_state: dict[str, Any] = {
        "strict_asset_use_counts": {},
        "strict_asset_used_by": {},
    }
    reuse_search_context = ReuseSearchContext()
    reuse_debug_mode = _normalize_reuse_debug_mode(reuse_debug_mode)
    checks: list[dict[str, Any]] = []
    materialized_count = 0
    specs: list[dict[str, Any]] = []
    if include_background:
        background_prompt = build_background_content_prompt(draft.visual)
        specs.append(
            {
                "asset_kind": "background",
                "page_number": None,
                "slot_key": "background",
                "need": None,
                "prompt": background_prompt,
                "prompt_route": None,
                "background_route": _build_background_route(plan_data),
                "page_title": "",
                "page_type": "",
                "role": "",
                "aspect_ratio": "16:9",
                "debug_context": {"check_type": "plan_reuse_match", "asset_kind": "background"},
            }
        )
    for page in draft.pages:
        routed_needs = build_routed_image_needs(draft, page)
        for slot_key, need in iter_image_slot_keys(routed_needs):
            if need.source == "ai_generate":
                specs.append(
                    {
                        "asset_kind": "page_image",
                        "page_number": page.page_number,
                        "slot_key": slot_key,
                        "need": need,
                        "prompt": need.query,
                        "prompt_route": need.prompt_route,
                        "background_route": None,
                        "page_title": page.title,
                        "page_type": page.page_type,
                        "role": need.role,
                        "aspect_ratio": need.aspect_ratio,
                        "debug_context": {
                            "check_type": "plan_reuse_match",
                            "asset_kind": "page_image",
                            "page_number": page.page_number,
                            "slot_key": slot_key,
                            "aspect_ratio": need.aspect_ratio,
                        },
                    }
                )

    total_checks = len(specs)
    page_image_count = sum(1 for spec in specs if spec["asset_kind"] == "page_image")
    reuse_search_concurrency = max(1, int(reuse_search_concurrency or 1))
    PROGRESS_LOGGER.info(
        "AI image reuse plan check start: plan={}, checks={}, background={}, page_images={}, libraries={}, "
        "keywords={}, materialize={}, search_concurrency={}",
        plan_file,
        total_checks,
        bool(include_background),
        page_image_count,
        [str(root) for root in library_roots],
        bool(keyword_client),
        bool(materialize_matches),
        reuse_search_concurrency,
    )

    for root in library_roots:
        _load_reuse_library_for_search(root, reuse_search_context)

    targets = [
        _build_reuse_target_asset(
            asset_kind=spec["asset_kind"],
            prompt=spec["prompt"],
            prompt_route=spec["prompt_route"],
            background_route=spec["background_route"],
            theme=context["theme"],
            grade=context["grade"],
            subject=context["subject"],
            grade_band=context["grade_band"],
            page_title=spec["page_title"],
            page_type=spec["page_type"],
            role=spec["role"],
            aspect_ratio=spec["aspect_ratio"],
        )
        for spec in specs
    ]
    _prewarm_reuse_target_keywords(
        targets,
        keyword_client,
        reuse_search_context.target_keyword_cache,
        batch_size=target_keyword_batch_size,
    )

    def collect_candidates(spec: dict[str, Any], ordinal: int) -> dict[str, Any] | None:
        if spec["asset_kind"] == "background":
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} candidate search start: background prompt={}",
                ordinal,
                total_checks,
                _log_snippet(spec["prompt"], 96),
            )
        else:
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} candidate search start: page={}, slot={}, role={}, aspect={}, query={}",
                ordinal,
                total_checks,
                spec["page_number"],
                spec["slot_key"],
                _clean_text(spec["role"]) or "unknown",
                _clean_text(spec["aspect_ratio"]) or "unknown",
                _log_snippet(spec["prompt"], 96),
            )
        collection = find_reusable_ai_image_asset(
            library_dir=library_dir,
            asset_kind=spec["asset_kind"],
            prompt=spec["prompt"],
            prompt_route=spec["prompt_route"],
            background_route=spec["background_route"],
            theme=context["theme"],
            grade=context["grade"],
            subject=context["subject"],
            grade_band=context["grade_band"],
            page_title=spec["page_title"],
            page_type=spec["page_type"],
            role=spec["role"],
            aspect_ratio=spec["aspect_ratio"],
            keyword_client=None,
            debug_path=None,
            debug_context=spec["debug_context"],
            reuse_session_state=None,
            llm_review_enabled=llm_review_enabled,
            reuse_debug_mode=reuse_debug_mode,
            reuse_search_context=reuse_search_context,
            _collect_candidates_only=True,
        )
        candidate_count = (
            len(collection.get("candidates") or [])
            if isinstance(collection, dict)
            else 0
        )
        PROGRESS_LOGGER.info(
            "AI image reuse check {}/{} candidate search done: asset_kind={}, candidates={}",
            ordinal,
            total_checks,
            spec["asset_kind"],
            candidate_count,
        )
        return collection

    collected: list[dict[str, Any] | None] = [None] * len(specs)
    if specs and reuse_search_concurrency > 1:
        max_workers = min(reuse_search_concurrency, len(specs))
        PROGRESS_LOGGER.info(
            "AI image reuse candidate searches parallel start: checks={}, workers={}",
            len(specs),
            max_workers,
        )
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(collect_candidates, spec, index + 1): index
                for index, spec in enumerate(specs)
            }
            for future in as_completed(futures):
                index = futures[future]
                collected[index] = future.result()
        PROGRESS_LOGGER.info("AI image reuse candidate searches parallel done: checks={}", len(specs))
    else:
        for index, spec in enumerate(specs):
            collected[index] = collect_candidates(spec, index + 1)

    for index, spec in enumerate(specs):
        current_check = index + 1
        if spec["asset_kind"] == "background":
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} policy start: background",
                current_check,
                total_checks,
            )
        else:
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} policy start: page={}, slot={}",
                current_check,
                total_checks,
                spec["page_number"],
                spec["slot_key"],
            )
        match = _finalize_reuse_candidate_collection(
            collected[index],
            debug_path=debug_path,
            keyword_client=keyword_client,
            reuse_session_state=reuse_session_state,
            llm_review_enabled=llm_review_enabled,
            reuse_debug_mode=reuse_debug_mode,
        )
        session_image_path: Path | None = None
        if match:
            if materialize_matches:
                session_image_path = _materialize_plan_reuse_match(
                    session_dir=plan_file.parent,
                    asset_kind=spec["asset_kind"],
                    page_number=spec["page_number"],
                    slot_key=spec["slot_key"],
                    match=match,
                )
                materialized_count += 1
            mark_context = dict(spec["debug_context"])
            mark_context["session_image_path"] = str(session_image_path or "")
            mark_reused_ai_image_asset_in_session(match, reuse_session_state, mark_context)
        checks.append(
            _plan_reuse_check_record(
                spec["asset_kind"],
                spec["page_number"],
                spec["slot_key"],
                spec["need"].model_dump() if spec["need"] is not None else None,
                match,
                session_image_path=session_image_path,
            )
        )
        if spec["asset_kind"] == "background":
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} done: background matched={}, asset_id={}, reason={}, materialized={}",
                current_check,
                total_checks,
                bool(match),
                _match_asset_id(match),
                _match_decision_reason(match),
                bool(session_image_path),
            )
        else:
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} done: page={}, slot={}, matched={}, asset_id={}, score={}, reason={}, "
                "materialized={}",
                current_check,
                total_checks,
                spec["page_number"],
                spec["slot_key"],
                bool(match),
                _match_asset_id(match),
                _match_score(match),
                _match_decision_reason(match),
                bool(session_image_path),
            )

    matched = [item for item in checks if item["matched"]]
    PROGRESS_LOGGER.info(
        "AI image reuse plan check complete: matched={}/{}, materialized={}, debug_path={}",
        len(matched),
        len(checks),
        materialized_count,
        debug_path or "",
    )
    return {
        "schema_version": 1,
        "asset_root": _relative_output_path(library_roots[0]),
        "asset_roots": [_relative_output_path(root) for root in library_roots],
        "generated_images": False,
        "updated_asset_store": False,
        "materialize_matches": materialize_matches,
        "materialized_count": materialized_count,
        "reuse_search_concurrency": reuse_search_concurrency,
        "target_keyword_batch_size": target_keyword_batch_size,
        "materials_dir": _relative_output_path(plan_file.parent / "materials") if materialize_matches else "",
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
        "candidate_image_path": _relative_output_path(match.get("candidate_image_path")) if match else "",
        "reuse_library_dir": _relative_output_path(match.get("library_dir") or match.get("asset_root")) if match else "",
        "session_image_path": _relative_output_path(session_image_path) if session_image_path else "",
        "keyword_score": match.get("keyword_score") if match else None,
        "policy_score": match.get("policy_score") if match else None,
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


def _log_snippet(value: Any, limit: int = 120) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _match_asset_id(match: dict[str, Any] | None) -> str:
    if not match:
        return ""
    return _clean_text(_dict(match.get("asset")).get("asset_id"))


def _match_score(match: dict[str, Any] | None) -> float | str:
    if not match:
        return ""
    score = match.get("policy_score")
    if score is None:
        score = _dict(match.get("score_details")).get("policy_score")
    if score is None:
        score = match.get("keyword_score")
    if score is None:
        score = _dict(match.get("score_details")).get("score")
    try:
        return round(float(score), 4)
    except (TypeError, ValueError):
        return ""


def _match_decision_reason(match: dict[str, Any] | None) -> str:
    if not match:
        return "no_match"
    policy = _dict(match.get("reuse_policy"))
    return (
        _clean_text(policy.get("reason"))
        or _clean_text(match.get("multi_library_reuse_reason"))
        or "matched"
    )


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
        elif mode == "transparent_pad":
            result = _transparent_pad_image(image, target_ratio, _target_size_from_transform_policy(transform_policy))
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


def _target_size_from_transform_policy(transform_policy: dict[str, Any]) -> tuple[int, int] | None:
    width = _optional_int(transform_policy.get("target_width"))
    height = _optional_int(transform_policy.get("target_height"))
    if width and height and width > 0 and height > 0:
        return width, height
    return None


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


def _transparent_pad_image(image: Any, target_ratio: float, target_size: tuple[int, int] | None = None) -> Any:
    from PIL import Image

    source = image.convert("RGBA")
    width, height = source.size
    if target_size is None:
        canvas_width, canvas_height = _contain_canvas_size(width, height, target_ratio)
    else:
        canvas_width, canvas_height = target_size

    scale = min(canvas_width / max(1, width), canvas_height / max(1, height))
    scaled_width = max(1, int(round(width * scale)))
    scaled_height = max(1, int(round(height * scale)))
    if (scaled_width, scaled_height) != source.size:
        source = source.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    left = (canvas_width - scaled_width) // 2
    top = (canvas_height - scaled_height) // 2
    canvas.paste(source, (left, top), source)
    return canvas


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
        "context": _relative_output_context(context),
        "asset_root": _relative_output_path(library_root),
        "db_path": _relative_output_path(db_path),
        "match_index_path": _relative_output_path(match_index_path),
        "asset_count": asset_count,
        "candidate_limit": candidate_limit,
        "min_keyword_score": min_keyword_score,
        "threshold_used": min_keyword_score,
        "target": {},
        "candidate_scores": [],
        "ranked_candidates": [],
        "policy_input_candidates": [],
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
    temp_path = debug_path.with_name(f"{debug_path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, debug_path)


def _normalize_reuse_debug_mode(value: Any) -> str:
    mode = _clean_text(value).casefold()
    if mode in {"full", "summary", "off"}:
        return mode
    env_mode = _clean_text(os.environ.get("EDUPPTX_AI_IMAGE_REUSE_DEBUG_MODE")).casefold()
    if env_mode in {"full", "summary", "off"}:
        return env_mode
    return "full"


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
    for key in ("policy_candidates", "policy_input_candidates", "ranked_candidates", "candidate_scores"):
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
        "image_path": _relative_output_path(candidate.get("image_path")),
        "candidate_image_path": _relative_output_path(candidate.get("candidate_image_path")),
        "caption": _asset_caption(candidate),
        "reuse_level": candidate.get("reuse_level"),
        "keyword_score": candidate.get("keyword_score"),
        "embedding_score": candidate.get("embedding_score"),
        "substring_score": candidate.get("substring_score"),
        "policy_score": candidate.get("policy_score"),
        "hybrid_score": candidate.get("hybrid_score"),
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
        "image_path": _relative_output_path(asset.get("image_path")),
        "caption": _asset_caption(asset),
        "query": _asset_query(asset),
        "generation_prompt": _asset_generation_prompt(asset),
        "style_prompt": _asset_style_prompt(asset),
        "prompt_route": _clean_prompt_route(asset.get("prompt_route")),
        "background_route": _clean_background_route(asset.get("background_route")),
        "color_temperature": _clean_text(asset.get("color_temperature")),
        "theme": _clean_text(asset.get("theme")),
        "unit_ref": _unit_ref_for_asset(asset),
        "topic_refs": _topic_refs_for_asset(asset),
        "teaching_intent": asset.get("teaching_intent"),
        "page_type": _asset_page_type(asset),
        "subject": asset.get("subject"),
        "general": _optional_bool(asset.get("general")),
        "grade": grade,
        "grade_norm": asset.get("grade_norm"),
        "grade_band": _normalize_grade_band_value(asset.get("grade_band")),
        "aspect_ratio": asset.get("aspect_ratio"),
        "context_summary": asset.get("context_summary"),
        "reuse_level": reuse_policy["reuse_level"],
        "generic_support_allowed": reuse_policy["generic_support_allowed"],
    }


def _reuse_debug_candidate_payload(candidate: dict[str, Any], *, threshold: float | None = None) -> dict[str, Any]:
    payload = _reuse_debug_asset_payload(_dict(candidate.get("asset")))
    payload["keyword_score"] = candidate.get("keyword_score")
    payload["embedding_score"] = candidate.get("embedding_score")
    payload["substring_score"] = candidate.get("substring_score")
    payload["policy_score"] = candidate.get("policy_score") or _candidate_policy_score(candidate)
    payload["hybrid_score"] = candidate.get("hybrid_score")
    payload["rrf_score"] = candidate.get("rrf_score")
    payload["retrieval_ranks"] = candidate.get("retrieval_ranks") or {}
    payload["substring_hits"] = candidate.get("substring_hits") or []
    payload["candidate_image_path"] = _relative_output_path(candidate.get("candidate_image_path"))
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
    score_details_cache: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        payload = _reuse_debug_asset_payload(item)
        image_path = _resolve_asset_image_path(library_root, item.get("image_path"))
        if image_path is None or not image_path.exists():
            payload["keyword_score"] = 0.0
            payload["candidate_image_path"] = _relative_output_path(image_path)
            payload["score_details"] = {
                "score": 0.0,
                "reject_reason": "missing_candidate_image",
            }
            rows.append(payload)
            continue

        details = _cached_base_reuse_score_details(target, item, score_details_cache)
        score = float(details.get("score") or 0.0)
        payload["keyword_score"] = round(score, 4)
        payload["candidate_image_path"] = _relative_output_path(image_path)
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

    PROGRESS_LOGGER.info(
        "AI image reuse LLM review start: target={}, candidate_asset_id={}, threshold={}",
        _log_snippet(_asset_content_prompt(target), 80),
        _clean_text(candidate.get("asset_id")),
        round(accept_threshold, 4),
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
        PROGRESS_LOGGER.warning(
            "AI image reuse LLM review failed: candidate_asset_id={}, error={}",
            _clean_text(candidate.get("asset_id")),
            _log_snippet(exc, 160),
        )
        return _normalize_reuse_review_score_response(
            {"score": 0.0, "brief_reason": f"llm_review_failed: {str(exc)[:160]}"},
            accept_threshold=accept_threshold,
        )

    if not isinstance(response, dict):
        PROGRESS_LOGGER.warning(
            "AI image reuse LLM review invalid response: candidate_asset_id={}",
            _clean_text(candidate.get("asset_id")),
        )
        return _normalize_reuse_review_score_response(
            {"score": 0.0, "brief_reason": "llm_review_invalid_response"},
            accept_threshold=accept_threshold,
        )
    normalized = _normalize_reuse_review_score_response(response, accept_threshold=accept_threshold)
    PROGRESS_LOGGER.info(
        "AI image reuse LLM review done: candidate_asset_id={}, decision={}, score={}",
        _clean_text(candidate.get("asset_id")),
        _clean_text(normalized.get("decision")),
        round(float(normalized.get("score") or 0.0), 4),
    )
    return normalized


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


def _normalize_grade_norm_value(value: Any) -> str:
    text = _clean_text(value)
    return text if text in _ALLOWED_GRADE_NORMS else _OTHER_GRADE


def _normalize_grade_band_value(value: Any) -> str:
    text = _clean_text(value)
    return text if text in _ALLOWED_GRADE_BANDS else _OTHER_GRADE


def _effective_grade_band(asset: dict[str, Any]) -> str:
    """存量资产 band=其他 但 grade_norm 已知时，派生出有效 band（避免幽灵 unknown 拦截）。"""
    band = _normalize_grade_band_value(asset.get("grade_band"))
    if band != _OTHER_GRADE:
        return band
    return grade_band_from_norm(asset.get("grade_norm"))


def _normalize_subject_value(value: Any) -> str:
    text = _clean_text(value)
    return text if text in _ALLOWED_SUBJECTS else _OTHER_SUBJECT


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _apply_general_from_payload(asset: dict[str, Any], payload: dict[str, Any]) -> None:
    general = _optional_bool(payload.get("general"))
    if general is not None:
        asset["general"] = general


def _target_metadata_unknown_fields(asset: dict[str, Any]) -> list[str]:
    unknown: list[str] = []
    if _normalize_subject_value(asset.get("subject")) == _OTHER_SUBJECT:
        unknown.append("subject")
    if _normalize_grade_norm_value(asset.get("grade_norm")) == _OTHER_GRADE:
        unknown.append("grade_norm")
    if _effective_grade_band(asset) == _OTHER_GRADE:
        unknown.append("grade_band")
    return unknown


def _target_unknown_fields_for_reuse(asset: dict[str, Any]) -> list[str]:
    ignored = {"subject", "grade_norm", "grade_band"}
    return [field for field in _target_metadata_unknown_fields(asset) if field not in ignored]


def _candidate_unknown_fields_for_reuse(
    asset: dict[str, Any],
    subject_decision: dict[str, Any],
) -> list[str]:
    unknown = _target_metadata_unknown_fields(asset)
    ignored = {"subject", "grade_norm", "grade_band"}
    return [field for field in unknown if field not in ignored]


def _grade_info_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    return {
        "grade_norm": _normalize_grade_norm_value(payload.get("grade_norm")),
        "grade_band": _normalize_grade_band_value(payload.get("grade_band")),
    }


def _grade_info_from_asset(asset: dict[str, Any]) -> dict[str, str]:
    return {
        "grade_norm": _normalize_grade_norm_value(asset.get("grade_norm")),
        "grade_band": _normalize_grade_band_value(asset.get("grade_band")),
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


def _unit_ref_for_asset(asset: dict[str, Any]) -> str:
    return _clean_topic_ref(asset.get("unit_ref") or asset.get("unit"))


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
    grade_info = _grade_info_from_asset(asset)
    exclusions = {
        grade,
        _clean_text(asset.get("grade_norm")),
        _clean_text(asset.get("grade_band")),
        _clean_text(grade_info.get("grade_norm")),
        _clean_text(grade_info.get("grade_band")),
        subject,
        _unit_ref_for_asset(asset),
    }
    if grade and subject:
        exclusions.add(f"{grade}{subject}")
        exclusions.add(f"{grade} {subject}")
    grade_norm = _clean_text(grade_info.get("grade_norm"))
    if grade_norm and subject:
        exclusions.add(f"{grade_norm}{subject}")
        exclusions.add(f"{grade_norm} {subject}")
    return {item for item in exclusions if item}


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


def _page_retrieval_text(asset: dict[str, Any]) -> str:
    return _asset_caption(asset)


def _background_retrieval_text(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("normalized_prompt"))


def _build_match_text(asset: dict[str, Any]) -> str:
    if _is_background_asset(asset):
        return _background_retrieval_text(asset)

    return _page_retrieval_text(asset)


def _asset_embedding_text(asset: dict[str, Any]) -> str:
    if _is_background_asset(asset):
        return _background_retrieval_text(asset)

    return _page_retrieval_text(asset)


def _target_embedding_text(asset: dict[str, Any]) -> str:
    if _is_background_asset(asset):
        return _background_retrieval_text(asset)

    return _page_retrieval_text(asset)


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


def _embedding_model_sidecar_matches(stored_model: Any, current_model: str) -> bool:
    stored = _clean_text(stored_model)
    current = _clean_text(current_model)
    if stored == current:
        return True
    if not stored or not current:
        return False
    current_path = Path(current)
    if not current_path.exists():
        return False
    stored_name = Path(stored.replace("\\", "/")).name
    current_name = current_path.name
    return bool(stored_name and current_name and stored_name == current_name)


def _load_embedding_model(model_name: str = DEFAULT_EMBEDDING_MODEL) -> Any:
    model_name = _embedding_model_name(model_name)
    with _EMBEDDING_MODEL_LOCK:
        cached = _EMBEDDING_MODEL_CACHE.get(model_name)
        if cached is not None:
            return cached
        from sentence_transformers import SentenceTransformer

        PROGRESS_LOGGER.info("AI image embedding model load start: model={}", model_name)
        model = SentenceTransformer(model_name)
        _EMBEDDING_MODEL_CACHE[model_name] = model
        PROGRESS_LOGGER.info("AI image embedding model load done: model={}", model_name)
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
    log_encode = len(cleaned) > 1 or not query
    if log_encode:
        PROGRESS_LOGGER.info(
            "AI image embedding encode start: texts={}, query={}, model={}",
            len(cleaned),
            bool(query),
            model_name,
        )
    with _EMBEDDING_MODEL_LOCK:
        vectors = model.encode(
            cleaned,
            batch_size=DEFAULT_EMBEDDING_BATCH_SIZE,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
    if log_encode:
        PROGRESS_LOGGER.info(
            "AI image embedding encode done: texts={}, query={}, model={}",
            len(cleaned),
            bool(query),
            model_name,
        )
    import numpy as np

    if len(vectors.shape) == 1:
        vectors = vectors.reshape(1, -1)
    return np.asarray(vectors, dtype="float32")


def _build_match_key(asset: dict[str, Any]) -> str:
    if _is_background_asset(asset):
        terms = _bm25_tokens_from_values([_background_retrieval_text(asset)])
    else:
        terms = _bm25_tokens_from_values([_page_retrieval_text(asset)])
    return "|".join(terms[:12])


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


def _source_pptx_refs_for_asset(asset: dict[str, Any]) -> list[dict[str, Any]]:
    raw_refs = asset.get("source_pptx_refs")
    if not isinstance(raw_refs, list):
        return []
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for raw in raw_refs:
        if not isinstance(raw, dict):
            continue
        ref: dict[str, Any] = {
            "pptx_id": _clean_text(raw.get("pptx_id")),
            "period_id": _clean_text(raw.get("period_id")),
            "file_path": _clean_text(raw.get("file_path")),
            "file_name": _clean_text(raw.get("file_name")),
            "absolute_path": _clean_text(raw.get("absolute_path")),
            "source": _clean_text(raw.get("source")),
        }
        slide_no = _clean_text(raw.get("slide_no"))
        shape_idx = _clean_text(raw.get("shape_idx"))
        source_media_path = _clean_text(raw.get("source_media_path"))
        if slide_no:
            try:
                ref["slide_no"] = int(slide_no)
            except ValueError:
                ref["slide_no"] = slide_no
        if shape_idx:
            try:
                ref["shape_idx"] = int(shape_idx)
            except ValueError:
                ref["shape_idx"] = shape_idx
        if source_media_path:
            ref["source_media_path"] = source_media_path
        ref = {key: value for key, value in ref.items() if value not in ("", None)}
        if not any(ref.get(key) for key in ("pptx_id", "file_path", "file_name", "absolute_path")):
            continue
        key = (
            _clean_text(ref.get("pptx_id")),
            _clean_text(ref.get("file_path")),
            _clean_text(ref.get("absolute_path")),
            _clean_text(ref.get("slide_no")),
            _clean_text(ref.get("shape_idx")),
            _clean_text(ref.get("source_media_path")),
            _clean_text(ref.get("source")),
        )
        if key in seen:
            continue
        seen.add(key)
        refs.append(ref)
    return refs


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
    return normalized in {item.casefold().replace(" ", "") for item in _NOISE_TOKENS}


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


def _asset_content_prompt(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("content_prompt")) or _clean_text(asset.get("prompt"))


def _asset_caption(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("caption"))


def _asset_query(asset: dict[str, Any]) -> str:
    """Verbose classification text. Falls back to legacy verbose fields so
    pre-rebuild libraries stay classifiable."""
    return (
        _clean_text(asset.get("query"))
        or _clean_text(asset.get("detail_prompt"))
        or _asset_content_prompt(asset)
    )


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
    return _clean_text(asset.get("generation_prompt")) or _clean_text(asset.get("normalized_prompt"))


def _asset_style_prompt(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("style_prompt")) or _route_style_prompt(_clean_prompt_route(asset.get("prompt_route")))


def _asset_page_type(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("page_type"))


def _route_grade_family(asset: dict[str, Any]) -> str:
    return _normalize_grade_band_value(asset.get("grade_band"))


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
    caption: str = "",
    grade_band: str = "",
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
    grade_info = normalize_grade_info(grade, grade_band)
    target = {
        "asset_id": "target_" + hashlib.sha256(asset_key.encode("utf-8")).hexdigest()[:16],
        "asset_kind": asset_kind,
        "image_path": "",
        "aspect_ratio": aspect_ratio,
        "theme": _clean_text(theme),
        "topic_refs": extract_topic_refs(theme),
        "query": content_prompt,
        "caption": _clean_text(caption) or content_prompt,
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
        "page_type": _clean_text(page_type),
        "subject_hint": _clean_text(subject),
        "grade_hint": _clean_text(grade),
        "grade_norm": grade_info["grade_norm"],
        "grade_band": grade_info["grade_band"],
        "subject": _normalize_subject_value(subject),
    }
    _normalize_rich_asset_fields(target, keep_match_keywords=True)
    return target


def _rank_reuse_candidates(
    target: dict[str, Any],
    assets: list[Any],
    *,
    library_root: Path,
    limit: int,
    score_details_cache: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        image_path = _resolve_asset_image_path(library_root, item.get("image_path"))
        if image_path is None or not image_path.exists():
            continue
        score_details = _cached_base_reuse_score_details(target, item, score_details_cache)
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


def _cached_base_reuse_score_details(
    target: dict[str, Any],
    candidate: dict[str, Any],
    score_details_cache: dict[int, dict[str, Any]] | None,
) -> dict[str, Any]:
    if score_details_cache is None:
        return _score_reuse_candidate_details(target, candidate)
    cache_key = id(candidate)
    details = score_details_cache.get(cache_key)
    if details is None:
        details = _score_reuse_candidate_details(target, candidate)
        score_details_cache[cache_key] = details
    return details


def _query_embedding_cache_paths(cache_dir: str | Path) -> tuple[Path, Path]:
    root = Path(cache_dir).expanduser().resolve()
    return (
        root / DEFAULT_QUERY_EMBEDDING_CACHE_FILENAME,
        root / DEFAULT_QUERY_EMBEDDING_CACHE_META_FILENAME,
    )


def _load_query_embedding_disk_cache(cache_dir: str | Path, *, model_name: str) -> dict[str, Any]:
    index_path, meta_path = _query_embedding_cache_paths(cache_dir)
    if not index_path.exists() or not meta_path.exists():
        return {}
    meta = _read_json_if_exists(meta_path)
    if (
        int(meta.get("schema_version") or 0) != QUERY_EMBEDDING_CACHE_SCHEMA_VERSION
        or _clean_text(meta.get("model")) != _clean_text(model_name)
    ):
        return {}
    try:
        import numpy as np

        data = np.load(index_path, allow_pickle=False)
        try:
            keys = [str(item) for item in data["keys"].tolist()]
            vectors = np.asarray(data["vectors"], dtype="float32")
        finally:
            data.close()
    except Exception as exc:
        PROGRESS_LOGGER.warning(
            "AI image query embedding cache ignored: path={}, reason={}",
            index_path,
            str(exc)[:180],
        )
        return {}
    if len(vectors.shape) == 1:
        vectors = vectors.reshape(1, -1)
    count = min(len(keys), int(vectors.shape[0]))
    return {
        keys[index]: vectors[index]
        for index in range(count)
        if keys[index]
    }


def _write_query_embedding_disk_cache(
    cache_dir: str | Path,
    cache: dict[str, Any],
    *,
    model_name: str,
) -> None:
    model_prefix = f"{model_name}:"
    rows = [
        (key, value)
        for key, value in sorted(cache.items())
        if isinstance(key, str) and key.startswith(model_prefix)
    ]
    if not rows:
        return
    try:
        import numpy as np

        keys: list[str] = []
        vectors: list[Any] = []
        expected_dim: int | None = None
        for key, value in rows:
            vector = np.asarray(value, dtype="float32")
            if len(vector.shape) != 1:
                continue
            dim = int(vector.shape[0])
            if expected_dim is None:
                expected_dim = dim
            if dim != expected_dim:
                continue
            keys.append(key)
            vectors.append(vector)
        if not keys:
            return
        index_path, meta_path = _query_embedding_cache_paths(cache_dir)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        temp_index_path = index_path.with_name(f"{index_path.name}.tmp")
        temp_meta_path = meta_path.with_name(f"{meta_path.name}.tmp")
        with temp_index_path.open("wb") as handle:
            np.savez_compressed(
                handle,
                keys=np.asarray(keys, dtype=str),
                vectors=np.vstack(vectors).astype("float32"),
            )
        meta = {
            "schema_version": QUERY_EMBEDDING_CACHE_SCHEMA_VERSION,
            "model": model_name,
            "entry_count": len(keys),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        temp_meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_index_path, index_path)
        os.replace(temp_meta_path, meta_path)
    except Exception as exc:
        PROGRESS_LOGGER.warning(
            "AI image query embedding cache write skipped: dir={}, reason={}",
            cache_dir,
            str(exc)[:180],
        )


def _rank_embedding_candidates(
    target: dict[str, Any],
    assets: list[Any],
    *,
    library_root: Path,
    embedding_index: dict[str, Any],
    limit: int,
    query_embedding_cache: dict[str, Any] | None = None,
    query_embedding_cache_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    vectors = embedding_index.get("vectors")
    asset_ids = embedding_index.get("asset_ids")
    if vectors is None or not isinstance(asset_ids, list) or not asset_ids:
        return []

    try:
        import numpy as np

        model_name = _embedding_model_name()
        if query_embedding_cache is None:
            query_embedding_cache = {}
        if query_embedding_cache_dir is not None:
            query_embedding_cache.update(
                {
                    key: value
                    for key, value in _load_query_embedding_disk_cache(
                        query_embedding_cache_dir,
                        model_name=model_name,
                    ).items()
                    if key not in query_embedding_cache
                }
            )
        disk_cache_dirty = False

        def query_vector_for(text: str, purpose: str):
            nonlocal disk_cache_dirty
            cache_key = f"{model_name}:{purpose}:{text}"
            if cache_key in query_embedding_cache:
                return query_embedding_cache[cache_key]
            encoded = _encode_embedding_texts([text], query=True)[0]
            query_embedding_cache[cache_key] = encoded
            disk_cache_dirty = True
            return encoded

        query_vector = query_vector_for(_target_embedding_text(target), "target")
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
            color_query_vector = query_vector_for(target_color_bias, "background_color_bias")
            color_scores = np.asarray(color_bias_vectors).dot(color_query_vector)
            background_color_bias_scores_by_id = {
                _clean_text(asset_id): float(color_scores[idx])
                for idx, asset_id in enumerate(color_bias_asset_ids)
            }
        if disk_cache_dirty and query_embedding_cache_dir is not None:
            _write_query_embedding_disk_cache(
                query_embedding_cache_dir,
                query_embedding_cache,
                model_name=model_name,
            )
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
        terms = _bm25_tokens_from_values([_page_retrieval_text(target)])
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
            score_details = _score_reuse_candidate_details(target, asset)
            bm25_score = float(score_details.get("score") or 0.0)
            candidate["keyword_score"] = round(max(float(candidate.get("keyword_score") or 0.0), bm25_score), 4)
        candidate["rrf_score"] = round(rrf_scores.get(asset_id, 0.0), 6)
        candidate["hybrid_score"] = round(rrf_scores.get(asset_id, 0.0) / max(max_rrf, 1e-9), 4)
        candidate["transform_policy"] = score_details.get("transform_policy") or {}
        policy_score = _candidate_policy_score(candidate, score_details)
        candidate["policy_score"] = policy_score
        score_details.update(
            {
                "embedding_score": candidate.get("embedding_score"),
                "substring_score": candidate.get("substring_score"),
                "substring_hits": candidate.get("substring_hits"),
                "background_color_bias_embedding_score": candidate.get("background_color_bias_embedding_score"),
                "rrf_score": candidate.get("rrf_score"),
                "hybrid_score": candidate.get("hybrid_score"),
                "policy_score": policy_score,
                "retrieval_ranks": candidate.get("retrieval_ranks"),
            }
        )
        candidate["score_details"] = _debug_score_details(score_details)
        results.append(candidate)

    results.sort(
        key=lambda item: (
            float(item.get("policy_score") or 0.0),
            float(item.get("keyword_score") or 0.0),
            float(item.get("embedding_score") or 0.0),
            float(item.get("hybrid_score") or 0.0),
        ),
        reverse=True,
    )
    return results[: max(1, int(limit or DEFAULT_REUSE_CANDIDATE_LIMIT))]


def _copy_transform_policy(target: dict[str, Any], candidate: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "decision": "accept",
        "mode": "copy",
        "crop_loss": 0.0,
        "transform_penalty": 0.0,
        "candidate_aspect_ratio": _asset_aspect_ratio_label(candidate),
        "target_aspect_ratio": _asset_aspect_ratio_label(target),
        "reason": reason,
    }


def _reuse_transform_policy(target: dict[str, Any], candidate: dict[str, Any], *, reason: str) -> dict[str, Any]:
    target_bucket = normalize_aspect_bucket(_asset_aspect_ratio_label(target))
    candidate_bucket = normalize_aspect_bucket(_asset_aspect_ratio_label(candidate))
    if target_bucket == candidate_bucket and target_bucket in _ASPECT_REUSE_BUCKET_VALUES:
        return _copy_transform_policy(target, candidate, reason=reason)
    if (target_bucket, candidate_bucket) not in ALLOWED_CROSS_ASPECT_RATIO_REUSE_PAIRS:
        return _copy_transform_policy(target, candidate, reason=reason)

    policy = {
        "decision": "accept",
        "mode": "transparent_pad",
        "crop_loss": round(_aspect_ratio_loss(target, candidate), 4),
        "transform_penalty": ASPECT_RATIO_ADJACENT_PENALTY,
        "candidate_aspect_ratio": candidate_bucket,
        "target_aspect_ratio": target_bucket,
        "reason": "transparent_pad_cross_aspect",
    }
    target_size = _target_transform_size(target)
    if target_size is not None:
        policy["target_width"], policy["target_height"] = target_size
    return policy


def _target_transform_size(target: dict[str, Any]) -> tuple[int, int] | None:
    for width_key, height_key in (
        ("target_width", "target_height"),
        ("padded_width", "padded_height"),
        ("actual_width", "actual_height"),
        ("width", "height"),
    ):
        width = _optional_int(target.get(width_key))
        height = _optional_int(target.get(height_key))
        if width and height and width > 0 and height > 0:
            return width, height
    return None


def _reuse_size_distance(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    if _aspect_ratio_penalty(target, candidate) < 0:
        return float("inf")
    return _aspect_ratio_loss(target, candidate)


def _prefer_size_close_tier_item(tier_items: list[dict[str, Any]]) -> dict[str, Any]:
    if not tier_items:
        return {}
    top_score = float(tier_items[0].get("keyword_score") or 0.0)
    close_items = [
        item for item in tier_items
        if top_score - float(item.get("keyword_score") or 0.0) <= T_GAP
    ]
    return min(
        close_items or tier_items,
        key=lambda item: (
            float(item.get("size_distance") or 0.0),
            -float(item.get("keyword_score") or 0.0),
        ),
    )


def _reuse_static_filter_reject_reason(target: dict[str, Any], candidate: dict[str, Any]) -> str:
    """与 per-image aspect 无关的确定性硬拒。

    只依赖候选自身 + plan 常量目标字段（asset_kind / subject），因此 ""==eligible
    的判定可按 (library, group, subject, kind) 缓存。等价于 _reuse_hard_filter_reject_reason
    去掉 aspect 检查，再加上 _score_reuse_candidate_details 在上游做的 asset_kind 等值。
    作布尔使用（""==通过），分支顺序不影响结果集合。
    """
    if _clean_text(target.get("asset_kind")) != _clean_text(candidate.get("asset_kind")):
        return "asset_kind_mismatch"
    target_group = _normalize_binary_reuse_group(target.get("strict_reuse_group"), default="")
    candidate_group = _normalize_binary_reuse_group(candidate.get("strict_reuse_group"), default="")
    if target_group == _CONTENT_REUSE_GROUP:
        return "material_category_skip"
    if candidate_group == _CONTENT_REUSE_GROUP:
        return "candidate_material_category_skip"
    if target_group and candidate_group and target_group != candidate_group:
        return "strict_reuse_group_mismatch"
    subject_decision = _subject_scope_decision(target, candidate)
    if _candidate_unknown_fields_for_reuse(candidate, subject_decision):
        return "candidate_metadata_unknown"
    if not subject_decision["compatible"]:
        return "subject_mismatch"
    return ""


def _eligible_reuse_assets(
    target: dict[str, Any],
    assets: list[Any],
    reuse_search_context: "ReuseSearchContext | None",
    library_root: Any,
    route_group: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """召回前剪枝：静态层（可缓存）+ 每图 aspect 层。

    静态层只依赖候选与 plan 常量（subject/asset_kind），按
    (library, group, subject, kind) 缓存；aspect 随图变化，不缓存。
    """
    asset_kind = _clean_text(target.get("asset_kind"))
    subject = _normalize_subject_value(target.get("subject"))
    cache_key = (str(library_root), route_group, subject, asset_kind)

    static_subset: list[dict[str, Any]] | None = None
    if reuse_search_context is not None:
        with reuse_search_context.cache_lock:
            static_subset = reuse_search_context.eligible_static_cache.get(cache_key)
    cache_hit = static_subset is not None

    if static_subset is None:
        static_subset = [
            candidate
            for candidate in assets
            if isinstance(candidate, dict)
            and _reuse_static_filter_reject_reason(target, candidate) == ""
        ]
        if reuse_search_context is not None:
            with reuse_search_context.cache_lock:
                # setdefault: 并发首次构建时所有线程共享同一子集
                static_subset = reuse_search_context.eligible_static_cache.setdefault(cache_key, static_subset)

    aspect_filtered = 0
    eligible: list[dict[str, Any]] = []
    for candidate in static_subset:
        if _aspect_ratio_penalty(target, candidate) < 0:
            aspect_filtered += 1
            continue
        eligible.append(candidate)

    summary = {
        "routed_count": sum(1 for candidate in assets if isinstance(candidate, dict)),
        "static_subset_count": len(static_subset),
        "aspect_filtered_count": aspect_filtered,
        "eligible_count": len(eligible),
        "static_cache_hit": cache_hit,
    }
    return eligible, summary


def _reuse_hard_filter_reject_reason(target: dict[str, Any], candidate: dict[str, Any]) -> str:
    target_group = _normalize_binary_reuse_group(target.get("strict_reuse_group"), default="")
    candidate_group = _normalize_binary_reuse_group(candidate.get("strict_reuse_group"), default="")
    if target_group == _CONTENT_REUSE_GROUP:
        return "material_category_skip"
    if candidate_group == _CONTENT_REUSE_GROUP:
        return "candidate_material_category_skip"
    if target_group and candidate_group and target_group != candidate_group:
        return "strict_reuse_group_mismatch"

    subject_decision = _subject_scope_decision(target, candidate)
    candidate_unknown_fields = _candidate_unknown_fields_for_reuse(candidate, subject_decision)
    if candidate_unknown_fields:
        return "candidate_metadata_unknown"

    penalty = _aspect_ratio_penalty(target, candidate)
    if penalty < 0:
        return "aspect_ratio_too_far"

    if not subject_decision["compatible"]:
        return "subject_mismatch"
    return ""


def _asset_general_value(asset_or_value: Any) -> bool | None:
    if isinstance(asset_or_value, dict):
        return _optional_bool(asset_or_value.get("general"))
    return None


def _asset_subject_value(asset_or_value: Any) -> str:
    if isinstance(asset_or_value, dict):
        return _normalize_subject_value(asset_or_value.get("subject"))
    return _normalize_subject_value(asset_or_value)


def _subject_scope_decision(target: Any, candidate: Any) -> dict[str, Any]:
    target_subject = _asset_subject_value(target)
    candidate_subject = _asset_subject_value(candidate)
    candidate_general = _asset_general_value(candidate)
    general_missing = candidate_general is None
    defaulted_from_other = bool(candidate_subject == _OTHER_SUBJECT)

    if candidate_general is True:
        mode = "general"
        compatible = True
    elif defaulted_from_other:
        mode = "subject_other_default"
        compatible = True
    elif target_subject in _KNOWN_SUBJECTS and candidate_subject == target_subject:
        mode = "same_subject"
        compatible = True
    elif target_subject == _OTHER_SUBJECT:
        mode = "target_subject_unknown"
        compatible = False
    else:
        mode = "subject_mismatch"
        compatible = False

    return {
        "compatible": compatible,
        "subject_filter_mode": mode,
        "target_subject": target_subject,
        "candidate_subject": candidate_subject,
        "candidate_general": candidate_general,
        "general_missing": general_missing,
        "general_defaulted_from_subject_other": defaulted_from_other,
    }


def _subject_scope_compatible(target_subject: Any, candidate_subject: Any) -> bool:
    return bool(_subject_scope_decision(target_subject, candidate_subject)["compatible"])


def _normalize_subject_scope(value: Any) -> str:
    return _normalize_subject_value(value)


def _score_reuse_candidate_details(
    target: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    if _clean_text(target.get("asset_kind")) != _clean_text(candidate.get("asset_kind")):
        return {"score": 0.0, "reject_reason": "asset_kind_mismatch"}
    if _is_background_asset(target):
        return _score_background_reuse_candidate_details(target, candidate)

    subject_filter = _subject_scope_decision(target, candidate)
    hard_reject = _reuse_hard_filter_reject_reason(target, candidate)
    transform_policy = _reuse_transform_policy(target, candidate, reason="aspect_ratio_aligned")
    if hard_reject:
        return {
            "score": 0.0,
            "reject_reason": hard_reject,
            "subject_filter": subject_filter,
            "transform_policy": transform_policy,
            "content_match_score": 0.0,
            "route_score": 0.0,
            "route_hits": [],
            "core_score": 0.0,
            "core_hits": [],
            "missing_core_groups": [],
            "aspect_score": 0.0,
            "raw_score_before_transform_penalty": 0.0,
        }

    target_text = _page_retrieval_text(target)
    candidate_text = _page_retrieval_text(candidate)
    bm25_score, bm25_hits = _bm25_similarity_with_hits(
        _bm25_tokens_from_values([target_text]),
        _bm25_tokens_from_values([candidate_text]),
    )
    substring_score, substring_hits = _background_substring_similarity(
        _background_text_terms(target_text),
        candidate_text,
    )
    score = _weighted_hybrid_signal(
        bm25_score=bm25_score,
        embedding_score=None,
        substring_score=substring_score,
        use_hybrid=True,
    )
    if score <= 0:
        return {
            "score": 0.0,
            "reject_reason": "no_retrieval_text_match",
            "subject_filter": subject_filter,
            "content_match_score": 0.0,
            "route_score": 0.0,
            "route_hits": [],
            "core_score": bm25_score,
            "core_hits": bm25_hits,
            "missing_core_groups": [],
            "aspect_score": 1.0,
            "transform_policy": transform_policy,
            "raw_score_before_transform_penalty": 0.0,
        }
    return {
        "score": max(0.0, min(1.0, score)),
        "reject_reason": "",
        "subject_filter": subject_filter,
        "content_match_score": max(0.0, min(1.0, score)),
        "route_score": 0.0,
        "route_hits": [],
        "core_score": bm25_score,
        "core_hits": bm25_hits,
        "missing_core_groups": [],
        "aspect_score": 1.0,
        "transform_policy": transform_policy,
        "raw_score_before_transform_penalty": max(0.0, min(1.0, score)),
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

    subject_filter = _subject_scope_decision(target, candidate)
    hard_reject = _reuse_hard_filter_reject_reason(target, candidate)
    transform_policy = _copy_transform_policy(target, candidate, reason="aspect_ratio_aligned")
    if hard_reject:
        return {
            "score": 0.0,
            "reject_reason": hard_reject,
            "subject_filter": subject_filter,
            "background_reuse_score": 0.0,
            "background_prompt_match_score": 0.0,
            "background_prompt_bm25_score": 0.0,
            "background_prompt_bm25_hits": [],
            "background_prompt_embedding_score": _optional_score(prompt_embedding_score),
            "background_prompt_substring_score": 0.0,
            "background_prompt_substring_hits": [],
            "background_color_bias_used": False,
            "background_color_bias_match_score": 0.0,
            "background_color_bias_bm25_score": 0.0,
            "background_color_bias_bm25_hits": [],
            "background_color_bias_embedding_score": _optional_score(color_bias_embedding_score),
            "background_color_bias_substring_score": 0.0,
            "background_color_bias_substring_hits": [],
            "content_match_score": 0.0,
            "route_score": 0.0,
            "route_hits": [],
            "core_score": 0.0,
            "core_hits": [],
            "missing_core_groups": [],
            "aspect_score": 0.0,
            "transform_policy": transform_policy,
            "raw_score_before_transform_penalty": 0.0,
        }

    prompt_bm25_score, prompt_bm25_hits = _bm25_similarity_with_hits(
        _background_prompt_query_tokens(target),
        _background_prompt_doc_tokens(candidate),
    )
    local_prompt_substring_score, prompt_substring_hits = _background_substring_similarity(
        _background_prompt_query_terms(target),
        _background_retrieval_text(candidate),
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

    raw_score = (
        BACKGROUND_CONTENT_PROMPT_REUSE_WEIGHT * prompt_match_score
        + BACKGROUND_COLOR_BIAS_REUSE_WEIGHT * color_bias_match_score
        if color_bias_used
        else prompt_match_score
    )
    score = raw_score
    reject_reason = "" if score > 0 else "no_background_prompt_match"
    return {
        "score": max(0.0, min(1.0, score)),
        "reject_reason": reject_reason,
        "subject_filter": subject_filter,
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
        "aspect_score": 0.0,
        "transform_policy": transform_policy,
        "raw_score_before_transform_penalty": max(0.0, min(1.0, raw_score)),
    }


def _background_prompt_query_terms(asset: dict[str, Any]) -> list[str]:
    return _background_text_terms(_background_retrieval_text(asset))


def _background_prompt_query_tokens(asset: dict[str, Any]) -> list[str]:
    return _bm25_tokens_from_values(_background_prompt_query_terms(asset))


def _background_prompt_doc_tokens(asset: dict[str, Any]) -> list[str]:
    return _bm25_tokens_from_values([_background_retrieval_text(asset)])


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


def _route_score_details(target: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    target_page_type = _asset_page_type(target)
    candidate_page_type = _asset_page_type(candidate)
    target_grade_family = _route_grade_family(target)
    candidate_grade_family = _route_grade_family(candidate)

    page_type_match = _exact_route_match(target_page_type, candidate_page_type)
    grade_family_match = _exact_route_match(target_grade_family, candidate_grade_family)
    route_hits: list[dict[str, str]] = []
    if page_type_match:
        route_hits.append({"field": "page_type", "target": target_page_type, "candidate": candidate_page_type})
    if grade_family_match:
        route_hits.append(
            {"field": "grade_family", "target": target_grade_family, "candidate": candidate_grade_family}
        )

    return {
        "route_score": 0.55 * page_type_match + 0.45 * grade_family_match,
        "route_hits": route_hits,
        "route_page_type_match": page_type_match,
        "route_grade_family_match": grade_family_match,
        "target_route_page_type": target_page_type,
        "candidate_route_page_type": candidate_page_type,
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
        "subject_filter": details.get("subject_filter") or {},
        "keyword_score": round(float(details.get("keyword_score") or 0.0), 4),
        "content_match_score": round(float(details.get("content_match_score") or 0.0), 4),
        "route_score": round(float(details.get("route_score") or 0.0), 4),
        "route_hits": details.get("route_hits") or [],
        "route_grade_family_match": round(float(details.get("route_grade_family_match") or 0.0), 4),
        "route_page_type_match": round(float(details.get("route_page_type_match") or 0.0), 4),
        "target_route_grade_family": _clean_text(details.get("target_route_grade_family")),
        "candidate_route_grade_family": _clean_text(details.get("candidate_route_grade_family")),
        "target_route_page_type": _clean_text(details.get("target_route_page_type")),
        "candidate_route_page_type": _clean_text(details.get("candidate_route_page_type")),
        "core_score": round(float(details.get("core_score") or 0.0), 4),
        "core_hits": details.get("core_hits") or [],
        "missing_core_groups": details.get("missing_core_groups") or [],
        "aspect_score": round(float(details.get("aspect_score") or 0.0), 4),
        "transform_policy": details.get("transform_policy") or {},
        "raw_score_before_transform_penalty": round(
            float(details.get("raw_score_before_transform_penalty") or 0.0),
            4,
        ),
        "embedding_score": round(float(details.get("embedding_score") or 0.0), 4),
        "substring_score": round(float(details.get("substring_score") or 0.0), 4),
        "substring_hits": details.get("substring_hits") or [],
        "rrf_score": round(float(details.get("rrf_score") or 0.0), 6),
        "hybrid_score": round(float(details.get("hybrid_score") or 0.0), 4),
        "policy_score": round(float(details.get("policy_score") or 0.0), 4),
        "retrieval_ranks": details.get("retrieval_ranks") or {},
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
        return _background_retrieval_text(asset)

    return _page_retrieval_text(asset)


def _term_in_text(term: str, text: str) -> bool:
    term = _clean_keyword(term).replace(" ", "")
    text = _clean_text(text).replace(" ", "")
    return bool(term and text and term in text)


def _transform_rejects_candidate(candidate: dict[str, Any]) -> bool:
    transform_policy = _dict(candidate.get("transform_policy"))
    if not transform_policy:
        transform_policy = _dict(_dict(candidate.get("score_details")).get("transform_policy"))
    return _clean_text(transform_policy.get("decision")) == "reject"


def _embedding_rescue_decision(
    *,
    embedding_score: float | None,
    transform_rejected: bool,
    floor: float = EMBED_RESCUE_FLOOR,
) -> bool:
    """True iff a policy-score rejected candidate should go to LLM review."""

    if transform_rejected:
        return False
    if embedding_score is None:
        return False
    return float(embedding_score) >= float(floor)


# Page type values that mark a page_image slot as serving an ambience
# purpose rather than precise content. Used by ``_target_is_background_like``
# instead of substring matching against arbitrary slot strings.
_BACKGROUND_LIKE_ROLE_TOKENS = frozenset({
    "background",
    "background_1",
    "background_2",
    "backdrop",
    "ambient",
    "atmosphere",
    "背景",
    "背景图",
    "背景插画",
    "氛围",
})


def _target_is_background_like(target: dict[str, Any]) -> bool:
    """True iff the target's page_type declares it as a backdrop slot.

    Single helper so the "background-like" classification has one place to
    maintain. Matches exact token equality (after casefold), not substring
    containment, to avoid false-positives like "background_decoration".
    """

    value = _clean_text(_dict(target).get("page_type")).casefold()
    return bool(value and value in _BACKGROUND_LIKE_ROLE_TOKENS)


def _reuse_gate_profile(target: dict[str, Any] | None) -> str:
    if target is None:
        return "medium"
    if _clean_text(target.get("asset_kind")) == "background":
        return "background"
    policy = normalize_reuse_policy_fields(_dict(target))
    group = _normalize_binary_reuse_group(_dict(target).get("strict_reuse_group"), default="")
    has_strict_knowledge = group in {
        "C01_irreplaceable_entity_event_action",
    }
    # Background-like page_image slot: declared via page_type by
    # ``_target_is_background_like``. Treated as ambience (loose) rather
    # than precise content for LLM-review purposes. Guarded by absence of
    # strict knowledge so a "background_1 with 写字" slot keeps strict.
    if _target_is_background_like(_dict(target)) and not has_strict_knowledge:
        return "loose"

    level = _clean_text(policy.get("reuse_level")) or "medium"
    if level == "strict":
        return "strict_knowledge"
    return level if level in {"loose", "medium"} else "medium"



def _reuse_gate_thresholds_for_target(target: dict[str, Any] | None) -> dict[str, float]:
    profile = _reuse_gate_profile(target)
    if profile == "background":
        return BACKGROUND_REUSE_GATE_THRESHOLDS
    return PAGE_IMAGE_REUSE_GATE_THRESHOLDS.get(profile, PAGE_IMAGE_REUSE_GATE_THRESHOLDS["medium"])


def _is_text_overlap_review_slot(target: dict[str, Any] | None, candidate: dict[str, Any]) -> bool:
    candidate_asset = _dict(candidate.get("asset")) or _dict(candidate)
    groups = {
        _normalize_binary_reuse_group(_dict(target).get("strict_reuse_group"), default="") if target is not None else "",
        _normalize_binary_reuse_group(candidate_asset.get("strict_reuse_group"), default=""),
    }
    return bool(groups & {
        "C01_irreplaceable_entity_event_action",
    })


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
    override = _dict(policy_result).get("llm_accept_threshold_override")
    if override is not None:
        try:
            return float(override)
        except (TypeError, ValueError):
            pass
    return 0.60


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
    # Threshold inlined from former reuse_policy.STRICT_EMBEDDING_REVIEW_THRESHOLD
    if embedding_score < 0.78:
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
    # Thresholds inlined from former reuse_policy constants
    if target is None:
        return False
    if embedding_score < 0.70:  # STRICT_SEMANTIC_GRAY_REVIEW_THRESHOLD
        return False
    if bm25_score < 0.20 and substring_score < 0.25:  # STRICT_SEMANTIC_GRAY_BM25_THRESHOLD
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
    return any(policy.get("reuse_level") == "strict" for policy in policies)


def _is_medium_embedding_review_candidate(
    target: dict[str, Any] | None,
    candidate: dict[str, Any],
    embedding_score: float,
) -> bool:
    # Threshold inlined from former reuse_policy.MEDIUM_EMBEDDING_REVIEW_THRESHOLD
    if embedding_score < 0.80:
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
    return True


def _reuse_threshold_for_target(target: dict[str, Any], explicit_threshold: float | None) -> float:
    if explicit_threshold is not None:
        try:
            return max(0.0, min(1.0, float(explicit_threshold)))
        except (TypeError, ValueError):
            pass
    if _clean_text(target.get("asset_kind")) == "background":
        return BACKGROUND_REUSE_THRESHOLD
    return policy_reuse_threshold_for_target(target)


def _asset_aspect_ratio_label(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("aspect_ratio")) or _clean_text(asset.get("aspect_bucket"))


def _aspect_ratio_score(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    target_ratio = normalize_aspect_bucket(_asset_aspect_ratio_label(target))
    candidate_ratio = normalize_aspect_bucket(_asset_aspect_ratio_label(candidate))
    if not target_ratio or not candidate_ratio:
        return 0.5
    if target_ratio == candidate_ratio:
        return 1.0
    target_orientation = _ratio_orientation(target_ratio)
    candidate_orientation = _ratio_orientation(candidate_ratio)
    return 0.6 if target_orientation and target_orientation == candidate_orientation else 0.2


def normalize_aspect_bucket(value: Any = "", *, width: float | int | None = None, height: float | int | None = None) -> str:
    if width is not None and height is not None:
        try:
            w = float(width)
            h = float(height)
        except (TypeError, ValueError):
            ratio = 0.0
        else:
            ratio = w / h if w > 0 and h > 0 else 0.0
    else:
        text = _clean_text(value)
        if text in ASPECT_REUSE_BUCKETS:
            return text
        ratio = _ratio_value(text)
    if ratio <= 0:
        return "other"
    best_bucket = "other"
    best_loss = float("inf")
    for bucket, bucket_ratio in _ASPECT_REUSE_BUCKET_VALUES.items():
        loss = 1.0 - min(ratio, bucket_ratio) / max(ratio, bucket_ratio)
        if loss < best_loss:
            best_loss = loss
            best_bucket = bucket
    return best_bucket if best_loss <= _ASPECT_BUCKET_MAX_LOSS else "other"


def _aspect_ratio_value(asset: dict[str, Any]) -> float:
    bucket = normalize_aspect_bucket(_asset_aspect_ratio_label(asset))
    return _ASPECT_REUSE_BUCKET_VALUES.get(bucket, 0.0)


def _aspect_ratio_diff(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    t = _aspect_ratio_value(target)
    c = _aspect_ratio_value(candidate)
    if t <= 0 or c <= 0:
        return 1.0
    return abs(t - c) / t


def _aspect_ratio_loss(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    t = _aspect_ratio_value(target)
    c = _aspect_ratio_value(candidate)
    if t <= 0 or c <= 0:
        return 1.0
    return 1.0 - min(t, c) / max(t, c)


def _aspect_ratio_penalty(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    target_bucket = normalize_aspect_bucket(_asset_aspect_ratio_label(target))
    candidate_bucket = normalize_aspect_bucket(_asset_aspect_ratio_label(candidate))
    if target_bucket == candidate_bucket and target_bucket in _ASPECT_REUSE_BUCKET_VALUES:
        return 0.0
    if (target_bucket, candidate_bucket) in ALLOWED_CROSS_ASPECT_RATIO_REUSE_PAIRS:
        return ASPECT_RATIO_ADJACENT_PENALTY
    return -1.0


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


def _read_existing_asset_index(library_root: Path, index_path: Path) -> tuple[dict[str, Any], Path]:
    split = read_ai_image_split_match_index(library_root)
    if split is not None:
        return split
    return _read_existing_db(index_path), index_path


def _read_match_index_or_build(library_root: Path, db: dict[str, Any]) -> tuple[dict[str, Any], Path]:
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


def _ensure_ai_image_embedding_index(match_index: dict[str, Any], library_root: Path) -> dict[str, Any]:
    model_name = _embedding_model_name()
    if _embedding_disabled():
        return {"enabled": False, "reason": "disabled_by_environment", "model": model_name}
    index_path = library_root / DEFAULT_EMBEDDING_INDEX_FILENAME
    meta_path = library_root / DEFAULT_EMBEDDING_META_FILENAME
    meta = _read_json_if_exists(meta_path)
    assets = match_index.get("assets")
    match_asset_count = len(assets) if isinstance(assets, list) else 0
    expected_count = 0
    expected_background_color_bias_count = 0
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            asset_id = _clean_text(asset.get("asset_id"))
            if asset_id and _asset_embedding_text(asset):
                expected_count += 1
            if asset_id and _is_background_asset(asset) and _background_color_bias(asset):
                expected_background_color_bias_count += 1
    non_embeddable_asset_count = max(0, match_asset_count - expected_count)
    if (
        index_path.exists()
        and meta_path.exists()
        and int(meta.get("schema_version") or 0) == EMBEDDING_INDEX_SCHEMA_VERSION
        and _embedding_model_sidecar_matches(meta.get("model"), model_name)
        and int(meta.get("asset_count") or -1) == expected_count
        and int(meta.get("background_color_bias_asset_count") or 0) == expected_background_color_bias_count
    ):
        return {
            "enabled": True,
            "model": model_name,
            "index_path": _relative_output_path(index_path),
            "meta_path": _relative_output_path(meta_path),
            "asset_count": expected_count,
            "match_asset_count": match_asset_count,
            "non_embeddable_asset_count": non_embeddable_asset_count,
            "background_color_bias_asset_count": int(meta.get("background_color_bias_asset_count") or 0),
            "vector_dim": int(meta.get("vector_dim") or 0),
        }
    PROGRESS_LOGGER.info(
        "AI image embedding index build start: library={}, assets={}, embeddable_assets={}, model={}, reason=missing_or_stale_sidecar",
        library_root,
        match_asset_count,
        expected_count,
        model_name,
    )
    report = write_ai_image_embedding_index(match_index, library_root)
    if report.get("enabled"):
        PROGRESS_LOGGER.info(
            "AI image embedding index build done: library={}, assets={}, model={}, vector_dim={}",
            library_root,
            report.get("asset_count", 0),
            report.get("model", model_name),
            report.get("vector_dim", 0),
        )
    else:
        PROGRESS_LOGGER.warning(
            "AI image embedding index build skipped: library={}, reason={}, model={}",
            library_root,
            report.get("reason") or "unknown",
            report.get("model", model_name),
        )
    return report


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
        "meta": meta,
    }, {
        "enabled": True,
        "model": _clean_text(meta.get("model")) or model_name,
        "index_path": _relative_output_path(index_path),
        "meta_path": _relative_output_path(meta_path),
        "asset_count": len(asset_ids),
        "background_color_bias_asset_count": len(background_color_bias_asset_ids),
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


def _preserve_review_fields(asset: dict[str, Any]) -> dict[str, Any]:
    preserved = {key: deepcopy(asset[key]) for key in _METADATA_PASSTHROUGH_FIELDS if key in asset}
    query = _clean_text(asset.get("query"))
    if query:
        preserved["query"] = query
    for key in ("visual_reuse_group", "visual_reuse_confidence", "visual_reuse_reason"):
        if asset.get(key) not in (None, ""):
            preserved[key] = deepcopy(asset[key])
    return preserved


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


def _normalize_binary_reuse_group(value: Any, *, default: str = _GENERAL_REUSE_GROUP) -> str:
    from edupptx.materials.strict_reuse_classifier import normalize_strict_reuse_group
    return normalize_strict_reuse_group(value, default=default)


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


def normalize_grade_info(*texts: Any) -> dict[str, Any]:
    values = list(texts)
    grade_norm = _normalize_grade_norm_value(values[0] if values else "")
    grade_band = _normalize_grade_band_value(values[1] if len(values) > 1 else "")
    if grade_band == _OTHER_GRADE:
        grade_band = grade_band_from_norm(grade_norm)
    return {"grade_norm": grade_norm, "grade_band": grade_band}


def infer_grade(*texts: Any) -> str:
    """Return a valid LLM-provided grade enum, or ``其他`` when absent/invalid."""

    return _normalize_grade_norm_value(next((text for text in texts if _clean_text(text)), ""))


def infer_grade_band(*texts: Any) -> str:
    """Return a valid LLM-provided grade band enum, or ``其他`` when absent/invalid."""

    return _normalize_grade_band_value(next((text for text in texts if _clean_text(text)), ""))


def grade_band_from_norm(grade_norm: Any) -> str:
    """从 grade_norm 派生学段：一-三年级→低年级；四年级及以上(含初/高中)→高年级；其他→其他。"""
    norm = _normalize_grade_norm_value(grade_norm)
    if norm == _OTHER_GRADE:
        return _OTHER_GRADE
    return _LOW_GRADE_BAND if norm in _LOW_GRADE_NORMS else _HIGH_GRADE_BAND


def infer_subject(*texts: Any) -> str:
    """返回合法学科枚举，缺失/越界时返回其他。"""
    return _normalize_subject_value(next((text for text in texts if _clean_text(text)), ""))


def _is_standard_subject_value(value: Any) -> bool:
    text = _clean_text(value)
    return bool(text) and text in _ALLOWED_SUBJECTS


def _is_standard_grade_norm_value(value: Any) -> bool:
    text = _clean_text(value)
    return bool(text) and text in _ALLOWED_GRADE_NORMS


def _is_standard_grade_band_value(value: Any) -> bool:
    text = _clean_text(value)
    return bool(text) and text in _ALLOWED_GRADE_BANDS


def _meta_grade_subject_fields_are_standard(
    *,
    subject: Any,
    grade: Any,
    grade_band: Any,
) -> bool:
    return (
        _is_standard_subject_value(subject)
        and _is_standard_grade_norm_value(grade)
        and _is_standard_grade_band_value(grade_band)
    )


def _normalize_meta_grade_subject_payload(payload: Any) -> dict[str, str]:
    data: dict[str, Any]
    if isinstance(payload, list):
        data = _dict(payload[0]) if payload else {}
    else:
        data = _dict(payload)
    if isinstance(data.get("meta"), dict):
        data = _dict(data.get("meta"))
    if isinstance(data.get("deck_metadata"), dict):
        data = _dict(data.get("deck_metadata"))

    grade = infer_grade(data.get("grade", data.get("grade_norm")))
    band = infer_grade_band(data.get("grade_band"))
    if band == _OTHER_GRADE:
        band = grade_band_from_norm(grade)
    return {
        "subject": infer_subject(data.get("subject")),
        "grade": grade,
        "grade_band": band,
    }


def _build_meta_grade_subject_normalizer_messages(
    *,
    subject_hint: Any = "",
    grade_hint: Any = "",
    grade_band_hint: Any = "",
    topic: Any = "",
    audience: Any = "",
    requirements: Any = "",
) -> list[dict[str, str]]:
    payload = {
        "subject_hint": _clean_text(subject_hint),
        "grade_hint": _clean_text(grade_hint),
        "grade_band_hint": _clean_text(grade_band_hint),
        "topic": _clean_text(topic),
        "audience": _clean_text(audience),
        "requirements": _clean_text(requirements),
    }
    system = (
        "你只做 PPT/deck 级学科与年级字段归一化。必须只返回严格 JSON 对象，"
        "字段只能包含 subject、grade、grade_band。"
        "subject 只能是：语文、数学、物理、其他。"
        "grade 只能是：一年级、二年级、三年级、四年级、五年级、六年级、七年级、八年级、九年级、高一、高二、高三、其他。"
        "grade_band 只能是：低年级、高年级、其他。"
        "根据输入 hints、topic、audience、requirements 归一化；无法判断时输出其他。"
        "不要输出图片级字段，不要判断具体图片。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def _call_meta_grade_subject_normalizer(
    client: Any,
    *,
    subject_hint: Any = "",
    grade_hint: Any = "",
    grade_band_hint: Any = "",
    topic: Any = "",
    audience: Any = "",
    requirements: Any = "",
) -> dict[str, str]:
    messages = _build_meta_grade_subject_normalizer_messages(
        subject_hint=subject_hint,
        grade_hint=grade_hint,
        grade_band_hint=grade_band_hint,
        topic=topic,
        audience=audience,
        requirements=requirements,
    )
    chat_json = getattr(client, "chat_json", None)
    if callable(chat_json):
        try:
            response = chat_json(messages=messages, temperature=0.0, max_tokens=800, max_retries=1)
        except TypeError:
            response = chat_json(messages, temperature=0.0, max_tokens=800)
    else:
        chat = getattr(client, "chat", None)
        if not callable(chat):
            raise TypeError("meta normalizer client must provide chat_json() or chat()")
        response = _load_json_response(chat(messages=messages, temperature=0.0, max_tokens=800))
    return _normalize_meta_grade_subject_payload(response)


_GRADE_ARABIC_TO_CN = {"1": "一", "2": "二", "3": "三", "4": "四", "5": "五",
                       "6": "六", "7": "七", "8": "八", "9": "九"}
_JUNIOR_ALIASES = (("初一", "七年级"), ("初二", "八年级"), ("初三", "九年级"))
_SENIOR_ALIASES = (("高一", "高一"), ("高二", "高二"), ("高三", "高三"))


def _extract_grade_token(text: Any) -> str:
    """从自由文本里抽出 grade_norm 枚举，抽不到返回其他。"""
    t = _clean_text(text)
    if not t:
        return _OTHER_GRADE
    for alias, norm in _SENIOR_ALIASES:
        if alias in t:
            return norm
    for alias, norm in _JUNIOR_ALIASES:
        if alias in t:
            return norm
    m = re.search(r"([一二三四五六七八九])年级", t)
    if m:
        return f"{m.group(1)}年级"
    m = re.search(r"([1-9])\s*年级", t)
    if m:
        return f"{_GRADE_ARABIC_TO_CN[m.group(1)]}年级"
    return _OTHER_GRADE


def _extract_subject_token(text: Any) -> str:
    """从自由文本里抽出学科枚举，抽不到返回其他。"""
    t = _clean_text(text)
    for subject in ("语文", "数学", "物理"):
        if subject in t:
            return subject
    return _OTHER_SUBJECT


def resolve_meta_grade_subject(
    *,
    llm_subject: Any = "",
    llm_grade: Any = "",
    llm_grade_band: Any = "",
    topic: Any = "",
    audience: Any = "",
    requirements: Any = "",
    normalizer_client: Any | None = None,
) -> dict[str, str]:
    """deck 级判定一次：LLM 优先，缺失则从 topic/audience/requirements 抽，band 最后从 grade 派生。"""
    if _meta_grade_subject_fields_are_standard(
        subject=llm_subject,
        grade=llm_grade,
        grade_band=llm_grade_band,
    ):
        return {
            "subject": _normalize_subject_value(llm_subject),
            "grade": _normalize_grade_norm_value(llm_grade),
            "grade_band": _normalize_grade_band_value(llm_grade_band),
        }

    if normalizer_client is not None:
        try:
            return _call_meta_grade_subject_normalizer(
                normalizer_client,
                subject_hint=llm_subject,
                grade_hint=llm_grade,
                grade_band_hint=llm_grade_band,
                topic=topic,
                audience=audience,
                requirements=requirements,
            )
        except Exception as exc:
            PROGRESS_LOGGER.warning("Deck metadata LLM normalization skipped: {}", str(exc)[:160])

    source_text = " ".join(
        _clean_text(t)
        for t in (llm_subject, llm_grade, llm_grade_band, topic, audience, requirements)
        if _clean_text(t)
    )

    subject = infer_subject(llm_subject)
    if subject == _OTHER_SUBJECT:
        subject = _extract_subject_token(source_text)

    grade = infer_grade(llm_grade)
    if grade == _OTHER_GRADE:
        grade = _extract_grade_token(source_text)

    band = infer_grade_band(llm_grade_band)
    if band == _OTHER_GRADE:
        band = grade_band_from_norm(grade)

    return {"subject": subject, "grade": grade, "grade_band": band}


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
    grade = _clean_text(meta.get("grade"))
    subject = _clean_text(meta.get("subject"))

    return {
        "theme": topic,
        "grade": grade,
        "subject": subject,
        "audience": audience,
    }


def _load_session_reuse_target_keyword_cache(session_dir: Path) -> dict[str, Any]:
    try:
        from edupptx.materials.reuse_query_cache import load_reuse_query_cache

        keyword_cache, _embedding_cache = load_reuse_query_cache(session_dir)
    except Exception:
        return {}
    return keyword_cache if isinstance(keyword_cache, dict) else {}


def _metadata_seed_from_reuse_target(
    target: dict[str, Any],
    target_keyword_cache: dict[str, Any] | None,
) -> dict[str, Any]:
    if not target_keyword_cache:
        return {}
    cached = target_keyword_cache.get(_target_keyword_cache_key(target))
    return cached if isinstance(cached, dict) else {}


def _apply_reuse_target_metadata_seed(asset: dict[str, Any], seed: dict[str, Any]) -> None:
    if not seed:
        return
    fields = (
        _BACKGROUND_REUSE_TARGET_METADATA_FIELDS
        if _is_background_asset(asset)
        else _PAGE_REUSE_TARGET_METADATA_FIELDS
    )
    copied = False
    for key in fields:
        if key not in seed:
            continue
        value = seed.get(key)
        if isinstance(value, bool):
            asset[key] = value
            copied = True
        elif isinstance(value, (list, dict)):
            if value:
                asset[key] = deepcopy(value)
                copied = True
        elif _clean_text(value):
            asset[key] = value
            copied = True
    if copied:
        asset[_REUSE_TARGET_METADATA_SEEDED_FIELD] = True


def _build_background_asset(
    *,
    root: Path,
    session_dir: Path,
    plan_path: Path,
    materials_dir: Path,
    context: dict[str, str],
    plan: dict[str, Any],
    reused_image_paths: set[str] | None = None,
    target_keyword_cache: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    from edupptx.materials.background_generator import build_background_content_prompt

    visual = _dict(plan.get("visual"))
    prompt = _clean_text(build_background_content_prompt(visual))
    image_path = materials_dir / "background.png"
    if not prompt or not image_path.exists():
        return None
    if _is_reused_image_path(image_path, session_dir, reused_image_paths):
        return None

    background_route = _build_background_route(plan)
    target = _build_reuse_target_asset(
        asset_kind="background",
        prompt=prompt,
        prompt_route=None,
        background_route=background_route,
        theme=context.get("theme", ""),
        grade=context.get("grade", ""),
        subject=context.get("subject", ""),
        page_title="",
        page_type="",
        role="background",
        aspect_ratio="16:9",
    )
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
        background_route=background_route,
        metadata_seed=_metadata_seed_from_reuse_target(target, target_keyword_cache),
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
    target_keyword_cache: dict[str, Any] | None = None,
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
        caption = _clean_text(image_need.get("caption"))

        image_path = _find_page_image_path(materials_dir, page_number, role, role_counts[role])
        if image_path is None:
            continue
        if _is_reused_image_path(image_path, session_dir, reused_image_paths):
            continue

        target = _build_reuse_target_asset(
            asset_kind="page_image",
            prompt=prompt,
            prompt_route=prompt_route,
            background_route=None,
            theme=context.get("theme", ""),
            grade=context.get("grade", ""),
            subject=context.get("subject", ""),
            page_title=_clean_text(page.get("title")),
            page_type=_clean_text(page.get("page_type")),
            role=role,
            aspect_ratio=_clean_text(image_need.get("aspect_ratio")),
            caption=caption,
        )

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
            caption=caption,
            metadata_seed=_metadata_seed_from_reuse_target(target, target_keyword_cache),
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
    caption: str = "",
    metadata_seed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rel_image_path = _relative_path(image_path, root)
    route = _clean_prompt_route(prompt_route)
    bg_route = _clean_background_route(background_route)
    content_prompt = _clean_text(prompt)
    asset_caption = _clean_text(caption)
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
    grade_info = normalize_grade_info(context.get("grade"), "")
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
        background_asset = {
            "asset_id": asset_id,
            "asset_kind": "background",
            "image_path": rel_image_path,
            "aspect_ratio": aspect_ratio,
            "theme": context.get("theme", ""),
            "subject_hint": context.get("subject", ""),
            "grade_hint": context.get("grade", ""),
            "subject": _normalize_subject_value(context.get("subject", "")),
            "grade_norm": grade_info["grade_norm"],
            "grade_band": grade_info["grade_band"],
            "topic_refs": topic_refs,
            "content_prompt": content_prompt,
            "background_route": bg_route,
            "normalized_prompt": normalized_prompt,
            "context_summary": context_summary,
            "teaching_intent": teaching_intent,
        }
        _apply_reuse_target_metadata_seed(background_asset, metadata_seed or {})
        return background_asset

    # the field from the VLM step — reuse matching that happens before VLM has
    page_image_asset: dict[str, Any] = {
        "asset_id": asset_id,
        "asset_kind": "page_image",
        "image_path": rel_image_path,
        "aspect_ratio": aspect_ratio,
        "page_type": page_type,
        "theme": context.get("theme", ""),
        "subject_hint": context.get("subject", ""),
        "grade_hint": context.get("grade", ""),
        "subject": _normalize_subject_value(context.get("subject", "")),
        "grade_norm": grade_info["grade_norm"],
        "grade_band": grade_info["grade_band"],
        "topic_refs": topic_refs,
        "caption": asset_caption or content_prompt,
        "context_summary": context_summary,
        "teaching_intent": teaching_intent,
        "duplicate_asset_ids": [],
    }
    _apply_reuse_target_metadata_seed(page_image_asset, metadata_seed or {})
    return page_image_asset


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
