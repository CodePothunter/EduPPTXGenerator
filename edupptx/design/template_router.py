"""Template-family routing for subject-specific SVG references."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from edupptx.models import PlanningDraft


_PAGE_TEMPLATES_DIR = Path(__file__).parent / "page_templates"
_DEFAULT_FAMILY = "basic"
_FAMILY_ORDER = ("语文", "数学", "物理", _DEFAULT_FAMILY)

_TEMPLATE_FAMILY_KEYWORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "语文": {
        "strong": (
            "语文", "拼音", "生字", "识字", "写字", "课文", "朗读", "古诗",
            "作文", "阅读理解", "汉字", "多音字", "轻声", "声调",
        ),
        "weak": (
            "词语", "句子", "段落", "部首", "偏旁", "近义词", "反义词",
            "修辞", "文言文", "背诵", "字词", "注音",
        ),
    },
    "数学": {
        "strong": (
            "数学", "口算", "应用题", "方程", "分数", "小数", "几何",
            "函数", "统计", "概率",
        ),
        "weak": (
            "加法", "减法", "乘法", "除法", "加减乘除", "比例", "百分数",
            "面积", "周长", "体积", "图形", "角", "线段", "坐标", "数轴",
        ),
    },
    "物理": {
        "strong": (
            "物理", "力学", "电学", "光学", "热学", "声学", "电路", "实验",
            "电流", "电压", "电阻",
        ),
        "weak": (
            "运动", "速度", "加速度", "受力", "摩擦力", "压强", "浮力",
            "串联", "并联", "功率", "能量", "做功", "热量", "密度", "质量",
            "探究", "机械", "透镜",
        ),
    },
}


def _available_families() -> list[str]:
    families: list[str] = []
    for family in _FAMILY_ORDER:
        if (_PAGE_TEMPLATES_DIR / family).is_dir():
            families.append(family)
    if _DEFAULT_FAMILY not in families:
        families.append(_DEFAULT_FAMILY)
    return families


def _append_if_text(parts: list[str], value: Any) -> None:
    if value is None:
        return
    text = str(value).strip()
    if text:
        parts.append(text)


def _flatten_content_points(value: Any) -> list[str]:
    flattened: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            flattened.extend(_flatten_content_points(item))
        return flattened
    if isinstance(value, (list, tuple)):
        for item in value:
            flattened.extend(_flatten_content_points(item))
        return flattened
    _append_if_text(flattened, value)
    return flattened


def collect_template_routing_text(draft: "PlanningDraft") -> str:
    """Collect deck-level text used for keyword routing."""
    parts: list[str] = []
    meta = getattr(draft, "meta", None)
    if meta is not None:
        _append_if_text(parts, getattr(meta, "topic", ""))
        _append_if_text(parts, getattr(meta, "style_direction", ""))
        _append_if_text(parts, getattr(meta, "audience", ""))
        _append_if_text(parts, getattr(meta, "purpose", ""))
    _append_if_text(parts, getattr(draft, "research_context", ""))

    for page in getattr(draft, "pages", []) or []:
        _append_if_text(parts, getattr(page, "page_type", ""))
        _append_if_text(parts, getattr(page, "layout_hint", ""))
        _append_if_text(parts, getattr(page, "title", ""))
        _append_if_text(parts, getattr(page, "subtitle", ""))
        _append_if_text(parts, getattr(page, "design_notes", ""))
        for point in _flatten_content_points(getattr(page, "content_points", [])):
            _append_if_text(parts, point)

    return "\n".join(parts)


def score_template_families(text: str) -> dict[str, int]:
    """Return keyword-match scores per template family."""
    normalized = text.lower()
    scores: dict[str, int] = {}
    for family, groups in _TEMPLATE_FAMILY_KEYWORDS.items():
        score = 0
        for keyword in groups["strong"]:
            if keyword.lower() in normalized:
                score += 3
        for keyword in groups["weak"]:
            if keyword.lower() in normalized:
                score += 1
        if family.lower() in normalized:
            score += 5
        scores[family] = score
    return scores


def choose_template_family_by_keywords(text: str) -> str | None:
    """Choose a family when keyword evidence is clear enough."""
    scores = score_template_families(text)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] <= 0:
        return None
    top_family, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0
    if top_score >= 3 and top_score - second_score >= 2:
        return top_family
    return None


def _summarize_family_templates(family: str) -> str:
    family_dir = _PAGE_TEMPLATES_DIR / family
    if not family_dir.is_dir():
        return f"- {family}: <missing>"
    names = sorted(path.name for path in family_dir.glob("*.svg"))
    if len(names) > 8:
        names = names[:8] + ["..."]
    return f"- {family}: {', '.join(names)}"


def choose_template_family_with_llm(client: Any, draft: "PlanningDraft") -> str:
    """Use a small LLM decision only when keyword routing is ambiguous."""
    if client is None:
        return _DEFAULT_FAMILY

    families = _available_families()
    family_summary = "\n".join(_summarize_family_templates(family) for family in families)
    routing_text = collect_template_routing_text(draft)
    messages = [
        {
            "role": "system",
            "content": (
                "You choose one template family for an educational PPT deck.\n"
                "Return exactly one family name from the allowed list.\n"
                "Do not explain."
            ),
        },
        {
            "role": "user",
            "content": (
                "Available template families:\n"
                f"{family_summary}\n\n"
                f"Allowed outputs: {', '.join(families)}\n\n"
                "Deck content:\n"
                f"{routing_text}\n"
            ),
        },
    ]
    try:
        response = client.chat(messages=messages, temperature=0.0, max_tokens=32)
    except Exception as exc:
        logger.warning("Template family LLM routing failed: {}", str(exc)[:120])
        return _DEFAULT_FAMILY

    response_text = str(response).strip()
    for family in families:
        if family in response_text:
            return family
    return _DEFAULT_FAMILY


def resolve_template_family(draft: "PlanningDraft", client: Any = None) -> str:
    """Resolve a single template family for the whole deck."""
    routing_text = collect_template_routing_text(draft)
    chosen = choose_template_family_by_keywords(routing_text)
    if chosen:
        logger.info("Template family resolved by keywords: {}", chosen)
        return chosen
    chosen = choose_template_family_with_llm(client, draft)
    logger.info("Template family resolved by LLM fallback: {}", chosen)
    return chosen or _DEFAULT_FAMILY
