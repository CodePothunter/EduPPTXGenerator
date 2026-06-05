import json
import sqlite3
from pathlib import Path

from edupptx.planning.exercise_db_provider import (
    load_exercise_bank_from_db,
    resolve_teach_kb_image_path,
)
from edupptx.models import PagePlan, PlanningDraft, PlanningMeta
from edupptx.planning.exercise_plan_binder import (
    bind_exercises_to_draft,
    bind_strict_lesson_exercises_to_draft,
)


def _create_teach_kb_db(tmp_path: Path) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    db_dir = data_dir / "db"
    image_root = data_dir / "uploads"
    db_dir.mkdir(parents=True)
    (image_root / "images").mkdir(parents=True)
    (image_root / "images" / "fraction.png").write_bytes(b"fraction-image")

    db_path = db_dir / "teach_kb.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        create table hierarchy (
            id integer primary key,
            parent_id integer,
            level text,
            name text,
            subject text,
            sort_order integer,
            is_published integer
        );
        create table image_assets (
            id integer primary key,
            file_path text,
            alt_text text,
            is_confirmed integer,
            updated_at text,
            updated_by text
        );
        create table questions (
            id integer primary key,
            lesson_id integer,
            difficulty text,
            question_type text,
            stem text,
            options text,
            answer text,
            answer_key text,
            scoring_criteria text,
            parent_id integer,
            image_id integer,
            sort_order integer,
            updated_at text,
            updated_by text
        );
        """
    )
    con.executemany(
        """
        insert into hierarchy(id, parent_id, level, name, subject, sort_order, is_published)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, None, "grade", "五年级", "数学", 0, 1),
            (2, 1, "semester", "上册", "数学", 0, 1),
            (3, 2, "unit", "分数", "数学", 0, 1),
            (4, 3, "lesson", "异分母分数加法", "数学", 0, 1),
        ],
    )
    con.execute(
        """
        insert into image_assets(id, file_path, alt_text, is_confirmed, updated_at, updated_by)
        values (10, 'images/fraction.png', 'fraction bar diagram', 1, null, null)
        """
    )
    con.executemany(
        """
        insert into questions(
            id, lesson_id, difficulty, question_type, stem, options, answer, answer_key,
            scoring_criteria, parent_id, image_id, sort_order, updated_at, updated_by
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                101,
                4,
                "B",
                "choice",
                "观察图中分数条，选择 1/2 + 1/3 的结果。",
                json.dumps(
                    [
                        {"label": "A", "text": "2/5"},
                        {"label": "B", "text": "5/6"},
                    ],
                    ensure_ascii=False,
                ),
                "B",
                "先通分，再相加，结果是 5/6。",
                None,
                None,
                10,
                0,
                None,
                None,
            ),
            (
                102,
                4,
                "A",
                "fill_blank",
                "计算 1/4 + 1/4。",
                None,
                json.dumps(["1/2"], ensure_ascii=False),
                "同分母分数相加，分母不变。",
                None,
                None,
                None,
                1,
                None,
                None,
            ),
        ],
    )
    con.commit()
    con.close()
    return db_path, image_root


def _create_ascii_teach_kb_db(tmp_path: Path) -> tuple[Path, Path]:
    from PIL import Image

    data_dir = tmp_path / "ascii_data"
    db_dir = data_dir / "db"
    image_root = data_dir / "uploads"
    db_dir.mkdir(parents=True)
    (image_root / "images").mkdir(parents=True)
    Image.new("RGB", (120, 100), (20, 80, 160)).save(image_root / "images" / "fraction.png")

    db_path = db_dir / "teach_kb.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        create table hierarchy (
            id integer primary key,
            parent_id integer,
            level text,
            name text,
            subject text,
            sort_order integer,
            is_published integer
        );
        create table image_assets (
            id integer primary key,
            file_path text,
            alt_text text,
            is_confirmed integer,
            updated_at text,
            updated_by text
        );
        create table questions (
            id integer primary key,
            lesson_id integer,
            difficulty text,
            question_type text,
            stem text,
            options text,
            answer text,
            answer_key text,
            scoring_criteria text,
            parent_id integer,
            image_id integer,
            sort_order integer,
            updated_at text,
            updated_by text
        );
        """
    )
    con.executemany(
        """
        insert into hierarchy(id, parent_id, level, name, subject, sort_order, is_published)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, None, "grade", "Grade 5", "Math", 0, 1),
            (2, 1, "semester", "Book A", "Math", 0, 1),
            (3, 2, "unit", "Numbers", "Math", 0, 1),
            (4, 3, "lesson", "Fractions", "Math", 0, 1),
            (5, 3, "lesson", "Geometry", "Math", 1, 1),
        ],
    )
    con.execute(
        """
        insert into image_assets(id, file_path, alt_text, is_confirmed, updated_at, updated_by)
        values (10, 'images/fraction.png', 'fraction diagram', 1, null, null)
        """
    )
    con.executemany(
        """
        insert into questions(
            id, lesson_id, difficulty, question_type, stem, options, answer, answer_key,
            scoring_criteria, parent_id, image_id, sort_order, updated_at, updated_by
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (101, 4, "B", "fill_blank", "Add 1/2 and 1/3.", None, "5/6", "Use common denominators.", None, None, 10, 0, None, None),
            (201, 5, "B", "fill_blank", "Name this triangle.", None, "isosceles", "Two sides are equal.", None, None, None, 0, None, None),
        ],
    )
    con.commit()
    con.close()
    return db_path, image_root


