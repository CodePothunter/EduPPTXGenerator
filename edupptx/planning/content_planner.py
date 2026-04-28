"""Planning pipeline helpers for stage-1 outlining and stage-2 refinement."""

from __future__ import annotations

import copy
import json
import os
import re
import time
from pathlib import Path

import json_repair
from loguru import logger

from edupptx.config import Config
from edupptx.design.template_router import TemplateManifest, build_page_variant_briefs
from edupptx.llm_client import create_llm_client
from edupptx.models import InputContext, PlanningDraft
from edupptx.planning.prompts import (
    build_outline_planning_system_prompt,
    build_outline_planning_user_prompt,
    build_refinement_planning_system_prompt,
    build_refinement_planning_user_prompt,
)


def generate_planning_outline(
    ctx: InputContext,
    config: Config,
) -> PlanningDraft:
    """Generate the stage-1 page outline without template constraints."""

    client = create_llm_client(config)
    system = build_outline_planning_system_prompt()
    user = build_outline_planning_user_prompt(
        topic=ctx.topic,
        requirements=ctx.requirements,
        source_text=ctx.source_text,
        research_summary=ctx.research_summary,
    )

    logger.info("Generating stage-1 planning outline for topic={}", ctx.topic)
    response = client.chat(messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])

    draft = _parse_draft(response, ensure_reveals=False)
    logger.info("Stage-1 outline: {} pages", len(draft.pages))
    return draft


def refine_planning_draft(
    outline: PlanningDraft,
    manifest: TemplateManifest,
    config: Config,
) -> PlanningDraft:
    """Generate the stage-2 refined draft using matched template references."""

    client = create_llm_client(config)
    system = build_refinement_planning_system_prompt()
    outline_json = json.dumps(outline.model_dump(), ensure_ascii=False, indent=2)
    template_brief = build_page_variant_briefs(outline, manifest)
    user = build_refinement_planning_user_prompt(
        outline_json=outline_json,
        template_refinement_brief=template_brief,
        template_family=manifest.template_family,
    )

    logger.info("Refining planning draft with template family={}", manifest.template_family)
    response = client.chat(messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])

    draft = _parse_draft(response, ensure_reveals=False)
    logger.info("Stage-2 refined draft: {} pages before reveal expansion", len(draft.pages))
    return draft


def finalize_reveal_pages(draft: PlanningDraft) -> PlanningDraft:
    """Insert reveal pages after refinement while preserving pseudo-animation rules."""

    data = draft.model_dump()
    _ensure_reveal_pairs(data)
    finalized = PlanningDraft.model_validate(data)
    logger.info("Reveal expansion complete: {} pages", len(finalized.pages))
    return finalized


def generate_planning_draft(
    ctx: InputContext,
    config: Config,
    template_brief: str = "",
) -> PlanningDraft:
    """Legacy wrapper kept for compatibility."""

    _ = template_brief
    return generate_planning_outline(ctx, config)


def _parse_draft(response: str, ensure_reveals: bool = False) -> PlanningDraft:
    """Extract JSON from LLM response and parse into PlanningDraft."""

    json_match = re.search(r"```(?:json)?\s*\n?(.*?)```", response, re.DOTALL)
    raw = json_match.group(1).strip() if json_match else response.strip()

    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]

    data = _load_json_with_repair(raw)

    _normalize_draft_dict(data)
    if ensure_reveals:
        _ensure_reveal_pairs(data)

    return PlanningDraft.model_validate(data)


