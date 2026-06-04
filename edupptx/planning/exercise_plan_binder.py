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

from edupptx.models import ImageNeed, PlanningDraft


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

        selected: list[ExerciseRecord] = []
        for exercise_id in refs:
            record = index.get(exercise_id)
            if record is None:
                raise ValueError(f"exercise_ref not found in exercise bank: {exercise_id}")
            if not record.answer:
                raise ValueError(f"exercise_ref has no answer for reveal binding: {exercise_id}")
            selected.append(record)

        page.content_points = _build_source_content_points(selected)
        page.exercise_payloads = []
        page.material_needs.images = [
            image for image in page.material_needs.images
            if image.source != "exercise_asset"
        ]
        for record in selected:
            image_payloads, image_needs, copied = _bind_images(record, session_root)
            copied_image_count += copied
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
) -> tuple[list[dict[str, str]], list[ImageNeed], int]:
    payloads: list[dict[str, str]] = []
    image_needs: list[ImageNeed] = []
    copied = 0
    for asset in record.image_assets:
        if not asset.path.exists():
            raise FileNotFoundError(f"exercise image missing: {asset.path}")
        suffix = asset.path.suffix.lower() or ".png"
        relative_path = Path("materials") / "exercises" / f"{record.exercise_id}_{asset.image_id}{suffix}"
        destination = session_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if asset.path.resolve() != destination.resolve():
            shutil.copy2(asset.path, destination)
        copied += 1
        relative_text = relative_path.as_posix()
        payloads.append({
            "image_id": asset.image_id,
            "path": relative_text,
            "role": asset.role,
            "query": asset.query,
            "aspect_ratio": asset.aspect_ratio,
        })
        image_needs.append(ImageNeed(
            query=asset.query or f"{record.exercise_id} 题目配图",
            source="exercise_asset",
            role="illustration",
            asset_id=asset.image_id,
            path=relative_text,
            aspect_ratio=asset.aspect_ratio,
            caption=asset.query,
        ))
    return payloads, image_needs, copied


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
    for point in record.knowledge_points:
        point_text = _clean_text(point)
        if point_text and point_text in query_text:
            score += 4
    for token in _tokens(query_text):
        if token and token in record.stem:
            score += 1
    if record.image_assets:
        score += 1
    return score


def _compatible(record_value: str, target_value: str) -> bool:
    record_text = _clean_text(record_value)
    target_text = _clean_text(target_value)
    if not record_text or record_text == "其他" or not target_text or target_text == "其他":
        return True
    return record_text == target_text


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[\s,，。；;、]+", text) if token]


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
