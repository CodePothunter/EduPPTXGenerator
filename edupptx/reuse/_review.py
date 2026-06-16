"""复用层 LLM 灰区审阅：构造 review prompt(含评分规则参考) + 调 LLM 打分 + 归一化 + accept 阈值。函数体逐字一致。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger as PROGRESS_LOGGER

from edupptx.materials.reuse_policy import normalize_reuse_policy_fields

# materials/Reference/ 评分规则参考（随 _review 迁出；路径从 reuse/ 上溯到 materials/，
# 与原 ai_image_asset_db.py 中 __file__.parent/"Reference" 指向同一文件）。
REUSE_REVIEW_SCORE_RULES_REFERENCE = (
    Path(__file__).resolve().parent.parent / "materials" / "Reference" / "ai_image_reuse_review_score_rules.md"
)

from edupptx.reuse._util import (
    _clean_text,
    _dict,
)
from edupptx.reuse._constants import (
    REUSE_REVIEW_ACCEPT_SCORE_THRESHOLD,
)
from edupptx.reuse._assets import (
    _as_string_list,
    _asset_caption,
    _asset_content_prompt,
    _asset_generation_prompt,
    _asset_page_type,
    _asset_query,
    _asset_style_prompt,
    _clean_prompt_route,
    _optional_bool,
    _topic_refs_for_asset,
    _unit_ref_for_asset,
)
from edupptx.reuse._normalize import (
    _load_json_response,
    _normalize_grade_band_value,
)
from edupptx.reuse._scoring import (
    _clean_background_route,
)
from edupptx.reuse._embedding import (
    _relative_output_path,
)
from edupptx.reuse._retrieve import (
    _debug_score_details,
)


def _log_snippet(value: Any, limit: int = 120) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _clamp_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


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


def _load_reuse_review_score_rules_reference() -> str:
    try:
        raw_text = REUSE_REVIEW_SCORE_RULES_REFERENCE.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"missing AI image reuse review score rules reference: {REUSE_REVIEW_SCORE_RULES_REFERENCE}") from exc
    text = re.sub(r"<!--.*?-->", "", raw_text, flags=re.S).strip()
    if not text:
        raise RuntimeError(f"empty AI image reuse review score rules reference: {REUSE_REVIEW_SCORE_RULES_REFERENCE}")
    return text


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
