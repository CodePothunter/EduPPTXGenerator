"""复用层检索打分：policy_score 单一裁决分（三路加权）、候选/背景打分明细、BM25/aspect/transform 等评分原语。仅依赖 _util/_constants/_assets/_normalize/stdlib。函数体与原 ai_image_asset_db.py 逐字一致。"""

from __future__ import annotations

import math
import os
import re
from typing import Any

from edupptx.reuse._util import (
    _clean_keyword,
    _clean_text,
    _dedupe_terms,
    _dict,
)
from edupptx.reuse._constants import (
    ALLOWED_CROSS_ASPECT_RATIO_REUSE_PAIRS,
    ASPECT_RATIO_ADJACENT_PENALTY,
    ASPECT_REUSE_BUCKETS,
    BACKGROUND_COLOR_BIAS_REUSE_WEIGHT,
    BACKGROUND_CONTENT_PROMPT_REUSE_WEIGHT,
    HYBRID_BM25_WEIGHT,
    HYBRID_EMBEDDING_WEIGHT,
    HYBRID_SUBSTRING_WEIGHT,
    _ASPECT_BUCKET_MAX_LOSS,
    _ASPECT_REUSE_BUCKET_VALUES,
    _BACKGROUND_ROUTE_FIELDS,
    _CONTENT_REUSE_GROUP,
    _KNOWN_SUBJECTS,
    _OTHER_SUBJECT,
)
from edupptx.reuse._assets import (
    _as_string_list,
    _asset_aspect_ratio_label,
    _asset_general_value,
    _asset_subject_value,
    _background_retrieval_text,
    _is_background_asset,
    _page_retrieval_text,
)
from edupptx.reuse._normalize import (
    _normalize_binary_reuse_group,
)


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
    if _embedding_disabled():
        # M-1: 显式关闭 embedding 时按可用信号权重重新归一化。否则 embedding 权重
        # （0.55）计 0，满分只剩 0.45 < T_DIRECT，decide_reuse 几乎全拒，使
        # EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS 这个文档化开关形同失效。
        total_weight = HYBRID_BM25_WEIGHT + HYBRID_SUBSTRING_WEIGHT
        component_score = (
            HYBRID_BM25_WEIGHT * keyword_score + HYBRID_SUBSTRING_WEIGHT * substring_score
        ) / max(total_weight, 1e-9)
    else:
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


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _embedding_disabled() -> bool:
    value = _clean_text(os.environ.get("EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS")).lower()
    return value in {"1", "true", "yes", "on"}


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


def _background_color_bias(asset: dict[str, Any]) -> str:
    route = _clean_background_route(asset.get("background_route"))
    return _clean_text(route.get("background_color_bias"))


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

    penalty = _aspect_ratio_penalty(target, candidate)
    if penalty < 0:
        return "aspect_ratio_too_far"

    if not subject_decision["compatible"]:
        return "subject_mismatch"
    return ""


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


def _term_in_text(term: str, text: str) -> bool:
    term = _clean_keyword(term).replace(" ", "")
    text = _clean_text(text).replace(" ", "")
    return bool(term and text and term in text)


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