def test_load_exercise_bank_from_teach_kb_sqlite_binds_hierarchy_and_images(tmp_path):
    db_path, image_root = _create_teach_kb_db(tmp_path)

    records = load_exercise_bank_from_db(db_path)

    by_id = {record.exercise_id: record for record in records}
    record = by_id["q_101"]
    assert record.category == "B"
    assert record.subject == "数学"
    assert record.grade == "五年级"
    assert record.grade_band == "高年级"
    assert record.options == ("A. 2/5", "B. 5/6")
    assert record.answer == "B"
    assert record.explanation == "先通分，再相加，结果是 5/6。"
    assert "异分母分数加法" in record.knowledge_points
    assert record.image_assets[0].path == image_root / "images" / "fraction.png"
    assert record.image_assets[0].query == "fraction bar diagram"

    text_record = by_id["q_102"]
    assert text_record.image_assets == ()
    assert text_record.answer == "1/2"


def test_resolve_teach_kb_image_path_supports_container_upload_prefix(tmp_path):
    db_path, _image_root = _create_teach_kb_db(tmp_path)
    explicit_root = tmp_path / "custom_uploads"

    resolved = resolve_teach_kb_image_path(
        db_path,
        explicit_root,
        "/app/uploads/images/fraction.png",
    )

    assert resolved == explicit_root / "images" / "fraction.png"


def test_db_loaded_exercise_image_is_copied_into_session_materials(tmp_path):
    db_path, _image_root = _create_teach_kb_db(tmp_path)
    records = load_exercise_bank_from_db(db_path)
    session_dir = tmp_path / "session"
    draft = PlanningDraft(
        meta=PlanningMeta(topic="分数加法", subject="数学", grade="五年级"),
        pages=[
            PagePlan(
                page_number=1,
                page_type="exercise",
                title="综合运用",
                exercise_refs=["q_101"],
            )
        ],
    )

    result = bind_exercises_to_draft(draft, records, session_dir=session_dir)

    assert result.bound_count == 1
    assert result.copied_image_count == 1
    image_need = draft.pages[0].material_needs.images[0]
    assert image_need.source == "exercise_asset"
    assert image_need.path == "materials/exercises/q_101_10.png"
    assert (session_dir / image_need.path).read_bytes() == b"fraction-image"


def test_strict_lesson_binding_uses_only_matching_course_records(tmp_path):
    db_path, _image_root = _create_ascii_teach_kb_db(tmp_path)
    records = load_exercise_bank_from_db(db_path)
    draft = PlanningDraft(
        meta=PlanningMeta(topic="Grade 5 Math Fractions", subject="Math", grade="Grade 5"),
        pages=[
            PagePlan(
                page_number=1,
                page_type="exercise",
                title="Comprehensive practice",
                content_points=["Use the plan's exercise slot."],
            )
        ],
    )

    result = bind_strict_lesson_exercises_to_draft(
        draft,
        records,
        session_dir=tmp_path / "session",
    )

    assert result.bound_count == 1
    assert result.matched_lesson_name == "Fractions"
    assert draft.pages[0].exercise_refs == ["q_101"]
    assert "Add 1/2 and 1/3" in "\n".join(str(item) for item in draft.pages[0].content_points)
    assert "triangle" not in "\n".join(str(item) for item in draft.pages[0].content_points)


