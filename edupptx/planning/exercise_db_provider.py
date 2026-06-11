"""SQLite adapter for the teach-kb exercise database."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from loguru import logger

from edupptx.models import match_aspect_ratio
from edupptx.planning.exercise_plan_binder import ExerciseImageAsset, ExerciseRecord


@dataclass(frozen=True)
class LessonContext:
    lesson_id: str = ""
    lesson_name: str = ""
    subject: str = ""
    grade: str = ""
    grade_band: str = ""
    knowledge_points: tuple[str, ...] = ()


def load_exercise_bank_from_db(
    db_path: str | Path | None,
    *,
    image_root: str | Path | None = None,
    chunk_size: int = 512,
    max_records: int | None = None,
) -> list[ExerciseRecord]:
    """Load exercise records from the teach-kb SQLite database.

    The adapter is read-only and maps:
    - questions.id -> ExerciseRecord.exercise_id as q_<id>
    - questions.difficulty -> A/B/C category
    - questions.image_id -> image_assets.id/file_path
    - questions.lesson_id -> hierarchy lesson/unit/semester/grade context
    """

    if db_path is None:
        return []
    path = Path(db_path).expanduser()
    if not path.exists():
        return []

    resolved_image_root = _infer_image_root(path, image_root)
    records: list[ExerciseRecord] = []
    with sqlite3.connect(str(path)) as con:
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA query_only = ON")
        except sqlite3.DatabaseError:
            pass
        lesson_contexts = _load_lesson_contexts(con)
        question_knowledge = _load_question_knowledge(con)
        for row in _iter_question_rows(con, chunk_size=max(1, int(chunk_size or 512))):
            record = _record_from_row(
                row,
                lesson_contexts.get(_as_int(row["lesson_id"])),
                question_knowledge.get(_as_int(row["id"]), ()),
                db_path=path,
                image_root=resolved_image_root,
            )
            if record is None:
                continue
            records.append(record)
            if max_records is not None and len(records) >= max_records:
                break
    return records


def resolve_teach_kb_image_path(
    db_path: str | Path,
    image_root: str | Path | None,
    file_path: str,
) -> Path:
    """Resolve image_assets.file_path against the teach-kb uploads directory."""

    root = _infer_image_root(Path(db_path).expanduser(), image_root)
    raw = _clean_text(file_path).replace("\\", "/")
    if not raw:
        return root

    container_prefixes = ("/app/uploads/", "/uploads/")
    for prefix in container_prefixes:
        if raw.startswith(prefix):
            return root / raw[len(prefix):]

    if raw.startswith("uploads/"):
        return root / raw[len("uploads/"):]

    raw_path = Path(raw)
    if raw_path.is_absolute():
        return raw_path
    return root / raw


def _infer_image_root(db_path: Path, image_root: str | Path | None) -> Path:
    if image_root is not None and _clean_text(image_root):
        return Path(image_root).expanduser()
    # Common teach-kb layout: <repo>/data/db/teach_kb.db and <repo>/data/uploads.
    if db_path.parent.name == "db":
        candidate = db_path.parent.parent / "uploads"
        if candidate.exists() or db_path.parent.parent.name == "data":
            return candidate
    return db_path.parent / "uploads"


def _load_lesson_contexts(con: sqlite3.Connection) -> dict[int, LessonContext]:
    try:
        rows = con.execute(
            "select id, parent_id, level, name, subject from hierarchy"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        logger.warning("Exercise DB hierarchy load skipped: {}", str(exc)[:160])
        return {}

    nodes: dict[int, sqlite3.Row] = {}
    for row in rows:
        node_id = _as_int(row["id"])
        if node_id is not None:
            nodes[node_id] = row

    contexts: dict[int, LessonContext] = {}
    for node_id in nodes:
        chain = _ancestor_chain(nodes, node_id)
        subject = _first_non_empty(row["subject"] for row in reversed(chain))
        grade = _first_non_empty(
            row["name"] for row in chain if _clean_text(row["level"]) == "grade"
        )
        names = tuple(
            _clean_text(row["name"])
            for row in reversed(chain)
            if _clean_text(row["name"])
        )
        lesson_row = next(
            (row for row in chain if _clean_text(row["level"]) == "lesson"),
            nodes.get(node_id),
        )
        contexts[node_id] = LessonContext(
            lesson_id=_clean_text(node_id),
            lesson_name=_clean_text(lesson_row["name"]) if lesson_row is not None else "",
            subject=subject,
            grade=grade,
            grade_band=_grade_band_from_grade(grade),
            knowledge_points=names,
        )
    return contexts


def _ancestor_chain(nodes: dict[int, sqlite3.Row], node_id: int) -> list[sqlite3.Row]:
    chain: list[sqlite3.Row] = []
    seen: set[int] = set()
    current_id: int | None = node_id
    while current_id is not None and current_id not in seen:
        row = nodes.get(current_id)
        if row is None:
            break
        seen.add(current_id)
        chain.append(row)
        current_id = _as_int(row["parent_id"])
    return chain


def _load_question_knowledge(con: sqlite3.Connection) -> dict[int, tuple[str, ...]]:
    try:
        rows = con.execute(
            """
            select qk.question_id, kn.label, kn.path
            from question_knowledge qk
            join knowledge_nodes kn on kn.id = qk.knowledge_id
            """
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}

    grouped: dict[int, list[str]] = {}
    for row in rows:
        question_id = _as_int(row["question_id"])
        if question_id is None:
            continue
        values = grouped.setdefault(question_id, [])
        for key in ("label", "path"):
            text = _clean_text(row[key])
            if text and text not in values:
                values.append(text)
    return {question_id: tuple(values) for question_id, values in grouped.items()}


def _iter_question_rows(
    con: sqlite3.Connection,
    *,
    chunk_size: int,
) -> Iterator[sqlite3.Row]:
    try:
        bounds = con.execute("select min(id), max(id) from questions").fetchone()
    except sqlite3.DatabaseError as exc:
        logger.warning("Exercise DB question bounds unavailable: {}", str(exc)[:160])
        return
    if bounds is None or bounds[0] is None or bounds[1] is None:
        return

    current = int(bounds[0])
    max_id = int(bounds[1])
    while current <= max_id:
        end = min(current + chunk_size - 1, max_id)
        yield from _read_question_range(con, current, end)
        current = end + 1


def _read_question_range(
    con: sqlite3.Connection,
    start_id: int,
    end_id: int,
) -> list[sqlite3.Row]:
    try:
        return con.execute(
            """
            select q.id, q.lesson_id, q.difficulty, q.question_type, q.stem,
                   q.options, q.answer, q.answer_key, q.scoring_criteria,
                   q.image_id, i.file_path as image_path, i.alt_text as image_alt_text
            from questions q
            left join image_assets i on i.id = q.image_id
            where q.id >= ? and q.id <= ?
              and q.stem is not null and trim(q.stem) <> ''
            order by q.id
            """,
            (start_id, end_id),
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        if start_id >= end_id:
            logger.warning(
                "Exercise DB question skipped: id={}, error={}",
                start_id,
                str(exc)[:160],
            )
            return []
        mid = (start_id + end_id) // 2
        return [
            *_read_question_range(con, start_id, mid),
            *_read_question_range(con, mid + 1, end_id),
        ]


def _record_from_row(
    row: sqlite3.Row,
    context: LessonContext | None,
    knowledge_points: tuple[str, ...],
    *,
    db_path: Path,
    image_root: Path,
) -> ExerciseRecord | None:
    stem = _clean_text(row["stem"])
    answer = _format_answer(row["answer"])
    if not stem or not answer:
        return None

    question_id = _clean_text(row["id"])
    image_assets = _image_assets_from_row(row, db_path=db_path, image_root=image_root)
    if image_assets is None:
        return None
    merged_knowledge = _dedupe_texts(
        *((context.knowledge_points if context else ())),
        *knowledge_points,
    )
    return ExerciseRecord(
        exercise_id=f"q_{question_id}",
        category=_normalize_db_category(row["difficulty"]),
        stem=stem,
        subject=context.subject if context else "",
        grade=context.grade if context else "",
        grade_band=context.grade_band if context else "",
        lesson_id=context.lesson_id if context else "",
        lesson_name=context.lesson_name if context else "",
        options=tuple(_format_options(row["options"])),
        answer=answer,
        explanation=_clean_text(row["answer_key"]) or _clean_text(row["scoring_criteria"]),
        knowledge_points=merged_knowledge,
        difficulty=_clean_text(row["difficulty"]),
        image_assets=image_assets,
    )


def _image_assets_from_row(
    row: sqlite3.Row,
    *,
    db_path: Path,
    image_root: Path,
) -> tuple[ExerciseImageAsset, ...] | None:
    image_id = _clean_text(row["image_id"])
    image_path = _clean_text(row["image_path"])
    if not image_id and not image_path:
        return ()
    if not image_id or not image_path:
        return None

    path = resolve_teach_kb_image_path(db_path, image_root, image_path)
    if not path.exists():
        logger.warning("Exercise DB image missing, question skipped: {}", path)
        return None

    return (
        ExerciseImageAsset(
            image_id=image_id,
            path=path,
            role="question_diagram",
            query=_clean_text(row["image_alt_text"]) or f"exercise image {image_id}",
            aspect_ratio=_image_aspect_ratio(path),
        ),
    )


def _image_aspect_ratio(path: Path) -> str:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return match_aspect_ratio(*image.size)
    except Exception:
        return "4:3"


def _format_options(value: Any) -> list[str]:
    text = _clean_text(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [line.strip() for line in text.splitlines() if line.strip()]
    if isinstance(parsed, list):
        result: list[str] = []
        for item in parsed:
            if isinstance(item, dict):
                label = _clean_text(item.get("label"))
                option_text = _clean_text(item.get("text") or item.get("value"))
                if label and option_text:
                    result.append(f"{label}. {option_text}")
                elif option_text:
                    result.append(option_text)
            else:
                option_text = _clean_text(item)
                if option_text:
                    result.append(option_text)
        return result
    return [text]


def _format_answer(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, list):
        return "；".join(_clean_text(item) for item in parsed if _clean_text(item))
    if isinstance(parsed, dict):
        parts: list[str] = []
        for key, item in parsed.items():
            item_text = _clean_text(item)
            if item_text:
                parts.append(f"{key}: {item_text}")
        return "；".join(parts)
    return _clean_text(parsed)


def _normalize_db_category(value: Any) -> str:
    text = _clean_text(value).upper()
    return text if text in {"A", "B", "C"} else "A"


def _grade_band_from_grade(grade: str) -> str:
    if not grade:
        return ""
    try:
        from edupptx.materials.ai_image_asset_db import grade_band_from_norm

        band = grade_band_from_norm(grade)
        return "" if band == "其他" else band
    except Exception:
        pass
    return "低年级" if grade in {"一年级", "二年级", "三年级"} else "高年级"


def _dedupe_texts(*items: str) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _clean_text(item)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return tuple(result)


def _first_non_empty(values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _as_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    return str(value or "").strip()
