"""复用层共享常量：文件名/schema 版本/检索权重/阈值/分组名/正则等纯值。

零对复用层其余模块的依赖（仅 stdlib），是 store/build/retrieve 等结构层的共同
地基，先抽它避免子模块与 ai_image_asset_db 间的循环 import。常量值与原
ai_image_asset_db.py 中的定义逐字一致、保持原定义顺序（保留常量间引用关系）。
保留在 ai_image_asset_db.py 的：2 个 materials/Reference 相对路径、LOGGER（__name__）、
6 个 *_LOCK/*_CACHE 可变状态（随其消费函数后续迁移）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SCHEMA_VERSION = 1

KEYWORD_SCHEMA_VERSION = 14

DEFAULT_DB_FILENAME = "ai_image_asset_db.json"

DEFAULT_MATCH_INDEX_FILENAME = "ai_image_match_index.json"

DEFAULT_EMBEDDING_INDEX_FILENAME = "ai_image_embedding_index.npz"

DEFAULT_EMBEDDING_META_FILENAME = "ai_image_embedding_meta.json"

DEFAULT_EMBEDDING_MISSING_CAPTION_REVIEW_FILENAME = "ai_image_embedding_missing_caption_review.json"

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

MAX_LLM_REVIEWS_PER_QUERY = 5

MAX_LLM_REVIEW_WORKERS = 15

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

EMBEDDING_KEYWORD_GAP_REJECT_THRESHOLD = 0.40

R5_NEAR_MISS_EPSILON = 0.05

R5_MAX_VLM_CALLS_PER_SESSION = 3

R5_SESSION_VLM_COUNT_KEY = "near_miss_vlm_calls_used"

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_OUTPUT_PATH_MARKERS = (
    "output",
    "materials_library",
    "materials_library_ppt",
    "report",
)

KEYWORD_LED_LLM_REVIEW_MIN_KEYWORD = 0.28

KEYWORD_LED_LLM_REVIEW_MIN_EMBEDDING = 0.60

EMBEDDING_LED_LLM_REVIEW_MIN_KEYWORD = 0.10

EMBEDDING_LED_LLM_REVIEW_MIN_SUBSTRING = 0.10

TEXT_OVERLAP_REVIEW_THRESHOLD = 0.15

TEXT_OVERLAP_EMBEDDING_THRESHOLD = 0.78

CONTENT_PROMPT_REUSE_WEIGHT = 0.85

ASPECT_REUSE_WEIGHT = 0.05

LIGHT_CONTEXT_REUSE_WEIGHT = 0.05

BACKGROUND_CONTENT_PROMPT_REUSE_WEIGHT = 0.85

BACKGROUND_COLOR_BIAS_REUSE_WEIGHT = 0.15

VISUAL_GENERIC_REUSE_THRESHOLD = 0.28

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

PREWARM_KEYWORD_BATCH_SIZE = DEFAULT_KEYWORD_BATCH_SIZE

PREWARM_KEYWORD_MAX_WORKERS = DEFAULT_REUSE_MAX_WORKERS

_PROMPT_ROUTE_LIST_FIELDS = (
    "profile_ids",
    "profile_prompt_terms",
    "role_prompt_terms",
    "page_type_prompt_terms",
    "aspect_ratio_prompt_terms",
    "quality_terms",
    "negative_terms",
)

_EMBEDDING_QUERY_FAILURE_WARNED = False

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

_GRADE_ARABIC_TO_CN = {"1": "一", "2": "二", "3": "三", "4": "四", "5": "五",
                       "6": "六", "7": "七", "8": "八", "9": "九"}

_JUNIOR_ALIASES = (("初一", "七年级"), ("初二", "八年级"), ("初三", "九年级"))

_SENIOR_ALIASES = (("高一", "高一"), ("高二", "高二"), ("高三", "高三"))
