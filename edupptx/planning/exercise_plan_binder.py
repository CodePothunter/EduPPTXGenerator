"""Optional exercise-bank binding for planning drafts.

This module is intentionally isolated so the exercise-bank feature can be
removed without touching the rest of the planning pipeline beyond small calls.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from edupptx.models import ImageNeed, PlanningDraft, match_aspect_ratio, normalize_image_aspect_ratio


_CATEGORY_ALIASES = {
    "A": "A",
    "A - 复习巩固": "A",
    "复习巩固": "A",
    "巩固": "A",
    "B": "B",
    "B - 综合运用": "B",
    "综合运用": "B",
    "综合": "B",
    "C": "C",
    "C - 扩展探索": "C",
    "扩展探索": "C",
    "拓展探索": "C",
    "拓展": "C",
}
_CATEGORY_LABELS = {
    "A": "A - 复习巩固",
    "B": "B - 综合运用",
    "C": "C - 扩展探索",
}


@dataclass(frozen=True)
class ExerciseImageAsset:
    image_id: str
    path: Path
    role: str = "question_diagram"
    query: str = ""
    aspect_ratio: str = "16:9"


@dataclass(frozen=True)
class ExerciseRecord:
    exercise_id: str
    category: str
    stem: str
    subject: str = ""
    grade: str = ""
    grade_band: str = ""
    lesson_id: str = ""
    lesson_name: str = ""
    options: tuple[str, ...] = ()
    answer: str = ""
    explanation: str = ""
    knowledge_points: tuple[str, ...] = ()
    difficulty: str = ""
    image_assets: tuple[ExerciseImageAsset, ...] = ()


@dataclass(frozen=True)
class ExerciseBindingResult:
    bound_count: int
    copied_image_count: int
    warnings: tuple[str, ...] = ()
    matched_subject: str = ""
    matched_grade: str = ""
    matched_lesson_id: str = ""
    matched_lesson_name: str = ""


def load_exercise_bank(path: str | Path | None) -> list[ExerciseRecord]:
    """Load exercise records from a JSON bank file.

    Supported top-level shapes:
    - `[record, ...]`
    - `{ "exercises": [record, ...] }`
    """

    if path is None:
        return []
    bank_path = Path(path).expanduser()
    if not bank_path.exists():
        return []
    raw = json.loads(bank_path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        items = raw.get("exercises") or raw.get("items") or []
    else:
        items = raw
    if not isinstance(items, list):
        return []
    root = bank_path.parent
    records: list[ExerciseRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        record = _normalize_exercise_record(item, root)
        if record is not None:
            records.append(record)
    return records


def load_exercise_records(
    *,
    bank_path: str | Path | None = None,
    db_path: str | Path | None = None,
    image_root: str | Path | None = None,
) -> list[ExerciseRecord]:
    """Load exercise records from all configured exercise-bank sources."""

    records: list[ExerciseRecord] = []
    if db_path is not None:
        from edupptx.planning.exercise_db_provider import load_exercise_bank_from_db

        records.extend(load_exercise_bank_from_db(db_path, image_root=image_root))
    records.extend(load_exercise_bank(bank_path))
    return records


def select_exercise_candidates(
    records: list[ExerciseRecord],
    *,
    subject: str = "",
    grade: str = "",
    grade_band: str = "",
    topic: str = "",
    requirements: str = "",
    limit_per_category: int = 4,
) -> list[ExerciseRecord]:
    """Select a small deterministic candidate pool for the planner prompt."""

    if not records:
        return []
    limit = max(1, int(limit_per_category or 4))
    eligible = [
        record for record in records
        if _compatible(record.subject, subject)
        and _compatible(record.grade, grade)
        and _compatible(record.grade_band, grade_band)
    ]
    if not eligible:
        return []

    query_text = _clean_text(f"{topic} {requirements}")
    grouped: dict[str, list[ExerciseRecord]] = {"A": [], "B": [], "C": []}
    for record in eligible:
        grouped.setdefault(record.category, []).append(record)

    selected: list[ExerciseRecord] = []
    for category in ("A", "B", "C"):
        ranked = sorted(
            grouped.get(category, []),
            key=lambda record: (
                -_record_score(record, query_text),
                record.exercise_id,
            ),
        )
        selected.extend(ranked[:limit])
    return selected


def bind_strict_lesson_exercises_to_draft(
    draft: PlanningDraft,
    records: list[ExerciseRecord],
    *,
    session_dir: str | Path,
    limit_per_category: int = 4,
) -> ExerciseBindingResult:
    """Bind DB exercises only when grade, subject and lesson name strictly match the plan."""

    subject = _effective_subject_from_draft(draft)
    grade = _clean_text(getattr(draft.meta, "grade", ""))
    if not subject or not grade or subject == "其他" or grade == "其他":
        return ExerciseBindingResult(
            bound_count=0,
            copied_image_count=0,
            warnings=("strict lesson match skipped: missing subject or grade",),
        )

    candidates = _lesson_name_candidates_from_draft(draft)
    matched_records, lesson_id, lesson_name = _strict_lesson_records(
        records,
        subject=subject,
        grade=grade,
        lesson_candidates=candidates,
    )
    if not matched_records:
        return ExerciseBindingResult(
            bound_count=0,
            copied_image_count=0,
            warnings=("strict lesson match skipped: no lesson-level exercise records",),
            matched_subject=subject,
            matched_grade=grade,
        )

    exercise_pages = _exercise_pages(draft)
    if not exercise_pages:
        return ExerciseBindingResult(
            bound_count=0,
            copied_image_count=0,
            warnings=("strict lesson match found records but plan has no exercise pages",),
            matched_subject=subject,
            matched_grade=grade,
            matched_lesson_id=lesson_id,
            matched_lesson_name=lesson_name,
        )

    used: set[str] = set()
    category_usage: dict[str, int] = {"A": 0, "B": 0, "C": 0}
    for page_index, page in enumerate(exercise_pages):
        category = _preferred_category_for_page(page, page_index)
        count = _planned_exercise_count(page)
        page_records = _pick_records_for_page(
            matched_records,
            category=category,
            count=count,
            used=used,
            category_usage=category_usage,
            query_text=_page_query_text(draft, page),
            limit_per_category=limit_per_category,
        )
        if page_records:
            page.exercise_refs = [record.exercise_id for record in page_records]

    result = bind_exercises_to_draft(draft, matched_records, session_dir=session_dir)
    return ExerciseBindingResult(
        bound_count=result.bound_count,
        copied_image_count=result.copied_image_count,
        warnings=result.warnings,
        matched_subject=subject,
        matched_grade=grade,
        matched_lesson_id=lesson_id,
        matched_lesson_name=lesson_name,
    )


def bind_exercises_to_draft(
    draft: PlanningDraft,
    records: list[ExerciseRecord],
    *,
    session_dir: str | Path,
) -> ExerciseBindingResult:
    """Bind selected `exercise_refs` into exact page content and local images."""

    index = {record.exercise_id: record for record in records}
    session_root = Path(session_dir)
    copied_image_count = 0
    bound_count = 0
    warnings: list[str] = []

    for page in draft.pages:
        refs = _dedupe_refs(page.exercise_refs)
        page.exercise_refs = refs
        if not refs:
            continue

        # Degrade gracefully: a hallucinated ref or an answer-less record drops
        # that single ref (surfaced as a warning) instead of crashing the whole
        # generation. The exercise feature is best-effort, not a hard gate.
        selected: list[ExerciseRecord] = []
        for exercise_id in refs:
            record = index.get(exercise_id)
            if record is None:
                warnings.append(f"exercise_ref not found in exercise bank, skipped: {exercise_id}")
                continue
            if not record.answer:
                warnings.append(f"exercise_ref has no answer for reveal binding, skipped: {exercise_id}")
                continue
            selected.append(record)

        # Prune dropped refs from the plan so it never references an unbound id.
        page.exercise_refs = [record.exercise_id for record in selected]
        if not selected:
            continue

        page.content_points = _build_source_content_points(selected)
        page.exercise_payloads = []
        page.material_needs.images = [
            image for image in page.material_needs.images
            if image.source != "exercise_asset"
        ]
        for record in selected:
            image_payloads, image_needs, copied, skipped = _bind_images(record, session_root)
            copied_image_count += copied
            warnings.extend(skipped)
            page.exercise_payloads.append(_record_payload(record, image_payloads))
            page.material_needs.images.extend(image_needs)
            bound_count += 1

        if page.page_type not in {"exercise", "quiz"}:
            page.page_type = "exercise"
        if page.reveal_from_page is None:
            page.reveal_mode = _infer_reveal_mode(selected)
        if not page.design_notes:
            page.design_notes = "数据库习题页，题干与配图保持绑定，先作答后揭晓答案"
        if not page.notes:
            page.notes = "先让学生独立完成题目，随后进入答案揭晓页核对并讲解。"

    return ExerciseBindingResult(
        bound_count=bound_count,
        copied_image_count=copied_image_count,
        warnings=tuple(warnings),
    )


def restore_exercise_refs_from_source(draft: PlanningDraft, source: PlanningDraft) -> None:
    """Restore refs/payloads by page number when stage-2 refinement drops them."""

    by_page = {page.page_number: page for page in source.pages if page.exercise_refs}
    for page in draft.pages:
        source_page = by_page.get(page.page_number)
        if source_page is None:
            continue
        if not page.exercise_refs:
            page.exercise_refs = list(source_page.exercise_refs)
        if not page.exercise_payloads:
            page.exercise_payloads = list(source_page.exercise_payloads)


def build_reveal_content_points_from_payloads(page_data: dict[str, Any]) -> list[str]:
    """Return answer content for reveal pages from bound exercise payloads."""

    payloads = page_data.get("exercise_payloads")
    if not isinstance(payloads, list):
        return []
    points: list[str] = []
    for index, payload in enumerate(payloads, 1):
        if not isinstance(payload, dict):
            continue
        label = f"题目{index}" if len(payloads) > 1 else "题目"
        answer = _clean_text(payload.get("answer"))
        explanation = _clean_text(payload.get("explanation"))
        if answer:
            points.append(f"{label}答案：{answer}")
        if explanation:
            points.append(f"{label}解析：{explanation}")
    return points


def _normalize_exercise_record(item: dict[str, Any], root: Path) -> ExerciseRecord | None:
    exercise_id = _first_text(item, "exercise_id", "id", "question_id")
    stem = _first_text(item, "stem", "question", "title", "content")
    if not exercise_id or not stem:
        return None

    category = _normalize_category(_first_text(item, "category", "category_code", "type"))
    options = tuple(_string_list(item.get("options") or item.get("choices")))
    answer = _first_text(item, "answer", "correct_answer", "solution")
    explanation = _first_text(item, "explanation", "analysis", "解析")
    knowledge_points = tuple(_string_list(
        item.get("knowledge_points")
        or item.get("knowledge")
        or item.get("topic_refs")
        or item.get("tags")
    ))
    images = tuple(_normalize_image_asset(asset, root) for asset in _image_items(item))
    images = tuple(asset for asset in images if asset is not None)

    return ExerciseRecord(
        exercise_id=exercise_id,
        category=category,
        subject=_first_text(item, "subject"),
        grade=_first_text(item, "grade", "grade_norm"),
        grade_band=_first_text(item, "grade_band"),
        lesson_id=_first_text(item, "lesson_id"),
        lesson_name=_first_text(item, "lesson_name", "lesson", "course_name"),
        stem=stem,
        options=options,
        answer=answer,
        explanation=explanation,
        knowledge_points=knowledge_points,
        difficulty=_first_text(item, "difficulty", "level"),
        image_assets=images,
    )


def _normalize_image_asset(item: Any, root: Path) -> ExerciseImageAsset | None:
    if isinstance(item, str):
        image_id = Path(item).stem
        path = Path(item)
        query = ""
        role = "question_diagram"
        aspect_ratio = "16:9"
    elif isinstance(item, dict):
        raw_path = _first_text(item, "path", "image_path", "file", "url")
        if not raw_path:
            return None
        image_id = _first_text(item, "image_id", "asset_id", "id") or Path(raw_path).stem
        path = Path(raw_path)
        query = _first_text(item, "query", "caption", "description") or "题目配图"
        role = _normalize_image_role(_first_text(item, "role") or "question_diagram")
        aspect_ratio = _first_text(item, "aspect_ratio") or "16:9"
    else:
        return None
    if not path.is_absolute():
        path = root / path
    return ExerciseImageAsset(
        image_id=image_id,
        path=path,
        role=role,
        query=query,
        aspect_ratio=aspect_ratio,
    )


def _image_items(item: dict[str, Any]) -> list[Any]:
    images = item.get("image_assets")
    if images is None:
        images = item.get("images")
    if images is None:
        image_path = item.get("image_path")
        images = [image_path] if image_path else []
    if isinstance(images, list):
        return images
    return [images] if images else []


def _bind_images(
    record: ExerciseRecord,
    session_root: Path,
) -> tuple[list[dict[str, str]], list[ImageNeed], int, list[str]]:
    payloads: list[dict[str, str]] = []
    image_needs: list[ImageNeed] = []
    skipped: list[str] = []
    copied = 0
    for asset in record.image_assets:
        if not asset.path.exists():
            # A missing image file drops just that asset (warned), keeping the
            # exercise's text and any other images bound rather than aborting.
            skipped.append(
                f"exercise image missing, skipped: {record.exercise_id}/{asset.image_id} ({asset.path})"
            )
            continue
        target_aspect_ratio = normalize_image_aspect_ratio(asset.aspect_ratio)
        relative_path = Path("materials") / "exercises" / f"{record.exercise_id}_{asset.image_id}.png"
        destination = session_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        materialized_aspect_ratio = _materialize_exercise_image(
            asset.path,
            destination,
            target_aspect_ratio=target_aspect_ratio,
        )
        copied += 1
        relative_text = relative_path.as_posix()
        payloads.append({
            "image_id": asset.image_id,
            "path": relative_text,
            "role": asset.role,
            "query": asset.query,
            "aspect_ratio": materialized_aspect_ratio,
        })
        image_needs.append(ImageNeed(
            query=asset.query or f"{record.exercise_id} 题目配图",
            source="exercise_asset",
            role="illustration",
            asset_id=asset.image_id,
            path=relative_text,
            aspect_ratio=materialized_aspect_ratio,
            caption=asset.query,
        ))
    return payloads, image_needs, copied, skipped


def _materialize_exercise_image(source: Path, destination: Path, *, target_aspect_ratio: str) -> str:
    try:
        from PIL import Image

        with Image.open(source) as raw:
            image = raw.convert("RGBA")
            aspect_ratio = normalize_image_aspect_ratio(
                target_aspect_ratio or match_aspect_ratio(*image.size)
            )
            canvas = _transparent_canvas_for_ratio(image, aspect_ratio)
            destination.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(destination, format="PNG")
            return match_aspect_ratio(canvas.width, canvas.height)
    except Exception:
        if source.resolve() != destination.resolve():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        return normalize_image_aspect_ratio(target_aspect_ratio)


def _transparent_canvas_for_ratio(image: Any, aspect_ratio: str) -> Any:
    from PIL import Image

    ratio_width, ratio_height = _ratio_parts(aspect_ratio)
    multiplier = max(
        _ceil_div(image.width, ratio_width),
        _ceil_div(image.height, ratio_height),
        1,
    )
    canvas_width = ratio_width * multiplier
    canvas_height = ratio_height * multiplier
    canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    left = (canvas_width - image.width) // 2
    top = (canvas_height - image.height) // 2
    canvas.alpha_composite(image, (left, top))
    return canvas


def _ratio_parts(aspect_ratio: str) -> tuple[int, int]:
    parts = str(aspect_ratio or "16:9").split(":", 1)
    if len(parts) != 2:
        return 16, 9
    try:
        width = max(1, int(parts[0]))
        height = max(1, int(parts[1]))
        return width, height
    except ValueError:
        return 16, 9


def _ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def _record_payload(record: ExerciseRecord, image_payloads: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "exercise_id": record.exercise_id,
        "category": record.category,
        "stem": record.stem,
        "options": list(record.options),
        "answer": record.answer,
        "explanation": record.explanation,
        "knowledge_points": list(record.knowledge_points),
        "difficulty": record.difficulty,
        "lesson_id": record.lesson_id,
        "lesson_name": record.lesson_name,
        "image_assets": image_payloads,
    }


def _build_source_content_points(records: list[ExerciseRecord]) -> list[str]:
    points: list[str] = []
    for index, record in enumerate(records, 1):
        label = f"题目{index}" if len(records) > 1 else "题目"
        points.append(f"{label}（{_CATEGORY_LABELS.get(record.category, record.category)}）：{record.stem}")
        for option in record.options:
            points.append(f"{label}选项：{option}")
        if record.image_assets:
            points.append(f"{label}配图：使用题库绑定图片，图片与题干不可拆分")
        points.append(f"{label}答案揭晓区：稍后揭晓")
    return points


def _infer_reveal_mode(records: list[ExerciseRecord]) -> str:
    if any(record.options for record in records):
        return "highlight_correct_option"
    return "show_answer"


def _dedupe_refs(refs: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for ref in refs or []:
        text = str(ref or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _record_score(record: ExerciseRecord, query_text: str) -> int:
    score = 0
    haystack = _clean_text(" ".join((
        record.stem,
        *record.options,
        *record.knowledge_points,
    )))
    for point in record.knowledge_points:
        point_text = _clean_text(point)
        if point_text and point_text in query_text:
            score += 8 + min(len(point_text), 8)
        elif point_text and query_text and query_text in point_text:
            score += 4
    for token in _tokens(query_text):
        if token and token in haystack:
            score += max(1, min(len(token), 6))
    for token in _semantic_query_tokens(query_text):
        if token in haystack:
            score += max(2, min(len(token), 6))
    if record.image_assets:
        score += 1
    return score


def _compatible(record_value: str, target_value: str) -> bool:
    record_text = _clean_text(record_value)
    target_text = _clean_text(target_value)
    if not record_text or record_text == "其他" or not target_text or target_text == "其他":
        return True
    return record_text == target_text


_SUBJECT_TERMS = ("语文", "数学", "物理")
_LESSON_GENERIC_TERMS = (
    "课文教学", "教学设计", "课程教学", "课堂教学", "教学课件", "课件",
    "同步课程", "同步教学", "上册", "下册", "第1课时", "第2课时",
    "第一课时", "第二课时", "第三课时",
    "一年级", "二年级", "三年级", "四年级", "五年级", "六年级", "七年级", "八年级",
    "小学", "初中", "高中", "语文", "数学", "物理",
)
_LESSON_PUNCTUATION = "《》〈〉「」『』“”\"'[]（）()：:，,。.!！?？ ·-—_、．\t\r\n"


def _effective_subject_from_draft(draft: PlanningDraft) -> str:
    subject = _clean_text(getattr(draft.meta, "subject", ""))
    if subject and subject != "其他":
        return subject
    text = _draft_text(draft)
    for candidate in _SUBJECT_TERMS:
        if candidate in text:
            return candidate
    return subject


def _lesson_name_candidates_from_draft(draft: PlanningDraft) -> list[str]:
    raw_candidates: list[str] = []
    text = _draft_text(draft)
    raw_candidates.extend(re.findall(r"《([^》]{1,40})》", text))
    raw_candidates.extend(re.findall(r"[“\"]([^”\"]{1,40})[”\"]", text))
    raw_candidates.append(_clean_text(getattr(draft.meta, "topic", "")))
    for page in draft.pages[:8]:
        raw_candidates.append(_clean_text(page.title))
        raw_candidates.append(_clean_text(page.subtitle))
        title = _clean_text(page.title)
        if "：" in title:
            raw_candidates.append(title.split("：", 1)[1])
        if ":" in title:
            raw_candidates.append(title.split(":", 1)[1])

    result: list[str] = []
    seen: set[str] = set()
    for candidate in raw_candidates:
        normalized = _normalize_lesson_name(candidate)
        if len(normalized) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _strict_lesson_records(
    records: list[ExerciseRecord],
    *,
    subject: str,
    grade: str,
    lesson_candidates: list[str],
) -> tuple[list[ExerciseRecord], str, str]:
    if not lesson_candidates:
        return [], "", ""

    grouped: dict[tuple[str, str], list[ExerciseRecord]] = {}
    best_scores: dict[tuple[str, str], int] = {}
    for record in records:
        if _clean_text(record.subject) != subject or _clean_text(record.grade) != grade:
            continue
        lesson_name = _clean_text(record.lesson_name)
        lesson_id = _clean_text(record.lesson_id)
        lesson_norm = _normalize_lesson_name(lesson_name)
        if not lesson_norm:
            continue
        score = _lesson_match_score(lesson_norm, lesson_candidates)
        if score < 90:
            continue
        key = (lesson_id, lesson_name)
        grouped.setdefault(key, []).append(record)
        best_scores[key] = max(best_scores.get(key, 0), score)

    if not grouped:
        return [], "", ""

    best_key = max(
        grouped,
        key=lambda key: (
            best_scores.get(key, 0),
            len(grouped[key]),
            key[1],
        ),
    )
    return grouped[best_key], best_key[0], best_key[1]


def _lesson_match_score(lesson_norm: str, candidates: list[str]) -> int:
    best = 0
    for candidate in candidates:
        if candidate == lesson_norm:
            best = max(best, 120 + len(candidate))
        elif candidate in lesson_norm:
            best = max(best, 90 + len(candidate))
        elif lesson_norm in candidate:
            best = max(best, 90 + len(lesson_norm))
    return best


def _normalize_lesson_name(value: Any) -> str:
    text = _clean_text(value)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"^第[一二三四五六七八九十\d]+[节课单元]*", "", text)
    text = re.sub(r"^[一二三四五六七八九十\d]+[.、．\s]+", "", text)
    for term in _LESSON_GENERIC_TERMS:
        text = text.replace(term, "")
    return "".join(char for char in text if char not in _LESSON_PUNCTUATION).strip()


def _draft_text(draft: PlanningDraft) -> str:
    parts = [
        _clean_text(getattr(draft.meta, "topic", "")),
        _clean_text(getattr(draft.meta, "audience", "")),
        _clean_text(getattr(draft.meta, "purpose", "")),
    ]
    for page in draft.pages:
        parts.append(_clean_text(page.title))
        parts.append(_clean_text(page.subtitle))
        for point in page.content_points or []:
            parts.append(_clean_text(point))
    return "\n".join(part for part in parts if part)


_STRUCTURAL_NON_EXERCISE_PAGE_TYPES = {"cover", "toc", "section", "summary", "closing"}


def _exercise_pages(draft: PlanningDraft) -> list[Any]:
    pages: list[Any] = []
    for page in draft.pages:
        if getattr(page, "reveal_from_page", None) is not None:
            continue
        page_type = str(getattr(page, "page_type", "") or "").strip()
        if page_type in _STRUCTURAL_NON_EXERCISE_PAGE_TYPES:
            continue
        if page_type in {"exercise", "quiz"}:
            pages.append(page)
            continue
        if page_type == "content" and _looks_like_exercise_page(_page_text(page)):
            pages.append(page)
    return pages


def _looks_like_exercise_page(text: str) -> bool:
    return any(term in text for term in (
        "练习", "习题", "巩固", "综合运用", "扩展探索", "拓展探索",
        "课堂检测", "随堂练", "达标检测", "小试牛刀", "挑战",
    ))


def _preferred_category_for_page(page: Any, page_index: int) -> str:
    text = _page_text(page)
    if any(term in text for term in ("扩展探索", "拓展探索", "拓展", "扩展", "探索", "挑战")):
        return "C"
    if any(term in text for term in ("综合运用", "综合", "应用", "实践", "任务")):
        return "B"
    if any(term in text for term in ("复习巩固", "巩固", "回顾", "基础", "练一练")):
        return "A"
    return ("B", "A", "C")[min(page_index, 2)]


def _planned_exercise_count(page: Any) -> int:
    points = [str(point) for point in (page.content_points or [])]
    question_like = 0
    for point in points:
        if any(marker in point for marker in ("？", "?", "<s>", "（", "(", "判断", "选择", "计算", "练习", "题")):
            question_like += 1
    if question_like:
        return max(1, min(question_like, 3))
    return 1


def _pick_records_for_page(
    records: list[ExerciseRecord],
    *,
    category: str,
    count: int,
    used: set[str],
    category_usage: dict[str, int],
    query_text: str,
    limit_per_category: int,
) -> list[ExerciseRecord]:
    category_limit = max(1, int(limit_per_category or 4))
    limit = max(1, min(count, category_limit))
    preferred = [
        record for record in records
        if record.category == category
        and record.exercise_id not in used
        and category_usage.get(record.category, 0) < category_limit
    ]
    fallback = [
        record for record in records
        if record.category != category
        and record.exercise_id not in used
        and category_usage.get(record.category, 0) < category_limit
    ]
    ranked = sorted(
        preferred,
        key=lambda record: (-_record_score(record, query_text), record.exercise_id),
    )
    if len(ranked) < limit:
        ranked.extend(sorted(
            fallback,
            key=lambda record: (-_record_score(record, query_text), record.category, record.exercise_id),
        ))
    selected: list[ExerciseRecord] = []
    for record in ranked:
        if len(selected) >= limit:
            break
        if category_usage.get(record.category, 0) >= category_limit:
            continue
        selected.append(record)
        category_usage[record.category] = category_usage.get(record.category, 0) + 1
        used.add(record.exercise_id)
    return selected


def _page_query_text(draft: PlanningDraft, page: Any) -> str:
    return " ".join((
        _clean_text(getattr(draft.meta, "topic", "")),
        _page_text(page),
    ))


def _page_text(page: Any) -> str:
    parts = [_clean_text(page.title), _clean_text(page.subtitle), _clean_text(page.design_notes), _clean_text(page.notes)]
    parts.extend(_clean_text(point) for point in (page.content_points or []))
    return "\n".join(part for part in parts if part)


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[\s,，。；;、]+", text) if token]


_GRADE_CONTEXT_PATTERN = re.compile(
    r"(?:小学|初中|高中)?(?:[一二三四五六七八九十\d]+年级|初[一二三\d]|高[一二三\d]|低年级|高年级)"
)
_QUERY_STOP_TERMS = {
    "数学", "语文", "物理", "学生", "适合", "课程", "教学", "课件", "题目",
    "练习", "习题", "复习", "巩固", "综合", "运用", "扩展", "探索",
}


def _semantic_query_tokens(text: str) -> list[str]:
    cleaned = _GRADE_CONTEXT_PATTERN.sub(" ", _clean_text(text))
    for term in _QUERY_STOP_TERMS:
        cleaned = cleaned.replace(term, " ")

    result: list[str] = []
    seen: set[str] = set()
    for token in _tokens(cleaned):
        _append_token(result, seen, token)
        if _is_chinese_run(token) and len(token) >= 3:
            for size in (4, 3, 2):
                if len(token) < size:
                    continue
                for index in range(0, len(token) - size + 1):
                    _append_token(result, seen, token[index:index + size])
    return result


def _append_token(result: list[str], seen: set[str], token: str) -> None:
    cleaned = _clean_text(token)
    if len(cleaned) < 2 or cleaned in _QUERY_STOP_TERMS or cleaned in seen:
        return
    seen.add(cleaned)
    result.append(cleaned)


def _is_chinese_run(text: str) -> bool:
    return bool(text) and all("\u4e00" <= char <= "\u9fff" for char in text)


def _normalize_category(value: str) -> str:
    text = _clean_text(value)
    return _CATEGORY_ALIASES.get(text, text if text in {"A", "B", "C"} else "A")


def _normalize_image_role(value: str) -> str:
    text = _clean_text(value)
    if text in {"hero", "illustration", "icon", "background"}:
        return text
    return "illustration"


def _first_text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        text = _clean_text(value)
        if text:
            return text
    return ""


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_clean_text(item) for item in value if _clean_text(item)]
    if isinstance(value, tuple):
        return [_clean_text(item) for item in value if _clean_text(item)]
    text = _clean_text(value)
    return [text] if text else []


def _clean_text(value: Any) -> str:
    return str(value or "").strip()
