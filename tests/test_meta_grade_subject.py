# -*- coding: utf-8 -*-
import pytest
from edupptx.materials import ai_image_asset_db as db


def test_grade_band_from_norm_low_high_other():
    assert db.grade_band_from_norm("三年级") == "低年级"
    assert db.grade_band_from_norm("四年级") == "高年级"
    assert db.grade_band_from_norm("八年级") == "高年级"  # 初中归高年级
    assert db.grade_band_from_norm("高一") == "高年级"
    assert db.grade_band_from_norm("其他") == "其他"
    assert db.grade_band_from_norm("") == "其他"


def test_infer_subject_enum_only():
    assert db.infer_subject("语文") == "语文"
    assert db.infer_subject("化学") == "其他"   # 枚举外
    assert db.infer_subject("") == "其他"


def test_extract_grade_token():
    assert db._extract_grade_token("八年级物理《质量》课文教学") == "八年级"
    assert db._extract_grade_token("初中八年级学生") == "八年级"
    assert db._extract_grade_token("初二上学期") == "八年级"
    assert db._extract_grade_token("高三复习") == "高三"
    assert db._extract_grade_token("3年级数学") == "三年级"
    assert db._extract_grade_token("光合作用") == "其他"


def test_extract_subject_token():
    assert db._extract_subject_token("八年级物理《质量》") == "物理"
    assert db._extract_subject_token("三年级语文荷花") == "语文"
    assert db._extract_subject_token("光合作用") == "其他"


def test_resolve_prefers_llm_then_extract_then_derive():
    # LLM 全给且合法 → 直接用，band 取 LLM
    r = db.resolve_meta_grade_subject(
        llm_subject="物理", llm_grade="八年级", llm_grade_band="低年级",
        topic="八年级物理《质量》", audience="初中八年级学生",
    )
    assert r == {"subject": "物理", "grade": "八年级", "grade_band": "低年级"}

    # LLM 缺失 → 从 topic/audience 抽，band 从 grade 派生
    r2 = db.resolve_meta_grade_subject(
        llm_subject="", llm_grade="", llm_grade_band="",
        topic="八年级物理《质量》课文教学", audience="初中八年级学生",
    )
    assert r2 == {"subject": "物理", "grade": "八年级", "grade_band": "高年级"}

    # 完全无信息 → 全其他（保守降级）
    r3 = db.resolve_meta_grade_subject(topic="光合作用", audience="")
    assert r3 == {"subject": "其他", "grade": "其他", "grade_band": "其他"}

    # requirements 也作为来源
    r4 = db.resolve_meta_grade_subject(topic="《荷花》", audience="", requirements="面向三年级语文")
    assert r4 == {"subject": "语文", "grade": "三年级", "grade_band": "低年级"}


class _DeckMetaClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    def chat_json(self, *args, **kwargs):
        self.calls += 1
        return self.payload


def test_resolve_meta_skips_llm_normalizer_when_fields_are_standard():
    client = _DeckMetaClient({"subject": "数学", "grade": "三年级", "grade_band": "低年级"})

    result = db.resolve_meta_grade_subject(
        llm_subject="语文",
        llm_grade="八年级",
        llm_grade_band="高年级",
        topic="八年级语文课",
        normalizer_client=client,
    )

    assert result == {"subject": "语文", "grade": "八年级", "grade_band": "高年级"}
    assert client.calls == 0


def test_resolve_meta_uses_llm_normalizer_once_for_nonstandard_fields():
    client = _DeckMetaClient({"subject": "语文", "grade": "八年级", "grade_band": "高年级"})

    result = db.resolve_meta_grade_subject(
        llm_subject="小学语文",
        llm_grade="初二",
        llm_grade_band="初中",
        topic="刷子李课文教学",
        normalizer_client=client,
    )

    assert result == {"subject": "语文", "grade": "八年级", "grade_band": "高年级"}
    assert client.calls == 1


def test_planning_meta_has_grade_subject_fields():
    from edupptx.models import PlanningMeta
    m = PlanningMeta(topic="x", subject="物理", grade="八年级", grade_band="高年级")
    assert m.subject == "物理"
    assert m.grade == "八年级"
    assert m.grade_band == "高年级"
    # 默认值为空字符串
    m2 = PlanningMeta(topic="x")
    assert (m2.subject, m2.grade, m2.grade_band) == ("", "", "")
    # 往返
    dumped = m.model_dump()
    assert dumped["subject"] == "物理" and dumped["grade"] == "八年级"


def test_resolve_meta_inplace_backfills_when_llm_omits():
    from edupptx.models import PlanningDraft, PlanningMeta, PagePlan
    from edupptx.planning.content_planner import _resolve_meta_grade_subject_inplace
    draft = PlanningDraft(
        meta=PlanningMeta(topic="八年级物理《质量》课文教学", audience="初中八年级学生"),
        pages=[PagePlan(page_number=1, page_type="cover", title="质量")],
    )
    _resolve_meta_grade_subject_inplace(draft, requirements="")
    assert draft.meta.subject == "物理"
    assert draft.meta.grade == "八年级"
    assert draft.meta.grade_band == "高年级"


def test_resolve_meta_inplace_respects_llm_values():
    from edupptx.models import PlanningDraft, PlanningMeta, PagePlan
    from edupptx.planning.content_planner import _resolve_meta_grade_subject_inplace
    draft = PlanningDraft(
        meta=PlanningMeta(topic="某主题", audience="", subject="数学", grade="五年级"),
        pages=[PagePlan(page_number=1, page_type="cover", title="x")],
    )
    _resolve_meta_grade_subject_inplace(draft, requirements="")
    assert draft.meta.subject == "数学"
    assert draft.meta.grade == "五年级"
    assert draft.meta.grade_band == "高年级"  # 从 grade 派生