def test_strict_lesson_binding_does_not_modify_toc_page_with_exercise_terms(tmp_path):
    db_path, _image_root = _create_teach_kb_db(tmp_path)
    records = load_exercise_bank_from_db(db_path)
    original_points = ["复习旧知", "基础练习安排", "拓展探索任务", "课后任务"]
    draft = PlanningDraft(
        meta=PlanningMeta(topic="异分母分数加法", subject="数学", grade="五年级"),
        pages=[
            PagePlan(page_number=1, page_type="cover", title="异分母分数加法"),
            PagePlan(
                page_number=2,
                page_type="toc",
                title="本节课学习内容",
                content_points=list(original_points),
            ),
        ],
    )

    result = bind_strict_lesson_exercises_to_draft(
        draft,
        records,
        session_dir=tmp_path / "session",
    )

    toc_page = draft.pages[1]
    assert result.bound_count == 0
    assert toc_page.page_type == "toc"
    assert toc_page.exercise_refs == []
    assert toc_page.exercise_payloads == []
    assert toc_page.content_points == original_points
    assert "观察图中分数条" not in "\n".join(str(item) for item in toc_page.content_points)


def test_strict_lesson_binding_still_uses_content_page_exercise_fallback(tmp_path):
    db_path, _image_root = _create_teach_kb_db(tmp_path)
    records = load_exercise_bank_from_db(db_path)
    draft = PlanningDraft(
        meta=PlanningMeta(topic="异分母分数加法", subject="数学", grade="五年级"),
        pages=[
            PagePlan(
                page_number=1,
                page_type="content",
                title="课堂检测：练一练",
                content_points=["完成一道练习题，稍后讲解。"],
            ),
        ],
    )

    result = bind_strict_lesson_exercises_to_draft(
        draft,
        records,
        session_dir=tmp_path / "session",
    )

    page = draft.pages[0]
    assert result.bound_count == 1
    assert page.page_type == "exercise"
    assert page.exercise_refs
    assert page.exercise_payloads
    assert "答案揭晓区：稍后揭晓" in "\n".join(str(item) for item in page.content_points)


def test_strict_lesson_binding_does_not_fallback_to_same_subject_grade(tmp_path):
    db_path, _image_root = _create_ascii_teach_kb_db(tmp_path)
    records = load_exercise_bank_from_db(db_path)
    draft = PlanningDraft(
        meta=PlanningMeta(topic="Grade 5 Math Decimals", subject="Math", grade="Grade 5"),
        pages=[
            PagePlan(
                page_number=1,
                page_type="exercise",
                title="AI generated practice",
                content_points=["Original AI exercise stays here."],
            )
        ],
    )

    result = bind_strict_lesson_exercises_to_draft(
        draft,
        records,
        session_dir=tmp_path / "session",
    )

    assert result.bound_count == 0
    assert draft.pages[0].exercise_refs == []
    assert draft.pages[0].content_points == ["Original AI exercise stays here."]


def test_strict_db_exercise_image_is_transparent_padded_to_supported_ratio(tmp_path):
    db_path, _image_root = _create_ascii_teach_kb_db(tmp_path)
    records = load_exercise_bank_from_db(db_path)
    draft = PlanningDraft(
        meta=PlanningMeta(topic="Grade 5 Math Fractions", subject="Math", grade="Grade 5"),
        pages=[
            PagePlan(
                page_number=1,
                page_type="exercise",
                title="Comprehensive practice",
                content_points=["Use one database exercise."],
            )
        ],
    )
    session_dir = tmp_path / "session"

    bind_strict_lesson_exercises_to_draft(draft, records, session_dir=session_dir)

    from PIL import Image

    image_need = draft.pages[0].material_needs.images[0]
    assert image_need.source == "exercise_asset"
    assert image_need.aspect_ratio == "4:3"
    with Image.open(session_dir / image_need.path) as image:
        assert image.mode == "RGBA"
        assert image.width * 3 == image.height * 4