def _load_json_with_repair(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse failed, attempting repair: {}", exc)

    cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Some LLMs emit literal line breaks inside JSON strings. Keep this
    # fallback narrow: only after normal parsing and trailing-comma repair fail.
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        pass

    # Final fallback: aggressive LLM-aware repair (handles unescaped quotes,
    # missing commas, truncated output, Python literals, comments, etc.).
    try:
        repaired = json_repair.loads(raw)
        if isinstance(repaired, dict):
            logger.warning("JSON parsed via json-repair fallback")
            return repaired
    except Exception as exc:
        logger.error("json-repair also failed: {}", exc)

    # Persist raw response for post-mortem before raising.
    _dump_failed_response(raw)
    raise json.JSONDecodeError("Could not parse LLM JSON after all repair attempts", raw, 0)


def _dump_failed_response(raw: str) -> None:
    """Save raw LLM response on parse failure so an agent can debug."""
    debug_dir = Path(os.environ.get("EDUPPTX_DEBUG_DIR", "output/_debug"))
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / f"llm_parse_fail_{int(time.time())}.txt"
        path.write_text(raw, encoding="utf-8")
        logger.error("Failed LLM response saved to {}", path)
    except Exception as exc:
        logger.error("Could not save failed response: {}", exc)


def _normalize_draft_dict(data: dict) -> None:
    _VALID_PAGE_TYPES = {
        "cover", "toc", "section", "content", "data", "case", "closing",
        "timeline", "exercise", "summary", "quiz", "formula", "experiment",
    }
    _VALID_LAYOUT_HINTS = {
        "center_hero", "vertical_list", "bento_2col_equal", "bento_2col_asymmetric",
        "bento_3col", "hero_top_cards_bottom", "cards_top_hero_bottom",
        "hero_with_microcards",
        "mixed_grid", "full_image", "timeline", "comparison", "relation",
    }
    _VALID_IMAGE_ROLES = {"hero", "illustration", "icon", "background"}
    _LAYOUT_ONLY_PAGE_TYPES = {"comparison", "relation"}

    def _normalize_image_role(value: object) -> str:
        text = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
        if not text:
            return "illustration"
        if text in _VALID_IMAGE_ROLES:
            return text
        if text in {"hero_banner", "banner", "cover", "cover_image", "main_visual", "main_image"}:
            return "hero"
        if "background" in text or text == "bg":
            return "background"
        if "icon" in text:
            return "icon"
        if "hero" in text or "banner" in text:
            return "hero"
        return "illustration"

    for page in data.get("pages", []):
        page_type = str(page.get("page_type") or "").strip()
        if page_type in _LAYOUT_ONLY_PAGE_TYPES:
            layout_hint = str(page.get("layout_hint") or "").strip()
            if not layout_hint or layout_hint == "mixed_grid" or layout_hint not in _VALID_LAYOUT_HINTS:
                page["layout_hint"] = page_type
            page["page_type"] = "content"

        if page.get("page_type") not in _VALID_PAGE_TYPES:
            logger.warning("Unknown page_type '{}' → 'content'", page.get("page_type"))
            page["page_type"] = "content"
        if page.get("layout_hint") not in _VALID_LAYOUT_HINTS:
            logger.warning("Unknown layout_hint '{}' → 'mixed_grid'", page.get("layout_hint"))
            page["layout_hint"] = "mixed_grid"

        cp = page.get("content_points")
        if isinstance(cp, dict):
            flat: list[str] = []
            for key, value in cp.items():
                if isinstance(value, list):
                    flat.extend(f"{key}: {item}" if len(cp) > 1 else str(item) for item in value)
                else:
                    flat.append(f"{key}: {value}")
            page["content_points"] = flat

        material_needs = page.get("material_needs")
        if isinstance(material_needs, dict):
            images = material_needs.get("images")
            if isinstance(images, list):
                for image in images:
                    if isinstance(image, dict):
                        image["role"] = _normalize_image_role(image.get("role"))

    meta = data.get("meta")
    if isinstance(meta, dict) and isinstance(data.get("pages"), list):
        meta["total_pages"] = len(data["pages"])


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


def _explicit_reveal_mode(page: dict) -> str | None:
    reveal_mode = page.get("reveal_mode")
    if reveal_mode in {"show_answer", "highlight_correct_option"} and page.get("reveal_from_page") is None:
        return str(reveal_mode)
    return None


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

        reveal_mode: str | None = _explicit_reveal_mode(page_copy)
        if reveal_mode is None:
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
