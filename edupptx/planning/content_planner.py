"""Phase 1: 策划稿生成 — 1 次 LLM 调用输出 PlanningDraft。"""

from __future__ import annotations

import copy
import json
import re

from loguru import logger

from edupptx.config import Config
from edupptx.llm_client import create_llm_client
from edupptx.models import InputContext, PlanningDraft
from edupptx.planning.prompts import (
    build_planning_system_prompt,
    build_planning_user_prompt,
)


def generate_planning_draft(
    ctx: InputContext,
    config: Config,
    template_brief: str = "",
) -> PlanningDraft:
    """Generate a planning draft from input context via a single LLM call."""
    client = create_llm_client(config)

    system = build_planning_system_prompt()
    user = build_planning_user_prompt(
        topic=ctx.topic,
        requirements=ctx.requirements,
        source_text=ctx.source_text,
        research_summary=ctx.research_summary,
        template_brief=template_brief,
    )

    logger.info("Generating planning draft for topic={}", ctx.topic)
    response = client.chat(messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])

    draft = _parse_draft(response, ctx)
    logger.info("Planning draft: {} pages", len(draft.pages))
    return draft


def _parse_draft(response: str, ctx: InputContext) -> PlanningDraft:
    """Extract JSON from LLM response and parse into PlanningDraft."""
    # Try to extract JSON from markdown code block
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)```", response, re.DOTALL)
    raw = json_match.group(1).strip() if json_match else response.strip()

    # Clean common LLM artifacts
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("JSON parse failed, attempting repair: {}", e)
        # Try fixing common issues: trailing commas
        cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
        data = json.loads(cleaned)

    # Coerce unknown page_type/layout_hint to safe defaults
    _VALID_PAGE_TYPES = {"cover", "toc", "section", "content", "data", "case", "closing",
                         "timeline", "comparison", "exercise", "summary", "relation",
                         "quiz", "formula", "experiment"}
    _VALID_LAYOUT_HINTS = {
        "center_hero", "vertical_list", "bento_2col_equal", "bento_2col_asymmetric",
        "bento_3col", "hero_top_cards_bottom", "cards_top_hero_bottom",
        "mixed_grid", "full_image", "timeline", "comparison", "relation",
    }
    for page in data.get("pages", []):
        if page.get("page_type") not in _VALID_PAGE_TYPES:
            logger.warning("Unknown page_type '{}' → 'content'", page.get("page_type"))
            page["page_type"] = "content"
        if page.get("layout_hint") not in _VALID_LAYOUT_HINTS:
            logger.warning("Unknown layout_hint '{}' → 'mixed_grid'", page.get("layout_hint"))
            page["layout_hint"] = "mixed_grid"
        # LLM sometimes outputs content_points as a dict instead of a list
        cp = page.get("content_points")
        if isinstance(cp, dict):
            # Flatten dict values into a list of strings
            flat: list = []
            for k, v in cp.items():
                if isinstance(v, list):
                    flat.extend(f"{k}: {item}" if len(cp) > 1 else str(item) for item in v)
                else:
                    flat.append(f"{k}: {v}")
            page["content_points"] = flat

    _ensure_reveal_pairs(data)

    return PlanningDraft.model_validate(data)


_FILL_BLANK_PATTERN = re.compile(
    r"（\s*）|\(\s*\)|_{2,}|＿{2,}|﹍{2,}|—{2,}|填空|填一填|补全|根据.*填|写出答案"
)
_ANSWER_CUE_PATTERN = re.compile(
    r"答案揭晓区|答案区|预留答案揭晓区|预留答案区|写完对答案|对答案|揭晓答案|写完我们就对答案|稍后揭晓|答案待揭晓"
)
_QUIZ_CHOICE_PATTERN = re.compile(r"\bA[.．、:]|\bB[.．、:]|\bC[.．、:]|\bD[.．、:]|判断题|选择题")


def _join_page_text(page: dict) -> str:
    parts: list[str] = []
    for key in ("title", "subtitle", "design_notes", "notes"):
        value = page.get(key)
        if value:
            parts.append(str(value))
    for item in page.get("content_points") or []:
        parts.append(str(item))
    return "\n".join(parts)


def _needs_show_answer_reveal(page: dict) -> bool:
    if page.get("page_type") != "exercise":
        return False
    text = _join_page_text(page)
    return bool(_FILL_BLANK_PATTERN.search(text) and _ANSWER_CUE_PATTERN.search(text))


def _needs_highlight_reveal(page: dict) -> bool:
    if page.get("page_type") != "quiz":
        return False
    text = _join_page_text(page)
    return bool(_QUIZ_CHOICE_PATTERN.search(text) and _ANSWER_CUE_PATTERN.search(text))


def _build_reveal_page(source_page: dict, reveal_mode: str) -> dict:
    reveal_page = copy.deepcopy(source_page)
    source_notes = str(source_page.get("notes", "") or "")
    reveal_page["reveal_from_page"] = source_page.get("page_number")
    reveal_page["reveal_mode"] = reveal_mode
    if reveal_mode == "show_answer":
        reveal_page["design_notes"] = (
            "保留原布局，只在原空位或答案区补充答案，不新增图片、不新增新卡片、不改变原元素位置"
        )
        reveal_page["notes"] = (
            "这一页用于答案揭晓。保持上一页版式不变，只在原空位或答案区补出正确答案，"
            f"并进行简短讲解。原题提示：{source_notes}"
        ).strip()
    else:
        reveal_page["design_notes"] = (
            "保留原布局，只高亮正确选项并添加勾选标记，不改变原卡片位置和换行"
        )
        reveal_page["notes"] = (
            "这一页用于答案揭晓。保持上一页版式不变，只高亮正确选项并做简短讲解，"
            f"原题提示：{source_notes}"
        ).strip()
    return reveal_page


def _ensure_reveal_pairs(data: dict) -> None:
    pages = data.get("pages")
    if not isinstance(pages, list) or not pages:
        return

    entries: list[dict] = []
    for index, page in enumerate(pages):
        old_page_number = int(page.get("page_number", index + 1))
        page_copy = copy.deepcopy(page)
        entries.append({
            "kind": "original",
            "old_page_number": old_page_number,
            "page": page_copy,
        })

        if page_copy.get("reveal_from_page") is not None:
            continue

        next_page = pages[index + 1] if index + 1 < len(pages) else None
        if isinstance(next_page, dict) and next_page.get("reveal_from_page") == old_page_number:
            continue

        reveal_mode: str | None = None
        if _needs_show_answer_reveal(page_copy):
            reveal_mode = "show_answer"
        elif _needs_highlight_reveal(page_copy):
            reveal_mode = "highlight_correct_option"

        if reveal_mode is None:
            continue

        entries.append({
            "kind": "inserted_reveal",
            "source_old_page_number": old_page_number,
            "page": _build_reveal_page(page_copy, reveal_mode),
        })

    old_to_new: dict[int, int] = {}
    next_page_number = 1
    for entry in entries:
        if entry["kind"] == "original":
            old_to_new[entry["old_page_number"]] = next_page_number
        next_page_number += 1

    normalized_pages: list[dict] = []
    for new_page_number, entry in enumerate(entries, start=1):
        page = entry["page"]
        page["page_number"] = new_page_number
        if entry["kind"] == "inserted_reveal":
            page["reveal_from_page"] = old_to_new[entry["source_old_page_number"]]
        elif page.get("reveal_from_page") is not None:
            page["reveal_from_page"] = old_to_new.get(page["reveal_from_page"], page["reveal_from_page"])
        normalized_pages.append(page)

    data["pages"] = normalized_pages
    meta = data.get("meta")
    if isinstance(meta, dict):
        meta["total_pages"] = len(normalized_pages)
