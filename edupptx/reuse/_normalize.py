"""复用层 grade/subject/group 归一化 helper：把自由文本的年级/学段/学科/分组
规范成受控枚举（含别名、阿拉伯数字→中文、deck 级 meta 推断 resolve_meta_grade_subject）。

仅依赖 _util/_assets/_constants/stdlib。被 content_planner（deck 级学科年级归一）与
检索/路由共用。函数体与原 ai_image_asset_db.py 中逐字一致。
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger as PROGRESS_LOGGER

from edupptx.reuse._util import _clean_text, _dict
from edupptx.reuse._assets import _normalize_subject_value
from edupptx.reuse._constants import (
    _ALLOWED_GRADE_BANDS,
    _ALLOWED_GRADE_NORMS,
    _ALLOWED_SUBJECTS,
    _GENERAL_REUSE_GROUP,
    _GRADE_ARABIC_TO_CN,
    _HIGH_GRADE_BAND,
    _JUNIOR_ALIASES,
    _LOW_GRADE_BAND,
    _LOW_GRADE_NORMS,
    _OTHER_GRADE,
    _OTHER_SUBJECT,
    _SENIOR_ALIASES,
)

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


def _normalize_grade_norm_value(value: Any) -> str:
    text = _clean_text(value)
    return text if text in _ALLOWED_GRADE_NORMS else _OTHER_GRADE


def _normalize_grade_band_value(value: Any) -> str:
    text = _clean_text(value)
    return text if text in _ALLOWED_GRADE_BANDS else _OTHER_GRADE


def _normalize_subject_scope(value: Any) -> str:
    return _normalize_subject_value(value)


def _normalize_binary_reuse_group(value: Any, *, default: str = _GENERAL_REUSE_GROUP) -> str:
    from edupptx.materials.strict_reuse_classifier import normalize_strict_reuse_group
    return normalize_strict_reuse_group(value, default=default)


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
