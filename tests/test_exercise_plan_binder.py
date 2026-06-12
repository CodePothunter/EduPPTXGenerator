import json
from pathlib import Path

from edupptx.models import PagePlan, PlanningDraft, PlanningMeta
from edupptx.planning.content_planner import finalize_reveal_pages
from edupptx.planning.exercise_plan_binder import (
    ExerciseRecord,
    bind_exercises_to_draft,
    load_exercise_bank,
    select_exercise_candidates,
)


def _write_bank(tmp_path: Path) -> Path:
    image_path = tmp_path / "circuit.png"
    image_path.write_bytes(b"fake-image")
    bank_path = tmp_path / "exercise_bank.json"
    bank_path.write_text(
        json.dumps(
            {
                "exercises": [
                    {
                        "exercise_id": "ex_text",
                        "category": "A",
                        "subject": "数学",
                        "grade": "五年级",
                        "stem": "计算 3/4 + 1/8。",
                        "answer": "7/8",
                        "knowledge_points": ["分数加法"],
                    },
                    {
                        "exercise_id": "ex_img",
                        "category": "B",
                        "subject": "物理",
                        "grade": "八年级",
                        "stem": "观察图中电路，判断灯泡是否发光。",
                        "answer": "会发光",
                        "explanation": "开关闭合，电路连通。",
                        "knowledge_points": ["电路"],
                        "image_assets": [
                            {
                                "image_id": "img_001",
                                "path": str(image_path),
                                "role": "question_diagram",
                                "query": "闭合电路题目配图",
                                "aspect_ratio": "4:3",
                            }
                        ],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return bank_path


def test_select_exercise_candidates_filters_by_subject_grade_and_category(tmp_path):
    records = load_exercise_bank(_write_bank(tmp_path))

    selected = select_exercise_candidates(
        records,
        subject="物理",
        grade="八年级",
        grade_band="高年级",
        topic="八年级物理电路",
        requirements="",
        limit_per_category=3,
    )

    assert [record.exercise_id for record in selected] == ["ex_img"]


def test_bind_exercises_injects_text_payload_and_copies_optional_images(tmp_path):
    records = load_exercise_bank(_write_bank(tmp_path))
    session_dir = tmp_path / "session"
    draft = PlanningDraft(
        meta=PlanningMeta(topic="八年级物理电路", subject="物理", grade="八年级"),
        pages=[
            PagePlan(
                page_number=1,
                page_type="exercise",
                title="综合运用",
                exercise_refs=["ex_img"],
            )
        ],
    )

    result = bind_exercises_to_draft(draft, records, session_dir=session_dir)

    assert result.bound_count == 1
    page = draft.pages[0]
    assert page.exercise_payloads[0]["exercise_id"] == "ex_img"
    assert page.reveal_mode == "show_answer"
    visible_text = "\n".join(str(item) for item in page.content_points)
    assert "观察图中电路" in visible_text
    assert "会发光" not in visible_text
    assert "答案揭晓区：稍后揭晓" in visible_text
    image_need = page.material_needs.images[0]
    assert image_need.source == "exercise_asset"
    assert image_need.path == "materials/exercises/ex_img_img_001.png"
    assert (session_dir / image_need.path).read_bytes() == b"fake-image"


def test_reveal_page_uses_database_answer_from_exercise_payload(tmp_path):
    records = load_exercise_bank(_write_bank(tmp_path))
    draft = PlanningDraft(
        meta=PlanningMeta(topic="五年级数学分数加法", subject="数学", grade="五年级"),
        pages=[
            PagePlan(
                page_number=1,
                page_type="exercise",
                title="练一练",
                exercise_refs=["ex_text"],
            )
        ],
    )
    bind_exercises_to_draft(draft, records, session_dir=tmp_path / "session")

    finalized = finalize_reveal_pages(draft)

    assert len(finalized.pages) == 2
    reveal_page = finalized.pages[1]
    assert reveal_page.reveal_from_page == 1
    reveal_text = "\n".join(str(item) for item in reveal_page.content_points)
    assert "答案：7/8" in reveal_text
    assert "计算 3/4 + 1/8" in reveal_text


def test_bind_skips_hallucinated_ref_without_crashing(tmp_path):
    # M-15: a ref the LLM invented (not in the bank) must degrade to a warning
    # + skip, not crash the whole generation. The valid ref still binds.
    records = load_exercise_bank(_write_bank(tmp_path))
    draft = PlanningDraft(
        meta=PlanningMeta(topic="八年级物理电路", subject="物理", grade="八年级"),
        pages=[
            PagePlan(
                page_number=1,
                page_type="exercise",
                title="综合运用",
                exercise_refs=["ex_img", "ex_ghost"],
            )
        ],
    )

    result = bind_exercises_to_draft(draft, records, session_dir=tmp_path / "session")

    assert result.bound_count == 1
    assert any("ex_ghost" in w for w in result.warnings)
    # The dropped ref is pruned from the plan so nothing dangles.
    assert draft.pages[0].exercise_refs == ["ex_img"]


def test_bind_skips_answerless_record(tmp_path):
    # M-15: a record with no answer cannot drive a reveal page -> skip + warn.
    records = [
        ExerciseRecord(
            exercise_id="ex_noanswer",
            category="A",
            stem="无答案题目。",
            subject="数学",
            grade="五年级",
            answer="",
        )
    ]
    draft = PlanningDraft(
        meta=PlanningMeta(topic="数学", subject="数学", grade="五年级"),
        pages=[
            PagePlan(
                page_number=1,
                page_type="exercise",
                title="练一练",
                exercise_refs=["ex_noanswer"],
            )
        ],
    )

    result = bind_exercises_to_draft(draft, records, session_dir=tmp_path / "session")

    assert result.bound_count == 0
    assert any("ex_noanswer" in w for w in result.warnings)
    assert draft.pages[0].exercise_refs == []


def test_bind_skips_missing_image_keeps_exercise(tmp_path):
    # M-15: a missing image file drops just that asset, keeping the exercise's
    # text bound — it must not abort the page or the pipeline.
    records = load_exercise_bank(_write_bank(tmp_path))
    (tmp_path / "circuit.png").unlink()  # remove the backing image after load
    draft = PlanningDraft(
        meta=PlanningMeta(topic="八年级物理电路", subject="物理", grade="八年级"),
        pages=[
            PagePlan(
                page_number=1,
                page_type="exercise",
                title="综合运用",
                exercise_refs=["ex_img"],
            )
        ],
    )

    result = bind_exercises_to_draft(draft, records, session_dir=tmp_path / "session")

    assert result.bound_count == 1  # exercise still bound (text intact)
    assert any("img_001" in w for w in result.warnings)
    page = draft.pages[0]
    assert page.exercise_payloads[0]["exercise_id"] == "ex_img"
    # No exercise_asset image need survives since the file was gone.
    assert all(img.source != "exercise_asset" for img in page.material_needs.images)
